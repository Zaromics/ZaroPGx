"""E2E for the BAM lane with HLA typing ON (the default BAM behavior).

The other BAM/CRAM/SAM e2e tests disable OptiType; this one leaves it on, so the run
exercises OptiTypeHLAFromBAM -> zarohla /call-hla -> OptiType and PharmCAT-with-HLA
alongside the PyPGx arm. The fixture carries reads at the class-I HLA loci (chr6) plus a
CYP2C19 variant (chr10). Gated on ZAROPGX_E2E_REFERENCE like the other alignment lanes.
"""

import os
import time
from pathlib import Path

import pytest

BAM = Path(__file__).resolve().parents[2] / "test_data" / "pgx_wgs_hla_example.bam"
TERMINAL_OK = {"completed"}
TERMINAL_BAD = {"failed", "cancelled", "error"}

_REFERENCE_AVAILABLE = os.environ.get("ZAROPGX_E2E_REFERENCE", "").lower() in (
    "1",
    "true",
    "yes",
)


@pytest.mark.e2e
@pytest.mark.skipif(
    not _REFERENCE_AVAILABLE,
    reason="BAM+HLA lane needs the GRCh38 reference; set ZAROPGX_E2E_REFERENCE=1",
)
def test_bam_with_hla_typing_completes_with_report(e2e_client):
    assert BAM.is_file(), f"missing dataset {BAM}"
    with BAM.open("rb") as fh:
        files = {"files": (BAM.name, fh, "application/octet-stream")}
        data = {
            "sample_identifier": "e2e-bam-hla",
            "reference_genome": "hg38",
            "optitype_enabled": "true",
            "gatk_enabled": "false",
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
    assert isinstance(payload, dict), payload
    assert payload.get("status") == "completed", payload
    reports = payload.get("reports")
    assert (
        isinstance(reports, dict) and reports
    ), f"completed but reports empty: {payload}"
    url_values = [v for v in reports.values() if isinstance(v, str) and v.strip()]
    assert url_values, f"completed but no report URL/path strings: {payload}"
