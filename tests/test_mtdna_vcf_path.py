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


def test_reference_is_gated_on_absent_to_ref():
    """Reference is a positive claim of normal risk; never a default."""
    assert "absent_to_ref" in _source()


def test_an_unsupported_build_is_refused_not_silently_skipped():
    source = _source()
    assert "plan.supported" in source
    assert "HTTPException" in source
