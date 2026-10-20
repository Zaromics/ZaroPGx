"""A genotype with no call says why, coded.

Since fix/fhir-no-empty-values every gene the run did not call had the genotype
valueCodeableConcept {"text": "Unknown"}, whether a tool tested the gene and could not
call it or nothing tested it at all (HLA from a VCF; F2 and F5 where PyPGx has nothing
for them). A reader could tell "not tested" from "tested, no call" only by matching
text, and not even that: both read "Unknown".

The IG's genotype profile (Genomics Reporting 3.0.0) requires value[x] (1..1), with an
example binding, and obs-6 forbids dataAbsentReason beside a value. So the reason goes
in the value, as a coding of HL7's DataAbsentReason code system, decided by markers the
report step puts on the gene rows (app/reports/generator.py):

- not-performed: not_tested. _build_canonical_diplotypes' placeholder rows (no tool
  reported the gene), and the genes only an outside-call tool supplies when that tool
  did not run: HLA-A, HLA-B and HLA-C without hla_result.json (no HLA typing, as in
  every VCF run), MT-RNR1 without mtdna_result.json.
- error: call_error. PyPGx tried the gene and failed ({"success": False}).
- unknown: tested and not called -- "Unknown", "Unknown/Unknown", "Indeterminate".

HAPI FHIR 8.12's $validate, with the IG and HL7 Terminology 6.1.0 loaded, checks the
code (a bogus one is an error) and gave the two release-candidate bundles rebuilt this
way no errors.
"""

from __future__ import annotations

import json
import tarfile
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from app.reports import generator as generator_module
from app.services.fhir import terminology
from app.services.fhir_export_service import FHIRExportService

REPO_ROOT = Path(__file__).resolve().parent.parent
TERMINOLOGY_PACKAGE = (
    REPO_ROOT / "docker" / "fhir-server" / "packages" / "hl7.terminology.r4-6.1.0.tgz"
)
DAR = "http://terminology.hl7.org/CodeSystem/data-absent-reason"

NOT_PERFORMED = {
    "coding": [{"system": DAR, "code": "not-performed", "display": "Not Performed"}],
    "text": "Not performed",
}
ERROR = {
    "coding": [{"system": DAR, "code": "error", "display": "Error"}],
    "text": "Error",
}


def _unknown(text: str) -> Dict[str, Any]:
    return {
        "coding": [{"system": DAR, "code": "unknown", "display": "Unknown"}],
        "text": text,
    }


# Rows as the report step builds them from the VCF run of PharmCAT's example VCF.
VCF_RUN_ROWS: List[Dict[str, Any]] = [
    # PharmCAT called it.
    {
        "gene": "CYP2C19",
        "diplotype": "*38/*38",
        "phenotype": "Normal Metabolizer",
        "call_source": "MATCHER",
    },
    # PharmCAT lists HLA-A and HLA-B, callSource NONE, with no outside call to take.
    {
        "gene": "HLA-A",
        "diplotype": "Unknown/Unknown",
        "phenotype": "Unknown",
        "call_source": "NONE",
    },
    {"gene": "HLA-B", "diplotype": "Unknown/Unknown", "call_source": "NONE"},
    # The same for MT-RNR1, though mtDNA calling ran and found no chrM.
    {"gene": "MT-RNR1", "diplotype": "Unknown", "call_source": "NONE"},
    # PharmCAT tested NAT2 and could not call it.
    {"gene": "NAT2", "diplotype": "Unknown/Unknown", "call_source": "MATCHER"},
    # PyPGx reported it and made no call: the enrichment's "Unknown".
    {
        "gene": "ABCB1",
        "diplotype": "Unknown",
        "phenotype": None,
        "tool_source": "PyPGx",
        "pyPgxOnly": True,
        "no_call_note": "No genotype in the VCF at any of PyPGx's positions",
    },
    # PyPGx's own word for no call.
    {"gene": "CYP2A6", "diplotype": "Indeterminate", "tool_source": "PyPGx"},
    # One allele called is a call (generator.gene_was_called).
    {"gene": "CYP2D6", "diplotype": "*1/Unknown", "tool_source": "PyPGx"},
]


def _rows(
    raw, report_dir: Path, failed=None, files=("mtdna_result.json",), pypgx_ran=None
):
    """The canonical rows as generate_report leaves them: built, then marked from
    what the run left in its report directory."""
    for name in files:
        (report_dir / name).write_text("{}", encoding="utf-8")
    rows = generator_module._build_canonical_diplotypes(
        raw_gene_entries=[dict(row) for row in raw],
        file_type="vcf",
        workflow_config=None,
    )
    generator_module.mark_not_tested_and_failed(
        rows, str(report_dir), failed or {}, pypgx_ran
    )
    return rows


def _bundle(rows: List[Dict[str, Any]], run_id: str = "run-uncalled"):
    result = FHIRExportService(MagicMock()).export_pgx_report(
        run_id=run_id,
        output_format="json",
        pharmcat_data={"genes": rows, "drugRecommendations": []},
    )
    assert result["success"], result.get("error")
    return result["bundle"]


def _genotypes(bundle: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        entry["resource"]["code"]["text"].removesuffix(" Genotype"): entry["resource"]
        for entry in bundle["entry"]
        if entry["resource"]["resourceType"] == "Observation"
        and entry["resource"]["code"].get("text", "").endswith(" Genotype")
    }


def _values(rows) -> Dict[str, Dict[str, Any]]:
    return {g: o["valueCodeableConcept"] for g, o in _genotypes(_bundle(rows)).items()}


def test_vcf_run_genes_nothing_tested_are_coded_not_performed(tmp_path):
    """No tool reported F2, F5 or HLA-C; no HLA typing ran, so PharmCAT's uncalled
    HLA-A and HLA-B were not tested either."""
    values = _values(_rows(VCF_RUN_ROWS, tmp_path))
    for gene in ("F2", "F5", "HLA-A", "HLA-B", "HLA-C"):
        assert values[gene] == NOT_PERFORMED, gene


def test_genes_tested_and_not_called_are_coded_unknown_in_the_tools_words(tmp_path):
    values = _values(_rows(VCF_RUN_ROWS, tmp_path))
    assert values["NAT2"] == _unknown("Unknown/Unknown")
    assert values["MT-RNR1"] == _unknown("Unknown")
    assert values["ABCB1"] == _unknown("Unknown")
    assert values["CYP2A6"] == _unknown("Indeterminate")


def test_after_hla_typing_an_untyped_hla_gene_is_unknown(tmp_path):
    """hla_result.json says OptiType ran: an HLA gene it typed none for was tested.
    Without mtdna_result.json, mtDNA calling did not run: MT-RNR1 was not tested."""
    rows = _rows(VCF_RUN_ROWS, tmp_path, files=("hla_result.json",))
    values = _values(rows)
    assert values["HLA-A"] == _unknown("Unknown/Unknown")
    assert values["HLA-C"] == _unknown("Unknown")
    assert values["MT-RNR1"] == NOT_PERFORMED
    assert values["F2"] == NOT_PERFORMED


def test_a_gene_pypgx_failed_on_is_coded_error_not_not_performed(tmp_path):
    """A gene PyPGx raised on, alone or with its batch, is skipped by the enrichment
    and would end as a placeholder; it was tested, and the tool failed."""
    failed = generator_module.pypgx_failed_genes(
        {
            "F5": {"success": False, "error": "Batch processing error: boom"},
            "CYP2D6": {"success": False, "error": "Traceback ..."},
            "ABCB1": {"success": True, "diplotype": None},
            "IFNL4": {"success": True, "diplotype": "Reference/Reference"},
        }
    )
    assert set(failed) == {"F5", "CYP2D6"}
    raw = [r for r in VCF_RUN_ROWS if r["gene"] != "CYP2D6"] + [
        {"gene": "CYP2D6", "diplotype": "Unknown/Unknown", "call_source": "NONE"}
    ]
    rows = _rows(raw, tmp_path, failed=failed)
    by_gene = {row["gene"]: row for row in rows}
    assert by_gene["F5"]["call_error"] == generator_module.PYPGX_GENE_FAILED
    assert "not_tested" not in by_gene["F5"]
    values = _values(rows)
    assert values["F5"] == ERROR
    assert values["CYP2D6"] == ERROR
    assert values["F2"] == NOT_PERFORMED
    assert values["ABCB1"] == _unknown("Unknown")


def test_a_call_is_left_alone_whatever_the_markers(tmp_path):
    failed = {"CYP2C19": "PyPGx failed: x", "CYP2D6": "PyPGx failed: x"}
    rows = _rows(VCF_RUN_ROWS, tmp_path, failed=failed)
    values = _values(rows)
    assert values["CYP2C19"] == {"text": "*38/*38"}
    assert values["CYP2D6"] == {"text": "*1/Unknown"}
    assert "call_error" not in {row["gene"]: row for row in rows}["CYP2C19"]


@pytest.mark.parametrize(
    "diplotype", ["Indeterminate", "indeterminate", "INDETERMINATE ", "unknown/unknown"]
)
def test_no_call_is_matched_without_regard_to_case(diplotype):
    assert FHIRExportService._uncalled_reason({"diplotype": diplotype}) == "unknown"


@pytest.mark.parametrize("diplotype", ["", None])
def test_a_blank_without_the_marker_is_unknown_not_not_performed(diplotype):
    """A database-lane gene stored without a diplotype label carries no marker; it
    is not taken for one nothing tested."""
    values = _values([{"gene": "CYP2D6", "diplotype": diplotype}])
    assert values["CYP2D6"] == _unknown("Unknown")


def test_every_genotype_keeps_a_value_and_no_data_absent_reason(tmp_path):
    """value[x] 1..1 in the IG's genotype profile; obs-6 forbids dataAbsentReason
    beside it."""
    for gene, genotype in _genotypes(_bundle(_rows(VCF_RUN_ROWS, tmp_path))).items():
        assert genotype["valueCodeableConcept"]["text"].strip(), gene
        assert "dataAbsentReason" not in genotype, gene


def test_the_report_lists_a_gene_not_tested_as_such(tmp_path):
    report = _bundle(_rows(VCF_RUN_ROWS, tmp_path))["entry"][0]["resource"]
    displays = {ref["display"] for ref in report["result"]}
    assert {"F2 Not performed", "HLA-A Not performed", "ABCB1 Unknown"} <= displays


def test_the_ids_do_not_change_so_a_re_push_updates_in_place(tmp_path):
    """A genotype's id is named by the report and the gene, not by its value."""
    genotypes = _genotypes(_bundle(_rows(VCF_RUN_ROWS, tmp_path), run_id="run-ids"))
    for gene in ("F2", "HLA-A", "CYP2C19"):
        assert genotypes[gene]["id"] == FHIRExportService._stable_id(
            "genotype", "run-ids", gene
        )


def test_the_codes_are_hl7_terminologys_with_its_displays():
    """The codes and displays sent are those of the DataAbsentReason code system in
    the HL7 Terminology package the bundled FHIR server installs."""
    with tarfile.open(TERMINOLOGY_PACKAGE) as package:
        member = package.extractfile("package/CodeSystem-data-absent-reason.json")
        code_system = json.load(member)
    assert code_system["url"] == terminology.DATA_ABSENT_REASON == DAR

    def concepts(nodes):
        for node in nodes:
            yield node
            yield from concepts(node.get("concept", []))

    displays = {c["code"]: c["display"] for c in concepts(code_system["concept"])}
    assert set(terminology.DATA_ABSENT_REASON_DISPLAYS) == {
        "not-performed",
        "error",
        "unknown",
    }
    for code, display in terminology.DATA_ABSENT_REASON_DISPLAYS.items():
        assert displays[code] == display


def test_the_xml_bundle_carries_the_coding(tmp_path):
    rows = _rows(VCF_RUN_ROWS, tmp_path, failed={"F5": "PyPGx failed: x"})
    xml = FHIRExportService(MagicMock()).export_pgx_report(
        run_id="run-uncalled",
        output_format="xml",
        pharmcat_data={"genes": rows, "drugRecommendations": []},
    )["content"]
    assert f'<system value="{DAR}" />' in xml
    for code in ("not-performed", "error", "unknown"):
        assert f'<code value="{code}" />' in xml


# The PyPGx lane, as the job's own steps record it (load_job_step_records).
def _steps(pypgx_status=None):
    steps = [{"name": "header_analysis", "status": "completed", "output": {}}]
    if pypgx_status is not None:
        steps.append({"name": "pypgx_analysis", "status": pypgx_status, "output": {}})
    steps.append({"name": "pharmcat_analysis", "status": "completed", "output": {}})
    return steps


# What PharmCAT alone leaves when PyPGx produced nothing: CYP2D6 has no outside call.
PHARMCAT_ONLY_ROWS = [
    {"gene": "CYP2C19", "diplotype": "*38/*38", "call_source": "MATCHER"},
    {"gene": "CYP2D6", "diplotype": "Unknown/Unknown", "call_source": "NONE"},
    {"gene": "HLA-A", "diplotype": "Unknown/Unknown", "call_source": "NONE"},
]


@pytest.mark.parametrize("status", ["completed", "failed", "running", "pending", ""])
def test_pypgx_on_with_no_result_file_codes_its_genes_error(tmp_path, status):
    """The upload turned PyPGx on (its step exists), and it left no result file: the
    lane died, so its genes were not left untested; they are errors."""
    failed = generator_module.pypgx_lane_failed_genes(_steps(status), {})
    rows = _rows(PHARMCAT_ONLY_ROWS, tmp_path, failed=failed)
    values = _values(rows)
    for gene in ("ABCB1", "CYP2A6", "UGT2B17", "CYP2D6"):
        assert values[gene] == ERROR, gene
    by_gene = {row["gene"]: row for row in rows}
    assert by_gene["ABCB1"]["call_error"].startswith("PyPGx")
    # Not PyPGx's to call after a VCF: PharmCAT's, and the genes nothing reported.
    assert values["CYP2C19"] == {"text": "*38/*38"}
    assert values["HLA-A"] == NOT_PERFORMED
    assert values["F2"] == NOT_PERFORMED


@pytest.mark.parametrize("steps", [_steps(None), _steps("skipped")])
def test_pypgx_switched_off_leaves_its_genes_not_performed(tmp_path, steps):
    """No pypgx_analysis step (the upload left PyPGx off), or a skipped one: PyPGx
    was never to run, and its genes were not tested."""
    assert generator_module.pypgx_lane_failed_genes(steps, {}) == {}
    values = _values(_rows(PHARMCAT_ONLY_ROWS, tmp_path, pypgx_ran=False))
    assert values["ABCB1"] == NOT_PERFORMED
    # PharmCAT lists CYP2D6, but only PyPGx's outside call could have supplied it.
    assert values["CYP2D6"] == NOT_PERFORMED


def test_cyp2d6_without_a_call_after_pypgx_ran_is_unknown(tmp_path):
    """PyPGx results were read: CYP2D6 was tested; PharmCAT's NONE is no call."""
    values = _values(_rows(PHARMCAT_ONLY_ROWS, tmp_path, pypgx_ran=True))
    assert values["CYP2D6"] == _unknown("Unknown/Unknown")


def test_a_pypgx_lane_that_died_is_error_for_cyp2d6_too(tmp_path):
    """The failure is checked first: PyPGx was to run and left nothing."""
    failed = generator_module.pypgx_lane_failed_genes(_steps("pending"), {})
    values = _values(_rows(PHARMCAT_ONLY_ROWS, tmp_path, failed=failed, pypgx_ran=True))
    assert values["CYP2D6"] == ERROR


def test_a_pypgx_failure_leaves_pharmcat_s_own_no_call_unknown(tmp_path):
    """After an alignment PyPGx is asked for PharmCAT's genes too. A gene PharmCAT's
    matcher examined (call_source MATCHER) stays tested, not called, whatever
    PyPGx did; a gene only PyPGx was asked for is an error."""
    failed = generator_module.pypgx_lane_failed_genes(_steps("failed"), {}, "cram")
    assert {"NAT2", "F2", "ABCB1", "CYP2D6"} <= set(failed)
    values = _values(_rows(VCF_RUN_ROWS, tmp_path, failed=failed, pypgx_ran=True))
    assert values["NAT2"] == _unknown("Unknown/Unknown")
    assert values["F2"] == ERROR
    per_gene = generator_module.pypgx_failed_genes(
        {"NAT2": {"success": False, "error": "x"}}
    )
    values = _values(_rows(VCF_RUN_ROWS, tmp_path, failed=per_gene))
    assert values["NAT2"] == _unknown("Unknown/Unknown")


def test_an_error_genotype_says_why_in_a_short_note(tmp_path):
    failed = generator_module.pypgx_failed_genes(
        {"F5": {"success": False, "error": "Traceback /app/x.py line 3: boom"}}
    )
    genotypes = _genotypes(_bundle(_rows(VCF_RUN_ROWS, tmp_path, failed=failed)))
    assert genotypes["F5"]["note"] == [{"text": generator_module.PYPGX_GENE_FAILED}]
    assert "/app" not in json.dumps(genotypes["F5"])
    for gene in ("F2", "ABCB1", "CYP2C19"):
        assert "note" not in genotypes[gene], gene


def test_pypgx_that_finished_leaves_the_codes_unchanged(tmp_path):
    results = {
        "ABCB1": {"success": True, "diplotype": None},
        "CYP2D6": {"success": True, "diplotype": "*1/*1"},
    }
    assert generator_module.pypgx_lane_failed_genes(_steps("completed"), results) == {}
    values = _values(_rows(VCF_RUN_ROWS, tmp_path))
    assert values["ABCB1"] == _unknown("Unknown")
    assert values["CYP2A6"] == _unknown("Indeterminate")


def test_a_failed_step_with_results_keeps_pypgx_s_own_record(tmp_path):
    """A gene PyPGx reported keeps that record; one it never got to is an error."""
    results = {"ABCB1": {"success": True, "diplotype": None}}
    failed = generator_module.pypgx_lane_failed_genes(_steps("failed"), results)
    assert "ABCB1" not in failed and "CYP2A6" in failed
    raw = [r for r in VCF_RUN_ROWS if r["gene"] != "CYP2A6"]
    values = _values(_rows(raw, tmp_path, failed=failed))
    assert values["ABCB1"] == _unknown("Unknown")
    assert values["CYP2A6"] == ERROR


def test_no_step_record_says_nothing():
    """Without the job's steps (no database), absence alone decides nothing."""
    assert generator_module.pypgx_lane_failed_genes([], {}) == {}
    assert generator_module.pypgx_lane_failed_genes(None, None) == {}


# generate_report itself: PyPGx's results read from the report directory, the job's
# steps (load_job_step_records, monkeypatched: job_id=None has no database), and the
# rows it hands the FHIR export.
def _generate(monkeypatch, tmp_path, pypgx_file, step_status):
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
        monkeypatch.setitem(generator_module.REPORT_CONFIG, key, False)
    monkeypatch.setattr(
        generator_module,
        "load_job_step_records",
        lambda db_session, job_id: _steps(step_status),
    )
    (tmp_path / "pypgx_result.json").write_text(json.dumps(pypgx_file))
    (tmp_path / "mtdna_result.json").write_text("{}")
    genes = [dict(row) for row in PHARMCAT_ONLY_ROWS]
    result = generator_module.generate_report(
        {"data": {"genes": genes, "drugRecommendations": []}},
        str(tmp_path),
        {"id": "p1"},
        job_id=None,
    )
    return {g["gene"]: g for g in result["processed_data"]["genes"] if g.get("gene")}


def test_generate_report_with_pypgx_s_results_marks_no_error(monkeypatch, tmp_path):
    results = {
        "success": True,
        "results": {
            "ABCB1": {"success": True, "diplotype": None, "details": {}},
            "CYP2A6": {"success": True, "diplotype": "*1/*1", "details": {}},
        },
    }
    rows = _generate(monkeypatch, tmp_path, results, "completed")
    assert not [g for g, row in rows.items() if row.get("call_error")]
    assert rows["CYP2A6"]["diplotype"] == "*1/*1"
    assert "not_tested" not in rows["ABCB1"] and "not_tested" not in rows["CYP2D6"]
    assert rows["F2"]["not_tested"] is True


def test_generate_report_with_nextflow_s_fallback_file_marks_error(
    monkeypatch, tmp_path
):
    """main.nf writes this when the PyPGx service is down; the step is still pending."""
    fallback = {"success": False, "error": "PyPGx service unavailable", "results": {}}
    rows = _generate(monkeypatch, tmp_path, fallback, "pending")
    for gene in ("ABCB1", "CYP2A6", "CYP2D6"):
        assert rows[gene]["call_error"] == generator_module.PYPGX_NO_RESULT, gene
    assert "call_error" not in rows["CYP2C19"]
    assert rows["F2"]["not_tested"] is True
