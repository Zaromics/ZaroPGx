"""A finished job's card offers mtDNA links only for mtDNA files that hold a result.

PharmCAT's example VCF has no chrM record. Its job's card still offered "View
Haplogroup Report" (mtdna_result.json, a no-call with no haplogroup) and "Download
chrM VCF" (a header with no records), because the server linked every mtDNA file that
existed (2026-10-20). The server now links one only when it holds a result
(app/reports/generator.py's mtdna_report_paths), and the page draws what it is given.

These tests take each run's real mtDNA files (tests/mtdna_outputs.py) through the
server's link list and GET /upload/reports/job/{job_id}, and the page's own
fetchReportLinks and showResults in Node (tests/js/results_card.js).
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import app.api.routes.upload_router as upload_router
from app.api.models import JobCreate, JobStatus, JobUpdate, WorkflowOptions
from app.reports.generator import native_report_paths
from tests.mtdna_outputs import write_na12878_run, write_no_chrm_vcf_run

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = REPO_ROOT / "app" / "templates" / "index.html"
DRIVER = Path(__file__).resolve().parent / "js" / "results_card.js"

PATIENT = "patient-mtdna-links"
MTDNA_URL_KEYS = ("mtdna_report_url", "mtdna_haplogroups_url", "mtdna_vcf_url")
MTDNA_LABELS = (
    "mtDNA Reports",
    "View mtDNA-Server 2 Report",
    "View Haplogroup Report",
    "Download chrM VCF",
)

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


def _card(tmp_path: Path, job_id: str, reports: dict) -> dict:
    """The results card the page draws from GET /reports/job/{job_id}'s answer."""
    scenario = tmp_path / "scenario.json"
    scenario.write_text(
        json.dumps(
            {
                "job_id": job_id,
                "reports_job": {
                    "job_id": job_id,
                    "status": "completed",
                    "reports": reports,
                },
            }
        )
    )
    proc = subprocess.run(
        [shutil.which("node"), str(DRIVER), str(TEMPLATE), str(scenario)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    out = json.loads(proc.stdout)
    assert out["ok"], out["errors"]
    return out


def _job_reports(job_dir: Path, job_id: str) -> dict:
    """The job's stored reports as the server builds them from its directory."""
    prefix = f"/reports/{PATIENT}/{job_id}"
    (job_dir / f"{job_id}_pgx_pharmcat.html").write_text("<html></html>")
    return {
        "pdf_path": f"{prefix}/{job_id}_pgx_report.pdf",
        "interactive_html_path": f"{prefix}/{job_id}_pgx_report_interactive.html",
        **native_report_paths(str(job_dir), job_id, prefix),
    }


@needs_node
def test_the_card_of_a_file_without_chrm_data_offers_no_mtdna_link(tmp_path):
    job_id = "e9958c90-3571-4c5a-a0fe-ac257368f8ac"
    job_dir = tmp_path / PATIENT / job_id
    write_no_chrm_vcf_run(job_dir)

    out = _card(tmp_path, job_id, _job_reports(job_dir, job_id))

    assert not [key for key in MTDNA_URL_KEYS if out["links"].get(key)]
    for label in MTDNA_LABELS:
        assert label not in out["card"]
    # The rest of the card is still there.
    assert "View PDF Report" in out["card"]
    assert "View PharmCAT HTML Report" in out["card"]


@needs_node
def test_the_card_of_a_run_with_chrm_data_offers_its_three_mtdna_links(tmp_path):
    job_id = "d2101253-141b-4d07-ac0b-3b9f807f84c8"
    job_dir = tmp_path / PATIENT / job_id
    write_na12878_run(job_dir)

    out = _card(tmp_path, job_id, _job_reports(job_dir, job_id))

    prefix = f"/reports/{PATIENT}/{job_id}"
    for label, name in (
        ("View mtDNA-Server 2 Report", "mtdna_report.html"),
        ("View Haplogroup Report", "mtdna_result.json"),
        ("Download chrM VCF", "mtdna_variants.vcf.gz"),
    ):
        assert f'href="{prefix}/{name}"' in out["card"]
        assert label in out["card"]


@pytest.fixture
def unrecorded_job(job_service, monkeypatch, tmp_path):
    """A completed job whose metadata has no reports: GET /upload/reports/job/{id}
    builds its links from the job's directory."""
    monkeypatch.setattr(upload_router, "REPORTS_DIR", str(tmp_path))
    job = job_service.create_job(
        JobCreate(
            name="Genomic Analysis - mtdna-links",
            workflow_type="genomic_analysis",
            options=WorkflowOptions(),
            metadata={"patient_id": PATIENT, "data_id": "data-mtdna-links"},
        )
    )
    job_service.update_job(job.id, JobUpdate(status=JobStatus.COMPLETED))
    return job, tmp_path / PATIENT / str(job.id)


def test_the_endpoints_fallback_links_no_mtdna_file_without_a_result(
    client, unrecorded_job
):
    job, job_dir = unrecorded_job
    write_no_chrm_vcf_run(job_dir)

    resp = client.get(f"/upload/reports/job/{job.id}")

    assert resp.status_code == 200, resp.text
    reports = resp.json()["reports"]
    assert not [key for key in MTDNA_URL_KEYS if key in reports], reports


def test_the_endpoints_fallback_links_an_mtdna_result(client, unrecorded_job):
    job, job_dir = unrecorded_job
    write_na12878_run(job_dir)

    resp = client.get(f"/upload/reports/job/{job.id}")

    assert resp.status_code == 200, resp.text
    reports = resp.json()["reports"]
    prefix = f"/reports/{PATIENT}/{job.id}"
    assert {key: reports.get(key) for key in MTDNA_URL_KEYS} == {
        "mtdna_report_url": f"{prefix}/mtdna_report.html",
        "mtdna_haplogroups_url": f"{prefix}/mtdna_result.json",
        "mtdna_vcf_url": f"{prefix}/mtdna_variants.vcf.gz",
    }
