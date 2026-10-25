"""PyPGx reads REF and ALT in capitals, whatever case the VCF's caller wrote them in.

Found 2026-10-24 scoring ZaroPGx against GeT-RM on 1000 Genomes 30x genomes
(scripts/getrm/): CYP1A2 came out *1A/*1C where GeT-RM has *1A/*1L or *1C/*1F. The
VCF PyPGx's create-input-vcf wrote held -2467delT, *1L's third variant, as
`chr15 74747271 at a`: bcftools copies the reference's case, and pypgx_grch38.fasta is
soft-masked. PyPGx matches its allele table ("15-74747271-AT-A") by string, so the
deletion was invisible and *1L could not be called. 29 such records in 54 runs, all
this one; 74 of one VCF's 9,362 records were lower-case indels. A VCF from any caller
run against a soft-masked reference has the same problem, so /genotype puts every VCF's
alleles in capitals before PyPGx reads it.
"""

from __future__ import annotations

import importlib.util
import logging
import re
import sys
import types
from pathlib import Path

import pytest

pysam = pytest.importorskip("pysam")

REPO = Path(__file__).resolve().parents[1]
WRAPPER = REPO / "docker" / "pypgx" / "pypgx_wrapper.py"


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
    root = tmp_path_factory.mktemp("pypgx_allele_case_home")
    before = list(logging.root.handlers)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATA_DIR", str(root / "data"))
        mp.setenv("TMPDIR", str(root / "tmp"))
        mp.setenv("REFERENCE_DIR", str(root / "reference"))
        mp.setenv("PYPGX_PROGRESS_LOG", str(root / "pypgx_progress.log"))
        mp.setitem(sys.modules, "psutil", _fake_psutil())
        mp.setitem(sys.modules, "job_client", _fake_job_client())
        spec = importlib.util.spec_from_file_location(
            "zaropgx_pypgx_allele_case_test", WRAPPER
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


def _vcf(path: Path, rows) -> str:
    """rows: (contig, pos, ref, alt, genotype); bgzipped and indexed, like
    create-input-vcf's output."""
    lines = [
        "##fileformat=VCFv4.2",
        "##contig=<ID=15>",
        "##contig=<ID=22>",
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
        "\t".join(
            [
                "#CHROM",
                "POS",
                "ID",
                "REF",
                "ALT",
                "QUAL",
                "FILTER",
                "INFO",
                "FORMAT",
                "S",
            ]
        ),
    ]
    for contig, pos, ref, alt, gt in rows:
        lines.append(
            "\t".join([contig, str(pos), ".", ref, alt, "50", "PASS", ".", "GT", gt])
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    gz = str(path) + ".gz"
    pysam.tabix_compress(str(path), gz, force=True)
    pysam.tabix_index(gz, preset="vcf", force=True)
    return gz


def _records(vcf_gz: str):
    with pysam.VariantFile(vcf_gz) as vcf:
        return [(r.contig, r.pos, r.alleles, r.samples[0]["GT"]) for r in vcf]


def test_lower_case_alleles_are_read_in_capitals(pypgx, tmp_path):
    src = _vcf(
        tmp_path / "calls.vcf",
        [
            (
                "15",
                74745879,
                "G",
                "A",
                "0/1",
            ),  # *1C, as bcftools writes it outside repeats
            ("15", 74747271, "at", "a", "0/1"),  # -2467delT, inside one
            ("22", 42126611, "C", "<DEL>", "0/1"),  # symbolic: left as it is
        ],
    )
    out = pypgx.uppercase_alleles(src)
    assert out != src
    assert _records(out) == [
        ("15", 74745879, ("G", "A"), (0, 1)),
        ("15", 74747271, ("AT", "A"), (0, 1)),
        ("22", 42126611, ("C", "<DEL>"), (0, 1)),
    ]
    # Indexed, so PyPGx can slice it to each gene's region.
    with pysam.VariantFile(out) as vcf:
        assert [r.pos for r in vcf.fetch("15", 74747000, 74748000)] == [74747271]


def test_a_vcf_already_in_capitals_is_used_as_it_is(pypgx, tmp_path):
    src = _vcf(tmp_path / "upper.vcf", [("15", 74747271, "AT", "A", "0/1")])
    assert pypgx.uppercase_alleles(src) == src


def test_genotype_capitalises_after_dropping_no_calls():
    # Every lane's VCF goes through /genotype's two steps, in this order, before
    # PyPGx reads it.
    source = WRAPPER.read_text(encoding="utf-8")
    drop = source.index(
        "vcf_for_pypgx = await asyncio.to_thread(drop_no_calls, vcf_for_pypgx)"
    )
    upper = source.index(
        "vcf_for_pypgx = await asyncio.to_thread(uppercase_alleles, vcf_for_pypgx)"
    )
    assert drop < upper
    assert len(re.findall(r"to_thread\(uppercase_alleles,", source)) == 1
