"""Withholding a verdict the data cannot support, and withholding nothing else.

The standard is AMP/CAP's minimum ("Tier 1") allele set per gene; see
tests/test_amp_tier1.py for the table and app/pharmcat/amp_tier1.py for the measurements
behind it. This module pins the edit: which rows the gate empties, which it must leave
exactly as they are, and that the guidance resting on a withheld gene cannot be left
standing.

The property that matters most is the NEGATIVE one. A gate that withholds too much is
not a safer gate -- it deletes results the data does support, and a report that declines
everything is one nobody can use. So the first test here runs it over the repo's own
PharmCAT fixtures and requires that nothing at all is withheld.
"""

import json
from pathlib import Path

import pytest

from app.pharmcat.report_json import iter_gene_blocks
from app.reports.tier1_gate import (
    mark_recommendations_without_a_call,
    withheld_alert,
    withhold_inadequate_calls,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = [
    "pharmcat.example.report.json",
    "pharmcat.unphased.example.report.json",
    "pharmcat.example.v340.report.json",
    "pharmcat.example.nested.v2.report.json",
]


def _rows_from(report):
    """The gene rows a report lane would build: the call PharmCAT printed, per gene."""
    rows = []
    for block in iter_gene_blocks(report.get("genes")):
        source = (block.gene_data or {}).get("sourceDiplotypes") or []
        label = (source[0].get("label") if source else "") or ""
        rows.append(
            {
                "gene": block.gene_symbol,
                "diplotype": label,
                "diplotype_display": label,
                "phenotype": "Normal Metabolizer",
            }
        )
    return rows


def _report(uncalled, shape="flat"):
    """A minimal report in PharmCAT's flat shape carrying one gene's uncalled list."""
    genes = {
        gene: {"geneSymbol": gene, "uncalledHaplotypes": list(names)}
        for gene, names in uncalled.items()
    }
    return {"genes": {"CPIC": genes} if shape == "nested" else genes}


# --------------------------------------------------------------------------
# The negative property, on the repo's own fixtures
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", FIXTURES)
def test_nothing_is_withheld_on_the_projects_own_pharmcat_reports(name):
    """These four carry 5 to 37 genes and real ``uncalledHaplotypes`` lists, and none of
    them is missing a Tier 1 allele. A gate that fires here is withholding results the
    data supports, which is its own kind of wrong answer."""
    report = json.loads((ROOT / "test_data" / name).read_text(encoding="utf-8"))
    rows = _rows_from(report)
    before = [dict(row) for row in rows]

    withheld = withhold_inadequate_calls(rows, report)

    assert withheld == [], f"{name}: withheld {withheld}"
    assert rows == before, "rows must be untouched, not merely un-withheld"


def test_a_report_with_no_uncalled_lists_is_left_alone():
    """An older report, or one that failed to parse as expected, costs its genes
    nothing: no evidence of inadequacy is not evidence of inadequacy."""
    rows = [{"gene": "CYP2C9", "diplotype": "*1/*1", "diplotype_display": "*1/*1"}]
    before = [dict(row) for row in rows]

    assert withhold_inadequate_calls(rows, {"genes": {"CYP2C9": {}}}) == []
    assert withhold_inadequate_calls(rows, {}) == []
    assert withhold_inadequate_calls(rows, None) == []
    assert rows == before


# --------------------------------------------------------------------------
# What it does withhold
# --------------------------------------------------------------------------


def test_a_call_missing_a_tier_1_allele_loses_its_verdict_and_gains_the_reason():
    """The measured case: CYP2C9 *1/*1 called from 2 of 88 positions, with *5, *6, *8
    and *11 unassessable."""
    report = _report({"CYP2C9": ["*5", "*6", "*8", "*11", "*12"]})
    rows = [
        {
            "gene": "CYP2C9",
            "diplotype": "*1/*1",
            "diplotype_display": "*1/*1",
            "phenotype": "Normal Metabolizer",
            "activity_score": "2.0",
        }
    ]

    assert withhold_inadequate_calls(rows, report) == ["CYP2C9"]

    row = rows[0]
    assert row["diplotype"] == ""
    assert row["diplotype_display"] == ""
    # The phenotype goes with it: a phenotype with no genotype under it is exactly the
    # confident-looking output this gate exists to remove.
    assert row["phenotype"] == ""
    assert row["activity_score"] == ""
    assert "*5, *6, *8 and *11" in row["no_call_note"]
    assert row["tier1_withheld"]["missing"] == ["*5", "*6", "*8", "*11"]
    assert "J Mol Diagn" in row["tier1_withheld"]["citation"]


def test_a_variant_call_is_withheld_too_when_a_tier_1_allele_is_unassessable():
    """CYP2C19 *2/*2 from 2 of 35 positions. The *2 was genuinely observed, but *3 was
    not assessable and a *2/*3 would print identically -- so the DIPLOTYPE is not
    supported even though one of its alleles is."""
    report = _report({"CYP2C19": ["*3", "*4A", "*35"]})
    rows = [{"gene": "CYP2C19", "diplotype": "*2/*2", "diplotype_display": "*2/*2"}]

    assert withhold_inadequate_calls(rows, report) == ["CYP2C19"]
    assert rows[0]["diplotype"] == ""
    assert "*3" in rows[0]["no_call_note"]


def test_it_reads_the_nested_report_shape_too():
    """PharmCAT 3.x nests genes under the guideline source and 2.x does not. Reading
    only the flat shape would make the gate silently stop firing on one of them."""
    report = _report({"CYP2C9": ["*5"]}, shape="nested")
    rows = [{"gene": "CYP2C9", "diplotype": "*1/*1", "diplotype_display": "*1/*1"}]

    assert withhold_inadequate_calls(rows, report) == ["CYP2C9"]


# --------------------------------------------------------------------------
# What it must not touch
# --------------------------------------------------------------------------


@pytest.mark.parametrize("call", ["Unknown/Unknown", "Unknown", "", "n/a", "-"])
def test_a_gene_pharmcat_already_declined_keeps_its_own_reason(call):
    """PharmCAT writes ``Unknown/Unknown`` for a gene it had nothing for, and that gene
    already carries a specific reason. Overwriting it with this general one would
    replace information with less information -- and on the measured array file that is
    most of the panel."""
    report = _report({"TPMT": ["*2", "*3A", "*3B", "*3C"]})
    rows = [
        {
            "gene": "TPMT",
            "diplotype": call,
            "diplotype_display": call,
            "no_call_note": "PharmCAT's own reason",
        }
    ]

    assert withhold_inadequate_calls(rows, report) == []
    assert rows[0]["no_call_note"] == "PharmCAT's own reason"
    assert "tier1_withheld" not in rows[0]


def test_a_gene_with_no_published_minimum_set_is_never_withheld():
    """SLCO1B1 has no AMP minimum set, so there is nothing to fall short of. Withholding
    it would be this module inventing a clinical threshold."""
    report = _report({"SLCO1B1": ["*5", "*15", "*20"]})
    rows = [{"gene": "SLCO1B1", "diplotype": "*1/*1", "diplotype_display": "*1/*1"}]

    assert withhold_inadequate_calls(rows, report) == []
    assert rows[0]["diplotype"] == "*1/*1"


def test_only_tier_2_alleles_unassessable_keeps_the_verdict():
    """The distinction the table exists for: these are real gaps, and AMP says a
    clinical assay need not cover them."""
    report = _report({"CYP2C9": ["*12", "*13", "*15", "*45"]})
    rows = [{"gene": "CYP2C9", "diplotype": "*1/*1", "diplotype_display": "*1/*1"}]

    assert withhold_inadequate_calls(rows, report) == []
    assert rows[0]["diplotype"] == "*1/*1"


def test_a_row_that_is_not_a_dict_is_skipped():
    report = _report({"CYP2C9": ["*5"]})
    rows = ["CYP2C9", None, 7, {"gene": "CYP2C9", "diplotype_display": "*1/*1"}]

    assert withhold_inadequate_calls(rows, report) == ["CYP2C9"]


# --------------------------------------------------------------------------
# The guidance that rested on it
# --------------------------------------------------------------------------


def test_guidance_resting_only_on_withheld_genes_is_marked():
    recs = [
        {"drug": "celecoxib", "recommendation_genes": ["CYP2C9"]},
        {"drug": "clopidogrel", "recommendation_genes": ["CYP2C19"]},
    ]

    assert mark_recommendations_without_a_call(recs, ["CYP2C9"]) == 1
    assert recs[0]["tier1_no_call"] == ["CYP2C9"]
    assert "tier1_no_call" not in recs[1]


def test_guidance_with_one_gene_still_called_is_left_alone():
    """CPIC's warfarin guidance reads CYP2C9 and VKORC1 together. Losing CYP2C9 does not
    make the VKORC1 half untrue, and blanking the row would discard it."""
    recs = [{"drug": "warfarin", "recommendation_genes": ["CYP2C9", "VKORC1"]}]

    assert mark_recommendations_without_a_call(recs, ["CYP2C9"]) == 0
    assert "tier1_no_call" not in recs[0]


def test_the_guidance_text_is_never_rewritten():
    """PharmCAT's words stay PharmCAT's: the annotation sits beside the row."""
    recs = [{"drug": "celecoxib", "gene": "CYP2C9", "recommendation": "Upstream text."}]

    mark_recommendations_without_a_call(recs, ["CYP2C9"])

    assert recs[0]["recommendation"] == "Upstream text."


@pytest.mark.parametrize("withheld", [None, [], ["", None]])
def test_nothing_is_marked_when_nothing_was_withheld(withheld):
    recs = [{"drug": "celecoxib", "recommendation_genes": ["CYP2C9"]}]

    assert mark_recommendations_without_a_call(recs, withheld) == 0
    assert "tier1_no_call" not in recs[0]


def test_a_single_gene_string_is_read_as_one_gene():
    """``genes`` carries a bare string on one of the report lanes (the "backward
    compatibility" field in map_recommendations_for_template)."""
    recs = [{"drug": "celecoxib", "genes": "CYP2C9"}]

    assert mark_recommendations_without_a_call(recs, ["CYP2C9"]) == 1


# --------------------------------------------------------------------------
# The rendered page, in both report lanes
# --------------------------------------------------------------------------

TEMPLATES = ("report_template.html", "interactive_report.html")


def _render(template_name, **overrides):
    from app.reports.generator import env

    context = {
        "diplotypes": [],
        "recommendations": [],
        "gene_drug_recommendations": [],
        "organized_recommendations": [],
        "patient_id": "test-patient",
        "report_id": "test-report",
        "report_date": "2026-11-12",
        "organization": "ZaroPGx",
        "disclaimer": "",
        "genome_build": "GRCh38.p14",
        "named_allele_matcher_version": "2.0.0",
        "pharmcat_data_version": "2025-11-05-00-25",
    }
    context.update(overrides)
    return env.get_template(template_name).render(**context)


def _text(markup):
    import re

    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", markup))


def _withheld_rows():
    """The measured case, put through the gate: CYP2C9 called from 2 of 88 positions,
    beside a gene the data genuinely determined."""
    report = _report({"CYP2C9": ["*5", "*6", "*8", "*11"]})
    rows = [
        {
            "gene": "CYP2C9",
            "diplotype": "*1/*1",
            "diplotype_display": "*1/*1",
            "phenotype": "Normal Metabolizer",
            "called_by": "PharmCAT",
        },
        {
            "gene": "VKORC1",
            "diplotype": "rs9923231 variant (T)/rs9923231 variant (T)",
            "diplotype_display": "rs9923231 variant (T)/rs9923231 variant (T)",
            "phenotype": "",
            "called_by": "PharmCAT",
        },
    ]
    withheld = withhold_inadequate_calls(rows, report)
    assert withheld == ["CYP2C9"]
    return rows


@pytest.mark.parametrize("template_name", TEMPLATES)
def test_the_withheld_gene_reaches_the_page_without_its_verdict(template_name):
    """What the reader must NOT see is the point: no "*1/*1" and no "Normal
    Metabolizer" for a gene assessed at 2 of 88 positions."""
    rows = _withheld_rows()

    text = _text(_render(template_name, diplotypes=rows))

    assert "*1/*1" not in text, "the unsupported verdict must not be on the page"
    assert "Normal Metabolizer" not in text, "nor the phenotype that followed from it"
    # The gene is still named -- withheld is not hidden.
    assert "CYP2C9" in text
    # And the gene the data did determine is untouched.
    assert "rs9923231 variant (T)" in text


@pytest.mark.parametrize("template_name", TEMPLATES)
def test_the_page_says_which_alleles_could_not_be_assessed(template_name):
    rows = _withheld_rows()

    text = _text(_render(template_name, diplotypes=rows))

    assert "*5, *6, *8 and *11" in text, "named on the page, not just in the data"
    assert "AMP/CAP" in text


@pytest.mark.parametrize("template_name", TEMPLATES)
def test_the_alert_reaches_the_page_too(template_name):
    rows = _withheld_rows()
    alert = withheld_alert(rows)
    assert alert is not None

    text = _text(_render(template_name, diplotypes=rows, workflow_warnings=[alert]))

    assert "minimum set of alleles" in text
    assert "CYP2C9 (*5; *6; *8; *11)" in text


@pytest.mark.parametrize("template_name", TEMPLATES)
def test_an_ordinary_run_says_nothing_about_tier_1(template_name):
    """Negative control: a report where nothing was withheld must not mention any of
    this. A standing paragraph about a rule that did not fire is noise."""
    rows = [
        {
            "gene": "CYP2C9",
            "diplotype": "*1/*1",
            "diplotype_display": "*1/*1",
            "phenotype": "Normal Metabolizer",
            "called_by": "PharmCAT",
        }
    ]
    assert withhold_inadequate_calls(rows, _report({"CYP2C9": ["*12", "*45"]})) == []

    text = _text(_render(template_name, diplotypes=rows))

    assert "AMP/CAP" not in text
    assert "*1/*1" in text, "and the supported verdict is still shown"


# --------------------------------------------------------------------------
# A realistic sparse panel, measured through the real pipeline
# --------------------------------------------------------------------------

# MEASURED 2026-11-12. PharmCAT's own complete example VCF was run through the full
# `pharmcat_pipeline` twice: once as shipped, and once with the single row for rs7900194
# deleted -- a panel that does not interrogate that position. These are the
# uncalledHaplotypes PharmCAT reported for CYP2C9 in each run.
#
# rs7900194 defines CYP2C9*8, and this is AMP's own published example of the harm: a
# patient heterozygous for it, tested on an assay without it, "may be assigned a
# genotype of *1/*1 (normal function) rather than *1/*8 ... potentially creating a
# health care disparity." Deleting one row from a complete file reproduces it exactly.
PANEL_COMPLETE_CYP2C9 = ["*86", "*87", "*88", "*89", "*90", "*91", "*94"]
PANEL_WITHOUT_RS7900194_CYP2C9 = [
    "*27",
    "*8",
    "*86",
    "*87",
    "*88",
    "*89",
    "*90",
    "*91",
    "*94",
]


def test_a_complete_input_keeps_cyp2c9_despite_seven_unassessable_alleles():
    """The negative half, on real pipeline output. Those seven are rare alleles no VCF
    can assess, and none is Tier 1 -- so the verdict stands."""
    rows = [{"gene": "CYP2C9", "diplotype": "*1/*1", "diplotype_display": "*1/*1"}]

    withheld = withhold_inadequate_calls(
        rows, _report({"CYP2C9": PANEL_COMPLETE_CYP2C9})
    )

    assert withheld == []
    assert rows[0]["diplotype"] == "*1/*1"


def test_a_panel_missing_one_tier_1_position_loses_the_verdict():
    """The positive half, from the same file with one row deleted. The only difference
    between this and the test above is rs7900194, and it is the difference between
    reporting "Normal Metabolizer" to a possible *1/*8 patient and declining."""
    rows = [
        {
            "gene": "CYP2C9",
            "diplotype": "*1/*1",
            "diplotype_display": "*1/*1",
            "phenotype": "Normal Metabolizer",
        }
    ]

    withheld = withhold_inadequate_calls(
        rows, _report({"CYP2C9": PANEL_WITHOUT_RS7900194_CYP2C9})
    )

    assert withheld == ["CYP2C9"]
    assert rows[0]["diplotype"] == ""
    assert rows[0]["phenotype"] == ""
    assert rows[0]["tier1_withheld"]["missing"] == [
        "*8"
    ], "only the Tier 1 gap, not *27"
    assert "*8" in rows[0]["no_call_note"]


def test_only_the_tier_1_allele_is_named_not_every_unassessable_one():
    """Nine alleles were unassessable in that run and only one of them is Tier 1.
    Listing all nine would bury the actionable fact in noise."""
    rows = [{"gene": "CYP2C9", "diplotype": "*1/*1", "diplotype_display": "*1/*1"}]
    withhold_inadequate_calls(rows, _report({"CYP2C9": PANEL_WITHOUT_RS7900194_CYP2C9}))

    note = rows[0]["no_call_note"]

    for rare in ("*86", "*87", "*94", "*27"):
        assert rare not in note, f"{rare} is not Tier 1 and must not be listed"
