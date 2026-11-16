"""Every phenotype badge is legible, not only the four that get a colour.

``.gene-phenotype`` set white text and no background; only Normal, Intermediate,
Poor and Rapid got one. Every other phenotype printed white on white: in 30x
NA12878's PDF Executive Summary that hid SLCO1B1 *1/*15 "Decreased Function",
RYR1 and CACNA1S "Uncertain Susceptibility", CFTR, and every "N/A",
"Indeterminate" and "Unknown". The base rule now has a neutral background that the
colour classes override.

The activity score badges too: white on orange, light green and turquoise was
2.6, 1.4 and 2.1 to 1. Those carry dark text now, as the yellow one did.

**The threshold here was 3:1 and should not have been.** That is WCAG's allowance for
large text, which means 18.66px bold or 24px regular -- and these badges are bold at
``0.95em`` over a print body of ``10px``, so **9.5px in the PDF** and about 15px in the
page. Neither is large, so 4.5:1 applies. Raising the gate failed exactly one tier:
tier-5's white on ``#28a745`` at **3.13:1**, which the 3:1 gate had been passing. It is
``#1e7e34`` now, 5.14:1.

Worth knowing: tier-1 (white on ``#dc3545``) passes at **4.53:1**, so any darkening of
that text or lightening of that red breaks it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

#: WCAG 2.x AA for text that is not large: these badges are 9.5px in the PDF.
MIN_RATIO = 4.5

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


def _variables(css: str) -> dict:
    """`--success-color: #2ecc71` from the `:root` block, so `var(...)` resolves.

    The interactive report styles its badges through theme variables, and reading the
    literal text found no colour at all -- which is how its Normal Metabolizer badge
    sat at 2.10:1 while this file was watching the other template.
    """
    root = re.search(r":root\s*\{([^}]*)\}", css)
    if not root:
        return {}
    return {
        name: value.strip()
        for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", root.group(1))
    }


def _colour(raw: str, variables: dict) -> str | None:
    """A literal colour, or what the `var(...)` around it resolves to."""
    named = {"white": "#ffffff", "black": "#000000"}
    raw = raw.strip()
    reference = re.fullmatch(r"var\((--[\w-]+)\)", raw)
    if reference:
        raw = variables.get(reference.group(1), "")
    raw = raw.strip()
    if raw in named:
        return named[raw]
    return raw if re.fullmatch(r"#[0-9A-Fa-f]{3,6}", raw) else None


@pytest.mark.parametrize(
    "name", ["report_template.html", "interactive_report.html", "style.css"]
)
def test_every_coloured_badge_reads_at_four_and_a_half_to_one(name):
    css = (TEMPLATES / name).read_text(encoding="utf-8")
    rules = _rules(css)
    variables = _variables(css)
    checked = 0
    for selector, body in rules.items():
        if not re.fullmatch(
            r"\.(as-tier-\d|gene-phenotype|[\w-]*-metabolizer|[\w-]*-function)"
            r"(?:\s*,\s*\.[\w-]+)*",
            selector,
        ):
            continue
        background = re.search(
            r"background(?:-color)?:\s*(#\w+|white|black|var\(--[\w-]+\))", body
        )
        if not background:
            continue
        back = _colour(background.group(1), variables)
        if back is None:
            continue
        text = re.search(r"(?<![-\w])color:\s*(#\w+|white|black|var\(--[\w-]+\))", body)
        # The badges' own white, where a rule sets none (.as-badge, the pill).
        front = _colour(text.group(1), variables) if text else "#ffffff"
        if front is None:
            continue
        ratio = _contrast(back, front)
        assert ratio >= MIN_RATIO, (name, selector, round(ratio, 2))
        checked += 1
    assert checked >= 5, (name, checked)


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
    badge the icon was not there at all. Each path, the ring and the needle, now
    carries its badge's text colour itself, at full ink, and reads at 3:1 on every
    tier (the ring at half opacity fell under it on five)."""
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
        assert "opacity" not in svg, (tier, svg)
        assert _contrast(fill, background) >= 3, ("ring", tier, fill, background)
        assert _contrast(stroke, background) >= 3, ("needle", tier, stroke, background)
