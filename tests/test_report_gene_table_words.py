"""The Genetic Results table breaks lines between words, and its notes stay whole.

At 17% of the page, Diplotype and Phenotype were narrower than the widest word they
hold, so the PDF broke it wherever it ran out of room: MT-RNR1's phenotype read
"aminoglycosi" / "de-induced", and UGT2B17's copy-number note left the ")" of
"(WholeDel1Hom)" alone on a line. The N/A footnote under the table split at a page
foot, its last two lines atop the next page.
"""

from __future__ import annotations

import pytest

import app.reports.generator as generator_module
from app.reports.pdf_fit import _outer_box, _section_element, section_pages

weasyprint = pytest.importorskip("weasyprint")

GENES = [
    {
        "gene": "MT-RNR1",
        "diplotype": "Reference",
        "phenotype": "normal risk of aminoglycoside-induced hearing loss",
        "call_source": "OUTSIDE",
        "tool_source": "mtDNA-Server-2",
    },
    {
        "gene": "UGT2B17",
        "diplotype": "*2/*2",
        "phenotype": "N/A",
        "tool_source": "PyPGx",
        "pyPgxOnly": True,
        "pypgx": {
            "copy_number": "WholeDel1Hom",
            "copy_number_summary": "whole-gene deletion on both chromosomes",
        },
    },
    {
        "gene": "CACNA1S",
        "diplotype": "Reference/Reference",
        "phenotype": "Uncertain Susceptibility",
        "call_source": "MATCHER",
    },
]


def _page(genes=GENES):
    return generator_module.env.get_template("report_template.html").render(
        diplotypes=genes,
        recommendations=[],
        report_id="test-report",
        report_date="2026-10-20",
        disclaimer="",
    )


def _layout(monkeypatch, page):
    monkeypatch.setattr(
        generator_module, "fit_sections", lambda render: (render(""), {})
    )
    document, _ = generator_module.render_pdf_document(page)
    return document


def _gene_table_lines(document):
    """The text of every line in the Genetic Results table's body cells."""
    lines = []

    def text(box):
        if getattr(box, "text", None):
            return box.text
        return "".join(text(c) for c in getattr(box, "children", None) or [])

    def walk(box, inside=False):
        element = getattr(box, "element", None)
        if element is not None and "gene-table" in (element.get("class") or ""):
            inside = True
        if inside and box.__class__.__name__ == "LineBox":
            lines.append(text(box).strip())
            return
        for child in getattr(box, "children", None) or []:
            walk(child, inside)

    for page in document.pages:
        walk(page._page_box)
    return lines


def test_the_widest_words_are_not_broken(monkeypatch):
    lines = _gene_table_lines(_layout(monkeypatch, _page()))
    assert any("aminoglycoside-" in line for line in lines), lines
    assert any("(WholeDel1Hom)" in line for line in lines), lines
    assert any("CACNA1S" == line for line in lines), lines
    assert any("Uncertain" in line for line in lines), lines
    assert ")" not in lines, lines
    # The headers too: "Guide" on one line, not "Guid/e".
    for header in ("Activity", "Call", "Guide"):
        assert header in lines, (header, lines)


def test_the_na_footnote_is_not_split_at_a_page_foot(monkeypatch):
    """Pushed down until the page foot falls between its second and third lines,
    the footnote moves to the next page whole."""
    genes = GENES + [
        {"gene": f"GENE{i}", "diplotype": "*1/*2", "phenotype": "N/A"} for i in range(3)
    ]
    page = _page(genes)
    results = page.index("<h2>Genetic Results</h2>")
    note = page.index('<p class="header-note"><strong>N/A</strong>', results)
    marked = (
        page[:note]
        + '<div style="height: {spacer}px"></div>'
        + page[note:].replace(
            '<p class="header-note"', '<p id="na-note" class="header-note"', 1
        )
    )
    first = _layout(monkeypatch, marked.replace("{spacer}", "0"))
    (index,) = section_pages(first, "na-note")
    page_box = first.pages[index]._page_box
    box = _outer_box(page_box, _section_element(page_box, "na-note"))
    lines = [c for c in box.children if c.__class__.__name__ == "LineBox"]
    assert len(lines) >= 4, len(lines)
    foot = page_box.content_box_y() + page_box.height
    spacer = foot - (box.content_box_y() + 2.5 * lines[0].height)
    document = _layout(monkeypatch, marked.replace("{spacer}", f"{spacer:.1f}"))
    assert len(section_pages(document, "na-note")) == 1
