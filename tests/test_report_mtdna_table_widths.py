"""The mtDNA table's columns are the same width whatever its values.

Sized by content, the label column took about half the table beside PharmCAT's
example VCF's no-call ("Not determined", "No call") and under a third beside the
NA12878 demo's phenotype ("normal risk of aminoglycoside-induced hearing loss").
Fixed at 32% / 68%, both reports draw it alike, with the values starting where
the Genetic Results table starts its Phenotype column.
"""

from __future__ import annotations

import re

import pytest

import app.reports.generator as generator_module
from tests.mtdna_outputs import write_na12878_run, write_no_chrm_vcf_run

pytestmark = pytest.mark.skipif(
    not generator_module._HAS_WEASYPRINT,
    reason="WeasyPrint native libraries unavailable",
)

DEMO_PHENOTYPE = "normal risk of aminoglycoside-induced hearing loss"


def _html(monkeypatch, tmp_path, write_run, genes):
    for key in list(generator_module.REPORT_CONFIG):
        if key.startswith(("write_", "show_")):
            monkeypatch.setitem(
                generator_module.REPORT_CONFIG, key, key == "write_html"
            )
    write_run(tmp_path)
    result = generator_module.generate_report(
        {"data": {"genes": genes, "drugRecommendations": []}},
        str(tmp_path),
        {"id": "p1"},
    )
    return (tmp_path / result["html_path"].split("/")[-1]).read_text(encoding="utf-8")


def _descendants(box):
    for child in getattr(box, "children", None) or []:
        yield child
        yield from _descendants(child)


def _classes(box):
    element = getattr(box, "element", None)
    return (element.get("class") or "").split() if element is not None else []


def _text(box):
    return "".join(
        getattr(b, "text", "") for b in _descendants(box) if hasattr(b, "text")
    )


def _mtdna_table(html):
    document, _ = generator_module.render_pdf_document(html)
    for page in document.pages:
        for box in _descendants(page._page_box):
            if type(box).__name__ == "TableBox" and "mtdna-table" in _classes(box):
                return box
    raise AssertionError("no mtDNA table in the PDF")


@pytest.mark.parametrize(
    "write_run, genes, value",
    [
        (write_no_chrm_vcf_run, [], "Not determined"),
        (
            write_na12878_run,
            [
                {
                    "gene": "MT-RNR1",
                    "diplotype": "Reference",
                    "phenotype": DEMO_PHENOTYPE,
                }
            ],
            DEMO_PHENOTYPE,
        ),
    ],
    ids=["example-vcf-no-call", "na12878-demo"],
)
def test_the_label_column_is_a_fixed_share(
    monkeypatch, tmp_path, write_run, genes, value
):
    html = _html(monkeypatch, tmp_path, write_run, genes)
    assert re.search(r'<table class="mtdna-table">\s*<colgroup>', html)
    assert value in html
    table = _mtdna_table(html)
    label, rest = table.column_widths
    assert label == pytest.approx(0.32 * (label + rest), abs=0.5)

    cells = [b for b in _descendants(table) if type(b).__name__ == "TableCellBox"]
    widest = next(c for c in cells if _text(c) == "Haplogroup quality")
    lines = [b for b in _descendants(widest) if type(b).__name__ == "LineBox"]
    assert len(lines) == 1, "the widest label should keep to one line"
    if value == DEMO_PHENOTYPE:
        cell = next(c for c in cells if _text(c) == DEMO_PHENOTYPE)
        lines = [b for b in _descendants(cell) if type(b).__name__ == "LineBox"]
        assert len(lines) == 1, "the demo's phenotype should keep to one line"
