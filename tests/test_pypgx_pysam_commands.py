"""pysam's samtools commands run in a child process, so they cannot freeze the sidecar.

Found 2026-10-24: the PyPGx sidecar's /health still stalled for ~30 s at a time during
/create-input-vcf, after the 0.4.0 fix that moved its blocking work off the event loop
(tests/test_pypgx_health_while_busy.py). That fix put the work in a thread, and a
thread is enough while the work waits -- on a subprocess, a lock, a sleep -- but not
while it holds the GIL. pysam's samtools commands do: pysam.sort, pysam.index and
pysam.faidx run samtools in this process, in C, without releasing it. Measured in the
0.4.0 image with a 10 ms ticker thread: a 600 MB sort took 55 s and the ticker's
longest gap was 55 s; pysam.index and pysam.view -c, the same; iterating the reads in
Python, 0.01 s. /create-input-vcf indexes the uploaded alignment (minutes for a 30x
genome) and sorts and indexes the re-aligned ALT loci, so /health timed out while they
ran. They now go through pysam_command, which runs them in a child Python.
"""

from __future__ import annotations

import importlib.util
import logging
import random
import re
import sys
import threading
import time
import types
from pathlib import Path

import pytest

pysam = pytest.importorskip("pysam")

REPO = Path(__file__).resolve().parents[1]
WRAPPER = REPO / "docker" / "pypgx" / "pypgx_wrapper.py"

# The ticker's longest allowed gap while a command runs in the child. The in-process
# command below holds the GIL for about as long as it takes (0.3-0.7 s here).
TICK_SECONDS = 0.01
GAP_BUDGET_SECONDS = 0.25


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
    tests/test_pypgx_health_while_busy.py)."""
    root = tmp_path_factory.mktemp("pypgx_pysam_home")
    before = list(logging.root.handlers)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATA_DIR", str(root / "data"))
        mp.setenv("TMPDIR", str(root / "tmp"))
        mp.setenv("REFERENCE_DIR", str(root / "reference"))
        mp.setenv("PYPGX_PROGRESS_LOG", str(root / "pypgx_progress.log"))
        mp.setitem(sys.modules, "psutil", _fake_psutil())
        mp.setitem(sys.modules, "job_client", _fake_job_client())
        spec = importlib.util.spec_from_file_location("zaropgx_pypgx_pysam_test", WRAPPER)
        module = importlib.util.module_from_spec(spec)
        mp.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
        yield module
    for handler in list(logging.root.handlers):
        if handler not in before:
            logging.root.removeHandler(handler)
    for handler in getattr(module, "_log_handlers", []):
        handler.close()


def _unsorted_bam(path: Path, reads: int) -> Path:
    header = {"HD": {"VN": "1.6", "SO": "unsorted"}, "SQ": [{"SN": "chr1", "LN": 10_000_000}]}
    rnd = random.Random(1)
    quals = pysam.qualitystring_to_array("I" * 100)
    with pysam.AlignmentFile(str(path), "wb", header=header) as out:
        for i in range(reads):
            read = pysam.AlignedSegment(out.header)
            read.query_name = f"r{i}"
            read.query_sequence = "ACGT" * 25
            read.reference_id = 0
            read.reference_start = rnd.randrange(9_000_000)
            read.mapping_quality = 60
            read.cigarstring = "100M"
            read.query_qualities = quals
            out.write(read)
    return path


def _longest_gap_while(fn) -> tuple[float, float]:
    """(fn's duration, a ticker thread's longest gap while fn ran)."""
    gaps, stop = [], threading.Event()

    def tick():
        last = time.monotonic()
        while not stop.is_set():
            time.sleep(TICK_SECONDS)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    ticker = threading.Thread(target=tick, daemon=True)
    ticker.start()
    t0 = time.monotonic()
    try:
        fn()
    finally:
        duration = time.monotonic() - t0
        stop.set()
        ticker.join()
    return duration, max(gaps)


def test_a_sort_in_the_child_leaves_the_other_threads_running(pypgx, tmp_path):
    unsorted = _unsorted_bam(tmp_path / "unsorted.bam", 600_000)
    out = tmp_path / "sorted.bam"

    duration, gap = _longest_gap_while(
        lambda: pypgx.pysam_command("sort", "-o", str(out), str(unsorted))
    )
    assert gap < GAP_BUDGET_SECONDS, (
        f"another thread was held for {gap:.2f}s of the {duration:.2f}s sort"
    )
    with pysam.AlignmentFile(str(out)) as bam:
        starts = [r.reference_start for r in bam.fetch(until_eof=True)]
    assert len(starts) == 600_000 and starts == sorted(starts)


def test_index_and_faidx_write_their_indexes(pypgx, tmp_path):
    unsorted = _unsorted_bam(tmp_path / "u.bam", 1000)
    bam = tmp_path / "s.bam"
    pypgx.pysam_command("sort", "-o", str(bam), str(unsorted))
    pypgx.pysam_command("index", str(bam))
    assert (tmp_path / "s.bam.bai").is_file()

    fasta = tmp_path / "ref.fa"
    fasta.write_text(">chr1\n" + "ACGT" * 100 + "\n")
    pypgx.pysam_command("faidx", str(fasta))
    assert (tmp_path / "ref.fa.fai").read_text().startswith("chr1\t400\t")


def test_a_failed_command_raises_with_its_error_line(pypgx, tmp_path):
    # The child's last line: pysam's error (SamtoolsError, or OSError for a missing file).
    with pytest.raises(RuntimeError, match=r"pysam index failed: .*missing\.bam"):
        pypgx.pysam_command("index", str(tmp_path / "missing.bam"))


def test_the_wrapper_calls_no_pysam_command_in_process():
    """Every samtools command goes through pysam_command; a direct pysam.sort/index/
    faidx/view call back in the wrapper would hold the GIL again."""
    source = WRAPPER.read_text(encoding="utf-8")
    direct = re.findall(r"\bpysam\.(sort|index|faidx|view|merge|depth|idxstats)\(", source)
    assert direct == [], f"in-process pysam commands: {direct}"
