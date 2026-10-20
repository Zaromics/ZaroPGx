"""The Genetic Results table breaks lines between words, and its notes stay whole.

At 17% of the page, Diplotype and Phenotype were narrower than the widest word they
hold, so the PDF broke it wherever it ran out of room: MT-RNR1's phenotype read
"aminoglycosi" / "de-induced", and UGT2B17's copy-number note left the ")" of
"(WholeDel1Hom)" alone on a line. Names wider than any column -- PharmCAT's HGVS
allele names, PyPGx's composite copy-number codes -- broke the same way
("c.13994_1399" / "5delTCinsCT", "(NoncodingDel1" / "Hom)"); they now break where
generator.name_breaks and code_breaks allow. The N/A footnote under the table split
at a page foot, its last two lines atop the next page.
"""

from __future__ import annotations

import re

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


# Real names no column is wide enough for: PharmCAT 3.4.0's allele names and
# PyPGx's copy-number codes.
LONG_NAMES = [
    ("RYR1", "c.13994_13995delTCinsCT/Reference", "Uncertain Susceptibility"),
    ("RYR1", "c.14422_14423delinsAA/c.7042_7044delGAG", "Uncertain Susceptibility"),
    ("G6PD", "B (reference)/202G>A_376A>G_1264C>G", "Deficient"),
    ("DPYD", "c.1129-5923C>G, c.1236G>A (HapB3)/c.295_298delTCAT", "Poor Metabolizer"),
    (
        "MT-RNR1",
        "m.961T>del+Cn",
        "increased risk of aminoglycoside-induced hearing loss",
    ),
]
LONG_CODES = [
    ("CYP2D6", "*1/*2", "ParalogPartialDel1"),
    ("CYP2A6", "*1/*1", "ParalogWholeDel1"),
    ("CYP2E1", "*1/*7", "PartialDup1Hom"),
    ("UGT1A4", "*1/*1", "NoncodingDel1Hom"),
    ("SLC22A2", "*1/*1", "NoncodingDel1+PartialDel1"),
    ("UGT2B17", "*1/*2", "WholeDel1+PartialDel3"),
    ("CYP2D6", "*1/*36+*10", "WholeDel1+Tandem1A"),
]


def _long_rows():
    rows = [
        {"gene": g, "diplotype": d, "phenotype": p, "call_source": "MATCHER"}
        for g, d, p in LONG_NAMES
    ]
    for gene, diplotype, code in LONG_CODES:
        rows.append(
            {
                "gene": gene,
                "diplotype": diplotype,
                "phenotype": "N/A",
                "tool_source": "PyPGx",
                "pyPgxOnly": True,
                "pypgx": {
                    "copy_number": code,
                    "copy_number_summary": generator_module.copy_number_summary(code),
                },
            }
        )
    return rows


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


def _text(box):
    if getattr(box, "text", None):
        return box.text
    return "".join(_text(c) for c in getattr(box, "children", None) or [])


def _gene_table_cells(document):
    """(cell text, [text of each of its lines]) for the Genetic Results table's
    cells, headers included."""
    cells = []

    def walk(box, inside=False):
        element = getattr(box, "element", None)
        if element is not None and "gene-table" in (element.get("class") or ""):
            inside = True
        if inside and box.__class__.__name__ == "TableCellBox":
            source = " ".join("".join(element.itertext()).split())
            cells.append((source, []))
        if inside and cells and box.__class__.__name__ == "LineBox":
            # WeasyPrint lays a <wbr> out as a zero-width space.
            cells[-1][1].append(" ".join(_text(box).replace("\u200b", "").split()))
            return
        for child in getattr(box, "children", None) or []:
            walk(child, inside)

    for page in document.pages:
        walk(page._page_box)
    return cells


def _may_break(source, end):
    """Whether a line may end at ``end`` of the cell's text: at a space, after a
    hyphen, "/", ",", "_" or "+", or at a break point the filters give -- before
    ">", before a word of a code (Del1|Hom), before an HGVS del, ins or dup."""
    before, after = source[:end], source[end:]
    return (
        after.startswith(" ")
        or before[-1] in "-/,_+"
        or after.startswith(">")
        or bool(re.match(r"[A-Z][a-z]", after) and re.search(r"[a-z0-9]$", before))
        or bool(re.match(r"(del|ins|dup)", after) and re.search(r"[0-9A-Z]$", before))
    )


def _mid_word_breaks(cells):
    """(line, what follows it) wherever a cell's line ends where it may not."""
    found = []
    for source, lines in cells:
        position = 0
        for line in lines[:-1]:
            start = source.find(line, position) if line else -1
            assert start >= 0, (line, source)
            position = start + len(line)
            if position < len(source) and not _may_break(source, position):
                found.append((line, source[position : position + 12]))
    return found


def test_long_names_break_only_where_they_may(monkeypatch):
    document = _layout(monkeypatch, _page(GENES + _long_rows()))
    cells = _gene_table_cells(document)
    lines = [line for _, cell_lines in cells for line in cell_lines]
    assert _mid_word_breaks(cells) == []
    # They did need to break: no column holds them whole.
    assert not any("c.13994_13995delTCinsCT/" in line for line in lines), lines
    # The widest words of the common names stay whole.
    assert any("aminoglycoside-" in line for line in lines), lines
    assert any("CACNA1S" == line for line in lines), lines
    assert ")" not in lines, lines
    # The headers too: "Guide" on one line, not "Guid/e".
    for header in ("Activity", "Call", "Guide"):
        assert header in lines, (header, lines)


def test_break_points_are_escaped_and_placed():
    """Only in a word longer than a line holds: given one, the line breaker split
    "c.1627A>G" after "(*4)/c.1627A" though it fitted on a line of its own."""
    from app.reports.generator import code_breaks, name_breaks

    assert str(name_breaks("c.295_298delTCAT")) == "c.295_<wbr>298<wbr>delTCAT"
    assert str(name_breaks("B (reference)/202G>A_376A>G_1264C>G")) == (
        "B (reference)/<wbr>202G<wbr>&gt;A_<wbr>376A<wbr>&gt;G_<wbr>1264C<wbr>&gt;G"
    )
    # A word that fits a line, or one the line breaker already breaks, is left.
    assert str(name_breaks("c.1601G>A (*4)/c.1627A>G (*5)")) == (
        "c.1601G&gt;A (*4)/c.1627A&gt;G (*5)"
    )
    assert str(name_breaks("*1/*2")) == "*1/*2"
    assert str(name_breaks("Reference/Reference")) == "Reference/Reference"
    assert str(name_breaks("<script>/1")) == "&lt;script&gt;/<wbr>1"
    assert str(code_breaks("WholeDel1+PartialDel3")) == (
        "Whole<wbr>Del1+<wbr>Partial<wbr>Del3"
    )
    assert str(code_breaks("WholeDel1Hom")) == "WholeDel1Hom"
    assert str(code_breaks("<script>alert(1)</script>")) == (
        "&lt;script&gt;alert(1)&lt;/script&gt;"
    )


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
