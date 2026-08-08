"""An activity score of 0 must reach the page in every rendering lane.

0 is not "no score". It is the Poor Metabolizer end of the CPIC activity-score
scale -- the single most clinically consequential value the field ever holds --
and it is the one value a truthiness test silently deletes. ``0``, ``0.0``,
``Decimal("0.0000")`` and ``"0"`` are all falsy or falsy-adjacent in Jinja, so

    {{ d.activity_score if d.activity_score else '' }}

rendered an empty Activity Score cell for exactly the patients whose score
matters most. A recent fix made ``Decimal("0.0000")`` survive the database lane;
the templates then threw it away at the last step.

The Executive Summary tables already decided presence numerically
(``activity_score_num`` + ``is not none``). These tests pin that every remaining
lane agrees:

* ``report_template.html`` full diplotype table (HTML + WeasyPrint PDF)
* ``interactive_report.html`` Genetic Results table
* ``interactive_report.html`` ``data-activity-score`` payload
* ``pdf_generators._diplotype_line`` (the ReportLab fallback PDF)

and that the two *upstream* assembly steps in ``generator.py`` do not blank the
score before any template can see it -- fixing only the render lanes would leave
the PyPGx paths still handing them ``None``.

Every rendering assertion is on rendered output, never on template source.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal

import pytest

TEMPLATES = ["report_template.html", "interactive_report.html"]

# The spellings the DB lane, the JSON lane and the TSV lane actually produce for
# a zero score, plus the int form for completeness.
ZERO_FORMS = [0, 0.0, Decimal("0.0000"), Decimal("0"), "0", "0.0", "0.0000"]

# Values that genuinely mean "no score" and must keep rendering blank.
ABSENT_FORMS = [None, "", "   ", "N/A", "n/a", "Unknown", "-", "None"]


def _render(template_name, diplotypes):
    """Render through the app's own Jinja env so custom filters are registered."""
    from app.reports.generator import env

    return env.get_template(template_name).render(
        diplotypes=diplotypes,
        recommendations=[],
        gene_drug_recommendations=[],
        organized_recommendations=[],
        patient_id="test-patient",
        report_id="test-report",
        report_date="2026-08-08",
        organization="ZaroPGx",
        disclaimer="",
    )


def _gene_row(html, gene):
    """The <td> texts of the gene table row for *gene*, from rendered output."""
    rows = re.findall(r"<tr>(.*?)</tr>", html, re.S | re.I)
    for row in rows:
        cells = [
            re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c)).strip()
            for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S | re.I)
        ]
        # The full diplotype table is the 7-column one; the Exec Summary is 4.
        if len(cells) == 7 and cells[0] == gene:
            return cells
    return None


def _activity_column_index(html):
    """Locate the Activity Score column by its header, not by position."""
    thead = re.search(r"<thead>\s*<tr>(.*?)</tr>\s*</thead>", html, re.S | re.I)
    assert thead, "no gene table <thead> in rendered output"
    headers = [
        re.sub(r"<[^>]+>", "", h).strip()
        for h in re.findall(r"<th[^>]*>(.*?)</th>", thead.group(1), re.S | re.I)
    ]
    assert "Activity Score" in headers, headers
    return headers.index("Activity Score")


def _activity_cell(html, gene):
    cells = _gene_row(html, gene)
    assert cells is not None, f"no 7-column gene row for {gene} in rendered output"
    return cells[_activity_column_index(html)]


def _diplotype(score):
    return {
        "gene": "CYP2D6",
        "diplotype": "*4/*4",
        "phenotype": "Poor Metabolizer",
        "activity_score": score,
    }


# ---------------------------------------------------------------------------
# HTML lanes: the full diplotype table in both templates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("template_name", TEMPLATES)
@pytest.mark.parametrize("score", ZERO_FORMS)
def test_zero_activity_score_renders_in_the_gene_table(template_name, score):
    html = _render(template_name, [_diplotype(score)])
    cell = _activity_cell(html, "CYP2D6")
    assert cell != "", (
        f"{template_name}: a Poor Metabolizer's activity score of {score!r} "
        "rendered as an empty cell"
    )
    assert float(cell) == 0.0, cell


@pytest.mark.parametrize("template_name", TEMPLATES)
@pytest.mark.parametrize("score", ABSENT_FORMS)
def test_genuinely_absent_scores_still_render_blank(template_name, score):
    html = _render(template_name, [_diplotype(score)])
    assert (
        _activity_cell(html, "CYP2D6") == ""
    ), f"{template_name}: {score!r} is not a score and must not be printed"


@pytest.mark.parametrize("template_name", TEMPLATES)
def test_nonzero_scores_keep_their_stored_precision(template_name):
    """The fix changes the presence test, not the printed value."""
    for raw, expected in ((Decimal("1.0000"), "1.0000"), (1.5, "1.5"), ("2", "2")):
        html = _render(template_name, [_diplotype(raw)])
        assert _activity_cell(html, "CYP2D6") == expected


@pytest.mark.parametrize("template_name", TEMPLATES)
def test_zero_and_absent_are_distinguishable_on_one_page(template_name):
    """Two genes, one scored 0 and one unscored, must not look identical."""
    html = _render(
        template_name,
        [
            {
                "gene": "CYP2D6",
                "diplotype": "*4/*4",
                "phenotype": "Poor Metabolizer",
                "activity_score": Decimal("0.0000"),
            },
            {
                "gene": "CYP2C19",
                "diplotype": "*1/*1",
                "phenotype": "Normal Metabolizer",
                "activity_score": None,
            },
        ],
    )
    assert float(_activity_cell(html, "CYP2D6")) == 0.0
    assert _activity_cell(html, "CYP2C19") == ""


# ---------------------------------------------------------------------------
# The interactive report's machine-readable payload
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("score", ZERO_FORMS)
def test_zero_survives_the_interactive_data_attribute(score):
    html = _render("interactive_report.html", [_diplotype(score)])
    match = re.search(r'data-activity-score="([^"]*)"', html)
    assert match, "no data-activity-score attribute rendered"
    assert match.group(1) != "", f"{score!r} serialised as an empty attribute"
    assert float(match.group(1)) == 0.0


def test_absent_score_serialises_empty_not_none():
    html = _render("interactive_report.html", [_diplotype(None)])
    match = re.search(r'data-activity-score="([^"]*)"', html)
    assert match and match.group(1) == ""


# ---------------------------------------------------------------------------
# Upstream: the two PyPGx assembly steps inside generate_report
#
# Fixing only the render lanes would have been cosmetic for these paths -- the
# score was already None by the time any template ran.
# ---------------------------------------------------------------------------


def _run_generate_report(monkeypatch, tmp_path, data, genes=None):
    """Drive generate_report with every artifact writer off; return processed genes."""
    import app.reports.generator as generator_module

    for key in (
        "write_pdf",
        "write_html",
        "write_interactive_html",
        "write_json",
        "write_tsv",
        "write_workflow_svg",
        "write_workflow_png",
        "show_pharmcat_html_report",
        "show_pharmcat_json_report",
        "show_pharmcat_tsv_report",
    ):
        monkeypatch.setitem(generator_module.REPORT_CONFIG, key, False)

    payload = {"genes": genes or [], "drugRecommendations": []}
    payload.update(data)
    result = generator_module.generate_report(
        {"data": payload}, str(tmp_path), {"id": "p1"}, job_id=None
    )
    return {
        g["gene"]: g
        for g in result["processed_data"]["genes"]
        if isinstance(g, dict) and g.get("gene")
    }


def test_pypgx_only_gene_keeps_an_activity_score_of_zero(monkeypatch, tmp_path):
    """``details.get("a") or details.get("b")`` turned a PyPGx 0 into None."""
    (tmp_path / "s_pypgx_results.json").write_text(
        json.dumps(
            {
                "results": {
                    "CYP2D6": {
                        "success": True,
                        "diplotype": "*4/*4",
                        "details": {
                            "phenotype": "Poor Metabolizer",
                            "activity_score": 0.0,
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    genes = _run_generate_report(monkeypatch, tmp_path, {})
    assert genes["CYP2D6"]["activity_score"] == 0.0, (
        "the PyPGx merge blanked a Poor Metabolizer's activity score of 0 before "
        "any template could render it"
    )


def test_pypgx_only_gene_still_falls_back_to_the_camelcase_key(monkeypatch, tmp_path):
    """The `or` also served as a key fallback; that must survive the fix."""
    (tmp_path / "s_pypgx_results.json").write_text(
        json.dumps(
            {
                "results": {
                    "CYP2C19": {
                        "success": True,
                        "diplotype": "*1/*1",
                        "details": {"activityScore": 2.0},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    genes = _run_generate_report(monkeypatch, tmp_path, {})
    assert genes["CYP2C19"]["activity_score"] == 2.0


def _stage_pipeline_dir(tmp_path, gene):
    pipeline = tmp_path / "pypgx_run" / f"{gene}-pipeline"
    pipeline.mkdir(parents=True)
    return pipeline


def test_pipeline_merge_does_not_overwrite_a_stored_zero(monkeypatch, tmp_path):
    """``not target.get("activity_score")`` read a stored 0 as "missing"."""
    import app.reports.generator as generator_module

    _stage_pipeline_dir(tmp_path, "CYP2D6")
    monkeypatch.setattr(
        generator_module,
        "parse_gene_pipeline",
        lambda pdir, gene: {"gene": gene, "activity_score": "2.0", "evidence": {}},
    )

    genes = _run_generate_report(
        monkeypatch,
        tmp_path,
        {},
        genes=[
            {
                "gene": "CYP2D6",
                "diplotype": "*4/*4",
                "phenotype": "Poor Metabolizer",
                "activity_score": Decimal("0.0000"),
            }
        ],
    )
    assert float(genes["CYP2D6"]["activity_score"]) == 0.0, (
        "a stored activity score of 0 was treated as missing and overwritten by "
        "the PyPGx pipeline value"
    )


def test_pipeline_merge_still_fills_a_genuinely_missing_score(monkeypatch, tmp_path):
    """The other half: an absent score must still be filled, including with 0."""
    import app.reports.generator as generator_module

    _stage_pipeline_dir(tmp_path, "CYP2D6")
    monkeypatch.setattr(
        generator_module,
        "parse_gene_pipeline",
        lambda pdir, gene: {"gene": gene, "activity_score": "0.0", "evidence": {}},
    )

    genes = _run_generate_report(
        monkeypatch,
        tmp_path,
        {},
        genes=[
            {
                "gene": "CYP2D6",
                "diplotype": "*4/*4",
                "phenotype": "Poor Metabolizer",
                "activity_score": None,
            }
        ],
    )
    assert float(genes["CYP2D6"]["activity_score"]) == 0.0


# ---------------------------------------------------------------------------
# ReportLab fallback PDF: flowing text, not a table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("score", ZERO_FORMS)
def test_zero_activity_score_reaches_the_reportlab_line(score):
    from app.reports.pdf_generators import _diplotype_line

    line = _diplotype_line(_diplotype(score))
    assert (
        "Activity Score:" in line
    ), f"ReportLab lane dropped an activity score of {score!r}: {line!r}"
    printed = re.search(r"\(Activity Score: ([^)]*)\)", line).group(1)
    assert float(printed) == 0.0


@pytest.mark.parametrize("score", ABSENT_FORMS)
def test_reportlab_line_omits_genuinely_absent_scores(score):
    from app.reports.pdf_generators import _diplotype_line

    assert "Activity Score:" not in _diplotype_line(_diplotype(score))


def test_reportlab_line_omits_the_score_when_the_key_is_missing_entirely():
    from app.reports.pdf_generators import _diplotype_line

    line = _diplotype_line({"gene": "CYP2D6", "diplotype": "*4/*4"})
    assert "Activity Score:" not in line
    assert "CYP2D6" in line


# ---------------------------------------------------------------------------
# WeasyPrint PDF lane: it builds its own Jinja Environment
# ---------------------------------------------------------------------------


def _drive_weasyprint_lane(monkeypatch, tmp_path, template_data):
    """Run WeasyPrintGenerator.generate_pdf, capturing the HTML it hands the engine.

    WeasyPrint's native libraries are container-only, so the engine itself is
    stubbed; everything above it -- Environment construction, template compile,
    render, fallback handling -- is the real code path.
    """
    import app.reports.pdf_generators as pdf_generators_module

    captured = {}

    class _StubHTML:
        def __init__(self, filename):
            with open(filename, encoding="utf-8") as fh:
                captured["html"] = fh.read()

        def write_pdf(self, output_path, **kwargs):
            with open(output_path, "wb") as fh:
                fh.write(b"%PDF-1.4 stub")

    monkeypatch.setattr(pdf_generators_module, "_HAS_WEASYPRINT", True)
    monkeypatch.setattr(pdf_generators_module, "HTML", _StubHTML)
    monkeypatch.setattr(pdf_generators_module, "FontConfiguration", lambda: None)

    generator = pdf_generators_module.WeasyPrintGenerator()
    ok = generator.generate_pdf(template_data, str(tmp_path / "out.pdf"))
    assert ok, "WeasyPrint lane reported failure"
    return captured["html"]


def test_weasyprint_lane_renders_the_real_report_with_a_zero_score(
    monkeypatch, tmp_path
):
    """This lane builds its own Environment, and forgot to register the filter.

    Jinja resolves filters at *compile* time, so ``get_template`` raised
    ``TemplateAssertionError: No filter named 'activity_score_num'`` -- which the
    broad ``except Exception`` around the render swallowed, silently substituting
    a five-line stub template carrying no genes, no diplotypes and no
    recommendations at all. The PDF still generated; it was just empty.
    """
    html = _drive_weasyprint_lane(
        monkeypatch,
        tmp_path,
        {
            "patient_id": "test-patient",
            "report_id": "test-report",
            "diplotypes": [_diplotype(Decimal("0.0000"))],
            "recommendations": [],
        },
    )

    # Not the stub: the stub has no gene table at all.
    assert "Pharmacogenomic Report" in html
    assert float(_activity_cell(html, "CYP2D6")) == 0.0


def test_weasyprint_lane_does_not_fall_back_to_the_stub_template(monkeypatch, tmp_path):
    html = _drive_weasyprint_lane(
        monkeypatch,
        tmp_path,
        {
            "patient_id": "test-patient",
            "report_id": "test-report",
            "diplotypes": [_diplotype(Decimal("0.0000"))],
            "recommendations": [],
        },
    )
    # Markers that exist only in the real template.
    assert "Recommendation Strength Legend" in html
    assert "Source Legend:" in html
    # A marker that exists only in the stub fallback.
    assert "No analysis results available" not in html
