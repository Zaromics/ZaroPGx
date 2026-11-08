"""Putting the report's drawn caveat marks back as characters, for tests.

The marks are SVG (app/reports/marks.py), so a rendered report carries an
``<svg>`` where the reader sees a triangle or a disc. A test that asserts on
report text is describing what the reader sees, so it normalises the drawings
back to the characters its expectations spell.

Those characters live in test expectations only. ``app/`` never types either
one, and ``tests/test_caveat_marks.py`` fails if it starts to.
"""

from app.reports.marks import CAUTION_SVG, INFO_SVG

CAUTION_CHAR = "\u26a0\ufe0f"
INFO_CHAR = "\u2139\ufe0f"


def marks_as_characters(html: str) -> str:
    """Swap each drawn mark for the character a test expectation spells."""
    return html.replace(CAUTION_SVG, CAUTION_CHAR).replace(INFO_SVG, INFO_CHAR)
