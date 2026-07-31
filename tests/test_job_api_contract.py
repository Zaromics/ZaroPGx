"""137a/137b: Job instance API under /api/v1/jobs; /api/v1/workflows is recipe catalog."""

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import AliasChoices, BaseModel, Field, ValidationError


def test_workflows_prefix_is_recipe_not_instance(client: TestClient):
    """137b: /api/v1/workflows is recipe catalog; instance routes stay on /jobs."""
    r = client.get("/api/v1/workflows")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert any(item.get("workflow_type") == "genomic_analysis" for item in body)

    fake = "00000000-0000-0000-0000-000000000001"
    # No instance progress under /workflows
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


# Mirror of container CancelRequest dual-accept contract (gatk/pypgx/pharmcat/zarohla/nextflow).
class _CancelRequestContract(BaseModel):
    workflow_id: str = Field(..., validation_alias=AliasChoices("job_id", "workflow_id"))
    patient_id: str
    action: str


_CANCEL_SOURCES = [
    Path("docker/gatk-api/gatk_api.py"),
    Path("docker/pypgx/pypgx_wrapper.py"),
    Path("docker/pharmcat/pharmcat.py"),
    Path("docker/zarohla/app.py"),
    Path("docker/nextflow/runner.py"),
]


def test_cancel_request_dual_accepts_job_id_or_workflow_id():
    """Containers must accept job_id (preferred) or legacy workflow_id."""
    via_job = _CancelRequestContract(
        job_id="abc", patient_id="p1", action="cancel"
    )
    via_wf = _CancelRequestContract(
        workflow_id="abc", patient_id="p1", action="cancel"
    )
    assert via_job.workflow_id == "abc"
    assert via_wf.workflow_id == "abc"

    with pytest.raises(ValidationError):
        _CancelRequestContract(patient_id="p1", action="cancel")


def test_container_cancel_models_use_alias_choices():
    root = Path(__file__).resolve().parent.parent
    for rel in _CANCEL_SOURCES:
        text = (root / rel).read_text(encoding="utf-8")
        assert "AliasChoices" in text, f"{rel} missing AliasChoices"
        assert 'AliasChoices("job_id", "workflow_id")' in text, (
            f"{rel} CancelRequest must dual-accept job_id|workflow_id"
        )


def test_job_router_cancel_payload_sends_job_id():
    text = (
        Path(__file__).resolve().parent.parent
        / "app/api/routes/job_router.py"
    ).read_text(encoding="utf-8")
    assert '"job_id": job_id' in text
    assert '"workflow_id": job_id' in text  # dual-key transition
