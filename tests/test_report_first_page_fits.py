"""The PDF's first page ends with the whole disclaimer, and nothing spills.

Page 1 is title, sample facts, Methodology and the disclaimer, and print CSS ends
the page after the disclaimer so Alerts starts the next. On a real report the
disclaimer was two lines too long: those two lines sat alone on page 2, with the
box's border broken across the pages, and Alerts moved to page 3. The disclaimer
is fine print in the PDF now, with less space above it, so a typical first page
holds it (with a liftover line too); when one still cannot, the box moves whole.
"""

from __future__ import annotations

import re

import pytest

import app.reports.generator as generator_module

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


def test_a_disclaimer_that_cannot_fit_moves_whole(monkeypatch, tmp_path):
    """A long research-flag paragraph leaves a few lines free, too few for the
    disclaimer: it starts page 2 rather than splitting across the two. (Twelve
    sentences sits mid-way between too short to push it off and too long to
    leave any room.)"""
    html = _report_html(monkeypatch, tmp_path)
    long_paragraph = (
        "<p>" + "A long methodology paragraph about research flags. " * 12 + "</p>"
    )
    html = html.replace(
        END_OF_METHODOLOGY, PROVENANCE + long_paragraph + END_OF_METHODOLOGY
    )
    pages = _pages(html, tmp_path)
    assert _disclaimer_pages(pages) == ([1], [1])
    assert pages[2].lstrip().startswith("Alerts and Warnings")
