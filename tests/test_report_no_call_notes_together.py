"""The footnotes under the uncalled genes' names print together, with the names.

On PharmCAT's example VCF's report the footnotes giving why PyPGx made no call
were split at a page foot: footnote 1 ended page 6 and footnotes 2 and 3 began
page 7, apart from it and from the names they number. Each note was kept whole
but the notes were not kept together. They are one block now, kept with the last
lines of the names: when the block does not fit under them, those lines go over
with it.

The test moves the block down the page until the foot falls in footnote 2, and
states that layout first: with the block's rules switched off, footnote 1 ends one
page and footnote 2 begins the next.
"""

from __future__ import annotations

import pytest

import app.reports.generator as generator_module

pytestmark = pytest.mark.skipif(
    not generator_module._HAS_WEASYPRINT,
    reason="WeasyPrint native libraries unavailable",
)

# The three notes of the example VCF's report (generator: no_call_note).
NOTES = {
    "ABCB1": (
        "No genotype in the VCF at any of PyPGx's positions for this gene, which "
        "PyPGx would read as the reference sequence. If the file is a whole-genome "
        'variant list, run it again with "Assume absent sites = reference" on'
    ),
    "GSTM1": (
        "No genotype in the VCF at any of PyPGx's positions for this gene: either "
        "not covered, or both copies deleted (common for GSTM1), which a variant "
        "list cannot tell apart, and PyPGx would read either as the reference "
        "sequence. A whole-genome BAM or CRAM lets PyPGx count the gene's copies"
    ),
    "UGT2B17": "No SNV/indel-based star alleles available for this gene",
}
BLOCK = '<div class="uncalled-genes">'
LOOSE = (
    "<style>.no-call-notes { break-inside: auto !important; "
    "break-before: auto !important; }</style></head>"
)


def _html(monkeypatch, tmp_path):
    for key in list(generator_module.REPORT_CONFIG):
        if key.startswith(("write_", "show_")):
            monkeypatch.setitem(
                generator_module.REPORT_CONFIG, key, key == "write_html"
            )
    genes = [
        {"gene": gene, "diplotype": "", "phenotype": "", "no_call_note": note}
        for gene, note in NOTES.items()
    ]
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


def _has_class(box, name):
    element = getattr(box, "element", None)
    return element is not None and name in (element.get("class") or "").split()


def _layout(html):
    """Per footnote, (page, top, height) of its box; the last page the names are
    laid out on; and the foot of each page's content, all in CSS pixels."""
    document, _ = generator_module.render_pdf_document(html)
    notes, names, foot = [], [], []
    for index, page in enumerate(document.pages):
        page_box = page._page_box
        foot.append(page_box.content_box_y() + page_box.height)
        for box in _descendants(page_box):
            if type(box).__name__ == "BlockBox" and _has_class(box, "no-call-note"):
                notes.append((index, box.position_y, box.margin_height()))
            if _has_class(box, "uncalled-list"):
                names.append(index)
    return notes, max(names), foot


def _pushed(html, spacer):
    return html.replace(BLOCK, f'<div style="height: {spacer}px"></div>' + BLOCK, 1)


def test_the_footnotes_stay_together_with_the_names(monkeypatch, tmp_path):
    html = _html(monkeypatch, tmp_path)
    assert html.count('class="no-call-note"') == 3
    assert '<div class="no-call-notes">' in html

    notes, _, foot = _layout(html)
    page, top, height = notes[1]
    assert {n[0] for n in notes} == {page}, "unpushed, the notes share a page"
    # The foot of the page in the middle of footnote 2.
    spacer = foot[page] - (top + height / 2)
    assert spacer > 0
    pushed = _pushed(html, spacer)

    # The layout this guards against: footnote 1 on one page, 2 and 3 on the next.
    loose, _, _ = _layout(pushed.replace("</head>", LOOSE, 1))
    assert loose[0][0] + 1 == loose[1][0] == loose[2][0], loose

    notes, names_end, _ = _layout(pushed)
    assert len({n[0] for n in notes}) == 1, notes
    assert names_end == notes[0][0], "the names' last lines go over with the notes"
