"""PharmCAT's exit code and its timeout end a /genotype run, with PharmCAT's own words.

Two faults in the sidecar's _run_and_stream, found 2026-10-24:

- A non-zero exit was passed over: the CalledProcessError handler only logged, and
  _run_and_stream never raised one anyway, so a failed PharmCAT went on to fail later
  as "Required PharmCAT v3output files not found", without what PharmCAT said.
- The 300 s timeout was checked only when PharmCAT printed a line, so a run that hung
  without printing was never stopped; and stopping meant killing pharmcat_pipeline
  alone, which leaves the JVM it starts running.

Now the output is read in a thread, the timeout counts wall-clock time, a timeout
kills the whole process group, and both a failed exit and a timeout fail the run at
once with the last lines PharmCAT printed. /cancel stops the whole group too: it used
to terminate pharmcat_pipeline alone, so a cancelled job's JVM ran on to the end. The
stubs here are real executables put on PATH as pharmcat_pipeline, so the process group
is real too.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import socket
import sys
import threading
import time
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

httpx = pytest.importorskip("httpx")
uvicorn = pytest.importorskip("uvicorn")

REPO = Path(__file__).resolve().parents[1]
SIDECAR = REPO / "docker" / "pharmcat" / "pharmcat.py"
# Where the handler's `from pharmcat_assume_ref import ...` resolves outside the
# image (tests/test_sidecar_chunked_io.py explains it).
ASSUME_REF_LIB = REPO / "app" / "utils"

VCF = (
    "##fileformat=VCFv4.2\n"
    "##contig=<ID=chr10,length=133797422>\n"
    '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\n"
    "chr10\t94781859\t.\tG\tA\t.\tPASS\t.\tGT\t0/1\n"
)

FAILS = """#!/bin/sh
echo "Named Allele Matcher: reading the VCF"
echo "Exception in thread main: java.lang.IllegalStateException: bad VCF header" >&2
echo "    at org.pharmgkb.pharmcat.VcfReader.read(VcfReader.java:120)" >&2
exit 3
"""

# Prints once, starts a child (as pharmcat_pipeline starts Java), then hangs silently.
HANGS = """#!/bin/sh
echo "Named Allele Matcher: reading the VCF"
sleep 30 &
echo $! > "{child_pid}"
sleep 30
"""


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
    root = tmp_path_factory.mktemp("pharmcat_exit_home")
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
        spec = importlib.util.spec_from_file_location(
            "zaropgx_pharmcat_exit_test", SIDECAR
        )
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
    """The sidecar's app on a real uvicorn server in a thread, so /cancel can come in
    while /genotype runs (as in tests/test_pharmcat_health_while_busy.py)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    srv = uvicorn.Server(
        uvicorn.Config(pharmcat.app, log_level="warning", lifespan="off")
    )
    thread = threading.Thread(target=srv.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not srv.started:
        assert (
            thread.is_alive() and time.monotonic() < deadline
        ), "uvicorn did not start"
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        srv.should_exit = True
        thread.join(timeout=15)
        sock.close()


def _install(tmp_path, monkeypatch, script: str) -> None:
    """`script` as pharmcat_pipeline, first on PATH (the sidecar passes its own
    environment to the process)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "pharmcat_pipeline"
    exe.write_text(script)
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def _genotype(pharmcat):
    return TestClient(pharmcat.app).post(
        "/genotype",
        files={"file": ("in.vcf", VCF, "text/plain")},
        data={"patient_id": "p1"},
    )


def _running(pid: int) -> bool:
    """Whether `pid` is a live process (a zombie, killed and not yet reaped, is not)."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return False
    return state != "Z"


def test_a_failed_exit_fails_the_run_with_pharmcats_last_lines(
    pharmcat, tmp_path, monkeypatch
):
    _install(tmp_path, monkeypatch, FAILS)

    resp = _genotype(pharmcat)

    assert resp.status_code == 500, resp.text
    detail = resp.json()["detail"]
    assert detail.startswith(
        "PharmCAT failed with exit code 3. Its last lines:"
    ), detail
    assert "IllegalStateException: bad VCF header" in detail
    assert "VcfReader.java:120" in detail
    assert "output files not found" not in detail


def test_a_silent_hang_is_stopped_at_the_timeout_with_everything_it_started(
    pharmcat, tmp_path, monkeypatch
):
    child_pid = tmp_path / "child.pid"
    _install(tmp_path, monkeypatch, HANGS.format(child_pid=child_pid))
    monkeypatch.setattr(pharmcat, "PHARMCAT_TIMEOUT_SECONDS", 1.5)

    t0 = time.monotonic()
    resp = _genotype(pharmcat)
    elapsed = time.monotonic() - t0

    assert resp.status_code == 500, resp.text
    detail = resp.json()["detail"]
    assert detail.startswith(
        "PharmCAT did not finish within 1.5 s and was stopped "
        "(its process and everything it started were killed)"
    ), detail
    assert "Named Allele Matcher: reading the VCF" in detail
    # Stopped at the timeout, not when the hang ended by itself (30 s).
    assert elapsed < 15, f"took {elapsed:.1f}s"
    # The child it started went with it.
    pid = int(child_pid.read_text())
    for _ in range(50):
        if not _running(pid):
            break
        time.sleep(0.1)
    assert not _running(pid), f"child {pid} outlived the timeout"


def test_the_timeout_is_the_same_300_seconds():
    """Wall-clock now, but the same value: the only setting it had was this literal."""
    source = SIDECAR.read_text(encoding="utf-8")
    assert "PHARMCAT_TIMEOUT_SECONDS = 300\n" in source


def test_cancel_stops_pharmcat_and_everything_it_started(
    pharmcat, server, tmp_path, monkeypatch
):
    child_pid = tmp_path / "child.pid"
    _install(tmp_path, monkeypatch, HANGS.format(child_pid=child_pid))
    job_id = "11111111-2222-3333-4444-555555555555"
    outcome = {}

    def genotype():
        try:
            outcome["response"] = httpx.post(
                f"{server}/genotype",
                files={"file": ("in.vcf", VCF, "text/plain")},
                data={"patient_id": "p1", "job_id": job_id},
                timeout=60,
            )
        except Exception as exc:  # surfaced below, not lost in the thread
            outcome["error"] = exc

    worker = threading.Thread(target=genotype, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 15
        while not (child_pid.exists() and child_pid.read_text().strip()):
            assert time.monotonic() < deadline, "the stub never started its child"
            time.sleep(0.05)
        pid = int(child_pid.read_text())
        assert _running(pid)

        t0 = time.monotonic()
        cancel = httpx.post(
            f"{server}/cancel",
            json={"job_id": job_id, "patient_id": "p1", "action": "cancel"},
            timeout=10,
        )
        assert cancel.status_code == 200, cancel.text
        assert cancel.json()["terminated_processes"] == 1
    finally:
        worker.join(timeout=45)
    assert "error" not in outcome, outcome.get("error")
    # The run ended with the cancel, not when the hang ended by itself (30 s).
    assert time.monotonic() - t0 < 15
    resp = outcome["response"]
    assert resp.status_code == 500, resp.text
    assert resp.json()["detail"].startswith(
        "PharmCAT was stopped: the job was cancelled"
    ), resp.text
    # The child it started went with it.
    for _ in range(50):
        if not _running(pid):
            break
        time.sleep(0.1)
    assert not _running(pid), f"child {pid} outlived the cancel"
