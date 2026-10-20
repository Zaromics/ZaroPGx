"""A job finished before #145 offers no link to an mtDNA file without a result.

#145 stopped generate_report linking an mtDNA file that holds no result
(mtdna_report_paths): PharmCAT's example VCF has no chrM record, and its job offered
"View Haplogroup Report" for a no-call mtdna_result.json and "Download chrM VCF" for a
header-only VCF. That decides the links a job stores when it finishes. A job finished
before it keeps those links in metadata["reports"], and GET /upload/reports/job/{job_id}
and GET /upload/status/{job_id} returned them as stored.

Both now check each stored mtDNA link, on read, against the file it serves, by the
same test (mtdna_file_holds_result), and leave the stored metadata as it is.
"""

import copy
import gzip
import logging

import pytest

import app.api.routes.upload_router as upload_router
from app.api.models import JobCreate, JobStatus, JobUpdate, WorkflowOptions
from tests.mtdna_outputs import write_na12878_run, write_no_chrm_vcf_run

PATIENT = "272af295-247d-4d46-9638-4a4f79e28af7"
MTDNA_PATH_KEYS = ("mtdna_report_path", "mtdna_haplogroups_path", "mtdna_vcf_path")
MTDNA_URL_KEYS = ("mtdna_report_url", "mtdna_haplogroups_url", "mtdna_vcf_url")


def _old_reports(job_id: str) -> dict:
    """metadata["reports"] as a job stored it before #145: a link to every mtDNA file
    that existed, held a result or not."""
    prefix = f"/reports/{PATIENT}/{job_id}"
    return {
        "pdf_path": f"{prefix}/{job_id}_pgx_report.pdf",
        "html_path": f"{prefix}/{job_id}_pgx_report.html",
        "interactive_html_path": f"{prefix}/{job_id}_pgx_report_interactive.html",
        "pharmcat_html_path": f"{prefix}/{job_id}_pgx_pharmcat.html",
        "pharmcat_json_path": f"{prefix}/{job_id}_pgx_pharmcat.json",
        "pharmcat_tsv_path": f"{prefix}/{job_id}_pgx_pharmcat.tsv",
        "mtdna_report_path": f"{prefix}/mtdna_report.html",
        "mtdna_haplogroups_path": f"{prefix}/mtdna_result.json",
        "mtdna_vcf_path": f"{prefix}/mtdna_variants.vcf.gz",
        "is_provisional": False,
        "job_directory": f"/data{prefix}",
    }


@pytest.fixture
def old_job(job_service, monkeypatch, tmp_path):
    """A completed job with pre-#145 stored reports; returns (job_id, job_dir, stored)."""
    monkeypatch.setattr(upload_router, "REPORTS_DIR", str(tmp_path))
    job = job_service.create_job(
        JobCreate(
            name="Genomic Analysis - pharmcat.example",
            workflow_type="genomic_analysis",
            options=WorkflowOptions(),
            metadata={"patient_id": PATIENT, "data_id": "data-old-mtdna-links"},
        )
    )
    job_id = str(job.id)
    metadata = dict(job.job_metadata)
    metadata["reports"] = _old_reports(job_id)
    job_service.update_job(
        job.id, JobUpdate(metadata=metadata, status=JobStatus.COMPLETED)
    )
    return job_id, tmp_path / PATIENT / job_id, copy.deepcopy(metadata)


def _no_result_files(job_dir):
    """The no-chrM VCF run's files, plus the empty report an alignment without a
    mitochondrial contig could leave."""
    write_no_chrm_vcf_run(job_dir)
    (job_dir / "mtdna_report.html").write_bytes(b"")


def test_reports_job_leaves_out_old_links_to_mtdna_files_without_a_result(
    client, old_job, job_service
):
    job_id, job_dir, stored = old_job
    _no_result_files(job_dir)

    resp = client.get(f"/upload/reports/job/{job_id}")

    assert resp.status_code == 200, resp.text
    reports = resp.json()["reports"]
    assert not [key for key in MTDNA_PATH_KEYS + MTDNA_URL_KEYS if key in reports]
    # Everything else is returned as stored.
    expected = {
        key: value
        for key, value in stored["reports"].items()
        if key not in MTDNA_PATH_KEYS
    }
    assert reports == expected
    # The stored metadata is not changed, in the session or in the database.
    assert job_service.get_job(job_id).job_metadata == stored
    job_service.db.expire_all()
    assert job_service.get_job(job_id).job_metadata == stored


def test_status_leaves_out_old_links_to_mtdna_files_without_a_result(
    client, old_job, job_service
):
    job_id, job_dir, stored = old_job
    _no_result_files(job_dir)

    resp = client.get(f"/upload/status/{job_id}")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert not [key for key in MTDNA_URL_KEYS if key in body], body
    assert body["pdf_report_url"] == stored["reports"]["pdf_path"]
    assert body["pharmcat_html_report_url"] == stored["reports"]["pharmcat_html_path"]
    assert job_service.get_job(job_id).job_metadata == stored
    job_service.db.expire_all()
    assert job_service.get_job(job_id).job_metadata == stored


def test_old_links_to_mtdna_results_are_kept(client, old_job):
    job_id, job_dir, stored = old_job
    write_na12878_run(job_dir)
    prefix = f"/reports/{PATIENT}/{job_id}"
    expected = {
        "mtdna_report_url": f"{prefix}/mtdna_report.html",
        "mtdna_haplogroups_url": f"{prefix}/mtdna_result.json",
        "mtdna_vcf_url": f"{prefix}/mtdna_variants.vcf.gz",
    }

    reports = client.get(f"/upload/reports/job/{job_id}").json()["reports"]
    body = client.get(f"/upload/status/{job_id}").json()

    assert reports == stored["reports"]
    assert {key: body.get(key) for key in MTDNA_URL_KEYS} == expected


def test_each_mtdna_link_is_decided_by_its_own_file(client, old_job):
    """A chrM VCF with a record is linked beside a no-call result JSON and no report."""
    job_id, job_dir, stored = old_job
    write_no_chrm_vcf_run(job_dir)
    with gzip.open(job_dir / "mtdna_variants.vcf.gz", "at", encoding="utf-8") as fh:
        fh.write("chrM\t750\t.\tA\tG\t.\tPASS\t.\tGT\t1\n")

    body = client.get(f"/upload/status/{job_id}").json()

    assert [key for key in MTDNA_URL_KEYS if key in body] == ["mtdna_vcf_url"]
    assert body["mtdna_vcf_url"] == stored["reports"]["mtdna_vcf_path"]


def test_an_old_link_to_a_file_that_is_gone_is_left_out(client, old_job):
    job_id, _job_dir, _stored = old_job

    reports = client.get(f"/upload/reports/job/{job_id}").json()["reports"]

    assert not [key for key in MTDNA_PATH_KEYS if key in reports]


@pytest.mark.parametrize(
    "url",
    [
        "/reports/../outside/mtdna_variants.vcf.gz",
        "/data/reports/x/mtdna_variants.vcf.gz",
        "https://example.org/mtdna_variants.vcf.gz",
        None,
    ],
)
def test_a_link_that_names_no_report_file_is_left_out(tmp_path, monkeypatch, url):
    """Only a served report path under REPORTS_DIR is checked; anything else names no
    file the app serves, and is left out rather than followed."""
    monkeypatch.setattr(upload_router, "REPORTS_DIR", str(tmp_path / "reports"))
    outside = tmp_path / "outside"
    outside.mkdir()
    with gzip.open(outside / "mtdna_variants.vcf.gz", "wt", encoding="utf-8") as fh:
        fh.write("#CHROM\tPOS\nchrM\t750\n")

    reports = {"pdf_path": "/reports/p/j/j_pgx_report.pdf", "mtdna_vcf_path": url}
    checked = upload_router.without_empty_mtdna_links(reports)

    assert checked == {"pdf_path": "/reports/p/j/j_pgx_report.pdf"}
    assert reports["mtdna_vcf_path"] == url


def test_a_link_through_a_symlink_loop_is_left_out_not_a_500(
    client, job_service, old_job
):
    """Path.resolve() raises RuntimeError for a symlink loop on Python 3.12; the path
    jail refuses it, so the link is left out and the answer is still 200."""
    job_id, job_dir, stored = old_job
    write_na12878_run(job_dir)
    try:
        (job_dir / "a").symlink_to(job_dir / "b")
        (job_dir / "b").symlink_to(job_dir / "a")
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    prefix = f"/reports/{PATIENT}/{job_id}"
    metadata = copy.deepcopy(stored)
    metadata["reports"]["mtdna_vcf_path"] = f"{prefix}/a/mtdna_variants.vcf.gz"
    job_service.update_job(job_id, JobUpdate(metadata=metadata))

    for endpoint in (f"/upload/reports/job/{job_id}", f"/upload/status/{job_id}"):
        resp = client.get(endpoint)
        assert resp.status_code == 200, endpoint
    reports = client.get(f"/upload/reports/job/{job_id}").json()["reports"]
    assert "mtdna_vcf_path" not in reports
    # The others, whose files hold a result, are still linked.
    assert reports["mtdna_report_path"] == f"{prefix}/mtdna_report.html"
    assert reports["mtdna_haplogroups_path"] == f"{prefix}/mtdna_result.json"


def test_a_link_with_a_nul_is_left_out_not_raised(tmp_path, monkeypatch):
    """Path.resolve() raises ValueError for an embedded NUL on Python 3.12. (The
    database's JSONB cannot store one, so this is the check alone.)"""
    monkeypatch.setattr(upload_router, "REPORTS_DIR", str(tmp_path))
    reports = {"mtdna_vcf_path": "/reports/p/j/mtdna_variants.vcf.gz\x00"}
    assert upload_router.without_empty_mtdna_links(reports) == {}


def test_reading_a_job_logs_its_mtdna_checks_at_debug_only(client, old_job, caplog):
    """The check runs on every read of the job, the status poll among them: nothing
    at INFO, so the log does not fill with paths."""
    job_id, job_dir, _stored = old_job
    _no_result_files(job_dir)
    caplog.set_level(logging.INFO, logger="app.reports.generator")

    client.get(f"/upload/reports/job/{job_id}")
    client.get(f"/upload/status/{job_id}")

    assert not [r for r in caplog.records if "mtDNA" in r.getMessage()]
    caplog.clear()
    caplog.set_level(logging.DEBUG, logger="app.reports.generator")
    client.get(f"/upload/reports/job/{job_id}")
    debug = [r for r in caplog.records if "not linked" in r.getMessage()]
    assert debug and all(r.levelno == logging.DEBUG for r in debug)
