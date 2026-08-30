"""A lifted run must say so in the report, with the cost of the lift.

Before this, a GRCh37 upload and a native GRCh38 upload produced reports that were
byte-indistinguishable in their provenance: the run-provenance paragraph names
PharmCAT's *definition* build (always GRCh38.x) and explicitly disclaims the
uploaded file's, so nothing on the page recorded that these coordinates were
converted, or that variants were dropped on the way. gatk-api computed the counts,
logged them, and they stopped at the database.

The counts come from the liftover ``JobStep``'s ``output_data`` rather than the
upload-time ``needs_liftover`` flag: the flag is an intention recorded before the
run, the step row is what actually happened, and only the row carries numbers.

These tests render the real templates through the app's own Jinja environment --
source-text assertions have repeatedly proved worthless in this repo -- and pin
both halves: the sentence builder, and that the sentence reaches both report lanes.

They assert the sentence reaches the *page*, not that it sits in a particular
paragraph. It originally shared the Executive Summary's run-provenance paragraph
with three PharmCAT tooling facts; in the print report it now opens the header
block, where a reader actually looks to find out what was analysed. The two lanes
diverge here on purpose -- the print report is a paginated document with a header
block, the interactive one is a tabbed page without -- so pinning a location
would pin the wrong thing.
"""

from __future__ import annotations

import re

import pytest

from app.utils.liftover_provenance import liftover_provenance_sentence

TEMPLATES = ["report_template.html", "interactive_report.html"]

FULL_STATS = {
    "n_lifted": 7,
    "n_rejected": 0,
    "reject_reasons": {},
    "source_build": "GRCh37",
    "target_build": "GRCh38",
}


# --------------------------------------------------------------------------
# The sentence builder
# --------------------------------------------------------------------------


@pytest.mark.parametrize("empty", [None, {}])
def test_no_sentence_when_no_lift_ran(empty):
    """The ordinary GRCh38 upload: no liftover step row, so nothing to say."""
    assert liftover_provenance_sentence(empty) is None


def test_sentence_names_both_builds_and_both_counts():
    sentence = liftover_provenance_sentence(FULL_STATS)
    assert sentence is not None
    assert "GRCh37" in sentence
    assert "GRCh38" in sentence
    assert "7 variants lifted" in sentence
    assert "0 dropped" in sentence


def test_zero_rejected_is_reported_not_swallowed():
    """0 is the reassuring answer, and a falsy-check would have deleted it."""
    sentence = liftover_provenance_sentence(FULL_STATS)
    assert "0 dropped as unliftable" in sentence


def test_large_counts_are_thousands_separated():
    sentence = liftover_provenance_sentence(
        {**FULL_STATS, "n_lifted": 1234567, "n_rejected": 8901}
    )
    assert "1,234,567 variants lifted" in sentence
    assert "8,901 dropped" in sentence


@pytest.mark.parametrize(
    "broken",
    [
        {"source_build": "GRCh37"},  # no counts at all
        {"source_build": "GRCh37", "n_lifted": 7},  # only one count
        {"source_build": "GRCh37", "n_lifted": "7", "n_rejected": "0"},  # strings
        {"source_build": "GRCh37", "n_lifted": True, "n_rejected": False},  # bools
        {"source_build": "GRCh37", "n_lifted": -1, "n_rejected": 0},  # nonsense
    ],
)
def test_unusable_counts_drop_the_numbers_but_keep_the_build_change(broken):
    """Never render "None dropped", and never invent a zero.

    The build change is the half the reader cannot afford to miss, so a step row
    with unusable counts still produces a sentence -- just without numbers.
    """
    sentence = liftover_provenance_sentence(broken)
    assert sentence is not None
    assert "lifted over to GRCh38" in sentence
    assert "None" not in sentence
    assert not re.search(r"\d+ variants lifted", sentence)


def test_builds_fall_back_rather_than_rendering_blank():
    sentence = liftover_provenance_sentence({"n_lifted": 3, "n_rejected": 1})
    assert "GRCh37" in sentence and "GRCh38" in sentence


def test_source_build_from_the_row_is_used_verbatim():
    """b37 and hg19 are distinct labels; the report must not relabel them.

    Asserted as "the row's own label, and not a substituted one" rather than on
    the surrounding phrasing, which is editorial and has changed once already.
    """
    sentence = liftover_provenance_sentence({**FULL_STATS, "source_build": "hg19"})
    assert "hg19" in sentence
    assert "GRCh37" not in sentence


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
        "report_date": "2026-08-29",
        "organization": "ZaroPGx",
        "disclaimer": "",
        "genome_build": "GRCh38.p14",
        "named_allele_matcher_version": "2.0.0",
        "pharmcat_data_version": "2025-11-05-00-25",
    }
    context.update(overrides)
    return env.get_template(template_name).render(**context)


def _provenance_text(html):
    match = re.search(r'<p class="run-provenance">(.*?)</p>', html, re.S | re.I)
    if match is None:
        return None
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", match.group(1))).strip()


@pytest.mark.parametrize("template_name", TEMPLATES)
def test_the_sentence_reaches_both_report_lanes(template_name):
    html = _render(
        template_name, liftover_provenance=liftover_provenance_sentence(FULL_STATS)
    )
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))

    assert "uploaded as GRCh37" in text and "lifted over to GRCh38" in text
    assert "7 variants lifted, 0 dropped as unliftable" in text


@pytest.mark.parametrize("template_name", TEMPLATES)
def test_a_native_grch38_run_says_nothing_about_liftover(template_name):
    """Negative control: no invented sentence when no lift ran."""
    text = _provenance_text(_render(template_name, liftover_provenance=None))
    assert text is not None, "the PharmCAT sentences should still render"
    assert "lifted" not in text.lower()


@pytest.mark.parametrize("template_name", TEMPLATES)
def test_the_paragraph_renders_for_a_lift_even_with_no_pharmcat_metadata(
    template_name,
):
    """v2-shaped reports carry no matcherMetadata; the lift must still be stated.

    This is why the two facts had to be separated: they resolve independently, and
    while they shared a paragraph the absence of PharmCAT metadata could take the
    liftover notice down with it.
    """
    html = _render(
        template_name,
        genome_build=None,
        named_allele_matcher_version=None,
        pharmcat_data_version=None,
        liftover_provenance=liftover_provenance_sentence(FULL_STATS),
    )
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))

    assert "lifted over to GRCh38" in text
    assert "allele definitions" not in text


@pytest.mark.parametrize("template_name", TEMPLATES)
def test_the_lift_notice_and_pharmcats_build_do_not_contradict(template_name):
    """Both sentences are about a build; each must name whose.

    They now live in different sections -- the file's build in the header, the
    tooling's under Methodology -- so this asserts they coexist on the page
    without contradiction, not that they share a paragraph.
    """
    html = _render(
        template_name, liftover_provenance=liftover_provenance_sentence(FULL_STATS)
    )
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))

    assert "uploaded as GRCh37" in text
    assert "not the reference build of the uploaded file" in text
