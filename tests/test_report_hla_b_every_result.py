"""HLA-B states every result PharmCAT gives it, not the first.

PharmCAT gives HLA-B one phenotype per risk allele it screens, in one list and
always in the order *15:02, *57:01, *58:01: ["*15:02 negative", "*57:01 positive",
"*58:01 negative"]. The diplotype rows the parser stored, and the rows the data
service built from PharmCAT's blocks, kept the first. On 30x NA12878 (HLA-B
*08:01/*56:01) the gene table and the Executive Summary read "*15:02 negative". For
a *57:01 carrier (abacavir) or a *58:01 carrier (allopurinol) they would have read
the same: a clean-looking result beside guidance that says otherwise, and the FHIR
export would have sent it. The drug recommendations, PharmCAT's own, were right.

Now the phenotype is every result, joined with ", " in PharmCAT's order as the JSON
lane already wrote it (report_json.diplotype_phenotype): in the stored rows, in
runs stored before (restated from the gene's stored JSON), in a recommendation's
"Applies if" conditions and in the FHIR export. A positive result colours the badge
red wherever it falls in the list, and the Executive Summary's pill, too long for one
line, breaks after a comma, not between an allele and its result.

HLA-B's block below is PharmCAT 3.4.0's for an outside call *08:01/*57:01, trimmed:
its example VCF, run with NA12878's HLA types (public, GIAB) and B*56:01 swapped for
B*57:01. Every other gene PharmCAT 3.4 gives a phenotype has one per diplotype (its
phenotype tables hold one string for each); HLA-A screens one allele, *31:01.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
from html import unescape
from pathlib import Path

import pytest

import app.reports.generator as generator_module
from app.pharmcat.pharmcat_client import normalize_pharmcat_results
from app.pharmcat.pharmcat_parser import PharmCATDiplotype, PharmCATParser
from app.pharmcat.report_json import annotation_conditions, diplotype_phenotype
from app.reports.evidence import change_genes
from app.reports.generator import phenotype_badge_class

REPO = Path(__file__).resolve().parents[1]
REPORT_JSON = REPO / "test_data" / "pharmcat.example.v340.report.json"
SCRIPT = REPO / "app" / "static" / "js" / "pgx-report.js"

CARRIER = "*15:02 negative, *57:01 positive, *58:01 negative"


def _allele(name):
    return {
        "gene": "HLA-B",
        "name": name,
        "function": None,
        "reference": False,
        "activityValue": None,
    }


def _diplotype(results=("*15:02 negative", "*57:01 positive", "*58:01 negative")):
    return {
        "allele1": _allele("*08:01"),
        "allele2": _allele("*57:01"),
        "gene": "HLA-B",
        "matchScore": 0,
        "phenotypes": list(results),
        "outsidePhenotype": False,
        "activityScore": None,
        "outsideActivityScore": False,
        "variant": None,
        "lookupKey": list(results),
        "label": "*08:01/*57:01",
        "inferred": False,
        "combination": False,
        "diplotypeKey": {},
    }


HLA_B = {
    "alleleDefinitionVersion": None,
    "alleleDefinitionSource": "UNKNOWN",
    "phenotypeVersion": None,
    "geneSymbol": "HLA-B",
    "chr": None,
    "phased": False,
    "effectivelyPhased": False,
    "callSource": "OUTSIDE",
    "uncalledHaplotypes": [],
    "messages": [],
    "relatedDrugs": [{"name": "abacavir", "id": "PA448004"}],
    "sourceDiplotypes": [_diplotype()],
    "recommendationDiplotypes": [_diplotype()],
    "variants": [],
    "variantsOfInterest": [],
    "hasUndocumentedVariations": False,
    "treatUndocumentedVariationsAsReference": False,
}

# CPIC abacavir's annotation for the call above, trimmed: looked up by the one
# result it rests on, its genotype the whole call.
ABACAVIR = {
    "classification": "Strong",
    "population": "general",
    "genotypes": [{"diplotypes": [_diplotype()]}],
    "dosingInformation": False,
    "alternateDrugAvailable": True,
    "otherPrescribingGuidance": False,
    "lookupKey": [{"HLA-B": "*57:01 positive"}],
}


def _report():
    report = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    report["genes"]["HLA-B"] = copy.deepcopy(HLA_B)
    return report


def _clean(fragment):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


# ---------------------------------------------------------------------------
# The phenotype
# ---------------------------------------------------------------------------


def test_every_result_in_pharmcats_order():
    assert diplotype_phenotype(HLA_B["sourceDiplotypes"][0]) == CARRIER
    # One phenotype, as every other gene has: as PharmCAT wrote it.
    assert diplotype_phenotype({"phenotypes": ["Normal Metabolizer"]}) == (
        "Normal Metabolizer"
    )
    assert diplotype_phenotype({"phenotypes": []}) == ""
    assert diplotype_phenotype({"phenotypes": None}) == ""
    assert diplotype_phenotype({}) == ""


def test_the_stored_row_keeps_every_result(db_session):
    PharmCATParser(db_session).parse_and_load(_report(), run_id="run-1")
    stored = (
        db_session.query(PharmCATDiplotype)
        .filter_by(run_id="run-1", gene_symbol="HLA-B")
        .one()
    )
    assert stored.phenotype == CARRIER


def test_the_database_lane_reports_every_result(db_session):
    from app.services.pharmcat_data_service import PharmCATDataService

    PharmCATParser(db_session).parse_and_load(_report(), run_id="run-1")
    data = PharmCATDataService(db_session).get_pharmcat_data_by_run_id("run-1")
    genes = {g["gene"]: g for g in data["genes"]}
    assert genes["HLA-B"]["diplotype"] == "*08:01/*57:01"
    assert genes["HLA-B"]["phenotype"] == CARRIER
    # The lanes agree.
    json_lane = {
        g["gene"]: g for g in normalize_pharmcat_results(_report())["data"]["genes"]
    }
    assert json_lane["HLA-B"]["phenotype"] == CARRIER


def test_a_run_stored_with_the_first_result_alone_reports_every_one(db_session):
    """Runs parsed before the fix hold "*15:02 negative" in their diplotype row; the
    gene's stored JSON holds PharmCAT's block, so the report and the FHIR export
    state every result without the run being parsed again."""
    from app.services.pharmcat_data_service import PharmCATDataService

    PharmCATParser(db_session).parse_and_load(_report(), run_id="run-1")
    db_session.query(PharmCATDiplotype).filter_by(
        run_id="run-1", gene_symbol="HLA-B"
    ).update({"phenotype": "*15:02 negative"})
    db_session.commit()
    data = PharmCATDataService(db_session).get_pharmcat_data_by_run_id("run-1")
    genes = {g["gene"]: g for g in data["genes"]}
    assert genes["HLA-B"]["phenotype"] == CARRIER
    # Single-phenotype genes come out as stored.
    assert genes["CYP2C19"]["phenotype"] == "Normal Metabolizer"


def test_the_recommendation_call_keeps_every_result():
    """Where the data service shows PharmCAT's recommendation call (the source is
    not one diplotype), it built the row from the block with the first result."""
    from app.services.pharmcat_data_service import _diplotype_row

    assert _diplotype_row(_diplotype())["phenotype"] == CARRIER
    assert _diplotype_row(_diplotype(results=()))["phenotype"] is None


def test_the_fhir_export_states_every_result(db_session):
    from app.services.fhir_export_service import FHIRExportService

    PharmCATParser(db_session).parse_and_load(_report(), run_id="run-1")
    result = FHIRExportService(db_session).export_pgx_report(run_id="run-1")
    assert result["success"], result.get("error")
    # Main states the phenotype as a component of the genotype observation; the
    # Genomics Reporting IG work (#124) moves it to its own therapeutic-implication
    # observation, coded. Either way it must carry every result.
    resources = [entry["resource"] for entry in result["bundle"]["entry"]]
    in_genotype = [
        c["valueCodeableConcept"]["text"]
        for r in resources
        if r.get("code", {}).get("text") == "HLA-B Genotype"
        for c in r.get("component", [])
        if c["code"].get("text") == "Phenotype"
    ]
    implications = [
        c["valueCodeableConcept"]
        for r in resources
        if r.get("code", {}).get("text") == "HLA-B phenotype"
        for c in r.get("component", [])
    ]
    assert in_genotype + [c["text"] for c in implications] == [CARRIER]
    for concept in implications:
        # A *57:01 carrier is High risk, not the Low risk of "*15:02 negative".
        assert [c["code"] for c in concept.get("coding", [])] == ["LA19541-4"]


# ---------------------------------------------------------------------------
# The guidance
# ---------------------------------------------------------------------------


def test_a_recommendations_conditions_state_the_results_as_one_call():
    """ "Applies if" read "HLA-B *15:02 negative or *57:01 positive or *58:01
    negative": " or " is how the report names candidates PharmCAT could not choose
    between, and these results hold at once."""
    assert annotation_conditions(ABACAVIR) == [("HLA-B", CARRIER, ["*08:01/*57:01"])]
    # Candidates are still alternatives, each with every result of its own.
    two = copy.deepcopy(ABACAVIR)
    other = _diplotype(
        results=("*15:02 negative", "*57:01 negative", "*58:01 negative")
    )
    other["label"] = "*08:01/*56:01"
    two["genotypes"].append({"diplotypes": [other]})
    ((_, phenotype, labels),) = annotation_conditions(two)
    assert phenotype == (
        CARRIER + " or *15:02 negative, *57:01 negative, *58:01 negative"
    )
    assert labels == ["*08:01/*57:01", "*08:01/*56:01"]


def test_a_positive_result_is_not_an_all_clear():
    assert change_genes(ABACAVIR) == ["HLA-B"]


# ---------------------------------------------------------------------------
# The colour: a positive result anywhere in the list is red
# ---------------------------------------------------------------------------

RESULTS = {
    "*15:02 positive, *57:01 negative, *58:01 negative": "poor-metabolizer",
    CARRIER: "poor-metabolizer",
    "*15:02 negative, *57:01 negative, *58:01 positive": "poor-metabolizer",
    "*15:02 negative, *57:01 negative, *58:01 negative": "normal-metabolizer",
}


@pytest.mark.parametrize("phenotype, css_class", RESULTS.items())
def test_the_badge_is_red_for_a_positive_result_anywhere(phenotype, css_class):
    assert phenotype_badge_class(phenotype) == css_class


GENE_COLOR = r"""
const fs = require('fs');
const vm = require('vm');
const context = { document: { addEventListener() {} }, console };
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
context.phenotypes = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(vm.runInContext('phenotypes.map(geneColor)', context)));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_interactive_reports_hub_is_red_for_a_positive_result_anywhere():
    proc = subprocess.run(
        [shutil.which("node"), "-e", GENE_COLOR, str(SCRIPT)],
        input=json.dumps(list(RESULTS)),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    red, green = "#e74c3c", "#2ecc71"
    assert json.loads(proc.stdout) == [red, red, red, green]


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def _report_html(monkeypatch, tmp_path, data):
    monkeypatch.setattr(generator_module, "EXECSUM_USE_TSV", False)
    for key in list(generator_module.REPORT_CONFIG):
        if key.startswith(("write_", "show_")):
            monkeypatch.setitem(
                generator_module.REPORT_CONFIG,
                key,
                key in ("write_html", "write_interactive_html"),
            )
    result = generator_module.generate_report(
        {"data": data}, str(tmp_path), {"id": "p1"}, job_id=None
    )
    return (tmp_path / result["html_path"].split("/")[-1]).read_text(encoding="utf-8")


def test_the_report_says_the_positive_result(monkeypatch, tmp_path, db_session):
    """The database lane, as a job's report reads it."""
    from app.services.pharmcat_data_service import PharmCATDataService

    PharmCATParser(db_session).parse_and_load(_report(), run_id="run-1")
    data = PharmCATDataService(db_session).get_pharmcat_data_by_run_id("run-1")
    page = _report_html(monkeypatch, tmp_path, data)

    summary = page[
        page.index('id="exec-summary"') : page.index("<h2>Genetic Results</h2>")
    ]
    row = re.search(r'<tr id="summary-HLA-B">(.*?)</tr>', summary, re.S).group(1)
    pill = re.search(r'<span class="gene-phenotype ([^"]*)">(.*?)</span>', row, re.S)
    assert (pill.group(1), _clean(pill.group(2))) == ("poor-metabolizer", CARRIER)

    genes = page[page.index("<h2>Genetic Results</h2>") :]
    cells = [
        _clean(c)
        for c in re.search(r"<tr>\s*<td>HLA-B</td>(.*?)</tr>", genes, re.S)
        .group(1)
        .split("</td>")
    ]
    assert cells[:2] == ["*08:01/*57:01", CARRIER]


def _text(box):
    if getattr(box, "text", None):
        return box.text
    return "".join(_text(child) for child in getattr(box, "children", None) or [])


def test_the_summary_pill_breaks_between_results_and_keeps_its_page(
    monkeypatch, tmp_path, db_session
):
    """Three results are more than one line of the Executive Summary holds. The
    pill broke between "*58:01" and "negative"; now every line starts with an
    allele, and the summary still ends on its one page."""
    pytest.importorskip("weasyprint")
    from app.reports.pdf_fit import room_under, section_pages, summary_pill_lines
    from app.services.pharmcat_data_service import PharmCATDataService

    PharmCATParser(db_session).parse_and_load(_report(), run_id="run-1")
    data = PharmCATDataService(db_session).get_pharmcat_data_by_run_id("run-1")
    document, _ = generator_module.render_pdf_document(
        _report_html(monkeypatch, tmp_path, data)
    )
    (index,) = section_pages(document, "exec-summary")
    assert room_under(document, "exec-summary") >= 0
    pills = dict(summary_pill_lines(document.pages[index]._page_box))
    lines = [_text(line).replace(" ", " ").strip() for line in pills["summary-HLA-B"]]
    assert len(lines) > 1, lines
    assert " ".join(lines) == CARRIER
    assert all(line.startswith("*") for line in lines), lines
