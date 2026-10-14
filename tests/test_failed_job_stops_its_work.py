"""A job that fails has its work stopped before its files are released.

Found 2026-10-13. A whole-genome BAM run filled the host's disk; the Nextflow head
died with SIGBUS, the poll loop marked the job failed, and update_job released its
files: the upload and the sidecars' scratch, zarohla's 151 GB read1.fq among them.
Nothing told zarohla. OptiType kept mapping the deleted FASTQ, holding its space and
four cores, for hours after the job had failed. A user's cancel already stopped the
work (job_router.cancel_job); a failure now does the same, first.
"""

import asyncio
from types import SimpleNamespace

import pytest

import app.api.routes.job_router as jr
import app.api.routes.upload_router as ur
from app.api.models import JobStatus

JOB_ID = "job-failed"
METADATA = {"patient_id": "patient-1", "data_id": "data-1"}


class _FakeJobService:
    def __init__(self, job_status="running"):
        self.job_status = job_status
        self.updates = []
        self.logs = []

    def get_job(self, job_id):
        return SimpleNamespace(
            id=job_id, status=self.job_status, job_metadata=dict(METADATA)
        )

    def update_job(self, job_id, update):
        self.updates.append(update)

    def log_job_event(self, job_id, log_data):
        self.logs.append(log_data)


class _FailedResponse:
    status_code = 200

    @staticmethod
    def json():
        return {
            "status": "failed",
            "error": "Nextflow pipeline failed with return code -6",
        }


def _wait(service):
    async def main():
        return await asyncio.wait_for(
            ur.wait_for_nextflow_completion(
                service, JOB_ID, "http://nextflow:5055", "key", "/data/reports/p/j"
            ),
            timeout=10,
        )

    return asyncio.run(main())


@pytest.fixture
def stops(monkeypatch):
    calls = []

    async def record(job_service, job_id):
        calls.append(job_id)
        job_service.updates.append("stopped")

    monkeypatch.setattr(ur, "stop_job_work", record)
    return calls


def _order(service):
    return [
        "stopped" if u == "stopped" else getattr(u, "status", None)
        for u in service.updates
    ]


def test_a_job_nextflow_reports_failed_is_stopped_before_it_is_failed(
    monkeypatch, stops
):
    monkeypatch.setattr(ur.requests, "get", lambda url, timeout=None: _FailedResponse())
    service = _FakeJobService()

    _wait(service)

    assert stops == [JOB_ID]
    assert _order(service) == ["stopped", JobStatus.FAILED]


def test_a_job_failed_at_the_deadline_is_stopped_first(monkeypatch, stops):
    monkeypatch.setenv("NEXTFLOW_MAX_WAIT_SECONDS", "0.2")

    class Running:
        status_code = 200

        @staticmethod
        def json():
            return {"status": "running"}

    monkeypatch.setattr(ur.requests, "get", lambda url, timeout=None: Running())
    service = _FakeJobService()

    _wait(service)

    assert _order(service) == ["stopped", JobStatus.FAILED]


def test_a_job_nextflow_reports_cancelled_is_not_stopped_again(monkeypatch, stops):
    """The user's cancel stopped it already (job_router.cancel_job)."""

    class Cancelled:
        status_code = 200

        @staticmethod
        def json():
            return {"status": "cancelled"}

    monkeypatch.setattr(ur.requests, "get", lambda url, timeout=None: Cancelled())

    _wait(_FakeJobService())

    assert stops == []


def test_stopping_cancels_nextflow_and_every_sidecar_with_the_jobs_metadata(
    monkeypatch,
):
    seen = []

    async def nextflow(job_id, metadata):
        seen.append(("nextflow", job_id, metadata))

    async def sidecars(job_id, metadata):
        seen.append(("sidecars", job_id, metadata))

    monkeypatch.setattr(jr, "cancel_nextflow_job", nextflow)
    monkeypatch.setattr(jr, "cancel_container_jobs", sidecars)

    asyncio.run(ur.stop_job_work(_FakeJobService(), JOB_ID))

    assert seen == [
        ("nextflow", JOB_ID, METADATA),
        ("sidecars", JOB_ID, METADATA),
    ]


def test_stopping_never_raises(monkeypatch):
    """The job must still be marked failed when a service cannot be reached."""

    async def unreachable(job_id, metadata):
        raise ConnectionError("nextflow is gone")

    monkeypatch.setattr(jr, "cancel_nextflow_job", unreachable)
    monkeypatch.setattr(jr, "cancel_container_jobs", unreachable)

    class Broken(_FakeJobService):
        def get_job(self, job_id):
            raise RuntimeError("database is gone")

    asyncio.run(ur.stop_job_work(Broken(), JOB_ID))


def test_each_sidecar_cancel_is_posted_off_the_event_loop(monkeypatch):
    """The failing job's monitor runs on the app's event loop; five 30 s posts on it
    would stall every request the app serves."""
    offloaded = []
    real_to_thread = asyncio.to_thread

    async def recording_to_thread(func, *args, **kwargs):
        offloaded.append(func)
        return await real_to_thread(func, *args, **kwargs)

    posted = []

    def post(url, json=None, timeout=None):
        posted.append(url)
        return SimpleNamespace(status_code=200, text="")

    monkeypatch.setattr(jr.asyncio, "to_thread", recording_to_thread)
    monkeypatch.setattr(jr.requests, "post", post)

    asyncio.run(jr.cancel_container_jobs(JOB_ID, METADATA))

    assert "http://zarohla:5000/cancel" in posted
    assert offloaded.count(post) == len(posted)
