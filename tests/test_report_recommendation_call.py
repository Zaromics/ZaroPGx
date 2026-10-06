"""Unphased DPYD: the report shows the call PharmCAT doses from.

On 30x NA12878 PharmCAT found DPYD c.1601G>A (*4) and c.1627A>G (*5) on unphased
data. Its sourceDiplotypes list each allele separately, each "Indeterminate"; its
recommendationDiplotypes combine them into one call, a Normal Metabolizer with
activity score 2.0, and every DPYD drug recommendation in the report rests on that.
The report showed it three ways: the gene table "c.1601G>A (*4), Indeterminate"
(first source entry), the Executive Summary "c.1601G>A (*4) AND c.1627A>G (*5),
Unknown, 2.0" (source columns mixed with a lookup score), and the guidance "Normal
DPD activity". Where the source call is one diplotype nothing changes (440).

The DPYD blocks below carry the fields of that run's PharmCAT 3 output.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.pharmcat.pharmcat_client import normalize_pharmcat_results
from app.pharmcat.pharmcat_parser import PharmCATParser
from app.pharmcat.report_json import (
    extract_display_call,
    recommendation_call_for_display,
)
from app.reports.pharmcat_tsv_parser import executive_summary_call
from app.services.pharmcat_data_service import PharmCATDataService

REPORT_JSON = (
    Path(__file__).resolve().parents[1]
    / "test_data"
    / "pharmcat.example.v340.report.json"
)


def _allele(name):
    return {"name": name, "function": "Normal function", "activityValue": "1.0"}


def _source(name):
    return {
        "gene": "DPYD",
        "label": name,
        "allele1": _allele(name),
        "allele2": None,
        "phenotypes": ["Indeterminate"],
        "activityScore": "n/a",
    }


COMBINED = {
    "gene": "DPYD",
    "label": "c.1601G>A (*4)/c.1627A>G (*5)",
    "allele1": _allele("c.1601G>A (*4)"),
    "allele2": _allele("c.1627A>G (*5)"),
    "phenotypes": ["Normal Metabolizer"],
    "activityScore": "2.0",
}
DPYD = {
    "geneSymbol": "DPYD",
    "callSource": "MATCHER",
    "sourceDiplotypes": [_source("c.1601G>A (*4)"), _source("c.1627A>G (*5)")],
    "recommendationDiplotypes": [COMBINED],
}
ONE_CALL = {
    "label": "*1/*2",
    "phenotypes": ["Intermediate Metabolizer"],
    "activityScore": None,
}
CYP2C19 = {
    "geneSymbol": "CYP2C19",
    "callSource": "MATCHER",
    "sourceDiplotypes": [ONE_CALL],
    "recommendationDiplotypes": [dict(ONE_CALL, label="*1/*2 (lookup)")],
}


@pytest.mark.parametrize(
    "source_count, recommendation, expected",
    [
        (2, [COMBINED], COMBINED),
        (0, [COMBINED], COMBINED),
        # One source diplotype is PharmCAT's displayed call (440).
        (1, [COMBINED], None),
        # No single, real recommendation call to show instead.
        (2, [COMBINED, COMBINED], None),
        (2, [], None),
        (2, None, None),
        (2, [dict(COMBINED, label="Unknown/Unknown")], None),
        (2, [dict(COMBINED, label="  ")], None),
    ],
)
def test_when_the_recommendation_call_is_shown(source_count, recommendation, expected):
    assert recommendation_call_for_display(source_count, recommendation) == expected


def test_json_lane_shows_the_combined_dpyd_call():
    assert extract_display_call(DPYD) == {
        "diplotype": "c.1601G>A (*4)/c.1627A>G (*5)",
        "phenotype": "Normal Metabolizer",
        "activity_score": "2.0",
    }
    # A one-diplotype source call still wins over its lookup.
    assert extract_display_call(CYP2C19)["diplotype"] == "*1/*2"


def test_file_lane_normalisation_uses_it():
    """The checked-in PharmCAT 3.4.0 report, with a DPYD block like NA12878's."""
    report = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    dpyd = copy.deepcopy(report["genes"]["CYP2C19"])
    dpyd.update(copy.deepcopy(DPYD))
    report["genes"]["DPYD"] = dpyd

    normalized = normalize_pharmcat_results(report)

    assert normalized["success"], normalized
    genes = {g["gene"]: g for g in normalized["data"]["genes"]}
    assert (genes["DPYD"]["diplotype"], genes["DPYD"]["phenotype"]) == (
        "c.1601G>A (*4)/c.1627A>G (*5)",
        "Normal Metabolizer",
    )
    assert genes["CYP2C19"]["diplotype"] == "*38/*38"


def test_stored_gene_rows_carry_the_recommendation_diplotypes():
    """The diplotypes table holds source calls only; the DB lane reads the
    recommendation call from the stored gene JSON."""
    session = MagicMock()
    session.query.return_value.filter.return_value.all.return_value = [
        SimpleNamespace(
            gene_symbol="DPYD",
            call_source="MATCHER",
            phenotype_source="CPIC",
            chromosome="chr1",
            phased=False,
            gene_full_data=DPYD,
        )
    ]
    (row,) = PharmCATParser(session).get_gene_summary("run")
    assert row["recommendation_diplotypes"] == [COMBINED]


def _stored_source(name):
    return {
        "gene_symbol": "DPYD",
        "diplotype_label": name,
        "phenotype": "Indeterminate",
        "activity_score": None,
    }


def test_db_lane_report_row_shows_the_combined_call():
    service = PharmCATDataService(MagicMock())
    genes = [{"gene_symbol": "DPYD", "recommendation_diplotypes": [COMBINED]}]
    diplotypes = [_stored_source("c.1601G>A (*4)"), _stored_source("c.1627A>G (*5)")]

    (row,) = service._transform_genes_for_reports(genes, diplotypes)

    assert row["diplotype"] == "c.1601G>A (*4)/c.1627A>G (*5)"
    assert row["phenotype"] == "Normal Metabolizer"
    assert row["activity_score"] == 2.0
    assert (row["allele1"], row["allele2"]) == ("c.1601G>A (*4)", "c.1627A>G (*5)")
    # Both alleles PharmCAT found are still listed with the row.
    assert [d["diplotype"] for d in row["all_diplotypes"]] == [
        "c.1601G>A (*4)",
        "c.1627A>G (*5)",
    ]


def test_db_lane_one_source_call_is_unchanged():
    service = PharmCATDataService(MagicMock())
    genes = [{"gene_symbol": "CYP2C19", "recommendation_diplotypes": [COMBINED]}]
    diplotypes = [
        {
            "gene_symbol": "CYP2C19",
            "diplotype_label": "*1/*2",
            "phenotype": "Intermediate Metabolizer",
            "activity_score": None,
        }
    ]
    (row,) = service._transform_genes_for_reports(genes, diplotypes)
    assert (row["diplotype"], row["phenotype"]) == ("*1/*2", "Intermediate Metabolizer")


# The Executive Summary rows, as app.reports.pharmcat_tsv_parser parses the TSV.
DPYD_TSV = {
    "gene": "DPYD",
    "diplotype": "c.1601G>A (*4) AND c.1627A>G (*5)",
    "phenotype": "Unknown",  # the parser's default for the blank Phenotype column
    "rec_lookup_diplotype": "c.1601G>A (*4)/c.1627A>G (*5)",
    "rec_lookup_phenotype": "Normal Metabolizer",
    "rec_lookup_activity_score": 2.0,
}


def test_executive_summary_shows_the_whole_lookup_call_for_dpyd():
    assert executive_summary_call(DPYD_TSV) == (
        "c.1601G>A (*4)/c.1627A>G (*5)",
        "Normal Metabolizer",
        2.0,
    )


@pytest.mark.parametrize(
    "row, expected",
    [
        (
            {
                "diplotype": "*1/*2",
                "phenotype": "Intermediate Metabolizer",
                "activity_score": 1.5,
                "rec_lookup_diplotype": "*1/*2",
                "rec_lookup_phenotype": "Intermediate Metabolizer",
                "rec_lookup_activity_score": 1.5,
            },
            ("*1/*2", "Intermediate Metabolizer", 1.5),
        ),
        # 440: a real source call beats an Unknown lookup, field by field.
        (
            {
                "diplotype": "*1/*3",
                "phenotype": "Normal Metabolizer",
                "rec_lookup_diplotype": "Unknown/Unknown",
            },
            ("*1/*3", "Normal Metabolizer", None),
        ),
        # Several source calls but no lookup call: the source text stays.
        (
            {
                "diplotype": "*1/*6 OR *4/*9",
                "phenotype": "",
                "rec_lookup_diplotype": "",
            },
            ("*1/*6 OR *4/*9", "", None),
        ),
    ],
)
def test_executive_summary_otherwise_keeps_the_source_columns(row, expected):
    assert executive_summary_call(row) == expected
