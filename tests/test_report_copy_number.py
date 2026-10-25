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
from html import unescape
from pathlib import Path

import pytest

import app.reports.generator as generator_module
from app.reports.copy_number import (
    CALL_NOTE,
    MISSED,
    NULL_DELETION,
    PHARMCAT_ALERT,
    PHARMCAT_CALL_NOTE,
    PHARMCAT_MISSED,
    annotate_copy_number,
    annotate_pharmcat_copy_number,
    copy_number_alert,
    copy_number_call_note,
    copy_number_note,
    genes_without_copy_number,
    measured_normal_genes,
    pharmcat_copy_number_alert,
    positions_summary_note,
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
    assert set(ast.literal_eval(node.value.args[0])) == set(NULL_DELETION)


def _table(name):
    with open(ROOT / "lexicon" / name, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# SV alleles in PyPGx's allele table that no copy-number state of its genotypers
# yields (pypgx/api/genotype.py, PyPGx 0.27): no call can show them either way.
# CYP2B6 *30 is its Tandem1 state, which its genotyper leaves unnamed and the report
# names (copy_number.NAMED).
NEVER_CALLED = {("CYP2D6", "*61"), ("CYP2D6", "*63")}


def test_each_gene_names_the_sv_alleles_pypgx_calls():
    """Every SV allele of the gene in PyPGx's allele table, and nothing else."""
    sv = {}
    for row in _table("allele-table.csv"):
        if row["SV"] == "TRUE" and row["Gene"] in MISSED:
            sv.setdefault(row["Gene"], set()).add(row["StarAllele"])
    for gene, text in MISSED.items():
        named = set(re.findall(r"\*\w+|\bMALE\b", text))
        # An allele no state yields is named only as one PyPGx does not call.
        named -= {
            a
            for g, a in NEVER_CALLED
            if g == gene and f"{a}, which PyPGx does not call" in text
        }
        expected = {a for a in sv.get(gene, set()) if (gene, a) not in NEVER_CALLED}
        assert named == expected, gene


def test_the_phenotype_genes_are_those_pypgx_gives_a_phenotype():
    from app.reports.copy_number import _PHENOTYPE_GENES

    assert _PHENOTYPE_GENES == {
        row["Gene"]
        for row in _table("gene-table.csv")
        if row["Gene"] in MISSED and row["PhenotypeMethod"] != "N/A"
    }


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


@pytest.mark.parametrize(
    "file_type, output",
    [("bam", {"alt_locus_mapq0": {}}), ("bam", None), (None, None)],
    ids=["sidecar-before-the-record", "no-step-row", "type-not-recorded"],
)
def test_without_a_known_reason_it_says_only_what_is_certain(file_type, output):
    """No input type is asserted that the run did not record."""
    why = why_not_assessed(file_type, output)
    assert why["short"] == "no read depth"
    assert why["sentence"] == "No depth of coverage reached PyPGx in this run."


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
        "deletion, extra copies (x2, x3) or a CYP2D7 hybrid (*13, *36, *68), which "
        "can change the phenotype. See Alerts and Warnings."
    )


@pytest.mark.parametrize("gene", sorted(NULL_DELETION))
def test_a_deletion_genes_notes_do_not_imply_both_copies(gene):
    call_note = copy_number_call_note(gene, VCF)
    assert "may be deleted" in call_note
    note = copy_number_note(gene, VCF)
    assert "the common null allele" in note
    assert "how many copies there are, if any" in note
    for text in (call_note, note):
        for claim in ("two copies", "both present", "normal", "would miss"):
            assert claim not in text.lower(), (gene, text)


def test_without_a_known_reason_the_note_asserts_none():
    assert copy_number_call_note("GSTT1", why_not_assessed(None, None)) == (
        "Copy number not assessed (no read depth): one or both copies may be "
        "deleted (*0) (see Alerts)"
    )


def test_every_gene_has_both_notes_and_a_short_call_note():
    """The call note sits in the diplotype column, a sixth of the page wide: about
    as long as the ALT-locus note beside it (app/reports/alt_locus.py)."""
    assert set(CALL_NOTE) == set(MISSED)
    for gene in MISSED:
        assert len(copy_number_call_note(gene, VCF)) <= 95, gene
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
        {
            "gene": "CYP2D6",
            "diplotype": "*1/*4",
            "call_source": "OUTSIDE",
            "tool_source": "PyPGx",
        },
        {"gene": "GSTM1", "diplotype": "*A/*A", "tool_source": "PyPGx"},
        {"gene": "ABCB1", "diplotype": "*1/*2", "tool_source": "PyPGx"},
    ]
    annotate_copy_number(rows, ["CYP2D6", "GSTM1"], VCF)
    assert rows[0]["copy_number_not_assessed"] == {
        "note": copy_number_note("CYP2D6", VCF),
        "call_note": copy_number_call_note("CYP2D6", VCF),
    }
    assert rows[1]["copy_number_not_assessed"]
    assert "copy_number_not_assessed" not in rows[2]


@pytest.mark.parametrize(
    "row",
    [
        # OUTSIDECALLSOVERRIDE's file: a lab's call, which PyPGx did not make.
        {"gene": "CYP2D6", "diplotype": "*1/*5", "call_source": "OUTSIDE"},
        # PharmCAT's own call from small variants, PyPGx's beside it unused.
        {
            "gene": "CYP2B6",
            "diplotype": "*1/*6",
            "call_source": "MATCHER",
            "tool_source": "PyPGx",
        },
    ],
    ids=["override-file", "pharmcat-matcher"],
)
def test_a_call_pypgx_did_not_make_is_not_noted(row):
    rows = [dict(row)]
    annotate_copy_number(rows, ["CYP2D6", "CYP2B6"], VCF)
    assert "copy_number_not_assessed" not in rows[0]


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
        {
            "gene": "GSTM1",
            "diplotype": "*A/*A",
            "phenotype": "N/A",
            "tool_source": "PyPGx",
        },
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

    rows = [{"gene": "CYP2D6", "diplotype": "Unknown/Unknown", "tool_source": "PyPGx"}]
    annotate_copy_number(rows, ["CYP2D6"], VCF)
    assert rows[0]["copy_number_not_assessed"]
    macros = env.get_template("_pypgx_evidence.html").module
    assert "copy-number-note" not in str(macros.call_notes(rows[0]))


def test_the_interactive_report_notes_the_call():
    from app.reports.generator import env

    macros = env.get_template("_pypgx_evidence.html").module
    note = str(macros.call_notes(_rows()[1]))
    assert 'class="call-note copy-number-note"' in note
    assert "copies may be deleted (*0) or duplicated" in note
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
    assert rows["CYP2D6"]["called_by"] == "P"
    assert rows["CYP2D6"]["copy_number_not_assessed"]
    assert "⚠ Copy number not assessed: see the notes" in body
    assert "ABCB1" in rows and "copy_number_not_assessed" not in rows["ABCB1"]

    pypgx = _wgs_run()
    pypgx["results"]["CYP2D6"] = _result("*1/*3", "Normal", cnv_model=True)
    result = _report(monkeypatch, tmp_path / "wgs", pypgx, file_type="bam")
    page = (tmp_path / "wgs" / result["html_path"].split("/")[-1]).read_text()
    body = _clean(page.split("</style>")[-1])
    # None of PyPGx's calls is noted, CYP2D6's included ...
    rows = {r["gene"]: r for r in result["processed_data"]["genes"]}
    assert "copy_number_not_assessed" not in rows["CYP2D6"]
    assert "Copy number not assessed: CYP2D6" not in body
    assert "(VCF input)" not in body
    # ... but PharmCAT's own calls are, on a whole genome too.
    assert "Structural alleles not assessed:" in body
    assert rows["CYP2B6"]["copy_number_not_assessed"]["kind"] == "pharmcat"


def test_pharmcats_rows_from_pypgx_are_all_its_call(monkeypatch, tmp_path):
    """PharmCAT 2.x's JSON (the example) has a CYP2D6 row per guideline source.
    Only the last was marked PyPGx's, the dedupe kept the first, and the Call column
    said X (an outside call, tool not recorded) for PyPGx's own call."""
    pypgx = {"results": {"CYP2D6": _result("*1/*3", "Normal", cnv_model=True)}}
    result = _report(monkeypatch, tmp_path, pypgx)
    (row,) = [r for r in result["processed_data"]["genes"] if r["gene"] == "CYP2D6"]
    assert (row["called_by"], row["tool_source"]) == ("P", "PyPGx")


def test_an_outside_call_pypgx_did_not_make_gets_no_note(monkeypatch, tmp_path):
    """OUTSIDECALLSOVERRIDE: PharmCAT took a lab's CYP2D6, not PyPGx's *1/*4."""
    result = _report(monkeypatch, tmp_path, _vcf_run())
    rows = {r["gene"]: r for r in result["processed_data"]["genes"]}
    assert rows["CYP2D6"]["diplotype"] == "*1/*3"
    assert rows["CYP2D6"]["called_by"] == "X"
    assert "copy_number_not_assessed" not in rows["CYP2D6"]
    page = (tmp_path / result["html_path"].split("/")[-1]).read_text()
    body = _clean(page.split("</style>")[-1])
    assert "Copy number not assessed: GSTM1 and UGT2B17." in body
    row = page[page.index('id="summary-CYP2D6"') :]
    assert "see the notes" not in row[: row.index("</tr>")]


def test_an_input_type_the_job_did_not_record_is_not_asserted(monkeypatch, tmp_path):
    """/reprocess-report rebuilds a report without the job's metadata: the data's
    file type then defaults to "vcf", whatever was uploaded."""
    pypgx = _vcf_run()
    pypgx["results"]["CYP2D6"] = _result("*1/*3", cnv_model=False)
    result = _report(monkeypatch, tmp_path, pypgx, file_type=None)
    page = (tmp_path / result["html_path"].split("/")[-1]).read_text()
    body = _clean(page.split("</style>")[-1])
    assert (
        "Copy number not assessed: CYP2D6, GSTM1 and UGT2B17. No depth of coverage "
        "reached PyPGx in this run." in body
    )
    # PyPGx's alert asserts no input type (PharmCAT's says what PharmCAT reads).
    alerts = body[: body.index("Executive Summary")]
    pypgx_alert = alerts[alerts.index("Copy number not assessed:") :]
    pypgx_alert = pypgx_alert[: pypgx_alert.index("Structural alleles not assessed")]
    assert "VCF" not in pypgx_alert


def test_the_interactive_report_has_the_alert_and_the_notes(monkeypatch, tmp_path):
    pypgx = _vcf_run()
    pypgx["results"]["CYP2D6"] = _result("*1/*3", cnv_model=False)
    result = _report(monkeypatch, tmp_path, pypgx, interactive=True)
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


# --------------------------------------------------------------------------
# PharmCAT's own calls: the structural alleles it says it cannot detect
# --------------------------------------------------------------------------

# PharmCAT 3.4.0's own words, its HTML report's disclaimers
# (org/pharmgkb/pharmcat/reporter/disclaimers.hbs in its jar).
PHARMCAT_DISCLAIMER = (
    "Structural variation star alleles that cannot be detected using VCF file data: "
    "CYP2B7-CYP2B6 hybrids: CYP2B6*29, CYP2B6*30; Partial and whole gene deletions: "
    "CYP2C19*36, CYP2C19*37, CYP4F2*16, SLCO1B1*48, SLCO1B1*49"
)


def test_pharmcats_genes_and_alleles_are_its_own_list():
    listed = dict()
    for gene, allele in re.findall(r"(\w+)\*(\d+)", PHARMCAT_DISCLAIMER):
        listed.setdefault(gene, []).append(f"*{allele}")
    texts = (PHARMCAT_MISSED, PHARMCAT_CALL_NOTE, PHARMCAT_ALERT)
    assert all(set(t) == set(listed) for t in texts)
    for gene, alleles in listed.items():
        for allele in alleles:
            assert all(allele in t[gene] for t in texts), (gene, allele)
    assert "G6PD" not in PHARMCAT_MISSED


def _pharmcat_rows():
    return [
        {"gene": "CYP2B6", "diplotype": "*1/*6", "call_source": "MATCHER"},
        {"gene": "CYP2C19", "diplotype": "*1/*2", "call_source": "MATCHER"},
        {"gene": "CYP4F2", "diplotype": "*1/*1", "call_source": "MATCHER"},
        {"gene": "SLCO1B1", "diplotype": "*1/*15", "call_source": "MATCHER"},
        {"gene": "G6PD", "diplotype": "B/B", "call_source": "MATCHER"},
        {"gene": "CYP2C9", "diplotype": "*1/*2", "call_source": "MATCHER"},
    ]


def _pypgx_row(gene, diplotype="*1/*1"):
    """PyPGx's call, through PharmCAT as an outside call (Call P)."""
    return {
        "gene": gene,
        "diplotype": diplotype,
        "call_source": "OUTSIDE",
        "tool_source": "PyPGx",
    }


def test_pharmcats_own_calls_are_noted():
    rows = _pharmcat_rows()
    annotate_pharmcat_copy_number(rows)
    noted = {r["gene"]: r.get("copy_number_not_assessed") for r in rows}
    for gene in ("CYP2B6", "CYP2C19", "CYP4F2", "SLCO1B1"):
        assert noted[gene]["listed"] and noted[gene]["kind"] == "pharmcat"
        assert noted[gene]["warn"] is True
        assert noted[gene]["marker"] == "Copy number not assessed"
        assert noted[gene]["call_note"] == (
            f"Copy number not assessed: {PHARMCAT_CALL_NOTE[gene]} (see Alerts)"
        )
    assert noted["G6PD"] is None and noted["CYP2C9"] is None
    # Short enough for the Diplotype column: no tool name, no raw code.
    assert noted["CYP2B6"]["call_note"] == (
        "Copy number not assessed: would miss hybrids (*29, *30) (see Alerts)"
    )
    assert noted["CYP2C19"]["note"] == (
        "Copy number not assessed, so this call would miss a partial or whole-gene "
        "deletion (*36, *37), which can change the phenotype. See Alerts and Warnings."
    )
    # CYP4F2 has no phenotype to change.
    assert "phenotype" not in noted["CYP4F2"]["note"]


def test_pypgxs_calls_it_cannot_assess_are_noted():
    """PyPGx's own CYP2C19 and SLCO1B1 without the deletion check (no copy-number
    model); its CYP2B6 with copy number measured has nothing left to show, *30
    being its Tandem1 state."""
    rows = [_pypgx_row("CYP2C19"), _pypgx_row("SLCO1B1"), _pypgx_row("CYP2B6")]
    annotate_pharmcat_copy_number(rows)
    cyp2c19, slco1b1, cyp2b6 = (r.get("copy_number_not_assessed") for r in rows)
    assert cyp2c19["kind"] == slco1b1["kind"] == "no_model"
    assert cyp2c19["warn"] is True
    assert cyp2c19["call_note"] == (
        "Copy number not assessed (no copy-number model): would miss deletions "
        "(*36, *37) (see Alerts)"
    )
    assert cyp2b6 is None
    # PyPGx's CYP4F2 with copy number measured can show its deletion (*DEL).
    rows = [_pypgx_row("CYP4F2")]
    annotate_pharmcat_copy_number(rows)
    assert "copy_number_not_assessed" not in rows[0]


@pytest.mark.parametrize(
    "row",
    [
        # An outside call neither tool made (OUTSIDECALLSOVERRIDE's file).
        {"gene": "CYP2C19", "diplotype": "*1/*2", "call_source": "OUTSIDE"},
        {"gene": "CYP2B6", "diplotype": "*1/*6", "call_source": "OUTSIDE"},
    ],
    ids=["override-cyp2c19", "override-cyp2b6"],
)
def test_an_outside_call_neither_tool_made_gets_no_note(row):
    rows = [dict(row)]
    annotate_pharmcat_copy_number(rows)
    assert "copy_number_not_assessed" not in rows[0]


def test_a_row_pypgx_noted_keeps_pypgxs_note():
    rows = [_pypgx_row("CYP2B6", "*1/*6")]
    annotate_copy_number(rows, ["CYP2B6"], VCF)
    before = dict(rows[0]["copy_number_not_assessed"])
    annotate_pharmcat_copy_number(rows)
    assert rows[0]["copy_number_not_assessed"] == before


def test_pypgxs_copy_number_in_the_same_run_is_stated_not_contradicted():
    """PharmCAT's own call shown, PyPGx's copy number measured normal beside it:
    no note on either, *30 included (PyPGx's Tandem1 state), and the alert says
    so once."""
    rows = [
        {
            "gene": "CYP2B6",
            "diplotype": "*1/*6",
            "call_source": "MATCHER",
            "tool_source": "PyPGx",
        },
        {"gene": "CYP4F2", "diplotype": "*1/*1", "call_source": "MATCHER"},
    ]
    pypgx = {
        "results": {
            "CYP2B6": _result("*1/*6", "Normal", cnv_model=True),
            "CYP4F2": _result("*1/*1", "Normal", cnv_model=True),
        }
    }
    annotate_pharmcat_copy_number(rows, pypgx)
    assert "copy_number_not_assessed" not in rows[0]
    assert "copy_number_not_assessed" not in rows[1]
    assert measured_normal_genes(rows, pypgx) == ["CYP2B6", "CYP4F2"]
    # Measured, so neither is named as not assessed, and each is said once.
    text = _clean(pharmcat_copy_number_alert(rows, pypgx))
    assert text == (
        "Structural alleles: PyPGx measured normal copy number for CYP2B6 (no *29 "
        "or *30 hybrid, or duplication) and CYP4F2 (no whole-gene deletion)."
    )
    assert "⚠" not in text and "not assessed" not in text


def test_every_listed_gene_can_be_measured():
    """PyPGx models CYP2B6 and CYP4F2; ZaroPGx's deletion check measures the two it
    has no model for, CYP2C19 and SLCO1B1, and DPYD's exon deletions."""
    from app.reports.copy_number import (
        CHECKED,
        MEASURED_NORMAL,
        PYPGX_NO_MODEL,
        UNLISTED_STRUCTURAL,
    )

    assert set(MEASURED_NORMAL) == set(PHARMCAT_MISSED) | set(UNLISTED_STRUCTURAL)
    assert set(CHECKED) == PYPGX_NO_MODEL


def test_an_alignment_under_auto_keeps_pharmcats_calls_with_pypgxs_copy_number(
    monkeypatch, tmp_path
):
    """PYPGX_PHARMCAT_PREFERENCE auto, the default: from reads PyPGx genotypes
    PharmCAT's genes too, for their copy number, and main.nf does not hand PharmCAT
    those calls, so its own stand (Call C). Measured normal, CYP2B6 and CYP4F2 get
    no warning: the alert's heading names only what was not assessed, and the
    measurement is said once."""
    pypgx = _wgs_run()
    pypgx["results"]["CYP2D6"] = _result("*1/*3", "Normal", cnv_model=True)
    # PyPGx's own diplotypes differ from PharmCAT's (*1/*1 for both): not shown.
    pypgx["results"]["CYP2B6"] = _result("*1/*6", "Normal", cnv_model=True)
    pypgx["results"]["CYP4F2"] = _result("*1/*3", "Normal", cnv_model=True)
    pypgx["results"]["CYP2C19"] = _result("*1/*2", cnv_model=False)
    result = _report(monkeypatch, tmp_path, pypgx, file_type="cram")
    page = (tmp_path / result["html_path"].split("/")[-1]).read_text()
    body = unescape(_clean(page.split("</style>")[-1]))
    genes = result["processed_data"]["genes"]
    rows = {r["gene"]: r for r in genes}
    for gene in ("CYP2B6", "CYP4F2", "CYP2C19"):
        assert [r["gene"] for r in genes].count(gene) == 1
        assert (rows[gene]["called_by"], rows[gene]["diplotype"]) == (
            "C",
            "*38/*38" if gene == "CYP2C19" else "*1/*1",
        )
    # CYP2B6 and CYP4F2: measured normal, nothing left to say beside the call.
    results = page[page.index("<h2>Genetic Results</h2>") :]
    for gene in ("CYP2B6", "CYP4F2"):
        assert "copy_number_not_assessed" not in rows[gene]
        cell = results[results.index(f"<td>{gene}</td>") :]
        assert "call-note" not in cell[: cell.index("</tr>")]
        row = page[page.index(f'id="summary-{gene}"') :]
        assert "see the notes" not in row[: row.index("</tr>")]
    # A run without the deletion check (an older pypgx image, or the check failed):
    # CYP2C19, SLCO1B1 and DPYD still not assessed, and said so.
    for gene in ("CYP2C19", "SLCO1B1", "DPYD"):
        row = page[page.index(f'id="summary-{gene}"') :]
        assert "⚠ Copy number not assessed: see the notes" in row[: row.index("</tr>")]
    alerts = body[: body.index("Executive Summary")]
    assert "Structural alleles not assessed: CYP2C19, DPYD and SLCO1B1." in alerts
    assert (
        "PyPGx measured normal copy number for CYP2B6 (no *29 or *30 hybrid, or "
        "duplication) and CYP4F2 (no whole-gene deletion)." in alerts
    )
    assert "No tool here calls" not in alerts
    assert (
        "ZaroPGx's deletion check, which measures CYP2C19, DPYD and SLCO1B1 from the "
        "read depth PyPGx uses, gave no result in this run." in alerts
    )
    # The alert does not tell how to get what this run measured.
    assert "unless PYPGX_PHARMCAT_PREFERENCE is pharmcat" not in body
    # The measurement is said once in the alert, and the notes do not copy it.
    assert body.count("PyPGx measured normal copy number for") == 1


def test_each_summary_note_is_short_and_its_own():
    """A note per gene behind the Executive Summary, pointing to the alert: not the
    alert's paragraph, once per gene."""
    rows = _pharmcat_rows()
    annotate_pharmcat_copy_number(rows)
    notes = [r["copy_number_not_assessed"]["note"] for r in rows[:4]]
    assert len(set(notes)) == 4
    for note in notes:
        assert note.endswith("See Alerts and Warnings.")
        assert note.count(".") == 2, note
        assert "PharmCAT lists as undetectable" not in note


def test_pharmcats_alert():
    rows = _pharmcat_rows()
    annotate_pharmcat_copy_number(rows)
    text = _clean(pharmcat_copy_number_alert(rows))
    assert text.startswith(
        "⚠️ Structural alleles not assessed: CYP2B6, CYP2C19, CYP4F2 and SLCO1B1. "
        "PharmCAT called them from small variants, as it does on every input, a "
        "whole genome included. These calls cannot show CYP2B6 *29 and *30 (hybrids "
        "with CYP2B7), or CYP2C19 *36 and *37, CYP4F2 *16 or SLCO1B1 *48 and *49 "
        "(partial or whole-gene deletions), structural alleles PharmCAT lists as "
        "undetectable from a VCF. A missed allele can change the phenotype of "
        "CYP2B6, CYP2C19 and SLCO1B1."
    )
    # auto, the default, has them measured from reads; only pharmcat does not.
    assert text.endswith(
        "From a GRCh38 whole-genome BAM or CRAM, unless PYPGX_PHARMCAT_PREFERENCE is "
        "pharmcat, PyPGx measures copy number for CYP2B6 and CYP4F2, and "
        "ZaroPGx checks CYP2C19, SLCO1B1 and DPYD for deletions from the same read "
        "depth."
    )
    assert pharmcat_copy_number_alert([]) is None
    assert pharmcat_copy_number_alert([{"gene": "G6PD"}]) is None
    # PyPGx's calls only: no word of what PyPGx could measure instead.
    rows = [_pypgx_row("CYP2C19"), _pypgx_row("CYP2B6")]
    annotate_pharmcat_copy_number(rows)
    text = _clean(pharmcat_copy_number_alert(rows))
    assert text.startswith(
        "⚠️ Structural alleles not assessed: CYP2C19. PyPGx called CYP2C19, and has "
        "no copy-number model for it. This call cannot show CYP2C19 *36 and *37 "
        "(partial or whole-gene deletions)"
    )
    # PyPGx's CYP2B6 with copy number measured: nothing to say.
    assert "CYP2B6" not in text
    assert "PYPGX_PHARMCAT_PREFERENCE" not in text


def test_a_heading_names_only_genes_not_assessed():
    """The demo under auto read "Structural alleles not assessed: CYP2B6, CYP2C19,
    CYP4F2 and SLCO1B1" over a body saying PyPGx measured CYP2B6 and CYP4F2."""
    rows = _pharmcat_rows()
    pypgx = {
        "results": {
            "CYP2B6": _result("*1/*1", "Normal", cnv_model=True),
            "CYP4F2": _result("*1/*1", "Normal", cnv_model=True),
        }
    }
    annotate_pharmcat_copy_number(rows, pypgx)
    alert = pharmcat_copy_number_alert(rows, pypgx)
    heading = _clean(alert[: alert.index("</p>")])
    assert heading.startswith(
        "⚠️ Structural alleles not assessed: CYP2C19 and SLCO1B1."
    )
    for gene in ("CYP2B6", "CYP4F2"):
        assert gene not in heading
    # Said once, not a sentence per gene.
    text = _clean(alert)
    assert text.count("PyPGx measured") == 1
    assert "the call shown is PharmCAT's" not in text
    assert text.count("⚠") == 1


def test_a_report_notes_pharmcats_calls_once_and_apart(monkeypatch, tmp_path):
    """PharmCAT's example (2.x JSON): CYP2D6 an outside call PyPGx made without
    copy number, CYP2B6, CYP2C19, CYP4F2 and SLCO1B1 PharmCAT's own."""
    pypgx = _vcf_run()
    pypgx["results"]["CYP2D6"] = _result("*1/*3", cnv_model=False)
    result = _report(monkeypatch, tmp_path, pypgx)
    page = (tmp_path / result["html_path"].split("/")[-1]).read_text()
    body = unescape(_clean(page.split("</style>")[-1]))
    # Two alerts, each naming its own genes.
    assert "Copy number not assessed: CYP2D6, GSTM1 and UGT2B17." in body
    assert (
        "Structural alleles not assessed: CYP2B6, CYP2C19, CYP4F2, DPYD and SLCO1B1."
        in body
    )
    assert (
        "or a deletion of DPYD exons, which CPIC's DPYD table does not list and which "
        "can reduce DPD activity." in body
    )
    assert body.count("Structural alleles not assessed") == 1
    rows = {r["gene"]: r for r in result["processed_data"]["genes"]}
    assert not rows["CYP2D6"]["copy_number_not_assessed"].get("listed")
    # A short note for each behind the summary, and each one's call note.
    notes = page[page.index('id="exec-summary-notes"') :]
    notes = unescape(_clean(notes[: notes.index("<h2>Genetic Results</h2>")]))
    for gene in list(PHARMCAT_MISSED) + ["DPYD"]:
        row = page[page.index(f'id="summary-{gene}"') :]
        assert "Copy number not assessed: see the notes" in row[: row.index("</tr>")]
        assert "⚠ " + rows[gene]["copy_number_not_assessed"]["call_note"] in body
        assert notes.count(f"⚠ {gene} : Copy number not assessed, so this call") == 1
    # The alert's paragraph is not copied behind the summary.
    assert "PharmCAT lists as undetectable" not in notes
    assert "⚠ G6PD" not in body


PANEL = (
    "ABCG2 CACNA1S CFTR CYP2B6 CYP2C19 CYP2C9 CYP2D6 CYP3A4 CYP3A5 CYP4F2 DPYD F2 F5 "
    "G6PD HLA-A HLA-B IFNL3 MT-RNR1 NAT2 NUDT15 RYR1 SLCO1B1 TPMT UGT1A1 VKORC1"
).split()
LOW_DEPTH = {"short": "depth 9.0x, under 15x", "sentence": "", "gate": "15"}


def test_the_notes_fit_one_page_in_the_worst_case(monkeypatch, tmp_path):
    """A heavy alignment run, PyPGx calling every gene it can (auto): copy-number
    notes on seven of the summary's genes (CYP2D6, CYP2B6, CYP4F2 and G6PD without
    depth; CYP2C19, SLCO1B1 and DPYD, which PyPGx has no model for), CYP2D6's reads mostly
    at mapping quality 0, and a positions note on every called gene. The Executive
    Summary stays one page, filled to its foot, and its notes fit the page behind
    it."""
    pytest.importorskip("weasyprint")
    from app.pharmcat.pharmcat_client import normalize_pharmcat_results
    from app.reports.pdf_fit import section_fragments, section_pages
    from tests.test_report_exec_summary_one_page import (
        EXAMPLE_JSON,
        _report_html,
        _worst_case_tsv,
    )

    data = normalize_pharmcat_results(
        json.loads(EXAMPLE_JSON.read_text(encoding="utf-8"))
    )["data"]
    original = generator_module.annotate_pharmcat_copy_number

    def heavy(genes, pypgx_results=None):
        for row in genes:
            gene = row.get("gene")
            if gene not in PANEL:
                continue
            if not str(row.get("diplotype") or "").startswith("*"):
                row["diplotype"] = "*1/*2"
            if gene.startswith("HLA") or gene == "MT-RNR1":
                continue
            row["call_source"], row["tool_source"] = "OUTSIDE", "PyPGx"
            # Both reasons, three-digit counts: the longest grouped note (review).
            why = (
                "no reads in the alignment"
                if len(gene) % 2
                else "no genotype in the file"
            )
            row["positions_note"] = (
                f"123 of 456 positions had {why} and were read as reference"
            )
        annotate_copy_number(genes, ["CYP2D6", "CYP2B6", "CYP4F2", "G6PD"], LOW_DEPTH)
        original(genes, pypgx_results)
        for row in genes:
            if row.get("gene") == "CYP2D6":
                row["alt_locus"] = {
                    "note": "Most of this gene's reads had mapping quality 0 "
                    "(91 of 1570): the call may be wrong.",
                    "call_note": "Reads mostly at mapping quality 0 (see Alerts)",
                }

    monkeypatch.setattr(generator_module, "annotate_pharmcat_copy_number", heavy)
    page = _report_html(monkeypatch, tmp_path, data, _worst_case_tsv())
    notes = page[page.index('id="exec-summary-notes"') :]
    notes = unescape(_clean(notes[: notes.index("<h2>Genetic Results</h2>")]))
    for gene in ("CYP2D6", "CYP2B6", "CYP4F2", "G6PD"):
        assert f"⚠ {gene} : Copy number was not assessed" in notes, gene
    for gene in ("CYP2C19", "SLCO1B1", "DPYD"):
        assert f"⚠ {gene} : Copy number not assessed (PyPGx has no" in notes, gene
    document, _ = generator_module.render_pdf_document(page)
    assert len(section_pages(document, "exec-summary")) == 1
    ((_, used),) = section_fragments(document, "exec-summary")
    assert used > 0.99, used
    assert len(section_pages(document, "exec-summary-notes")) == 1, notes
    # With room to spare, not just inside the page.
    ((_, notes_used),) = section_fragments(document, "exec-summary-notes")
    assert notes_used < 0.9, notes_used
    assert "⚠ ABCG2, CACNA1S, CFTR" in notes


def test_several_positions_notes_are_one_entry():
    rows = [
        {
            "gene": "CFTR",
            "positions_note": "5 of 93 positions had no genotype in "
            "the file and were read as reference",
        },
        {
            "gene": "RYR1",
            "positions_note": "6 of 319 positions had no genotype in "
            "the file and were read as reference",
        },
        {
            "gene": "DPYD",
            "positions_note": "1 of 20 positions had no reads in the "
            "alignment and were read as reference",
        },
    ]
    note = positions_summary_note(rows)
    assert note["genes"] == "CFTR, RYR1 and DPYD"
    assert note["note"] == (
        "CFTR 5 of 93, RYR1 6 of 319 positions had no genotype in the file and were "
        "read as reference; DPYD 1 of 20 positions had no reads in the alignment and "
        "were read as reference, so a variant there would not have been seen and "
        "these calls may be wrong. Genetic Results notes each beside its call."
    )


def test_the_summary_notes_are_escaped_once():
    """Review: listed_alleles_note and positions_summary_note escaped their text,
    then the template's autoescape escaped it again ("&amp;amp;")."""
    from jinja2 import Environment

    from app.reports.copy_number import positions_summary_note

    env = Environment(autoescape=True)
    out = env.from_string("{{ n.genes }}: {{ n.note }}").render(
        n=positions_summary_note(
            [
                {"gene": "A&B", "positions_note": "1 of 2 positions <x>"},
                {"gene": "C<D", "positions_note": "1 of 2 positions <y>"},
            ]
        )
    )
    assert "A&amp;B" in out and "C&lt;D" in out and "&lt;x&gt;" in out, out
    assert "&amp;amp;" not in out and "&amp;lt;" not in out, out


def test_a_structural_allele_pypgx_found_beside_pharmcat_s_call_is_flagged(
    monkeypatch,
):
    """Review: if PyPGx measured a copy number other than normal for a gene whose
    call shown is PharmCAT's, the summary must not use the softer line."""
    from app.reports import copy_number as cn

    monkeypatch.setattr(cn, "pharmcat_called", lambda row: True)
    monkeypatch.setattr(cn, "pypgx_copy_number", lambda results, gene: "Hybrid1")
    row = {"gene": "CYP2B6", "diplotype": "*1/*6"}
    entry = cn.listed_alleles_entry(row, {})
    assert entry["marker"] == cn.FOUND_MARKER and entry["warn"] is True, entry
    # In words, without PyPGx's code.
    assert entry["call_note"] == (
        "PyPGx found a hybrid gene, which this call cannot show (see Alerts)"
    )
    assert "Hybrid1" not in entry["note"] and "may be wrong" in entry["note"]
    rows = [dict(row, copy_number_not_assessed=dict(entry, listed=True))]
    text = _clean(cn.pharmcat_copy_number_alert(rows, {}))
    assert text.startswith(
        "⚠️ Structural allele found: CYP2B6. PyPGx measured copy number that "
        "PharmCAT's call, from small variants, cannot show: CYP2B6, a hybrid gene. "
        "This call may be wrong, and so may the phenotype."
    )
    assert "Hybrid1" not in text
    # PyPGx's Tandem1, which its genotyper leaves unnamed, is *30.
    monkeypatch.setattr(cn, "pypgx_copy_number", lambda results, gene: "Tandem1")
    entry = cn.listed_alleles_entry(row, {})
    # Short under the call, in a narrow column; in full in the note and the alert.
    assert entry["call_note"] == (
        "PyPGx found *30, a hybrid duplication, which this call cannot show (see Alerts)"
    )
    assert entry["note"].startswith(
        "PyPGx found the *30 hybrid duplication (CYP2B6 exons 1-4 joined to CYP2B7 "
        "exons 5-9, beside an intact CYP2B6), which PharmCAT's call"
    )
    monkeypatch.setattr(cn, "pypgx_copy_number", lambda results, gene: "Normal")
    assert cn.listed_alleles_entry(row, {}) is None
    assert cn.listed_alleles_entry({"gene": "CYP4F2", "diplotype": "*1/*1"}, {}) is None


# --------------------------------------------------------------------------
# CYP2C19's and SLCO1B1's deletions, checked from read depth
# --------------------------------------------------------------------------

STAR37 = "CYP2C19 *37: one copy of exons 1-5 deleted"
EXONS_2_5 = (
    "a deletion of one copy of exons 2-5, which is no star allele CPIC assigns a "
    "function (it keeps exon 1, so it is not *37; Krebs et al. 2026 proposed *42 for "
    "a deletion of exons 2-5)"
)


def _checked(diplotype, code, found=(), snv=None):
    """A gene the sidecar's deletion check measured (docker/pypgx's
    apply_deletion_check)."""
    result = _result(diplotype, code)
    result["details"]["copy_number_by"] = "deletion_check"
    result["details"]["deletion_check"] = {
        "status": "measured",
        "deletions": [{"description": text} for text in found],
    }
    if snv:
        result["details"]["snv_diplotype"] = snv
    return result


def test_a_deletion_in_the_call_is_said_beside_it_and_in_the_alert():
    """HG00268's CYP2C19: *1/*37 went to PharmCAT as an outside call; from small
    variants alone it read *1/*1, Normal Metabolizer."""
    rows = [
        _pypgx_row("CYP2C19", "*1/*37"),
        {"gene": "SLCO1B1", "diplotype": "*1/*15", "call_source": "MATCHER"},
    ]
    pypgx = {
        "results": {
            "CYP2C19": _checked("*1/*37", "PartialDel1", [STAR37], snv="*1/*1"),
            "SLCO1B1": _checked("*1/*15", "Normal"),
        }
    }
    annotate_pharmcat_copy_number(rows, pypgx)
    entry = rows[0]["copy_number_not_assessed"]
    assert entry["kind"] == "deletion" and entry["warn"] is True
    assert entry["marker"] == "Deletion found from read depth"
    assert entry["call_note"] == "Deletion found from read depth (see Alerts)"
    assert entry["note"] == (
        f"Read depth shows {STAR37}. The call includes it (from small variants "
        "alone, *1/*1). Confirm it with an independent test. See Alerts and Warnings."
    )
    # Measured normal: nothing beside the call.
    assert "copy_number_not_assessed" not in rows[1]
    assert measured_normal_genes(rows, pypgx) == ["SLCO1B1"]
    text = _clean(pharmcat_copy_number_alert(rows, pypgx))
    assert text.startswith(
        f"⚠️ Deletion found: CYP2C19. Read depth shows {STAR37}. PharmCAT was given "
        "the call with it, *1/*37; from small variants alone it reads *1/*1."
    )
    assert "Confirm a deletion with an independent test before acting on it." in text
    assert (
        "Read depth showed no deletion in SLCO1B1 (*48, *49), which ZaroPGx checks "
        "from the same depth and control gene, PyPGx having no copy-number model for "
        "it." in text
    )
    assert "not assessed" not in text and "PyPGx found" not in text


def test_a_deletion_the_call_shown_cannot_show():
    """HG01771's CYP2C19: a deletion of exons 2-5, which no star allele with a
    function names, so PharmCAT keeps its call from small variants, and the report
    says it may be wrong."""
    rows = [{"gene": "CYP2C19", "diplotype": "*1/*1", "call_source": "MATCHER"}]
    pypgx = {"results": {"CYP2C19": _checked("*1/*1", "PartialDel1", [EXONS_2_5])}}
    annotate_pharmcat_copy_number(rows, pypgx)
    entry = rows[0]["copy_number_not_assessed"]
    assert entry["kind"] == "found_deletion" and entry["warn"] is True
    assert entry["call_note"] == (
        "Deletion found from read depth, which this call cannot show (see Alerts)"
    )
    assert entry["note"].startswith(f"Read depth shows {EXONS_2_5}, which this call")
    text = _clean(pharmcat_copy_number_alert(rows, pypgx))
    assert (
        "The call shown, from small variants, cannot show it: the call and its "
        "phenotype may be wrong." in text
    )
    assert "PharmCAT was given" not in text


def test_checked_genes_measured_normal_say_so_once(monkeypatch, tmp_path):
    """A whole genome under auto with the check: CYP2C19 and SLCO1B1 are no longer
    "not assessed", and the alert says once that no deletion was found."""
    pypgx = _wgs_run()
    pypgx["results"]["CYP2B6"] = _result("*1/*6", "Normal", cnv_model=True)
    pypgx["results"]["CYP4F2"] = _result("*1/*3", "Normal", cnv_model=True)
    pypgx["results"]["CYP2C19"] = _checked("*1/*2", "Normal")
    pypgx["results"]["SLCO1B1"] = _checked("*1/*15", "Normal")
    pypgx["results"]["DPYD"] = _checked("Reference/Reference", "Normal")
    result = _report(monkeypatch, tmp_path, pypgx, file_type="cram")
    page = (tmp_path / result["html_path"].split("/")[-1]).read_text()
    body = unescape(_clean(page.split("</style>")[-1]))
    for gene in ("CYP2C19", "SLCO1B1"):
        row = page[page.index(f'id="summary-{gene}"') :]
        assert "see the notes" not in row[: row.index("</tr>")]
    alerts = body[: body.index("Executive Summary")]
    assert "not assessed" not in alerts
    assert (
        "Read depth showed no deletion in CYP2C19 (*36, *37), DPYD (of one exon or "
        "more) or SLCO1B1 (*48, *49)" in alerts
    )
    assert body.count("Read depth showed no deletion in") == 1


DPYD_EXON_4 = "a deletion of one copy of DPYD exon 4"


def test_a_dpyd_deletion_is_an_alert_and_leaves_pharmcats_call():
    """HG00325 (GeT-RM's DPYD study): one copy of exon 4 deleted. No allele names it,
    so PharmCAT's call and phenotype stand, and the report says what they cannot
    show and how to confirm it before a fluoropyrimidine."""
    rows = [
        {
            "gene": "DPYD",
            "diplotype": "Reference/Reference",
            "call_source": "MATCHER",
            "phenotype": "Normal Metabolizer",
        }
    ]
    pypgx = {
        "results": {
            "DPYD": _checked("Reference/Reference", "PartialDel1", [DPYD_EXON_4])
        }
    }
    annotate_pharmcat_copy_number(rows, pypgx)
    entry = rows[0]["copy_number_not_assessed"]
    assert entry["kind"] == "found_deletion" and entry["warn"] is True
    assert entry["call_note"] == (
        "Deletion found from read depth, which this call cannot show (see Alerts)"
    )
    assert entry["note"] == (
        f"Read depth shows {DPYD_EXON_4}, which this call, from small variants, cannot "
        "show. DPD activity may be reduced: confirm it with an independent test, such "
        "as DPD phenotyping (DPD activity or plasma uracil) or a DPYD copy-number "
        "assay, before fluoropyrimidine treatment. See Alerts and Warnings."
    )
    # The phenotype is PharmCAT's, unchanged.
    assert rows[0]["phenotype"] == "Normal Metabolizer"
    text = unescape(_clean(pharmcat_copy_number_alert(rows, pypgx)))
    assert text.startswith(
        f"⚠️ Deletion found: DPYD. Read depth shows {DPYD_EXON_4}. CPIC's DPYD table "
        "assigns no function to a deletion of DPYD exons, and PharmCAT's call, from "
        "small variants, cannot show one, so its phenotype does not take it into "
        "account. Deletions of DPYD exons have been found in patients with DPD "
        "deficiency (van Kuilenburg et al., Hum Genet 2009; Saarenheimo et al., Cancer "
        "Chemother Pharmacol 2021). DPD activity may be reduced: confirm it with an "
        "independent test"
    )
    assert "before fluoropyrimidine treatment." in text
    assert "PyPGx has no copy-number model for DPYD; ZaroPGx checks it" in text
    # How to confirm it is said once.
    assert "Confirm a deletion with an independent test before acting on it" not in text
    assert "not assessed" not in text


def test_dpyd_measured_normal_is_said_with_the_other_checked_genes():
    rows = [
        {"gene": "CYP2C19", "diplotype": "*1/*2", "call_source": "MATCHER"},
        {"gene": "DPYD", "diplotype": "Reference/Reference", "call_source": "MATCHER"},
    ]
    pypgx = {
        "results": {
            "CYP2C19": _checked("*1/*2", "Normal"),
            "DPYD": _checked("Reference/Reference", "Normal"),
        }
    }
    annotate_pharmcat_copy_number(rows, pypgx)
    assert all("copy_number_not_assessed" not in r for r in rows)
    text = _clean(pharmcat_copy_number_alert(rows, pypgx))
    assert text.startswith(
        "Structural alleles: Read depth showed no deletion in CYP2C19 (*36, *37) or "
        "DPYD (of one exon or more), which ZaroPGx checks from the same depth and "
        "control gene, PyPGx having no copy-number model for them."
    )
    assert "⚠" not in text
