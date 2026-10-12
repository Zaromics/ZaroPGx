"""The PDF ends with Platform and Citations, closed by the licence lines.

The workflow diagram fills a PDF page of its own. Placed after the citations, it
left their tail on a mostly empty page and the closing licence lines alone on the
last one (a real VCF report: pages 47 and 49 of 49, 31% and 22% used). It now
comes before Platform and Citations, and the licence closes the citations' page.
"""

from __future__ import annotations

import re

import pytest

import app.reports.generator as generator_module

weasyprint = pytest.importorskip("weasyprint")

LAST_CITATION = "Calling star alleles with Stargazer in 28 pharmacogenes"


def _pages(monkeypatch, tmp_path):
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


def _page_of(pages, text):
    found = [i for i, page in enumerate(pages) if text in page]
    assert found, text
    return found[0]


def test_the_licence_closes_the_citations_page(monkeypatch, tmp_path):
    pages = _pages(monkeypatch, tmp_path)
    diagram = _page_of(pages, "Workflow Diagram")
    citations = _page_of(pages, "Platform and Citations")
    assert diagram < citations
    # The licence lines follow the last citation on its page, not alone after
    # a page-filling diagram.
    licence = _page_of(pages, "ZaroPGx is distributed under the")
    assert licence == _page_of(pages, LAST_CITATION)
