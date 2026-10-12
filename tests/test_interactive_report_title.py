"""The interactive report's title can be read.

Its header is a band in the primary colour with white text, but the h1 inside it
took the headings' rule, also the primary colour: "Individual Pharmacogenomic
Report" rendered dark blue on dark blue, invisible, since the first commit.
"""

from __future__ import annotations

import re
from pathlib import Path

TEMPLATE = (
    Path(__file__).resolve().parent.parent
    / "app"
    / "reports"
    / "templates"
    / "interactive_report.html"
).read_text(encoding="utf-8")


def _rule(selector: str) -> str:
    match = re.search(
        rf"^\s*{re.escape(selector)}\s*\{{([^}}]*)\}}", TEMPLATE, re.MULTILINE
    )
    assert match, selector
    return match.group(1)


def test_the_title_is_not_drawn_in_the_header_s_own_colour():
    header = _rule("header")
    assert "background-color: var(--primary-color)" in header
    # The more specific rule wins over "h1, h2, h3, h4 { color: primary }".
    assert "color: white" in _rule("header h1")
