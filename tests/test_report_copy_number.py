"""A gene with SV-defined alleles called without copy number says so beside its call.

PyPGx calls structural-variant star alleles (CYP2D6 *5, duplications and CYP2D7
hybrids; GSTM1, GSTT1 and UGT2B17 whole-gene deletions; the rest of its gene
table's SV genes) only from a whole-genome alignment's depth of coverage. Every
other input gets these genes called from small variants alone, and the report was
silent: a VCF's CYP2D6 *1/*1 went to PharmCAT as a Normal Metabolizer whether or not
the sample carried *5 or a duplication, and GSTM1 *A/*A read as two copies of a gene
about half of Europeans have none of. On NA12878 (GeT-RM *3/*68+*4) the call was
*3/*4.

Now PyPGx's own record decides which calls had no copy number (no CNV call, no
cnv-calls.zip), and the run says why: the input type, or the depth gate's record on
the pypgx_bam2vcf step. The report says so beside each such call, on the Executive
Summary's row and in its notes for a gene in PharmCAT's panel, and in one alert. A
whole-genome run, whose genes all have a copy-number call, shows none of it.
"""

from __future__ import annotations

import ast
import csv
import json
import re
from pathlib import Path

import pytest

import app.reports.generator as generator_module
from app.reports.copy_number import (
    CALL_NOTE,
    MISSED,
    NULL_DELETION,
    annotate_copy_number,
    copy_number_alert,
    copy_number_call_note,
    copy_number_note,
    genes_without_copy_number,
    why_not_assessed,
)

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "docker" / "pypgx" / "pypgx_wrapper.py"
DATA = ROOT / "test_data"


def _result(diplotype, copy_number=None, cnv_model=None):
    """One gene of the /genotype response (docker/pypgx's run_pypgx)."""
    details = {"phenotype_method": None}
    if copy_number:
        details["copy_number"] = copy_number
    result = {"success": True, "gene": "", "diplotype": diplotype, "details": details}
    if cnv_model is not None:
        result["methods"] = {"beagle": True, "cnv_model": cnv_model}
    return result


def _vcf_run():
    """What /genotype returns for a VCF: no copy number for any gene."""
    return {
        "success": True,
        "results": {
            "CYP2D6": _result("*1/*4", cnv_model=False),
            "GSTM1": _result("*A/*A", cnv_model=False),
            "UGT2B17": _result("*1/*1", cnv_model=False),
            "ABCB1": _result("*1/*2", cnv_model=False),
        },
    }


def _wgs_run():
    """A whole genome: PyPGx's copy-number models ran for every SV gene."""
    return {
        "success": True,
        "results": {
            "CYP2D6": _result("*3/*68+*4", "Tandem1A", cnv_model=True),
            "GSTM1": _result("*0/*0", "WholeDel1Hom", cnv_model=True),
            "UGT2B17": _result("*1/*1", "Normal", cnv_model=True),
            "ABCB1": _result("*1/*2", cnv_model=False),
        },
    }


# --------------------------------------------------------------------------
# Which genes, and why
# --------------------------------------------------------------------------


def test_the_genes_are_pypgx_sv_target_genes():
    with open(ROOT / "lexicon" / "gene-table.csv", newline="", encoding="utf-8") as fh:
        sv = {
            row["Gene"]
            for row in csv.DictReader(fh)
            if row["SV"] == "TRUE" and row["Target"] == "TRUE"
        }
    assert set(MISSED) == sv


def test_the_whole_gene_deletion_genes_are_the_sidecars():
    tree = ast.parse(WRAPPER.read_text(encoding="utf-8"))
    (node,) = [
        n
        for n in tree.body
        if isinstance(n, ast.Assign)
        and any(
            getattr(t, "id", None) == "WHOLE_GENE_DELETION_GENES" for t in n.targets
        )
    ]
    assert set(ast.literal_eval(node.value.args[0])) == NULL_DELETION


def test_a_vcf_runs_sv_genes_are_flagged_and_no_other():
    assert genes_without_copy_number(_vcf_run()) == ["CYP2D6", "GSTM1", "UGT2B17"]


def test_a_whole_genome_flags_nothing():
    assert genes_without_copy_number(_wgs_run()) == []


def test_either_sign_of_copy_number_counts():
    """The CNV column alone (a record from before ``methods``), or the models' file."""
    run = {
        "results": {
            "CYP2D6": _result("*1/*2", "Normal"),
            "GSTT1": _result("*A/*A", cnv_model=True),
            "GSTM1": _result("*A/*A"),
        }
    }
    assert genes_without_copy_number(run) == ["GSTM1"]


def test_a_gene_without_a_call_is_not_flagged():
    run = {
        "results": {
            "CYP2D6": {"success": False, "error": "PyPGx failed"},
            "GSTM1": {
                "success": True,
                "diplotype": None,
                "details": {"note": "No reads over this gene in the alignment"},
            },
        }
    }
    assert genes_without_copy_number(run) == []


@pytest.mark.parametrize(
    "run", [None, {}, {"results": None}, {"results": ["CYP2D6"]}, "x"]
)
def test_nothing_readable_flags_nothing(run):
    assert genes_without_copy_number(run) == []


@pytest.mark.parametrize(
    "file_type, short",
    [("vcf", "VCF input"), ("BCF", "BCF input"), ("gvcf", "gVCF input")],
)
def test_a_variant_file_is_the_reason(file_type, short):
    why = why_not_assessed(file_type, None)
    assert why["short"] == short
    assert "read depth" in why["sentence"]


def test_a_panel_alignment_gives_its_depth():
    record = {"status": "low_depth", "median_depth": 0.0, "min_median_depth": 15.0}
    why = why_not_assessed("bam", {"sv_calling": record})
    assert why["short"] == "depth 0.0x, under 15x"
    assert why["sentence"] == (
        "The alignment's median depth at loci no pharmacogene panel targets was 0.0x."
    )


@pytest.mark.parametrize(
    "status, words",
    [("not_grch38", "not on GRCh38"), ("failed", "could not compute")],
)
def test_the_other_alignment_reasons(status, words):
    record = {"status": status, "median_depth": None, "min_median_depth": 15.0}
    assert words in why_not_assessed("cram", {"sv_calling": record})["sentence"]


def test_an_alignment_without_the_record_has_no_reason():
    """A sidecar from before the record: the notes say what, not why."""
    assert why_not_assessed("bam", {"alt_locus_mapq0": {}}) is None
    assert why_not_assessed("bam", None) is None
    assert why_not_assessed(None, None) is None


# --------------------------------------------------------------------------
# What it says
# --------------------------------------------------------------------------

VCF = why_not_assessed("vcf", None)


def test_cyp2d6s_notes_name_what_it_misses_and_that_the_phenotype_can_move():
    assert copy_number_call_note("CYP2D6", VCF) == (
        "Copy number not assessed (VCF input): would miss *5, duplications and "
        "hybrids (see Alerts)"
    )
    assert copy_number_note("CYP2D6", VCF) == (
        "Copy number was not assessed (VCF input), so this call cannot show a *5 "
        "deletion, a duplication or a CYP2D7 hybrid (*13, *36, *68), which can "
        "change the phenotype. See Alerts and Warnings."
    )


@pytest.mark.parametrize("gene", sorted(NULL_DELETION))
def test_a_deletion_genes_notes_do_not_imply_both_copies(gene):
    call_note = copy_number_call_note(gene, VCF)
    assert "one or both copies may be deleted" in call_note
    note = copy_number_note(gene, VCF)
    assert "the common null allele" in note
    assert "how many copies there are, if any" in note
    for text in (call_note, note):
        for claim in ("two copies", "both present", "normal", "would miss"):
            assert claim not in text.lower(), (gene, text)


def test_without_a_reason_the_note_says_what_not_why():
    assert copy_number_call_note("GSTT1", None) == (
        "Copy number not assessed: one or both copies may be deleted (*0) (see Alerts)"
    )


def test_every_gene_has_both_notes_and_a_short_call_note():
    """The call note sits in the diplotype column, a sixth of the page wide: about
    as long as the ALT-locus note beside it (app/reports/alt_locus.py)."""
    assert set(CALL_NOTE) == set(MISSED)
    for gene in MISSED:
        assert len(copy_number_call_note(gene, VCF)) <= 90, gene
        assert copy_number_note(gene, VCF).endswith("See Alerts and Warnings."), gene


def test_the_alert_names_the_genes_why_and_what_would_assess_them():
    alert = copy_number_alert(["CYP2D6", "GSTM1", "UGT2B17"], VCF)
    text = re.sub(r"<[^>]+>", "", alert)
    assert text.startswith(
        "⚠️ Copy number not assessed: CYP2D6, GSTM1 and UGT2B17. The input was a VCF"
    )
    assert "small variants alone" in text
    assert "GRCh38 whole-genome BAM or CRAM with a median depth of 15x" in text
    assert copy_number_alert([], VCF) is None


def test_rows_of_flagged_genes_carry_the_notes():
    rows = [
        {"gene": "CYP2D6", "diplotype": "*1/*4", "call_source": "OUTSIDE"},
        {"gene": "ABCB1", "diplotype": "*1/*2"},
    ]
    annotate_copy_number(rows, ["CYP2D6"], VCF)
    assert rows[0]["copy_number_not_assessed"] == {
        "note": copy_number_note("CYP2D6", VCF),
        "call_note": copy_number_call_note("CYP2D6", VCF),
    }
    assert "copy_number_not_assessed" not in rows[1]


# --------------------------------------------------------------------------
# The sidecar records why, where the report reads it
# --------------------------------------------------------------------------


def test_create_input_vcf_puts_the_decision_on_the_step_row():
    src = WRAPPER.read_text(encoding="utf-8")
    body = src[src.index("def run_pypgx_create_input_vcf") :]
    body = body[: body.index("\ndef ", 1)]
    assert "sv_inputs, sv_calling = prepare_sv_inputs(" in body
    assert '"sv_calling": sv_calling' in body
    route = src[src.index("async def create_input_vcf(") :]
    complete = route[route.index("complete_step(") :]
    complete = complete[: complete.index(")\n")]
    assert '"sv_calling": res.get("sv_calling")' in complete


# --------------------------------------------------------------------------
# What the reports show
# --------------------------------------------------------------------------


def _rows():
    rows = [
        {
            "gene": "CYP2D6",
            "diplotype": "*1/*4",
            "phenotype": "Intermediate Metabolizer",
            "call_source": "OUTSIDE",
            "tool_source": "PyPGx",
        },
        {"gene": "GSTM1", "diplotype": "*A/*A", "phenotype": "N/A"},
        {"gene": "CYP2C19", "diplotype": "*1/*2", "phenotype": "Intermediate"},
    ]
    annotate_copy_number(rows, ["CYP2D6", "GSTM1"], VCF)
    return rows


def _render_pdf_template(rows):
    from app.reports.generator import env

    tsv = {
        "gene": "CYP2D6",
        "rec_lookup_diplotype": "*1/*4",
        "rec_lookup_phenotype": "Intermediate Metabolizer",
        "rec_lookup_activity_score": 1.0,
    }
    return env.get_template("report_template.html").render(
        execsum_from_tsv=[tsv],
        diplotypes=rows,
        recommendations=[],
        patient_id="p",
        report_id="r",
        report_date="2026-10-20",
        organization="ZaroPGx",
        disclaimer="",
    )


def _clean(fragment):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment)).strip()


def test_the_pdf_notes_the_call_the_summary_row_and_its_notes():
    page = _render_pdf_template(_rows())
    summary = page[
        page.index('id="exec-summary"') : page.index('id="exec-summary-notes"')
    ]
    row = summary[summary.index('id="summary-CYP2D6"') :]
    assert "⚠ Copy number not assessed: see the notes" in _clean(
        row[: row.index("</tr>")]
    )
    cyp2c19 = summary[summary.index('id="summary-CYP2C19"') :]
    assert "Copy number" not in cyp2c19[: cyp2c19.index("</tr>")]
    notes = page[page.index('id="exec-summary-notes"') :]
    notes = _clean(notes[: notes.index("Genetic Results")])
    assert f"⚠ CYP2D6 : {copy_number_note('CYP2D6', VCF)}" in notes
    # GSTM1 is outside PharmCAT's panel: no summary note, but its call is noted.
    assert "GSTM1" not in notes
    results = page[page.index("<h2>Genetic Results</h2>") :]
    for gene in ("CYP2D6", "GSTM1"):
        row = results[results.index(f"<td>{gene}</td>") :]
        assert f"⚠ {copy_number_call_note(gene, VCF)}" in _clean(
            row[: row.index("</tr>")]
        )
    row = results[results.index("<td>CYP2C19</td>") :]
    assert "Copy number" not in row[: row.index("</tr>")]


def test_an_unflagged_report_is_unchanged():
    rows = [{"gene": "CYP2D6", "diplotype": "*1/*2", "phenotype": "Normal Metabolizer"}]
    body = _render_pdf_template(rows).split("</style>")[-1]
    assert "copy-number-note" not in body
    assert "Copy number not assessed" not in body


def test_an_uncalled_gene_gets_no_note():
    from app.reports.generator import env

    rows = [{"gene": "CYP2D6", "diplotype": "Unknown/Unknown"}]
    annotate_copy_number(rows, ["CYP2D6"], VCF)
    macros = env.get_template("_pypgx_evidence.html").module
    assert "copy-number-note" not in str(macros.call_notes(rows[0]))


def test_the_interactive_report_notes_the_call():
    from app.reports.generator import env

    macros = env.get_template("_pypgx_evidence.html").module
    note = str(macros.call_notes(_rows()[1]))
    assert 'class="call-note copy-number-note"' in note
    assert "one or both copies may be deleted (*0)" in note
    interactive = (
        ROOT / "app" / "reports" / "templates" / "interactive_report.html"
    ).read_text(encoding="utf-8")
    assert ".copy-number-note" in interactive


def _report(monkeypatch, tmp_path, pypgx, file_type="vcf", interactive=False):
    """generate_report on PharmCAT's example (CYP2D6 *1/*3, an outside call), with
    the PyPGx results a Nextflow run leaves beside it."""
    written = (
        {"write_html", "write_interactive_html"} if interactive else {"write_html"}
    )
    for key in list(generator_module.REPORT_CONFIG):
        if key.startswith(("write_", "show_")):
            monkeypatch.setitem(generator_module.REPORT_CONFIG, key, key in written)
    data = json.loads((DATA / "pharmcat.example.report.json").read_text())
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "pypgx_result.json").write_text(json.dumps(pypgx), encoding="utf-8")
    monkeypatch.setattr(
        generator_module, "load_job_file_type", lambda *a, **k: file_type
    )
    result = generator_module.generate_report(
        {"data": data}, str(tmp_path), {"id": "p1"}, job_id=None
    )
    return result


def test_a_vcf_report_says_so_and_a_whole_genome_does_not(monkeypatch, tmp_path):
    pypgx = _vcf_run()
    pypgx["results"]["CYP2D6"] = _result("*1/*3", cnv_model=False)
    result = _report(monkeypatch, tmp_path / "vcf", pypgx)
    page = (tmp_path / "vcf" / result["html_path"].split("/")[-1]).read_text()
    body = _clean(page.split("</style>")[-1])
    assert (
        "Copy number not assessed: CYP2D6, GSTM1 and UGT2B17. The input was a VCF"
        in body
    )
    # PharmCAT's row for CYP2D6, an outside call from PyPGx, carries the note.
    rows = {r["gene"]: r for r in result["processed_data"]["genes"]}
    assert rows["CYP2D6"]["call_source"] == "OUTSIDE"
    assert rows["CYP2D6"]["copy_number_not_assessed"]
    assert "⚠ Copy number not assessed: see the notes" in body
    assert "ABCB1" in rows and "copy_number_not_assessed" not in rows["ABCB1"]

    pypgx = _wgs_run()
    pypgx["results"]["CYP2D6"] = _result("*1/*3", "Normal", cnv_model=True)
    result = _report(monkeypatch, tmp_path / "wgs", pypgx, file_type="bam")
    page = (tmp_path / "wgs" / result["html_path"].split("/")[-1]).read_text()
    assert "Copy number not assessed" not in page
    assert "copy-number-note" not in page.split("</style>")[-1]


def test_the_interactive_report_has_the_alert_and_the_notes(monkeypatch, tmp_path):
    result = _report(monkeypatch, tmp_path, _vcf_run(), interactive=True)
    page = (tmp_path / result["interactive_html_path"].split("/")[-1]).read_text()
    body = _clean(page.split("</style>")[-1])
    assert "Copy number not assessed: CYP2D6, GSTM1 and UGT2B17." in body
    assert copy_number_call_note("GSTM1", VCF) in body


def test_the_summary_still_fits_one_page_with_the_note(monkeypatch, tmp_path):
    """The worst case (25 genes called, long phenotypes), CYP2D6 noted."""
    pytest.importorskip("weasyprint")
    from app.pharmcat.pharmcat_client import normalize_pharmcat_results
    from app.reports.pdf_fit import section_pages
    from tests.test_report_exec_summary_one_page import (
        EXAMPLE_JSON,
        _report_html,
        _worst_case_tsv,
    )

    (tmp_path / "pypgx_result.json").write_text(
        json.dumps(_vcf_run()), encoding="utf-8"
    )
    data = normalize_pharmcat_results(
        json.loads(EXAMPLE_JSON.read_text(encoding="utf-8"))
    )["data"]
    # The worst case's TSV has CYP2D6 *1/*4 from PyPGx; so does the gene list here.
    (cyp2d6,) = [row for row in data["genes"] if row["gene"] == "CYP2D6"]
    cyp2d6.update(diplotype="*1/*4", call_source="OUTSIDE")
    page = _report_html(monkeypatch, tmp_path, data, _worst_case_tsv())
    row = page[page.index('id="summary-CYP2D6"') :]
    assert "Copy number not assessed: see the notes" in row[: row.index("</tr>")]
    document, _ = generator_module.render_pdf_document(page)
    assert len(section_pages(document, "exec-summary")) == 1
