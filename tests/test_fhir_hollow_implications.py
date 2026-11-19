"""A drug row that names no gene and no recommendation makes no therapeutic implication.

MEASURED 2026-11-19: the bundle built from a real PharmCAT 3.4.0 report.json through
the JSON lane (the generator's fallback when a job's PharmCAT rows are not in the
database) had 111 therapeutic implications, and 104 of them were hollow -- a drug
name, "See report for details", and no genotype. HAPI's $validate against the
Genomics Reporting IG 3.0.0 gave 104 errors, every one "Observation.derivedFrom:
minimum required = 1". 100 came from pharmcat_client's "Related drug" rows (flat:
`gene`, not `genes`; a pointer, "See CPIC guidelines for ABCG2", not a
recommendation) and 4 from drugs no guideline matched. After: 0 errors.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import app.reports.generator as generator_module
from app.services.fhir import report_data, terminology
from app.services.fhir_export_service import FHIRExportService

REPO = Path(__file__).resolve().parents[1]
REPORT_340 = REPO / "test_data" / "pharmcat.example.v340.report.json"


def _bundle(data):
    return FHIRExportService(MagicMock())._build_fhir_bundle(
        data, "run-1", {"id": "p1"}
    )


def _drug(implication):
    medication = FHIRExportService.LOINC_CODES["medication_assessed"]
    for component in implication.get("component", []):
        if component["code"]["coding"][0]["code"] == medication:
            return component["valueCodeableConcept"]["text"]
    return None


def _implications(bundle):
    """The drugs' therapeutic implications. A gene's phenotype is stated with the same
    profile (_create_phenotype_implication) and names no drug."""
    return [
        e["resource"]
        for e in bundle["entry"]
        if terminology.PROFILE_THERAPEUTIC_IMPLICATION
        in e["resource"].get("meta", {}).get("profile", [])
        and _drug(e["resource"]) is not None
    ]


RELATED = {
    "gene": "ABCG2",
    "drug": "allopurinol",
    "drugId": "PA448320",
    "guideline": "CPIC",
    "recommendation": "See CPIC guidelines for ABCG2",
    "classification": "Related drug",
}
UNMATCHED = {
    "drug": "amikacin",
    "drugId": "PA448090",
    "genes": [],
    "recommendations": [],
}
GROUPED = {
    "drug": "allopurinol",
    "drugId": "PA448320",
    "genes": ["HLA-B"],
    "recommendations": [
        {
            "gene": "HLA-B",
            "recommendation": "Use allopurinol per standard dosing guidelines",
            "classification": "Strong",
            "guideline": "CPIC",
        }
    ],
}
GENES = [{"gene": "HLA-B", "diplotype": "*15:01/*15:01", "phenotype": "Normal"}]


def test_a_related_drug_row_and_an_unmatched_drug_make_nothing():
    bundle = _bundle(
        {"genes": GENES, "drugRecommendations": [RELATED, UNMATCHED, GROUPED]}
    )
    implications = _implications(bundle)
    assert [_drug(i) for i in implications] == ["allopurinol"]
    [implication] = implications
    assert implication["derivedFrom"]
    tasks = [e for e in bundle["entry"] if e["resource"]["resourceType"] == "Task"]
    assert len(tasks) == 1


def test_a_row_with_only_genes_or_only_recommendations_is_kept():
    genes_only = dict(GROUPED, recommendations=[])
    bundle = _bundle({"genes": GENES, "drugRecommendations": [genes_only]})
    assert len(_implications(bundle)) == 1


def test_the_json_lanes_bundle_has_no_implication_without_its_genotype(
    monkeypatch, tmp_path
):
    """From generate_report over the real report.json, as a job's bundle is built."""
    for key in list(generator_module.REPORT_CONFIG):
        if key.startswith(("write_", "show_")):
            monkeypatch.setitem(
                generator_module.REPORT_CONFIG, key, key == "write_html"
            )
    monkeypatch.setattr(generator_module, "fhir_export_enabled", lambda: False)
    job_id = "11111111-1111-1111-1111-111111111111"
    generator_module.generate_report(
        {"data": json.loads(REPORT_340.read_text(encoding="utf-8"))},
        str(tmp_path),
        {"id": "p1"},
        job_id=job_id,
    )
    data = json.loads((tmp_path / report_data.filename(job_id)).read_text("utf-8"))[
        "data"
    ]
    rows = data["drugRecommendations"]
    # The shape this guards: the lane still makes the hollow rows.
    assert any(r.get("classification") == "Related drug" for r in rows)

    implications = _implications(_bundle(data))
    assert implications
    assert all(i.get("derivedFrom") for i in implications)
    stated = [r for r in rows if r.get("genes") or r.get("recommendations")]
    assert len(implications) == len(stated)
