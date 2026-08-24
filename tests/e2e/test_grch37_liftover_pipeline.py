"""E2E for the GRCh37/hg19 VCF lane: liftover to GRCh38, then PyPGx + PharmCAT.

A GRCh37 VCF used to be flagged unsupported/provisional. It is now a first-class
lane: gatk-api's Picard LiftoverVcf converts it to GRCh38 (UCSC hg19->hg38 chain)
before analysis. This exercises the whole path end to end -- app build-detection
-> --source_build -> main.nf LiftoverVCF -> PyPGx -> PharmCAT -> report -- which
no unit test can cover (the main.nf Groovy and the live GATK only run here).

Uploaded with reference_genome=hg19 ON PURPOSE: the file's real build. The
pipeline must still analyse it against GRCh38 (the lift target), so this also
guards analysis_reference() -- picking hg19 here must not make PyPGx run its
GRCh37 assembly against lifted GRCh38 coordinates. Gated on
ZAROPGX_E2E_REFERENCE like the other reference-dependent lanes.
"""

import os
import time
from pathlib import Path

import pytest

VCF = Path(__file__).resolve().parents[2] / "test_data" / "grch37_pgx_snps.vcf"
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
    reason="GRCh37 liftover lane needs the GRCh38 reference + chain; set ZAROPGX_E2E_REFERENCE=1",
)
def test_grch37_vcf_is_lifted_and_completes_with_report(e2e_client):
    assert VCF.is_file(), f"missing dataset {VCF}"
    with VCF.open("rb") as fh:
        files = {"files": (VCF.name, fh, "application/octet-stream")}
        data = {
            "sample_identifier": "e2e-grch37-liftover",
            # The file's real build, deliberately -- the lift target must still win.
            "reference_genome": "hg19",
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
    job_id = body.get("job_id") or body.get("workflow_id")
    assert job_id, body

    deadline = time.time() + 30 * 60
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
