from app.pharmcat.report_json import extract_source_call
from app.reports.pharmcat_tsv_parser import (
    prefer_source_over_lookup,
    tsv_entry_to_source_diplotype,
)


def test_prefer_source_when_both_present():
    assert prefer_source_over_lookup("*1/*3", "Unknown/Unknown") == "*1/*3"


def test_prefer_lookup_when_source_empty():
    # NAT2-style: Source Diplotype present in real TSV but Recommendation Lookup blank
    assert prefer_source_over_lookup("*1/*1", "") == "*1/*1"
    assert prefer_source_over_lookup("", "*1/*1") == "*1/*1"
    assert prefer_source_over_lookup("  ", "rs1/rs1") == "rs1/rs1"


def test_prefer_both_empty():
    assert prefer_source_over_lookup("", "") == ""
    assert prefer_source_over_lookup(None, None) == ""


def test_tsv_synth_has_label_readable_by_extract_source_call():
    dip = tsv_entry_to_source_diplotype(
        {
            "diplotype": "*1/*3",
            "phenotype": "Intermediate Metabolizer",
            "activity_score": 1.0,
        }
    )
    assert dip["label"] == "*1/*3"
    call = extract_source_call({"sourceDiplotypes": [dip]})
    assert call["diplotype"] == "*1/*3"
    assert call["phenotype"] == "Intermediate Metabolizer"
