"""The PyPGx sidecar's /health answers while it is genotyping.

Found 2026-10-20 on the 0.4.0 release candidate: while PyPGx ran, the sidecar's
/health timed out. Docker's health log alternated OK / timeout, and the app's
/services-status answered "pypgx: Failed after 2 retries" (5 s timeout per try), so
the page could show PyPGx as unavailable in the middle of a run that then finished.

/genotype and /create-input-vcf are ``async def``, and both did their long blocking
work on the event loop: /genotype waited on the PyPGx threads with
``concurrent.futures.as_completed`` (a blocking wait), and /create-input-vcf ran
PyPGx's create-input-vcf in-line. /health is a plain ``def`` FastAPI runs in its
threadpool, but a blocked loop cannot even accept the request to hand it there. Each
gene that finished let the loop run once, which is why every other probe got through.

Only a real server shows this -- TestClient runs each request to completion on its
own -- so these start uvicorn in a thread and talk to it over httpx, with PyPGx's work
stubbed to sleep.
"""

from __future__ import annotations

import importlib.util
import logging
import socket
import sys
import threading
import time
import types
from pathlib import Path

import pytest

httpx = pytest.importorskip("httpx")
uvicorn = pytest.importorskip("uvicorn")
pytest.importorskip("pysam")

REPO = Path(__file__).resolve().parents[1]
WRAPPER = REPO / "docker" / "pypgx" / "pypgx_wrapper.py"

# How long the stubbed PyPGx step takes, and how long /health may take meanwhile.
# Docker's probe allows 5 s (compose.yml), the app's services-status 5 s a try.
WORK_SECONDS = 4.0
HEALTH_BUDGET_SECONDS = 1.5

VCF = (
    "##fileformat=VCFv4.2\n"
    "##contig=<ID=chr22,length=50818468>\n"
    '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\n"
    "chr22\t42128945\t.\tC\tT\t.\tPASS\t.\tGT\t0/1\n"
)


def _fake_psutil():
    module = types.ModuleType("psutil")
    module.virtual_memory = lambda: types.SimpleNamespace(
        total=16 * 1024**3, available=8 * 1024**3, used=8 * 1024**3, percent=50.0
    )
    module.Process = lambda *a, **k: types.SimpleNamespace()
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
def pypgx(tmp_path_factory):
    """The sidecar module, imported out of its source (as in
    tests/test_pypgx_vcf_without_genotypes.py)."""
    root = tmp_path_factory.mktemp("pypgx_health_home")
    before = list(logging.root.handlers)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATA_DIR", str(root / "data"))
        mp.setenv("TMPDIR", str(root / "tmp"))
        mp.setenv("REFERENCE_DIR", str(root / "reference"))
        mp.setenv("PYPGX_PROGRESS_LOG", str(root / "pypgx_progress.log"))
        mp.setitem(sys.modules, "psutil", _fake_psutil())
        mp.setitem(sys.modules, "job_client", _fake_job_client())
        spec = importlib.util.spec_from_file_location(
            "zaropgx_pypgx_health_test", WRAPPER
        )
        module = importlib.util.module_from_spec(spec)
        mp.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
        module.TEMP_DIR.mkdir(parents=True, exist_ok=True)
        yield module
    for handler in list(logging.root.handlers):
        if handler not in before:
            logging.root.removeHandler(handler)
    for handler in getattr(module, "_log_handlers", []):
        handler.close()


@pytest.fixture
def server(pypgx):
    """The sidecar's app on a real uvicorn server in a thread; its base URL."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    config = uvicorn.Config(pypgx.app, log_level="warning", lifespan="off")
    srv = uvicorn.Server(config)
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
        thread.join(timeout=10 + WORK_SECONDS)
        sock.close()


def _health_while(base_url, started: threading.Event, request):
    """Start `request` in a thread; once the stubbed work has begun, time /health.

    Returns (/health's latency, `request`'s response); fails if /health does not
    answer in budget. Waits for `request` to finish either way."""
    outcome = {}

    def call():
        try:
            outcome["response"] = request()
        except Exception as exc:  # surfaced below, not lost in the thread
            outcome["error"] = exc

    worker = threading.Thread(target=call, daemon=True)
    worker.start()
    try:
        assert started.wait(timeout=10), "the stubbed work never started"
        t0 = time.monotonic()
        try:
            resp = httpx.get(f"{base_url}/health", timeout=HEALTH_BUDGET_SECONDS)
        except httpx.TimeoutException:
            pytest.fail(
                f"/health did not answer within {HEALTH_BUDGET_SECONDS}s while the "
                "sidecar was working: the event loop is blocked"
            )
        latency = time.monotonic() - t0
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"
    finally:
        worker.join(timeout=30 + WORK_SECONDS)
    assert "error" not in outcome, outcome.get("error")
    return latency, outcome["response"]


def test_health_answers_while_genotype_runs_pypgx(pypgx, server, monkeypatch):
    started = threading.Event()

    def slow_run_pypgx(
        vcf_path, output_dir, gene, reference_genome="hg19", job_id=None, sv_inputs=None
    ):
        started.set()
        time.sleep(WORK_SECONDS)
        return {"success": True, "gene": gene, "diplotype": "*1/*4", "details": {}}

    # PyPGx itself and the tools the test host lacks (bgzip, tabix, bcftools) are
    # stubbed; process_gene_batch_parallel -- where the blocking wait was -- is real.
    monkeypatch.setattr(pypgx, "run_pypgx", slow_run_pypgx)
    monkeypatch.setattr(pypgx, "prepare_vcf_for_pypgx", lambda path: str(path))
    monkeypatch.setattr(pypgx, "drop_no_calls", lambda path: path)
    monkeypatch.setattr(pypgx, "pypgx_gene_positions", lambda assembly: {})

    def genotype():
        return httpx.post(
            f"{server}/genotype",
            files={"file": ("in.vcf", VCF, "text/plain")},
            data={
                "genes": "CYP2D6",
                "reference_genome": "hg38",
                "absent_to_ref": "true",
            },
            timeout=30 + WORK_SECONDS,
        )

    latency, resp = _health_while(server, started, genotype)

    assert latency < HEALTH_BUDGET_SECONDS
    # And the run itself is untouched: the stubbed call comes back as the result.
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["results"]["CYP2D6"]["diplotype"] == "*1/*4"


def test_health_answers_while_create_input_vcf_runs(pypgx, server, monkeypatch):
    started = threading.Event()

    def slow_create_input_vcf(alignment_path, output_vcf_gz, assembly):
        started.set()
        time.sleep(WORK_SECONDS)
        return {"success": True}

    monkeypatch.setattr(pypgx, "run_pypgx_create_input_vcf", slow_create_input_vcf)

    def create_input_vcf():
        return httpx.post(
            f"{server}/create-input-vcf",
            files={"file": ("in.bam", b"not a real BAM", "application/octet-stream")},
            data={"reference_genome": "hg38"},
            timeout=30 + WORK_SECONDS,
        )

    latency, resp = _health_while(server, started, create_input_vcf)

    assert latency < HEALTH_BUDGET_SECONDS
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True
