"""The bundle's Patient is the job's patient, whoever asks for the bundle.

The Patient's identifier came from patient_info["id"] and otherwise fell back to
the run id. The report generator passes patient details, but POST
/fhir/save/workflow/{job} without them rewrote the job's bundle with a Patient
named after the run, so the next push (app.services.fhir.push, conditional on
that identifier) created a second Patient and moved the report onto it. Now the
fallback is the ZaroPGx patient: the directory the bundle is saved under, else
the job's metadata.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services import fhir_export_service as fhir_module
from app.services.fhir_export_service import FHIRExportService
from app.services.job_service import JobService

DATA = {
    "run_id": "run-9",
    "pharmcat_version": "3.4.0",
    "genes": [{"gene": "CYP2D6", "diplotype": "*1/*1", "phenotype": "Normal"}],
    "drugRecommendations": [],
}

JOB = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr(fhir_module, "REPORT_DIR", tmp_path)
    monkeypatch.setenv("FHIR_EXPORT_ENABLED", "true")
    return FHIRExportService(MagicMock())


def _job_metadata(monkeypatch, metadata):
    monkeypatch.setattr(
        JobService,
        "get_job",
        lambda self, job_id: SimpleNamespace(job_metadata=metadata),
    )


def _patient_identifier(path):
    bundle = json.loads(path.read_text())
    (patient,) = [
        e["resource"]
        for e in bundle["entry"]
        if e["resource"]["resourceType"] == "Patient"
    ]
    return patient["identifier"][0]["value"]


def test_saved_without_patient_details_the_patient_is_the_directory(
    service, tmp_path, monkeypatch
):
    _job_metadata(monkeypatch, {})
    result = service.save_fhir_export(
        run_id="run-9", patient_id="patient-1", pharmcat_data=DATA, workflow_id=JOB
    )
    assert result["success"], result
    path = tmp_path / "patient-1" / JOB / f"{JOB}_pgx_fhir_report.json"
    assert _patient_identifier(path) == "patient-1"


def test_save_for_workflow_without_a_patient_asks_the_job(
    service, tmp_path, monkeypatch
):
    _job_metadata(monkeypatch, {"patient_id": "patient-7"})
    monkeypatch.setattr(
        service.pharmcat_service,
        "get_pharmcat_data_for_workflow",
        lambda workflow_id: dict(DATA),
    )
    result = service.save_fhir_export_for_workflow(workflow_id=JOB)
    assert result["success"], result
    # Where the file goes is unchanged (the job's own directory) ...
    path = tmp_path / JOB / "pgx_fhir_report.json"
    # ... but its Patient is the job's patient, not the run.
    assert _patient_identifier(path) == "patient-7"


def test_patient_details_still_win(service, tmp_path, monkeypatch):
    _job_metadata(monkeypatch, {"patient_id": "patient-7"})
    service.save_fhir_export(
        run_id="run-9",
        patient_id="patient-1",
        patient_info={"id": "MRN-42"},
        pharmcat_data=DATA,
        workflow_id=JOB,
    )
    path = tmp_path / "patient-1" / JOB / f"{JOB}_pgx_fhir_report.json"
    assert _patient_identifier(path) == "MRN-42"


def test_with_no_patient_anywhere_the_run_id_remains(service, monkeypatch):
    _job_metadata(monkeypatch, None)
    result = service.export_pgx_report(
        run_id="run-9", pharmcat_data=DATA, workflow_id=JOB
    )
    bundle = result["bundle"]
    (patient,) = [
        e["resource"]
        for e in bundle["entry"]
        if e["resource"]["resourceType"] == "Patient"
    ]
    assert patient["identifier"][0]["value"] == "run-9"


def test_the_same_patient_gets_the_same_resource_id(service, monkeypatch):
    """Two jobs of one patient name one Patient, which a push reuses."""
    _job_metadata(monkeypatch, {"patient_id": "patient-7"})
    first = service.export_pgx_report(run_id="run-1", pharmcat_data=DATA)
    second = service.export_pgx_report(
        run_id="run-2", pharmcat_data=DATA, patient_id="patient-7"
    )
    third = service.export_pgx_report(
        run_id="run-3", pharmcat_data=DATA, workflow_id=JOB
    )

    def patient_url(result):
        return result["bundle"]["entry"][1]["fullUrl"]

    assert patient_url(second) == patient_url(third)
    assert patient_url(first) != patient_url(second)
