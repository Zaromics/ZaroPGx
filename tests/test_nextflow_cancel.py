"""Cancelling a job must stop its Nextflow run, and only its run.

The app posted to the runner's /cancel/{patient_id}_{data_id}: a route the runner
does not have, under a key it does not use (it registers {patient_id}_{report_id},
and report_id is the job id). The 404 was logged as "may have already completed", so
Nextflow kept launching steps. Observed 2026-10-05: a cancelled NA12878 30x job went
on to run PyPGx's BAM-to-VCF conversion for 26 minutes.

The runner's real POST /cancel matched any run of the same patient, or any key
containing the id, so once the app's call arrives, a loose match would stop and
delete the files of every other run for that patient. It now matches on job id.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

RUNNER_PATH = Path(__file__).resolve().parents[1] / "docker" / "nextflow" / "runner.py"


def _load_runner():
    name = "zaropgx_nextflow_runner"
    if name in sys.modules:
        return sys.modules[name]
    os.environ.setdefault(
        "NEXTFLOW_PROGRESS_LOG",
        str(Path(tempfile.gettempdir()) / "zaropgx_nextflow_progress_test.log"),
    )
    spec = importlib.util.spec_from_file_location(name, RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


runner = _load_runner()


def test_the_app_calls_the_runners_cancel_with_the_job_id(monkeypatch):
    from app.api.routes import job_router

    calls = []

    class Response:
        status_code = 200
        text = ""

        def json(self):
            return {"terminated_processes": 1}

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr(job_router.requests, "post", fake_post)
    monkeypatch.setenv("NEXTFLOW_RUNNER_URL", "http://nextflow:5055")

    asyncio.run(
        job_router.cancel_nextflow_job(
            "job-a", {"patient_id": "patient-1", "data_id": "data-9"}
        )
    )

    ((url, kwargs),) = calls
    assert url == "http://nextflow:5055/cancel"
    assert kwargs["json"]["job_id"] == "job-a"
    # Longer than the runner's own 30 s wait for Nextflow to exit after SIGTERM.
    assert kwargs["timeout"] > 30


class _Process:
    def __init__(self):
        self.terminated = False
        self.pid = 4242

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.terminated = True


def _register(job_key, job_id, tmp_path):
    leftover = tmp_path / f"{job_id}.tmp"
    leftover.write_text("work")
    process = _Process()
    runner.running_jobs[job_key] = {
        "job_id": job_id,
        "patient_id": "patient-1",
        "report_id": job_id,
        "status": "running",
        "start_time": "2026-10-05T00:00:00+00:00",
        "nextflow_process": process,
        "cleanup_paths": [str(leftover)],
    }
    return process, leftover


def test_only_the_cancelled_job_is_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "running_jobs", {})
    a_proc, a_file = _register("patient-1_job-a", "job-a", tmp_path)
    b_proc, b_file = _register("patient-1_job-b", "job-b", tmp_path)

    resp = TestClient(runner.app).post(
        "/cancel",
        json={"job_id": "job-a", "patient_id": "patient-1", "action": "cancel"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["terminated_processes"] == 1
    assert a_proc.terminated and not a_file.exists()
    assert runner.running_jobs["patient-1_job-a"]["status"] == "cancelled"
    # The other run for the same patient is untouched.
    assert not b_proc.terminated and b_file.exists()
    assert runner.running_jobs["patient-1_job-b"]["status"] == "running"


def test_an_id_that_is_a_substring_of_another_key_matches_nothing(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(runner, "running_jobs", {})
    proc, leftover = _register("patient-1_job-abc", "job-abc", tmp_path)

    TestClient(runner.app).post(
        "/cancel",
        json={"job_id": "job-a", "patient_id": "patient-2", "action": "cancel"},
    )

    assert not proc.terminated and leftover.exists()
