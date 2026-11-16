"""A gene whose calling tool crashed is not a gene the sample could not answer.

docker/pypgx's wrapper records a gene whose call raised as
``{"success": False, "error": ...}``; generator.pypgx_failed_genes collects those and
mark_not_tested_and_failed puts the reason on the row as ``call_error``. The FHIR export
codes such a gene `error` and carries the text.

The report said something else. Both surfaces listed it with the genes the data could not
answer -- the print report under "this sample's data did not support a diplotype call",
the interactive one under "Not called from this sample's data" -- and named no failure
anywhere. Measured on a rendered report with PyPGx failed on CYP2D6, the gene PyPGx is
solely responsible for: the page blamed the sample for a MemoryError, while the FHIR
bundle from the same run said the tool failed.
"""

from __future__ import annotations

import re

import pytest

from app.reports.generator import (
    env,
    mark_not_tested_and_failed,
    pypgx_failed_genes,
    summary_rows,
)

TEMPLATES = ("report_template.html", "interactive_report.html")


def _text(markup: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", markup))


def _render(rows):
    context = {
        "diplotypes": rows,
        "recommendations": [],
        "gene_drug_recommendations": [],
        "organized_recommendations": [],
        "patient_id": "test-patient",
        "report_id": "test-report",
        "report_date": "2026-11-14",
        "organization": "ZaroPGx",
        "disclaimer": "",
        "genome_build": "GRCh38.p14",
        "named_allele_matcher_version": "2.0.0",
        "pharmcat_data_version": "2025-11-05-00-25",
        "execsum_from_tsv": [],
        "called_genes": rows,
        "missing_positions_not_assumed": False,
    }
    return {name: _text(env.get_template(name).render(**context)) for name in TEMPLATES}


def _rows_with_a_failed_caller():
    failed = pypgx_failed_genes(
        {
            "CYP2D6": {"success": False, "error": "Traceback ... MemoryError"},
            "CYP2C19": {"success": True, "diplotype": "*1/*2", "details": {}},
        }
    )
    rows = [
        {"gene": "CYP2D6", "diplotype": "", "phenotype": "", "called_by": "P"},
        {"gene": "CYP3A5", "diplotype": "", "phenotype": "", "called_by": "M"},
    ]
    mark_not_tested_and_failed(
        rows, report_dir="/nonexistent", failed=failed, pypgx_ran=True
    )
    return rows


def test_the_wrapper_s_failure_reaches_the_row():
    rows = _rows_with_a_failed_caller()
    by_gene = {row["gene"]: row for row in rows}

    assert by_gene["CYP2D6"]["call_error"].startswith("PyPGx failed on this gene")
    assert "call_error" not in by_gene["CYP3A5"] or not by_gene["CYP3A5"]["call_error"]


@pytest.mark.parametrize("template", TEMPLATES)
def test_a_failed_gene_is_not_blamed_on_the_sample(template):
    body = _render(_rows_with_a_failed_caller())[template]

    said = re.search(r"Not called from this sample's data \(\d+\):[^.]*", body)
    if said:
        assert "CYP2D6" not in said.group(0), (
            "the gene whose tool crashed is listed as one this sample's data could not "
            "answer: " + said.group(0)[:200]
        )


@pytest.mark.parametrize("template", TEMPLATES)
def test_the_page_names_the_failure_and_its_reason(template):
    body = _render(_rows_with_a_failed_caller())[template]

    assert "CYP2D6" in body
    assert (
        "PyPGx failed on this gene" in body
    ), "the reason reached FHIR and not the page"
    assert "tool failed" in body


@pytest.mark.parametrize("template", TEMPLATES)
def test_a_run_with_no_failure_says_nothing_about_one(template):
    """The paragraph is conditional: a clean run must not carry an empty heading, and the
    genes the data could not answer must still be named as such."""
    rows = [
        {"gene": "CYP2D6", "diplotype": "", "phenotype": "", "called_by": "P"},
        {"gene": "CYP3A5", "diplotype": "", "phenotype": "", "called_by": "M"},
    ]
    mark_not_tested_and_failed(
        rows, report_dir="/nonexistent", failed={}, pypgx_ran=True
    )

    body = _render(rows)[template]

    assert "tool failed" not in body
    assert "PyPGx failed on this gene" not in body
    said = re.search(r"Not called from this sample's data \(\d+\):[^.]*", body)
    if said:
        assert "CYP2D6" in said.group(0)


def _rows_with_a_tool_that_never_ran():
    """OptiType enabled, its result file never written: the case
    mark_not_tested_and_failed's own docstring describes for HLA-A and HLA-B."""
    rows = [
        {"gene": "HLA-A", "diplotype": "", "phenotype": "", "called_by": "?"},
        {"gene": "CYP3A5", "diplotype": "", "phenotype": "", "called_by": "M"},
    ]
    mark_not_tested_and_failed(
        rows, report_dir="/nonexistent", failed={}, pypgx_ran=True
    )
    return rows


@pytest.mark.parametrize("template", TEMPLATES)
def test_a_gene_no_tool_reported_on_is_not_blamed_on_the_sample(template):
    """The second marker, and the ordinary case rather than the rare one: after a VCF
    run nothing types HLA-A, and the page said this sample's data could not answer it.
    The FHIR export codes these `not-performed`."""
    rows = _rows_with_a_tool_that_never_ran()
    assert {r["gene"]: r.get("not_tested") for r in rows}["HLA-A"] is True

    body = _render(rows)[template]

    said = re.search(r"Not called from this sample's data \(\d+\):[^.]*", body)
    if said:
        assert "HLA-A" not in said.group(0), said.group(0)[:200]
    assert "not examined" in body.lower()
    assert "HLA-A" in body


@pytest.mark.parametrize("template", TEMPLATES)
def test_the_three_reasons_are_told_apart_at_once(template):
    """A run with all three: one gene examined and unanswerable, one nothing reported
    on, one whose tool crashed. Each belongs to exactly one list."""
    failed = pypgx_failed_genes({"CYP2D6": {"success": False, "error": "MemoryError"}})
    rows = [
        {"gene": "HLA-A", "diplotype": "", "phenotype": "", "called_by": "?"},
        {"gene": "CYP2D6", "diplotype": "", "phenotype": "", "called_by": "P"},
        {"gene": "CYP3A5", "diplotype": "", "phenotype": "", "called_by": "M"},
    ]
    mark_not_tested_and_failed(
        rows, report_dir="/nonexistent", failed=failed, pypgx_ran=True
    )

    body = _render(rows)[template]

    said = re.search(r"Not called from this sample's data \(\d+\):[^.]*", body)
    if said:
        assert "HLA-A" not in said.group(0)
        assert "CYP2D6" not in said.group(0)
    assert "not examined" in body.lower()
    assert "tool failed" in body
    assert "PyPGx failed on this gene" in body


def test_the_summary_rows_carry_the_marker():
    """The print report's sample-data line is built from summary_rows, which rebuilds
    each row; dropping the marker there put the gene straight back in the wrong list --
    the same shape as the Tier 1 gate's marker."""
    rows = _rows_with_a_failed_caller()

    summary = summary_rows([], rows)
    by_gene = {row["gene"]: row for row in summary}

    assert by_gene["CYP2D6"]["call_error"].startswith("PyPGx failed on this gene")
    assert not by_gene["CYP3A5"]["call_error"]


def test_the_summary_rows_carry_the_other_marker_too():
    """not_tested for the same reason as call_error: the print report's sample-data
    line is built from these rebuilt rows."""
    summary = summary_rows([], _rows_with_a_tool_that_never_ran())
    by_gene = {row["gene"]: row for row in summary}

    assert by_gene["HLA-A"]["not_tested"] is True
    assert not by_gene["CYP3A5"]["not_tested"]
