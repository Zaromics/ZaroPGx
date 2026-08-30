"""Each fact in the report has one home, chosen by what it is about.

The report answers three different questions and had stopped separating them,
because facts were added wherever a plausible-looking container already existed:

* **what was analysed** (this sample) -- Sample ID, date, reference build and
  liftover outcome. Belongs in the header block, which is where a reader looks.
  The liftover result was instead stranded on the Executive Summary page.
* **how it was analysed** (the tooling) -- PharmCAT's definition build, its
  matcher version, its guideline data version, the assume-reference flag.
  Belongs under Methodology, a section that exists for exactly this. Three of
  those four were also on the Executive Summary page, sharing one paragraph with
  the liftover counts: four facts about three subjects, in one grey block.
* **what qualifies the results** (caveats) -- Alerts and Warnings. This replayed
  the upload screen verbatim, including advice to upload a different file and a
  future-tense promise about what liftover was going to do, on a page describing
  a run that had already finished and reported those exact counts.

The pre-flight/standing split is made at the source: ``file_processor`` tags
advisory copy ``class='preflight'`` and the report drops it. Deciding at the
point of authorship is the difference between routing and pattern-matching, and
it means a new warning has to state which it is.

Rendered through the app's own Jinja environment, because where a fact lands is a
property of the template, not of the string.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT = REPO_ROOT / "app" / "reports" / "templates" / "report_template.html"
FILE_PROCESSOR = REPO_ROOT / "app" / "api" / "utils" / "file_processor.py"
GENERATOR = REPO_ROOT / "app" / "reports" / "generator.py"

LIFT = "The input file was uploaded as GRCh37 and then lifted over to GRCh38."


def _render(**overrides) -> str:
    from app.reports.generator import env

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
        "genome_build": "GRCh38.p14",
        "named_allele_matcher_version": "2.0.0",
        "pharmcat_data_version": "2026-07-13",
        "liftover_provenance": LIFT,
    }
    context.update(overrides)
    return env.get_template("report_template.html").render(**context)


def _section(html: str, marker: str, length: int = 1400) -> str:
    i = html.index(marker)
    return html[i : i + length]


# --------------------------------------------------------------------------
# What was analysed -> the header block
# --------------------------------------------------------------------------


def test_the_reference_build_is_stated_in_the_header_block():
    html = _render()
    block = _section(html, '<div class="report-info">')
    assert LIFT in block, "the liftover result is not in the header block"


def test_the_header_block_does_not_repeat_the_id_twice():
    """Sample ID and Report ID rendered the same UUID on consecutive lines."""
    block = _section(_render(), '<div class="report-info">')
    assert "Report ID" not in block, block


def test_a_native_grch38_run_states_no_build_line():
    block = _section(_render(liftover_provenance=None), '<div class="report-info">')
    assert "lifted over" not in block


# --------------------------------------------------------------------------
# How it was analysed -> Methodology
# --------------------------------------------------------------------------


def test_the_tooling_provenance_sits_under_methodology():
    html = _render()
    methodology = _section(html, "<h2>Methodology</h2>", 2600)

    assert "PharmCAT's allele definitions" in methodology
    assert "Named Allele Matcher" in methodology
    assert "guideline data version" in methodology


def test_the_tooling_facts_are_not_also_left_on_the_executive_summary():
    """One home each. Two copies is how the paragraph accreted in the first place."""
    html = _render()
    assert html.count("Named Allele Matcher") == 1, "matcher version rendered twice"
    assert html.count("PharmCAT's allele definitions") == 1


def test_only_one_run_provenance_paragraph_exists():
    assert _render().count('<p class="run-provenance">') == 1


# --------------------------------------------------------------------------
# What qualifies the results -> Alerts, minus the pre-flight advice
# --------------------------------------------------------------------------


PREFLIGHT = "<p class='preflight'>Consider uploading a BAM instead.</p>"
STANDING = "<p>VCF datafiles lack the raw information for complete analysis.</p>"


def test_the_generator_drops_preflight_warnings():
    from app.reports.generator import generate_report  # noqa: F401  (import guard)

    source = GENERATOR.read_text(encoding="utf-8")
    assert "class='preflight'" in source, (
        "the report no longer filters pre-flight advisories; upload-screen advice "
        "will reappear in finished reports"
    )


def test_file_processor_tags_its_advisories_at_the_point_of_authorship():
    source = FILE_PROCESSOR.read_text(encoding="utf-8")
    assert source.count("<p class='preflight'>") >= 3, (
        "the pre-flight tags are gone from file_processor; the split has to be "
        "made where the copy is written, not guessed at render time"
    )


@pytest.mark.parametrize(
    "advisory",
    [
        "please consider uploading it instead",
        "using an upstream datafile(s) is strongly recommended",
        "Liftover will drop and report the number",
    ],
)
def test_each_known_advisory_is_tagged(advisory):
    """Named individually so removing a tag fails loudly rather than silently."""
    source = FILE_PROCESSOR.read_text(encoding="utf-8")
    line = next(l for l in source.splitlines() if advisory in l)
    assert "class='preflight'" in line, line.strip()[:120]


def test_a_standing_caveat_still_renders_in_the_report():
    """Negative control: the filter must not empty the Alerts section."""
    html = _render(workflow_warnings=[STANDING])
    assert "VCF datafiles lack the raw information" in html


# --------------------------------------------------------------------------
# Every renderer of this template must be able to compile it
# --------------------------------------------------------------------------


def test_no_environment_registers_template_helpers_by_hand():
    """Three Environments render report_template.html; each used to register
    its filters and tests itself, and Jinja resolves both at *compile* time --
    so an Environment missing one does not render a slightly worse report, it
    raises TemplateAssertionError out of get_template() and the caller falls
    back to a stub page saying "No analysis results available".

    That failure mode has landed twice, once per helper. Registration is now a
    single function; this pins that nobody goes back to assigning by hand.
    """
    for path in (GENERATOR, REPO_ROOT / "app" / "reports" / "pdf_generators.py"):
        source = path.read_text(encoding="utf-8")
        # The registrar itself assigns, of course. It names its parameter
        # `environment`, and every hand-registration this guards against uses the
        # local `env`, so the two are distinguishable without parsing scopes.
        offenders = [
            line.strip()
            for line in source.splitlines()
            if re.match(r"env\.(filters|tests)\[", line.strip())
        ]
        assert not offenders, (
            f"{path.name} registers Jinja helpers by hand: {offenders}. Use "
            "register_report_template_helpers(env) so every renderer of "
            "report_template.html gets the same set."
        )


def test_the_registrar_covers_both_helpers_the_template_uses():
    from jinja2 import Environment

    from app.reports.generator import register_report_template_helpers

    env = Environment()
    register_report_template_helpers(env)

    assert "activity_score_num" in env.filters
    assert "a_call" in env.tests


def test_the_template_only_uses_helpers_the_registrar_provides():
    """A new filter/test in the template must be added to the registrar."""
    from jinja2 import Environment

    from app.reports.generator import register_report_template_helpers

    env = Environment()
    register_report_template_helpers(env)
    source = REPORT.read_text(encoding="utf-8")

    used_tests = set(re.findall(r"selectattr\('[^']+',\s*'([a-z_]+)'\)", source))
    used_tests |= set(re.findall(r"rejectattr\('[^']+',\s*'([a-z_]+)'\)", source))
    builtin = {"equalto", "defined", "undefined", "none", "in", "eq", "ne"}

    missing = {t for t in used_tests if t not in builtin and t not in env.tests}
    assert not missing, (
        f"report_template.html uses Jinja tests {missing} that "
        "register_report_template_helpers does not provide; every renderer will "
        "fail to compile the template and fall back to the stub page"
    )
