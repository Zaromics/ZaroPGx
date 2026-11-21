"""A failed job's reason, in the failed step's own words, reaches the page.

MEASURED 2026-11-15 on a real failing run: the database held "mtDNA calling failed:
bcftools failed: [E::vcf_parse] Could not parse the position 'NOTANUMBER'" in the job's
log, and the page learned none of it. Two breaks, both fixed here:

- mtdna's ``client.fail_step(f"mtDNA calling failed: {exc.detail}")`` sends its reason
  as the failed update's ``message``, with no ``error`` in ``error_details`` -- as do
  three gatk-api calls and three pharmcat calls. The app logged the message and stored
  nothing the progress response reads, so it said "mtdna_analysis failed." and stopped.
- The page's "Analysis Failed" card said "The processing log above says where it
  stopped and why", and nothing put the why in the log.

The progress response, the socket's job_update and its reconnect status now carry
``error``: the failed step's own words, or nothing. A separate field rather than
``message``, because a failed job with no failed step still has the progress
calculator's description of whatever stage it reached in ``message``, and the page
must not print that as a reason.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import Path

import pytest

from app.api.db import Job, JobStep
from app.api.models import JobStepUpdate, StepStatus
from app.services.job_service import JobService
from app.utils.job_client import JobClient

REPO = Path(__file__).resolve().parents[1]
MTDNA_REASON = (
    "mtDNA calling failed: bcftools failed: [E::vcf_parse] Could not parse the "
    "position 'NOTANUMBER'"
)


def _job(db_session, status="running", metadata=None):
    job_id = uuid.uuid4()
    db_session.add(
        Job(
            id=job_id,
            name="failed-job-reason",
            status=status,
            job_metadata=metadata or {},
            total_steps=2,
            completed_steps=1,
        )
    )
    for order, (name, state) in enumerate(
        (("header_analysis", "completed"), ("mtdna_analysis", "running")), start=1
    ):
        db_session.add(
            JobStep(
                job_id=job_id,
                step_name=name,
                step_order=order,
                status=state,
                output_data={},
                error_details={},
            )
        )
    db_session.commit()
    return job_id


def _fail_job(db_session, job_id):
    job = db_session.query(Job).filter(Job.id == job_id).one()
    job.status = "failed"
    db_session.commit()


def _step(db_session, job_id, name="mtdna_analysis"):
    return (
        db_session.query(JobStep)
        .filter(JobStep.job_id == job_id, JobStep.step_name == name)
        .one()
    )


# --- the sidecar's own call, through the real route ---------------------------------


def test_mtdnas_fail_step_reaches_the_progress_response(
    client, db_session, monkeypatch
):
    """JobClient.fail_step exactly as docker/mtdna-server-2/app.py calls it, sent to
    the real PUT route."""

    async def through_the_app(self, method, endpoint, data=None):
        response = client.request(method, f"/api/v1{endpoint}", json=data)
        response.raise_for_status()
        return response.json()

    monkeypatch.setattr(JobClient, "_make_request", through_the_app)
    job_id = _job(db_session)

    sidecar = JobClient(job_id=str(job_id), step_name="mtdna_analysis")
    assert asyncio.run(sidecar.fail_step(MTDNA_REASON))
    _fail_job(db_session, job_id)

    db_session.expire_all()
    assert _step(db_session, job_id).error_details == {"error": MTDNA_REASON}
    progress = client.get(f"/api/v1/jobs/{job_id}/progress").json()
    assert progress["status"] == "failed"
    assert progress["current_step"] == "mtdna_analysis"
    assert progress["error"] == MTDNA_REASON
    assert progress["message"] == MTDNA_REASON


def test_details_without_a_reason_keep_their_keys(db_session):
    """pharmcat's shape: the reason in the message, other facts in the details."""
    job_id = _job(db_session)
    JobService(db_session).update_job_step(
        str(job_id),
        "mtdna_analysis",
        JobStepUpdate(
            status=StepStatus.FAILED,
            message="Required PharmCAT output files not found",
            error_details={"error_type": "missing_output_files"},
        ),
    )
    assert _step(db_session, job_id).error_details == {
        "error_type": "missing_output_files",
        "error": "Required PharmCAT output files not found",
    }


def test_a_reason_the_sidecar_gave_is_not_replaced_by_its_message(db_session):
    """zarohla: message "OptiType execution failed", error OptiType's stderr. The
    stderr is the more specific of the two and stays."""
    job_id = _job(db_session)
    JobService(db_session).update_job_step(
        str(job_id),
        "mtdna_analysis",
        JobStepUpdate(
            status=StepStatus.FAILED,
            message="OptiType execution failed",
            error_details={"error": "razers3: out of memory"},
        ),
    )
    assert _step(db_session, job_id).error_details == {
        "error": "razers3: out of memory"
    }


@pytest.mark.parametrize("status", [StepStatus.RUNNING, StepStatus.COMPLETED])
def test_a_message_that_is_not_a_failure_is_not_a_reason(db_session, status):
    job_id = _job(db_session)
    JobService(db_session).update_job_step(
        str(job_id),
        "mtdna_analysis",
        JobStepUpdate(status=status, message="Processing chromosome chrM"),
    )
    assert not (_step(db_session, job_id).error_details or {}).get("error")


# --- what the progress response says ------------------------------------------------


def test_a_failed_job_with_no_failed_step_gives_no_reason(db_session):
    """Its message is the progress calculator's description of a stage: printed as
    a reason it would be false."""
    job_id = _job(db_session)
    _fail_job(db_session, job_id)
    progress = JobService(db_session).get_job_progress(str(job_id))
    assert progress.error is None


def test_a_failed_step_with_no_words_gives_no_reason(db_session):
    job_id = _job(db_session)
    _step(db_session, job_id).status = "failed"
    _fail_job(db_session, job_id)
    progress = JobService(db_session).get_job_progress(str(job_id))
    assert progress.message == "mtdna_analysis failed."
    assert progress.error is None


def test_a_cancelled_run_gives_no_reason(db_session):
    """The step in flight dies because it was stopped; its words describe that, not
    a defect (test_progress_names_the_failed_step)."""
    job_id = _job(db_session, metadata={"cancelled": True})
    step = _step(db_session, job_id)
    step.status = "failed"
    step.error_details = {"error": "samtools view exited 0 but wrote no BAM"}
    _fail_job(db_session, job_id)
    progress = JobService(db_session).get_job_progress(str(job_id))
    assert progress.error is None


def test_a_running_job_has_no_reason(db_session):
    job_id = _job(db_session)
    assert JobService(db_session).get_job_progress(str(job_id)).error is None


def test_every_payload_that_carries_the_message_carries_the_reason():
    """The page learns a job's end from three places: the poll (the progress response
    itself), the socket's job_update and the status sent on (re)connecting. Each of
    the last two is a dict copied field by field from the progress response."""
    for path in ("app/services/job_service.py", "app/api/routes/job_router.py"):
        source = (REPO / path).read_text(encoding="utf-8")
        messages = len(
            re.findall(r'"message": \(?\s*progress_response\.message', source)
        )
        errors = len(re.findall(r'"error": \(?\s*progress_response\.error', source))
        assert messages and messages == errors, path
