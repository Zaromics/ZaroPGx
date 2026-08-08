"""BACKLOG 340 - coverage for the JobService <-> PharmCAT linkage and progress roll-up.

``link_pharmcat_run`` / ``get_pharmcat_run_id`` / ``get_pharmcat_data`` are the
only path by which a finished PharmCAT run is reattached to the job that
produced it: ``upload_router`` calls ``link_pharmcat_run`` right after
``load_pharmcat_file``, and every downstream report read goes back through
``jobs.job_metadata['pharmcat_run_id']``.  ``_update_job_progress`` is the sole
writer of ``jobs.completed_steps`` and the only place a job is flipped to
COMPLETED.  None of the four had a test.

``get_job_progress`` / ``get_job_logs`` are already covered in
tests/test_job_monitoring.py and are not re-tested here.
"""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.api.db import Job, JobStep
from app.api.models import JobCreate, JobStatus, StepStatus
from app.services import job_service as job_service_module


def _make_job(job_service, **metadata):
    """Create a real job through the service so metadata/steps look production-shaped."""
    return job_service.create_job(
        JobCreate(
            name="Test Job",
            workflow_type="genomic_analysis",
            metadata=metadata,
            created_by="pytest",
        )
    )


# ---------------------------------------------------------------------------
# link_pharmcat_run
# ---------------------------------------------------------------------------


def test_link_pharmcat_run_stores_run_id_and_timestamp(job_service):
    job = _make_job(job_service)

    assert job_service.link_pharmcat_run(str(job.id), "pcat-run-001") is True

    metadata = job.job_metadata
    assert metadata["pharmcat_run_id"] == "pcat-run-001"

    linked_at = datetime.fromisoformat(metadata["pharmcat_linked_at"])
    assert linked_at.tzinfo is not None
    assert abs((datetime.now(timezone.utc) - linked_at).total_seconds()) < 60


def test_link_pharmcat_run_survives_a_reload_from_the_database(job_service, db_session):
    """The link has to reach the jobs row, not just the in-memory instance.

    ``jobs.job_metadata`` is a plain ``Column(JSON)`` with no MutableDict, and
    ``create_job`` always seeds it (workflow_type, workflow), so the dict is
    never empty.  Mutating that dict in place and assigning it back to the same
    attribute leaves SQLAlchemy's history unchanged and the UPDATE is never
    emitted; the value only appears to stick because the instance stays in the
    identity map.  Expiring the session is what a second request would see.
    """
    job = _make_job(job_service, patient_id="PAT-42")
    job_id = job.id

    assert job_service.link_pharmcat_run(str(job_id), "pcat-run-002") is True

    db_session.expire_all()

    reloaded = db_session.query(Job).filter(Job.id == job_id).first()
    assert reloaded.job_metadata["pharmcat_run_id"] == "pcat-run-002"


def test_link_pharmcat_run_preserves_existing_metadata(job_service, db_session):
    job = _make_job(job_service, patient_id="PAT-7", file_type="vcf")
    job_id = job.id

    assert job_service.link_pharmcat_run(str(job_id), "pcat-run-003") is True
    db_session.expire_all()

    metadata = db_session.query(Job).filter(Job.id == job_id).first().job_metadata
    assert metadata["patient_id"] == "PAT-7"
    assert metadata["file_type"] == "vcf"
    assert metadata["workflow_type"] == "genomic_analysis"
    assert metadata["pharmcat_run_id"] == "pcat-run-003"


def test_link_pharmcat_run_accepts_a_uuid_object(job_service):
    job = _make_job(job_service)
    assert job_service.link_pharmcat_run(job.id, "pcat-run-004") is True
    assert job_service.get_pharmcat_run_id(job.id) == "pcat-run-004"


def test_link_pharmcat_run_relink_overwrites(job_service, db_session):
    job = _make_job(job_service)
    job_id = job.id

    job_service.link_pharmcat_run(str(job_id), "pcat-run-old")
    job_service.link_pharmcat_run(str(job_id), "pcat-run-new")
    db_session.expire_all()

    assert job_service.get_pharmcat_run_id(str(job_id)) == "pcat-run-new"


def test_link_pharmcat_run_unknown_job_is_false(job_service):
    assert job_service.link_pharmcat_run(str(uuid.uuid4()), "pcat-run-005") is False


def test_link_pharmcat_run_malformed_job_id_is_false(job_service):
    # Must be swallowed into False, never raised at the caller in upload_router.
    assert job_service.link_pharmcat_run("not-a-uuid", "pcat-run-006") is False


# ---------------------------------------------------------------------------
# get_pharmcat_run_id
# ---------------------------------------------------------------------------


def test_get_pharmcat_run_id_before_linking_is_none(job_service):
    job = _make_job(job_service)
    assert job_service.get_pharmcat_run_id(str(job.id)) is None


def test_get_pharmcat_run_id_unknown_job_is_none(job_service):
    assert job_service.get_pharmcat_run_id(str(uuid.uuid4())) is None


def test_get_pharmcat_run_id_malformed_job_id_is_none(job_service):
    assert job_service.get_pharmcat_run_id("not-a-uuid") is None


def test_get_pharmcat_run_id_with_null_metadata_is_none(job_service, db_session):
    job = _make_job(job_service)
    job.job_metadata = None
    db_session.commit()

    assert job_service.get_pharmcat_run_id(str(job.id)) is None


# ---------------------------------------------------------------------------
# get_pharmcat_data
# ---------------------------------------------------------------------------


def test_get_pharmcat_data_delegates_to_the_data_service(job_service, monkeypatch):
    seen = {}
    payload = {"genes": [{"gene": "CYP2D6"}], "drugRecommendations": []}

    class _StubService:
        def __init__(self, db):
            seen["db"] = db

        def get_pharmcat_data_for_workflow(self, workflow_id):
            seen["workflow_id"] = workflow_id
            return payload

    monkeypatch.setattr(job_service_module, "PharmCATDataService", _StubService)

    job_id = str(uuid.uuid4())
    assert job_service.get_pharmcat_data(job_id) == payload
    assert seen["workflow_id"] == job_id
    # The service must share the JobService session, not open its own.
    assert seen["db"] is job_service.db


def test_get_pharmcat_data_swallows_service_errors(job_service, monkeypatch):
    class _ExplodingService:
        def __init__(self, db):
            pass

        def get_pharmcat_data_for_workflow(self, workflow_id):
            raise RuntimeError("pharmcat tables missing")

    monkeypatch.setattr(job_service_module, "PharmCATDataService", _ExplodingService)

    assert job_service.get_pharmcat_data(str(uuid.uuid4())) is None


def test_get_pharmcat_data_unlinked_job_is_none(job_service):
    """Real PharmCATDataService: no pharmcat_run_id in metadata means no data."""
    job = _make_job(job_service)
    assert job_service.get_pharmcat_data(str(job.id)) is None


def test_get_pharmcat_data_unknown_job_is_none(job_service):
    assert job_service.get_pharmcat_data(str(uuid.uuid4())) is None


# ---------------------------------------------------------------------------
# _update_job_progress
# ---------------------------------------------------------------------------


@pytest.fixture
def no_cleanup(monkeypatch):
    """Stub cleanup_service so the completion branch never touches the filesystem."""
    calls = []

    def _cleanup_job_files(job_id, patient_id=None, additional_paths=None):
        calls.append({"job_id": job_id, "patient_id": patient_id})
        return {"success": True, "total_items_cleaned": 0, "total_size_cleaned": 0}

    monkeypatch.setattr(
        job_service_module,
        "cleanup_service",
        SimpleNamespace(cleanup_job_files=_cleanup_job_files),
    )
    return calls


def _complete_steps(db_session, job_id, count):
    steps = (
        db_session.query(JobStep)
        .filter(JobStep.job_id == job_id)
        .order_by(JobStep.step_order)
        .all()
    )
    for step in steps[:count]:
        step.status = StepStatus.COMPLETED
    db_session.commit()
    return len(steps)


def test_update_job_progress_counts_only_completed_steps(job_service, db_session):
    job = _make_job(job_service)
    total = _complete_steps(db_session, job.id, 1)
    # Guards the test itself: with a single-step recipe "count the completed
    # ones" and "count them all" would be indistinguishable.
    assert total > 1

    job_service._update_job_progress(job.id)

    db_session.expire_all()
    reloaded = db_session.query(Job).filter(Job.id == job.id).first()
    assert reloaded.completed_steps == 1
    # Under 100%, the job must not be marked finished.
    assert reloaded.status != JobStatus.COMPLETED.value
    assert reloaded.completed_at is None


def test_update_job_progress_recount_is_idempotent(job_service, db_session):
    job = _make_job(job_service)
    _complete_steps(db_session, job.id, 2)

    job_service._update_job_progress(job.id)
    job_service._update_job_progress(job.id)

    db_session.expire_all()
    assert db_session.query(Job).filter(Job.id == job.id).first().completed_steps == 2


def test_update_job_progress_unknown_job_is_a_silent_noop(job_service):
    # Called from step-update paths; a vanished job must not raise.
    job_service._update_job_progress(uuid.uuid4())


def test_update_job_progress_marks_job_completed_at_100(
    job_service, db_session, no_cleanup, monkeypatch
):
    job = _make_job(job_service, patient_id="PAT-99")
    _complete_steps(db_session, job.id, 2)

    monkeypatch.setattr(
        job_service,
        "get_job_progress",
        lambda job_id: SimpleNamespace(progress_percentage=100),
    )

    job_service._update_job_progress(job.id)

    db_session.expire_all()
    reloaded = db_session.query(Job).filter(Job.id == job.id).first()
    assert reloaded.status == JobStatus.COMPLETED.value
    assert reloaded.completed_at is not None
    assert reloaded.completed_steps == 2

    # Completion is logged and cleanup is handed the patient id from metadata.
    messages = [log.message for log in job_service.get_job_logs(job.id)]
    assert "Job completed successfully with reports generated" in messages
    assert no_cleanup == [{"job_id": str(job.id), "patient_id": "PAT-99"}]


def test_update_job_progress_below_100_does_not_clean_up(
    job_service, db_session, no_cleanup, monkeypatch
):
    job = _make_job(job_service)
    _complete_steps(db_session, job.id, 1)

    monkeypatch.setattr(
        job_service,
        "get_job_progress",
        lambda job_id: SimpleNamespace(progress_percentage=99),
    )

    job_service._update_job_progress(job.id)

    db_session.expire_all()
    reloaded = db_session.query(Job).filter(Job.id == job.id).first()
    assert reloaded.status != JobStatus.COMPLETED.value
    assert reloaded.completed_at is None
    assert no_cleanup == []


def test_update_job_progress_completes_even_when_cleanup_fails(
    job_service, db_session, monkeypatch
):
    """A failing cleanup must not cost the job its COMPLETED status."""

    def _boom(job_id, patient_id=None, additional_paths=None):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(
        job_service_module,
        "cleanup_service",
        SimpleNamespace(cleanup_job_files=_boom),
    )
    monkeypatch.setattr(
        job_service,
        "get_job_progress",
        lambda job_id: SimpleNamespace(progress_percentage=100),
    )

    job = _make_job(job_service)
    job_service._update_job_progress(job.id)

    db_session.expire_all()
    reloaded = db_session.query(Job).filter(Job.id == job.id).first()
    assert reloaded.status == JobStatus.COMPLETED.value
    assert reloaded.completed_at is not None


def test_update_job_progress_tolerates_missing_progress(
    job_service, db_session, no_cleanup, monkeypatch
):
    """get_job_progress returning None must not blow up the recount."""
    job = _make_job(job_service)
    _complete_steps(db_session, job.id, 1)

    monkeypatch.setattr(job_service, "get_job_progress", lambda job_id: None)

    job_service._update_job_progress(job.id)

    db_session.expire_all()
    reloaded = db_session.query(Job).filter(Job.id == job.id).first()
    assert reloaded.completed_steps == 1
    assert reloaded.status != JobStatus.COMPLETED.value
