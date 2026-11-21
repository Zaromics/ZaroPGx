"""A call in a gene whose alternative cannot be named still says its pairing is
unchecked.

``docker/pypgx/read_phase.py`` names no haplotype for CYP1A2, CYP2A6, CYP2E1, POR,
SLC22A2 and SLCO1B1 (its NAMING_UNFAITHFUL): over the 61 GeT-RM genomes, naming from
the variants reproduced PyPGx's print for 2,434 of 2,497 haplotypes, and the 63 misses
were almost all in those six, so a name there would disagree with the caller. The
report's open-pairing note needs a name, so those calls got no note at all -- though
whether the reads reach a pairing does not depend on naming it. Now they get the
first half of the note, and the Methodology paragraph says why the second is missing.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from app.reports import read_phase as report_phase

REPO = Path(__file__).resolve().parents[1]
SERVICE = REPO / "docker" / "pypgx" / "read_phase.py"
EXAMPLE_JSON = REPO / "test_data" / "pharmcat.unphased.example.report.json"

# A CYP2A6 call placing one variant on each chromosome: one pairing, one alternative.
# The variant names are illustrative; what the module reads is which allele each
# defines and which haplotype each allele is on.
A, B, C = "19-40848628-A-T", "19-40850414-G-A", "19-40843783-C-T"


def _results(gene="CYP2A6", haplotype1=("*2",), haplotype2=("*9",), defines=None):
    defines = defines or {"*2": [A], "*9": [B]}
    return {
        "results": {
            gene: {
                "success": True,
                "diplotype": f"{'+'.join(haplotype1)}/{'+'.join(haplotype2)}",
                "details": {
                    "haplotype1": list(haplotype1),
                    "haplotype2": list(haplotype2),
                    "supporting_variants": [
                        {"allele": allele, "variants": [{"variant": v} for v in vs]}
                        for allele, vs in defines.items()
                    ],
                },
            }
        }
    }


def _row(gene="CYP2A6", diplotype="*2/*9", tool="PYPGX"):
    if tool == "PHARMCAT":
        return {"gene": gene, "diplotype": diplotype, "call_source": "MATCHER"}
    return {
        "gene": gene,
        "diplotype": diplotype,
        "call_source": "OUTSIDE",
        "tool_source": tool,
    }


DONE = {"status": "done", "pairs": [], "names": {}}


def test_the_list_is_the_services_own():
    tree = ast.parse(SERVICE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == "NAMING_UNFAITHFUL" for t in node.targets
        ):
            names = {
                elt.value
                for elt in ast.walk(node.value)
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            }
            break
    else:
        pytest.fail("docker/pypgx/read_phase.py no longer defines NAMING_UNFAITHFUL")
    assert names == set(report_phase.UNNAMED)


def test_an_unreached_pairing_is_marked_without_a_name():
    row = _row()
    found = report_phase.annotate_unnamed([row], DONE, _results())
    assert set(found) == {"CYP2A6"}
    detail = row["read_phase_open"]
    assert detail["alternative"] is None
    assert detail["call_note"] == report_phase.unnamed_note()


def test_the_note_says_the_fact_and_names_nothing():
    note = report_phase.unnamed_note()
    assert note.startswith("Reads cannot check this pairing;")
    assert "*" not in note


def test_the_note_fits_the_column_the_named_one_fits():
    """The same 19%-wide Diplotype column test_read_phase.py measures open_note for."""
    assert len(report_phase.unnamed_note()) <= 120


def test_a_pairing_the_reads_decided_is_left_to_the_evidence():
    evidence = dict(DONE, pairs=[{"gene": "CYP2A6", "a": A, "b": B, "shows": "trans"}])
    row = _row()
    assert report_phase.annotate_unnamed([row], evidence, _results()) == {}
    assert "read_phase_open" not in row


def test_more_than_one_pairing_is_not_this_shape():
    """Three placed variants is three pairings: annotate_open does not speak for
    that shape either."""
    results = _results(
        haplotype1=("*2", "*4"), defines={"*2": [A], "*9": [B], "*4": [C]}
    )
    assert report_phase.annotate_unnamed([_row()], DONE, results) == {}


def test_a_call_placing_nothing_says_nothing():
    results = _results(haplotype1=("*1",), haplotype2=("*9",), defines={"*9": [B]})
    assert report_phase.annotate_unnamed([_row()], DONE, results) == {}


def test_a_gene_whose_alternative_is_named_is_left_to_annotate_open():
    results = _results(gene="CYP2B6")
    assert report_phase.annotate_unnamed([_row(gene="CYP2B6")], DONE, results) == {}


def test_a_call_pharmcat_made_is_left_to_pharmcat_phase():
    results = _results(gene="SLCO1B1")
    row = _row(gene="SLCO1B1", tool="PHARMCAT")
    assert report_phase.annotate_unnamed([row], DONE, results) == {}


@pytest.mark.parametrize("key", ["read_phase_conflict", "read_phase_open"])
def test_an_existing_note_is_not_doubled(key):
    row = _row()
    row[key] = {"call_note": "already said"}
    assert report_phase.annotate_unnamed([row], DONE, _results()) == {}
    assert row[key] == {"call_note": "already said"}


@pytest.mark.parametrize("status", ["not_run", "failed"])
def test_nothing_is_said_where_the_reads_were_not_read(status):
    evidence = {"status": status, "reason": "x"}
    assert report_phase.annotate_unnamed([_row()], evidence, _results()) == {}


def test_methodology_says_why_no_name_is_given():
    counts = {"asserting": 1, "agree": 0, "conflict": 0, "unchecked": 1}
    without = report_phase.coverage_note(counts)
    assert "not named" not in without
    one = report_phase.coverage_note(counts, unnamed=["CYP2A6"])
    assert "For CYP2A6 the other pairing is not named" in one
    assert "for this gene" in one
    two = report_phase.coverage_note(counts, unnamed=["POR", "CYP2A6"])
    assert "For CYP2A6 and POR" in two and "these genes" in two


def test_the_note_and_the_reason_reach_the_page(monkeypatch, tmp_path):
    """Generated as a run generates it, from a pypgx_result.json and read_phase.json
    on disk."""
    pytest.importorskip("weasyprint")
    import app.reports.generator as generator_module

    (tmp_path / "read_phase.json").write_text(json.dumps(DONE), encoding="utf-8")
    (tmp_path / "pypgx_result.json").write_text(
        json.dumps(_results()), encoding="utf-8"
    )
    for key in list(generator_module.REPORT_CONFIG):
        if key.startswith(("write_", "show_")):
            monkeypatch.setitem(
                generator_module.REPORT_CONFIG, key, key == "write_html"
            )
    result = generator_module.generate_report(
        {"data": json.loads(EXAMPLE_JSON.read_text(encoding="utf-8"))},
        str(tmp_path),
        {"id": "p1"},
        job_id=None,
    )
    page = (tmp_path / result["html_path"].split("/")[-1]).read_text(encoding="utf-8")
    assert (
        "Reads cannot check this pairing; the other reading cannot be named here."
        in page
    )
    assert "For CYP2A6 the other pairing is not named" in page
    # The call itself is printed as PyPGx made it.
    assert "*2/*9" in page
