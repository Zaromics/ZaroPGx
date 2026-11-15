"""E2E for the BCF input lane.

BCF converts to VCF in the gatk-api sidecar (`bcftools view -O z`, then
`bcftools index -t`) and then runs the same VCF->PyPGx->PharmCAT->report path the VCF
lane does. Unlike CRAM and SAM, that conversion involves no reference FASTA -- the
endpoint says so itself -- so this lane needs nothing the CI e2e stack does not have
and runs on every pull request, which is why it is not gated like the others.

`test_data/pharmcat.example.bcf` is `pharmcat.example.vcf` written as BCF
(`bcftools view -O b`), the same fixture the VCF lane's test uploads, so the two lanes
are the same records by two routes. test_bcf_fixture_matches_its_vcf.py keeps them that
way.
"""

import time
from pathlib import Path

import pytest

from tests.e2e.report_links import assert_report_links_resolve

BCF = Path(__file__).resolve().parents[2] / "test_data" / "pharmcat.example.bcf"
TERMINAL_OK = {"completed"}
TERMINAL_BAD = {"failed", "cancelled", "error"}


@pytest.mark.e2e
def test_bcf_upload_completes_with_report_artifact(e2e_client):
    """The lane that had no end-to-end test of any kind: every other input type the
    pipeline accepts is covered somewhere, and `bcf` was covered only at the endpoint."""
    assert BCF.is_file(), f"missing dataset {BCF}"
    with BCF.open("rb") as fh:
        files = {"files": (BCF.name, fh, "application/octet-stream")}
        data = {
            "sample_identifier": "e2e-bcf-example",
            "reference_genome": "hg38",
            "optitype_enabled": "false",
            # gatk-api does the BCF->VCF conversion, so unlike the VCF lane this one
            # needs the sidecar; it does not need the sidecar's reference.
            "gatk_enabled": "true",
            "pypgx_enabled": "true",
            "report_enabled": "true",
        }
        resp = e2e_client.post(
            "/upload/genomic-data", files=files, data=data, timeout=120.0
        )
    assert resp.status_code in {200, 201}, resp.text
    body = resp.json()
    job_id = body.get("job_id") or body.get("workflow_id")
    assert job_id, body

    deadline = time.time() + 45 * 60
    status = None
    while time.time() < deadline:
        wr = e2e_client.get(f"/api/v1/jobs/{job_id}")
        assert wr.status_code == 200, wr.text
        status = (wr.json().get("status") or "").lower()
        if status in TERMINAL_OK | TERMINAL_BAD:
            break
        time.sleep(10)
    assert status in TERMINAL_OK, f"job ended as {status}"

    reports_resp = e2e_client.get(f"/reports/job/{job_id}")
    assert reports_resp.status_code == 200, reports_resp.text
    payload = reports_resp.json()
    assert payload.get("status") == "completed", payload
    reports = payload.get("reports")
    assert isinstance(reports, dict) and reports, f"no reports: {payload}"
    assert_report_links_resolve(e2e_client, reports)
    assert reports.get("pdf_path"), reports
