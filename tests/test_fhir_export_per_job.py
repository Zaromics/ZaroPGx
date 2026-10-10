"""Each job's FHIR bundle is its own file, in that job's report directory.

save_fhir_export wrote every bundle to <patient>/pgx_fhir_report.{json,xml}. Two
30x NA12878 jobs for one patient showed what that does: the second overwrote the
first's, and the first job's report linked the second job's bundle. Every other
report output already lives in <patient>/<job>/, named after the job.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services import fhir_export_service as fhir_module
from app.services.fhir_export_service import FHIRExportService


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr(fhir_module, "REPORT_DIR", tmp_path)
    svc = FHIRExportService(MagicMock())
    monkeypatch.setattr(svc, "is_enabled", lambda: True)

    def fake_export(**kwargs):
        # The bundle names its job, so a file can be traced to the job it holds.
        return {
            "success": True,
            "content": f"bundle of {kwargs['workflow_id']} ({kwargs['output_format']})",
        }

    monkeypatch.setattr(svc, "export_pgx_report", fake_export)
    return svc


def _save(service, **kwargs):
    result = service.save_fhir_export(
        output_format="both", pharmcat_data={"genes": []}, **kwargs
    )
    assert result["success"], result
    return {f["format"]: f for f in result["files_saved"]}


def test_two_jobs_for_one_patient_keep_their_own_bundles(service, tmp_path):
    first = _save(service, patient_id="patient-1", workflow_id="job-1")
    second = _save(service, patient_id="patient-1", workflow_id="job-2")

    for job, saved in (("job-1", first), ("job-2", second)):
        for fmt in ("json", "xml"):
            name = f"{job}_pgx_fhir_report.{fmt}"
            path = tmp_path / "patient-1" / job / name
            assert saved[fmt]["path"] == str(path)
            assert saved[fmt]["url"] == f"/reports/patient-1/{job}/{name}"
            assert path.read_text() == f"bundle of {job} ({fmt})"


def test_without_a_patient_the_workflow_directory_is_unchanged(service, tmp_path):
    """save_fhir_export_for_workflow passes the workflow as the directory when it
    has no patient; that was one file per workflow already."""
    saved = _save(service, patient_id="wf-1", workflow_id="wf-1")
    assert saved["json"]["path"] == str(tmp_path / "wf-1" / "pgx_fhir_report.json")
    assert saved["json"]["url"] == "/reports/wf-1/pgx_fhir_report.json"


def test_without_a_workflow_the_run_export_is_unchanged(service, tmp_path):
    """/fhir/save/run/{run_id} names no job, so there is no job directory."""
    service.export_pgx_report = lambda **kwargs: {"success": True, "content": "x"}
    saved = _save(service, run_id="run-1", patient_id="patient-1")
    assert saved["json"]["path"] == str(tmp_path / "patient-1" / "pgx_fhir_report.json")
