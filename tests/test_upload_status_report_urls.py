"""GET /upload/status/{job_id} gives a completed job's report links.

The API reference lists it as "Status and report links", but it never returned one: it
looked for pdf_report_url and the like at the top of the job's metadata["reports"],
which holds generate_report's served paths under pdf_path, html_path and so on. (The
*_url keys there are inside processed_data, the report's template data.) The page was
unaffected, since it takes its links from the completion message or
GET /reports/job/{job_id}; API clients got none.

The stored reports below are the shape a real job stored: the NA12878 demo CRAM's
GET /api/v1/jobs/{job_id} metadata["reports"] from the rehearsal image (2026-10-20),
with this test's ids and processed_data cut down to its links.
"""

import pytest

import app.api.routes.upload_router as upload_router
from app.api.models import JobCreate, JobStatus, JobUpdate, WorkflowOptions
from tests.mtdna_outputs import write_na12878_run

PATIENT = "f39cb25a-3f5b-49e2-bc8a-e7a280bccc0d"


def _stored_reports(job_id: str) -> dict:
    prefix = f"/reports/{PATIENT}/{job_id}"
    return {
        "pdf_path": f"{prefix}/{job_id}_pgx_report.pdf",
        "html_path": f"{prefix}/{job_id}_pgx_report.html",
        "fhir_xml_path": f"{prefix}/{job_id}_pgx_fhir_report.xml",
        "job_directory": f"/data{prefix}",
        "fhir_json_path": f"{prefix}/{job_id}_pgx_fhir_report.json",
        "is_provisional": False,
        "mtdna_vcf_path": f"{prefix}/mtdna_variants.vcf.gz",
        "processed_data": {
            "run_id": job_id,
            "file_type": "cram",
            "pdf_report_url": f"{prefix}/{job_id}_pgx_report.pdf",
            "html_report_url": f"{prefix}/{job_id}_pgx_report.html",
            "interactive_html_report_url": f"{prefix}/{job_id}_pgx_report_interactive.html",
            "mtdna_vcf_url": f"{prefix}/mtdna_variants.vcf.gz",
            "genes": [],
        },
        "pharmcat_run_id": job_id,
        "mtdna_report_path": f"{prefix}/mtdna_report.html",
        "pharmcat_tsv_path": f"{prefix}/{job_id}_pgx_pharmcat.tsv",
        "workflow_png_path": f"{prefix}/{job_id}_workflow.png",
        "workflow_svg_path": f"{prefix}/{job_id}_workflow.svg",
        "pharmcat_html_path": f"{prefix}/{job_id}_pgx_pharmcat.html",
        "pharmcat_json_path": f"{prefix}/{job_id}_pgx_pharmcat.json",
        "interactive_html_path": f"{prefix}/{job_id}_pgx_report_interactive.html",
        "mtdna_haplogroups_path": f"{prefix}/mtdna_result.json",
    }


@pytest.fixture
def make_job(job_service):
    def make(status: JobStatus, reports=None):
        job = job_service.create_job(
            JobCreate(
                name="Genomic Analysis - NA12878-demo",
                workflow_type="genomic_analysis",
                options=WorkflowOptions(),
                metadata={"patient_id": PATIENT, "data_id": "data-status-links"},
            )
        )
        metadata = dict(job.job_metadata)
        if reports is not None:
            metadata["reports"] = reports(str(job.id))
        job_service.update_job(job.id, JobUpdate(metadata=metadata, status=status))
        return str(job.id)

    return make


def test_a_completed_job_gives_its_report_links(
    client, make_job, monkeypatch, tmp_path
):
    job_id = make_job(JobStatus.COMPLETED, _stored_reports)
    # Its mtDNA files on disk, each holding a result: a stored mtDNA link is checked
    # against its file on read (tests/test_ui_old_job_mtdna_links.py).
    monkeypatch.setattr(upload_router, "REPORTS_DIR", str(tmp_path))
    write_na12878_run(tmp_path / PATIENT / job_id)

    resp = client.get(f"/upload/status/{job_id}")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "completed"
    prefix = f"/reports/{PATIENT}/{job_id}"
    # The links the completion message sends and the page shows, in the same form:
    # the app's own path to each file.
    assert {key: body.get(key) for key in body if key.endswith("_url")} == {
        "pdf_report_url": f"{prefix}/{job_id}_pgx_report.pdf",
        "html_report_url": f"{prefix}/{job_id}_pgx_report.html",
        "interactive_html_report_url": f"{prefix}/{job_id}_pgx_report_interactive.html",
        "pharmcat_html_report_url": f"{prefix}/{job_id}_pgx_pharmcat.html",
        "pharmcat_json_report_url": f"{prefix}/{job_id}_pgx_pharmcat.json",
        "pharmcat_tsv_report_url": f"{prefix}/{job_id}_pgx_pharmcat.tsv",
        "mtdna_report_url": f"{prefix}/mtdna_report.html",
        "mtdna_haplogroups_url": f"{prefix}/mtdna_result.json",
        "mtdna_vcf_url": f"{prefix}/mtdna_variants.vcf.gz",
    }
    # The links are the same ones GET /upload/reports/job/{job_id} returns.
    reports = client.get(f"/upload/reports/job/{job_id}").json()["reports"]
    assert body["pdf_report_url"] == reports["pdf_path"]
    assert body["interactive_html_report_url"] == reports["interactive_html_path"]


def test_a_job_with_some_reports_gives_those_links_only(client, make_job):
    """The custom report off: PharmCAT's files and no mtDNA (upload_router stores
    native_report_paths' links, is_provisional and job_directory)."""

    def reports(job_id):
        prefix = f"/reports/{PATIENT}/{job_id}"
        return {
            "pharmcat_html_path": f"{prefix}/{job_id}_pgx_pharmcat.html",
            "pharmcat_json_path": f"{prefix}/{job_id}_pgx_pharmcat.json",
            "pharmcat_tsv_path": f"{prefix}/{job_id}_pgx_pharmcat.tsv",
            "is_provisional": False,
            "job_directory": f"/data{prefix}",
        }

    job_id = make_job(JobStatus.COMPLETED, reports)

    body = client.get(f"/upload/status/{job_id}").json()

    assert sorted(key for key in body if key.endswith("_url")) == [
        "pharmcat_html_report_url",
        "pharmcat_json_report_url",
        "pharmcat_tsv_report_url",
    ]


@pytest.mark.parametrize(
    "status, reports",
    [(JobStatus.RUNNING, _stored_reports), (JobStatus.COMPLETED, None)],
)
def test_no_links_before_a_job_has_completed_reports(client, make_job, status, reports):
    job_id = make_job(status, reports)

    body = client.get(f"/upload/status/{job_id}").json()

    assert not [key for key in body if key.endswith("_url")], body
