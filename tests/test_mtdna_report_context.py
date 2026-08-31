"""mtdna_report_context turns the sidecar's own JSON into reader-facing text.

Its first automated coverage: nothing under tests/ referenced this function
before. The integration branch verified it by rendering templates by hand,
which is why the no-call-reason branching shipped unverified.
"""

import json
from pathlib import Path

from app.reports.generator import mtdna_report_context


def _context(tmp_path: Path, payload: dict) -> dict:
    (tmp_path / "mtdna_result.json").write_text(json.dumps(payload), encoding="utf-8")
    return mtdna_report_context(str(tmp_path))


def test_no_result_file_means_the_section_does_not_render(tmp_path):
    """A job that never touched mtDNA must look as it did before the feature."""
    assert mtdna_report_context(str(tmp_path))["used"] is False


def test_a_measured_reference_says_so_with_its_depth(tmp_path):
    ctx = _context(
        tmp_path,
        {
            "mt_rnr1": "Reference",
            "evidence_basis": "measured",
            "mean_coverage": 1331.37,
        },
    )
    text = ctx["call_basis_text"]
    assert "measured" in text.lower()
    assert "1331" in text


def test_an_inferred_reference_is_not_described_as_measured(tmp_path):
    """The distinction is the point: an inference must not read as a measurement."""
    ctx = _context(tmp_path, {"mt_rnr1": "Reference", "evidence_basis": "inferred"})
    text = ctx["call_basis_text"].lower()
    assert "inferred" in text
    assert "measured" not in text


def test_a_named_allele_gets_no_basis_line(tmp_path):
    """The variant is its own evidence; a basis sentence would be noise."""
    ctx = _context(tmp_path, {"mt_rnr1": "m.1555A>G", "evidence_basis": None})
    assert not ctx.get("call_basis_text")


def test_region_not_covered_reads_differently_from_no_chrm_data(tmp_path):
    """Different causes, different remedies -- the reader must be able to tell."""
    a = _context(
        tmp_path, {"mt_rnr1": None, "mt_rnr1_no_call_reason": "region_not_covered"}
    )
    b = _context(tmp_path, {"mt_rnr1": None, "mt_rnr1_no_call_reason": "no_chrm_data"})
    assert a["no_call_reason"]
    assert a["no_call_reason"] != b["no_call_reason"]
