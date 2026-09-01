"""A genotyped-gVCF run must say so in the report, next to the assume-ref paragraph.

A gVCF upload is the one lane whose homozygous-reference calls at pharmacogene
positions are real. Every other lane either has a variant at a position or has nothing,
and "nothing" only becomes a reference call if the operator turns on one of PharmCAT's
assume-reference flags, which fabricates it. Two reports that differ in exactly that way
would otherwise be indistinguishable on the page.

The counts come from the ``gvcf_to_vcf`` ``JobStep``'s ``output_data``, written by
gatk-api's ``/gvcf-to-vcf`` when the step completes -- not from the upload-time
``needs_gvcf_genotyping`` flag. The flag is an intention recorded before the run, the
step row is what actually happened, and only the row carries numbers.

WHAT THE FLAGS DO TO THIS PARAGRAPH is the half these tests exist for now. The
paragraph used to assert, unconditionally, that ``--absent-to-ref`` "was not used on
this run" and that the uncovered positions "remain no-calls". Both are the run's
configuration, not the lane's: the two assume-reference checkboxes are global, resolved
once per upload from form-or-env, and reach PharmCAT with no input-type branch anywhere
on the way. And the one that bites here is ``--unspecified-to-ref``, not
``--absent-to-ref``: the reference pass runs ``--include-non-variant-sites``, which
emits a row at every position in PharmCAT's list, so an uncovered position arrives as a
present ``./.`` row -- exactly what ``--unspecified-to-ref`` rewrites to ``0/0``. So a
GRCh38 gVCF uploaded with that box ticked measured 46 uncovered positions, reported them
as reference, and printed a paragraph saying they stayed no-calls.

Both halves are pinned: the paragraph builder, and that the paragraph reaches both
report lanes through the real Jinja environment -- source-text assertions have
repeatedly proved worthless in this repo. Its adjacency to the assume-ref paragraph is
pinned too, because that adjacency is the point: a reader who sees one without the other
cannot tell which they are looking at. And since the two paragraphs are now built from
the same pair of booleans, that they cannot CONTRADICT each other is pinned directly.
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

# (absent_to_ref, unspecified_to_ref) -- every combination the upload can produce.
FLAG_COMBINATIONS = [(False, False), (True, False), (False, True), (True, True)]

# The claim that is true only when neither flag ran. Kept as a constant because three
# tests below key off exactly it, and a reworded paragraph that quietly kept saying it
# in the wrong case is the defect.
CALLED_DATA_CLAIM = "called data, not assumed"
NO_FLAGS_CLAIM = "assume-reference flags was used on this run"
NO_CALL_CLAIM = "were not covered by the file and remain no-calls"


def _paragraph(stats=FULL_STATS, absent=False, unspecified=False):
    return gvcf_provenance_paragraph(
        stats, absent_to_ref=absent, unspecified_to_ref=unspecified
    )


# --------------------------------------------------------------------------
# The paragraph builder
# --------------------------------------------------------------------------


@pytest.mark.parametrize("empty", [None, {}])
def test_no_paragraph_when_no_genotyping_ran(empty):
    """The ordinary VCF/BAM upload: no gvcf_to_vcf step row, so nothing to say."""
    assert _paragraph(empty) is None


def test_the_paragraph_says_the_reference_calls_are_called_data():
    """The claim the whole lane exists to be able to make -- when no flag ran."""
    paragraph = _paragraph()

    assert paragraph is not None
    assert CALLED_DATA_CLAIM in paragraph
    assert NO_FLAGS_CLAIM in paragraph
    assert "--include-non-variant-sites" in paragraph


def test_the_clean_run_still_names_the_flag_that_would_have_mattered():
    """--absent-to-ref is the wrong flag to reassure the reader about.

    The PGx pass emits a row at every position in the interval list, so an uncovered
    position is PRESENT with ./. rather than missing -- which is --unspecified-to-ref's
    business, not --absent-to-ref's. A paragraph that only ever names the latter tells a
    reader the box they ticked is harmless here.
    """
    paragraph = _paragraph()

    assert "<code>--unspecified-to-ref</code>" in paragraph
    assert "./." in paragraph


def test_the_paragraph_reports_coverage_against_pharmcats_own_list():
    """A gVCF that omits a region has no reference block there; absent is not
    reference, and the reader is owed the number."""
    paragraph = _paragraph()

    assert "1,180 of PharmCAT's 1,226 positions" in paragraph
    assert "46 were not covered" in paragraph
    assert NO_CALL_CLAIM in paragraph


def test_zero_coverage_is_reported_not_swallowed():
    """0 called is exactly what a reader needs told, and a falsy check would delete it."""
    paragraph = _paragraph({"n_pharmcat_positions": 1226, "n_pgx_positions_called": 0})

    assert "0 of PharmCAT's 1,226 positions" in paragraph
    assert "1,226 were not covered" in paragraph


def test_the_paragraph_states_the_re_genotyping_caveat():
    """GenotypeGVCFs re-derives each genotype from the PLs rather than copying the
    original caller's. ZaroPGx sets the calling-confidence threshold to zero, which
    removes a filter but not the re-derivation, and the copy must not overclaim."""
    paragraph = _paragraph()

    assert "re-derives each genotype" in paragraph
    assert "not guaranteed identical" in paragraph
    assert "threshold was set to zero" in paragraph


def test_the_paragraph_states_the_indel_representation_caveat():
    """Those positions become no-calls, and the copy must say it is the same outcome a
    plain VCF gets rather than a cost of the conversion."""
    paragraph = _paragraph()

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
    paragraph = _paragraph(broken)

    assert paragraph is not None
    assert CALLED_DATA_CLAIM in paragraph
    assert "None" not in paragraph
    assert not re.search(r"\d+ of PharmCAT's", paragraph)


def test_a_missing_absent_count_is_derived_rather_than_dropped():
    """The endpoint sends all three, but the two that matter can rebuild the third."""
    paragraph = _paragraph({"n_pharmcat_positions": 100, "n_pgx_positions_called": 90})

    assert "10 were not covered" in paragraph


# --------------------------------------------------------------------------
# The run's assume-reference flags, which this paragraph used to assert were off
# --------------------------------------------------------------------------


def test_unspecified_to_ref_withdraws_the_called_data_claim():
    """The wrong clinical answer this signature exists to stop.

    GRCh38 gVCF + "assume unspecified = reference": the run measures 46 uncovered
    positions, PharmCAT reports all 46 as homozygous reference, and the paragraph used
    to say they remained no-calls and that no flag was used.
    """
    paragraph = _paragraph(unspecified=True)

    assert CALLED_DATA_CLAIM not in paragraph
    assert NO_FLAGS_CLAIM not in paragraph
    assert NO_CALL_CLAIM not in paragraph
    assert "did NOT stay no-calls" in paragraph
    assert "<code>--unspecified-to-ref</code>" in paragraph
    # The counts still have to be there -- they are what the sentence is about.
    assert "1,180 of PharmCAT's 1,226 positions" in paragraph
    assert "46 were not covered" in paragraph


def test_unspecified_to_ref_says_which_positions_became_assumed():
    """ "Some of this report is fabricated" is only useful if it says which part."""
    paragraph = _paragraph(unspecified=True)

    assert "assumed, not called" in paragraph
    assert "only the covered ones" in paragraph


def test_absent_to_ref_alone_is_named_and_does_not_move_the_no_calls():
    """--absent-to-ref acts on positions MISSING from the VCF, and on this lane there
    are essentially none -- but the run still used an assume-reference flag, so the
    blanket "called data, not assumed" cannot stand either."""
    paragraph = _paragraph(absent=True)

    assert CALLED_DATA_CLAIM not in paragraph
    assert NO_FLAGS_CLAIM not in paragraph
    assert "<code>--absent-to-ref</code>" in paragraph
    # The uncovered positions genuinely did stay no-calls in this configuration.
    assert "stayed no-calls" in paragraph


def test_both_flags_are_named_as_the_single_flag_pharmcat_is_given():
    """pharmcat_cli_ref_flags collapses the pair into --missing-to-ref, so a reader
    looking for that flag in the log must find it named here."""
    paragraph = _paragraph(absent=True, unspecified=True)

    assert "<code>--missing-to-ref</code>" in paragraph
    assert "did NOT stay no-calls" in paragraph
    assert CALLED_DATA_CLAIM not in paragraph


@pytest.mark.parametrize("absent,unspecified", FLAG_COMBINATIONS)
def test_the_flags_are_required_arguments(absent, unspecified):
    """No default, deliberately.

    A default would let a caller that does not know the run's configuration print the
    old unconditional "not used on this run" -- which is precisely how the bug shipped:
    the builder was simply never told.
    """
    import inspect

    parameters = inspect.signature(gvcf_provenance_paragraph).parameters
    for name in ("absent_to_ref", "unspecified_to_ref"):
        assert parameters[name].default is inspect.Parameter.empty, name
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY, name

    assert _paragraph(absent=absent, unspecified=unspecified) is not None


def test_the_generator_hands_over_the_job_metadata_flags():
    """The call site, pinned on source, for the reason the PDF test below gives.

    Required keyword-only arguments mean generator.py cannot silently omit them -- but
    it CAN pass the wrong thing, and the whole block is wrapped in a try/except that
    turns a TypeError into a one-line "provenance could not be read" warning. So what is
    asserted is that the same two job_metadata keys feeding the assume-ref paragraph
    also feed this one.
    """
    from pathlib import Path

    import app.reports.generator as generator

    source = Path(generator.__file__).read_text(encoding="utf-8")
    assert 'absent_to_ref = bool(meta.get("pharmcat_absent_to_ref"))' in source
    assert (
        'unspecified_to_ref = bool(meta.get("pharmcat_unspecified_to_ref"))' in source
    )
    assert "absent_to_ref=absent_to_ref," in source
    assert "unspecified_to_ref=unspecified_to_ref," in source


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
    text = _text(_render(template_name, gvcf_provenance=_paragraph()))

    assert "gVCF genotyping:" in text
    assert CALLED_DATA_CLAIM in text
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

    An assume-reference flag fabricates reference calls and the gVCF lane reads them out
    of the data. A reader who meets one paragraph without the other cannot tell which
    kind of reference call this report rests on, so both must render in the same block
    and with nothing between them that changes the subject.
    """
    html = _render(
        template_name,
        gvcf_provenance=_paragraph(absent=True),
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


@pytest.mark.parametrize("absent,unspecified", FLAG_COMBINATIONS)
@pytest.mark.parametrize("template_name", TEMPLATES)
def test_the_two_paragraphs_cannot_contradict_each_other(
    template_name, absent, unspecified
):
    """Adjacency is worthless if the neighbours disagree, and they did.

    Rendered together, the assume-reference paragraph said "this research run used
    --absent-to-ref ... fabricating reference calls can over-call normal phenotypes"
    two lines above a gVCF paragraph asserting that flag was not used. Both are built
    from the same pair of booleans now, so the page can be checked as one statement.
    """
    text = _text(
        _render(
            template_name,
            gvcf_provenance=_paragraph(absent=absent, unspecified=unspecified),
            pharmcat_assume_ref_methodology=methodology_assume_ref_paragraph(
                absent, unspecified
            ),
        )
    )

    any_flag = absent or unspecified
    assert ("Assume reference when missing:" in text) is any_flag
    # The gVCF half must not deny what the assume-ref half just admitted.
    assert (CALLED_DATA_CLAIM in text) is not any_flag
    assert (NO_FLAGS_CLAIM in text) is not any_flag
    # And the fate of the uncovered positions is stated once, correctly:
    # --unspecified-to-ref is what turns a present ./. row into 0/0.
    assert (NO_CALL_CLAIM in text) is not unspecified
    assert ("did NOT stay no-calls" in text) is unspecified


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
            gvcf_provenance=_paragraph(),
        )
    )

    assert CALLED_DATA_CLAIM in text


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
