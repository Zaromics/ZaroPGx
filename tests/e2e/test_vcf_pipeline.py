import time
from pathlib import Path

import pytest

VCF = Path(__file__).resolve().parents[2] / "test_data" / "pharmcat.example.vcf"
TERMINAL_OK = {"completed"}
TERMINAL_BAD = {"failed", "cancelled", "error"}


@pytest.mark.e2e
def test_vcf_upload_completes_with_report_artifact(e2e_client):
    assert VCF.is_file(), f"missing dataset {VCF}"
    with VCF.open("rb") as fh:
        files = {"files": (VCF.name, fh, "text/plain")}
        data = {
            "sample_identifier": "e2e-pharmcat-example",
            "reference_genome": "hg38",
            "optitype_enabled": "false",
            "gatk_enabled": "false",
            "pypgx_enabled": "true",
            "report_enabled": "true",
        }
        resp = e2e_client.post(
            "/upload/genomic-data", files=files, data=data, timeout=120.0
        )
    assert resp.status_code in {200, 201}, resp.text
    body = resp.json()
    # UploadResponse.job_id is the workflow UUID (upload_router sets job_id=str(workflow.id))
    workflow_id = body.get("job_id") or body.get("workflow_id")
    assert workflow_id, body

    deadline = time.time() + 45 * 60
    status = None
    while time.time() < deadline:
        wr = e2e_client.get(f"/api/v1/workflows/{workflow_id}")
        assert wr.status_code == 200, wr.text
        status = (wr.json().get("status") or "").lower()
        if status in TERMINAL_OK | TERMINAL_BAD:
            break
        time.sleep(10)
    assert status in TERMINAL_OK, f"workflow ended as {status}"

    # GET /reports/job/{job_id} → get_report_urls shape:
    # {"job_id", "workflow_id", "status": "completed", "reports": {<name>_url: path, ...}}
    reports_resp = e2e_client.get(f"/reports/job/{workflow_id}")
    assert reports_resp.status_code == 200, reports_resp.text
    payload = reports_resp.json()
    assert isinstance(payload, dict), payload
    assert payload.get("status") == "completed", payload
    reports = payload.get("reports")
    assert isinstance(reports, dict) and reports, (
        f"completed workflow but reports empty: {payload}"
    )
    url_values = [
        v for v in reports.values() if isinstance(v, str) and v.strip()
    ]
    assert url_values, f"completed workflow but no report URL/path strings: {payload}"
