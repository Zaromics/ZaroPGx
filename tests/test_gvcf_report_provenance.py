"""A genotyped-gVCF run must say so in the report, next to the assume-ref paragraph.

A gVCF upload is the one lane whose homozygous-reference calls at pharmacogene
positions are real. Every other lane either has a variant at a position or has nothing,
and "nothing" only becomes a reference call if the operator turns on PharmCAT's
``--absent-to-ref``, which fabricates it. Two reports that differ in exactly that way
would otherwise be indistinguishable on the page.

The counts come from the ``gvcf_to_vcf`` ``JobStep``'s ``output_data``, written by
gatk-api's ``/gvcf-to-vcf`` when the step completes -- not from the upload-time
``needs_gvcf_genotyping`` flag. The flag is an intention recorded before the run, the
step row is what actually happened, and only the row carries numbers.

Both halves are pinned: the paragraph builder, and that the paragraph reaches both
report lanes through the real Jinja environment -- source-text assertions have
repeatedly proved worthless in this repo. Its adjacency to the ``--absent-to-ref``
paragraph is pinned too, because that adjacency is the point: a reader who sees one
without the other cannot tell which they are looking at.
"""

from __future__ import annotations

import re

import pytest

from app.utils.gvcf_provenance import gvcf_provenance_paragraph
from app.utils.pharmcat_assume_ref import methodology_assume_ref_paragraph

TEMPLATES = ["report_template.html", "interactive_report.html"]

FULL_STATS = {
    "n_pharmcat_positions": 1226,
    "n_pgx_positions_called": 1180,
    "n_positions_absent": 46,
    "target_build": "GRCh38",
}


# --------------------------------------------------------------------------
# The paragraph builder
# --------------------------------------------------------------------------


@pytest.mark.parametrize("empty", [None, {}])
def test_no_paragraph_when_no_genotyping_ran(empty):
    """The ordinary VCF/BAM upload: no gvcf_to_vcf step row, so nothing to say."""
    assert gvcf_provenance_paragraph(empty) is None


def test_the_paragraph_says_the_reference_calls_are_called_data():
    """The claim the whole lane exists to be able to make."""
    paragraph = gvcf_provenance_paragraph(FULL_STATS)

    assert paragraph is not None
    assert "called data, not assumed" in paragraph
    assert "--absent-to-ref</code> was not used" in paragraph
    assert "--include-non-variant-sites" in paragraph


def test_the_paragraph_reports_coverage_against_pharmcats_own_list():
    """A gVCF that omits a region has no reference block there; absent is not
    reference, and the reader is owed the number."""
    paragraph = gvcf_provenance_paragraph(FULL_STATS)

    assert "1,180 of PharmCAT's 1,226 positions" in paragraph
    assert "46 were not covered" in paragraph
    assert "remain no-calls" in paragraph


def test_zero_coverage_is_reported_not_swallowed():
    """0 called is exactly what a reader needs told, and a falsy check would delete it."""
    paragraph = gvcf_provenance_paragraph(
        {"n_pharmcat_positions": 1226, "n_pgx_positions_called": 0}
    )

    assert "0 of PharmCAT's 1,226 positions" in paragraph
    assert "1,226 were not covered" in paragraph


def test_the_paragraph_states_the_re_genotyping_caveat():
    """GenotypeGVCFs re-derives each genotype from the PLs rather than copying the
    original caller's. ZaroPGx sets the calling-confidence threshold to zero, which
    removes a filter but not the re-derivation, and the copy must not overclaim."""
    paragraph = gvcf_provenance_paragraph(FULL_STATS)

    assert "re-derives each genotype" in paragraph
    assert "not guaranteed identical" in paragraph
    assert "threshold was set to zero" in paragraph


def test_the_paragraph_states_the_indel_representation_caveat():
    """Those positions become no-calls, and the copy must say it is the same outcome a
    plain VCF gets rather than a cost of the conversion."""
    paragraph = gvcf_provenance_paragraph(FULL_STATS)

    assert "indel representation" in paragraph
    assert "same as they would from a plain VCF" in paragraph


@pytest.mark.parametrize(
    "broken",
    [
        {"target_build": "GRCh38"},  # no counts at all
        {"n_pgx_positions_called": 7},  # only one count
        {"n_pharmcat_positions": "1226", "n_pgx_positions_called": "7"},  # strings
        {"n_pharmcat_positions": True, "n_pgx_positions_called": False},  # bools
        {"n_pharmcat_positions": 0, "n_pgx_positions_called": 0},  # no denominator
        {"n_pharmcat_positions": 1226, "n_pgx_positions_called": -1},  # nonsense
    ],
)
def test_unusable_counts_drop_the_numbers_but_keep_the_claim(broken):
    """Never render "None of PharmCAT's None positions", and never invent a zero.

    That the reference calls are called data is the half the reader cannot afford to
    miss, and it does not depend on the counts.
    """
    paragraph = gvcf_provenance_paragraph(broken)

    assert paragraph is not None
    assert "called data, not assumed" in paragraph
    assert "None" not in paragraph
    assert not re.search(r"\d+ of PharmCAT's", paragraph)


def test_a_missing_absent_count_is_derived_rather_than_dropped():
    """The endpoint sends all three, but the two that matter can rebuild the third."""
    paragraph = gvcf_provenance_paragraph(
        {"n_pharmcat_positions": 100, "n_pgx_positions_called": 90}
    )

    assert "10 were not covered" in paragraph


# --------------------------------------------------------------------------
# The templates
# --------------------------------------------------------------------------


def _render(template_name, **overrides):
    from app.reports.generator import env

    context = {
        "diplotypes": [],
        "recommendations": [],
        "gene_drug_recommendations": [],
        "organized_recommendations": [],
        "patient_id": "test-patient",
        "report_id": "test-report",
        "report_date": "2026-08-31",
        "organization": "ZaroPGx",
        "disclaimer": "",
        "genome_build": "GRCh38.p14",
        "named_allele_matcher_version": "2.0.0",
        "pharmcat_data_version": "2025-11-05-00-25",
    }
    context.update(overrides)
    return env.get_template(template_name).render(**context)


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


@pytest.mark.parametrize("template_name", TEMPLATES)
def test_the_paragraph_reaches_both_report_lanes(template_name):
    text = _text(
        _render(template_name, gvcf_provenance=gvcf_provenance_paragraph(FULL_STATS))
    )

    assert "gVCF genotyping:" in text
    assert "called data, not assumed" in text
    assert "1,180 of PharmCAT's 1,226 positions" in text


@pytest.mark.parametrize("template_name", TEMPLATES)
def test_an_ordinary_vcf_run_says_nothing_about_gvcf_genotyping(template_name):
    """Negative control: no invented paragraph when no genotyping ran."""
    text = _text(_render(template_name, gvcf_provenance=None))

    assert "gVCF genotyping" not in text
    assert "GenotypeGVCFs" not in text


@pytest.mark.parametrize("template_name", TEMPLATES)
def test_the_two_reference_call_paragraphs_sit_together(template_name):
    """The adjacency is the point.

    ``--absent-to-ref`` fabricates reference calls and the gVCF lane reads them out of
    the data. A reader who meets one paragraph without the other cannot tell which kind
    of reference call this report rests on, so both must render in the same block and
    with nothing between them that changes the subject.
    """
    html = _render(
        template_name,
        gvcf_provenance=gvcf_provenance_paragraph(FULL_STATS),
        pharmcat_assume_ref_methodology=methodology_assume_ref_paragraph(True, False),
    )
    text = _text(html)

    assume_ref_at = text.index("Assume reference when missing:")
    gvcf_at = text.index("gVCF genotyping:")
    between = text[assume_ref_at:gvcf_at]

    assert gvcf_at > assume_ref_at
    # Nothing but the assume-ref paragraph's own body may separate them.
    assert "<h" not in between.lower()
    assert len(between) < 700, between


@pytest.mark.parametrize("template_name", TEMPLATES)
def test_the_paragraph_renders_even_with_no_pharmcat_metadata(template_name):
    """v2-shaped reports carry no matcherMetadata; the genotyping must still be stated.

    Same reason the liftover notice was separated from the PharmCAT tooling sentences:
    they resolve independently, and sharing a conditional lets one take the other down.
    """
    text = _text(
        _render(
            template_name,
            genome_build=None,
            named_allele_matcher_version=None,
            pharmcat_data_version=None,
            gvcf_provenance=gvcf_provenance_paragraph(FULL_STATS),
        )
    )

    assert "called data, not assumed" in text


def test_the_pdf_lane_forwards_both_provenance_keys():
    """pdf_generators rebuilds its own context dict, so a key missing there renders as
    nothing at all rather than as an error -- silently dropping the paragraph from the
    one artefact most likely to be filed.

    That is not hypothetical: ``liftover_provenance`` was absent from this dict from the
    day it was introduced, so ``report_template.html``'s "Reference build:" line -- the
    only place a reader learns the analysed coordinates are not the uploaded file's --
    rendered in the HTML report and silently vanished from the PDF. It is asserted here
    alongside the new key because both fail the same silent way.

    A source-text assertion, deliberately, and against this module's own stated
    preference for rendering: the defect is a missing dict KEY, and the only way to
    observe it through a render is to run WeasyPrint over a full report, which needs
    system libraries CI does not carry. What is pinned is exactly what was wrong.
    """
    from pathlib import Path

    import app.reports.pdf_generators as pdf_generators

    source = Path(pdf_generators.__file__).read_text(encoding="utf-8")
    for key in ("gvcf_provenance", "liftover_provenance"):
        assert f'"{key}": template_data.get("{key}")' in source, key
