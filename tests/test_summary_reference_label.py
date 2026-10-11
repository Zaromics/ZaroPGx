"""The Executive Summary shows CFTR's reference call as PharmCAT labels it.

PharmCAT's TSV prints a diplotype as its alleles' names, and CFTR's reference
allele is named "ivacaftor non-responsive CFTR sequence": the call read
"ivacaftor non-responsive CFTR sequence/ivacaftor non-responsive CFTR sequence",
the summary's longest cell by far. PharmCAT's own label for it, in its JSON, is
"Reference/Reference", as CACNA1S's and RYR1's read. The summary shows the label,
and its notes give the allele's name.
"""

from __future__ import annotations

import re

from app.reports.generator import summary_rows

CFTR_NAME = "ivacaftor non-responsive CFTR sequence"


def _tsv(gene, diplotype, phenotype="", candidates=None):
    return {
        "gene": gene,
        "diplotype_display": diplotype,
        "rec_lookup_diplotype": diplotype,
        "rec_lookup_phenotype": phenotype,
        "rec_lookup_activity_score": None,
        "diplotype_candidates": candidates,
    }


def _row(rows, gene):
    return next(r for r in rows if r["gene"] == gene)


def test_cftr_shows_pharmcats_label_and_keeps_the_name():
    rows = summary_rows(
        [
            _tsv(
                "CFTR",
                f"{CFTR_NAME}/{CFTR_NAME}",
                "ivacaftor non-responsive in CF patients",
            )
        ],
        [{"gene": "CFTR", "diplotype": "Reference/Reference"}],
    )
    cftr = _row(rows, "CFTR")
    assert cftr["diplotype"] == "Reference/Reference"
    assert cftr["reference_name"] == CFTR_NAME
    assert cftr["phenotype"] == "ivacaftor non-responsive in CF patients"


def test_other_calls_are_left_as_the_tsv_has_them():
    vkorc1 = "rs9923231 reference (C)/rs9923231 reference (C)"
    rows = summary_rows(
        [
            _tsv("VKORC1", vkorc1),
            # One allele not the reference: PharmCAT's label is not Reference.
            _tsv("CFTR", f"{CFTR_NAME}/G551D", "ivacaftor responsive in CF patients"),
            _tsv("CACNA1S", "Reference/Reference", "Uncertain Susceptibility"),
        ],
        [
            {"gene": "VKORC1", "diplotype": vkorc1},
            {"gene": "CFTR", "diplotype": f"{CFTR_NAME}/G551D"},
            {"gene": "CACNA1S", "diplotype": "Reference/Reference"},
        ],
    )
    assert _row(rows, "VKORC1")["diplotype"] == vkorc1
    assert _row(rows, "CFTR")["diplotype"] == f"{CFTR_NAME}/G551D"
    assert _row(rows, "CACNA1S")["diplotype"] == "Reference/Reference"
    assert all(r["reference_name"] is None for r in rows)


def test_an_unresolved_call_is_never_relabelled():
    rows = summary_rows(
        [_tsv("CFTR", f"{CFTR_NAME}/{CFTR_NAME}", candidates=["a", "b"])],
        [{"gene": "CFTR", "diplotype": "Reference/Reference"}],
    )
    assert _row(rows, "CFTR")["diplotype"] == f"{CFTR_NAME}/{CFTR_NAME}"


def test_the_notes_name_the_allele():
    from app.reports.generator import env

    template = env.get_template("report_template.html")
    rows_tsv = [
        _tsv(
            "CFTR",
            f"{CFTR_NAME}/{CFTR_NAME}",
            "ivacaftor non-responsive in CF patients",
        )
    ]
    page = template.render(
        execsum_from_tsv=rows_tsv,
        diplotypes=[{"gene": "CFTR", "diplotype": "Reference/Reference"}],
        recommendations=[],
        patient_id="p",
        report_id="r",
        report_date="2026-10-12",
        organization="ZaroPGx",
        disclaimer="",
    )
    summary = page[
        page.index('id="exec-summary"') : page.index('id="exec-summary-notes"')
    ]
    notes = page[page.index('id="exec-summary-notes"') :]
    text = re.sub(r"<[^>]+>", "", summary)
    assert CFTR_NAME not in text
    assert "Reference/Reference" in text
    assert f"&ldquo;{CFTR_NAME}&rdquo;" in notes
