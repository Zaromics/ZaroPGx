"""No empty element anywhere in the FHIR bundle.

FHIR has no empty elements: a string holds something besides whitespace, an array
at least one item, an object a value or children (ele-1), and JSON null is no value
at all. HAPI FHIR 8.12's $validate, with the Genomics Reporting IG 3.0.0 loaded,
gave the bundle of a VCF run of PharmCAT's example VCF 6 errors: the genotypes of
F2, F5 and HLA-C had valueCodeableConcept.text "" ("value cannot be empty"). No tool
reported those genes, so the report's gene list (app/reports/generator.py
_build_canonical_diplotypes) held a placeholder row for each with diplotype "", and
so would any run for any gene no tool reports (HLA-C from a VCF, which PharmCAT has
no row for). Pushed as a transaction, HAPI took that bundle and stored the three
genotypes with no value, which the IG's genotype profile requires.

A placeholder row now states "Not performed", coded DataAbsentReason not-performed;
a blank diplotype on a row without that marker states "Unknown"
(tests/test_fhir_uncalled_reason.py). ``empty_elements`` walks a whole bundle; the
tests run it over bundles built from the rows the report step produces, from blank
inputs, and over the bundles the repository ships.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from app.reports import generator as generator_module
from app.services.fhir_export_service import FHIRExportService

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_BUNDLE = REPO_ROOT / "docs" / "samples" / "sample_fhir_r4_pgx_bundle.json"

# The genes the VCF run of PharmCAT's example VCF had no row for.
UNREPORTED = ("F2", "F5", "HLA-C")


def empty_elements(node: Any, path: str = "Bundle") -> List[str]:
    """The path of every element of ``node`` FHIR forbids for having nothing in
    it: null, a blank string, an empty array or object."""
    if node is None or (isinstance(node, str) and not node.strip()):
        return [path]
    if isinstance(node, (dict, list)) and not node:
        return [path]
    found: List[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            found += empty_elements(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found += empty_elements(value, f"{path}[{index}]")
    return found


def _export(pharmcat_data: Dict[str, Any], output_format: str = "json", **kwargs):
    result = FHIRExportService(MagicMock()).export_pgx_report(
        run_id="run-empty",
        output_format=output_format,
        pharmcat_data=pharmcat_data,
        **kwargs,
    )
    assert result["success"], result.get("error")
    return result


def _canonical_rows(raw_gene_entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The gene list as the report step hands it to the export: every supported
    gene, a placeholder row for each one no tool reported."""
    return generator_module._build_canonical_diplotypes(
        raw_gene_entries=raw_gene_entries, file_type="vcf", workflow_config=None
    )


def _genotypes(bundle: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        entry["resource"]["code"]["text"].removesuffix(" Genotype"): entry["resource"]
        for entry in bundle["entry"]
        if entry["resource"]["resourceType"] == "Observation"
        and entry["resource"]["code"].get("text", "").endswith(" Genotype")
    }


def test_the_placeholder_rows_are_what_the_bundle_was_built_from():
    """The premise: a gene no tool reported comes to the export with diplotype ""."""
    rows = {row["gene"]: row for row in _canonical_rows([])}
    for gene in UNREPORTED:
        assert rows[gene]["diplotype"] == ""


def test_a_gene_no_tool_reported_has_a_value():
    data = {
        "genes": _canonical_rows(
            [
                {"gene": "CYP2C19", "diplotype": "*38/*38", "phenotype": "Normal"},
                # A gene PharmCAT reported but did not call.
                {"gene": "ABCB1", "diplotype": "Unknown", "phenotype": "Unknown"},
            ]
        ),
        "drugRecommendations": [],
    }
    bundle = _export(data)["bundle"]
    genotypes = _genotypes(bundle)

    for gene in UNREPORTED:
        assert genotypes[gene]["valueCodeableConcept"]["text"] == "Not performed"
        assert "dataAbsentReason" not in genotypes[gene]
    assert genotypes["ABCB1"]["valueCodeableConcept"]["text"] == "Unknown"
    assert "dataAbsentReason" not in genotypes["ABCB1"]
    assert genotypes["CYP2C19"]["valueCodeableConcept"] == {"text": "*38/*38"}

    report = bundle["entry"][0]["resource"]
    displays = {ref["display"] for ref in report["result"]}
    assert {"F2 Not performed", "F5 Not performed", "HLA-C Not performed"} <= displays
    assert empty_elements(bundle) == []


def test_no_empty_element_when_no_gene_was_called():
    """Every supported gene a placeholder: the worst case of the VCF run."""
    data = {"genes": _canonical_rows([]), "drugRecommendations": []}
    bundle = _export(data)["bundle"]
    assert len(_genotypes(bundle)) == len(data["genes"])
    assert empty_elements(bundle) == []
    xml = _export(data, output_format="xml")["content"]
    assert 'value=""' not in xml


@pytest.mark.parametrize("diplotype", ["", "   ", None])
def test_a_blank_or_missing_diplotype_states_unknown(diplotype):
    """Without the placeholder's not_tested marker (a database-lane row with no
    diplotype label), a blank says no call, not that nothing tested the gene."""
    bundle = _export(
        {"genes": [{"gene": "CYP2D6", "diplotype": diplotype}]},
    )["bundle"]
    value = _genotypes(bundle)["CYP2D6"]["valueCodeableConcept"]
    assert value["text"] == "Unknown"
    assert value["coding"][0]["code"] == "unknown"
    assert empty_elements(bundle) == []


def test_blank_inputs_leave_no_empty_element():
    """Every other field the export copies in, blank: left out or defaulted."""
    data = {
        "genes": [
            {
                "gene": "CYP2D6",
                "diplotype": "*1/*4",
                "phenotype": "   ",
                "allele1": " ",
                "allele2": "*4",
            },
        ],
        "drugRecommendations": [
            {
                "drug": "codeine",
                "genes": ["CYP2D6"],
                "recommendations": [
                    {
                        "recommendation": "",
                        "classification": " ",
                        "guideline_source": "  ",
                    }
                ],
            },
            {"drug": "", "genes": ["CYP2D6"], "recommendations": [{}]},
        ],
    }
    bundle = _export(
        data, patient_info={"id": "p-1", "name": {"family": "Doe", "given": ""}}
    )["bundle"]
    assert empty_elements(bundle) == []

    resources = [entry["resource"] for entry in bundle["entry"]]
    patient = next(r for r in resources if r["resourceType"] == "Patient")
    assert patient["name"] == [{"family": "Doe"}]
    tasks = [r for r in resources if r["resourceType"] == "Task"]
    assert [t["description"] for t in tasks] == ["See report for details"] * 2
    assert tasks[1]["input"][0]["valueCodeableConcept"] == {"text": "Unknown"}
    genotype = _genotypes(bundle)["CYP2D6"]
    alleles = [c["valueCodeableConcept"]["text"] for c in genotype["component"][1:]]
    assert alleles == ["*4"]


@pytest.mark.parametrize(
    "name", [{"family": "", "given": [""]}, {"family": " ", "given": None}]
)
def test_a_blank_patient_name_is_left_out(name):
    bundle = _export(
        {"genes": [{"gene": "CYP2D6", "diplotype": "*1/*1"}]},
        patient_info={"id": "p-1", "name": name},
    )["bundle"]
    patient = next(
        e["resource"]
        for e in bundle["entry"]
        if e["resource"]["resourceType"] == "Patient"
    )
    assert "name" not in patient
    assert empty_elements(bundle) == []


def test_a_report_with_no_results_has_no_empty_result_list():
    bundle = _export({"genes": [], "drugRecommendations": []})["bundle"]
    assert "result" not in bundle["entry"][0]["resource"]
    assert empty_elements(bundle) == []


def test_the_shipped_sample_bundle_has_no_empty_element():
    assert empty_elements(json.loads(SAMPLE_BUNDLE.read_text(encoding="utf-8"))) == []


def test_the_walker_finds_what_hapi_rejected():
    """The walker itself, on the shape of the three genotypes HAPI rejected."""
    observation = {"valueCodeableConcept": {"text": ""}, "component": [], "note": [{}]}
    assert empty_elements(observation, "Observation") == [
        "Observation.valueCodeableConcept.text",
        "Observation.component",
        "Observation.note[0]",
    ]


def test_blank_patient_details_gene_names_and_recommendations_are_not_sent():
    """From review: the Patient's gender, birthDate and id, a gene's symbol, a
    recommendation and a guideline source that are only whitespace, and an activity
    score that parses as NaN (no JSON number), are left out or defaulted."""
    data = {
        "genes": [
            {"gene": "CYP2D6", "diplotype": "*1/*4", "activity_score": "nan"},
            {"gene": "  ", "diplotype": "*1/*1"},
        ],
        "drugRecommendations": [
            {
                "drug": "codeine",
                "genes": ["CYP2D6"],
                "recommendations": [
                    {"recommendation": "   ", "guideline_source": "   "}
                ],
            },
        ],
    }
    result = _export(data, patient_info={"id": "  ", "gender": " ", "birthDate": " "})
    bundle = result["bundle"]
    assert empty_elements(bundle) == []
    # Valid JSON: no bare NaN.
    json.loads(json.dumps(bundle, allow_nan=False))

    resources = [entry["resource"] for entry in bundle["entry"]]
    patient = next(r for r in resources if r["resourceType"] == "Patient")
    assert "gender" not in patient and "birthDate" not in patient
    assert patient["identifier"][0]["value"].strip()
    implication = next(
        r
        for r in resources
        if r["resourceType"] == "Observation"
        and any(
            c.get("valueString") == "See report for details"
            for c in r.get("component", [])
        )
    )
    assert implication
    assert "Guideline" not in json.dumps(bundle).replace("Genomics Reporting", "")
