"""Provenance resolver contract (BACKLOG 28 + 216).

The rule under test: report what the run recorded, and say so explicitly when
it recorded nothing. No arm may consult the gene name.
"""

import re
from pathlib import Path

import pytest

import app.reports.generator as generator_module
from app.reports.provenance import (
    CALLED_BY_NO_CALL,
    CALLED_BY_OUTSIDE,
    CALLED_BY_PHARMCAT,
    CALLED_BY_PYPGX,
    CALLED_BY_UNKNOWN,
    resolve_called_by,
    resolve_guideline_source,
)
from app.services.pharmcat_data_service import PharmCATDataService

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_matcher_is_pharmcat():
    prov = resolve_called_by({"call_source": "MATCHER"})
    assert prov.letter == CALLED_BY_PHARMCAT
    assert prov.recorded is True


def test_outside_without_tool_marker_is_explicitly_outside():
    prov = resolve_called_by({"call_source": "OUTSIDE"})
    assert prov.letter == CALLED_BY_OUTSIDE
    assert prov.recorded is True


def test_outside_with_tool_marker_names_the_tool():
    assert (
        resolve_called_by({"call_source": "OUTSIDE", "tool_source": "PyPGx"}).letter
        == CALLED_BY_PYPGX
    )
    assert (
        resolve_called_by({"call_source": "OUTSIDE", "source": "PyPGx"}).letter
        == CALLED_BY_PYPGX
    )


def test_enrichment_marker_never_overrides_matcher():
    # generator.py merge-2 stamps tool_source="PyPGx" onto genes PharmCAT called.
    prov = resolve_called_by({"call_source": "MATCHER", "tool_source": "PyPGx"})
    assert prov.letter == CALLED_BY_PHARMCAT


def test_none_is_no_call():
    prov = resolve_called_by({"call_source": "NONE"})
    assert prov.letter == CALLED_BY_NO_CALL
    assert prov.recorded is True


def test_tsv_outside_call_column():
    assert resolve_called_by({"outside_call": "no"}).letter == CALLED_BY_PHARMCAT
    assert resolve_called_by({"outside_call": "yes"}).letter == CALLED_BY_OUTSIDE
    assert (
        resolve_called_by({"outside_call": "yes", "tool_source": "PyPGx"}).letter
        == CALLED_BY_PYPGX
    )


def test_pypgx_only_gene_pharmcat_never_saw():
    prov = resolve_called_by(
        {"tool_source": "PyPGx", "pyPgxOnly": True, "diplotype": "*1/*4"}
    )
    assert prov.letter == CALLED_BY_PYPGX
    assert prov.recorded is True


def test_called_but_unrecorded_is_unknown_not_a_guess():
    prov = resolve_called_by({"diplotype": "*1/*1"})
    assert prov.letter == CALLED_BY_UNKNOWN
    assert prov.recorded is False


def test_empty_row_is_no_call():
    assert resolve_called_by({}).letter == CALLED_BY_NO_CALL


def test_gene_name_alone_never_produces_a_tool_letter():
    # Regression lock on the deleted determine_called_by heuristic.
    for gene in ("CYP2D6", "HLA-A", "HLA-B", "MT-RNR1", "CYP2C19"):
        assert resolve_called_by({"gene": gene}).letter == CALLED_BY_NO_CALL


def test_every_provenance_carries_a_label():
    for row in ({"call_source": "MATCHER"}, {"call_source": "OUTSIDE"}, {}):
        assert resolve_called_by(row).label.strip()


def test_guideline_source_letters():
    assert resolve_guideline_source({"phenotype_source": "DPWG"}) == "D"
    assert resolve_guideline_source({"guideline_source": "CPIC"}) == "C"
    assert resolve_guideline_source({"guideline_source": "FDA"}) == "F"
    assert resolve_guideline_source({"guideline_source": "PharmGKB"}) == "P"
    assert resolve_guideline_source({"guideline_source": "C"}) == "C"


def test_guideline_source_blank_when_not_recorded():
    assert resolve_guideline_source({}) == ""
    assert resolve_guideline_source({"phenotype_source": None}) == ""
    assert resolve_guideline_source({"guideline_source": "Whatever"}) == ""


# ---------------------------------------------------------------------------
# DB lane -- pharmcat_data_service reads the same resolver
# ---------------------------------------------------------------------------


def _transform(genes, diplotypes=None):
    service = PharmCATDataService.__new__(PharmCATDataService)
    return service._transform_genes_for_reports(genes, diplotypes or [])


def test_db_lane_outside_gene_is_outside_not_pharmcat():
    rows = _transform(
        [
            {
                "gene_symbol": "CYP2D6",
                "call_source": "OUTSIDE",
                "phenotype_source": "CPIC",
            }
        ]
    )
    assert rows[0]["called_by"] == CALLED_BY_OUTSIDE
    assert rows[0]["guideline_source"] == "C"


def test_db_lane_no_call_gene_is_no_call():
    rows = _transform([{"gene_symbol": "CYP2D6", "call_source": "NONE"}])
    assert rows[0]["called_by"] == CALLED_BY_NO_CALL


def test_db_lane_matcher_gene_is_pharmcat():
    rows = _transform([{"gene_symbol": "CYP2C19", "call_source": "MATCHER"}])
    assert rows[0]["called_by"] == CALLED_BY_PHARMCAT


def test_db_lane_never_emits_report_data_from():
    rows = _transform(
        [
            {"gene_symbol": "CYP2C19", "call_source": "MATCHER"},
            {"gene_symbol": "CYP2D6", "call_source": "OUTSIDE"},
        ]
    )
    assert all("report_data_from" not in row for row in rows)


def test_db_lane_legacy_row_with_a_call_is_unknown_not_no_call():
    """Rows parsed before 28+216 hold the guideline bucket in ``call_source``.

    That value records nothing about who called the gene, so the honest answer
    is ``?``. Answering ``-`` ("no call made") would assert something the run
    never said, for a gene that plainly has a diplotype -- the same class of
    fabrication this pass removes. The gene-summary row carries no diplotype of
    its own, so the caller must supply the one it is about to render.
    """
    rows = _transform(
        [{"gene_symbol": "CYP2C19", "call_source": "CPIC", "phenotype_source": "CPIC"}],
        [
            {
                "gene_symbol": "CYP2C19",
                "diplotype_label": "*38/*38",
                "phenotype": "Normal Metabolizer",
            }
        ],
    )
    assert rows[0]["diplotype"] == "*38/*38"
    assert rows[0]["called_by"] == CALLED_BY_UNKNOWN


def test_db_lane_row_without_a_diplotype_is_still_no_call():
    rows = _transform(
        [{"gene_symbol": "CYP2C19", "call_source": "CPIC", "phenotype_source": "CPIC"}]
    )
    assert rows[0]["called_by"] == CALLED_BY_NO_CALL


def test_db_lane_dedupe_prefers_cpic_on_phenotype_source():
    service = PharmCATDataService.__new__(PharmCATDataService)
    cpic = {
        "gene_symbol": "CYP2D6",
        "call_source": "OUTSIDE",
        "phenotype_source": "CPIC",
    }
    dpwg = {
        "gene_symbol": "CYP2D6",
        "call_source": "OUTSIDE",
        "phenotype_source": "DPWG",
    }
    assert service._is_better_gene_entry(cpic, dpwg) is True
    assert service._is_better_gene_entry(dpwg, cpic) is False


def test_db_lane_dedupe_still_prefers_a_recorded_bucket_over_none():
    """Both rows carry the same callSource now; the bucket is the only key."""
    service = PharmCATDataService.__new__(PharmCATDataService)
    bucketed = {"gene_symbol": "CYP2D6", "phenotype_source": "DPWG"}
    unbucketed = {"gene_symbol": "CYP2D6", "phenotype_source": None}
    assert service._is_better_gene_entry(bucketed, unbucketed) is True
    assert service._is_better_gene_entry(unbucketed, bucketed) is False


def test_db_lane_workflow_summary_uses_the_request_session():
    """A second engine/connection per request commits outside the request
    transaction; ``get_pharmcat_summary`` takes the session (see the call at
    ``_get_normalized_pharmcat_data``)."""
    src = REPO_ROOT.joinpath("app/services/pharmcat_data_service.py").read_text(
        encoding="utf-8"
    )
    assert "get_pharmcat_summary(pharmcat_run_id, self.db)" in src
    assert "get_pharmcat_summary(pharmcat_run_id)" not in src


# ---------------------------------------------------------------------------
# File/generator lane -- the four gene-name ladders are gone
# ---------------------------------------------------------------------------


def test_generator_no_longer_defines_the_gene_name_ladders():
    for dead in (
        "determine_called_by",
        "determine_report_data_from",
        "determine_tool_source",
        "determine_guideline_source",
    ):
        assert not hasattr(generator_module, dead), f"{dead} must be deleted"


def test_canonical_rows_report_recorded_provenance_only():
    rows = generator_module._build_canonical_diplotypes(
        raw_gene_entries=[
            {"gene": "CYP2C19", "diplotype": "*38/*38", "call_source": "MATCHER"},
            {"gene": "CYP2D6", "diplotype": "*1/*3", "call_source": "OUTSIDE"},
        ],
        file_type="vcf",
        workflow_config=None,
    )
    by_gene = {r["gene"]: r for r in rows}

    assert by_gene["CYP2C19"]["called_by"] == CALLED_BY_PHARMCAT
    # No gene-name guess: OUTSIDE with no tool marker stays honest.
    assert by_gene["CYP2D6"]["called_by"] == CALLED_BY_OUTSIDE
    assert all("report_data_from" not in r for r in rows)


def test_canonical_placeholder_rows_are_no_call_not_a_tool():
    rows = generator_module._build_canonical_diplotypes(
        raw_gene_entries=[],
        file_type="vcf",
        workflow_config=None,
    )
    assert rows, "canonical gene list should not be empty"
    assert {r["called_by"] for r in rows} == {CALLED_BY_NO_CALL}


def test_canonical_rows_blank_an_unrecorded_guideline_source():
    """The file lane used to render the word "CPIC" (straight from block.source)
    into a letter column, and to guess "C" whenever it guessed the caller."""
    rows = generator_module._build_canonical_diplotypes(
        raw_gene_entries=[
            {"gene": "CYP2C19", "diplotype": "*38/*38", "call_source": "MATCHER"},
            {
                "gene": "CYP2C9",
                "diplotype": "*1/*2",
                "call_source": "MATCHER",
                "guideline_source": "DPWG",
            },
        ],
        file_type="vcf",
        workflow_config=None,
    )
    by_gene = {r["gene"]: r for r in rows}
    assert by_gene["CYP2C19"].get("guideline_source", "") == ""
    assert by_gene["CYP2C9"]["guideline_source"] == "D"


def test_pypgx_only_row_keeps_its_tool_attribution():
    rows = generator_module._build_canonical_diplotypes(
        raw_gene_entries=[
            {
                "gene": "CYP2D6",
                "diplotype": "*1/*4",
                "tool_source": "PyPGx",
                "pyPgxOnly": True,
            }
        ],
        file_type="bam",
        workflow_config=None,
    )
    by_gene = {r["gene"]: r for r in rows}
    assert by_gene["CYP2D6"]["called_by"] == CALLED_BY_PYPGX


def test_both_pypgx_merges_write_the_same_tool_marker_key():
    """merge-1 wrote ``source``, merge-2 ``tool_source`` -- same fact, two keys."""
    src = REPO_ROOT.joinpath("app/reports/generator.py").read_text(encoding="utf-8")
    assert '"source": "PyPGx"' not in src
    assert src.count('"tool_source": "PyPGx"') >= 2


# ---------------------------------------------------------------------------
# Templates -- rendered markup, not template source text
# ---------------------------------------------------------------------------

TEMPLATES = ("report_template.html", "interactive_report.html")

_ROWS = [
    {
        "gene": "CYP2C19",
        "diplotype": "*38/*38",
        "phenotype": "Normal Metabolizer",
        "called_by": CALLED_BY_PHARMCAT,
        "called_by_label": "Called by PharmCAT",
        "guideline_source": "C",
        # A stale value from an upstream lane must never reach the page.
        "report_data_from": "ZZZ_SHOULD_NOT_RENDER",
    },
    {
        "gene": "CYP2D6",
        "diplotype": "*1/*3",
        "phenotype": "Intermediate Metabolizer",
        "called_by": CALLED_BY_OUTSIDE,
        "called_by_label": "Outside call - producing tool not recorded by this run",
    },
    {
        "gene": "ABCG2",
        "diplotype": "",
        "phenotype": "",
        "called_by": CALLED_BY_NO_CALL,
        "called_by_label": "No call made for this gene",
    },
    {
        "gene": "NAT2",
        "diplotype": "*1/*1",
        "phenotype": "Unknown",
        "called_by": CALLED_BY_UNKNOWN,
        "called_by_label": "Calling tool not recorded by this run",
    },
]


def _render(template_name, diplotypes=_ROWS):
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


def _gene_table_headers(html):
    """The <th> texts of the gene table, from the rendered page."""
    thead = re.search(
        r"<thead>\s*<tr>(.*?)</tr>\s*</thead>", html, re.S | re.I
    )  # first table on the page is the gene table in both templates
    assert thead, "no gene table <thead> found in rendered output"
    return [
        re.sub(r"<[^>]+>", "", h).strip()
        for h in re.findall(r"<th[^>]*>(.*?)</th>", thead.group(1), re.S | re.I)
    ]


@pytest.mark.parametrize("template_name", TEMPLATES)
def test_rendered_gene_table_has_seven_columns_and_no_data_column(template_name):
    headers = _gene_table_headers(_render(template_name))
    assert headers == [
        "Gene",
        "Diplotype",
        "Phenotype",
        "Activity Score",
        "Implications",
        "Call",
        "Guide",
    ], headers


@pytest.mark.parametrize("template_name", TEMPLATES)
def test_rendered_page_never_carries_report_data_from(template_name):
    html = _render(template_name)
    assert "ZZZ_SHOULD_NOT_RENDER" not in html
    assert "report_data_from" not in html


@pytest.mark.parametrize("template_name", TEMPLATES)
def test_templates_no_longer_reference_report_data_from(template_name):
    path = REPO_ROOT / "app" / "reports" / "templates" / template_name
    assert "report_data_from" not in path.read_text(encoding="utf-8"), path


@pytest.mark.parametrize("template_name", TEMPLATES)
def test_rendered_legend_explains_every_glyph(template_name):
    html = _render(template_name)
    assert "X</strong> = outside call" in html
    assert "?</strong> = not recorded by this run" in html
    assert "&ndash;</strong> = no call made" in html
    # The Data column is gone, so the legend must not describe it.
    assert "Data:</strong>" not in html
    assert "Report data tool" not in html
    # GATK never called a diplotype; grep 'return "G"' has zero hits in app/.
    assert "G</strong> = GATK" not in html
    assert "G = GATK" not in html


@pytest.mark.parametrize("template_name", TEMPLATES)
def test_rendered_call_cell_is_never_blank_and_self_explains(template_name):
    html = _render(template_name)
    for letter, label in (
        (CALLED_BY_PHARMCAT, "Called by PharmCAT"),
        (CALLED_BY_OUTSIDE, "Outside call - producing tool not recorded by this run"),
        (CALLED_BY_NO_CALL, "No call made for this gene"),
        (CALLED_BY_UNKNOWN, "Calling tool not recorded by this run"),
    ):
        cell = f'<td class="narrow-col tool-source" title="{label}">{letter}</td>'
        assert cell in html, f"missing Call cell for {letter!r}: {label}"


@pytest.mark.parametrize("template_name", TEMPLATES)
def test_rendered_guide_cell_is_blank_when_not_recorded(template_name):
    html = _render(template_name)
    # CYP2C19 recorded "C"; the other three recorded nothing.
    assert html.count('<td class="narrow-col">C</td>') == 1
    assert html.count('<td class="narrow-col"></td>') == 3


# ---------------------------------------------------------------------------
# ReportLab fallback lane -- previously dropped provenance entirely
# ---------------------------------------------------------------------------


def test_reportlab_gene_line_states_provenance_in_words():
    """This lane renders flowing text, not a table: it has no legend and no
    hover, so a bare glyph would be uninterpretable. Render the label."""
    from app.reports.pdf_generators import _diplotype_line

    line = _diplotype_line(
        {
            "gene": "CYP2C19",
            "diplotype": "*38/*38",
            "phenotype": "Normal Metabolizer",
            "call_source": "MATCHER",
        }
    )
    assert "<b>CYP2C19:</b> *38/*38" in line
    assert "(Phenotype: Normal Metabolizer)" in line
    assert "[Called by PharmCAT]" in line


def test_reportlab_gene_line_is_honest_about_outside_and_unknown():
    from app.reports.pdf_generators import _diplotype_line

    outside = _diplotype_line(
        {"gene": "CYP2D6", "diplotype": "*1/*3", "call_source": "OUTSIDE"}
    )
    assert "[Outside call - producing tool not recorded by this run]" in outside

    unrecorded = _diplotype_line({"gene": "NAT2", "diplotype": "*1/*1"})
    assert "[Calling tool not recorded by this run]" in unrecorded

    no_call = _diplotype_line({"gene": "ABCG2", "diplotype": ""})
    assert "[No call made for this gene]" in no_call


def test_reportlab_gene_line_preserves_the_existing_activity_score_format():
    from app.reports.pdf_generators import _diplotype_line

    line = _diplotype_line(
        {
            "gene": "CYP2D6",
            "diplotype": "*1/*3",
            "phenotype": "Intermediate Metabolizer",
            "activity_score": 1.0,
            "call_source": "OUTSIDE",
            "tool_source": "PyPGx",
        }
    )
    assert "(Activity Score: 1.0)" in line
    # OUTSIDE refined by a recorded tool marker names the tool.
    assert "[Called by PyPGx]" in line
    # Provenance goes last, after the existing fields.
    assert line.index("Activity Score") < line.index("[Called by PyPGx]")
