"""Regression: the Nextflow poll loop must be bounded, and must not block the event loop.

BACKLOG 359 (residual half). ``wait_for_nextflow_completion`` was a bare ``while True:``
whose only exits were terminal Nextflow states, so a job stuck in ``running`` polled every
5s forever with no deadline and no iteration cap. Its cancellation check
(``job_service.get_job``) was also a blocking SQLAlchemy call issued straight on the
uvicorn event loop, once per iteration, for the lifetime of every job.

Every test that drives the loop wraps it in ``asyncio.wait_for``: a regression here is a
hang, and an unguarded hang wedges CI instead of failing it.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.api.routes.upload_router as ur
from app.api.models import JobStatus

GUARD_SECONDS = 10.0

NEXTFLOW_URL = "http://nextflow:5055"
JOB_ID = "job-359"
JOB_KEY = "nf-key-359"
OUTDIR = "/data/reports/patient/job-359"


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


def _run_guarded(coro, guard=GUARD_SECONDS):
    """Run ``coro`` with a hard timeout so a regression fails rather than hangs."""

    async def _main():
        return await asyncio.wait_for(coro, timeout=guard)

    return asyncio.run(_main())


def _failed_statuses(service):
    return [
        getattr(update, "status", None)
        for update in service.updates
        if getattr(update, "status", None) == JobStatus.FAILED
    ]


def _deadline_logs(service):
    return [
        log
        for log in service.logs
        if "NEXTFLOW_MAX_WAIT_SECONDS" in getattr(log, "message", "")
    ]


def test_zero_deadline_fails_the_job_instead_of_polling_forever(monkeypatch):
    """A deadline of 0 must end the coroutine, not leave the job running."""
    monkeypatch.setenv("NEXTFLOW_MAX_WAIT_SECONDS", "0")
    service = _FakeJobService(job_status="running")

    def _never_called(*args, **kwargs):  # pragma: no cover - guard
        raise AssertionError("status must not be polled once the deadline has passed")

    monkeypatch.setattr(ur.requests, "get", _never_called)

    _run_guarded(
        ur.wait_for_nextflow_completion(service, JOB_ID, NEXTFLOW_URL, JOB_KEY, OUTDIR)
    )

    assert _failed_statuses(service), "expired wait must mark the job FAILED"

    logs = _deadline_logs(service)
    assert logs, "expired wait must write a JobLog naming the deadline"
    message = logs[0].message
    assert "deadline" in message.lower()
    assert "NEXTFLOW_MAX_WAIT_SECONDS=0" in message


def test_stuck_running_job_polls_then_gives_up_at_the_deadline(monkeypatch):
    """A job that never leaves 'running' is polled, then failed at the deadline."""
    monkeypatch.setenv("NEXTFLOW_MAX_WAIT_SECONDS", "0.5")
    service = _FakeJobService(job_status="running")

    polls = []

    def _fake_get(url, timeout=None):
        polls.append(url)
        return _RunningResponse()

    monkeypatch.setattr(ur.requests, "get", _fake_get)

    real_sleep = asyncio.sleep

    async def _fast_sleep(_seconds):
        # Keep wall-clock time moving (the deadline is wall-clock) without the
        # production 5s/15s pauses.
        await real_sleep(0.01)

    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)

    _run_guarded(
        ur.wait_for_nextflow_completion(service, JOB_ID, NEXTFLOW_URL, JOB_KEY, OUTDIR)
    )

    assert polls, "the loop must poll at least once before giving up"
    assert polls[0] == f"{NEXTFLOW_URL}/status/{JOB_KEY}"
    assert _failed_statuses(service), "expired wait must mark the job FAILED"

    logs = _deadline_logs(service)
    assert logs, "expired wait must write a JobLog naming the deadline"
    assert "NEXTFLOW_MAX_WAIT_SECONDS=0.5" in logs[0].message


def test_cancellation_check_reaches_get_job_through_to_thread(monkeypatch):
    """The blocking SQLAlchemy read must be offloaded, not run on the event loop."""
    service = _FakeJobService(job_status="cancelled")

    def _never_called(*args, **kwargs):  # pragma: no cover - guard
        raise AssertionError("a cancelled job must not be polled for status")

    monkeypatch.setattr(ur.requests, "get", _never_called)

    real_to_thread = asyncio.to_thread
    offloaded = []

    async def _recording_to_thread(func, *args, **kwargs):
        offloaded.append(func)
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _recording_to_thread)

    _run_guarded(
        ur.wait_for_nextflow_completion(service, JOB_ID, NEXTFLOW_URL, JOB_KEY, OUTDIR)
    )

    assert (
        service.get_job in offloaded
    ), "job_service.get_job must be awaited via asyncio.to_thread"


def test_default_deadline_is_generous_and_configurable(monkeypatch):
    """Unset means hours, not minutes: a real WGS run must not be killed."""
    monkeypatch.delenv("NEXTFLOW_MAX_WAIT_SECONDS", raising=False)
    default = ur._nextflow_max_wait_seconds()
    assert default >= 3600, "the default must be generous enough for a long WGS run"

    monkeypatch.setenv("NEXTFLOW_MAX_WAIT_SECONDS", "120")
    assert ur._nextflow_max_wait_seconds() == pytest.approx(120.0)


@pytest.mark.parametrize("raw", ["", "   ", "forever", "-5"])
def test_unusable_deadline_values_fall_back_or_clamp(monkeypatch, raw):
    """A typo in the env must not disable the cap or produce a negative deadline."""
    monkeypatch.setenv("NEXTFLOW_MAX_WAIT_SECONDS", raw)
    value = ur._nextflow_max_wait_seconds()
    assert value >= 0


def test_wait_deadline_knob_is_documented_in_env_example():
    example = Path(__file__).resolve().parent.parent / ".env.example"
    assert "NEXTFLOW_MAX_WAIT_SECONDS" in example.read_text(encoding="utf-8")
