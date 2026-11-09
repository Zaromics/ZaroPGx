"""The two marks the report puts in front of a caveat.

They are not interchangeable: the caution mark goes on something a reader must
attend to, the info mark on something merely being stated -- most often an
all-clear. ``generator``'s ``workflow_warnings`` docstring describes the channel
that carries both.

**Why they are drawn rather than typed.** As characters these marks cannot be
what the report needs them to be. U+26A0 with the emoji selector is a color
triangle in a browser but a thin monochrome outline in the PDF, because
WeasyPrint has no color-emoji font in the shipped image -- so the report has
never actually had a yellow caution mark in print. Its counterpart U+2139 is
worse: the only fonts in that image carrying it draw a plain serif "i", which
reads as a stray letter rather than an icon. U+1F6C8 prints but loses its color
in a browser.

Drawn, both marks are the same color, weight and size in the page and in the
PDF, which is the point: the report says one thing in two outputs.

Templates reach these through ``CAUTION`` and ``INFO``, registered as globals by
``generator.register_report_template_helpers``. Python that builds an alert
fragment interpolates ``CAUTION_SVG`` or ``INFO_SVG`` directly.
``tests/test_caveat_marks.py`` fails if a surface types a mark instead.
"""

from __future__ import annotations

# 1em square, nudged onto the text baseline. aria-hidden because the words that
# follow each mark already say what it is, so a screen reader gains nothing from
# announcing the image.
_BOX = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" '
    'aria-hidden="true" focusable="false" '
    'style="display:inline-block;width:1em;height:1em;vertical-align:-0.13em">'
)

# Amber triangle, white exclamation: the same amber as the warning bar.
CAUTION_SVG = (
    _BOX + '<path d="M8 1.4a1.3 1.3 0 0 1 1.12.65l6.0 10.4A1.3 1.3 0 0 1 14 14.5H2'
    'a1.3 1.3 0 0 1-1.12-1.95l6.0-10.4A1.3 1.3 0 0 1 8 1.4z" fill="#f39c12"/>'
    '<rect x="7.1" y="5.4" width="1.8" height="4.6" rx="0.9" fill="#ffffff"/>'
    '<circle cx="8" cy="11.8" r="1.05" fill="#ffffff"/>'
    "</svg>"
)

# Blue disc, white "i": the same blue as the informational bar.
INFO_SVG = (
    _BOX + '<circle cx="8" cy="8" r="7" fill="#3498db"/>'
    '<rect x="7.1" y="6.6" width="1.8" height="5.4" rx="0.9" fill="#ffffff"/>'
    '<circle cx="8" cy="4.3" r="1.1" fill="#ffffff"/>'
    "</svg>"
)
