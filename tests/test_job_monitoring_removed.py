"""Regression: legacy Job *monitoring* stack must stay gone; instance Job (137a) stays."""

import importlib.util

from fastapi.testclient import TestClient


def test_monitoring_jobs_returns_404(client: TestClient):
    assert client.get("/monitoring/jobs").status_code == 404
    assert client.get("/monitoring/jobs/status/pending").status_code == 404
    assert client.post("/monitoring/jobs", json={}).status_code == 404


def test_monitoring_progress_duplicate_gone(client: TestClient):
    # Duplicate lived at /monitoring/progress/{id}; canonical is under /api/v1/jobs
    fake_id = "00000000-0000-0000-0000-000000000001"
    assert client.get(f"/monitoring/progress/{fake_id}").status_code == 404


def test_job_progress_route_still_registered(client: TestClient):
    fake_id = "00000000-0000-0000-0000-000000000001"
    # 404-not-found job is fine; route must exist under /api/v1/jobs
    r = client.get(f"/api/v1/jobs/{fake_id}/progress")
    assert r.status_code in (404, 200)


def test_job_status_service_module_gone():
    """138 legacy monitoring helper — must remain absent."""
    spec = importlib.util.find_spec("app.services.job_status_service")
    assert spec is None


def test_legacy_monitoring_only_symbols_gone():
    """Assert 138-only monitoring symbols stay gone; 137a Job symbols may exist."""
    import app.api.db as db
    import app.api.models as models

    # 138 monitoring-era Pydantic (distinct from 137a JobCreate/Update/Status/Response)
    for name in (
        "JobStage",
        "JobStageStatus",
        "JobEventType",
        "JobBase",
        "JobStageResponse",
        "JobEventResponse",
        "JobProgressUpdate",
    ):
        assert not hasattr(models, name), name

    # 138 monitoring-era ORM
    for name in ("JobEvent", "JobDependency"):
        assert not hasattr(db, name), name

    # 137a instance stack must remain
    assert hasattr(models, "JobStatus")
    assert hasattr(models, "JobCreate")
    assert hasattr(models, "JobUpdate")
    assert hasattr(models, "JobResponse")
    assert hasattr(db, "Job")
    assert hasattr(db, "JobStep")
    assert hasattr(db, "JobLog")
