"""The PDF's first page ends with the whole disclaimer, and nothing spills.

Page 1 is title, sample facts, Methodology and the disclaimer, and print CSS ends
the page after the disclaimer so Alerts starts the next. On a real report the
disclaimer was two lines too long: those two lines sat alone on page 2, with the
box's border broken across the pages, and Alerts moved to page 3. The disclaimer
is fine print in the PDF now, with less space above it, so a typical first page
holds it (with a liftover line too); when one still cannot, the box moves whole.

A gVCF run's Methodology carries one more paragraph, the gVCF genotyping one, six
lines at body size. With it the box no longer fit, moved whole to page 2, and the
Executive Summary (a recto page) went from page 3 to page 5 behind a blank page 4.
The run's own Methodology paragraphs are fine print in the PDF too now, with the
spacing above them tighter; the gVCF tests below render the real paragraph, so a
longer one fails here rather than in a delivered report.
"""

from __future__ import annotations

import re

import pytest

import app.reports.generator as generator_module
from app.utils.gvcf_provenance import gvcf_provenance_paragraph
from app.utils.pharmcat_assume_ref import explicit_calls_paragraph

weasyprint = pytest.importorskip("weasyprint")

# Methodology's provenance paragraph, as PharmCAT 3.4 runs print it.
PROVENANCE = (
    '<p class="run-provenance">Star alleles were matched against PharmCAT\'s allele '
    "definitions, specified on the GRCh38.p14 assembly. That is PharmCAT's build, "
    "not the reference build of the uploaded file; concerns about this file's build "
    "are raised under Alerts and Warnings. Diplotypes were called by PharmCAT's "
    "Named Allele Matcher v2.0.0. Recommendations reflect PharmCAT guideline data "
    "version 2026-07-13-11-40.</p>"
)
LIFTOVER = (
    "<p><strong>Reference build:</strong> GRCh37 (hg19), lifted over to GRCh38 with "
    "CrossMap 0.7 and the UCSC chain.</p>"
)
END_OF_METHODOLOGY = '    </div>\n\n    <div class="disclaimer">'
# A gVCF run's paragraph as the report builds it, in its longest form without the
# assume-reference flags (they are off for a gVCF): counts of a real public run,
# plus the sentence about covered but uncertain positions.
GVCF = gvcf_provenance_paragraph(
    {
        "n_pgx_positions_called": 192,
        "n_pharmcat_positions": 1226,
        "n_positions_absent": 1034,
        "n_positions_uncertain": 12,
    },
    absent_to_ref=False,
    unspecified_to_ref=False,
)
# ...and the paragraph a gVCF run adds when its uploader ticked the flags.
GVCF_FLAGS_SET_ASIDE = explicit_calls_paragraph("gvcf", True, True)


def _report_html(monkeypatch, tmp_path):
    for key in list(generator_module.REPORT_CONFIG):
        if key.startswith(("write_", "show_")):
            monkeypatch.setitem(
                generator_module.REPORT_CONFIG, key, key == "write_html"
            )
    result = generator_module.generate_report(
        {"data": {"genes": [], "drugRecommendations": []}},
        str(tmp_path),
        {"id": "p1"},
    )
    html = (tmp_path / result["html_path"].split("/")[-1]).read_text(encoding="utf-8")
    assert html.count(END_OF_METHODOLOGY) == 1
    return html


def _pages(html, tmp_path):
    document = weasyprint.HTML(string=html, base_url=str(tmp_path)).render()
    pages = []
    for page in document.pages:
        text = []

        def walk(box):
            if getattr(box, "text", None):
                text.append(box.text)
            for child in getattr(box, "children", None) or []:
                walk(child)

        walk(page._page_box)
        pages.append(re.sub(r"\s+", " ", " ".join(text)))
    return pages


def _disclaimer_pages(pages):
    first = generator_module.get_disclaimer().split()
    start, end = " ".join(first[:6]), " ".join(first[-6:])
    return (
        [i for i, page in enumerate(pages) if start in page],
        [i for i, page in enumerate(pages) if end in page],
    )


def test_a_typical_first_page_holds_the_whole_disclaimer(monkeypatch, tmp_path):
    html = _report_html(monkeypatch, tmp_path)
    html = html.replace(END_OF_METHODOLOGY, PROVENANCE + END_OF_METHODOLOGY)
    html = html.replace(
        "<p><strong>Report Date:</strong>",
        LIFTOVER + "<p><strong>Report Date:</strong>",
        1,
    )
    pages = _pages(html, tmp_path)
    assert _disclaimer_pages(pages) == ([0], [0])
    assert pages[1].lstrip().startswith("Alerts and Warnings")


@pytest.mark.parametrize(
    "methodology, liftover",
    [
        pytest.param(GVCF, False, id="gvcf"),
        pytest.param(GVCF + GVCF_FLAGS_SET_ASIDE, False, id="gvcf-flags-set-aside"),
        pytest.param(GVCF, True, id="gvcf-liftover"),
    ],
)
def test_a_gvcf_first_page_holds_the_whole_disclaimer(
    monkeypatch, tmp_path, methodology, liftover
):
    assert GVCF and GVCF_FLAGS_SET_ASIDE
    html = _report_html(monkeypatch, tmp_path)
    html = html.replace(
        END_OF_METHODOLOGY, PROVENANCE + methodology + END_OF_METHODOLOGY
    )
    if liftover:
        html = html.replace(
            "<p><strong>Report Date:</strong>",
            LIFTOVER + "<p><strong>Report Date:</strong>",
            1,
        )
    pages = _pages(html, tmp_path)
    assert _disclaimer_pages(pages) == ([0], [0])
    assert pages[1].lstrip().startswith("Alerts and Warnings")
    assert pages[2].lstrip().startswith("Executive Summary")


def test_a_disclaimer_that_cannot_fit_moves_whole(monkeypatch, tmp_path):
    """A long research-flag paragraph leaves a few lines free, too few for the
    disclaimer: it starts page 2 rather than splitting across the two. (Thirty-two
    sentences sits mid-way between too short to push it off and too long to
    leave any room.)"""
    html = _report_html(monkeypatch, tmp_path)
    long_paragraph = (
        "<p>" + "A long methodology paragraph about research flags. " * 32 + "</p>"
    )
    html = html.replace(
        END_OF_METHODOLOGY, PROVENANCE + long_paragraph + END_OF_METHODOLOGY
    )
    pages = _pages(html, tmp_path)
    assert _disclaimer_pages(pages) == ([1], [1])
    assert pages[2].lstrip().startswith("Alerts and Warnings")
