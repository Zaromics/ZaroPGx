"""Regression: ``GET /upload/reports/job/{job_id}`` returned a duplicate ``job_id`` key.

The response literal read::

    return {
        "job_id": job_id,        # the raw path parameter
        "job_id": str(job.id),   # the resolved job's canonical UUID
        ...
    }

Python keeps the last write, so the first entry was dead code — and a trap: reordering
the literal (or deleting the "wrong" line) would silently change the wire contract.

The two values are not always the same string. ``job_id`` arrives from the URL, so a
client may send a UUID in any form ``uuid.UUID`` accepts (upper case, braces, a
``urn:uuid:`` prefix); ``str(job.id)`` is always the canonical lower-case form that the
report *paths* under ``/data/reports/{patient_id}/{job.id}/`` are named with. The
canonical id is the one worth returning, which is what the shadowing accidentally did.
"""

import ast
from pathlib import Path

import pytest

from app.api.models import JobCreate, JobStatus, JobUpdate, WorkflowOptions

UPLOAD_ROUTER = (
    Path(__file__).resolve().parents[1] / "app" / "api" / "routes" / "upload_router.py"
)


def _duplicate_dict_keys(source: str):
    """Every dict literal in ``source`` that repeats a constant key."""
    duplicates = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Dict):
            continue
        seen = set()
        for key in node.keys:
            if not isinstance(key, ast.Constant):
                continue  # ``**spread`` (None) or a computed key: not decidable here
            if key.value in seen:
                duplicates.append((node.lineno, key.value))
            seen.add(key.value)
    return duplicates


def test_upload_router_has_no_shadowed_dict_keys():
    """A repeated key in a dict literal is always a bug: one of the two is dead."""
    duplicates = _duplicate_dict_keys(UPLOAD_ROUTER.read_text(encoding="utf-8"))
    assert not duplicates, f"duplicate keys in dict literals: {duplicates}"


@pytest.fixture
def completed_job(job_service):
    job = job_service.create_job(
        JobCreate(
            name="Genomic Analysis - contract-sample",
            workflow_type="genomic_analysis",
            options=WorkflowOptions(),
            metadata={"patient_id": "patient-contract", "data_id": "data-contract"},
        )
    )
    job_service.update_job(job.id, JobUpdate(status=JobStatus.COMPLETED))
    return job


def test_report_urls_echo_the_canonical_job_id(client, completed_job):
    """The response id must be the resolved job's UUID, not whatever the URL spelled."""
    canonical = str(completed_job.id)
    resp = client.get(f"/upload/reports/job/{canonical.upper()}")

    assert resp.status_code == 200, resp.text
    assert resp.json()["job_id"] == canonical
