"""Golden-file tests for the PharmCAT TSV report parser.

The parser is the fallback path used when PharmCAT's JSON output cannot be
read, so these lock its behaviour against two checked-in real reports.
"""

from pathlib import Path

from app.pharmcat.pharmcat_client import parse_pharmcat_tsv_report

TEST_DATA = Path(__file__).resolve().parent.parent / "test_data"


def _genes_by_name(filename, phenotype_data=None):
    content = (TEST_DATA / filename).read_text(encoding="utf-8")
    result = parse_pharmcat_tsv_report(content, phenotype_data)
    return {gene["gene"]: gene for gene in result["genes"]}


def test_example1_parses_expected_genes():
    genes = _genes_by_name("pharmcat.example.report.tsv")

    assert len(genes) == 19

    assert genes["CYP2D6"]["diplotype"] == "*1/*3"
    assert genes["CYP2D6"]["phenotype"] == "Intermediate Metabolizer"
    assert genes["CYP2D6"]["activity_score"] == 1.0

    assert genes["CYP2C19"]["diplotype"] == "*38/*38"
    assert genes["CYP2C19"]["phenotype"] == "Normal Metabolizer"


def test_example2_parses_expected_genes():
    genes = _genes_by_name("pharmcat.example2.report.tsv")

    assert len(genes) == 18

    assert genes["CYP2C19"]["diplotype"] == "*2/*2"
    assert genes["CYP2C19"]["phenotype"] == "Poor Metabolizer"

    # example2 has no CYP2D6 call at all.
    assert "CYP2D6" not in genes


def test_no_drug_recommendations_without_phenotype_data():
    content = (TEST_DATA / "pharmcat.example.report.tsv").read_text(encoding="utf-8")
    result = parse_pharmcat_tsv_report(content)
    assert result["drugRecommendations"] == []


def test_drug_recommendations_come_from_phenotype_data():
    phenotype_data = {
        "phenotypes": {
            "CYP2C19": {
                "diplotype": "*2/*2",
                "phenotype": "Poor Metabolizer",
                "drugRecommendations": [
                    {
                        "drug": {"name": "clopidogrel"},
                        "drugId": "B01AC04",
                        "guidelineName": "CPIC",
                        "recommendationText": "Consider alternative antiplatelet therapy.",
                        "classification": "Strong",
                    }
                ],
            }
        }
    }
    content = (TEST_DATA / "pharmcat.example2.report.tsv").read_text(encoding="utf-8")
    result = parse_pharmcat_tsv_report(content, phenotype_data)

    assert "clopidogrel" in {rec["drug"] for rec in result["drugRecommendations"]}
