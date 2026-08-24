"""E2E for the CRAM and SAM input lanes.

Both convert to BAM in the gatk-api sidecar (CramToBAM / SamToBAM) and then run the
same BAM->VCF->PyPGx->PharmCAT->report path the BAM lane does. These fixtures are
derived from tests/e2e's GRCh38 BAM (samtools view -C / -h), so the conversion is
exercised for real. Like the BAM lane, this needs the multi-GB reference the CI e2e
stack does not populate, so it is gated on ZAROPGX_E2E_REFERENCE.
"""

import os
import time
from pathlib import Path

import pytest

TEST_DATA = Path(__file__).resolve().parents[2] / "test_data"
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
    reason="CRAM/SAM lanes convert via gatk-api and need the GRCh38 reference; set ZAROPGX_E2E_REFERENCE=1",
)
@pytest.mark.parametrize(
    "filename,content_type",
    [
        ("pgx_ngs_example.cram", "application/octet-stream"),
        ("pgx_ngs_example.sam", "text/plain"),
    ],
)
def test_alignment_upload_completes_with_report_artifact(
    e2e_client, filename, content_type
):
    src = TEST_DATA / filename
    assert src.is_file(), f"missing dataset {src}"
    with src.open("rb") as fh:
        files = {"files": (src.name, fh, content_type)}
        data = {
            "sample_identifier": f"e2e-{src.suffix.lstrip('.')}-example",
            "reference_genome": "hg38",
            "optitype_enabled": "false",
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
    assert status in TERMINAL_OK, f"{filename} job ended as {status}"

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
