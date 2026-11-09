"""The report draws its two caveat marks; no surface types them.

They used to be characters, and they had drifted: every alert builder wrote
U+26A0 with the emoji selector, every template wrote a bare U+26A0. WeasyPrint
draws both the same way, so the PDF never showed it and no test caught it; a
browser with a colour emoji font draws one in colour and the other in
monochrome, a few lines apart on one page.

Characters could not fix it either. U+26A0 is a colour triangle in a browser
and a thin monochrome outline in the PDF, so the report had no yellow caution
mark in print; U+2139's only fonts in the shipped image draw a plain serif "i".
Both marks are now SVG in app/reports/marks.py -- same colour, weight and size
in the page and in the PDF. This file keeps them that way.
"""

from pathlib import Path

import pytest

from app.reports.marks import CAUTION_SVG, INFO_SVG

# Written as code points so that a find-and-replace over the repo's marks
# cannot quietly rewrite the test that polices them.
CAUTION_CHAR = chr(0x26A0)
INFO_CHAR = chr(0x2139)
SELECTOR = chr(0xFE0F)

ROOT = Path(__file__).resolve().parents[1]

# Everything that writes text into a report: the alert builders, and the
# templates that lay the report out.
SURFACES = sorted(
    [
        ROOT / "app/reports/alt_locus.py",
        ROOT / "app/reports/caller_agreement.py",
        ROOT / "app/reports/copy_number.py",
        ROOT / "app/reports/generator.py",
        ROOT / "app/reports/rarity.py",
        ROOT / "app/reports/read_phase.py",
        ROOT / "app/utils/pharmcat_assume_ref.py",
        ROOT / "app/api/utils/file_processor.py",
    ]
    + list((ROOT / "app/reports/templates").glob("*.html"))
)


def _typed(path: Path, mark: str) -> list[int]:
    """Line numbers that type `mark` instead of drawing it. A logger line is
    not report text, and a comment may name the character it explains."""
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.lstrip()
        if "logger" in line or stripped.startswith(("#", "{#", "*")):
            continue
        if mark in line:
            out.append(n)
    return out


@pytest.mark.parametrize("path", SURFACES, ids=lambda p: p.name)
def test_no_surface_types_the_caution_mark(path):
    typed = _typed(path, CAUTION_CHAR)

    assert not typed, (
        f"{path.relative_to(ROOT)} types U+26A0 on line(s) {typed}. "
        "The caution mark is drawn: marks.CAUTION_SVG, or {{ CAUTION }} in a "
        "template."
    )


@pytest.mark.parametrize("path", SURFACES, ids=lambda p: p.name)
def test_no_surface_types_the_info_mark(path):
    typed = _typed(path, INFO_CHAR)

    assert not typed, (
        f"{path.relative_to(ROOT)} types U+2139 on line(s) {typed}. "
        "The info mark is drawn: marks.INFO_SVG, or {{ INFO }} in a template."
    )


def test_the_marks_carry_the_colours_of_the_bars_they_sit_in():
    assert "#f39c12" in CAUTION_SVG, "the caution mark is the amber of its bar"
    assert "#3498db" in INFO_SVG, "the info mark is the blue of its bar"
    assert CAUTION_SVG != INFO_SVG


def test_both_marks_are_actually_reached():
    """Guards the tests above from passing because nothing uses either mark."""
    python = [p for p in SURFACES if p.suffix == ".py"]
    templates = [p for p in SURFACES if p.suffix == ".html"]

    by_name = sum(p.read_text(encoding="utf-8").count("CAUTION_SVG") for p in python)
    in_templates = sum(
        p.read_text(encoding="utf-8").count("{{ CAUTION }}") for p in templates
    )

    assert by_name >= 30, "the caution mark went missing from the builders"
    assert in_templates >= 20, "the caution mark went missing from the templates"
    assert any("INFO_SVG" in p.read_text(encoding="utf-8") for p in python)
    assert any("{{ INFO }}" in p.read_text(encoding="utf-8") for p in templates)


def test_a_mark_survives_autoescape_in_a_template():
    """Registered as Markup, or every report would show raw SVG source."""
    from jinja2 import Environment, select_autoescape

    from app.reports.generator import register_report_template_helpers

    env = Environment(autoescape=select_autoescape(["html", "xml"]))
    register_report_template_helpers(env)

    rendered = env.from_string("{{ CAUTION }}{{ INFO }}").render()

    assert "&lt;svg" not in rendered
    assert rendered.count("<svg") == 2
