"""Every phenotype badge is legible, not only the four that get a colour.

``.gene-phenotype`` set white text and no background; only Normal, Intermediate,
Poor and Rapid got one. Every other phenotype printed white on white: in 30x
NA12878's PDF Executive Summary that hid SLCO1B1 *1/*15 "Decreased Function",
RYR1 and CACNA1S "Uncertain Susceptibility", CFTR, and every "N/A",
"Indeterminate" and "Unknown". The base rule now has a neutral background that the
colour classes override.

The activity score badges too: white on orange, light green and turquoise was
2.6, 1.4 and 2.1 to 1, under the 3:1 WCAG asks even of bold text. Those carry
dark text now, as the yellow one did.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "reports" / "templates"


def _rules(css: str) -> dict:
    return {
        sel.strip(): body for sel, body in re.findall(r"([.\w#,\s-]+)\{([^}]*)\}", css)
    }


@pytest.mark.parametrize(
    "name", ["report_template.html", "interactive_report.html", "style.css"]
)
def test_the_base_badge_has_a_background_the_colours_override(name):
    css = (TEMPLATES / name).read_text(encoding="utf-8")
    rules = _rules(css)
    base = rules[".gene-phenotype"]
    assert "color: white" in base
    assert re.search(r"background-color:\s*#[0-9a-fA-F]{3,6}", base), name
    # The colour classes come later, so they still win at equal specificity.
    start = css.index(".gene-phenotype {")
    for colour in (
        ".normal-metabolizer",
        ".poor-metabolizer",
        ".intermediate-metabolizer",
    ):
        assert css.index(colour + " {") > start, (name, colour)


def _luminance(colour: str) -> float:
    colour = colour.lstrip("#")
    if len(colour) == 3:
        colour = "".join(c * 2 for c in colour)
    channels = [int(colour[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    r, g, b = (
        c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels
    )
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: str, b: str) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_every_coloured_badge_reads_at_three_to_one():
    css = (TEMPLATES / "report_template.html").read_text(encoding="utf-8")
    rules = _rules(css)
    names = {"white": "#ffffff", "black": "#000000"}
    checked = 0
    for selector, body in rules.items():
        if not re.fullmatch(
            r"\.(as-tier-\d|gene-phenotype|\w+-metabolizer|\w+-function)", selector
        ):
            continue
        background = re.search(r"background-color:\s*(#\w+|white|black)", body)
        if not background:
            continue
        text = re.search(r"(?<![-\w])color:\s*(#\w+|white|black)", body)
        # The badges' own white, where a rule sets none (.as-badge, the pill).
        foreground = text.group(1) if text else "#ffffff"
        ratio = _contrast(
            names.get(background.group(1), background.group(1)),
            names.get(foreground, foreground),
        )
        assert ratio >= 3, (selector, round(ratio, 2))
        checked += 1
    assert checked >= 10


# One score per tier of the Executive Summary's activity score badge.
TIER_SCORES = {
    "CYP2D6": "0.0",
    "CYP2C9": "0.25",
    "CYP2C19": "0.5",
    "DPYD": "0.75",
    "CYP2B6": "1.0",
    "TPMT": "2.0",
    "NUDT15": "3.0",
}


def test_the_score_gauge_is_drawn_in_its_badges_text_colour():
    """The gauge was `fill: currentColor` in CSS, which WeasyPrint ignores (it
    warned so), and currentColor on its needle came out black: on the black 0.0
    badge the icon was not there at all. Each path now carries its badge's text
    colour itself, which reads at 3:1 on every tier."""
    from app.reports.generator import env

    page = env.get_template("report_template.html").render(
        diplotypes=[
            {
                "gene": gene,
                "diplotype": "*1/*2",
                "phenotype": "Intermediate Metabolizer",
                "activity_score": score,
            }
            for gene, score in TIER_SCORES.items()
        ],
        recommendations=[],
        report_id="test-report",
        report_date="2026-10-20",
        disclaimer="",
    )
    rules = _rules((TEMPLATES / "report_template.html").read_text(encoding="utf-8"))
    names = {"white": "#ffffff"}
    badges = re.findall(
        r'<span class="as-badge as-tier-(\d)"[^>]*>\s*<svg[^>]*>(.*?)</svg>', page, re.S
    )
    assert sorted(tier for tier, _ in badges) == list("0123456"), badges
    for tier, svg in badges:
        assert "currentColor" not in svg, tier
        body = rules[f".as-tier-{tier}"]
        text = re.search(r"(?<![-\w])color:\s*(#\w+|white)", body)
        ink = names.get(text.group(1), text.group(1)) if text else "#ffffff"
        background = re.search(r"background-color:\s*(#\w+)", body).group(1)
        fill = re.search(r'fill="(#\w+)"', svg).group(1)
        stroke = re.search(r'stroke="(#\w+)"', svg).group(1)
        assert _luminance(fill) == _luminance(ink), (tier, fill, ink)
        assert _luminance(stroke) == _luminance(ink), (tier, stroke, ink)
        assert _contrast(stroke, background) >= 3, (tier, stroke, background)
