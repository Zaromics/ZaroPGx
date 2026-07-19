import os
import types
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

# Ensure dev mode by default for tests (no auth)
os.environ.setdefault("ZAROPGX_DEV_MODE", "true")
# Ensure FHIR router is included
os.environ.setdefault("FHIR_EXPORT_ENABLED", "true")


@pytest.fixture()
def client(monkeypatch):
    # Import after env vars are set
    import app.main as main

    # Prevent startup hooks from trying to init Postgres or call other containers
    main.app.router.on_startup.clear()
    main.app.router.on_shutdown.clear()

    return TestClient(main.app)


def test_openapi_contains_core_paths(client):
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    data = resp.json()

    paths = data.get("paths", {})

    # "Safe" endpoints
    assert "/health" in paths

    # Upload API
    assert "/upload/genomic-data" in paths
    assert "/upload/status/{job_id}" in paths

    # Job-centric report endpoints (upload router)
    assert "/upload/reports/job/{job_id}" in paths
    assert "/upload/reports/download/{patient_id}" in paths

    # FHIR export API
    assert "/fhir/status" in paths
    assert "/fhir/export/run/{run_id}" in paths

    # Workflow API
    assert "/api/v1/workflows/{workflow_id}" in paths


def test_openapi_upload_uses_files_field_name(client):
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    data = resp.json()

    post = data["paths"]["/upload/genomic-data"]["post"]
    request_body = post.get("requestBody")
    assert request_body, "Expected multipart requestBody for /upload/genomic-data"

    content = request_body.get("content", {})
    assert "multipart/form-data" in content

    schema = content["multipart/form-data"].get("schema", {})
    # FastAPI emits the generated multipart body model as a $ref into
    # components/schemas rather than inlining it, so resolve one hop.
    ref = schema.get("$ref")
    if ref:
        schema = data["components"]["schemas"][ref.rsplit("/", 1)[-1]]
    properties = schema.get("properties", {})

    # Critical contract check: endpoint accepts 'files' (List[UploadFile])
    assert "files" in properties


def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload.get("status") == "healthy"


def test_fhir_status_endpoint(client):
    resp = client.get("/fhir/status")
    assert resp.status_code == 200
    payload = resp.json()
    assert "enabled" in payload
    assert "supported_formats" in payload


def test_upload_genomic_data_smoke_without_services(client, monkeypatch, tmp_path):
    """Smoke test the upload endpoint without Postgres/Nextflow/other services.

    This verifies:
    - endpoint is reachable
    - request accepts multipart field named `files`
    - response matches the documented UploadResponse shape at a high level

    Heavy dependencies are stubbed.
    """

    from app.api.models import FileType
    from app.api.routes import upload_router
    from app.api.utils.file_processor import FileAnalysis as DcFileAnalysis

    # ---- stub out DB-related helpers ----
    monkeypatch.setattr(
        upload_router, "create_patient", lambda db, identifier: uuid.uuid4()
    )
    monkeypatch.setattr(
        upload_router,
        "register_genetic_data",
        lambda db, patient_id, file_type, file_path, is_supplementary: uuid.uuid4(),
    )

    # ---- stub out workflow service ----
    class _FakeWorkflow:
        def __init__(self, workflow_id):
            self.id = workflow_id
            self.status = "running"
            self.workflow_metadata = {}
            self.data_id = str(uuid.uuid4())

    class _FakeStep:
        def __init__(self, name, order, container):
            self.step_name = name
            self.status = "pending"
            self.step_order = order
            self.container_name = container
            self.output_data = {}
            self.metadata = {}

    class _FakeWorkflowService:
        def __init__(self, db):
            self._workflow_id = str(uuid.uuid4())
            self._workflow = _FakeWorkflow(self._workflow_id)
            self._steps = []

        def create_workflow(self, workflow_create):
            return self._workflow

        def add_workflow_step(self, workflow_id, step_create):
            self._steps.append(
                _FakeStep(
                    step_create.step_name,
                    step_create.step_order,
                    step_create.container_name,
                )
            )

        def update_workflow(self, workflow_id, workflow_update):
            self._workflow.status = "running"
            return self._workflow

        def get_workflow(self, workflow_id):
            if str(workflow_id) != str(self._workflow.id):
                return None
            return self._workflow

        def get_workflow_steps(self, workflow_id):
            return self._steps

        def get_workflow_logs(self, workflow_id):
            return []

    monkeypatch.setattr(upload_router, "WorkflowService", _FakeWorkflowService)

    # ---- stub out progress calculator used by /upload/status/{job_id} (indirectly) ----
    class _FakeProgressCalc:
        def calculate_progress_from_steps(self, steps_dict, workflow_config, job_id):
            return types.SimpleNamespace(
                progress_percentage=0,
                stage=types.SimpleNamespace(value="header_analysis"),
                message="stubbed",
            )

    monkeypatch.setattr(upload_router, "WorkflowProgressCalculator", _FakeProgressCalc)

    # ---- stub out background task target (so even if executed, it's a no-op) ----
    async def _noop_background(*args, **kwargs):
        return None

    monkeypatch.setattr(
        upload_router, "process_file_nextflow_background_with_db", _noop_background
    )

    # ---- stub out file processing ----
    async def _fake_process_files(files, reference_genome, **kwargs):
        # Write uploaded bytes to a temp file to simulate "stored" upload
        stored_paths = []
        for f in files:
            p = tmp_path / f.filename
            p.write_bytes(await f.read())
            stored_paths.append(str(p))

        analysis = DcFileAnalysis(
            file_type=FileType.VCF,
            is_compressed=False,
            has_index=False,
            file_size=p.stat().st_size,
            vcf_info=None,
            is_valid=True,
            validation_errors=[],
        )

        return {
            "success": True,
            "file_paths": stored_paths,
            "file_analysis": analysis,
            "workflow": {
                "workflow_type": "genomic_analysis",
                "file_type": "vcf",
                "needs_alignment": False,
                "needs_gatk": False,
                "needs_hla": False,
                "needs_pypgx_bam2vcf": False,
                "needs_pypgx": False,
                "needs_pharmcat": True,
                "needs_report": True,
                "reference": reference_genome or "hg38",
                "is_provisional": False,
                "recommendations": [],
                "warnings": [],
            },
        }

    monkeypatch.setattr(
        upload_router.file_processor, "process_files", _fake_process_files
    )

    # ---- override get_db dependency to avoid real Postgres ----
    import app.main as main

    def _fake_get_db():
        # The upload endpoint passes this into our fake WorkflowService but we don't use it.
        yield object()

    # monkeypatch.setitem so the previous entry (conftest's SQLite override) is
    # restored even when an assertion below fails; the old code cleared it only
    # on the happy path, leaking onto the shared app singleton for the session.
    monkeypatch.setitem(main.app.dependency_overrides, main.get_db, _fake_get_db)

    # Build a minimal VCF payload
    vcf_bytes = b"##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\n"

    resp = client.post(
        "/upload/genomic-data",
        files={"files": ("sample.vcf", vcf_bytes, "text/plain")},
        data={"sample_identifier": "test_sample", "reference_genome": "hg38"},
    )

    assert resp.status_code == 200
    payload = resp.json()

    assert "job_id" in payload
    assert "file_id" in payload
    assert payload.get("file_type") == "vcf"
    assert payload.get("status") in {"processing", "uploaded"}
    assert "message" in payload
