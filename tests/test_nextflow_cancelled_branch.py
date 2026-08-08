"""Regression: a Nextflow-side cancellation left the job ``running`` forever.

``wait_for_nextflow_completion`` handled the runner reporting ``cancelled`` by logging a
line and ``break``ing — with no DB write at all. The poll is the only thing watching that
run, so the job sat at ``running`` with no terminal status, no completion timestamp and
no report: the UI spins forever and nothing ever reaps it. Same defect class as the
unbounded wait fixed in 359 (bff00ad).

The write needs the same terminal-status guard bff00ad introduced. ``update_job`` has no
guard of its own, and the poll is not the only path that finishes a run (containers
report in over JOB_API_BASE, and app.main completes a job once its reports exist), so an
unconditional write here could stomp a job somebody else already completed.

The loop wraps its body in a blanket ``except Exception``, so nothing below raises to
signal a violation — everything is recorded on the fake and asserted afterwards. Every
case that drives the loop is capped with ``asyncio.wait_for``: a regression here is a
hang, and an unguarded hang wedges CI instead of failing it.
"""

import asyncio

import pytest

import app.api.routes.upload_router as ur
from app.api.models import JobStatus

GUARD_SECONDS = 10.0

NEXTFLOW_URL = "http://nextflow:5055"
JOB_ID = "job-cancelled"
JOB_KEY = "nf-key-cancelled"
OUTDIR = "/data/reports/patient/job-cancelled"


@pytest.fixture(autouse=True)
def _clear_wait_env(monkeypatch):
    """Never inherit the ambient deadline: app.api.db calls load_dotenv() at import."""
    monkeypatch.delenv("NEXTFLOW_MAX_WAIT_SECONDS", raising=False)


class _FakeJob:
    def __init__(self, status):
        self.id = JOB_ID
        self.status = status


class _FakeJobService:
    def __init__(self, job_status="running"):
        self.job = _FakeJob(job_status)
        self.updates = []
        self.logs = []

    def get_job(self, job_id):
        return self.job

    def update_job(self, job_id, update):
        self.updates.append(update)
        return self.job

    def log_job_event(self, job_id, log_data):
        self.logs.append(log_data)

    def statuses(self):
        return [getattr(u, "status", None) for u in self.updates]


class _CancelledResponse:
    status_code = 200

    @staticmethod
    def json():
        return {"status": "cancelled", "message": "Run cancelled"}


class _StatusStub:
    """Records every poll so a call that should not happen can be asserted, not raised."""

    def __init__(self):
        self.calls = []

    def __call__(self, url, timeout=None):
        self.calls.append(url)
        return _CancelledResponse()


def _run_guarded(coro, guard=GUARD_SECONDS):
    async def _main():
        return await asyncio.wait_for(coro, timeout=guard)

    return asyncio.run(_main())


def _wait(service):
    return ur.wait_for_nextflow_completion(
        service, JOB_ID, NEXTFLOW_URL, JOB_KEY, OUTDIR
    )


def test_nextflow_cancellation_marks_the_job_cancelled(monkeypatch):
    """The runner reporting 'cancelled' must end the job, not strand it at running."""
    service = _FakeJobService(job_status="running")
    status = _StatusStub()
    monkeypatch.setattr(ur.requests, "get", status)

    _run_guarded(_wait(service))

    assert status.calls, "the loop must have polled before deciding"
    assert (
        JobStatus.CANCELLED in service.statuses()
    ), "a Nextflow-side cancellation must be written to the job"
    assert any(
        "cancel" in (getattr(log, "message", "") or "").lower() for log in service.logs
    ), "the cancellation must leave a JobLog explaining it"


@pytest.mark.parametrize("already", ["completed", "failed"])
def test_nextflow_cancellation_never_overwrites_a_finished_job(monkeypatch, already):
    """Containers and app.main finish jobs the poll never sees; do not stomp them.

    ('cancelled' is not parametrised: the cancellation read at the top of the loop
    already breaks out before the status poll for that one.)
    """
    service = _FakeJobService(job_status=already)
    status = _StatusStub()
    monkeypatch.setattr(ur.requests, "get", status)

    _run_guarded(_wait(service))

    assert (
        JobStatus.CANCELLED not in service.statuses()
    ), f"a {already} job must not be re-marked CANCELLED"
