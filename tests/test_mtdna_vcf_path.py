"""The VCF path: haplogroup + MT-RNR1, and honest silence about the rest."""

import json
from pathlib import Path

import pytest

APP_PY = Path(__file__).resolve().parent.parent / "docker/mtdna-server-2/app.py"


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
    gate itself were deleted. The real behaviour (a real match always wins;
    without positive evidence the call stays a no-call; an unresolved delins
    at 961 blocks the promotion even with evidence) is exercised directly
    against real VcfRecord inputs in test_mt_rnr1_vocabulary.py's
    resolve_mt_rnr1_call tests -- see review round 1, finding 4 (2026-08-30).
    """
    branch = _vcf_branch()
    assert "if not absent_to_ref:" in branch
    assert "resolve_mt_rnr1_call(" in branch


def test_an_empty_vcf_match_is_not_promoted_without_chrm_evidence():
    """pharmcat_absent_to_ref alone must never be read as chrM evidence:
    pharmcat_positions.vcf carries no chrM position at all (mt_rnr1.py's
    module docstring), so consenting to it asserts nothing about chrM."""
    branch = _vcf_branch()
    assert "vcf_carried_chrm_data" in branch
    assert "os.path.getsize(query)" in branch


def test_an_unsupported_build_is_refused_not_silently_skipped():
    source = _source()
    assert "plan.supported" in source
    assert "HTTPException" in source
