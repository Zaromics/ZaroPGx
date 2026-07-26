"""Golden-file tests for live PharmCAT JSON parse paths.

Wave 4 item 337: lock ``normalize_pharmcat_results`` and
``PharmCATParser._parse_genes`` against checked-in fixtures before parser
unification (355).

Fixtures:
- ``pharmcat.example.nested.v2.report.json`` — nested genes→CPIC/DPWG shape
  (legacy PharmCAT ~2.15).
- ``pharmcat.example.v340.report.json`` — flat genes→symbol shape regenerated
  from PharmCAT 3.4.0 against ``pharmcat.example.vcf``.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from app.pharmcat.pharmcat_client import (
    extract_drug_recommendations_from_phenotype,
    extract_sample_id_from_vcf,
    normalize_pharmcat_results,
)
from app.pharmcat.pharmcat_parser import (
    PharmCATDiplotype,
    PharmCATGeneSummary,
    PharmCATParser,
)

TEST_DATA = Path(__file__).resolve().parent.parent / "test_data"
NESTED_V2 = TEST_DATA / "pharmcat.example.nested.v2.report.json"
FLAT_V340 = TEST_DATA / "pharmcat.example.v340.report.json"
EXAMPLE_VCF = TEST_DATA / "pharmcat.example.vcf"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _genes_by_name(normalized: dict, *, source: str | None = None) -> dict:
    out = {}
    for entry in normalized["data"]["genes"]:
        if source is not None and entry.get("guideline_source") != source:
            continue
        out[entry["gene"]] = entry
    return out


def _parse_genes_with_mock(genes_section: dict):
    added = []
    session = MagicMock()
    session.add.side_effect = lambda obj: added.append(obj)
    parser = PharmCATParser(db_session=session)
    parser._parse_genes(genes_section, "golden-run")
    return added


def test_nested_v2_fixture_is_nested_cpic_dpwg():
    report = _load(NESTED_V2)
    assert "CPIC" in report["genes"]
    assert "DPWG" in report["genes"]
    assert report["pharmcatVersion"].startswith("v2.")


def test_v340_fixture_is_flat_and_version_3_4_0():
    report = _load(FLAT_V340)
    assert report["pharmcatVersion"] == "3.4.0"
    assert "CPIC" not in report["genes"]
    assert "CYP2C19" in report["genes"]
    assert "geneSymbol" in report["genes"]["CYP2C19"]


def test_normalize_nested_v2_cpic_cyp_calls():
    normalized = normalize_pharmcat_results(_load(NESTED_V2))
    assert normalized["success"] is True

    cpic = _genes_by_name(normalized, source="CPIC")
    assert cpic["CYP2D6"]["diplotype"] == "*1/*3"
    assert cpic["CYP2D6"]["phenotype"] == "Intermediate Metabolizer"
    assert float(cpic["CYP2D6"]["activity_score"]) == 1.0

    assert cpic["CYP2C19"]["diplotype"] == "*38/*38"
    assert cpic["CYP2C19"]["phenotype"] == "Normal Metabolizer"

    sources = {g["guideline_source"] for g in normalized["data"]["genes"]}
    assert sources == {"CPIC", "DPWG"}
    assert len(normalized["data"]["drugRecommendations"]) > 0


def test_normalize_flat_v340_cyp_calls():
    """3.4.0 flat report from the same VCF — CYP2D6 has no call without outside calls."""
    normalized = normalize_pharmcat_results(_load(FLAT_V340))
    assert normalized["success"] is True

    genes = _genes_by_name(normalized)
    assert genes["CYP2C19"]["diplotype"] == "*38/*38"
    assert genes["CYP2C19"]["phenotype"] == "Normal Metabolizer"
    assert genes["CYP2C19"]["guideline_source"] == "CPIC"

    assert genes["CYP2D6"]["diplotype"] == "Unknown/Unknown"
    assert genes["CYP2D6"]["phenotype"] == "No Result"

    assert genes["ABCG2"]["diplotype"].startswith("rs2231142 reference")
    assert len(normalized["data"]["drugRecommendations"]) > 0


def test_normalize_accepts_wrapped_report_json_envelope():
    report = _load(FLAT_V340)
    wrapped = {"success": True, "report_json": report}
    normalized = normalize_pharmcat_results(wrapped)
    assert normalized["success"] is True
    genes = _genes_by_name(normalized)
    assert genes["CYP2C19"]["diplotype"] == "*38/*38"


def test_normalize_failed_api_response_short_circuits():
    normalized = normalize_pharmcat_results(
        {"success": False, "message": "pipeline exploded"}
    )
    assert normalized["success"] is False
    assert "pipeline exploded" in normalized["message"]
    assert normalized["data"]["genes"] == []


def test_parser_parse_genes_nested_v2_stores_cpic_and_dpwg():
    report = _load(NESTED_V2)
    added = _parse_genes_with_mock(report["genes"])

    summaries = [o for o in added if isinstance(o, PharmCATGeneSummary)]
    diplotypes = [o for o in added if isinstance(o, PharmCATDiplotype)]

    symbols = {(s.gene_symbol, s.call_source) for s in summaries}
    assert ("CYP2D6", "CPIC") in symbols
    assert ("CYP2D6", "DPWG") in symbols
    assert ("CYP2C19", "CPIC") in symbols

    cyp2d6 = [
        d
        for d in diplotypes
        if d.gene_symbol == "CYP2D6" and d.diplotype_label == "*1/*3"
    ]
    assert cyp2d6
    assert cyp2d6[0].phenotype == "Intermediate Metabolizer"
    assert float(cyp2d6[0].activity_score) == 1.0


def test_parser_parse_genes_flat_v340_stores_gene_symbols():
    report = _load(FLAT_V340)
    added = _parse_genes_with_mock(report["genes"])

    summaries = [o for o in added if isinstance(o, PharmCATGeneSummary)]
    diplotypes = [o for o in added if isinstance(o, PharmCATDiplotype)]

    symbols = {s.gene_symbol for s in summaries}
    assert symbols == {"ABCG2", "CYP2C19", "CYP2D6", "SLCO1B1", "VKORC1"}

    by_gene = {d.gene_symbol: d for d in diplotypes}
    assert by_gene["CYP2C19"].diplotype_label == "*38/*38"
    assert by_gene["CYP2C19"].phenotype == "Normal Metabolizer"
    assert by_gene["CYP2D6"].diplotype_label == "Unknown/Unknown"


def test_extract_sample_id_from_example_vcf():
    assert extract_sample_id_from_vcf(str(EXAMPLE_VCF)) == "Sample_1"


def test_extract_drug_recommendations_from_phenotype_top_level():
    phenotype = {
        "drugRecommendations": [
            {
                "gene": "CYP2C19",
                "drug": {"name": "clopidogrel"},
                "drugId": "B01AC04",
                "guidelineName": "CPIC",
                "recommendationText": "Consider alternative therapy.",
                "classification": "Strong",
            }
        ]
    }
    recs = extract_drug_recommendations_from_phenotype(phenotype)
    assert len(recs) == 1
    assert recs[0]["drug"] == "clopidogrel"
    assert recs[0]["gene"] == "CYP2C19"
    assert recs[0]["guideline"] == "CPIC"


def test_extract_drug_recommendations_from_phenotype_nested_phenotypes():
    phenotype = {
        "phenotypes": {
            "CYP2C19": {
                "drugRecommendations": [
                    {
                        "drug": {"name": "voriconazole"},
                        "drugId": "J02AC03",
                        "guidelineName": "CPIC",
                        "recommendationText": "Adjust dose.",
                        "classification": "Moderate",
                    }
                ]
            }
        }
    }
    recs = extract_drug_recommendations_from_phenotype(phenotype)
    assert {r["drug"] for r in recs} == {"voriconazole"}
    assert recs[0]["gene"] == "CYP2C19"
