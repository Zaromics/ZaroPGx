"""Every phenotype the stack can report gets an Implications line of its own.

The gene tables' Implications column matched words in the phenotype: metabolizer
phenotypes had lines, everything else read "Consult clinical guidelines" (SLCO1B1
*1/*15, Decreased Function, on the NA12878 demo), and SLCO1B1's, ABCG2's and
G6PD's "Normal" read "Standard drug metabolism expected". app/reports/
implications.py now says what each phenotype means, as CPIC defines the term,
and points to the drug recommendations.
"""

from __future__ import annotations

import csv
import json
import re
from html import unescape
from pathlib import Path

import pytest

import app.reports.generator as generator_module
from app.reports.implications import (
    FALLBACK,
    NOT_CALLED,
    NOT_DETERMINED,
    SHARED,
    TRANSPORTER_GENES,
    UNRESOLVED,
    phenotype_implication,
    row_implication,
)

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "test_data"

# PharmCAT 3.4.0's gene results, every "generesult" in its phenotype data
# (org/pharmgkb/pharmcat/phenotype/<GENE>.json in the jar of
# zaromicsresearch/zaropgx-pharmcat), by gene.
PHARMCAT_340 = {
    "ABCG2": ["Normal Function", "Decreased Function", "Poor Function"],
    "CACNA1S": ["Uncertain Susceptibility", "Malignant Hyperthermia Susceptibility"],
    "CFTR": [
        "ivacaftor responsive in CF patients",
        "ivacaftor non-responsive in CF patients",
    ],
    "CYP2B6": [
        "Normal Metabolizer",
        "Indeterminate",
        "Rapid Metabolizer",
        "Intermediate Metabolizer",
        "Ultrarapid Metabolizer",
        "Poor Metabolizer",
    ],
    "CYP2C19": [
        "Normal Metabolizer",
        "Intermediate Metabolizer",
        "Likely Intermediate Metabolizer",
        "Indeterminate",
        "Rapid Metabolizer",
        "Poor Metabolizer",
        "Likely Poor Metabolizer",
        "Ultrarapid Metabolizer",
    ],
    "CYP2C9": [
        "Normal Metabolizer",
        "Intermediate Metabolizer",
        "Indeterminate",
        "Poor Metabolizer",
    ],
    "CYP2D6": [
        "Normal Metabolizer",
        "Ultrarapid Metabolizer",
        "Intermediate Metabolizer",
        "Indeterminate",
        "Poor Metabolizer",
    ],
    "CYP3A4": ["Normal Metabolizer", "Intermediate Metabolizer", "Poor Metabolizer"],
    "CYP3A5": [
        "Normal Metabolizer",
        "Intermediate Metabolizer",
        "Possible Intermediate Metabolizer",
        "Poor Metabolizer",
        "Indeterminate",
    ],
    "DPYD": ["Normal Metabolizer", "Intermediate Metabolizer", "Poor Metabolizer"],
    "G6PD": [
        "Deficient with CNSHA",
        "Normal",
        "Deficient",
        "Indeterminate",
        "Variable",
    ],
    "MT-RNR1": [
        "normal risk of aminoglycoside-induced hearing loss",
        "uncertain risk of aminoglycoside-induced hearing loss",
        "increased risk of aminoglycoside-induced hearing loss",
    ],
    "NAT2": [
        "Rapid Metabolizer",
        "Intermediate Metabolizer",
        "Indeterminate",
        "Poor Metabolizer",
    ],
    "NUDT15": [
        "Normal Metabolizer",
        "Intermediate Metabolizer",
        "Indeterminate",
        "Poor Metabolizer",
        "Possible Intermediate Metabolizer",
    ],
    "RYR1": ["Uncertain Susceptibility", "Malignant Hyperthermia Susceptibility"],
    "SLCO1B1": [
        "Normal Function",
        "Indeterminate",
        "Decreased Function",
        "Possible Decreased Function",
        "Poor Function",
        "Increased Function",
    ],
    "TPMT": [
        "Normal Metabolizer",
        "Intermediate Metabolizer",
        "Indeterminate",
        "Poor Metabolizer",
        "Possible Intermediate Metabolizer",
    ],
    "UGT1A1": [
        "Normal Metabolizer",
        "Intermediate Metabolizer",
        "Indeterminate",
        "Poor Metabolizer",
    ],
    # HLA: PharmCAT's result for each allele it screens, as the report joins them.
    "HLA-A": ["*31:01 negative", "*31:01 positive"],
    "HLA-B": [
        "*57:01 negative",
        "*57:01 positive",
        "*15:02 negative, *57:01 negative, *58:01 negative",
        "*15:02 negative, *57:01 positive, *58:01 negative",
        "*15:02 positive, *57:01 positive, *58:01 positive",
    ],
}

# PyPGx 0.27's phenotypes beyond its phenotype table: the MT-RNR1 spellings in
# pypgx/api/data/diplotype-table.csv.
PYPGX_027_EXTRA = {
    "MT-RNR1": [
        "Normal Risk of Aminoglycoside-Induced Hearing Loss",
        "Increased Risk of Aminoglycoside-Induced Hearing Loss",
        "Uncertain Risk of Aminoglycoside-Induced Hearing Loss",
    ]
}

# The report's own Phenotype values (generator.py).
REPORT_LABELS = [
    "Possibly Wild Type",
    "Likely Wild Type",
    "N/A",
    "Unknown",
    "",
    None,
]


def _lexicon_pharmcat():
    """lexicon/phenotypes.tsv: PharmCAT's own phenotype list."""
    with open(REPO / "lexicon" / "phenotypes.tsv", encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            for column in ("CPIC Phenotypes", "DPWG Phenotypes"):
                for phenotype in (row.get(column) or "").split(";"):
                    if phenotype.strip():
                        yield row["Gene"], phenotype.strip()


def _lexicon_pypgx():
    """lexicon/phenotype-table.csv: PyPGx 0.27's phenotype table."""
    with open(REPO / "lexicon" / "phenotype-table.csv", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            yield row["Gene"], row["Phenotype"].strip()


def _every_phenotype():
    for source in (PHARMCAT_340, PYPGX_027_EXTRA):
        for gene, phenotypes in source.items():
            for phenotype in phenotypes:
                yield gene, phenotype
    yield from _lexicon_pharmcat()
    yield from _lexicon_pypgx()


EVERY = sorted(set(_every_phenotype()))


def test_the_audited_sets_are_all_here():
    genes = {gene for gene, _ in EVERY}
    assert {"SLCO1B1", "ABCG2", "G6PD", "HLA-B", "MT-RNR1", "CFTR", "RYR1"} <= genes
    assert len(EVERY) > 100


@pytest.mark.parametrize("gene,phenotype", EVERY)
def test_every_phenotype_has_its_own_line(gene, phenotype):
    line = phenotype_implication(phenotype, gene)
    assert line and line != FALLBACK
    assert "Consult clinical guidelines" not in line
    # A metabolizer phenotype in metabolism wording; nothing else in it.
    if "metabolizer" in phenotype.lower():
        assert "metabolism" in line, line
    else:
        assert "metaboli" not in line, line


@pytest.mark.parametrize("phenotype", REPORT_LABELS)
def test_the_reports_own_labels_have_their_own_line(phenotype):
    line = phenotype_implication(phenotype, "CYP4F2")
    assert line and line != FALLBACK
    assert "metaboli" not in line


def test_the_demo_slco1b1_call_reads_as_a_transporter():
    # NA12878 demo: SLCO1B1 *1/*15, Decreased Function.
    line = phenotype_implication("Decreased Function", "SLCO1B1")
    assert line == (
        "Decreased transporter function, between normal and poor; see recommendations"
    )


@pytest.mark.parametrize("gene", sorted(TRANSPORTER_GENES))
@pytest.mark.parametrize(
    "phenotype",
    [
        "Normal Function",
        "Decreased Function",
        "Possible Decreased Function",
        "Poor Function",
        "Increased Function",
    ],
)
def test_function_phenotypes_are_a_transporters(gene, phenotype):
    line = phenotype_implication(phenotype, gene)
    assert "transporter function" in line
    assert "metaboli" not in line


def test_a_function_phenotype_of_another_gene_names_no_transporter():
    line = phenotype_implication("Decreased Function", "XYZ1")
    assert line.startswith("Decreased function") and "transporter" not in line


def test_cpic_terms():
    """Each line states the term's CPIC meaning (Caudle et al. 2017)."""
    say = phenotype_implication
    assert say("Normal Metabolizer", "CYP2C19") == "Standard drug metabolism expected"
    assert "between normal and poor" in say("Intermediate Metabolizer", "DPYD")
    assert "Little to no" in say("Poor Metabolizer", "CYP2D6")
    assert "above normal" in say("Rapid Metabolizer", "CYP2C19")
    assert "above a rapid" in say("Ultrarapid Metabolizer", "CYP2D6")
    assert "Likely" in say("Likely Poor Metabolizer", "CYP2C19")
    # CPIC's "possible intermediate" by gene (api.cpicpgx.org gene_result).
    for gene in ("TPMT", "NUDT15"):
        assert say("Possible Intermediate Metabolizer", gene) == (
            "Reduced drug metabolism, no higher than intermediate and possibly lower;"
            " see recommendations"
        )
    assert say("Possible Intermediate Metabolizer", "CYP3A5") == (
        "Possibly reduced drug metabolism (one allele's function uncertain or"
        " unknown); see recommendations"
    )
    assert say("Possible Decreased Function", "SLCO1B1").startswith(
        "Possibly decreased transporter function (one no-function allele, one of"
        " uncertain or unknown function)"
    )
    assert say("increased risk of aminoglycoside-induced hearing loss").startswith(
        "Variant conferring increased risk of aminoglycoside-induced hearing loss"
    )
    assert "Little to no transporter function" in say("Poor Function", "SLCO1B1")
    assert say("Normal", "G6PD") == "Normal G6PD activity expected"
    assert say("G6PD Normal", "G6PD") == say("Normal", "G6PD")
    assert "normal to deficient" in say("Variable", "G6PD")
    assert "CNSHA" in say("Deficient with CNSHA", "G6PD")
    assert "uncertain or unknown" in say("Indeterminate", "CYP2C9")
    assert "not ruled out" in say("Uncertain Susceptibility", "RYR1")


def test_hla_results():
    say = phenotype_implication
    assert say("*31:01 negative", "HLA-A") == "High-risk allele not detected"
    assert say("*15:02 negative, *57:01 negative, *58:01 negative", "HLA-B") == (
        "None of the high-risk alleles screened for was detected"
    )
    line = say("*15:02 negative, *57:01 positive, *58:01 negative", "HLA-B")
    assert line.startswith("High-risk allele detected (*57:01)")
    # Not an HLA result, so not read as one.
    assert say("*57:01 negative, something else", "HLA-B") == FALLBACK


def test_no_consequence_of_its_own():
    """No line names a dose, a drug or an outcome: the recommendations do."""
    for gene, phenotype in EVERY:
        line = phenotype_implication(phenotype, gene).lower()
        for claim in ("dose", "efficacy", "monitor", "adverse", "avoid"):
            assert claim not in line, (gene, phenotype, line)


def test_a_gene_not_called():
    for diplotype in ("Unknown/Unknown", "Unknown", ""):
        row = {"gene": "CYP2C9", "diplotype": diplotype, "phenotype": "Unknown"}
        assert row_implication(row) == NOT_CALLED
    # Not "no concern": nothing is known.
    assert NOT_CALLED == "Not called: phenotype unknown, so no genotype-based guidance"
    row = {"gene": "CYP2C9", "diplotype": "*1/*2", "phenotype": "Unknown"}
    assert row_implication(row) == phenotype_implication("Unknown")


def test_pypgx_response_categories_by_gene():
    """PyPGx 0.27's diplotype table: CFTR, F5 and IFNL3."""
    say = phenotype_implication
    assert say("Favorable Response", "IFNL3").startswith(
        "rs12979860 CC, CPIC's favorable response genotype"
    )
    assert say("Unfavorable Response", "IFNL3").startswith("rs12979860 T carried")
    assert say("Favorable Response", "F5") == "Factor V Leiden not detected"
    assert say("Unfavorable Response", "F5").startswith("Factor V Leiden detected")
    assert say("Favorable Response", "CFTR").startswith("Ivacaftor-responsive")
    assert "F508del on both copies" in say("Unfavorable Response", "CFTR")


def test_pypgx_genotype_not_determined():
    row = {"gene": "CYP2D6", "diplotype": "Indeterminate", "phenotype": "Indeterminate"}
    assert row_implication(row) == NOT_DETERMINED


def test_an_unresolved_call():
    """Candidates with different phenotypes, and candidates that share one
    (report_json gives such a call that one phenotype)."""
    assert phenotype_implication("Normal Function or Poor Function", "SLCO1B1") == (
        UNRESOLVED
    )
    assert "different phenotypes" in UNRESOLVED
    candidates = [{"diplotype": "*1/*6"}, {"diplotype": "*4/*9"}]
    row = {
        "gene": "CYP2B6",
        "phenotype": "Intermediate Metabolizer",
        "diplotype_candidates": candidates,
    }
    assert row_implication(row) == (
        "Every diplotype that fits has this phenotype: reduced drug metabolism,"
        " between normal and poor; see recommendations"
    )
    row["phenotype"] = "Intermediate Metabolizer or Poor Metabolizer"
    assert row_implication(row) == UNRESOLVED


def test_the_unphased_example(monkeypatch, tmp_path):
    """CYP2B6 "*1/*6 or 14 other possible diplotypes", every one Intermediate;
    SLCO1B1's candidates are Decreased or Poor Function."""
    page = _page(monkeypatch, tmp_path, "pharmcat.unphased.example.report.json", "html")
    start = page.index('class="gene-table"')
    rows = _implications(page[start : page.index("</table>", start)])
    assert rows["CYP2B6"].startswith(SHARED + "reduced drug metabolism")
    assert rows["SLCO1B1"] == UNRESOLVED


def _page(monkeypatch, tmp_path, name, kind):
    for key in list(generator_module.REPORT_CONFIG):
        if key.startswith(("write_", "show_")):
            monkeypatch.setitem(
                generator_module.REPORT_CONFIG,
                key,
                key == f"write_{kind}" or key.startswith("show_"),
            )
    result = generator_module.generate_report(
        {"data": json.loads((DATA / name).read_text(encoding="utf-8"))},
        str(tmp_path),
        {"id": "p1"},
    )
    path = result[f"{kind}_path"]
    return (tmp_path / path.split("/")[-1]).read_text(encoding="utf-8")


def _implications(table_html):
    rows = {}
    for row in re.findall(r"<tr>(.*?)</tr>", table_html, re.S):
        cells = [
            re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", c))).strip()
            for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        ]
        if len(cells) == 6:
            rows[cells[0]] = cells[4]
    return rows


@pytest.mark.parametrize("name", ["pharmcat.example.v340.report.json"])
def test_the_html_gene_table(monkeypatch, tmp_path, name):
    page = _page(monkeypatch, tmp_path, name, "html")
    start = page.index('class="gene-table"')
    rows = _implications(page[start : page.index("</table>", start)])
    assert rows, "no gene table rows"
    for gene, line in rows.items():
        assert line and "Consult clinical guidelines" not in line, (gene, line)
    assert "metaboli" not in rows["SLCO1B1"] and "transporter" in rows["SLCO1B1"]
    assert "metaboli" not in rows["ABCG2"] and "transporter" in rows["ABCG2"]


def test_the_interactive_genes_table_and_panels(monkeypatch, tmp_path):
    page = _page(
        monkeypatch, tmp_path, "pharmcat.example.v340.report.json", "interactive_html"
    )
    genes = page[page.index('<div id="genes"') :]
    rows = _implications(genes[: genes.index("</table>")])
    assert rows
    for gene, line in rows.items():
        assert line and "Consult clinical guidelines" not in line, (gene, line)
    assert "transporter" in rows["SLCO1B1"]
    # The gene panels read the same line from the data element.
    found = dict(
        re.findall(r'data-gene="([^"]+)"[^>]*?data-implication="([^"]*)"', page, re.S)
    )
    assert unescape(found["SLCO1B1"]) == rows["SLCO1B1"]
