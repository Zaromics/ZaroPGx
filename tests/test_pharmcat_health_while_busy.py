"""The PharmCAT sidecar answers /health (and /cancel) while PharmCAT runs, and /health
asks for the tools' versions once.

/genotype is ``async def``, and it ran PharmCAT in-line: ``_run_and_stream`` reads the
process's output line by line until it exits, on the event loop, for the whole run. A
blocked loop cannot accept a request even for a plain ``def`` route FastAPI would run
in its threadpool, so /health timed out during every PharmCAT run, and a /cancel for
the running job could not get in to stop it. The PyPGx sidecar had the same fault and
fix (tests/test_pypgx_health_while_busy.py); like that test, this one needs a real
server -- TestClient runs each request to completion on its own -- so it starts
uvicorn in a thread and talks to it over httpx, with PharmCAT's process stubbed.

/health also ran ``pharmcat_pipeline --version`` and ``java -version`` on every call,
a Python and a JVM start for a probe made every few seconds. Neither changes while
the container runs, so they are asked until they answer and then remembered.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest

httpx = pytest.importorskip("httpx")
uvicorn = pytest.importorskip("uvicorn")

REPO = Path(__file__).resolve().parents[1]
SIDECAR = REPO / "docker" / "pharmcat" / "pharmcat.py"
# Where the handler's `from pharmcat_assume_ref import ...` resolves outside the
# image (tests/test_sidecar_chunked_io.py explains it).
ASSUME_REF_LIB = REPO / "app" / "utils"

# How long the stubbed PharmCAT run takes, and how long /health may take meanwhile.
# Docker's probe allows 5 s (compose.yml), the app's services-status 5 s a try.
WORK_SECONDS = 4.0
HEALTH_BUDGET_SECONDS = 1.5

VCF = (
    "##fileformat=VCFv4.2\n"
    "##contig=<ID=chr10,length=133797422>\n"
    '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\n"
    "chr10\t94781859\t.\tG\tA\t.\tPASS\t.\tGT\t0/1\n"
)


def _fake_psutil():
    module = types.ModuleType("psutil")
    module.virtual_memory = lambda: types.SimpleNamespace(
        total=16 * 1024**3, available=8 * 1024**3, used=8 * 1024**3, percent=50.0
    )
    module.Process = lambda *a, **k: types.SimpleNamespace(terminate=lambda: None)
    module.pid_exists = lambda pid: False
    module.NoSuchProcess = type("NoSuchProcess", (Exception,), {})
    module.AccessDenied = type("AccessDenied", (Exception,), {})
    module.TimeoutExpired = type("TimeoutExpired", (Exception,), {})
    return module


def _fake_job_client():
    module = types.ModuleType("job_client")

    class JobClient:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("no job server in tests")

    module.JobClient = JobClient
    module.create_job_client = lambda *a, **k: JobClient()
    return module


@pytest.fixture(scope="module")
def pharmcat(tmp_path_factory):
    """The sidecar module, imported out of its source (as in
    tests/test_sidecar_chunked_io.py)."""
    root = tmp_path_factory.mktemp("pharmcat_health_home")
    before = list(logging.root.handlers)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATA_DIR", str(root / "data"))
        mp.setenv("TMPDIR", str(root / "tmp"))
        mp.setenv("TEMP_DIR", str(root / "pharmcat_temp"))
        mp.setenv("REPORT_DIR", str(root / "reports"))
        mp.setenv("PHARMCAT_PROGRESS_LOG", str(root / "pharmcat_progress.log"))
        (root / "data").mkdir()
        mp.syspath_prepend(str(ASSUME_REF_LIB))
        mp.setitem(sys.modules, "psutil", _fake_psutil())
        mp.setitem(sys.modules, "job_client", _fake_job_client())
        spec = importlib.util.spec_from_file_location("zaropgx_pharmcat_health_test", SIDECAR)
        module = importlib.util.module_from_spec(spec)
        mp.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
        yield module
    for handler in list(logging.root.handlers):
        if handler not in before:
            logging.root.removeHandler(handler)
    for handler in getattr(module, "_log_handlers", []):
        handler.close()


@pytest.fixture
def server(pharmcat):
    """The sidecar's app on a real uvicorn server in a thread; its base URL."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    config = uvicorn.Config(pharmcat.app, log_level="warning", lifespan="off")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not srv.started:
        assert thread.is_alive() and time.monotonic() < deadline, "uvicorn did not start"
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        srv.should_exit = True
        thread.join(timeout=10 + WORK_SECONDS)
        sock.close()


class _SlowPharmCAT:
    """Stands in for the pharmcat_pipeline process: prints for WORK_SECONDS, a line
    at a time (the handler reads them as they come), writes the report files
    PharmCAT would, and exits 0."""

    def __init__(self, cmd, started, cwd=None, **kwargs):
        self.pid = 4242
        self.returncode = None
        self._cmd, self._cwd, self._started = cmd, cwd, started

    @property
    def stdout(self):
        self._started.set()
        steps = 8
        for i in range(steps):
            time.sleep(WORK_SECONDS / steps)
            yield f"step {i}\n"
        base = Path(self._cmd[-1]).name.split(".vcf")[0]
        Path(self._cwd, f"{base}.report.json").write_text('{"genes": {}, "drugs": {}}')
        Path(self._cwd, f"{base}.report.tsv").write_text("PharmCAT 3.4.0\nGene\tDiplotype\n")

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def kill(self):
        self.returncode = -9


def test_health_and_cancel_answer_while_pharmcat_runs(pharmcat, server, monkeypatch):
    started = threading.Event()
    real_popen = subprocess.Popen

    def popen(cmd, *args, **kwargs):
        if cmd and cmd[0] == "pharmcat_pipeline":
            return _SlowPharmCAT(cmd, started, **kwargs)
        return real_popen(cmd, *args, **kwargs)

    monkeypatch.setattr(pharmcat.subprocess, "Popen", popen)
    # The versions as if already asked: this test is about the event loop.
    monkeypatch.setattr(pharmcat, "_tool_versions", {"pharmcat": "3.4.0", "java": "21"})
    outcome = {}

    def genotype():
        try:
            outcome["response"] = httpx.post(
                f"{server}/genotype",
                files={"file": ("in.vcf", VCF, "text/plain")},
                data={"patient_id": "p1", "job_id": "11111111-2222-3333-4444-555555555555"},
                timeout=30 + WORK_SECONDS,
            )
        except Exception as exc:  # surfaced below, not lost in the thread
            outcome["error"] = exc

    worker = threading.Thread(target=genotype, daemon=True)
    worker.start()
    try:
        assert started.wait(timeout=10), "the stubbed PharmCAT run never started"
        t0 = time.monotonic()
        try:
            health = httpx.get(f"{server}/health", timeout=HEALTH_BUDGET_SECONDS)
            cancel = httpx.post(
                f"{server}/cancel",
                json={"job_id": "another-job", "patient_id": "p2", "action": "cancel"},
                timeout=HEALTH_BUDGET_SECONDS,
            )
        except httpx.TimeoutException:
            pytest.fail(
                f"/health or /cancel did not answer within {HEALTH_BUDGET_SECONDS}s while "
                "PharmCAT was running: the event loop is blocked"
            )
        latency = time.monotonic() - t0
        assert health.status_code == 200 and cancel.status_code == 200
        assert health.json()["pharmcat_version"] == "3.4.0"
    finally:
        worker.join(timeout=30 + WORK_SECONDS)
    assert "error" not in outcome, outcome.get("error")
    assert latency < 2 * HEALTH_BUDGET_SECONDS
    # And the run itself is untouched: the stubbed run's report comes back.
    resp = outcome["response"]
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True


def test_health_asks_for_the_versions_once(pharmcat, monkeypatch):
    from fastapi.testclient import TestClient

    calls = []

    def run(cmd, *args, **kwargs):
        calls.append(cmd[0])
        out = "PharmCAT 3.4.0" if cmd[0] == "pharmcat_pipeline" else 'openjdk version "21"'
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(pharmcat.subprocess, "run", run)
    monkeypatch.setattr(pharmcat, "_tool_versions", {})
    client = TestClient(pharmcat.app)
    for _ in range(3):
        body = client.get("/health").json()
        assert body["pharmcat_working"] is True
        assert body["pharmcat_version"] == "PharmCAT 3.4.0"
        assert body["java_version"] == 'openjdk version "21"'
    assert calls == ["pharmcat_pipeline", "java"]


def test_a_failed_version_check_is_asked_again(pharmcat, monkeypatch):
    from fastapi.testclient import TestClient

    answers = iter([1, 0])  # pharmcat_pipeline not ready yet, then ready

    def run(cmd, *args, **kwargs):
        code = next(answers) if cmd[0] == "pharmcat_pipeline" else 0
        return subprocess.CompletedProcess(cmd, code, stdout="v", stderr="")

    monkeypatch.setattr(pharmcat.subprocess, "run", run)
    monkeypatch.setattr(pharmcat, "_tool_versions", {})
    client = TestClient(pharmcat.app)
    assert client.get("/health").json()["status"] == "degraded"
    assert client.get("/health").json()["pharmcat_working"] is True
