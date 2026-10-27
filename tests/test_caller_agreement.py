"""The report says when its two callers read the same data differently.

On an alignment run PyPGx genotypes PharmCAT's genes as well as its own, for their
copy number, and PharmCAT's call is the one printed. PyPGx's own call for such a
gene was then dropped, so a reader saw one diplotype with nothing to say that a
second caller, over the same reads, had named different alleles. On NA19908 PharmCAT
printed CYP4F2 *1/*4 and PyPGx called *2/*3, which is what GeT-RM says, and the
report gave no sign the two differed.

Now a disagreement about the alleles is said beside the call, in the Executive
Summary's notes and in one alert, and the printed call does not change. Only calls
both callers write as star alleles are compared, so the genes whose callers describe
a genotype in different words -- ABCG2, IFNL3 and VKORC1 by rsID, DPYD by variant,
G6PD's "MALE", the RYR1 and CFTR variants PyPGx has no allele for -- are left out,
and so is NAT2, whose two callers name the same haplotypes differently.
"""

from __future__ import annotations

import json
import re
from html import unescape
from pathlib import Path

import pytest

import app.reports.generator as generator_module
from app.reports.caller_agreement import (
    CALLER,
    DIFFERENT_NAMES,
    alert,
    annotate_genes,
    call_note,
    disagree,
    haplotypes,
    pypgx_call,
    summary_note,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "test_data"


def _pharmcat(gene, diplotype):
    """A gene row as PharmCAT's own matcher call reaches the templates."""
    return {"gene": gene, "diplotype": diplotype, "call_source": "MATCHER"}


def _pypgx(**calls):
    """A /genotype response (docker/pypgx's run_pypgx) with these calls."""
    return {
        "success": True,
        "results": {
            gene: {
                "success": True,
                "gene": gene,
                "diplotype": diplotype,
                "details": {"phenotype_method": None},
            }
            for gene, diplotype in calls.items()
        },
    }


# --------------------------------------------------------------------------
# What counts as the same call
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "printed, other",
    [
        ("*1/*5", "*5/*1"),  # the order of the two chromosomes
        ("*68 + *4/*5", "*68+*4/*5"),  # the spacing of a tandem
        (" *2 / *3 ", "*2/*3"),  # spacing anywhere
        ("*2x2/*10", "*2+*2/*10"),  # x2 is two copies
        ("*1/*2", "*1/*2;*1/*3"),  # one of PyPGx's candidates
        ("*14/*27 or *37/*53", "*37/*53"),  # one of PharmCAT's
        ("*4a/*5", "*4A/*5"),  # the case of a suffix
    ],
)
def test_the_same_call_written_differently_is_not_a_disagreement(printed, other):
    assert not disagree(printed, other)


@pytest.mark.parametrize(
    "printed, other",
    [
        ("*1/*4", "*2/*3"),  # CYP4F2 NA19908: the phasing case
        ("*3/*3", "*2/*2"),  # NUDT15 HG00599: PharmCAT has no *2
        ("*1/*46", "*1/*8"),  # TPMT HG03521: PyPGx has no *46
        ("*27/*80+*28", "*1/*80+*28"),  # UGT1A1 HG00436: one allele differs
        ("*14/*27 or *37/*53", "*14/*37"),  # SLCO1B1 NA19035: a third reading
        ("*2x2/*10", "*2/*10"),  # the number of copies
    ],
)
def test_different_alleles_are_a_disagreement(printed, other):
    assert disagree(printed, other)


@pytest.mark.parametrize(
    "printed, other",
    [
        # The genes whose two callers do not describe a genotype in star alleles.
        ("rs2231142 reference (G)/rs2231142 reference (G)", "Reference/Reference"),
        ("rs9923231 reference (C)/rs9923231 variant (T)", "Reference/rs9923231"),
        ("B (reference)/B (reference)", "MALE/B (reference)"),
        ("c.85T>C (*9A)|c.496A>G", "c.496A>G/c.1129-5923C>G"),
        ("c.10747G>C (heterozygous)", "Reference/Reference"),
        ("Reference/Reference", "Reference/F508del"),
        # Nothing to compare.
        ("Unknown/Unknown", "*1/*2"),
        ("*1/*2", ""),
        ("", "*1/*2"),
        ("*1/*2", None),
    ],
)
def test_a_call_that_is_not_star_alleles_is_never_a_disagreement(printed, other):
    assert not disagree(printed, other)


def test_a_diplotype_of_star_alleles_parses_and_others_do_not():
    assert haplotypes("*1/*5") == (("*1",), ("*5",))
    assert haplotypes("*5/*1") == haplotypes("*1/*5")
    assert haplotypes("*68+*4/*5") == (("*4", "*68"), ("*5",))
    assert haplotypes("*2x2/*10") == (("*10",), ("*2", "*2"))
    for text in ("Reference/Reference", "*1", "*1/*2/*3", "B (reference)/B", ""):
        assert haplotypes(text) is None


# --------------------------------------------------------------------------
# PyPGx's call for the gene
# --------------------------------------------------------------------------


def test_pypgx_call_reads_its_own_result_and_nothing_else():
    results = _pypgx(CYP4F2="*2/*3")
    assert pypgx_call(results, "CYP4F2") == "*2/*3"
    assert pypgx_call(results, "cyp4f2") == "*2/*3"
    assert pypgx_call(results, "TPMT") is None
    assert pypgx_call(None, "CYP4F2") is None
    assert pypgx_call({}, "CYP4F2") is None


@pytest.mark.parametrize("call", ["", "Indeterminate", "None", None])
def test_pypgx_without_a_call_is_not_a_second_opinion(call):
    results = _pypgx(CYP4F2=call)
    assert pypgx_call(results, "CYP4F2") is None
    rows = [_pharmcat("CYP4F2", "*1/*4")]
    assert annotate_genes(rows, results) == {}
    assert "caller_disagreement" not in rows[0]


def test_a_gene_pypgx_failed_is_not_a_second_opinion():
    results = _pypgx(CYP4F2="*2/*3")
    results["results"]["CYP4F2"]["success"] = False
    assert pypgx_call(results, "CYP4F2") is None
    rows = [_pharmcat("CYP4F2", "*1/*4")]
    assert annotate_genes(rows, results) == {}


# --------------------------------------------------------------------------
# Which rows are marked
# --------------------------------------------------------------------------


def test_a_disagreement_is_marked_on_pharmcats_row():
    rows = [_pharmcat("CYP4F2", "*1/*4")]
    assert annotate_genes(rows, _pypgx(CYP4F2="*2/*3")) == {"CYP4F2": "*2/*3"}
    mark = rows[0]["caller_disagreement"]
    assert mark["tool"] == CALLER == "PyPGx"
    assert mark["call"] == "*2/*3"
    assert mark["call_note"] == call_note("*2/*3")
    assert mark["note"] == summary_note("*2/*3")
    assert "*2/*3" in mark["call_note"] and "PharmCAT" in mark["call_note"]


def test_agreement_leaves_the_row_alone():
    rows = [_pharmcat("CYP4F2", "*2/*3")]
    assert annotate_genes(rows, _pypgx(CYP4F2="*3/*2")) == {}
    assert "caller_disagreement" not in rows[0]


def test_a_call_pypgx_supplied_has_no_second_opinion_to_show():
    """PharmCAT takes an outside call over its own matcher's and records only the
    outside one, so its own reading of CYP2D6 -- or of a gene in genes.json's
    pypgx_preferred_over_pharmcat -- is not in its report to compare."""
    rows = [
        {
            "gene": "CYP2D6",
            "diplotype": "*1/*4",
            "call_source": "OUTSIDE",
            "tool_source": "PyPGx",
        }
    ]
    assert annotate_genes(rows, _pypgx(CYP2D6="*1/*4")) == {}
    assert "caller_disagreement" not in rows[0]


def test_nat2_is_left_out_because_the_callers_name_it_differently():
    """PharmCAT 3.4 names NAT2 alleles as PharmVar does, PyPGx by the legacy
    haplotype groups: on the 61 GeT-RM genomes every NAT2 difference was these two
    vocabularies, and both callers agreed with GeT-RM."""
    assert "NAT2" in DIFFERENT_NAMES
    rows = [_pharmcat("NAT2", "*4/*14")]
    assert annotate_genes(rows, _pypgx(NAT2="*12/*14")) == {}
    assert "caller_disagreement" not in rows[0]


def test_an_uncalled_row_is_not_a_disagreement():
    rows = [_pharmcat("CYP4F2", "Unknown/Unknown")]
    assert annotate_genes(rows, _pypgx(CYP4F2="*2/*3")) == {}


def test_rows_that_are_not_dictionaries_are_skipped():
    assert annotate_genes([None, "CYP4F2", 3], _pypgx(CYP4F2="*2/*3")) == {}
    assert annotate_genes([], _pypgx(CYP4F2="*2/*3")) == {}
    assert annotate_genes(None, _pypgx(CYP4F2="*2/*3")) == {}


# --------------------------------------------------------------------------
# Every disagreement the 61 GeT-RM genomes produced
# --------------------------------------------------------------------------

# PharmCAT's printed call and PyPGx's own, for every gene-sample pair of the 61
# GeT-RM genomes (scripts/getrm) where the two named different alleles. 671 of the
# 1,159 pairs where PharmCAT called a gene itself and PyPGx called it too are
# comparable as star alleles; these are the 14 that differed, at most one in any
# one report. GeT-RM's value is beside each: PyPGx was right in four, PharmCAT in
# four, both wrong in one, and the rest GeT-RM does not settle.
GETRM_DISAGREEMENTS = [
    ("HG00129", "SLCO1B1", "*1/*15", "*5/*37"),
    ("HG00185", "CYP3A4", "*1/*37", "*3/*22"),
    ("HG00436", "UGT1A1", "*27/*80+*28", "*1/*80+*28"),  # GeT-RM *27/(*28 + *60)
    ("HG00599", "NUDT15", "*3/*3", "*2/*2"),  # GeT-RM *2/*2
    ("HG01086", "NUDT15", "*3/*9", "*1/*3"),  # GeT-RM *2/*9: both wrong
    ("HG01269", "CYP3A4", "*1/*37", "*1/*3"),  # GeT-RM *1/*37 or *3/*22
    ("HG03521", "TPMT", "*1/*46", "*1/*8"),  # GeT-RM *1/*46
    ("NA06991", "SLCO1B1", "*15/*45", "*15/*46"),
    ("NA12248", "CYP2B6", "*1/*36", "*6/*22"),
    ("NA18526", "NUDT15", "*1/*3", "*1/*2"),
    ("NA19026", "TPMT", "*1/*46", "*1/*8"),  # GeT-RM *8/*33 or *1/*46
    ("NA19035", "SLCO1B1", "*14/*27 or *37/*53", "*14/*37"),
    ("NA19079", "NUDT15", "*3/*5", "*2/*5"),  # GeT-RM *2/*5
    ("NA19908", "CYP4F2", "*1/*4", "*2/*3"),  # GeT-RM (*2)/*3
]

# The NAT2 pairs from the same runs, every one of them the two vocabularies and not
# a disagreement: PyPGx's *12 and *13 are PharmCAT's *4, its *5 is PharmCAT's *6.
GETRM_NAT2 = [
    ("HG00589", "*4/*61", "*4/*13"),
    ("NA18855", "*4/*6", "*6/*13"),
    ("NA19035", "*4/*6", "*6/*13"),
    ("NA19093", "*4/*14", "*12/*14"),
    ("NA19122", "*4/*44", "*12/*24"),
    ("NA19143", "*4/*14", "*13/*14"),
    ("NA19207", "*4/*14", "*12/*14"),
]


@pytest.mark.parametrize("sample, gene, printed, other", GETRM_DISAGREEMENTS)
def test_every_getrm_disagreement_is_marked(sample, gene, printed, other):
    rows = [_pharmcat(gene, printed)]
    assert annotate_genes(rows, _pypgx(**{gene: other})) == {gene: other}


@pytest.mark.parametrize("sample, printed, other", GETRM_NAT2)
def test_no_getrm_nat2_pair_is_marked(sample, printed, other):
    rows = [_pharmcat("NAT2", printed)]
    assert annotate_genes(rows, _pypgx(NAT2=other)) == {}


def test_the_whole_run_marks_one_gene_and_leaves_the_rest(monkeypatch):
    """A report of its own: the one gene the two callers differ on is marked, every
    gene they agree on and every gene with no comparison is not."""
    rows = [
        _pharmcat("CYP4F2", "*1/*4"),
        _pharmcat("CYP2C19", "*1/*2"),
        _pharmcat("NAT2", "*4/*14"),
        _pharmcat("ABCG2", "rs2231142 reference (G)/rs2231142 reference (G)"),
        _pharmcat("TPMT", "*1/*1"),
    ]
    found = annotate_genes(
        rows,
        _pypgx(
            CYP4F2="*2/*3",
            CYP2C19="*2/*1",
            NAT2="*12/*14",
            ABCG2="Reference/Reference",
        ),
    )
    assert found == {"CYP4F2": "*2/*3"}
    assert [r["gene"] for r in rows if r.get("caller_disagreement")] == ["CYP4F2"]


# --------------------------------------------------------------------------
# The alert
# --------------------------------------------------------------------------


def test_no_disagreement_is_no_alert():
    assert alert({}) is None


def test_the_alert_names_each_gene_and_the_other_call():
    one = alert({"CYP4F2": "*2/*3"})
    assert "CYP4F2 (PyPGx *2/*3)" in one
    assert "The two callers disagree" in one
    assert "does not know which is right" in one
    # A difference can be a definition one caller does not have (TPMT *46).
    assert "allele definitions are not identical" in one
    both = alert({"CYP4F2": "*2/*3", "NUDT15": "*2/*2"})
    assert "CYP4F2 (PyPGx *2/*3), NUDT15 (PyPGx *2/*2)" in both


def test_the_alert_escapes_what_it_is_given():
    assert "<b>" not in alert({"<b>G</b>": "*1/*2"})


# --------------------------------------------------------------------------
# What the report shows
# --------------------------------------------------------------------------


def _clean(fragment):
    return unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment)).strip())


def test_the_note_sits_beside_the_call_in_both_reports():
    from app.reports.generator import env

    rows = [_pharmcat("CYP4F2", "*1/*4")]
    annotate_genes(rows, _pypgx(CYP4F2="*2/*3"))
    note = str(env.get_template("_pypgx_evidence.html").module.call_notes(rows[0]))
    assert 'class="call-note disagreement-note"' in note
    assert f"⚠ {call_note('*2/*3')}" in _clean(note)
    for template in ("report_template.html", "interactive_report.html"):
        assert ".disagreement-note" in (
            ROOT / "app" / "reports" / "templates" / template
        ).read_text(encoding="utf-8")


def test_an_agreed_call_gets_no_note():
    from app.reports.generator import env

    rows = [_pharmcat("CYP4F2", "*2/*3")]
    annotate_genes(rows, _pypgx(CYP4F2="*2/*3"))
    note = str(env.get_template("_pypgx_evidence.html").module.call_notes(rows[0]))
    assert "disagreement-note" not in note


def _report(monkeypatch, tmp_path, pypgx):
    """generate_report on PharmCAT's example, which calls CYP4F2 *1/*1 itself, with
    the PyPGx results a Nextflow alignment run leaves beside it."""
    for key in list(generator_module.REPORT_CONFIG):
        if key.startswith(("write_", "show_")):
            monkeypatch.setitem(
                generator_module.REPORT_CONFIG, key, key == "write_html"
            )
    data = json.loads((DATA / "pharmcat.example.report.json").read_text())
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "pypgx_result.json").write_text(json.dumps(pypgx), encoding="utf-8")
    monkeypatch.setattr(generator_module, "load_job_file_type", lambda *a, **k: "bam")
    result = generator_module.generate_report(
        {"data": data}, str(tmp_path), {"id": "p1"}, job_id=None
    )
    return result, (tmp_path / result["html_path"].split("/")[-1]).read_text()


def test_the_report_marks_the_call_the_summary_and_the_alerts(monkeypatch, tmp_path):
    result, page = _report(
        monkeypatch, tmp_path / "differ", _pypgx(CYP4F2="*2/*3", TPMT="*1/*1")
    )
    rows = {r["gene"]: r for r in result["processed_data"]["genes"]}
    assert rows["CYP4F2"]["call_source"] == "MATCHER"
    assert rows["CYP4F2"]["caller_disagreement"]["call"] == "*2/*3"
    assert "caller_disagreement" not in rows["TPMT"]
    body = _clean(page.split("</style>")[-1])
    # Beside the call, in the Executive Summary's notes, and in one alert.
    assert f"⚠ {call_note('*2/*3')}" in body
    assert f"⚠ CYP4F2 : {summary_note('*2/*3')}" in body
    assert "The two callers disagree: CYP4F2 (PyPGx *2/*3)" in body
    # The printed call is still PharmCAT's.
    assert rows["CYP4F2"]["diplotype"] == "*1/*1"


def test_a_report_whose_callers_agree_says_nothing(monkeypatch, tmp_path):
    result, page = _report(
        monkeypatch, tmp_path / "agree", _pypgx(CYP4F2="*1/*1", TPMT="*1/*1")
    )
    rows = {r["gene"]: r for r in result["processed_data"]["genes"]}
    assert "caller_disagreement" not in rows["CYP4F2"]
    body = page.split("</style>")[-1]
    assert "disagreement-note" not in body
    assert "The two callers disagree" not in body
