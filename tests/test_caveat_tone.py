"""Alerts and Warnings carries two tones, and the templates render both.

An entry is either a string -- a warning, amber bar, caution mark -- or
``{"tone": "info", "html": ...}``, blue bar, info mark. Before this split the
copy-number check's all-clear, which reports that nothing is wrong, sat in an
amber warning box beside the real warnings and looked exactly like them.
"""

import re
from typing import Mapping

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.reports.copy_number import (
    annotate_pharmcat_copy_number,
    pharmcat_copy_number_alert,
)
from app.reports.generator import register_report_template_helpers

AMBER = "#f39c12"
BLUE = "#3498db"
# Both marks are drawn: app/reports/marks.py says why.
from app.reports.marks import CAUTION_SVG as CAUTION
from app.reports.marks import INFO_SVG as INFO


def _result(diplotype, copy_number=None, cnv_model=None):
    """One gene of PyPGx's /genotype response, as test_report_copy_number builds it."""
    details = {"phenotype_method": None}
    if copy_number:
        details["copy_number"] = copy_number
    result = {"success": True, "gene": "", "diplotype": diplotype, "details": details}
    if cnv_model is not None:
        result["methods"] = {"beagle": True, "cnv_model": cnv_model}
    return result


def _all_clear():
    """Two genes whose copy number was measured and found normal, and nothing
    else to report: the case that produces the all-clear."""
    rows = [
        {
            "gene": "CYP2B6",
            "diplotype": "*1/*6",
            "call_source": "MATCHER",
            "tool_source": "PyPGx",
        },
        {"gene": "CYP4F2", "diplotype": "*1/*1", "call_source": "MATCHER"},
    ]
    pypgx = {
        "results": {
            "CYP2B6": _result("*1/*6", "Normal", cnv_model=True),
            "CYP4F2": _result("*1/*1", "Normal", cnv_model=True),
        }
    }
    annotate_pharmcat_copy_number(rows, pypgx)
    return rows, pypgx


def test_an_all_clear_is_information_not_a_warning():
    rows, pypgx = _all_clear()

    entry = pharmcat_copy_number_alert(rows, pypgx)

    assert isinstance(entry, Mapping), "an all-clear must carry its tone"
    assert entry["tone"] == "info"
    assert entry["html"].startswith("<p>" + INFO)
    assert "Structural alleles:" in entry["html"]
    assert CAUTION not in entry["html"], "an all-clear carries no caution mark"


def test_something_found_stays_a_plain_warning_string():
    """Only the all-clear changes shape; every other entry is a warning string."""
    rows = [
        {"gene": "CYP2B6", "diplotype": "*1/*6", "call_source": "MATCHER"},
        {"gene": "CYP2C19", "diplotype": "*1/*2", "call_source": "MATCHER"},
    ]
    annotate_pharmcat_copy_number(rows)

    entry = pharmcat_copy_number_alert(rows)

    assert isinstance(entry, str)
    assert CAUTION in entry


def _render(template_name, warnings):
    env = Environment(
        loader=FileSystemLoader("app/reports/templates"),
        autoescape=select_autoescape(["html", "xml"]),
    )
    # The marks are Jinja globals, so an Environment that skips this renders
    # them as nothing at all -- silently, unlike a missing filter.
    register_report_template_helpers(env)
    env.tests["a_call"] = lambda v: bool(v)
    env.tests["in_summary_panel"] = lambda v: True
    src = env.loader.get_source(env, template_name)[0]
    # Just the Alerts and Warnings block, so the rest of the report's context
    # is not needed to check how one entry is painted.
    start = src.index("{% if workflow_warnings")
    end = src.index("{% endif %}", src.index("No alerts or warnings")) + len(
        "{% endif %}"
    )
    return env.from_string(src[start:end]).render(workflow_warnings=warnings)


def _bar(html):
    found = re.findall(r"border-left: 4px solid (#[0-9a-f]{6})", html)
    assert len(found) == 1, f"expected one box, got {found}"
    return found[0]


def test_the_pdf_template_paints_a_warning_amber_and_information_blue():
    assert (
        _bar(_render("report_template.html", ["<p>" + CAUTION + " warned</p>"]))
        == AMBER
    )
    assert (
        _bar(
            _render(
                "report_template.html",
                [{"tone": "info", "html": "<p>" + INFO + " fine</p>"}],
            )
        )
        == BLUE
    )


def test_the_interactive_template_paints_them_the_same_way():
    assert (
        _bar(_render("interactive_report.html", ["<p>" + CAUTION + " warned</p>"]))
        == AMBER
    )
    assert (
        _bar(
            _render(
                "interactive_report.html",
                [{"tone": "info", "html": "<p>" + INFO + " fine</p>"}],
            )
        )
        == BLUE
    )


def test_both_templates_show_the_entry_text():
    for name in ("report_template.html", "interactive_report.html"):
        assert "fine" in _render(
            name, [{"tone": "info", "html": "<p>" + INFO + " fine</p>"}]
        )
        assert "warned" in _render(name, ["<p>" + CAUTION + " warned</p>"])


def test_the_no_alerts_box_carries_the_info_mark():
    for name in ("report_template.html", "interactive_report.html"):
        html = _render(name, [])
        assert "No alerts or warnings" in html
        assert INFO in html, f"{name}'s empty state needs the info mark"
        assert _bar(html) == BLUE


def test_every_report_lane_registers_the_marks():
    """A mark is a Jinja global, so an Environment that forgets
    register_report_template_helpers drops it without raising -- unlike a
    missing filter, which fails at compile time. Both templates, rendered
    through the generator's own Environment, must show both marks."""
    from app.reports import generator

    for name in ("report_template.html", "interactive_report.html"):
        src = generator.env.loader.get_source(generator.env, name)[0]
        assert "{{ CAUTION }}" in src or "{{ INFO }}" in src, name

    assert "CAUTION" in generator.env.globals
    assert "INFO" in generator.env.globals
    assert str(generator.env.globals["CAUTION"]).startswith("<svg")
    assert str(generator.env.globals["INFO"]).startswith("<svg")
