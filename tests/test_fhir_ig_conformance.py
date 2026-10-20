"""The FHIR bundle meets the HL7 Genomics Reporting IG (3.0.0) profiles it claims.

Validated against HAPI FHIR 8.12's ``$validate`` with the IG loaded and
tx.fhir.org for LOINC and SNOMED CT (2026-10-16), the bundle of a 30x NA12878 job
had 731 errors. Each test below pins one of the fixes, so the export cannot drift
back into what the validator rejected:

- genotype and therapeutic-implication observations need both the "laboratory"
  and the Genetics (v2-0074 GE) category (464 errors);
- a medication-recommendation Task's code is bound (required) to LOINC LL4049-4,
  which has no "Recommendation for <drug>" (265 errors);
- the genomic-study Procedure's category is fixed to "laboratory" (2 errors).

and the warnings that pointed at wrong codes: tbd codes the IG does not define, a
diplotype sent as a PharmVar code, genes under a made-up HGNC system, RxNorm
codings without a code, the IG's display for conclusion-string.

The clinical content (genes, diplotypes, phenotypes, activity scores, alleles,
drugs, recommendations, strengths of recommendation, guideline citations) is unchanged; the
last test checks it survives.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from app.services.fhir import terminology
from app.services.fhir_export_service import FHIRExportService

GR = "http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/"

# CodeSystem tbd-codes-cs of the IG, version 3.0.0, content "complete": every code
# and its display.
IG_TBD_CODES = {
    "associated-therapy": "Associated Therapy",
    "molecular-consequence": "Molecular Consequence",
    "feature-consequence": "Feature Consequence",
    "diagnostic-implication": "Diagnostic Implication",
    "therapeutic-implication": "Therapeutic Implication",
    "functional-effect": "Functional Effect",
    "conclusion-string": "Conclusion Text",
    "condition-inheritance": "Condition Inheritance",
    "variant-confidence-status": "Variant Confidence Status",
    "repeat-motif": "Repeat Expansion Motif",
    "repeat-number": "Number of Repeat Expansions",
    "biomarker-category": "A characterization of a given biomarker observation.",
    "protein-ref-seq": "An identifier for the protein reference sequence.",
}

PHARMCAT_DATA: Dict[str, Any] = {
    "pharmcat_version": "3.4.0",
    "total_genes": 3,
    "actionable_findings": [{"gene": "CYP2D6"}],
    "genes": [
        {
            "gene": "CYP2D6",
            "diplotype": "*1/*4",
            "phenotype": "Intermediate Metabolizer",
            "activity_score": "1.0",
            "allele1": "*1",
            "allele2": "*4",
        },
        {
            "gene": "ABCG2",
            "diplotype": "rs2231142 reference (G)/rs2231142 reference (G)",
            "phenotype": "Normal Function",
        },
        # Not in HGNC_IDS, with an activity score LOINC has no code for.
        {
            "gene": "NOVEL1",
            "diplotype": "*1/*1",
            "phenotype": "Normal Metabolizer",
            "activity_score": 2,
        },
    ],
    "drugRecommendations": [
        {
            "drug": "codeine",
            "genes": ["CYP2D6"],
            "recommendations": [
                {
                    "recommendation": "Use label recommended dosing.",
                    "classification": "Moderate",
                    "guideline_source": "CPIC",
                },
                {
                    "recommendation": "Be alert to ineffectiveness.",
                    "classification": "",
                    "guideline_source": "DPWG",
                },
            ],
        }
    ],
}


@pytest.fixture
def bundle() -> Dict[str, Any]:
    service = FHIRExportService(MagicMock())
    result = service.export_pgx_report(
        run_id="run-1",
        patient_info={"id": "patient-1"},
        pharmcat_data=PHARMCAT_DATA,
        output_format="json",
    )
    assert result["success"], result
    return json.loads(result["content"])


def _resources(bundle, profile: str) -> List[Dict[str, Any]]:
    return [
        e["resource"]
        for e in bundle["entry"]
        if e["resource"].get("meta", {}).get("profile") == [GR + profile]
    ]


def _codings(node, found):
    if isinstance(node, dict):
        if "system" in node and ("code" in node or "display" in node):
            found.append(node)
        for value in node.values():
            _codings(value, found)
    elif isinstance(node, list):
        for value in node:
            _codings(value, found)
    return found


def _component(resource, code):
    return [
        c
        for c in resource.get("component", [])
        if any(x.get("code") == code for x in c["code"].get("coding", []))
    ]


def test_genomic_observations_carry_both_required_categories(bundle):
    observations = _resources(bundle, "genotype") + _resources(
        bundle, "therapeutic-implication"
    )
    # 3 genotypes, their 3 phenotypes, 1 drug.
    assert len(observations) == 7
    for observation in observations:
        pairs = [
            (c["coding"][0]["system"], c["coding"][0]["code"])
            for c in observation["category"]
        ]
        assert pairs == [
            (
                "http://terminology.hl7.org/CodeSystem/observation-category",
                "laboratory",
            ),
            ("http://terminology.hl7.org/CodeSystem/v2-0074", "GE"),
        ]


def test_every_coding_has_a_code(bundle):
    """An RxNorm coding with a display and no code stood in for every drug."""
    for coding in _codings(bundle, []):
        assert coding.get("code"), coding


def test_only_tbd_codes_the_ig_defines_with_its_display(bundle):
    tbd = [
        c for c in _codings(bundle, []) if c["system"] == terminology.GENOMICS_TBD_CODES
    ]
    assert tbd
    for coding in tbd:
        assert IG_TBD_CODES.get(coding["code"]) == coding["display"], coding


def test_genes_are_hgnc_ids_and_an_unknown_gene_is_text(bundle):
    genes = {}
    for observation in _resources(bundle, "genotype"):
        (gene,) = _component(observation, "48018-6")
        genes[gene["valueCodeableConcept"]["text"]] = gene["valueCodeableConcept"]
    assert genes["CYP2D6"]["coding"] == [
        {"system": "http://www.genenames.org", "code": "HGNC:2625", "display": "CYP2D6"}
    ]
    assert genes["ABCG2"]["coding"][0]["code"] == "HGNC:74"
    assert genes["NOVEL1"] == {"text": "NOVEL1"}


def test_the_diplotype_is_text_not_a_pharmvar_code(bundle):
    values = [o["valueCodeableConcept"] for o in _resources(bundle, "genotype")]
    assert values == [
        {"text": "*1/*4"},
        {"text": "rs2231142 reference (G)/rs2231142 reference (G)"},
        {"text": "*1/*1"},
    ]


def test_activity_score_is_coded_where_loinc_has_a_code(bundle):
    by_gene = {}
    for observation in _resources(bundle, "genotype"):
        (gene,) = _component(observation, "48018-6")
        scores = [c for c in observation["component"] if "valueQuantity" in c]
        by_gene[gene["valueCodeableConcept"]["text"]] = scores
    (cyp2d6,) = by_gene["CYP2D6"]
    assert cyp2d6["code"]["coding"][0]["code"] == "104669-7"
    assert cyp2d6["valueQuantity"]["value"] == 1.0
    (novel,) = by_gene["NOVEL1"]
    assert novel["code"] == {"text": "Activity score"}
    assert novel["valueQuantity"]["value"] == 2.0
    assert by_gene["ABCG2"] == []


def test_medication_recommendation_task_has_no_code(bundle):
    tasks = _resources(bundle, "medication-recommendation")
    assert len(tasks) == 2
    for task in tasks:
        assert "code" not in task
        assert task["status"] == "requested" and task["intent"] == "proposal"
    first, second = tasks
    assert first["input"] == [
        {
            "type": {
                "coding": [
                    {
                        "system": "http://loinc.org",
                        "code": "51963-7",
                        "display": "Medication assessed [ID]",
                    }
                ]
            },
            "valueCodeableConcept": {"text": "codeine"},
        },
        # CPIC's strength of recommendation is not LOINC's "Level of evidence"
        # (93044-6): text, under a text type.
        {
            "type": {"text": "Strength of recommendation"},
            "valueCodeableConcept": {"text": "Moderate"},
        },
    ]
    assert [i["type"]["coding"][0]["code"] for i in second["input"]] == ["51963-7"]


@pytest.mark.parametrize(
    "classification, sent",
    [
        ("Unknown", None),
        (" unknown ", None),
        ("", None),
        ("Unspecified", "Unspecified"),
    ],
)
def test_an_unknown_strength_is_left_out(classification, sent):
    """ "Unknown" is the readers' default for nothing; "Unspecified" is PharmCAT's
    word for a guideline without a CPIC strength (app/reports/evidence.py)."""
    task = FHIRExportService(MagicMock())._create_medication_recommendation_task(
        drug_name="codeine",
        recommendation={"recommendation": "x", "classification": classification},
        patient_ref="urn:uuid:p",
        implication_ref="urn:uuid:i",
        run_id="run-1",
    )
    strengths = [
        i["valueCodeableConcept"]["text"]
        for i in task["input"]
        if i["type"] == {"text": "Strength of recommendation"}
    ]
    assert strengths == ([sent] if sent else [])


def test_genomic_study_category_is_laboratory_and_code_is_a_procedure(bundle):
    (study,) = _resources(bundle, "genomic-study")
    assert study["category"]["coding"] == [
        {
            "system": "http://terminology.hl7.org/CodeSystem/observation-category",
            "code": "laboratory",
            "display": "Laboratory",
        }
    ]
    assert study["code"]["coding"] == [
        {
            "system": "http://snomed.info/sct",
            "code": "405824009",
            "display": "Genetic test",
        }
    ]


def test_report_has_no_conclusion_code_from_an_undefined_system(bundle):
    (report,) = _resources(bundle, "genomic-report")
    assert "conclusionCode" not in report
    assert report["code"]["coding"][0]["code"] == "51969-4"
    assert report["category"][0]["coding"][0]["code"] == "GE"


def test_clinical_content_survives(bundle):
    genotypes = _resources(bundle, "genotype")
    assert [o["code"]["text"] for o in genotypes] == [
        "CYP2D6 Genotype",
        "ABCG2 Genotype",
        "NOVEL1 Genotype",
    ]
    cyp2d6 = genotypes[0]
    haplotypes = [c["valueCodeableConcept"] for c in _component(cyp2d6, "84414-2")]
    assert haplotypes == [{"text": "*1"}, {"text": "*4"}]

    implications = _resources(bundle, "therapeutic-implication")
    phenotypes = {
        o["derivedFrom"][0]["display"]: o["component"][0]["valueCodeableConcept"]
        for o in implications
        if not _component(o, "51963-7")
    }
    assert {k: v["text"] for k, v in phenotypes.items()} == {
        "CYP2D6 *1/*4": "Intermediate Metabolizer",
        "ABCG2 rs2231142 reference (G)/rs2231142 reference (G)": "Normal Function",
        "NOVEL1 *1/*1": "Normal Metabolizer",
    }

    (implication,) = [o for o in implications if _component(o, "51963-7")]
    (drug,) = _component(implication, "51963-7")
    assert drug["valueCodeableConcept"] == {"text": "codeine"}
    (conclusion,) = _component(implication, "conclusion-string")
    assert conclusion["valueString"] == "Use label recommended dosing."
    assert implication["derivedFrom"][0]["display"] == "CYP2D6 *1/*4"
    citation = implication["extension"][0]["valueRelatedArtifact"]
    assert citation["display"] == "CPIC Guideline"

    descriptions = [
        t["description"] for t in _resources(bundle, "medication-recommendation")
    ]
    assert descriptions == [
        "Use label recommended dosing.",
        "Be alert to ineffectiveness.",
    ]
