"""Everything PyPGx reports for a gene reaches the report, not only the genotype.

PyPGx writes each gene's call to <gene>-pipeline/results.zip: Genotype, Phenotype,
Haplotype1/2 (candidate alleles), AlternativePhase, VariantData (the variants
behind each allele, with allele fractions) and CNV. The wrapper read genotypes.zip
first and stopped, so only the genotype arrived; a second path in generator.py that
scraped the pipeline folders had been dead since reports moved into per-job
folders, and read the wrong columns when it ran. On 30x NA12878 the report lost
CYP2D6's "Tandem1A" and GSTM1's "WholeDel1" copy-number calls and every allele's
supporting variants.

The rows below are NA12878's, from PyPGx 0.27 on the 30x BAM.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

import app.reports.generator as generator_module
from app.reports.generator import copy_number_summary
from tests.test_sidecar_chunked_io import (
    PYPGX_SOURCE,
    _close_module_log_handlers,
    _import_sidecar,
)

HEADER = (
    "\tGenotype\tPhenotype\tHaplotype1\tHaplotype2\tAlternativePhase\tVariantData\tCNV"
)
CYP2D6_ROW = (
    "NA12878\t*3/*68+*4\tPoor Metabolizer\t*3;\t*4;*10;*74;\t;\t"
    "*3:22-42128241-CT-C:0.417;*4:22-42128945-C-T:0.425;"
    "*10:22-42130692-G-A,22-42126611-C-G:0.617,0.476;*74:22-42129819-G-T:0.35;\tTandem1A"
)
GSTM1_ROW = (
    "NA12878\t*0/*B\tIndeterminate\t*B;\t*B;\t;\t*B:1-109690516-G-C:1.0;\tWholeDel1"
)
ABCB1_ROW = (
    "NA12878\t*1/*2\tIndeterminate\t*2;\t*1;\t;\t"
    "*2:default;*1:7-87550285-A-G,7-87509329-A-G,7-87531302-A-C:1.0,0.52,1.0;\t"
)


def _archive(path, header, row):
    """A PyPGx archive: <uuid>/data.tsv plus metadata, as pypgx writes it."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("0f1e2d3c/metadata.txt", "SemanticType=SampleTable[Results]\n")
        zf.writestr("0f1e2d3c/data.tsv", f"{header}\n{row}\n")
    path.write_bytes(buffer.getvalue())


@pytest.fixture(scope="module")
def pypgx_api(tmp_path_factory):
    root = tmp_path_factory.mktemp("pypgx_full_record")
    before = list(logging.root.handlers)
    with pytest.MonkeyPatch.context() as mp:
        module = _import_sidecar(
            "zaropgx_pypgx_full_record_test", PYPGX_SOURCE, mp, root
        )
        yield module
    _close_module_log_handlers(module, before)


@pytest.fixture
def pipelines(tmp_path, pypgx_api, monkeypatch):
    # PyPGx's gene table: CYP2D6 has a phenotype rule, GSTM1 and ABCB1 none.
    monkeypatch.setattr(
        pypgx_api,
        "_PHENOTYPE_METHODS",
        {"CYP2D6": "Score", "GSTM1": None, "ABCB1": None},
    )
    for gene, row in (
        ("CYP2D6", CYP2D6_ROW),
        ("GSTM1", GSTM1_ROW),
        ("ABCB1", ABCB1_ROW),
    ):
        pipeline = tmp_path / f"{gene}-pipeline"
        pipeline.mkdir()
        _archive(pipeline / "results.zip", HEADER, row)
        _archive(
            pipeline / "genotypes.zip",
            "\tGenotype",
            row.split("\t")[0] + "\t" + row.split("\t")[1],
        )
    return tmp_path


def test_the_gene_table_s_n_a_is_not_a_method(pypgx_api, tmp_path):
    """PyPGx's gene-table.csv writes "N/A" for genes without a method -- 75 of its
    93 -- so GSTM1's "Indeterminate" must not pass for a phenotype. Rows abridged
    from pypgx 0.27's table (the exon columns dropped)."""
    table = tmp_path / "gene-table.csv"
    table.write_text(
        "Gene,Target,Control,Paralog,Variants,SV,PhenotypeMethod,RefAllele\n"
        "CYP2D6,TRUE,FALSE,CYP2D7,TRUE,TRUE,Score,*1\n"
        "GSTM1,TRUE,FALSE,N/A,TRUE,TRUE,N/A,*A\n"
        "IFNL3,TRUE,FALSE,N/A,TRUE,FALSE,Diplotype,Reference\n"
    )
    assert pypgx_api.load_phenotype_methods(table) == {
        "CYP2D6": "Score",
        "GSTM1": None,
        "IFNL3": "Diplotype",
    }


def test_the_whole_record_is_read(pypgx_api, pipelines):
    genotype, details = pypgx_api.parse_pypgx_results(
        pipelines / "CYP2D6-pipeline", "CYP2D6"
    )
    assert genotype == "*3/*68+*4"
    assert details["phenotype"] == "Poor Metabolizer"
    assert details["copy_number"] == "Tandem1A"
    assert details["haplotype1"] == ["*3"]
    assert details["haplotype2"] == ["*4", "*10", "*74"]
    assert "alternative_phase" not in details  # ";" alone: none
    star3 = details["supporting_variants"][0]
    assert star3 == {
        "allele": "*3",
        "variants": [{"variant": "22-42128241-CT-C", "allele_fraction": 0.417}],
    }
    star10 = next(a for a in details["supporting_variants"] if a["allele"] == "*10")
    # By position, not PyPGx's set order (22-42130692 was listed first).
    assert [v["variant"] for v in star10["variants"]] == [
        "22-42126611-C-G",
        "22-42130692-G-A",
    ]
    assert [v["allele_fraction"] for v in star10["variants"]] == [0.476, 0.617]


def test_no_phenotype_rule_means_no_phenotype(pypgx_api, pipelines):
    """PyPGx writes "Indeterminate" for a gene it has no phenotype rule for."""
    genotype, details = pypgx_api.parse_pypgx_results(
        pipelines / "GSTM1-pipeline", "GSTM1"
    )
    assert genotype == "*0/*B"
    assert "phenotype" not in details and details["phenotype_method"] is None
    assert details["copy_number"] == "WholeDel1"


def test_a_default_allele_has_no_supporting_variants(pypgx_api, pipelines):
    _, details = pypgx_api.parse_pypgx_results(pipelines / "ABCB1-pipeline", "ABCB1")
    assert [a["allele"] for a in details["supporting_variants"]] == ["*1"]
    assert "copy_number" not in details  # empty CNV column: no call


def test_a_genotype_only_vcf_has_no_allele_fractions(pypgx_api, tmp_path):
    """A VCF with GT but no AD gets "nan" fractions from PyPGx. Parsed as a float
    NaN it made the /genotype response unencodable (Starlette refuses NaN), so the
    job lost every PyPGx call: the GRCh37 liftover e2e lane lost CYP2D6 *1/*4. Row
    from PyPGx 0.27 on that lane's lifted VCF."""
    pipeline = tmp_path / "CYP2D6-pipeline"
    pipeline.mkdir()
    _archive(
        pipeline / "results.zip",
        HEADER,
        "SAMPLE1\t*1/*4\tIntermediate Metabolizer\t*1;\t*4;\t;\t"
        "*4:22-42128945-C-T:nan;*1:default;\t",
    )
    genotype, details = pypgx_api.parse_pypgx_results(pipeline, "CYP2D6")
    assert genotype == "*1/*4"
    assert details["supporting_variants"] == [
        {
            "allele": "*4",
            "variants": [{"variant": "22-42128945-C-T", "allele_fraction": None}],
        }
    ]
    json.dumps(details, allow_nan=False)  # what the response encoder requires


def test_genotypes_zip_is_the_fallback(pypgx_api, pipelines):
    (pipelines / "CYP2D6-pipeline" / "results.zip").unlink()
    genotype, details = pypgx_api.parse_pypgx_results(
        pipelines / "CYP2D6-pipeline", "CYP2D6"
    )
    assert (genotype, details) == ("*3/*68+*4", {})


def test_profile_plots_go_into_the_job_report_folder(pypgx_api, tmp_path, monkeypatch):
    monkeypatch.setattr(pypgx_api, "REPORT_DIR", tmp_path / "reports")
    pipeline = tmp_path / "CYP2D6-pipeline"
    for folder in ("copy-number-profile", "allele-fraction-profile"):
        (pipeline / folder).mkdir(parents=True)
        (pipeline / folder / "NA12878.png").write_bytes(b"\x89PNG " + folder.encode())

    plots = pypgx_api.save_profile_plots(pipeline, "CYP2D6", "patient-1", "job-1")

    assert plots == {
        "copy_number": "pypgx/CYP2D6.copy-number-profile.png",
        "allele_fraction": "pypgx/CYP2D6.allele-fraction-profile.png",
    }
    saved = tmp_path / "reports" / "patient-1" / "job-1" / plots["copy_number"]
    assert saved.read_bytes() == b"\x89PNG copy-number-profile"
    # An id that is not a plain token never becomes a path component.
    assert pypgx_api.save_profile_plots(pipeline, "CYP2D6", "../x", "job-1") == {}
    assert pypgx_api.save_profile_plots(pipeline, "CYP2D6", "patient-1", None) == {}


@pytest.mark.parametrize(
    "code, summary",
    [
        ("Normal", "normal copy number"),
        ("WholeDel1", "whole-gene deletion on one chromosome"),
        ("WholeDel1Hom", "whole-gene deletion on both chromosomes"),
        ("WholeDup1", "whole-gene duplication on one chromosome"),
        ("WholeMultip1", "whole-gene multiplication (two or more extra copies)"),
        ("Tandem1A", "tandem arrangement (see diplotype)"),
        ("Hybrid2Hom", "hybrid gene on both chromosomes"),
        ("ParalogWholeDel1", "whole deletion of the paralog"),
        ("PartialDel1", "partial-gene deletion on one chromosome"),
        ("NoncodingDel1Hom", "deletion of non-coding sequence on both chromosomes"),
        ("Unknown2", "unclassified copy-number pattern"),
        ("Male", "one X chromosome"),
        (
            "WholeDel1+Tandem1A",
            "whole-gene deletion on one chromosome + tandem arrangement (see diplotype)",
        ),
        ("", None),
    ],
)
def test_copy_number_summary(code, summary):
    assert copy_number_summary(code) == summary


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------

PYPGX_RESULTS = {
    "results": {
        "CYP2D6": {
            "success": True,
            "diplotype": "*3/*68+*4",
            "details": {
                "phenotype_method": "Score",
                "phenotype": "Poor Metabolizer",
                "haplotype1": ["*3"],
                "haplotype2": ["*4", "*10", "*74"],
                "copy_number": "Tandem1A",
                "supporting_variants": [
                    {
                        "allele": "*3",
                        "variants": [
                            {"variant": "22-42128241-CT-C", "allele_fraction": 0.417}
                        ],
                    }
                ],
                "plots": {"copy_number": "pypgx/CYP2D6.copy-number-profile.png"},
            },
        },
        "GSTM1": {
            "success": True,
            "diplotype": "*0/*B",
            "details": {"phenotype_method": None, "copy_number": "WholeDel1"},
        },
        "ABCB1": {
            "success": True,
            "diplotype": "*1/*2",
            "details": {"phenotype_method": None, "alternative_phase": ["*3"]},
        },
        # A gene PyPGx has a phenotype rule for, here without a PharmCAT row
        # (PharmCAT reported nothing for it): PyPGx's phenotype is the one shown.
        "CYP2B6": {
            "success": True,
            "diplotype": "*1/*6",
            "details": {
                "phenotype_method": "Diplotype",
                "phenotype": "Intermediate Metabolizer",
            },
        },
        "UGT2B17": {
            "success": True,
            "diplotype": "*1/*1",
            "details": {"phenotype_method": None},
        },
    }
}
# PharmCAT's row for CYP2D6, an outside call: PyPGx's diplotype in PharmCAT's spelling.
# VKORC1 is a gene PharmCAT calls and assigns no phenotype to.
VKORC1_PHARMCAT = {
    "gene": "VKORC1",
    "diplotype": "rs9923231 reference (C)/rs9923231 variant (T)",
    "phenotype": "",
    "call_source": "MATCHER",
}
CYP2D6_PHARMCAT = {
    "gene": "CYP2D6",
    "diplotype": "*3/*68 + *4",
    "phenotype": "Poor Metabolizer",
    "activity_score": 0.0,
    "call_source": "OUTSIDE",
}


def _render(monkeypatch, tmp_path, genes, lane, pypgx=PYPGX_RESULTS):
    for key in (
        "write_pdf",
        "write_html",
        "write_interactive_html",
        "write_json",
        "write_tsv",
        "write_workflow_svg",
        "write_workflow_png",
        "show_pharmcat_html_report",
        "show_pharmcat_json_report",
        "show_pharmcat_tsv_report",
    ):
        monkeypatch.setitem(generator_module.REPORT_CONFIG, key, key == lane)
    (tmp_path / "job_pypgx_results.json").write_text(json.dumps(pypgx))
    result = generator_module.generate_report(
        {"data": {"genes": genes, "drugRecommendations": []}},
        str(tmp_path),
        {"id": "p1"},
        job_id=None,
    )
    rows = {g["gene"]: g for g in result["processed_data"]["genes"] if g.get("gene")}
    key = "html_path" if lane == "write_html" else "interactive_html_path"
    page = (tmp_path / result[key].split("/")[-1]).read_text(encoding="utf-8")
    return rows, page


def _gene_row(page, gene):
    """The cells of one gene's row in the gene table, as text."""
    for tr in re.findall(r"<tr>(.*?)</tr>", page, re.S):
        cells = [
            re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c)).strip()
            for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        ]
        if cells and cells[0] == gene:
            return cells
    raise AssertionError(f"no {gene} row")


@pytest.mark.parametrize("lane", ["write_html", "write_interactive_html"])
def test_the_gene_table_carries_pypgx_s_record(monkeypatch, tmp_path, lane):
    rows, page = _render(
        monkeypatch, tmp_path, [dict(CYP2D6_PHARMCAT), dict(VKORC1_PHARMCAT)], lane
    )

    gstm1 = _gene_row(page, "GSTM1")
    assert gstm1[2] == "N/A"  # no phenotype rule: N/A, not "Unknown"
    assert "Copy number: whole-gene deletion on one chromosome (WholeDel1)" in gstm1[1]
    assert _gene_row(page, "CYP2B6")[2] == "Intermediate Metabolizer"  # PyPGx's own
    # PharmCAT's no-phenotype genes read the same as PyPGx's, in both views (it
    # was a blank in one and "Unknown" in the other).
    assert _gene_row(page, "VKORC1")[2] == "N/A"
    # A reference call keeps its wild-type label rather than N/A.
    assert _gene_row(page, "UGT2B17")[2] == "Possibly Wild Type"
    assert "would also find *3" in _gene_row(page, "ABCB1")[1]
    assert "defines no phenotype for it" in page

    # PharmCAT's CYP2D6 row is PyPGx's call: PyPGx's evidence and Call letter,
    # PharmCAT's phenotype.
    cyp2d6 = _gene_row(page, "CYP2D6")
    assert "Tandem1A" in cyp2d6[1] and cyp2d6[2] == "Poor Metabolizer"
    assert rows["CYP2D6"]["called_by"] == "P"


def test_n_a_only_where_the_calling_tool_defines_no_phenotype(monkeypatch, tmp_path):
    """A blank or "Unknown" phenotype elsewhere is missing data, and the N/A legend
    ("the tool defines no phenotype") would be false there."""
    cyp2c19 = {
        "gene": "CYP2C19",
        "diplotype": "*1/*2",
        "phenotype": "Unknown",
        "call_source": "MATCHER",
    }
    pypgx = {
        "results": {
            # PyPGx has a phenotype rule for it but reported none.
            "CYP2A6": {
                "success": True,
                "diplotype": "*1/*9",
                "details": {"phenotype_method": "Diplotype"},
            },
            # PyPGx could not call it at all.
            "CYP2E1": {
                "success": True,
                "diplotype": "Indeterminate",
                "details": {"phenotype_method": None, "copy_number": "Unknown2"},
            },
            "GSTM1": PYPGX_RESULTS["results"]["GSTM1"],
        }
    }
    rows, _ = _render(
        monkeypatch,
        tmp_path,
        [cyp2c19, dict(VKORC1_PHARMCAT)],
        "write_html",
        pypgx=pypgx,
    )
    assert rows["CYP2C19"]["phenotype"] != "N/A"
    assert rows["CYP2A6"]["phenotype"] != "N/A"
    assert rows["CYP2E1"]["phenotype"] != "N/A"
    assert rows["VKORC1"]["phenotype"] == "N/A"
    assert rows["GSTM1"]["phenotype"] == "N/A"


def test_the_executive_summary_says_n_a_too(monkeypatch, tmp_path):
    """EXECSUM_USE_TSV (on in .env.example) builds the PDF's summary from
    PharmCAT's TSV, which leaves VKORC1, CYP4F2 and IFNL3 blank: the summary read
    blank while the gene table below said N/A."""
    monkeypatch.setattr(generator_module, "EXECSUM_USE_TSV", True)
    tsv = Path(__file__).resolve().parent.parent / "test_data"
    (tmp_path / "p1_pgx_pharmcat.tsv").write_text(
        (tsv / "pharmcat.example.v340.report.tsv").read_text(encoding="utf-8")
    )
    _, page = _render(monkeypatch, tmp_path, [dict(VKORC1_PHARMCAT)], "write_html")
    summary = page[
        page.index('id="exec-summary"') : page.index("<h2>Genetic Results</h2>")
    ]
    cells = {
        re.sub(r"<[^>]+>", "", gene)
        .strip(): re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", ph))
        .strip()
        for gene, ph in re.findall(
            r'class="exec-summary-gene">(.*?)</td>.*?class="exec-summary-phenotype">(.*?)</td>',
            summary,
            re.S,
        )
    }
    assert cells["VKORC1"].startswith("N/A") and cells["IFNL3"].startswith("N/A")
    assert cells["CYP4F2"].startswith("Possibly Wild Type")  # *1/*1: a reference call
    assert cells["CYP2C19"].startswith("Normal Metabolizer")
    assert "defines no phenotype for it" in summary


def test_fhir_states_no_phenotype_for_n_a():
    """N/A says there is none; it is not a phenotype to export."""
    from app.services.fhir_export_service import FHIRExportService

    service = FHIRExportService.__new__(FHIRExportService)
    observation = service._create_genotype_observation(
        {
            "gene": "VKORC1",
            "diplotype": "rs9923231 reference (C)/...",
            "phenotype": "N/A",
        },
        "Patient/p1",
        "run-1",
    )
    texts = [
        c.get("valueCodeableConcept", {}).get("text")
        for c in observation.get("component", [])
    ]
    assert "N/A" not in texts


def test_the_interactive_report_shows_the_evidence(monkeypatch, tmp_path):
    _, page = _render(
        monkeypatch, tmp_path, [dict(CYP2D6_PHARMCAT)], "write_interactive_html"
    )
    panel = page[page.index('<div class="pypgx-evidence">') :]
    assert "22-42128241-CT-C" in panel and "0.42" in panel
    assert "chromosome 2: *4, *10, *74" in panel
    assert 'src="pypgx/CYP2D6.copy-number-profile.png"' in panel


def test_an_outside_call_pypgx_did_not_make_gets_none_of_it(monkeypatch, tmp_path):
    """E.g. the manual override file: a different diplotype than PyPGx's."""
    other = dict(CYP2D6_PHARMCAT, diplotype="*1/*1", phenotype="Normal Metabolizer")
    rows, page = _render(monkeypatch, tmp_path, [other], "write_html")
    assert "pypgx" not in rows["CYP2D6"]
    assert rows["CYP2D6"]["called_by"] == "X"
    assert "Tandem1A" not in _gene_row(page, "CYP2D6")[1]


# ---------------------------------------------------------------------------
# PharmCAT's outside calls
# ---------------------------------------------------------------------------


def _outside_call_writer():
    """The Python main.nf's PyPGx process runs to write pharmcat.outside.tsv, as
    bash receives it: Groovy turns each \\\\ into \\ in a ''' shell block."""
    source = (
        Path(__file__).resolve().parent.parent / "pipelines" / "pgx" / "main.nf"
    ).read_text(encoding="utf-8")
    start = source.index("python3 - <<PY\n") + len("python3 - <<PY\n")
    end = source.index("\nPY\n", start)
    return source[start:end].replace("\\\\", "\\")


def test_outside_calls_leave_the_phenotype_to_pharmcat(tmp_path):
    """PyPGx's phenotype in the outside call made PharmCAT take it as given and drop
    the activity score: CYP2D6 *3/*68+*4 went from 0.0 to "n/a" on NA12878."""
    (tmp_path / "pypgx_result.json").write_text(json.dumps(PYPGX_RESULTS))
    proc = subprocess.run(
        [sys.executable, "-c", _outside_call_writer()],
        cwd=tmp_path,
        env={"PYPGX_SUCCESS": "true", "PATH": os.environ.get("PATH", "")},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    rows = (tmp_path / "pharmcat.outside.tsv").read_text().splitlines()
    assert "CYP2D6\t*3/*68+*4" in rows
    assert all(len(row.split("\t")) == 2 for row in rows), rows
