"""F2 and F5: genes PharmCAT calls without a guideline annotation.

PharmCAT 3.4 writes them under ``unannotatedGeneCalls``, not ``genes``. Neither
report lane read that list, so the gene table named F2 and F5 among the genes
examined without a call -- while the Executive Summary, built from PharmCAT's TSV,
listed the same two genes as called, with phenotype "Unknown", and then again as
not called. They are called: reference/reference is "Prothrombin 20210A negative"
and "Factor V Leiden negative". PharmCAT gives them no phenotype ("n/a"): a
reference call is labelled as CYP4F2's *1/*1 is ("Possibly Wild Type" from a VCF),
any other call N/A, as for VKORC1.

The blocks below are PharmCAT 3.4's for 30x NA12878 (public, GIAB), trimmed.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
from html import unescape
from pathlib import Path

import pytest

import app.reports.generator as generator_module
from app.pharmcat.pharmcat_client import normalize_pharmcat_results
from app.pharmcat.pharmcat_parser import PharmCATParser

DATA = Path(__file__).resolve().parents[1] / "test_data"
REPORT_JSON = DATA / "pharmcat.unphased.example.report.json"
REPORT_TSV = DATA / "pharmcat.unphased.example.report.tsv"


def _block(gene, chromosome, rsid, base, function):
    allele = {
        "gene": gene,
        "name": f"{rsid} reference ({base})",
        "function": function,
        "reference": True,
        "activityValue": "n/a",
    }
    diplotype = {
        "allele1": allele,
        "allele2": dict(allele),
        "gene": gene,
        "matchScore": 2,
        "phenotypes": ["n/a"],
        "activityScore": None,
        "lookupKey": ["n/a"],
        "label": f"{allele['name']}/{allele['name']}",
        "inferred": False,
        "combination": False,
    }
    return {
        "geneSymbol": gene,
        "chr": chromosome,
        "phased": False,
        "effectivelyPhased": True,
        "callSource": "MATCHER",
        "uncalledHaplotypes": [],
        "messages": [],
        "relatedDrugs": [],
        "sourceDiplotypes": [diplotype],
        "recommendationDiplotypes": [copy.deepcopy(diplotype)],
        "variants": [],
    }


F2 = _block("F2", "chr11", "rs1799963", "G", "Prothrombin 20210A negative")
F5 = _block("F5", "chr1", "rs6025", "C", "Factor V Leiden negative")
F2_CALL = "rs1799963 reference (G)/rs1799963 reference (G)"
TSV_ROWS = [
    "F2\trs1799963 reference (G)/rs1799963 reference (G)\t \t\t"
    "rs1799963 reference (G)\tProthrombin 20210A negative\t\t"
    "rs1799963 reference (G)\tProthrombin 20210A negative\t\tno\t2\tno\t\t\t",
    "F5\trs6025 reference (C)/rs6025 reference (C)\t \t\t"
    "rs6025 reference (C)\tFactor V Leiden negative\t\t"
    "rs6025 reference (C)\tFactor V Leiden negative\t\tno\t2\tno\t\t\t",
]


def _report():
    report = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    report["unannotatedGeneCalls"] = [copy.deepcopy(F2), copy.deepcopy(F5)]
    return report


def _clean(fragment):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def test_the_json_lane_reads_them():
    genes = {
        g["gene"]: g for g in normalize_pharmcat_results(_report())["data"]["genes"]
    }
    assert genes["F2"]["diplotype"] == F2_CALL
    assert genes["F5"]["diplotype"] == "rs6025 reference (C)/rs6025 reference (C)"
    assert genes["F2"]["call_source"] == "MATCHER"
    # No guideline covers them; none is invented.
    assert genes["F2"]["guideline_source"] is None


def test_the_database_lane_reads_them(db_session):
    from app.services.pharmcat_data_service import PharmCATDataService

    PharmCATParser(db_session).parse_and_load(_report(), run_id="run-1")
    data = PharmCATDataService(db_session).get_pharmcat_data_by_run_id("run-1")
    genes = {g["gene"]: g for g in data["genes"]}
    f5 = genes["F5"]
    assert f5["diplotype"] == "rs6025 reference (C)/rs6025 reference (C)"
    assert (f5["function1"], f5["function2"]) == ("Factor V Leiden negative",) * 2
    assert f5["called_by"] == "C"
    assert genes["F2"]["diplotype"] == F2_CALL


def _render(monkeypatch, tmp_path, tsv):
    monkeypatch.setattr(generator_module, "EXECSUM_USE_TSV", tsv)
    for key in list(generator_module.REPORT_CONFIG):
        if key.startswith(("write_", "show_")):
            monkeypatch.setitem(
                generator_module.REPORT_CONFIG, key, key == "write_html"
            )
    if tsv:
        shutil.copy(REPORT_TSV, tmp_path / "p1_pgx_pharmcat.tsv")
        with open(tmp_path / "p1_pgx_pharmcat.tsv", "a", encoding="utf-8") as fh:
            fh.write("\n".join(TSV_ROWS) + "\n")
    result = generator_module.generate_report(
        {"data": _report()}, str(tmp_path), {"id": "p1"}, job_id=None
    )
    return (tmp_path / result["html_path"].split("/")[-1]).read_text(encoding="utf-8")


def _summary_rows(page):
    summary = page[
        page.index('id="exec-summary"') : page.index("<h2>Genetic Results</h2>")
    ]
    rows = {
        _clean(g): (_clean(d).replace(" / ", "/"), _clean(p))
        for g, d, p in re.findall(
            r'class="exec-summary-gene">(.*?)</td>\s*<td class="exec-summary-diplotype[^"]*">'
            r'(.*?)</td>\s*<td class="exec-summary-phenotype">(.*?)</td>',
            summary,
            re.S,
        )
    }
    return summary, rows


@pytest.mark.parametrize("tsv", [True, False])
def test_the_summary_shows_them_called_with_no_phenotype(monkeypatch, tmp_path, tsv):
    summary, rows = _summary_rows(_render(monkeypatch, tmp_path, tsv))
    # Reference calls, from a VCF: the report's input type when none is recorded.
    assert rows["F2"] == (F2_CALL, "Possibly Wild Type")
    assert rows["F5"][1] == "Possibly Wild Type"
    not_called = _clean(summary[summary.index("Not called from this sample") :])
    assert not re.search(r"\bF2\b|\bF5\b", not_called)


def test_the_gene_table_does_not_list_them_as_uncalled(monkeypatch, tmp_path):
    page = _render(monkeypatch, tmp_path, False)
    genes = page[page.index("<h2>Genetic Results</h2>") :]
    uncalled = _clean(genes[genes.index("Genes examined without a call") :])
    assert not re.search(r"\bF2\b|\bF5\b", uncalled.split("Drugs")[0])
    assert F2_CALL in _clean(genes)
