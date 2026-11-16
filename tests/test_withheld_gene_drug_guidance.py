"""Guidance that rests on a withheld call says so, beside the guidance.

The AMP/CAP Tier 1 gate exists because PharmCAT will report `CYP2C9 Normal Metabolizer,
Activity Score 2.0` from 2 of that gene's 88 positions. When it withholds a gene's
verdict, `tier1_gate.mark_recommendations_without_a_call` annotates every recommendation
whose genes were all withheld -- "for the template to render beside the row", as its
docstring puts it.

No template rendered it. So in exactly the case the gate is built for, the drug card
printed PharmCAT's advice and its strength grade unqualified -- "Alternative antiplatelet
therapy recommended / Strong" -- beside a gene the same report declined to call. The
Executive Summary and the gene table were honest about the gene; the drug section, which
is the part a prescriber acts on, was not.

PharmCAT's words are not rewritten. The line says what they rest on.
"""

from __future__ import annotations

import re

import pytest

from app.reports.generator import env
from app.reports.tier1_gate import mark_recommendations_without_a_call

TEMPLATES = ("report_template.html", "interactive_report.html")


def _text(markup: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", markup))


def _render(recs):
    context = {
        "diplotypes": [{"gene": "CYP2C19", "diplotype": "", "phenotype": ""}],
        "recommendations": recs,
        "gene_drug_recommendations": recs,
        "organized_recommendations": recs,
        "grouped_recommendations": {"clopidogrel": recs},
        "patient_id": "p",
        "report_id": "r",
        "report_date": "2026-11-14",
        "organization": "ZaroPGx",
        "disclaimer": "",
        "genome_build": "GRCh38.p14",
        "named_allele_matcher_version": "2.0.0",
        "pharmcat_data_version": "x",
        "execsum_from_tsv": [],
        "called_genes": [],
        "missing_positions_not_assumed": False,
    }
    return {name: env.get_template(name).render(**context) for name in TEMPLATES}


def _clopidogrel(genes=("CYP2C19",)):
    return [
        {
            "gene": genes[0],
            "recommendation_genes": list(genes),
            "drug": "clopidogrel",
            "recommendation": "Alternative antiplatelet therapy recommended",
            "classification": "Strong",
            "evidence_class": "evidence-strong",
            "guideline_source": "CPIC",
            "phenotype": "Poor Metabolizer",
        }
    ]


@pytest.mark.parametrize("template", TEMPLATES)
def test_the_card_says_its_guidance_has_no_genotype_under_it(template):
    recs = _clopidogrel()
    assert mark_recommendations_without_a_call(recs, ["CYP2C19"]) == 1

    body = _text(_render(recs)[template])

    assert "No genotype under this guidance" in body
    assert "CYP2C19 had its verdict withheld" in body
    # PharmCAT's own words are kept, not replaced
    assert "Alternative antiplatelet therapy recommended" in body


@pytest.mark.parametrize("template", TEMPLATES)
def test_it_is_said_before_the_advice_it_qualifies(template):
    """Above the Recommendation line, not in a footnote: a reader who stops at the
    advice has still read it."""
    recs = _clopidogrel()
    mark_recommendations_without_a_call(recs, ["CYP2C19"])

    body = _text(_render(recs)[template])

    assert body.index("No genotype under this guidance") < body.index(
        "Recommendation: Alternative antiplatelet"
    )


@pytest.mark.parametrize("template", TEMPLATES)
def test_a_card_with_a_call_behind_it_says_nothing(template):
    recs = _clopidogrel()

    body = _text(_render(recs)[template])

    assert "No genotype under this guidance" not in body
    assert "Alternative antiplatelet therapy recommended" in body


def test_guidance_on_several_genes_keeps_its_call_while_one_remains():
    """CPIC's warfarin guidance reads CYP2C9 and VKORC1 together: losing CYP2C9 does
    not make the VKORC1 half untrue, and the gate's own docstring says so. The line
    must not appear for such a card."""
    recs = _clopidogrel(genes=("CYP2C9", "VKORC1"))

    assert mark_recommendations_without_a_call(recs, ["CYP2C9"]) == 0
    assert "tier1_no_call" not in recs[0]

    for template in TEMPLATES:
        assert "No genotype under this guidance" not in _text(_render(recs)[template])


@pytest.mark.parametrize("template", TEMPLATES)
def test_two_withheld_genes_read_as_a_plural(template):
    recs = _clopidogrel(genes=("CYP2C19", "CYP2C9"))
    mark_recommendations_without_a_call(recs, ["CYP2C19", "CYP2C9"])

    body = _text(_render(recs)[template])

    assert "CYP2C19, CYP2C9 had their verdict withheld" in body


@pytest.mark.parametrize("template", TEMPLATES)
def test_a_card_with_no_recommendation_text_does_not_print_the_word_none(template):
    """CPIC's warfarin entry is a dosing algorithm rather than a sentence, so its
    annotation carries no recommendation text. The card printed "Recommendation: None"
    -- Jinja's rendering of the Python value -- on every report that carries warfarin
    guidance, which is every report in the 61-sample GeT-RM corpus.

    The card keeps its heading and its Source line, which is what there is to say. The
    strength line below it is already suppressed for the same card, which is
    unclassified."""
    recs = [
        {
            "gene": None,
            "recommendation_genes": ["CYP2C9", "CYP4F2", "VKORC1"],
            "drug": "warfarin",
            "recommendation": None,
            "classification": None,
            "evidence_class": "evidence-unclassified",
            "guideline_source": "Annotation of CPIC Guideline for warfarin",
        }
    ]

    rendered = _render(recs)[template]

    assert "<strong>Recommendation:</strong>" not in rendered
    assert "Recommendation: None" not in _text(rendered)
    assert "Annotation of CPIC Guideline for warfarin" in _text(rendered)


@pytest.mark.parametrize("template", TEMPLATES)
def test_an_empty_string_is_treated_the_same_as_none(template):
    """Asserted on the markup, not the text: "Recommendation:" also matches the
    "Strength of Recommendation:" line, which is a different field and prints
    legitimately."""
    recs = _clopidogrel()
    recs[0]["recommendation"] = ""

    markup = _render(recs)[template]

    assert "<strong>Recommendation:</strong>" not in markup
    assert "Strength of Recommendation:" in markup


@pytest.mark.parametrize("template", TEMPLATES)
def test_a_card_with_text_still_prints_it(template):
    body = _text(_render(_clopidogrel())[template])

    assert "Recommendation: Alternative antiplatelet therapy recommended" in body
