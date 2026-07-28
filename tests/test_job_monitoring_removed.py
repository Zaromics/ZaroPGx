"""Regression: Job monitoring stack must be gone; Workflow progress stays."""

import importlib
import importlib.util

import pytest
from fastapi.testclient import TestClient


def test_monitoring_jobs_returns_404(client: TestClient):
    assert client.get("/monitoring/jobs").status_code == 404
    assert client.get("/monitoring/jobs/status/pending").status_code == 404
    assert client.post("/monitoring/jobs", json={}).status_code == 404


def test_monitoring_progress_duplicate_gone(client: TestClient):
    # Duplicate lived at /monitoring/progress/{id}; canonical is under workflows
    fake_id = "00000000-0000-0000-0000-000000000001"
    assert client.get(f"/monitoring/progress/{fake_id}").status_code == 404


def test_workflow_progress_route_still_registered(client: TestClient):
    fake_id = "00000000-0000-0000-0000-000000000001"
    # 404-not-found workflow is fine; 405/404-on-path would mean route missing
    r = client.get(f"/api/v1/workflows/{fake_id}/progress")
    assert r.status_code in (404, 200)


def test_job_status_service_module_gone():
    spec = importlib.util.find_spec("app.services.job_status_service")
    assert spec is None


def test_job_pydantic_symbols_gone():
    import app.api.models as models

    for name in (
        "JobStatus",
        "JobStage",
        "JobStageStatus",
        "JobEventType",
        "JobBase",
        "JobCreate",
        "JobUpdate",
        "JobResponse",
        "JobStageResponse",
        "JobEventResponse",
        "JobProgressUpdate",
    ):
        assert not hasattr(models, name), name


def test_job_orm_symbols_gone():
    import app.api.db as db

    for name in ("Job", "JobStage", "JobEvent", "JobDependency"):
        assert not hasattr(db, name), name
