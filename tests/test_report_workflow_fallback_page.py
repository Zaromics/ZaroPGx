"""With no diagram, the Workflow Diagram heading must not land on the fallback.

The print lane gives this section its own page and positions the heading absolutely
over it (``@page workflow-page`` in report_template.html), because a diagram covers
the whole page and the heading has to sit on top of it. Nothing scoped that to the
case where a diagram exists. With only the HTML fallback -- a step list, or the note
that the steps were not recorded -- the fallback renders at the top of the figure and
the overlaid heading was drawn straight across it: in a real 46-page report the grey
note box ran through "Workflow Diagram" at its cap height, with the heading's own rule
crossing the box, and the remaining 70% of the page empty.

Measured on the rendered boxes rather than on the CSS text, because the defect was a
geometry one: the rule that caused it was correct in isolation and wrong only for this
content.
"""

from __future__ import annotations

import pytest

import app.reports.generator as generator_module

pytestmark = pytest.mark.skipif(
    not generator_module._HAS_WEASYPRINT,
    reason="WeasyPrint native libraries unavailable",
)

NOTE = "The steps of this run were not recorded."


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
    ) + getattr(box, "text", "")


def _boxes(document):
    for page in document.pages:
        for box in _descendants(page._page_box):
            yield page, box


def _report_html(monkeypatch, tmp_path):
    """A report with no workflow diagram, through the real generator."""
    for key in list(generator_module.REPORT_CONFIG):
        if key.startswith(("write_", "show_")):
            monkeypatch.setitem(
                generator_module.REPORT_CONFIG, key, key == "write_html"
            )
    result = generator_module.generate_report(
        {"data": {"genes": {}, "drugRecommendations": []}},
        str(tmp_path),
        {"id": "p1"},
    )
    return (tmp_path / result["html_path"].split("/")[-1]).read_text(encoding="utf-8")


def test_the_heading_does_not_overlap_the_fallback_note(monkeypatch, tmp_path):
    html = _report_html(monkeypatch, tmp_path)
    assert NOTE in html, "this fixture is meant to have no diagram"

    document, _levels = generator_module.render_pdf_document(html)

    heading = note = None
    for _page, box in _boxes(document):
        text = _text(box)
        if heading is None and text.strip() == "Workflow Diagram":
            heading = box
        if note is None and "workflow-text" in _classes(box):
            note = box
    assert heading is not None, "no Workflow Diagram heading in the rendered document"
    assert note is not None, "no fallback note in the rendered document"

    heading_bottom = heading.position_y + heading.height
    note_bottom = note.position_y + note.height
    overlap = min(heading_bottom, note_bottom) - max(
        heading.position_y, note.position_y
    )
    assert overlap <= 0, (
        "the heading and the fallback note overlap by "
        f"{overlap:.1f}px: heading y={heading.position_y:.1f}..{heading_bottom:.1f}, "
        f"note y={note.position_y:.1f}..{note_bottom:.1f}"
    )
    assert heading_bottom <= note.position_y, "the note must sit under the heading"


def test_one_sentence_does_not_claim_a_page_of_its_own(monkeypatch, tmp_path):
    """The dedicated page exists to hold a diagram. Without one it held a sentence."""
    html = _report_html(monkeypatch, tmp_path)
    document, _levels = generator_module.render_pdf_document(html)

    note_pages = {
        id(page) for page, box in _boxes(document) if "workflow-text" in _classes(box)
    }
    assert len(note_pages) == 1
    page_id = next(iter(note_pages))
    on_that_page = {
        _text(box).strip()
        for page, box in _boxes(document)
        if id(page) == page_id and "section" in _classes(box)
    }
    assert (
        len(on_that_page) > 1
    ), "the fallback is alone on its own page; it should flow with its neighbours"


def test_a_real_diagram_still_gets_the_overlaid_full_page_layout():
    """The flowed layout must apply only when there is nothing to overlay.

    The full-page treatment is deliberate: a diagram is placed to fill the page and
    the heading is drawn over it, 16mm from the paper's edge, so it sits where every
    other page's heading does. Scoping that to the diagram case is what fixes the
    fallback; this pins the other half, which must not change.
    """
    from app.reports.generator import env

    template = env.get_template("report_template.html")
    png = "data:image/png;base64,iVBORw0KGgo="

    with_diagram = template.render(workflow_png_data_uri=png)
    without = template.render()

    def section_tag(html):
        i = html.index('class="section workflow-section')
        return html[i : html.index(">", i)]

    assert "workflow-flowed" not in section_tag(
        with_diagram
    ), "a report with a diagram must keep the dedicated page and the overlaid heading"
    assert "workflow-flowed" in section_tag(
        without
    ), "a report with no diagram must flow with its neighbours"
