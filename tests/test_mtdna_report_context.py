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
    """The variant is its own evidence; a basis sentence would be noise.

    evidence_basis is deliberately "measured" (with a mean_coverage to match
    -- see test_a_measured_reference_says_so_with_its_depth), not None: a
    None-in/None-out payload would pass this assertion whether or not the
    generator actually gates the basis line on mt_rnr1 == REFERENCE, since
    neither BASIS_MEASURED nor BASIS_INFERRED would match a None basis
    regardless. This shape only passes if the REFERENCE gate is real.
    """
    ctx = _context(
        tmp_path,
        {
            "mt_rnr1": "m.1555A>G",
            "evidence_basis": "measured",
            "mean_coverage": 1331.37,
        },
    )
    assert not ctx.get("call_basis_text")


def test_region_not_covered_reads_differently_from_no_chrm_data(tmp_path):
    """Different causes, different remedies -- the reader must be able to tell.

    Pins each branch's substantive claim, not just their inequality: many wrong
    answers are also unequal, so an inequality-only assertion would still pass
    if region_not_covered silently fell through to the generic fallback (which
    reads differently from no_chrm_data too, but says nothing about coverage).
    """
    a = _context(
        tmp_path, {"mt_rnr1": None, "mt_rnr1_no_call_reason": "region_not_covered"}
    )
    b = _context(tmp_path, {"mt_rnr1": None, "mt_rnr1_no_call_reason": "no_chrm_data"})
    region_text = a["no_call_reason"].lower()
    chrm_text = b["no_call_reason"].lower()

    # Tier D's claim: mitochondrial data IS present, but MT-RNR1 coverage
    # specifically could not be established -- not "no mtDNA data at all".
    assert "mitochondrial data is present" in region_text
    assert "coverage could not be established" in region_text
    assert "no mitochondrial" not in region_text

    # no_chrm_data's claim: the opposite -- no chrM data anywhere in the file.
    assert "no" in chrm_text and "mitochondrial" in chrm_text and "chrm" in chrm_text
    assert "coverage could not be established" not in chrm_text

    assert region_text != chrm_text


def test_an_unrecognized_reason_code_hits_the_generic_fallback(tmp_path):
    """The fallback is its own case, not just a silent escape hatch.

    A result carrying a reason code this report does not recognise (e.g. an
    older format, or a future sidecar addition) must not be mistaken for one
    of the named tiers -- it gets the generic, non-specific sentence.
    """
    ctx = _context(
        tmp_path, {"mt_rnr1": None, "mt_rnr1_no_call_reason": "some_future_reason_code"}
    )
    text = ctx["no_call_reason"].lower()
    assert "could not be confirmed as reference" in text
    assert "mitochondrial data is present" not in text
    assert "chrm" not in text
