"""The report footer must carry a year, and must stop losing it.

The PDF footer read "(c) 2024- Iliya Yaroshevskiy". ``author_name`` resolved and
``current_year`` did not, which narrows the cause to a context dict that supplied
one and not the other.

Four dicts feed these templates and each listed the branding keys by hand:

* ``generator.py``'s interactive-report context,
* ``generator.py``'s first ``template_data``,
* ``generator.py``'s **rebound** ``template_data`` -- the one actually rendered
  into the PDF,
* ``pdf_generators.py``'s WeasyPrint context.

Only the third was missing ``current_year``. That is why this has been fixed
repeatedly without staying fixed: three of the four dicts were already correct,
so a fix applied to whichever one someone opened looked right, tested right in
the HTML lane, and changed nothing about the PDF.

So the guard here is deliberately two-layered. The rendering test proves the
footer resolves; the source test removes the ability to hand-list these keys at
all, because a fifth context dict is the only way this comes back, and the
rendering test alone would not see one until someone rendered through it.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ["report_template.html", "interactive_report.html"]
CONTEXT_FILES = [
    REPO_ROOT / "app" / "reports" / "generator.py",
    REPO_ROOT / "app" / "reports" / "pdf_generators.py",
]


def _render(template_name: str, **overrides) -> str:
    from app.reports.generator import env, report_branding_context

    context = {
        "diplotypes": [],
        "recommendations": [],
        "gene_drug_recommendations": [],
        "organized_recommendations": [],
        "patient_id": "p",
        "report_id": "r",
        "report_date": "2026-08-30",
        "organization": "ZaroPGx",
        "disclaimer": "",
        **report_branding_context(),
    }
    context.update(overrides)
    return env.get_template(template_name).render(**context)


def _footer_year_text(html: str) -> str:
    match = re.search(r"&copy;\s*2024-([^<]*)", html)
    assert match is not None, "the copyright line is gone from the footer"
    return match.group(1)


@pytest.mark.parametrize("template_name", TEMPLATES)
def test_the_footer_states_a_four_digit_year(template_name):
    """The regression itself: '2024- Iliya Yaroshevskiy', no end year."""
    tail = _footer_year_text(_render(template_name))

    assert re.match(
        r"^\d{4}\b", tail.strip()
    ), f"footer reads '2024-{tail.strip()[:40]}' -- current_year did not resolve"


@pytest.mark.parametrize("template_name", TEMPLATES)
def test_the_year_is_the_current_one(template_name):
    tail = _footer_year_text(_render(template_name))
    assert tail.strip().startswith(str(datetime.now().year))


@pytest.mark.parametrize("template_name", TEMPLATES)
def test_the_author_alone_does_not_satisfy_the_footer(template_name):
    """Negative control.

    The bug rendered a *plausible* footer -- the author's name was there, and only
    a missing number gave it away. A test that merely looked for the author would
    have passed throughout.
    """
    html = _render(template_name, current_year=None)
    tail = _footer_year_text(html)
    assert not re.match(r"^\d{4}\b", tail.strip())


# --------------------------------------------------------------------------
# The structural half: no fifth dict can hand-list these again
# --------------------------------------------------------------------------


BRANDING_KEYS = ("author_name", "license_name", "license_url", "source_url")


@pytest.mark.parametrize("path", CONTEXT_FILES, ids=lambda p: p.name)
def test_no_context_dict_hand_lists_the_branding_keys(path):
    source = path.read_text(encoding="utf-8")
    inside_helper = False
    offenders = []

    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("def report_branding_context"):
            inside_helper = True
            continue
        if inside_helper:
            # The helper is the one place allowed to name them; it ends at its
            # closing brace.
            if stripped == "}":
                inside_helper = False
            continue
        # `"author_name": template_data.get(...)` forwards an already-built
        # context rather than constructing one, so it cannot drop a key the
        # source dict has. Only fresh construction is the hazard.
        if "template_data.get(" in stripped:
            continue
        for key in BRANDING_KEYS:
            if stripped.startswith(f'"{key}":'):
                offenders.append(stripped)

    assert not offenders, (
        f"{path.name} builds branding keys by hand: {offenders}. Spread "
        "**report_branding_context() instead -- a dict that lists four of the "
        "five keys renders a footer that looks right and is missing the year."
    )


def test_the_helper_supplies_every_key_the_footer_uses():
    from app.reports.generator import report_branding_context

    supplied = set(report_branding_context())
    template = (
        REPO_ROOT / "app" / "reports" / "templates" / "report_template.html"
    ).read_text(encoding="utf-8")

    footer = template[template.index('<div class="footer">') :]
    used = set(re.findall(r"\{\{\s*(\w+)", footer))
    branding = used & (set(BRANDING_KEYS) | {"current_year"})

    missing = branding - supplied
    assert not missing, f"the footer reads {missing}, which the helper does not supply"
