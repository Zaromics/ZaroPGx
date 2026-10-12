"""The PDF's workflow diagram is whole on its page, in its own proportions.

Print CSS gives the diagram a page of its own. It used to size the image to a fixed
200 x 287mm box and leave object-fit to keep its shape. That held for the old,
generic diagram, twice as tall as wide, but a run's own diagram is about as wide as
tall, and WeasyPrint placed it 130px left of the box: its left side ran off the page
and an empty strip was left on the right. The test laid the page out with plain
WeasyPrint, not through the app's PDF pipeline, whose stylesheet capped the image
at 280px with !important, so neither showed. The image now carries its own size
(generator.workflow_pdf_style), and this lays the page out as the app does.
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

import pytest

import app.reports.generator as generator_module
from app.reports.generator import workflow_pdf_style

weasyprint = pytest.importorskip("weasyprint")
from weasyprint.layout.replaced import replacedbox_layout  # noqa: E402

MM = 96 / 25.4


def _png(width, height):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (250, 250, 210)).save(buffer, format="PNG")
    return buffer.getvalue()


def _pdf(monkeypatch, png):
    def diagram(steps, file_type, facts=None):
        return {
            "recorded": True,
            "svg": "",
            "svg_bytes": b"",
            "png": png,
            "png_data_uri": "data:image/png;base64,"
            + __import__("base64").b64encode(png).decode(),
            "pdf_style": workflow_pdf_style(png),
            "html": "",
        }

    monkeypatch.setattr(generator_module, "job_workflow_diagram", diagram)
    monkeypatch.setattr(generator_module, "load_job_step_records", lambda db, job: [])
    for key in list(generator_module.REPORT_CONFIG):
        if key.startswith(("write_", "show_")):
            monkeypatch.setitem(
                generator_module.REPORT_CONFIG, key, key in ("write_html", "write_pdf")
            )
    rendered = []
    original = generator_module.render_pdf_document

    def capture(html):
        document, levels = original(html)
        rendered.append(document)
        return document, levels

    monkeypatch.setattr(generator_module, "render_pdf_document", capture)
    out = Path(tempfile.mkdtemp())
    generator_module.generate_report(
        {"data": {"genes": [], "drugRecommendations": []}}, str(out), {"id": "p1"}
    )
    return rendered[-1]


def _diagram_page(document):
    for page in document.pages:
        boxes = list(page._page_box.descendants())
        image = [
            b
            for b in boxes
            if getattr(b, "element", None) is not None
            and b.element.get("alt") == "Workflow Diagram"
            and b.__class__.__name__.endswith("ReplacedBox")
        ]
        if image:
            title = [
                b
                for b in boxes
                if getattr(b, "element", None) is not None
                and b.element.tag.rsplit("}", 1)[-1] == "h2"
                and "Workflow Diagram"
                in "".join(t.text for t in b.descendants() if getattr(t, "text", None))
            ]
            return page._page_box, image[0], title[0]
    raise AssertionError("no diagram page")


@pytest.mark.parametrize(
    "width, height",
    [(1233, 1091), (1508, 1881), (2095, 4100), (4000, 1500)],
    ids=["a-vcf-run", "a-cram-run", "tall", "wide"],
)
def test_the_diagram_is_whole_and_in_proportion(monkeypatch, width, height):
    page, image, title = _diagram_page(_pdf(monkeypatch, _png(width, height)))
    draw_width, draw_height, draw_x, draw_y = replacedbox_layout(image)
    # Drawn where its box is, at the box's size: nothing shifted or cropped. (The
    # broken page drew a run's diagram at x = -130, off the paper.)
    assert (draw_x, draw_y) == pytest.approx(
        (image.content_box_x(), image.content_box_y()), abs=0.5
    )
    assert (draw_width, draw_height) == pytest.approx(
        (image.width, image.height), abs=0.5
    )
    assert draw_height / draw_width == pytest.approx(height / width, rel=0.01)
    # On the page, inside its margins, under its title.
    left = image.content_box_x()
    top = image.content_box_y()
    assert left >= page.content_box_x() - 0.5
    assert left + image.width <= page.content_box_x() + page.width + 0.5
    assert top + image.height <= page.content_box_y() + page.height + 0.5
    assert title.position_y + title.margin_height() <= top
    # As large as the page allows: the 178mm text width or the height under the title.
    assert image.width == pytest.approx(178 * MM, rel=0.01) or image.height == (
        pytest.approx(250 * MM, rel=0.01)
    )


def test_the_style_is_none_without_a_png():
    assert workflow_pdf_style(b"") == ""
    assert workflow_pdf_style(b"<svg></svg>" * 4) == ""
