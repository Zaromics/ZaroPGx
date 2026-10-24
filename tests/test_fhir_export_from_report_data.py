"""A FHIR export made later says what the report's own bundle says.

The report generator builds the job's bundle from the report's gene rows. An export
through the API afterwards (/fhir/export/run/{run_id}, /fhir/export/workflow/{job},
/fhir/save/...) was rebuilt from PharmCAT's rows in the database, so it left out what
ZaroPGx adds to them: PyPGx's genes (CYP1A2, which PharmCAT does not call), calls
corrected for copy number, and the variants no allele definition lists, with their
Variant observations (found 2026-10-24, adding those variants to the bundle). The
generator now keeps what it built the bundle from beside the report
(app/services/fhir/report_data.py), and a later export reads it back; a job reported
before has none and is exported from the database as it was.
"""

from __future__ import annotations

import copy
import json
import uuid

import pytest

from app.reports import unlisted
from app.services import fhir_export_service as fhir_module
from app.services.fhir import report_data
from app.services.fhir_export_service import FHIRExportService
from app.services.pharmcat_data_service import PharmCATDataService

RUN_ID = "5d0c7a2e-1b3f-4c8d-9e6a-2f4b8c1d3e70"
PATIENT = "patient-1"

STOP = {
    "chrom": "chr1", "pos": 97082449, "ref": "G", "alt": "A",
    "gene": "DPYD", "genes": ["DPYD"], "transcript": "ENST0", "refseq": "NM_000110.4",
    "consequence": "stop gained", "hgvs_c": "c.2788C>T", "hgvs_p": "p.Gln930Ter",
    "zygosity": "heterozygous", "quality": [], "defined_position": False,
    "gnomad": None, "alphamissense": None, "clinvar": None, "low_confidence": None,
    "tier": "A", "label": "Predicted loss of function",
}


def _report_data():
    """The report's data as the FHIR export is given it: a PharmCAT call, a gene only
    PyPGx calls, a call corrected for copy number, and a call an unlisted variant marks."""
    genes = [
        {"gene": "CYP2C19", "diplotype": "*1/*2", "phenotype": "Intermediate Metabolizer"},
        {"gene": "CYP1A2", "diplotype": "*1A/*1F", "phenotype": "N/A", "tool_source": "PyPGx"},
        {
            "gene": "CYP2D6", "diplotype": "*1/*5", "phenotype": "Intermediate Metabolizer",
            "activity_score": 1.0, "tool_source": "PyPGx",
        },
        {"gene": "DPYD", "diplotype": "Reference/Reference", "phenotype": "Normal Metabolizer"},
    ]
    unlisted.annotate_genes(genes, unlisted.context({
        "status": "done", "genes_checked": ["DPYD"], "genes_without_transcript": [],
        "variants_listed": 0, "variants": [STOP], "flagged_genes": {"DPYD": [0]},
        "annotation": {"sources": {}},
    }))
    assert genes[3]["unlisted_flag"]
    return {"run_id": RUN_ID, "genes": genes, "drugRecommendations": []}


def _job(session):
    from app.api.models import JobCreate
    from app.services.job_service import JobService

    jobs = JobService(session)
    job = jobs.create_job(JobCreate(workflow_type="genomic_analysis", name="fhir-source-probe"))
    session.commit()
    assert jobs.link_pharmcat_run(job.id, RUN_ID)
    from app.api.db import Job

    row = session.query(Job).filter(Job.id == job.id).first()
    row.job_metadata = {**(row.job_metadata or {}), "patient_id": PATIENT}
    session.commit()
    return str(job.id)


def _differences(a, b, path="bundle"):
    """Where two JSON values differ, as paths."""
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            yield from _differences(a.get(key), b.get(key), f"{path}.{key}")
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            yield from _differences(x, y, f"{path}[{i}]")
    elif a != b:
        yield f"{path}: {a!r} != {b!r}"


def _comparable(bundle):
    """The bundle without what differs between two exports of the same report: when
    each was made, and the bundle's own id (new each time; its resources' ids are not)."""
    bundle = copy.deepcopy(bundle)
    for key in ("id", "timestamp", "meta"):
        bundle.pop(key, None)
    for entry in bundle.get("entry", []):
        resource = entry["resource"]
        for key in ("effectiveDateTime", "performedDateTime", "issued", "date", "authoredOn", "lastModified"):
            resource.pop(key, None)
        resource.get("meta", {}).pop("lastUpdated", None)
    return bundle


@pytest.fixture
def reports(tmp_path, monkeypatch):
    monkeypatch.setattr(fhir_module, "REPORT_DIR", tmp_path)
    monkeypatch.setenv("FHIR_EXPORT_ENABLED", "true")
    return tmp_path


@pytest.fixture
def no_database_rows(monkeypatch):
    """PharmCAT's rows in the database are not what a later export should read."""
    monkeypatch.setattr(
        PharmCATDataService, "get_pharmcat_data_by_run_id",
        lambda self, run_id: pytest.fail("exported from the database"),
    )
    monkeypatch.setattr(
        PharmCATDataService, "get_pharmcat_data_for_workflow",
        lambda self, workflow_id: pytest.fail("exported from the database"),
    )


def _report_time_bundle(db_session, job_id, data):
    """The bundle the generator writes for the job: save_fhir_export's call."""
    result = FHIRExportService(db_session).export_pgx_report(
        run_id=RUN_ID, pharmcat_data=data, workflow_id=job_id, patient_id=PATIENT,
        patient_info={"id": PATIENT, "name": None, "gender": None, "birthDate": None},
    )
    assert result["success"], result
    return result["bundle"]


def _write(reports, job_id, data):
    job_dir = reports / PATIENT / job_id
    job_dir.mkdir(parents=True)
    path = report_data.write(
        str(job_dir), job_id, data, run_id=RUN_ID, patient_id=PATIENT,
        patient_info={"id": PATIENT, "name": None, "gender": None, "birthDate": None},
    )
    assert path == str(job_dir / f"{job_id}_pgx_fhir_source.json")


def _profiles(bundle, name):
    return [
        e["resource"] for e in bundle["entry"]
        if e["resource"].get("meta", {}).get("profile", [""])[0].endswith("/" + name)
    ]


def test_a_later_export_of_the_run_is_the_reports_own_bundle(
    client, db_session, reports, no_database_rows
):
    job_id = _job(db_session)
    data = _report_data()
    _write(reports, job_id, json.loads(json.dumps(data)))
    expected = _report_time_bundle(db_session, job_id, data)

    for path in (f"/fhir/export/run/{RUN_ID}", f"/fhir/export/workflow/{job_id}"):
        response = client.get(path)
        assert response.status_code == 200, (path, response.text)
        later = response.json()
        differences = list(_differences(_comparable(later), _comparable(expected)))
        assert not differences, (path, differences[:10])

    # What the database lane left out is there.
    genotypes = {o["code"]["text"] for o in _profiles(later, "genotype")}
    assert {"CYP1A2 Genotype", "CYP2D6 Genotype", "DPYD Genotype"} <= genotypes
    cyp2d6 = next(o for o in _profiles(later, "genotype") if o["code"]["text"] == "CYP2D6 Genotype")
    assert cyp2d6["valueCodeableConcept"]["text"] == "*1/*5"
    assert len(_profiles(later, "variant")) == 1
    assert _profiles(later, "diagnostic-implication")


def test_the_posted_patient_still_names_the_patient(client, db_session, reports, no_database_rows):
    job_id = _job(db_session)
    _write(reports, job_id, _report_data())
    response = client.post(
        f"/fhir/export/run/{RUN_ID}",
        json={"patient_info": {"id": "posted-patient", "gender": "female"}, "output_format": "json"},
    )
    assert response.status_code == 200, response.text
    patient = next(e["resource"] for e in response.json()["entry"] if e["resource"]["resourceType"] == "Patient")
    assert patient["gender"] == "female"
    assert len(_profiles(response.json(), "variant")) == 1


def test_saving_the_workflows_bundle_again_keeps_the_reports_data(db_session, reports, no_database_rows):
    job_id = _job(db_session)
    _write(reports, job_id, _report_data())
    result = FHIRExportService(db_session).save_fhir_export_for_workflow(
        workflow_id=job_id, patient_id=PATIENT, output_format="json"
    )
    assert result["success"], result
    saved = json.loads((reports / PATIENT / job_id / f"{job_id}_pgx_fhir_report.json").read_text())
    assert len(_profiles(saved, "variant")) == 1


def test_a_job_reported_before_is_exported_from_the_database(db_session, reports, monkeypatch):
    job_id = _job(db_session)  # no source file beside its report
    from_database = {"run_id": RUN_ID, "genes": [{"gene": "CYP2C19", "diplotype": "*1/*2"}]}
    monkeypatch.setattr(
        PharmCATDataService, "get_pharmcat_data_by_run_id", lambda self, run_id: from_database
    )
    result = FHIRExportService(db_session).export_pgx_report(run_id=RUN_ID)
    assert result["success"], result
    assert {o["code"]["text"] for o in _profiles(result["bundle"], "genotype")} == {"CYP2C19 Genotype"}
    assert job_id


def test_the_source_is_read_only_where_the_report_is_and_only_for_its_own_job(tmp_path):
    job_id = str(uuid.uuid4())
    other = str(uuid.uuid4())
    job_dir = tmp_path / PATIENT / job_id
    job_dir.mkdir(parents=True)
    report_data.write(str(job_dir), job_id, {"genes": []}, run_id=RUN_ID, patient_id=PATIENT)

    assert report_data.read(tmp_path, job_id, PATIENT)["workflow_id"] == job_id
    assert report_data.read(tmp_path, job_id)["run_id"] == RUN_ID  # found under any patient
    assert report_data.read(tmp_path, job_id, PATIENT, run_id="another-run") is None
    assert report_data.read(tmp_path, other, PATIENT) is None
    # An id from a URL is a job, never a pattern or a path.
    for bad in ("*", "../" + job_id, f"{PATIENT}/{job_id}"):
        assert report_data.read(tmp_path, bad) is None
    # One written by another version, or for another job, is not this job's.
    (job_dir / report_data.filename(job_id)).write_text(
        json.dumps({"version": 0, "workflow_id": job_id, "data": {}})
    )
    assert report_data.read(tmp_path, job_id, PATIENT) is None


def test_the_patient_the_reports_bundle_named(tmp_path):
    job_id = str(uuid.uuid4())
    assert report_data.bundle_patient_id({"workflow_id": job_id, "patient_id": PATIENT}) == PATIENT
    # A job with no patient keeps its reports in its own directory, and the bundle
    # names none (save_fhir_export's rule).
    assert report_data.bundle_patient_id({"workflow_id": job_id, "patient_id": job_id}) is None


def test_the_generator_keeps_it_with_fhir_export_off_too(monkeypatch, tmp_path):
    """generate_report writes it beside the report even with FHIR export off, so turning
    FHIR export on later still exports the report as it was, PyPGx's genes and all."""
    from app.reports import generator

    monkeypatch.setenv("FHIR_EXPORT_ENABLED", "false")
    for key in (
        "write_pdf", "write_html", "write_interactive_html", "write_json", "write_tsv",
        "write_workflow_svg", "write_workflow_png", "show_pharmcat_html_report",
        "show_pharmcat_json_report", "show_pharmcat_tsv_report",
    ):
        monkeypatch.setitem(generator.REPORT_CONFIG, key, False)
    monkeypatch.setattr(
        generator, "load_job_step_records",
        lambda db_session, job_id: [
            {"name": "pypgx_analysis", "status": "completed", "output": {}},
            {"name": "pharmcat_analysis", "status": "completed", "output": {}},
        ],
    )
    (tmp_path / "pypgx_result.json").write_text(json.dumps({
        "success": True,
        "results": {"CYP1A2": {"success": True, "diplotype": "*1A/*1F", "details": {}}},
    }))
    (tmp_path / "mtdna_result.json").write_text("{}")
    job_id = str(uuid.uuid4())
    result = generator.generate_report(
        {"data": {"genes": [{"gene": "CYP2C19", "diplotype": "*1/*2", "call_source": "MATCHER"}],
                  "drugRecommendations": []}},
        str(tmp_path),
        {"id": PATIENT},
        job_id=job_id,
    )

    record = json.loads((tmp_path / report_data.filename(job_id)).read_text())
    assert record["workflow_id"] == job_id and record["patient_id"] == PATIENT
    genes = {g["gene"]: g for g in record["data"]["genes"]}
    assert genes["CYP1A2"]["diplotype"] == "*1A/*1F"
    assert genes["CYP2C19"]["diplotype"] == "*1/*2"
    assert [g["gene"] for g in record["data"]["genes"]] == [
        g["gene"] for g in result["processed_data"]["genes"]
    ]
