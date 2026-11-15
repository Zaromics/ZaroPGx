"""A footnote ends with one period, whatever the note brought.

The template added "." to every no-call note. PyPGx's own reasons are fragments and
need it. The AMP/CAP Tier 1 note (app/pharmcat/amp_tier1.py) is a pair of sentences
and already ends in one, so every report with a withheld gene printed "positions the
data does not cover..".

Found by rendering the PDF in the shipped image and reading the page, which is the
only way this kind of thing shows up: the text was right in every string the tests
compared, and wrong on the paper.
"""

from __future__ import annotations

import re

import pytest

from app.pharmcat.amp_tier1 import *  # noqa: F401,F403  (module import check only)
from app.reports.generator import env


def _render(note):
    rows = [
        {
            "gene": "CYP2C9",
            "diplotype": "",
            "phenotype": "",
            "called_by": "M",
            "no_call_note": note,
        }
    ]
    markup = env.get_template("report_template.html").render(
        diplotypes=rows,
        recommendations=[],
        gene_drug_recommendations=[],
        organized_recommendations=[],
        patient_id="p",
        report_id="r",
        report_date="2026-11-14",
        organization="ZaroPGx",
        disclaimer="",
        genome_build="GRCh38.p14",
        named_allele_matcher_version="2.0.0",
        pharmcat_data_version="x",
        execsum_from_tsv=[],
        called_genes=rows,
        missing_positions_not_assumed=False,
    )
    # The footnote paragraphs only. The page's <style> block carries ellipses in its
    # comments ("/* ...except a one-letter column"), so a whole-page search for ".."
    # finds them and proves nothing about the footnote.
    notes = re.findall(r'<p class="no-call-note">(.*?)</p>', markup, re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", " ".join(notes))).strip()


@pytest.mark.parametrize(
    "note",
    [
        "A reference call here would rest on positions the data does not cover.",
        "no reads at its positions",
        "the gene is absent from this assembly!",
        "was anything sequenced here?",
    ],
)
def test_a_footnote_never_doubles_its_full_stop(note):
    body = _render(note)

    assert note.rstrip(".!?") in body
    assert ".." not in body
    assert "?." not in body and "!." not in body


def test_a_fragment_still_gets_its_period():
    body = _render("no reads at its positions")

    assert "no reads at its positions." in body
