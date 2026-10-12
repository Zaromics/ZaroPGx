"""The PDF prints a VCF header's columns apart, as a terminal shows them.

The header is printed with tab-size 4, and WeasyPrint gives a tab no width when the
field before it ends on a tab stop. A VCF's column line has two such fields, QUAL
and INFO, so every report printed "QUALFILTER" and "INFOFORMAT". The tabs are now
expanded before WeasyPrint sees them.
"""

from __future__ import annotations

import pytest

import app.reports.generator as generator_module

weasyprint = pytest.importorskip("weasyprint")

COLUMNS = "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tNA12878"
HEADER = "\n".join(
    [
        "##fileformat=VCFv4.2",
        "##contig=<ID=chr1,length=248956422>",
        COLUMNS,
    ]
)


def _pages(monkeypatch, tmp_path):
    for key in list(generator_module.REPORT_CONFIG):
        if key.startswith(("write_", "show_")):
            monkeypatch.setitem(
                generator_module.REPORT_CONFIG, key, key == "write_html"
            )
    (tmp_path / "sample.header.txt").write_text(HEADER, encoding="utf-8")
    result = generator_module.generate_report(
        {"data": {"genes": [], "drugRecommendations": []}},
        str(tmp_path),
        {"id": "p1"},
    )
    html = (tmp_path / result["html_path"].split("/")[-1]).read_text(encoding="utf-8")
    return weasyprint.HTML(string=html, base_url=str(tmp_path)).render().pages


def _header_lines(box, inside=False):
    """(text, width) of each printed line inside the element with class file-header."""
    element = getattr(box, "element", None)
    if element is not None and "file-header" in (element.get("class") or "").split():
        inside = True
    if inside and box.__class__.__name__ == "TextBox":
        yield box.text, box.width
    for child in getattr(box, "children", None) or []:
        yield from _header_lines(child, inside)


def test_the_column_line_is_as_wide_as_a_terminal_prints_it(monkeypatch, tmp_path):
    printed = {}
    for page in _pages(monkeypatch, tmp_path):
        for text, width in _header_lines(page._page_box):
            printed[text.replace(" ", "").replace("\t", "")] = (text, width)
    # The header is monospaced: a line without tabs gives the width of a column.
    fileformat, width = printed["##fileformat=VCFv4.2"]
    column = width / len(fileformat)
    _, chrom_width = printed[COLUMNS.replace("\t", "")]
    assert chrom_width == pytest.approx(len(COLUMNS.expandtabs(4)) * column, rel=0.01)
