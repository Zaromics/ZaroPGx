"""Every list the report reads back from the database comes in PharmCAT's order.

The drug annotations were read without an ORDER BY and came back in the order
they lay in the table, which is not the order they were written
(test_report_annotation_order.py). The same held for a run's other lists: its
genes, each gene's diplotypes (a gene's first is the call shown when PharmCAT
gives no recommendation diplotype), its messages and its unannotated calls. On
PharmCAT's example report the gene rows already lie out of the order they were
written in. They are all read in the order the parser wrote them, which is
PharmCAT's.

Each test moves the earlier half of a run's rows of each list after the rest, as
rows land where there is room in a busier table, and states that the table order
is disturbed before checking what is read.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from sqlalchemy import text

from app.pharmcat.pharmcat_parser import PharmCATParser
from app.pharmcat.report_json import iter_gene_blocks
from app.services.pharmcat_data_service import PharmCATDataService
from tests.test_report_unannotated_gene_calls import F2, F5

DATA = Path(__file__).resolve().parents[1] / "test_data"
TABLES = ("gene_summary", "diplotypes", "messages", "unannotated_gene_calls")


def _report(name):
    report = json.loads((DATA / name).read_text(encoding="utf-8"))
    if "unannotatedGeneCalls" in report:
        # PharmCAT 3.4's: F2 and F5 (test_report_unannotated_gene_calls.py).
        report["unannotatedGeneCalls"] = [copy.deepcopy(F5), copy.deepcopy(F2)]
    return report


def _pharmcat_order(report):
    blocks = list(iter_gene_blocks(report["genes"]))
    return {
        "genes": [b.gene_symbol for b in blocks],
        "diplotypes": [
            (b.gene_symbol, d.get("label"))
            for b in blocks
            for d in b.gene_data.get("sourceDiplotypes") or []
        ],
        "messages": [
            (b.gene_symbol, m.get("message"))
            for b in blocks
            for m in b.gene_data.get("messages") or []
        ],
        "unannotated": [
            c["geneSymbol"] for c in report.get("unannotatedGeneCalls", [])
        ],
    }


def _ids(session, table, run_id, ordered):
    order = " ORDER BY created_at, id" if ordered else ""
    return [
        row_id
        for (row_id,) in session.execute(
            text(f"SELECT id FROM pharmcat.{table} WHERE run_id = :run_id{order}"),
            {"run_id": run_id},
        )
    ]


def _load_disturbed(session, report, run_id):
    """Load the report, then move the earlier half of each list's rows after the
    rest: copied out, deleted and written back, unchanged, at the table's end."""
    PharmCATParser(session).parse_and_load(report, run_id=run_id)
    session.flush()
    for table in TABLES:
        written = _ids(session, table, run_id, ordered=True)
        if len(written) < 2:
            continue
        moved = {"ids": written[: len(written) // 2]}
        session.execute(
            text(
                f"CREATE TEMP TABLE moved AS SELECT * FROM pharmcat.{table} "
                "WHERE id = ANY(:ids)"
            ),
            moved,
        )
        session.execute(
            text(f"DELETE FROM pharmcat.{table} WHERE id = ANY(:ids)"), moved
        )
        session.execute(text(f"INSERT INTO pharmcat.{table} SELECT * FROM moved"))
        session.execute(text("DROP TABLE moved"))
        session.flush()
        assert (
            _ids(session, table, run_id, ordered=False) != written
        ), f"{table}: the table order should be disturbed"


@pytest.mark.parametrize(
    "fixture",
    [
        # PharmCAT 2.15's example: CPIC and DPWG gene blocks, 46 messages.
        "pharmcat.example.report.json",
        # PharmCAT 3.4, unphased: several candidate diplotypes per gene, and
        # unannotated calls.
        "pharmcat.unphased.example.report.json",
    ],
)
def test_the_parser_reads_each_list_in_pharmcat_s_order(db_session, fixture):
    report = _report(fixture)
    _load_disturbed(db_session, report, "order")
    parser = PharmCATParser(db_session)
    expected = _pharmcat_order(report)

    assert [g["gene_symbol"] for g in parser.get_gene_summary("order")] == expected[
        "genes"
    ]
    assert [
        (d["gene_symbol"], d["diplotype_label"]) for d in parser.get_diplotypes("order")
    ] == expected["diplotypes"]
    assert [
        (m["gene_symbol"], m["message"]) for m in parser.get_messages("order")
    ] == expected["messages"]
    assert [
        c["geneSymbol"] for c in parser.get_unannotated_gene_calls("order")
    ] == expected["unannotated"]
    # The cases at stake: a gene with several diplotypes, and messages.
    genes = [g for g, _ in expected["diplotypes"]]
    assert expected["messages"] or any(genes.count(g) > 1 for g in genes)


@pytest.mark.parametrize(
    "fixture",
    ["pharmcat.example.report.json", "pharmcat.unphased.example.report.json"],
)
def test_the_report_s_genes_and_messages_are_in_pharmcat_s_order(db_session, fixture):
    report = _report(fixture)
    _load_disturbed(db_session, report, "order")
    data = PharmCATDataService(db_session).get_pharmcat_data_by_run_id("order")
    expected = _pharmcat_order(report)

    first_seen = list(dict.fromkeys(expected["genes"] + expected["unannotated"]))
    assert [g["gene"] for g in data["genes"]] == first_seen
    assert [
        (m["gene_symbol"], m["message"]) for m in data.get("messages", [])
    ] == expected["messages"]
    # A gene's candidates, in PharmCAT's order, the first of them the one shown.
    for gene in data["genes"]:
        if gene.get("all_diplotypes"):
            labels = [d["diplotype"] for d in gene["all_diplotypes"]]
            mine = [l for g, l in expected["diplotypes"] if g == gene["gene"]]
            assert labels == mine, gene["gene"]
