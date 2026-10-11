"""A reference call is labelled wild type however PharmCAT names the alleles.

PharmCAT 3.4 names a one-position gene's reference allele by its position and
base: VKORC1 "rs9923231 reference (C)", F2 "rs1799963 reference (G)", F5 "rs6025
reference (C)". The wild-type rule knew only "*1/*1" and "Reference/Reference", so
from a VCF the report labelled CYP4F2 *1/*1 "Possibly Wild Type" and VKORC1's
reference call, beside it, "N/A". Reports before October 2026 printed VKORC1, F2 and
F5 as Reference/Reference and labelled all four alike.
"""

from __future__ import annotations

import re

import pytest

from app.reports.generator import wild_type_phenotype

VKORC1 = "rs9923231 reference (C)/rs9923231 reference (C)"
F5 = "rs6025 reference (C)/rs6025 reference (C)"


@pytest.mark.parametrize("diplotype", [VKORC1, F5, VKORC1.replace("/", " / ")])
@pytest.mark.parametrize("phenotype", ["", " ", "n/a", None])
def test_a_named_reference_call_is_labelled(diplotype, phenotype):
    assert wild_type_phenotype(diplotype, phenotype, "vcf") == "Possibly Wild Type"
    assert wild_type_phenotype(diplotype, phenotype, "bam") == "Likely Wild Type"
    assert wild_type_phenotype(diplotype, phenotype, "unknown") is None


@pytest.mark.parametrize(
    "diplotype, phenotype",
    [
        # A variant allele: IFNL3 as PharmCAT calls it.
        ("rs12979860 reference (C)/rs12979860 variant (T)", ""),
        # A phenotype of its own is the phenotype: ABCG2.
        ("rs2231142 reference (G)/rs2231142 reference (G)", "Normal Function"),
        ("rs9923231 reference (C)", ""),
        (f"{VKORC1}/rs9923231 reference (C)", ""),
    ],
)
def test_anything_else_is_left_alone(diplotype, phenotype):
    assert wild_type_phenotype(diplotype, phenotype, "vcf") is None


GENES = [
    {"gene": "CYP4F2", "diplotype": "*1/*1", "phenotype": ""},
    {"gene": "VKORC1", "diplotype": VKORC1, "phenotype": ""},
]


@pytest.mark.parametrize(
    "file_type, label", [("vcf", "Possibly Wild Type"), ("bam", "Likely Wild Type")]
)
def test_vkorc1_is_labelled_as_cyp4f2_is(monkeypatch, tmp_path, file_type, label):
    import app.reports.generator as generator_module

    for key in list(generator_module.REPORT_CONFIG):
        if key.startswith(("write_", "show_")):
            monkeypatch.setitem(
                generator_module.REPORT_CONFIG, key, key == "write_html"
            )
    result = generator_module.generate_report(
        {"data": {"genes": GENES, "drugRecommendations": [], "file_type": file_type}},
        str(tmp_path),
        {"id": "p1"},
        job_id=None,
    )
    phenotypes = {g["gene"]: g["phenotype"] for g in result["processed_data"]["genes"]}
    assert phenotypes["VKORC1"] == phenotypes["CYP4F2"] == label
    page = (tmp_path / result["html_path"].split("/")[-1]).read_text(encoding="utf-8")
    summary = page[
        page.index('id="exec-summary"') : page.index("<h2>Genetic Results</h2>")
    ]
    for gene in ("CYP4F2", "VKORC1"):
        assert re.search(
            rf'exec-summary-gene">{gene}</td>.*?exec-summary-phenotype">\s*'
            rf'<div class="phenotype-line">\s*<span class="gene-phenotype [a-z-]+">{label}',
            summary,
            re.S,
        ), gene
