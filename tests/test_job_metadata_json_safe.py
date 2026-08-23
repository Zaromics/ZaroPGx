"""Regression tests for datetime-in-metadata JSON serialization.

A live VCF run reached ``update_job`` at the end of report generation with
``metadata["reports"]`` carrying datetime objects (PharmCAT/PyPGx processed data).
``jobs.job_metadata`` is a ``Column(JSON)`` serialized with ``json.dumps``, so the
write raised ``TypeError: Object of type datetime is not JSON serializable`` and the
job was marked failed even though the report and all its artifacts had generated. The
fix coerces datetimes to ISO strings before the value reaches the column.
"""

from datetime import date, datetime, timezone

from app.api.db import Job
from app.api.models import JobCreate, JobUpdate
from app.services.job_service import _json_safe


def test_json_safe_coerces_datetime_to_isoformat():
    dt = datetime(2026, 8, 23, 15, 7, 56, tzinfo=timezone.utc)
    assert _json_safe(dt) == dt.isoformat()


def test_json_safe_recurses_through_dicts_and_lists():
    dt = datetime(2026, 8, 23, 9, 0, 0)
    payload = {
        "reports": {"generated_at": dt, "paths": ["a.html", "b.pdf"]},
        "runs": [{"when": dt}, {"when": date(2026, 8, 23)}],
        "count": 3,
        "name": "keep-me",
    }
    safe = _json_safe(payload)
    assert safe["reports"]["generated_at"] == dt.isoformat()
    assert safe["reports"]["paths"] == ["a.html", "b.pdf"]
    assert safe["runs"][0]["when"] == dt.isoformat()
    assert safe["runs"][1]["when"] == "2026-08-23"
    assert safe["count"] == 3
    assert safe["name"] == "keep-me"


def test_json_safe_leaves_plain_json_untouched():
    plain = {"a": 1, "b": [True, None, "x"], "c": 1.5}
    assert _json_safe(plain) == plain


def test_update_job_with_datetime_metadata_persists(job_service, db_session):
    """The bug end to end: update_job must not raise on datetime-bearing metadata."""
    job = job_service.create_job(
        JobCreate(
            name="Test Job",
            workflow_type="genomic_analysis",
            metadata={},
            created_by="pytest",
        )
    )
    generated_at = datetime(2026, 8, 23, 15, 7, 56, tzinfo=timezone.utc)
    job_service.update_job(
        job.id,
        JobUpdate(
            metadata={"reports": {"generated_at": generated_at, "pdf_path": "r.pdf"}}
        ),
    )
    db_session.expire_all()
    stored = db_session.query(Job).filter(Job.id == job.id).first().job_metadata
    assert stored["reports"]["generated_at"] == generated_at.isoformat()
    assert stored["reports"]["pdf_path"] == "r.pdf"
