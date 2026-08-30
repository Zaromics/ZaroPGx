"""The VCF path: haplogroup + MT-RNR1, and honest silence about the rest."""

import asyncio
import importlib.util
import json
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_PY = REPO_ROOT / "docker/mtdna-server-2/app.py"


def _source() -> str:
    return APP_PY.read_text(encoding="utf-8")


def test_the_endpoint_exists():
    assert '@app.post("/call-mtdna")' in _source()


def test_the_vcf_path_does_not_run_mutserve():
    """mutserve needs an alignment; on a VCF there is nothing for it to read."""
    source = _source()
    vcf_branch = source[
        source.index("def _call_from_vcf") : source.index("def _call_from_alignment")
    ]
    assert "mutserve" not in vcf_branch.lower()


def test_the_vcf_path_does_not_claim_a_report():
    """report.html needs coverage/statistics/haplocheck, which need a BAM."""
    source = _source()
    vcf_branch = source[
        source.index("def _call_from_vcf") : source.index("def _call_from_alignment")
    ]
    assert "report.Rmd" not in vcf_branch


def _vcf_branch() -> str:
    source = _source()
    return source[
        source.index("def _call_from_vcf") : source.index("def _classify_haplogroup")
    ]


def test_reference_is_gated_on_absent_to_ref():
    """Reference is a positive claim of normal risk; never a default.

    Scoped to the VCF branch's actual gating expression, not a bare
    substring anywhere in the file -- "absent_to_ref" also appears in the
    function signature and docstrings, which would stay green even if the
    gate itself were deleted. The gating decision itself now lives in
    vcf_evidence() (app/mtdna/mt_rnr1.py), checked ahead of every other
    tier -- exercised directly against real inputs in
    test_mt_rnr1_vocabulary.py's test_vcf_evidence_checks_consent_before_
    every_other_tier. This only pins that the VCF branch actually passes
    absent_to_ref through to that function rather than dropping it. See
    review round 1, findings 1 and 4 (2026-08-30).
    """
    branch = _vcf_branch()
    assert "vcf_evidence(" in branch
    assert "absent_to_ref=absent_to_ref" in branch
    assert "resolve_mt_rnr1_call(" in branch


def test_an_empty_vcf_match_is_not_promoted_without_chrm_evidence():
    """pharmcat_absent_to_ref alone must never be read as chrM evidence:
    pharmcat_positions.vcf carries no chrM position at all (mt_rnr1.py's
    module docstring), so consenting to it asserts nothing about chrM."""
    branch = _vcf_branch()
    assert "vcf_carried_chrm_data" in branch
    assert "os.path.getsize(query)" in branch


def test_a_chrm_contig_header_alone_is_not_accepted_as_evidence():
    """GATK/DRAGEN/bcftools all write the full sequence dictionary -- chrM
    included -- whether or not chrM was ever covered, so a declared ##contig
    chrM line is not proof the sample was sequenced there: a nuclear-only
    exome/panel VCF from any of those callers still carries one.
    pharmcat.example.vcf escaping an earlier version of this gate (which did
    accept the header) was luck -- an unusually minimal header, not the
    general case -- not a closed case. Pins the exact evidence expression so
    a well-meaning "let's also accept the header" edit is caught here.
    See test_a_declared_chrm_header_with_no_chrm_records_stays_a_no_call
    below for the behavioural version of this same regression. Review round
    2, finding 1 (2026-08-30).
    """
    branch = _vcf_branch()
    assert "vcf_carried_chrm_data = os.path.getsize(query) > 0" in branch
    assert "bool(contig_name)" not in branch


def test_classify_haplogroup_returns_the_coverage_columns():
    """Not_Found_Polys and Range are coverage evidence we already pay for.

    haplogrep3 is already run with --extend-report; the columns were parsed
    and thrown away. Not_Found_Polys lists expected haplogroup-defining
    polymorphisms that were NOT observed, so an empty list means nothing the
    phylogeny predicted was missing -- Tier C's second half.
    """
    source = _source()
    branch = source[source.index("async def _classify_haplogroup") :]
    assert 'field("not_found_polys")' in branch
    assert 'field("range")' in branch


def test_the_vcf_branch_delegates_the_evidence_decision():
    """The Tier C/D/E decision must be made by vcf_evidence(), not hand-rolled
    inline.

    docker/mtdna-server-2/app.py is not importable in the unit suite (see the
    fixture docstring below), so a test against its source text can only pin
    substrings -- which cannot fail if a conjunction quietly degrades to a
    disjunction, or absent_to_ref stops being checked first. That is exactly
    the failure shape review round 1 caught here: the previous version of
    this test asserted "has_variant_in_gene(" / "not_found_polys" /
    "NO_CALL_REGION_NOT_COVERED" were present in the branch, all of which
    stay true even if `and` becomes `or` in the conjunction. The fix is to
    keep the logic itself in app/mtdna/mt_rnr1.py's vcf_evidence(), which IS
    importable, and exercise it directly with real inputs -- see
    test_mt_rnr1_vocabulary.py's vcf_evidence tests for that coverage
    (including a mutation check: `and` -> `or` in the conjunction, and the
    absent_to_ref ordering, both confirmed to turn a test red). This test
    only pins that the sidecar actually delegates to that function rather
    than re-implementing the decision locally.
    """
    branch = _vcf_branch()
    assert "vcf_evidence(" in branch
    assert "resolve_mt_rnr1_call(" in branch


def test_the_response_carries_the_evidence_basis():
    assert '"evidence_basis"' in _source()


def test_an_unsupported_build_is_refused_not_silently_skipped():
    source = _source()
    assert "plan.supported" in source
    assert "HTTPException" in source


# --- behavioural coverage: import the sidecar out-of-container -------------
#
# docker/mtdna-server-2/app.py is not importable the way app.* is: it reads
# /job-client off sys.path and imports psutil, which the dev venv does not
# ship (see tests/test_log_rotation_252.py's docstring). The fixture below
# stubs both, repoints DATA_DIR at a temp tree, and puts this repo's own
# app/mtdna package on sys.path so app.py's `from mtdna.builds import ...` /
# `from mtdna.mt_rnr1 import ...` (which the container resolves via
# /mtdna-lib) resolve to the real, tested module -- the same technique
# tests/test_gatk_api_no_mock_bam.py's `gatk_api` fixture uses. This buys
# real behavioural coverage of the evidence gate with an actual bcftools
# pipeline, rather than a source-text pin alone.


def _fake_psutil() -> types.ModuleType:
    # Imported by app.py but never called anywhere in it -- a plain stand-in
    # is enough.
    return types.ModuleType("psutil")


def _fake_job_client() -> types.ModuleType:
    """Stand-in for app/utils/job_client.py, which lives at /job-client in
    the image. Never actually instantiated here: _call_from_vcf only builds
    a JobClient when a job_id is passed, and the test below passes none."""
    module = types.ModuleType("job_client")

    class JobClient:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("no job server in tests")

    module.JobClient = JobClient
    return module


@pytest.fixture(scope="module")
def mtdna_app(tmp_path_factory):
    """Import the sidecar module, pointed at a temp data tree and a locally
    indexed copy of the vendored rCRS FASTA (the image builds this index at
    build time via `samtools faidx`; the dev venv has neither the image's
    RCRS_FASTA path nor its index, so both are staged here)."""
    root = tmp_path_factory.mktemp("mtdna_home")

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATA_DIR", str(root / "data"))
        mp.setitem(sys.modules, "psutil", _fake_psutil())
        mp.setitem(sys.modules, "job_client", _fake_job_client())
        mp.syspath_prepend(str(REPO_ROOT / "app"))

        spec = importlib.util.spec_from_file_location(
            "zaropgx_mtdna_app_under_test", APP_PY
        )
        module = importlib.util.module_from_spec(spec)
        mp.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)

        rcrs_fasta = root / "rcrs_mutserve.fasta"
        shutil.copyfile(
            REPO_ROOT / "docker/mtdna-server-2/files/rcrs_mutserve.fasta",
            rcrs_fasta,
        )
        subprocess.run(["samtools", "faidx", str(rcrs_fasta)], check=True)
        module.RCRS_FASTA = str(rcrs_fasta)

        yield module


@pytest.mark.skipif(
    shutil.which("bcftools") is None or shutil.which("samtools") is None,
    reason="bcftools/samtools not on PATH",
)
def test_a_declared_chrm_header_with_no_chrm_records_stays_a_no_call(
    mtdna_app, tmp_path
):
    """The exact regression the evidence gate above exists to prevent: a VCF
    whose header declares a chrM contig (so `contig_name` is truthy -- the
    old, wrong evidence source) but that carries zero chrM records -- a
    realistic nuclear-only exome/panel VCF from GATK/DRAGEN/bcftools, none of
    which omit chrM from the sequence dictionary just because it was never
    covered. Even with absent_to_ref=true, this must stay a no-call naming
    the absent chrM data, never Reference. If `bool(contig_name) or` is
    restored in docker/mtdna-server-2/app.py, this fails: contig_name is
    "chrM" here, which used to be sufficient on its own to promote to
    Reference. See review round 2, finding 1 (2026-08-30).
    """
    vcf_path = tmp_path / "no_chrm_data.vcf"
    vcf_path.write_text(
        "##fileformat=VCFv4.2\n"
        "##contig=<ID=chr1,length=248956422>\n"
        "##contig=<ID=chrM,length=16569>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chr1\t100\t.\tA\tG\t.\tPASS\t.\n",
        encoding="utf-8",
    )
    work = tmp_path / "work"
    work.mkdir()

    result = asyncio.run(
        mtdna_app._call_from_vcf(
            str(vcf_path), str(work), "GRCh38", True, "test-no-chrm-data"
        )
    )

    assert result["mt_rnr1"] is None
    assert result["mt_rnr1_no_call_reason"] == "no_chrm_data"
    # The haplogroup guard added for the same reason (Finding 1, round 1)
    # must also withhold on this input: no chrM evidence means no root-
    # haplogroup call either.
    assert result["haplogroup"] is None
