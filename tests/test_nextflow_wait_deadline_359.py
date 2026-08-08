"""Regression: the Nextflow poll loop must be bounded, and must not block the event loop.

BACKLOG 359 (residual half). ``wait_for_nextflow_completion`` was a bare ``while True:``
whose only exits were terminal Nextflow states, so a job stuck in ``running`` polled every
5s forever with no deadline and no iteration cap. Its cancellation check
(``job_service.get_job``) was also a blocking SQLAlchemy call issued straight on the
uvicorn event loop, once per iteration, for the lifetime of every job.

Two things these tests are deliberate about:

* Every case that drives the loop goes through ``_run_guarded``, which caps it with
  ``asyncio.wait_for``. A regression here is a hang, and an unguarded hang wedges CI
  instead of failing it.
* Nothing raises from inside the loop to signal a violation. ``wait_for_nextflow_completion``
  wraps its body in a blanket ``except Exception``, which would swallow an ``AssertionError``
  and quietly turn it into an ordinary failure — so "must not be called" is recorded on the
  fake and asserted afterwards, never raised.
"""

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

import app.api.routes.upload_router as ur
from app.api.models import JobStatus

GUARD_SECONDS = 10.0

# Short enough that the loop's clamped sleep exits well inside the guard, long enough to
# leave room for at least one poll first.
SHORT_DEADLINE = "0.25"

NEXTFLOW_URL = "http://nextflow:5055"
JOB_ID = "job-359"
JOB_KEY = "nf-key-359"
OUTDIR = "/data/reports/patient/job-359"


@pytest.fixture(autouse=True)
def _clear_wait_env(monkeypatch):
    """Never inherit the ambient deadline: app.api.db calls load_dotenv() at import."""
    monkeypatch.delenv("NEXTFLOW_MAX_WAIT_SECONDS", raising=False)


class _FakeJobService:
    """Minimal stand-in for JobService that records what the loop wrote."""

    def __init__(self, job_status="running"):
        self.job_status = job_status
        self.updates = []
        self.logs = []

    def get_job(self, job_id):
        return SimpleNamespace(id=job_id, status=self.job_status)

    def update_job(self, job_id, update):
        self.updates.append(update)
        return None

    def log_job_event(self, job_id, log_data):
        self.logs.append(log_data)
        return None


class _RunningResponse:
    """A Nextflow /status payload that never reaches a terminal state."""

    status_code = 200

    @staticmethod
    def json():
        return {"status": "running", "message": "Processing..."}


class _StatusStub:
    """Records every poll so a call that should not happen can be asserted, not raised."""

    def __init__(self, raises=None):
        self.calls = []
        self.raises = raises

    def __call__(self, url, timeout=None):
        self.calls.append(url)
        if self.raises is not None:
            raise self.raises
        return _RunningResponse()


def _run_guarded(coro, guard=GUARD_SECONDS):
    """Run ``coro`` with a hard timeout so a regression fails rather than hangs."""

    async def _main():
        return await asyncio.wait_for(coro, timeout=guard)

    return asyncio.run(_main())


def _wait(service):
    return ur.wait_for_nextflow_completion(
        service, JOB_ID, NEXTFLOW_URL, JOB_KEY, OUTDIR
    )


def _failed_updates(service):
    return [
        u for u in service.updates if getattr(u, "status", None) == JobStatus.FAILED
    ]


def _deadline_logs(service):
    return [
        log
        for log in service.logs
        if "NEXTFLOW_MAX_WAIT_SECONDS" in getattr(log, "message", "")
    ]


def test_stuck_running_job_polls_then_fails_at_the_deadline(monkeypatch):
    """A job that never leaves 'running' is polled, then failed — it must not hang."""
    monkeypatch.setenv("NEXTFLOW_MAX_WAIT_SECONDS", SHORT_DEADLINE)
    service = _FakeJobService(job_status="running")
    status = _StatusStub()
    monkeypatch.setattr(ur.requests, "get", status)

    started = time.monotonic()
    _run_guarded(_wait(service))
    elapsed = time.monotonic() - started

    assert status.calls, "the loop must poll at least once before giving up"
    assert status.calls[0] == f"{NEXTFLOW_URL}/status/{JOB_KEY}"
    assert _failed_updates(service), "an expired wait must mark the job FAILED"

    logs = _deadline_logs(service)
    assert logs, "an expired wait must write a JobLog naming the deadline"
    assert "deadline" in logs[0].message.lower()
    assert f"NEXTFLOW_MAX_WAIT_SECONDS={SHORT_DEADLINE}" in logs[0].message

    # The unclamped inter-poll sleep is 5s; finishing well inside that proves the loop
    # shortens its sleep to land on the deadline instead of overshooting it.
    assert elapsed < 4.0, f"the poll sleep was not clamped to the deadline ({elapsed}s)"


def test_deadline_also_bounds_the_status_error_path(monkeypatch):
    """A status endpoint that only ever errors must hit the deadline, not loop forever."""
    monkeypatch.setenv("NEXTFLOW_MAX_WAIT_SECONDS", SHORT_DEADLINE)
    service = _FakeJobService(job_status="running")
    status = _StatusStub(raises=requests.RequestException("nextflow runner is gone"))
    monkeypatch.setattr(ur.requests, "get", status)

    started = time.monotonic()
    _run_guarded(_wait(service))
    elapsed = time.monotonic() - started

    assert status.calls, "the error path must still have polled"
    assert _failed_updates(service), "an expired wait must mark the job FAILED"
    assert _deadline_logs(
        service
    ), "an expired wait must write a JobLog naming the limit"
    # The unclamped retry sleep on this branch is 15s.
    assert (
        elapsed < 4.0
    ), f"the retry sleep was not clamped to the deadline ({elapsed}s)"


@pytest.mark.parametrize("already", ["completed", "failed", "cancelled"])
def test_deadline_never_overwrites_a_job_another_path_finished(monkeypatch, already):
    """Containers and app.main can complete a job without the poll seeing it.

    The runner keeps job state in memory, so a runner restart makes /status 404 forever
    and the wait runs to its deadline — exactly when the job is most likely to have been
    completed by someone else. Failing it then would report a successful run as FAILED.
    """
    monkeypatch.setenv("NEXTFLOW_MAX_WAIT_SECONDS", SHORT_DEADLINE)
    service = _FakeJobService(job_status=already)
    status = _StatusStub()
    monkeypatch.setattr(ur.requests, "get", status)

    _run_guarded(_wait(service))

    assert not _failed_updates(service), f"a {already} job must not be re-marked FAILED"
    assert not _deadline_logs(
        service
    ), f"a {already} job must not get a deadline JobLog"


def test_cancellation_check_reaches_get_job_through_to_thread(monkeypatch):
    """The blocking SQLAlchemy read must be offloaded, not run on the event loop."""
    service = _FakeJobService(job_status="cancelled")
    status = _StatusStub()
    monkeypatch.setattr(ur.requests, "get", status)

    real_to_thread = asyncio.to_thread
    offloaded = []

    async def _recording_to_thread(func, *args, **kwargs):
        offloaded.append(func)
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _recording_to_thread)

    _run_guarded(_wait(service))

    assert (
        service.get_job in offloaded
    ), "job_service.get_job must be awaited via asyncio.to_thread"
    assert not status.calls, "a cancelled job must not be polled for status"


def test_default_deadline_is_generous_and_configurable(monkeypatch):
    """Unset means hours, not minutes: a real WGS run must not be killed."""
    assert ur._nextflow_max_wait_seconds() == float(
        ur.DEFAULT_NEXTFLOW_MAX_WAIT_SECONDS
    )
    assert ur.DEFAULT_NEXTFLOW_MAX_WAIT_SECONDS >= 3600

    monkeypatch.setenv("NEXTFLOW_MAX_WAIT_SECONDS", "120")
    assert ur._nextflow_max_wait_seconds() == pytest.approx(120.0)


@pytest.mark.parametrize("raw", ["", "   ", "forever", "-5", "0", "0.0"])
def test_unusable_deadline_values_fall_back_to_the_default(monkeypatch, raw):
    """A typo must neither disable the cap nor fail every job instantly.

    ``0`` conventionally reads as "no limit"; here it would mean "expire immediately",
    so it falls back rather than taking either literal meaning.
    """
    monkeypatch.setenv("NEXTFLOW_MAX_WAIT_SECONDS", raw)
    assert ur._nextflow_max_wait_seconds() == float(
        ur.DEFAULT_NEXTFLOW_MAX_WAIT_SECONDS
    )


def test_wait_deadline_knob_is_documented_in_env_example():
    example = Path(__file__).resolve().parent.parent / ".env.example"
    assert "NEXTFLOW_MAX_WAIT_SECONDS" in example.read_text(encoding="utf-8")
