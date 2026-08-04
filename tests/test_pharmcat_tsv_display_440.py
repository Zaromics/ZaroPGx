from app.reports.pharmcat_tsv_parser import prefer_source_over_lookup


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
