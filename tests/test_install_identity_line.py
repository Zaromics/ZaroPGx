"""The page says which ZaroPGx this is, who runs it, and when the code is not the image.

One muted line in the footer, server-rendered. It answers the three questions an
operator or a visitor actually has -- what version is this, whose install am I on, and
is it current -- without the page making a request of its own.

**It is deliberately a link, not a version check.** Knowing whether a newer release
exists means asking an outside service, which would be the first outbound call this
application makes on its own behalf: ``tests/test_no_external_service_defaults.py``
exists because ``render_with_kroki`` nearly defaulted to the public kroki.io, and a
self-hosted clinical install telling GitHub that it exists and what version it runs is
a decision for whoever runs it, not a default. ``docs/to-do.md`` carries the opt-in
design.

The warning is the part that earns its place. ``build_identity.disagreement`` returns a
line only when the served code is not the code the image was built from -- which compose
makes the normal case for a developer and a real surprise on an install -- and an
operator debugging "I fixed that bug, why is it still here" has nowhere else to see it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PAGE = REPO / "app" / "templates" / "index.html"
MAIN = REPO / "app" / "main.py"

#: WCAG 2.x AA for text that is not large.
MIN_RATIO = 4.5


def _luminance(colour: str) -> float:
    colour = colour.lstrip("#")
    parts = [int(colour[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    parts = [p / 12.92 if p <= 0.04045 else ((p + 0.055) / 1.055) ** 2.4 for p in parts]
    return 0.2126 * parts[0] + 0.7152 * parts[1] + 0.0722 * parts[2]


def _contrast(one: str, two: str) -> float:
    first, second = _luminance(one), _luminance(two)
    high, low = max(first, second), min(first, second)
    return (high + 0.05) / (low + 0.05)


def _variable(name: str) -> dict:
    """{theme: value} for a CSS custom property declared in both theme blocks."""
    text = PAGE.read_text(encoding="utf-8")
    light = re.search(r":root\s*\{(.*?)\}", text, re.S)
    dark = re.search(r"\[data-bs-theme='dark'\]\s*\{(.*?)\}", text, re.S)
    assert light and dark, "the page no longer declares both theme blocks"
    found = {}
    for theme, block in (("light", light.group(1)), ("dark", dark.group(1))):
        match = re.search(rf"{name}:\s*(#[0-9A-Fa-f]{{6}})", block)
        assert match, f"{name} is not declared for the {theme} theme"
        found[theme] = match.group(1)
    return found


def test_the_line_names_the_version_and_the_operator():
    page = PAGE.read_text(encoding="utf-8")
    line = re.search(r'class="[^"]*install-identity"[^>]*>(.*?)</p>', page, re.S)
    assert line, "the footer no longer carries the install identity line"
    body = line.group(1)
    assert "{{ app_version }}" in body
    assert "{{ operator }}" in body, "the operator is not shown"
    assert "{% if operator %}" in body, (
        "an unset operator must show nothing: a self-hosted install saying "
        '"hosted by ZaroPGx" would be a false claim about who holds the data'
    )


def test_the_releases_link_is_a_link_and_not_a_check():
    page = PAGE.read_text(encoding="utf-8")
    assert "{{ source_url }}/releases" in page, "no link to the releases"
    line = re.search(r'class="[^"]*install-identity"[^>]*>(.*?)</p>', page, re.S)
    assert line and "fetch(" not in line.group(1), (
        "the install line must not call out: an update check is opt-in, and this "
        "page makes no request of its own"
    )


def test_the_warning_appears_only_on_a_real_disagreement():
    page = PAGE.read_text(encoding="utf-8")
    assert (
        "{%- if build_disagreement %}" in page or "{% if build_disagreement %}" in page
    )
    assert MIN_RATIO  # the next test does the colours
    main = MAIN.read_text(encoding="utf-8")
    assert "build_identity.disagreement(_BUILD_IDENTITY)" in main, (
        "the page must take the warning from build_identity, which returns None "
        "unless both commits are known AND differ -- an unknown commit is not a "
        "disagreement and must not be shown as one"
    )


def test_the_operator_is_empty_until_someone_sets_it():
    main = MAIN.read_text(encoding="utf-8")
    assert re.search(
        r'OPERATOR\s*=\s*os\.getenv\(\s*"ZAROPGX_OPERATOR",\s*""', main
    ), "ZAROPGX_OPERATOR must default to empty"


@pytest.mark.parametrize(
    "theme,background", [("light", "--footer-bg"), ("dark", "--footer-bg")]
)
def test_the_warning_reads_against_the_footer_it_sits_on(theme, background):
    """Amber is 1.55:1 on the light footer, which is why this is per-theme."""
    warning = _variable("--install-warning")[theme]
    footer = _variable(background)[theme]
    ratio = _contrast(warning, footer)
    assert ratio >= MIN_RATIO, (
        f"the {theme} warning {warning} on {footer} is {ratio:.2f}:1, under the "
        f"{MIN_RATIO} AA asks of text this size"
    )
