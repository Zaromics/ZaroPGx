"""The interactive report's tabs stay inside their card at any width.

In one flex row, eleven tabs overran the card at 1280px ("Acknowledgements" past
its right edge) and gave the page a horizontal scroll at 1024px and narrower. The
row wraps now. Checked in Firefox and Chromium at 1280, 1024, 768 and 375px when
it was changed; this pins the rule that does it.
"""

from __future__ import annotations

import re
from pathlib import Path

TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "reports"
    / "templates"
    / "interactive_report.html"
)


def _rule(css: str, selector: str) -> str:
    match = re.search(r"(?m)^\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert match, selector
    return match.group(1)


def test_the_tab_row_wraps_and_a_tab_does_not():
    css = TEMPLATE.read_text(encoding="utf-8")
    tabs = _rule(css, ".tabs")
    assert "display: flex" in tabs
    assert "flex-wrap: wrap" in tabs
    tab = _rule(css, ".tab")
    assert "white-space: nowrap" in tab
