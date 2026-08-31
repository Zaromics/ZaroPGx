# tests/test_workflow_recipe_registry.py
"""137b: Workflow recipe registry, Job workflow_type/snapshot, mint via registry."""

import importlib.util

import pytest
from fastapi.testclient import TestClient


def test_workflow_registry_module_present():
    assert importlib.util.find_spec("app.services.workflow_registry") is not None


def test_registry_lists_genomic_analysis():
    from app.services.workflow_registry import get_recipe, list_recipes

    recipes = list_recipes()
    assert any(r.workflow_type == "genomic_analysis" for r in recipes)
    recipe = get_recipe("genomic_analysis")
    assert recipe is not None
    assert recipe.display_name
    assert recipe.step_templates


def test_get_recipe_unknown_returns_none():
    from app.services.workflow_registry import get_recipe

    assert get_recipe("no_such_recipe") is None


def test_resolve_steps_always_includes_header_and_pharmcat():
    from app.api.models import WorkflowOptions
    from app.services.workflow_registry import resolve_steps

    steps = resolve_steps("genomic_analysis", WorkflowOptions())
    names = [s.step_name for s in steps]
    assert names[0] == "header_analysis"
    assert "pharmcat_analysis" in names
    assert "hla_typing" not in names
    assert "pypgx_analysis" not in names
    assert "report_generation" in names  # needs_report default True


def test_resolve_steps_respects_toggles():
    from app.api.models import WorkflowOptions
    from app.services.workflow_registry import resolve_steps

    opts = WorkflowOptions(
        needs_hla=True,
        needs_pypgx=True,
        needs_pypgx_bam2vcf=True,
        needs_report=False,
        needs_gatk=True,
    )
    names = [s.step_name for s in resolve_steps("genomic_analysis", opts)]
    assert "hla_typing" in names
    assert "pypgx_bam2vcf" in names
    assert "pypgx_analysis" in names
    assert "report_generation" not in names
    # needs_gatk DOES mint a step now. It was "orchestration only" here because the
    # registry had no GATK conversion template at all -- which is precisely why
    # main.nf's CramToBAM/SamToBAM status updates 404'd and the CRAM and SAM lanes
    # hung at [pending]. See tests/test_pipeline_step_names_are_registered.py.
    assert "gatk_cram_sam_to_bam" in names
    # ...but only the CRAM/SAM conversion. FASTQ alignment is gated on
    # needs_alignment, which nothing sets, and the BCF conversion on needs_conversion.
    assert "gatk_alignment" not in names
    assert "bcf_to_vcf" not in names


def test_resolve_steps_unknown_key_raises():
    from app.api.models import WorkflowOptions
    from app.services.workflow_registry import resolve_steps

    with pytest.raises(ValueError, match="Unknown workflow_type"):
        resolve_steps("no_such_recipe", WorkflowOptions())


def test_get_workflows_api(client: TestClient):
    r = client.get("/api/v1/workflows")
    assert r.status_code == 200
    assert any(x["workflow_type"] == "genomic_analysis" for x in r.json())


def test_get_workflow_by_key(client: TestClient):
    r = client.get("/api/v1/workflows/genomic_analysis")
    assert r.status_code == 200
    body = r.json()
    assert body["workflow_type"] == "genomic_analysis"
    assert "step_templates" in body


def test_get_workflow_unknown_404(client: TestClient):
    assert client.get("/api/v1/workflows/no_such_recipe").status_code == 404


def test_job_orm_has_workflow_type_and_snapshot():
    from app.api.db import Job

    assert hasattr(Job, "workflow_type")
    assert hasattr(Job, "workflow_snapshot")


def test_job_create_requires_workflow_type():
    from pydantic import ValidationError

    from app.api.models import JobCreate

    with pytest.raises(ValidationError):
        JobCreate(name="x")  # missing workflow_type


def test_post_job_mints_steps_and_snapshot(client: TestClient):
    r = client.post(
        "/api/v1/jobs/",
        json={
            "name": "137b mint test",
            "workflow_type": "genomic_analysis",
            "options": {"needs_pypgx": True, "needs_report": True},
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["workflow_type"] == "genomic_analysis"
    assert body["workflow_snapshot"] is not None
    assert body["workflow_snapshot"]["workflow_type"] == "genomic_analysis"
    job_id = body["id"]

    steps = client.get(f"/api/v1/jobs/{job_id}/steps").json()
    names = [s["step_name"] for s in steps]
    assert "header_analysis" in names
    assert "pypgx_analysis" in names
    assert "pharmcat_analysis" in names
    assert "report_generation" in names


def test_post_job_unknown_workflow_type_400(client: TestClient):
    r = client.post(
        "/api/v1/jobs/",
        json={"name": "bad", "workflow_type": "no_such_recipe"},
    )
    assert r.status_code == 400
