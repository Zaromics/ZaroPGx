"""Golden-file tests for the PharmCAT TSV report parser.

The parser is the fallback path used when PharmCAT's JSON output cannot be
read, so these lock its behaviour against two checked-in real reports.
"""

from pathlib import Path

from app.pharmcat.pharmcat_client import parse_pharmcat_tsv_report
from app.reports.pharmcat_tsv_parser import parse_pharmcat_tsv

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


# ---------------------------------------------------------------------------
# 28 + 216 -- the TSV "Outside Call" column is a real provenance signal.
# pharmcat_client computed its index and never read it; this parser -- the one
# the report path actually uses -- did not look for it at all.
# ---------------------------------------------------------------------------


def test_parse_pharmcat_tsv_surfaces_outside_call():
    diplotypes, _recs = parse_pharmcat_tsv(
        str(TEST_DATA / "pharmcat.example.report.tsv")
    )
    by_gene = {d["gene"]: d for d in diplotypes}

    # Verified against the checked-in fixture: CYP2D6 is the only "yes".
    assert by_gene["CYP2D6"]["outside_call"] == "yes"
    assert by_gene["RYR1"]["outside_call"] == "no"
    assert all("outside_call" in d for d in diplotypes)


def test_parse_pharmcat_tsv_outside_call_absent_is_blank():
    diplotypes, _recs = parse_pharmcat_tsv(
        str(TEST_DATA / "pharmcat.example.v340.report.tsv")
    )
    assert diplotypes
    assert all(d["outside_call"] in {"yes", "no", ""} for d in diplotypes)


def test_outside_call_alone_does_not_make_a_row_informative():
    """Every TSV row carries a "yes"/"no", so reading the column must not
    resurrect rows the parser previously (correctly) skipped. Counts pinned
    from the pre-change parser."""
    for filename, expected in (
        ("pharmcat.example.report.tsv", 20),
        ("pharmcat.example.v340.report.tsv", 18),
        ("pharmcat.example2.report.tsv", 19),
    ):
        diplotypes, _recs = parse_pharmcat_tsv(str(TEST_DATA / filename))
        assert len(diplotypes) == expected, filename


def test_tsv_outside_call_feeds_the_provenance_resolver():
    from app.reports.provenance import (
        CALLED_BY_OUTSIDE,
        CALLED_BY_PHARMCAT,
        resolve_called_by,
    )

    diplotypes, _recs = parse_pharmcat_tsv(
        str(TEST_DATA / "pharmcat.example.report.tsv")
    )
    by_gene = {d["gene"]: d for d in diplotypes}
    assert resolve_called_by(by_gene["CYP2D6"]).letter == CALLED_BY_OUTSIDE
    assert resolve_called_by(by_gene["RYR1"]).letter == CALLED_BY_PHARMCAT
