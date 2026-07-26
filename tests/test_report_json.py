"""Unit tests for the unified PharmCAT report.json walkers (Wave 4 / 355)."""

from __future__ import annotations

import json
from pathlib import Path

from app.pharmcat.report_json import (
    detect_format,
    extract_recommendation_call,
    iter_gene_blocks,
)

TEST_DATA = Path(__file__).resolve().parent.parent / "test_data"
NESTED_V2 = TEST_DATA / "pharmcat.example.nested.v2.report.json"
FLAT_V340 = TEST_DATA / "pharmcat.example.v340.report.json"


def _genes(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))["genes"]


def test_detect_format_nested_v2():
    assert detect_format(_genes(NESTED_V2)) == "nested"


def test_detect_format_flat_v340():
    assert detect_format(_genes(FLAT_V340)) == "flat"


def test_detect_format_empty():
    assert detect_format({}) == "empty"
    assert detect_format(None) == "empty"


def test_iter_gene_blocks_nested_emits_source_and_symbol():
    blocks = list(iter_gene_blocks(_genes(NESTED_V2)))
    keys = {(b.source, b.gene_symbol) for b in blocks}
    assert ("CPIC", "CYP2D6") in keys
    assert ("DPWG", "CYP2D6") in keys
    assert ("CPIC", "CYP2C19") in keys

    cyp = next(b for b in blocks if b.source == "CPIC" and b.gene_symbol == "CYP2D6")
    call = extract_recommendation_call(cyp.gene_data)
    assert call["diplotype"] == "*1/*3"
    assert call["phenotype"] == "Intermediate Metabolizer"
    assert float(call["activity_score"]) == 1.0


def test_iter_gene_blocks_flat_defaults_source_when_missing():
    blocks = list(iter_gene_blocks(_genes(FLAT_V340)))
    by_gene = {b.gene_symbol: b for b in blocks}
    assert set(by_gene) == {"ABCG2", "CYP2C19", "CYP2D6", "SLCO1B1", "VKORC1"}
    assert by_gene["CYP2C19"].source == "CPIC"

    call = extract_recommendation_call(by_gene["CYP2C19"].gene_data)
    assert call["diplotype"] == "*38/*38"
    assert call["phenotype"] == "Normal Metabolizer"

    no_result = extract_recommendation_call(by_gene["CYP2D6"].gene_data)
    assert no_result["diplotype"] == "Unknown/Unknown"
    assert no_result["phenotype"] == "No Result"


def test_extract_recommendation_call_defaults_when_absent():
    call = extract_recommendation_call({})
    assert call["diplotype"] == "Unknown/Unknown"
    assert call["phenotype"] == "Unknown"
    assert call["activity_score"] is None


def test_detect_format_prefers_guideline_keys_over_gene_like_values():
    """A CPIC/DPWG/FDA top key is nested even if values look gene-shaped."""
    genes = {
        "CPIC": {
            "CYP2D6": {
                "geneSymbol": "CYP2D6",
                "sourceDiplotypes": [],
            }
        }
    }
    assert detect_format(genes) == "nested"


def test_detect_format_flat_when_gene_fields_present():
    genes = {
        "CYP2D6": {
            "geneSymbol": "CYP2D6",
            "alleleDefinitionVersion": "2026-01-01",
            "recommendationDiplotypes": [{"label": "*1/*1", "phenotypes": ["NM"]}],
        }
    }
    assert detect_format(genes) == "flat"
