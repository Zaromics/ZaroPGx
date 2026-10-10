"""PyPGx calls SV-defined star alleles from a whole-genome alignment, and only there.

run-ngs-pipeline needs the alignment's depth of coverage and a control gene's
statistics to call structural variants; without them it returns the SNV-only call.
On NA12878 (1000 Genomes 30x) that was CYP2D6 *3/*4 against GeT-RM's *3/*68+*4,
and PyPGx called *3/*68+*4 once given both inputs. /create-input-vcf, which has the
alignment, now prepares them behind a whole-genome depth gate and records their
paths in the VCF header; /genotype honours only paths it could have written and
passes them for the genes PyPGx defines SVs for.

The sidecar module is imported out of its source with psutil and the job client
stubbed (as in tests/test_sidecar_chunked_io.py); pysam and the pypgx CLI are
stubbed per test.
"""

from __future__ import annotations

import gzip
import importlib.util
import logging
import subprocess
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SOURCE = (
    Path(__file__).resolve().parent.parent / "docker" / "pypgx" / "pypgx_wrapper.py"
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
    root = tmp_path_factory.mktemp("pypgx_sv_home")
    before = list(logging.root.handlers)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATA_DIR", str(root / "data"))
        mp.setenv("TMPDIR", str(root / "tmp"))
        mp.setenv("REFERENCE_DIR", str(root / "reference"))
        mp.setenv("PYPGX_PROGRESS_LOG", str(root / "pypgx_progress.log"))
        mp.setitem(sys.modules, "psutil", _fake_psutil())
        mp.setitem(sys.modules, "job_client", _fake_job_client())
        spec = importlib.util.spec_from_file_location("zaropgx_pypgx_sv_test", SOURCE)
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


SV_GENES = frozenset({"CYP2D6", "GSTM1", "GSTT1", "UGT2B17"})


@pytest.fixture()
def sv_genes(pypgx, monkeypatch):
    monkeypatch.setattr(pypgx, "sv_target_genes", lambda: SV_GENES)


# ---------------------------------------------------------------------------
# The whole-genome depth gate
# ---------------------------------------------------------------------------


class _Read:
    def __init__(self, start, end, mapq=60, **flags):
        self.reference_start, self.reference_end = start, end
        self.mapping_quality = mapq
        self.is_unmapped = flags.get("unmapped", False)
        self.is_secondary = flags.get("secondary", False)
        self.is_supplementary = flags.get("supplementary", False)
        self.is_duplicate = flags.get("duplicate", False)


def _fake_pysam(depth_per_window, contigs=None, extra_reads=()):
    """An alignment with `depth_per_window` x coverage of 100 bp reads per window."""

    class AlignmentFile:
        def __init__(self, path):
            self.references = contigs or [f"chr{n}" for n in range(1, 23)]

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def fetch(self, contig, start, end):
            n = int(depth_per_window * (end - start) / 100)
            step = (end - start) // max(n, 1)
            reads = [_Read(start + i * step, start + i * step + 100) for i in range(n)]
            return reads + [r for r in extra_reads if r.reference_start < end]

    return types.SimpleNamespace(AlignmentFile=AlignmentFile)


def test_whole_genome_depth_reads_the_probe_windows(pypgx, monkeypatch):
    monkeypatch.setitem(sys.modules, "pysam", _fake_pysam(30))
    assert pypgx.whole_genome_depth("x.bam", "GRCh38") == pytest.approx(30, rel=0.02)


def test_unusable_reads_do_not_count(pypgx, monkeypatch):
    junk = [
        _Read(0, 10**9, mapq=0),
        _Read(0, 10**9, duplicate=True),
        _Read(0, 10**9, secondary=True),
        _Read(0, 10**9, supplementary=True),
    ]
    monkeypatch.setitem(sys.modules, "pysam", _fake_pysam(0, extra_reads=junk))
    assert pypgx.whole_genome_depth("x.bam", "GRCh38") == 0


def test_contig_names_without_chr_are_probed_too(pypgx, monkeypatch):
    contigs = [str(n) for n in range(1, 23)]
    monkeypatch.setitem(sys.modules, "pysam", _fake_pysam(30, contigs=contigs))
    assert pypgx.whole_genome_depth("x.bam", "GRCh38") == pytest.approx(30, rel=0.02)


def test_other_builds_are_not_probed(pypgx):
    assert pypgx.whole_genome_depth("x.bam", "GRCh37") is None


class _Recorder:
    def __init__(self, fail_on=None):
        self.calls, self.fail_on = [], fail_on

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        out = cmd[2] if cmd[1] == "prepare-depth-of-coverage" else cmd[3]
        Path(out).write_bytes(b"zip")
        code = 1 if cmd[1] == self.fail_on else 0
        return subprocess.CompletedProcess(cmd, code, "", "boom" if code else "")


def test_a_panel_gets_no_sv_inputs(pypgx, monkeypatch, tmp_path, sv_genes):
    monkeypatch.setattr(pypgx, "whole_genome_depth", lambda *a: 0.0)
    recorder = _Recorder()
    monkeypatch.setattr(pypgx.subprocess, "run", recorder)

    assert pypgx.prepare_sv_inputs("x.bam", str(tmp_path / "s"), "GRCh38") is None
    assert recorder.calls == [], "no PyPGx SV command may run for a panel"


def test_a_whole_genome_gets_both_inputs(pypgx, monkeypatch, tmp_path, sv_genes):
    monkeypatch.setattr(pypgx, "whole_genome_depth", lambda *a: 33.0)
    recorder = _Recorder()
    monkeypatch.setattr(pypgx.subprocess, "run", recorder)

    depth, control = pypgx.prepare_sv_inputs("x.bam", str(tmp_path / "s"), "GRCh38")

    assert depth.endswith(".depth-of-coverage.zip")
    assert control.endswith(".control-statistics.zip")
    prepare, stats = recorder.calls
    assert prepare[:4] == ["pypgx", "prepare-depth-of-coverage", depth, "x.bam"]
    assert prepare[prepare.index("--genes") + 1 :] == sorted(SV_GENES)
    assert stats[:5] == ["pypgx", "compute-control-statistics", "VDR", control, "x.bam"]


@pytest.mark.parametrize(
    "failing", ["prepare-depth-of-coverage", "compute-control-statistics"]
)
def test_a_failed_pypgx_step_leaves_no_partial_inputs(
    pypgx, monkeypatch, tmp_path, sv_genes, failing
):
    monkeypatch.setattr(pypgx, "whole_genome_depth", lambda *a: 33.0)
    monkeypatch.setattr(pypgx.subprocess, "run", _Recorder(fail_on=failing))

    assert pypgx.prepare_sv_inputs("x.bam", str(tmp_path / "s"), "GRCh38") is None
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# The header line: only paths this sidecar could have written
# ---------------------------------------------------------------------------


def _vcf(tmp_path, header_extra=""):
    path = tmp_path / "in.vcf.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(
            "##fileformat=VCFv4.2\n"
            + header_extra
            + "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS\n"
            + "chr22\t42127941\t.\tG\tA\t50\tPASS\t.\tGT\t0/1\n"
        )
    return path


def _sv_files(temp_dir, job="job1"):
    job_dir = Path(temp_dir) / job
    job_dir.mkdir(parents=True, exist_ok=True)
    depth = job_dir / "abc.depth-of-coverage.zip"
    control = job_dir / "abc.control-statistics.zip"
    depth.write_bytes(b"zip")
    control.write_bytes(b"zip")
    return str(depth), str(control)


@pytest.mark.parametrize(
    "job",
    [
        "job1",
        # The per-job working directory, temp/pypgx/<job>/<random>. A check
        # pinned to temp/<dir> turned SV calling off for every job in it.
        "pypgx/2b7763a0-4fdf-4102-9437-2024a478842c/8ada20b6",
    ],
)
def test_the_header_line_round_trips(pypgx, tmp_path, job):
    depth, control = _sv_files(tmp_path / "temp", job=job)
    vcf = _vcf(tmp_path, pypgx.sv_inputs_header_line(depth, control) + "\n")

    assert pypgx.sv_inputs_from_vcf(str(vcf), tmp_path / "temp") == (depth, control)


def test_an_uploaded_vcf_without_the_line_has_no_sv_inputs(pypgx, tmp_path):
    assert pypgx.sv_inputs_from_vcf(str(_vcf(tmp_path)), tmp_path / "temp") is None


@pytest.mark.parametrize(
    "rewrite",
    [
        lambda d, c: (d.replace("/temp/", "/elsewhere/"), c),  # outside temp_dir
        lambda d, c: (d, c.replace(".control-statistics.zip", ".zip")),  # wrong name
        lambda d, c: (d, c.replace("abc.", "missing.")),  # not on disk
        lambda d, c: (d, c.replace("/job1/", "/job2/")),  # two job directories
        lambda d, c: (d.replace("/job1/", "/job1/../../"), c),  # walks out
        lambda d, c: (
            d.replace("/job1/", "/"),
            c.replace("/job1/", "/"),
        ),  # temp itself
    ],
)
def test_paths_the_sidecar_did_not_write_are_ignored(pypgx, tmp_path, rewrite):
    depth, control = _sv_files(tmp_path / "temp")
    _sv_files(tmp_path / "temp", job="job2")
    for name in ("abc.depth-of-coverage.zip", "abc.control-statistics.zip"):
        (tmp_path / "temp" / name).write_bytes(b"zip")  # in temp itself
    elsewhere = tmp_path / "elsewhere" / "job1"
    elsewhere.mkdir(parents=True)
    (elsewhere / "abc.depth-of-coverage.zip").write_bytes(b"zip")
    vcf = _vcf(tmp_path, pypgx.sv_inputs_header_line(*rewrite(depth, control)) + "\n")

    assert pypgx.sv_inputs_from_vcf(str(vcf), tmp_path / "temp") is None


# ---------------------------------------------------------------------------
# run-ngs-pipeline gets the inputs for SV genes only
# ---------------------------------------------------------------------------


def _run(pypgx, monkeypatch, tmp_path, gene, sv_inputs):
    calls = []

    class Popen:
        def __init__(self, cmd, **kwargs):
            calls.append(cmd)
            self.pid, self.returncode = 4242, 0

        def communicate(self, timeout=None):
            return "", ""

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(pypgx.subprocess, "Popen", Popen)
    monkeypatch.setattr(pypgx, "parse_pypgx_results", lambda *a: ("*1/*1", {}))
    pypgx.run_pypgx(
        str(tmp_path / "in.vcf.gz"),
        str(tmp_path),
        gene,
        "GRCh38",
        job_id=None,
        sv_inputs=sv_inputs,
    )
    return next(c for c in calls if c[:2] == ["pypgx", "run-ngs-pipeline"])


def test_an_sv_gene_gets_depth_and_control(pypgx, monkeypatch, tmp_path, sv_genes):
    cmd = _run(pypgx, monkeypatch, tmp_path, "CYP2D6", ("d.zip", "c.zip"))
    assert cmd[cmd.index("--depth-of-coverage") + 1] == "d.zip"
    assert cmd[cmd.index("--control-statistics") + 1] == "c.zip"


def test_a_gene_without_svs_does_not(pypgx, monkeypatch, tmp_path, sv_genes):
    cmd = _run(pypgx, monkeypatch, tmp_path, "CYP2C19", ("d.zip", "c.zip"))
    assert "--depth-of-coverage" not in cmd and "--control-statistics" not in cmd


def test_no_sv_inputs_keeps_the_snv_only_call(pypgx, monkeypatch, tmp_path, sv_genes):
    cmd = _run(pypgx, monkeypatch, tmp_path, "CYP2D6", None)
    assert "--depth-of-coverage" not in cmd


# ---------------------------------------------------------------------------
# /genotype: an uncovered deletion gene is called once SV inputs exist
# ---------------------------------------------------------------------------


def _genotype(pypgx, monkeypatch, header_extra):
    seen = {}

    async def fake_batch(genes, vcf_path, job_dir, reference_genome, **kwargs):
        seen.setdefault("genes", []).extend(genes)
        seen["sv_inputs"] = kwargs.get("sv_inputs")
        # The real batch runner returns {gene: result}.
        return {g: {"success": True, "diplotype": "*1/*1"} for g in genes}

    monkeypatch.setattr(pypgx, "process_gene_batch_parallel", fake_batch)
    body = (
        "##fileformat=VCFv4.2\n"
        "##ZaroPGx_uncovered_genes=GSTM1\n"
        + header_extra
        + "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS\n"
        "chr22\t42127941\t.\tG\tA\t50\tPASS\t.\tGT\t0/1\n"
    ).encode()
    resp = TestClient(pypgx.app).post(
        "/genotype",
        files={"file": ("in.vcf", body, "text/plain")},
        data={"genes": "CYP2D6,GSTM1", "reference_genome": "hg38"},
    )
    assert resp.status_code == 200, resp.text
    return seen, resp.json()


def test_without_sv_inputs_an_uncovered_deletion_gene_is_not_called(pypgx, monkeypatch):
    seen, body = _genotype(pypgx, monkeypatch, "")

    assert seen["genes"] == ["CYP2D6"]
    assert seen["sv_inputs"] is None
    assert body["results"]["GSTM1"]["diplotype"] is None


def test_with_sv_inputs_an_uncovered_deletion_gene_goes_to_pypgx(pypgx, monkeypatch):
    depth, control = _sv_files(pypgx.TEMP_DIR, job="sv-job")
    seen, _ = _genotype(
        pypgx, monkeypatch, pypgx.sv_inputs_header_line(depth, control) + "\n"
    )

    assert sorted(seen["genes"]) == ["CYP2D6", "GSTM1"]
    assert seen["sv_inputs"] == (depth, control)
