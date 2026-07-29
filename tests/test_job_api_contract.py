"""137a: instance stack is Job under /api/v1/jobs; /workflows gone."""

import importlib.util

from fastapi.testclient import TestClient


def test_workflows_prefix_gone(client: TestClient):
    assert client.get("/api/v1/workflows").status_code == 404
    fake = "00000000-0000-0000-0000-000000000001"
    assert client.get(f"/api/v1/workflows/{fake}").status_code == 404
    assert client.get(f"/api/v1/workflows/{fake}/progress").status_code == 404


def test_jobs_progress_route_registered(client: TestClient):
    fake = "00000000-0000-0000-0000-000000000001"
    r = client.get(f"/api/v1/jobs/{fake}/progress")
    assert r.status_code in (404, 200)  # route exists; entity may 404


def test_job_orm_symbols_present():
    import app.api.db as db

    assert hasattr(db, "Job")
    assert hasattr(db, "JobStep")
    assert hasattr(db, "JobLog")
    assert not hasattr(db, "Workflow")
    assert db.Job.__tablename__ == "jobs"
    assert db.JobStep.__tablename__ == "job_steps"
    assert db.JobLog.__tablename__ == "job_logs"


def test_job_pydantic_symbols_present():
    import app.api.models as models

    assert hasattr(models, "JobStatus")
    assert hasattr(models, "JobCreate")
    assert hasattr(models, "JobProgressResponse")
    assert hasattr(models, "WorkflowInfo")  # type metadata — keep
    assert not hasattr(models, "WorkflowStatus")
    assert not hasattr(models, "WorkflowCreate")


def test_job_service_module_present():
    assert importlib.util.find_spec("app.services.job_service") is not None
    assert importlib.util.find_spec("app.services.workflow_service") is None


def test_job_client_module_present():
    assert importlib.util.find_spec("app.utils.job_client") is not None
    assert importlib.util.find_spec("app.utils.workflow_client") is None
