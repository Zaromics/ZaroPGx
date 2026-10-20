"""A call PharmCAT could not narrow to one diplotype is shown as what it is.

An unphased file can leave several diplotypes that fit it equally well, each with
its own phenotype and guidance; a variant-only VCF run without --missing-to-ref
leaves many. Both report lanes showed the first candidate as the call, and the
Executive Summary printed PharmCAT's TSV cells raw and cut the diplotype at its
first "/": "*1 / *6 OR *1" beside "Intermediate Metabolizer" fifteen times over.

The fixture is PharmCAT's own example VCF cut to four heterozygous sites, unphased
and variant-only (test_data/pharmcat.unphased.example.vcf), run through PharmCAT
3.4: CYP2B6 is any of 15 diplotypes, all Intermediate Metabolizer; SLCO1B1 any of
6, Decreased or Poor Function.
"""

from __future__ import annotations

import json
import re
import shutil
from html import unescape
from pathlib import Path

import pytest

import app.reports.generator as generator_module
from app.pharmcat.report_json import extract_display_call, unresolved_call
from app.reports.pharmcat_tsv_parser import (
    executive_summary_call,
    parse_pharmcat_tsv,
    unresolved_summary_call,
)

DATA = Path(__file__).resolve().parents[1] / "test_data"
REPORT_JSON = DATA / "pharmcat.unphased.example.report.json"
REPORT_TSV = DATA / "pharmcat.unphased.example.report.tsv"


@pytest.fixture(scope="module")
def report():
    return json.loads(REPORT_JSON.read_text(encoding="utf-8"))


def _tsv_rows():
    rows, _ = parse_pharmcat_tsv(str(REPORT_TSV))
    return {r["gene"]: r for r in rows}


# ---------------------------------------------------------------------------
# The call
# ---------------------------------------------------------------------------


def test_candidates_that_agree_give_their_phenotype(report):
    call = extract_display_call(report["genes"]["CYP2B6"])
    assert call["phenotype"] == "Intermediate Metabolizer"
    assert len(call["diplotype_candidates"]) == 15
    assert call["diplotype"].startswith("*1/*6 or *1/*7 or ")
    assert call["diplotype_display"] == "*1/*6 or 14 other possible diplotypes"
    assert call["activity_score"] is None


def test_candidates_that_differ_give_each_phenotype_they_could_be(report):
    call = extract_display_call(report["genes"]["SLCO1B1"])
    assert call["phenotype"] == "Decreased Function or Poor Function"
    assert [c["phenotype"] for c in call["diplotype_candidates"]] == [
        "Decreased Function"
    ] * 3 + ["Poor Function"] * 3


def test_a_single_call_is_untouched(report):
    call = extract_display_call(report["genes"]["CYP2D6"])
    assert "diplotype_candidates" not in call


def test_an_activity_score_only_when_every_candidate_has_it():
    two = [
        {
            "label": "*1/*2",
            "phenotypes": ["Intermediate Metabolizer"],
            "activityScore": 1.5,
        },
        {
            "label": "*1/*5",
            "phenotypes": ["Intermediate Metabolizer"],
            "activityScore": 1.5,
        },
    ]
    assert unresolved_call(two)["activity_score"] == 1.5
    two[1]["activityScore"] = "n/a"
    assert unresolved_call(two)["activity_score"] is None
    assert unresolved_call(two[:1]) is None


def test_the_tsv_lane_matches_the_json_lane(report):
    rows = _tsv_rows()
    for gene in ("CYP2B6", "SLCO1B1"):
        from_json = extract_display_call(report["genes"][gene])
        from_tsv = unresolved_summary_call(rows[gene])
        assert from_tsv["diplotype_candidates"] == from_json["diplotype_candidates"]
        assert executive_summary_call(rows[gene]) == (
            from_json["diplotype"],
            from_json["phenotype"],
            from_json["activity_score"],
        )


def test_the_database_lane_matches_the_json_lane(report):
    """The diplotypes table keeps no order; the gene summary's stored JSON does."""
    from app.services.pharmcat_data_service import PharmCATDataService

    gene = report["genes"]["SLCO1B1"]
    service = PharmCATDataService.__new__(PharmCATDataService)
    rows = service._transform_genes_for_reports(
        [
            {
                "gene_symbol": "SLCO1B1",
                "call_source": "MATCHER",
                "source_diplotypes": gene["sourceDiplotypes"],
                "recommendation_diplotypes": gene["recommendationDiplotypes"],
            }
        ],
        # Stored rows, in no particular order: the last candidate first.
        [
            {
                "gene_symbol": "SLCO1B1",
                "diplotype_label": d["label"],
                "phenotype": (d.get("phenotypes") or [None])[0],
                "allele1_name": d["allele1"]["name"],
            }
            for d in reversed(gene["sourceDiplotypes"])
        ],
    )
    row = next(r for r in rows if r["gene"] == "SLCO1B1")
    from_json = extract_display_call(gene)
    for key in ("diplotype", "diplotype_display", "phenotype", "diplotype_candidates"):
        assert row[key] == from_json[key], key
    assert row["allele1"] is None, "no one pair of alleles to name"


# ---------------------------------------------------------------------------
# The reports
# ---------------------------------------------------------------------------


def _clean(fragment: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _render(monkeypatch, tmp_path, lane):
    monkeypatch.setattr(generator_module, "EXECSUM_USE_TSV", True)
    for key in list(generator_module.REPORT_CONFIG):
        if key.startswith(("write_", "show_")):
            monkeypatch.setitem(generator_module.REPORT_CONFIG, key, key == lane)
    shutil.copy(REPORT_TSV, tmp_path / "p1_pgx_pharmcat.tsv")
    result = generator_module.generate_report(
        {"data": json.loads(REPORT_JSON.read_text(encoding="utf-8"))},
        str(tmp_path),
        {"id": "p1"},
        job_id=None,
    )
    key = "html_path" if lane == "write_html" else "interactive_html_path"
    return (tmp_path / result[key].split("/")[-1]).read_text(encoding="utf-8")


def test_the_executive_summary_shows_the_candidates_not_a_cut_string(
    monkeypatch, tmp_path
):
    page = _render(monkeypatch, tmp_path, "write_html")
    summary = page[
        page.index('id="exec-summary"') : page.index("<h2>Genetic Results</h2>")
    ]
    rows = {
        _clean(g): (_clean(d), _clean(p), p)
        for g, d, p in re.findall(
            r'class="exec-summary-gene">(.*?)</td>\s*<td class="exec-summary-diplotype[^"]*">'
            r'(.*?)</td>\s*<td class="exec-summary-phenotype">(.*?)</td>',
            summary,
            re.S,
        )
    }
    assert rows["SLCO1B1"][:2] == (
        "*1/*15 or 5 other possible diplotypes",
        "Decreased Function or Poor Function",
    )
    assert rows["CYP2B6"][:2] == (
        "*1/*6 or 14 other possible diplotypes",
        "Intermediate Metabolizer",
    )
    # Plain text, not a coloured pill claiming one phenotype: the candidates span
    # Decreased to Poor. (A pill also did not wrap, and ran off the page.)
    assert 'class="phenotype-candidates"' in rows["SLCO1B1"][2]
    assert "gene-phenotype" not in rows["SLCO1B1"][2]
    assert " OR " not in summary
    assert '"or"' in summary and "every possibility is listed" in summary.lower()
    # The genes PharmCAT could not call are named, not silently absent.
    assert "Not called from this sample" in summary and "DPYD" in summary


def test_the_gene_table_lists_every_candidate(monkeypatch, tmp_path):
    page = _render(monkeypatch, tmp_path, "write_html")
    genes = page[page.index("<h2>Genetic Results</h2>") :]
    note = _clean(
        re.search(r'<div class="unresolved-calls">(.*?)</div>', genes, re.S).group(1)
    )
    assert "CYP2B6 (15)" in note and "*1/*43 (Intermediate Metabolizer)" in note
    assert "SLCO1B1 (6)" in note and "*45/*47 (Poor Function)" in note
    assert "*1/*15 or 5 other possible diplotypes" in genes


def test_the_interactive_report_shows_them_too(monkeypatch, tmp_path):
    page = _render(monkeypatch, tmp_path, "write_interactive_html")
    assert "*1/*6 or 14 other possible diplotypes" in page
    assert '<div class="unresolved-calls">' in page
    assert "Decreased Function or Poor Function" in page


def test_a_no_call_row_leaves_the_summary_for_the_not_called_list(
    monkeypatch, tmp_path
):
    tsv = REPORT_TSV.read_text(encoding="utf-8").rstrip("\n").splitlines()
    tsv.append("NAT2\tno call" + "\t" * 14)
    rows, _ = parse_pharmcat_tsv_from_lines(tmp_path, tsv)
    assert "NAT2" in rows  # the parser keeps it; the summary must not
    monkeypatch.setattr(generator_module, "EXECSUM_USE_TSV", True)
    for key in list(generator_module.REPORT_CONFIG):
        if key.startswith(("write_", "show_")):
            monkeypatch.setitem(
                generator_module.REPORT_CONFIG, key, key == "write_html"
            )
    (tmp_path / "p1_pgx_pharmcat.tsv").write_text("\n".join(tsv) + "\n")
    result = generator_module.generate_report(
        {"data": json.loads(REPORT_JSON.read_text(encoding="utf-8"))},
        str(tmp_path),
        {"id": "p1"},
        job_id=None,
    )
    page = (tmp_path / result["html_path"].split("/")[-1]).read_text(encoding="utf-8")
    summary = page[
        page.index('id="exec-summary"') : page.index("<h2>Genetic Results</h2>")
    ]
    # Its row stays, every summary lists the same genes, but as not called.
    assert re.search(
        r'<tr id="summary-NAT2" class="exec-summary-uncalled">\s*'
        r'<td class="exec-summary-number">'
        r'\d+\.</td>\s*<td class="exec-summary-gene">NAT2</td>',
        summary,
    )
    assert "no call" not in summary


def parse_pharmcat_tsv_from_lines(tmp_path, lines):
    path = tmp_path / "rows.tsv"
    path.write_text("\n".join(lines) + "\n")
    rows, recs = parse_pharmcat_tsv(str(path))
    return {r["gene"]: r for r in rows}, recs


# ---------------------------------------------------------------------------
# Why genes were not called
# ---------------------------------------------------------------------------


def _job(session, absent_to_ref):
    from app.api.db import Job
    from app.api.models import JobCreate
    from app.services.job_service import JobService

    job = JobService(session).create_job(
        JobCreate(workflow_type="genomic_analysis", name="unresolved-calls")
    )
    session.commit()
    row = session.query(Job).filter(Job.id == job.id).first()
    row.job_metadata = {
        "file_analysis": {"file_type": "vcf"},
        "workflow": {},
        "pharmcat_absent_to_ref": absent_to_ref,
    }
    session.commit()
    return str(job.id)


@pytest.mark.parametrize("absent_to_ref, said", [(False, True), (True, False)])
def test_the_reason_is_given_when_the_job_says_so(
    session_factory, monkeypatch, tmp_path, absent_to_ref, said
):
    """A variant-only VCF has no record where the sample matches the reference;
    with "assume reference" off, PharmCAT cannot call such a gene."""
    data = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    monkeypatch.setattr(
        generator_module.PharmCATDataService,
        "get_pharmcat_data_for_workflow",
        lambda self, job: None,  # the files lane, with the job's metadata
    )
    for key in list(generator_module.REPORT_CONFIG):
        if key.startswith(("write_", "show_")):
            monkeypatch.setitem(
                generator_module.REPORT_CONFIG, key, key == "write_html"
            )
    session = session_factory()
    try:
        job_id = _job(session, absent_to_ref)
        result = generator_module.generate_report(
            {"data": data},
            str(tmp_path),
            {"id": "p1"},
            job_id=job_id,
            db_session=session,
        )
    finally:
        session.rollback()
        session.close()
    page = (tmp_path / result["html_path"].split("/")[-1]).read_text(encoding="utf-8")
    # Named as the upload page labels it, so it can be found there.
    assert ("Assume absent sites = reference" in page) is said
    # Said once, as a note among the Executive Summary's notes, where the genes
    # are named: Genetic Results printed it a second time.
    assert page.count("This file lists variant sites only") == int(said)
    if said:
        notes = page[
            page.index('id="exec-summary-notes"') : page.index(
                "<h2>Genetic Results</h2>"
            )
        ]
        assert '<p class="header-note">This file lists variant sites only' in notes
