"""Each report credits every tool and data source its run used, and only those.

app/reports/credits.py holds them: what each does here, where its version is
recorded, the papers its authors ask to be cited by (DOIs checked against Crossref)
and the notices its terms require. These tests hold the list to its rules and pin
what each kind of run credits.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.reports import credits as c
from app.reports.credits import CREDITS, CreditRun, Paper, ref_numbers, report_credits

ROOT = Path(__file__).resolve().parent.parent

ALL_PAPERS = {
    paper.key: paper
    for credit in CREDITS
    for paper in credit.papers + tuple(p for _, p in credit.papers_when)
}


def _credited(run, versions=None, components=None):
    out = report_credits(run, versions or {}, components or {})
    return {row["key"] for row in out["software"] + out["data"]}


# ---------------------------------------------------------------------------
# The list's own rules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("credit", CREDITS, ids=lambda credit: credit.key)
def test_every_credit_says_what_it_does_where_it_lives_and_its_terms(credit):
    assert credit.kind in ("software", "data")
    assert credit.used_for and not credit.used_for.endswith(".")
    assert credit.url.startswith("https://"), credit.url
    assert credit.licence


@pytest.mark.parametrize("paper", ALL_PAPERS.values(), ids=lambda p: p.key)
def test_every_reference_can_be_found(paper):
    """A DOI wherever one exists; a URL otherwise (books by publisher)."""
    assert paper.doi or paper.url or paper.publisher, paper.title
    if paper.doi:
        assert re.fullmatch(r"10\.\d{4,9}/\S+", paper.doi), paper.doi
    # Page ranges with an en dash, as typeset; never a hyphen between numbers.
    assert not re.search(r":\w*\d+-\d", paper.detail), paper.detail


def test_dois_are_unique():
    dois = [p.doi for p in ALL_PAPERS.values() if p.doi]
    assert len(dois) == len(set(dois))


def test_software_with_a_paper_and_data_cite_their_own():
    """Credits without a paper get a software reference (name, version, URL);
    the knowledge bases and reference data all have a citation of their own."""
    for credit in CREDITS:
        if credit.kind == "data":
            assert credit.papers, credit.key


# ---------------------------------------------------------------------------
# Reference formatting (AMA)
# ---------------------------------------------------------------------------


def test_up_to_six_authors_are_listed():
    assert c.OPTITYPE_2014.text().startswith(
        "Szolek A, Schubert B, Mohr C, Sturm M, Feldhahn M, Kohlbacher O. OptiType:"
    )


def test_more_than_six_become_three_and_et_al():
    assert c.SANGKUHL_2020.text() == (
        "Sangkuhl K, Whirl-Carrillo M, Whaley RM, et al. Pharmacogenomics Clinical "
        "Annotation Tool (PharmCAT). Clinical Pharmacology & Therapeutics. "
        "2020;107(1):203–210. doi:10.1002/cpt.1568"
    )


def test_a_consortium_author():
    assert c.KGP_PHASE3_2015.text().startswith(
        "1000 Genomes Project Consortium; Auton A, Brooks LD, Durbin RM, et al. "
    )
    assert "; PharmVar Steering Committee. The Evolution" in c.PHARMVAR_2019.text()


def test_a_preprint_a_book_a_thesis_and_a_data_set():
    assert (
        "[Preprint]. bioRxiv. 2017:201178. doi:10.1101/201178" in c.POPLIN_2017.text()
    )
    assert c.GATK_BOOK_2020.text() == (
        "Van der Auwera GA, O'Connor BD. Genomics in the Cloud: Using Docker, GATK, "
        "and WDL in Terra. 1st ed. O'Reilly Media; 2020."
    )
    assert (
        "[dissertation]. Freie Universität Berlin; 2015." in c.YARA_THESIS_2015.text()
    )
    assert "[Data set]. Zenodo; 2022. doi:10.5281/zenodo.7288118" in (
        c.PHARMCAT_REFERENCE_2022.text()
    )


def test_html_sets_the_journal_in_italics_and_links_the_doi():
    html = str(c.NEXTFLOW_2017.html())
    assert "<em>Nature Biotechnology</em>. 2017;35(4):316–319." in html
    assert 'doi:<a href="https://doi.org/10.1038/nbt.3820">10.1038/nbt.3820</a>' in html


def test_html_escapes_text():
    paper = Paper(("A B",), "x < y & z", "J", "2020;1:1", doi="10.1/x")
    assert "x &lt; y &amp; z" in str(paper.html())


def test_reference_numbers_are_ranged():
    assert ref_numbers([1, 2, 3, 5]) == "1–3, 5"
    assert ref_numbers([4]) == "4"
    assert ref_numbers([]) == ""


# ---------------------------------------------------------------------------
# What a run credits
# ---------------------------------------------------------------------------

EVERY_JOB = {
    "zaropgx",
    "nextflow",
    "htslib",
    "pysam",
    "pharmcat",
    "grch38",
    "pharmcat_reference",
    "cpic",
    "dpwg",
    "fda",
    "clinpgx",
    "pharmvar",
    "dbsnp",
}


def test_a_vcf_run():
    run = CreditRun(
        steps=frozenset({"header_analysis", "pypgx_analysis", "pharmcat_analysis"}),
        file_type="vcf",
        pypgx={"beagle_genes": 40, "cnv_model_genes": 0},
        kroki=False,
    )
    assert _credited(run) == EVERY_JOB | {
        "pypgx",
        "beagle",
        "kgp_panel",
        "weasyprint",
        "chartjs",
        "d3",
    }


def test_a_phased_vcf_is_not_phased_again():
    """PyPGx skips Beagle for a fully phased input; then neither Beagle nor its
    1000 Genomes panel is credited."""
    run = CreditRun(
        steps=frozenset({"pypgx_analysis"}),
        file_type="vcf",
        pypgx={"beagle_genes": 0, "cnv_model_genes": 0},
    )
    assert not _credited(run) & {"beagle", "kgp_panel", "sklearn"}


def test_a_run_from_before_pypgx_said_credits_beagle():
    """Older runs did not record what PyPGx ran: credit rather than omit."""
    run = CreditRun(steps=frozenset({"pypgx_analysis"}), file_type="vcf", pypgx={})
    assert {"beagle", "kgp_panel"} <= _credited(run)
    assert "sklearn" not in _credited(run)  # a VCF has no depth to model


def test_a_cram_run_with_hla_and_mtdna():
    run = CreditRun(
        steps=frozenset(
            {
                "gatk_cram_sam_to_bam",
                "hla_typing",
                "pypgx_bam2vcf",
                "pypgx_analysis",
                "mtdna_analysis",
                "pharmcat_analysis",
            }
        ),
        file_type="cram",
        pypgx={"beagle_genes": 60, "cnv_model_genes": 13},
    )
    credited = _credited(run)
    assert {
        "fuc",
        "beagle",
        "sklearn",
        "optitype",
        "yara",
        "seqan",
        "glpk",
        "pyomo",
        "imgt_hla",
        "mtdna",
        "mutserve",
        "haplogrep",
        "haplocheck",
        "phylotree",
        "rcrs",
        "ucsc_hg38",
        "kgp_grch38",
        "kroki",
        "mermaid",
    } <= credited
    # Not aligned from FASTQ, not lifted, not a gVCF.
    assert not credited & {"gatk", "bwa", "picard", "ucsc_chain", "grch37"}


def test_a_fastq_run_credits_the_aligner():
    run = CreditRun(steps=frozenset({"gatk_alignment"}), file_type="fastq")
    assert {"gatk", "bwa", "picard", "ucsc_hg38"} <= _credited(run)


def test_a_grch37_vcf_credits_the_liftover_and_its_terms():
    run = CreditRun(steps=frozenset({"liftover"}), file_type="vcf")
    out = report_credits(run, {}, {})
    keys = {row["key"] for row in out["software"] + out["data"]}
    assert {"gatk", "picard", "ucsc_chain", "grch37", "ucsc_hg38"} <= keys
    assert any("UCSC Chain Files" in notice for notice in out["notices"])


def test_gatk_joint_calling_is_cited_only_for_a_gvcf():
    def gatk_refs(steps):
        out = report_credits(CreditRun(steps=frozenset(steps)), {}, {})
        return [r["text"] for r in out["references"] if "Scaling accurate" in r["text"]]

    assert gatk_refs({"gvcf_to_vcf"})
    assert not gatk_refs({"liftover"})


def test_mtdna_from_a_vcf_does_not_credit_read_callers():
    run = CreditRun(steps=frozenset({"mtdna_analysis"}), file_type="vcf")
    credited = _credited(run)
    assert {"mtdna", "haplogrep", "phylotree", "rcrs"} <= credited
    assert not credited & {"mutserve", "haplocheck"}


def test_the_rendering_tools_follow_the_outputs():
    run = CreditRun(steps=frozenset(), pdf=False, interactive=False, kroki=False)
    assert not _credited(run) & {"weasyprint", "chartjs", "d3", "kroki", "mermaid"}


def test_notices_appear_only_with_their_source():
    run = CreditRun(steps=frozenset(), file_type="vcf")
    notices = " ".join(report_credits(run, {}, {})["notices"])
    assert "Attribution-ShareAlike 4.0" in notices  # ClinPGx and PharmVar: always
    assert "CC0 1.0" in notices
    assert "UCSC Chain Files" not in notices
    assert "IPD-IMGT/HLA" not in notices
    assert "New York Genome Center" not in notices


def test_references_are_numbered_once_each():
    out = report_credits(CreditRun(), {}, {})
    texts = [r["text"] for r in out["references"]]
    assert len(texts) == len(set(texts))
    assert [r["n"] for r in out["references"]] == list(range(1, len(texts) + 1))
    # fuc and PyPGx share ClinPharmSeq; one entry, cited by both.
    rows = {row["key"]: row for row in out["software"]}
    assert set(rows["fuc"]["refs"]) <= set(rows["pypgx"]["refs"])


def test_pharmcat_s_methods_paper_is_pointed_to():
    out = report_credits(CreditRun(), {}, {})
    assert "Pharmacogenomics Clinical Annotation Tool (PharmCAT)" in (
        out["references"][out["pharmcat_ref"] - 1]["text"]
    )


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------


def test_versions_come_from_the_services():
    versions = {"gatk": "The Genome Analysis Toolkit () v4.7.0.0", "pypgx": "0.27.0"}
    components = {
        "pypgx": {"beagle": "5.4 (22Jul22.46e)", "pypgx-bundle": "0.27.0"},
        "gatk": {"picard": "3.5.0"},
        "pharmcat": {"bcftools": "1.24"},
        "mtdna-server-2": {"bcftools": "1.19", "phylotree": "phylotree-fu-rcrs@1.3"},
    }
    out = report_credits(CreditRun(), versions, components)
    v = {row["key"]: row["version"] for row in out["software"] + out["data"]}
    assert v["gatk"] == "4.7.0.0"
    assert v["bwa"] == "in GATK 4.7.0.0"
    assert v["picard"] == "3.5.0"
    assert v["beagle"] == "5.4 (22Jul22.46e)"
    assert v["htslib"] == "1.24; 1.19 in mtDNA-Server 2"
    assert v["phylotree"] == "17, Forensic Update 1.3"
    assert v["kgp_panel"] == "30x, phased 2020-10-28 (pypgx-bundle 0.27.0)"
    assert v["imgt_hla"] == "3.13.0, in OptiType"


def test_knowledge_carries_pharmcat_s_data_version():
    run = CreditRun(pharmcat_data_version="2026-07-13-11-40")
    out = report_credits(run, {"pharmcat": "3.4.0"}, {})
    for row in out["data"]:
        if row["key"] in ("cpic", "dpwg", "fda", "clinpgx", "pharmvar", "dbsnp"):
            assert row["version"] == "PharmCAT data 2026-07-13-11-40"


def test_vendored_versions_match_the_files():
    """The interactive report inlines these; their banners carry the version."""
    vendor = ROOT / "app" / "static" / "vendor"
    chart = next((vendor / "chartjs").glob("*.js")).read_text(encoding="utf-8")[:300]
    d3 = next((vendor / "d3").glob("*.js")).read_text(encoding="utf-8")[:300]
    assert f"Chart.js v{c.VENDORED['chart.js']}" in chart
    assert f"d3js.org v{c.VENDORED['d3']}" in d3


# ---------------------------------------------------------------------------
# Every report renders them
# ---------------------------------------------------------------------------


def test_both_templates_include_the_credits():
    templates = ROOT / "app" / "reports" / "templates"
    for name in ("report_template.html", "interactive_report.html"):
        text = (templates / name).read_text(encoding="utf-8")
        assert '{% include "credits_section.html" %}' in text, name
