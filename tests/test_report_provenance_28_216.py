"""Provenance resolver contract (BACKLOG 28 + 216).

The rule under test: report what the run recorded, and say so explicitly when
it recorded nothing. No arm may consult the gene name.
"""

from app.reports.provenance import (
    CALLED_BY_NO_CALL,
    CALLED_BY_OUTSIDE,
    CALLED_BY_PHARMCAT,
    CALLED_BY_PYPGX,
    CALLED_BY_UNKNOWN,
    resolve_called_by,
    resolve_guideline_source,
)


def test_matcher_is_pharmcat():
    prov = resolve_called_by({"call_source": "MATCHER"})
    assert prov.letter == CALLED_BY_PHARMCAT
    assert prov.recorded is True


def test_outside_without_tool_marker_is_explicitly_outside():
    prov = resolve_called_by({"call_source": "OUTSIDE"})
    assert prov.letter == CALLED_BY_OUTSIDE
    assert prov.recorded is True


def test_outside_with_tool_marker_names_the_tool():
    assert (
        resolve_called_by({"call_source": "OUTSIDE", "tool_source": "PyPGx"}).letter
        == CALLED_BY_PYPGX
    )
    assert (
        resolve_called_by({"call_source": "OUTSIDE", "source": "PyPGx"}).letter
        == CALLED_BY_PYPGX
    )


def test_enrichment_marker_never_overrides_matcher():
    # generator.py merge-2 stamps tool_source="PyPGx" onto genes PharmCAT called.
    prov = resolve_called_by({"call_source": "MATCHER", "tool_source": "PyPGx"})
    assert prov.letter == CALLED_BY_PHARMCAT


def test_none_is_no_call():
    prov = resolve_called_by({"call_source": "NONE"})
    assert prov.letter == CALLED_BY_NO_CALL
    assert prov.recorded is True


def test_tsv_outside_call_column():
    assert resolve_called_by({"outside_call": "no"}).letter == CALLED_BY_PHARMCAT
    assert resolve_called_by({"outside_call": "yes"}).letter == CALLED_BY_OUTSIDE
    assert (
        resolve_called_by({"outside_call": "yes", "tool_source": "PyPGx"}).letter
        == CALLED_BY_PYPGX
    )


def test_pypgx_only_gene_pharmcat_never_saw():
    prov = resolve_called_by(
        {"tool_source": "PyPGx", "pyPgxOnly": True, "diplotype": "*1/*4"}
    )
    assert prov.letter == CALLED_BY_PYPGX
    assert prov.recorded is True


def test_called_but_unrecorded_is_unknown_not_a_guess():
    prov = resolve_called_by({"diplotype": "*1/*1"})
    assert prov.letter == CALLED_BY_UNKNOWN
    assert prov.recorded is False


def test_empty_row_is_no_call():
    assert resolve_called_by({}).letter == CALLED_BY_NO_CALL


def test_gene_name_alone_never_produces_a_tool_letter():
    # Regression lock on the deleted determine_called_by heuristic.
    for gene in ("CYP2D6", "HLA-A", "HLA-B", "MT-RNR1", "CYP2C19"):
        assert resolve_called_by({"gene": gene}).letter == CALLED_BY_NO_CALL


def test_every_provenance_carries_a_label():
    for row in ({"call_source": "MATCHER"}, {"call_source": "OUTSIDE"}, {}):
        assert resolve_called_by(row).label.strip()


def test_guideline_source_letters():
    assert resolve_guideline_source({"phenotype_source": "DPWG"}) == "D"
    assert resolve_guideline_source({"guideline_source": "CPIC"}) == "C"
    assert resolve_guideline_source({"guideline_source": "FDA"}) == "F"
    assert resolve_guideline_source({"guideline_source": "PharmGKB"}) == "P"
    assert resolve_guideline_source({"guideline_source": "C"}) == "C"


def test_guideline_source_blank_when_not_recorded():
    assert resolve_guideline_source({}) == ""
    assert resolve_guideline_source({"phenotype_source": None}) == ""
    assert resolve_guideline_source({"guideline_source": "Whatever"}) == ""
