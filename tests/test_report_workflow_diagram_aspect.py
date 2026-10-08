"""The PDF's workflow diagram keeps its proportions.

Print CSS gives the diagram a whole page: the image box is 200 x 287 mm. The image
was drawn to fill that box, whatever its shape; a real run's diagram is about twice
as tall as it is wide, so the PDF showed it 36% too wide, its text and shapes with
it. It is now scaled to fit, the same in both directions.
"""

from __future__ import annotations

import base64
import io

import pytest

import app.reports.generator as generator_module

weasyprint = pytest.importorskip("weasyprint")
from weasyprint.layout.replaced import replacedbox_layout  # noqa: E402

FIGURE = '<div class="workflow-figure">'


def _png(width, height):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (250, 250, 210)).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


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
    return (tmp_path / result["html_path"].split("/")[-1]).read_text(encoding="utf-8")


@pytest.mark.parametrize("width, height", [(2095, 4100), (4000, 1500)])
def test_the_diagram_is_scaled_alike_both_ways(monkeypatch, tmp_path, width, height):
    """A tall diagram (a real run's: 2095 x 4100) and a wide one."""
    html = _report_html(monkeypatch, tmp_path)
    start = html.index(FIGURE)
    end = html.index("</div>", start)
    html = (
        html[:start]
        + FIGURE
        + f'<img id="diagram" src="{_png(width, height)}" alt="Workflow Diagram" />'
        + html[end:]
    )
    document = weasyprint.HTML(string=html, base_url=str(tmp_path)).render()
    (image,) = [
        box
        for page in document.pages
        for box in page._page_box.descendants()
        if getattr(box, "element", None) is not None
        and box.element.get("id") == "diagram"
    ]
    draw_width, draw_height, _, _ = replacedbox_layout(image)
    assert draw_height / draw_width == pytest.approx(height / width, rel=0.01)
    # It still uses the page: it fills one dimension of the box.
    assert max(draw_width / image.width, draw_height / image.height) == pytest.approx(
        1, rel=0.01
    )
