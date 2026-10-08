"""The PDF prints the whole genomic file header: no line runs off the page.

The header was printed with white-space: pre. A BAM's header lines are long (@SQ
with checksum and URI, @RG, @PG command lines); on 30x NA12878, 52 of the 53 lines
the report keeps ran off the right edge, so the checksums, read groups and command
lines were not in the PDF at all. Each line now wraps at the page edge, its
continuation indented under it.
"""

from __future__ import annotations

import re

import pytest

import app.reports.generator as generator_module

weasyprint = pytest.importorskip("weasyprint")

UR = "file:/gpfs/internal/production/Resources/GRCh38/Homo_sapiens_assembly38.fasta"
HEADER = "\n".join(
    ["@HD\tVN:1.6\tSO:coordinate"]
    + [
        f"@SQ\tSN:chr{n}\tLN:{248956422 - n * 1000}\tM5:{'%032x' % (n * 7919)}\tUR:{UR}"
        for n in range(1, 23)
    ]
    + [
        "@RG\tID:NA12878.L1\tPL:illumina\tPU:HFTH3ALXX.1\tLB:NA12878-lib\tSM:NA12878"
        "\tCN:Example Sequencing Centre\tDS:30x PCR-free WGS, 150 bp paired-end",
        "@PG\tID:bwa\tPN:bwa\tVN:0.7.17\tCL:"
        + " ".join(
            f"--option-{n} /a/long/path/to/input_{n}.fastq.gz" for n in range(25)
        ),
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
    assert "Genomic Datafile Header" in html
    return weasyprint.HTML(string=html, base_url=str(tmp_path)).render().pages


def _header_text_boxes(box, inside=False):
    """Text boxes inside the element with class file-header."""
    element = getattr(box, "element", None)
    if element is not None and "file-header" in (element.get("class") or "").split():
        inside = True
    if inside and box.__class__.__name__ == "TextBox":
        yield box
    for child in getattr(box, "children", None) or []:
        yield from _header_text_boxes(child, inside)


def test_every_header_line_ends_inside_the_page(monkeypatch, tmp_path):
    printed = []
    for page in _pages(monkeypatch, tmp_path):
        right = page._page_box.content_box_x() + page._page_box.width
        for box in _header_text_boxes(page._page_box):
            assert box.position_x + box.width <= right + 0.5, box.text[:60]
            printed.append(box.text)
    # And nothing is lost: every character of the header is printed.
    assert re.sub(r"\s", "", "".join(printed)) == re.sub(r"\s", "", HEADER)
