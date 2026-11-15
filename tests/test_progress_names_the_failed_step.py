"""A failed job's progress names the step that failed, not the last one that worked.

`get_job_progress` answered from the progress calculator, which describes PROGRESS.
Asked about a failed job it named the furthest stage reached and described the work of
that stage -- measured on a real run whose mtDNA step died on an unparseable position:

    status:       failed
    current_step: header_analysis            <- this step had COMPLETED
    message:      "Analyzing file headers and metadata. Determining workflow."

So the page, which polls this endpoint, named a step that had succeeded and described
work that had gone fine. The step rows knew which step failed all along.
"""

from __future__ import annotations

import uuid

import pytest

from app.api.db import Job, JobStep
from app.services.job_service import JobService


@pytest.fixture
def failed_job(db_session):
    """A job whose second step failed, as the database records one."""
    job_id = uuid.uuid4()
    db_session.add(
        Job(
            id=job_id,
            name="progress-failure",
            status="failed",
            job_metadata={},
            total_steps=3,
            completed_steps=1,
        )
    )
    for order, (name, state) in enumerate(
        (
            ("header_analysis", "completed"),
            ("mtdna_analysis", "failed"),
            ("report_generation", "pending"),
        ),
        start=1,
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


def test_it_names_the_step_that_failed(db_session, failed_job):
    progress = JobService(db_session).get_job_progress(str(failed_job))

    assert progress is not None
    assert progress.current_step == "mtdna_analysis"
    assert "header_analysis" not in (progress.current_step or "")


def test_the_message_says_that_step_failed(db_session, failed_job):
    progress = JobService(db_session).get_job_progress(str(failed_job))

    assert progress.message == "mtdna_analysis failed."


def test_a_step_that_recorded_a_reason_has_it_quoted(db_session, failed_job):
    """When a sidecar passes error_details through fail_step, that text is the
    message rather than the generic sentence."""
    step = (
        db_session.query(JobStep)
        .filter(JobStep.job_id == failed_job, JobStep.step_name == "mtdna_analysis")
        .one()
    )
    step.error_details = {"error": "bcftools failed: could not parse the position"}
    db_session.commit()

    progress = JobService(db_session).get_job_progress(str(failed_job))

    assert progress.message == "bcftools failed: could not parse the position"


def test_a_running_job_is_untouched(db_session):
    """The failed path is the only one that changes: a job in flight still gets the
    progress calculator's stage and its description."""
    job_id = uuid.uuid4()
    db_session.add(
        Job(
            id=job_id,
            name="progress-running",
            status="running",
            job_metadata={},
            total_steps=2,
            completed_steps=1,
        )
    )
    db_session.add(
        JobStep(
            job_id=job_id,
            step_name="header_analysis",
            step_order=1,
            status="completed",
            output_data={},
            error_details={},
        )
    )
    db_session.add(
        JobStep(
            job_id=job_id,
            step_name="pharmcat_analysis",
            step_order=2,
            status="running",
            output_data={},
            error_details={},
        )
    )
    db_session.commit()

    progress = JobService(db_session).get_job_progress(str(job_id))

    assert progress.message
    assert "failed." not in (progress.message or "")


def test_status_text_reads_an_enum_and_a_string(db_session):
    """Job.status comes back as JobStatus.FAILED, so str() gives "jobstatus.failed":
    the first version of this fix compared that against "failed" and silently did
    nothing. Both forms have to work."""
    from app.api.models import JobStatus
    from app.services.job_service import _status_text

    assert _status_text(JobStatus.FAILED) == "failed"
    assert _status_text("failed") == "failed"
    assert _status_text(None) == ""


def test_a_cancelled_run_says_it_was_cancelled(db_session, failed_job):
    """A cancelled run ends as `failed`: the step in flight dies because it was
    stopped, and the pipeline's non-zero exit overwrites the status the cancel
    endpoint set. Measured on a real cancel -- status "failed",
    job_metadata.cancelled true, and the step's own words "CRAM to BAM conversion
    failed: samtools view exited 0 but wrote no BAM". That reads as a defect in the
    data when the run was stopped on request."""
    job = db_session.query(Job).filter(Job.id == failed_job).one()
    job.job_metadata = {"cancelled": True, "cancelled_at": "2026-11-15T05:54:33Z"}
    step = (
        db_session.query(JobStep)
        .filter(JobStep.job_id == failed_job, JobStep.step_name == "mtdna_analysis")
        .one()
    )
    step.error_details = {"error": "samtools view exited 0 but wrote no BAM"}
    db_session.commit()

    progress = JobService(db_session).get_job_progress(str(failed_job))

    assert progress.message == "Cancelled while mtdna_analysis was running."
    assert "samtools" not in (progress.message or "")
    assert progress.current_step == "mtdna_analysis"


def test_a_failure_that_was_not_cancelled_still_quotes_the_reason(
    db_session, failed_job
):
    """The cancel branch must not swallow a real failure's text."""
    step = (
        db_session.query(JobStep)
        .filter(JobStep.job_id == failed_job, JobStep.step_name == "mtdna_analysis")
        .one()
    )
    step.error_details = {"error": "bcftools failed: could not parse the position"}
    db_session.commit()

    progress = JobService(db_session).get_job_progress(str(failed_job))

    assert progress.message == "bcftools failed: could not parse the position"
