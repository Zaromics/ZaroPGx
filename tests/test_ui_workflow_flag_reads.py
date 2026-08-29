"""Post-upload panel must read the workflow flags where the server puts them.

BACKLOG 137b moved the upload response's workflow toggles and messages one
level down -- from ``workflow.<flag>`` to ``workflow.options.<flag>``
(``WorkflowInfo`` now holds only ``workflow_type`` + ``options:
WorkflowOptions``). app/templates/index.html kept reading them at the old top
level, so ``wf.unsupported``, ``wf.recommendations``, ``wf.warnings``,
``wf.unsupported_reason`` and the five ``needs_*`` reads in
``buildPlannedWorkflowHTML`` all evaluated to ``undefined``: the Workflow
Details panel silently rendered nothing at all after an upload. The GRCh37/hg19
copy -- which now tells the user the file will be lifted over to GRCh38 and
that unliftable variants are dropped -- reached nobody.

Two tests, deliberately of different kinds:

* ``test_panel_renders_grch37_warnings_from_real_upload_response`` is the real
  evidence. It drives the actual FastAPI upload endpoint (heavy deps stubbed,
  but the workflow dict comes from the real ``FileProcessor.determine_workflow``
  and the response is built by the real ``upload_router`` code), then executes
  the template's real inline ``<script>`` against that exact JSON in Node and
  asserts the rendered HTML contains the warning and recommendation strings.
* ``test_template_workflow_reads_are_declared_model_fields`` is the rename
  guard. This bug existed because nothing tied the template's read paths to the
  server's field names; that test joins the two, so renaming a
  ``WorkflowOptions`` field breaks a test instead of the UI.
"""

import json
import os
import re
import shutil
import subprocess
import types
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("ZAROPGX_DEV_MODE", "true")
os.environ.setdefault("SECRET_KEY", "pytest-secret-key-not-for-production")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://pytest:pytest@localhost:5432/pytest",
)

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = REPO_ROOT / "app" / "templates" / "index.html"
RENDERER = Path(__file__).resolve().parent / "js" / "render_workflow_panel.js"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _inline_panel_script() -> str:
    """The one inline <script> block that defines the panel renderer."""
    html = TEMPLATE.read_text(encoding="utf-8")
    blocks = re.findall(
        r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, flags=re.DOTALL
    )
    for block in blocks:
        if "window.updateFileAnalysis" in block:
            return block
    raise AssertionError("no inline <script> in index.html defines updateFileAnalysis")


def _grch37_vcf_analysis():
    """A GRCh37 VCF: sets needs_liftover plus warnings and recommendations
    (liftover drops unliftable variants) -- a panel with real copy to lose."""
    from app.api.models import FileType, SequencingProfile, VCFHeaderInfo
    from app.api.utils.file_processor import FileAnalysis as DcFileAnalysis

    return DcFileAnalysis(
        file_type=FileType.VCF,
        is_compressed=True,
        has_index=True,
        file_size=1024,
        vcf_info=VCFHeaderInfo(
            reference_genome="GRCh37",
            sequencing_platform="Illumina",
            sequencing_profile=SequencingProfile.WGS,
            has_index=True,
            is_bgzipped=True,
            contigs=["chr1", "chr2"],
            sample_count=1,
            variant_count=1000,
        ),
        is_valid=True,
        validation_errors=[],
    )


def _upload_grch37_and_capture_response(monkeypatch, tmp_path) -> dict:
    """POST a GRCh37 VCF through the real endpoint and return the JSON body.

    Only the DB/Nextflow/file-IO edges are stubbed. The workflow dict comes
    from the real FileProcessor.determine_workflow (so the user-visible copy is
    the real copy) and the response is assembled by the real upload_router
    (so the nesting is the real nesting).
    """
    import app.main as main
    from app.api.routes import upload_router
    from app.api.utils.file_processor import FileProcessor

    main.app.router.on_startup.clear()
    main.app.router.on_shutdown.clear()

    monkeypatch.setattr(
        upload_router, "create_patient", lambda db, identifier: uuid.uuid4()
    )
    monkeypatch.setattr(
        upload_router,
        "register_genetic_data",
        lambda db, patient_id, file_type, file_path, is_supplementary: uuid.uuid4(),
    )

    class _FakeJob:
        def __init__(self):
            self.id = str(uuid.uuid4())
            self.status = "running"
            self.job_metadata = {}

    class _FakeJobService:
        def __init__(self, db):
            self._job = _FakeJob()

        def create_job(self, job_create):
            return self._job

        def update_job(self, job_id, job_update):
            return self._job

    monkeypatch.setattr(upload_router, "JobService", _FakeJobService)

    class _FakeProgressCalc:
        def calculate_progress_from_steps(self, steps_dict, workflow_config, job_id):
            return types.SimpleNamespace(
                progress_percentage=0,
                stage=types.SimpleNamespace(value="header_analysis"),
                message="stubbed",
            )

    monkeypatch.setattr(upload_router, "WorkflowProgressCalculator", _FakeProgressCalc)

    async def _noop_background(*args, **kwargs):
        return None

    monkeypatch.setattr(
        upload_router, "process_file_nextflow_background_with_db", _noop_background
    )

    async def _fake_process_files(files, reference_genome, **kwargs):
        stored = []
        for f in files:
            p = tmp_path / f.filename
            p.write_bytes(await f.read())
            stored.append(str(p))

        analysis = _grch37_vcf_analysis()
        # Mirror the real FileProcessor.process_upload/process_files tail:
        # determine_workflow(), then file_type / reference / workflow_type.
        workflow = FileProcessor().determine_workflow(analysis)
        workflow["file_type"] = analysis.file_type.value
        workflow["reference"] = reference_genome or "hg38"
        workflow["workflow_type"] = "genomic_analysis"
        return {
            "success": True,
            "file_paths": stored,
            "file_analysis": analysis,
            "workflow": workflow,
        }

    monkeypatch.setattr(
        upload_router.file_processor, "process_files", _fake_process_files
    )

    def _fake_get_db():
        yield object()

    monkeypatch.setitem(main.app.dependency_overrides, main.get_db, _fake_get_db)

    vcf = b"##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS\n"
    with TestClient(main.app) as client:
        resp = client.post(
            "/upload/genomic-data",
            files={"files": ("grch37.vcf", vcf, "text/plain")},
            data={"sample_identifier": "grch37_sample", "reference_genome": "hg19"},
        )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _render_panel(payload: dict, tmp_path: Path) -> dict:
    payload_path = tmp_path / "upload_response.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    proc = subprocess.run(
        [shutil.which("node"), str(RENDERER), str(TEMPLATE), str(payload_path)],
        capture_output=True,
        text=True,
        # The copy under test contains non-ASCII (U+26A0 warning signs); Windows
        # would otherwise decode node's pipe as cp1252 and blow up.
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    return json.loads(proc.stdout)


# --------------------------------------------------------------------------
# the real evidence: render the panel
# --------------------------------------------------------------------------
@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_panel_renders_grch37_warnings_from_real_upload_response(monkeypatch, tmp_path):
    payload = _upload_grch37_and_capture_response(monkeypatch, tmp_path)

    # The shape this whole bug is about: flags are nested under options, and
    # are NOT present at the level index.html used to read them.
    workflow = payload["workflow"]
    assert "options" in workflow
    for flag in ("unsupported", "warnings", "recommendations", "unsupported_reason"):
        assert flag not in workflow, f"{flag} unexpectedly flat -- premise changed"
        assert flag in workflow["options"]

    options = workflow["options"]
    # A GRCh37 VCF is supported via the liftover now: not unsupported, not
    # provisional, needs_liftover set -- but still carrying warnings (dropped
    # variants) and recommendations (native GRCh38 is better evidence).
    assert options["unsupported"] is False
    assert options["is_provisional"] is False
    assert options["needs_liftover"] is True
    assert options["warnings"] and options["recommendations"]

    rendered = _render_panel(payload, tmp_path)
    assert rendered["ok"], rendered["errors"]

    els = rendered["elements"]
    assert "warnings" in els, "panel never touched the #warnings element"

    # The alert must be visible (it ships with d-none in the markup) ...
    warn_el = els["warnings"]
    assert "d-none" not in warn_el["classes"].split(), (
        "#warnings stayed hidden: " + warn_el["html"][:200]
    )
    assert "alert-warning" in warn_el["classes"].split()

    # ... the warning list must carry the server's exact strings ...
    for warning in options["warnings"]:
        assert warning in warn_el["html"], f"missing warning in panel: {warning!r}"
    assert "<strong>Warnings:</strong>" in warn_el["html"]

    # ... and no red Unsupported alert: a lifted-over file is supported, so the
    # panel must not contradict that verdict beside the liftover warnings.
    assert options["unsupported_reason"] is None
    assert "Unsupported:" not in warn_el["html"]

    # ... and the recommendations list must render too.
    info_el = els["infoAlertsLeft"]
    for rec in options["recommendations"]:
        assert rec in info_el["html"], f"missing recommendation in panel: {rec!r}"
    assert "alert-info" in info_el["html"]

    # The panel itself must be revealed.
    assert "d-none" not in els["workflowAnalysisPanel"]["classes"].split()

    # Sanity: the liftover honesty copy is what actually reached the DOM.
    combined = warn_el["html"] + info_el["html"]
    assert "GRCh38" in combined or "hg38" in combined
    assert "lift" in combined.lower()
    # Stem, not "dropped": what must hold is that the copy admits variants are
    # lost, not which tense it says it in. Pinning the inflection made an
    # editorial pass over the wording look like a behaviour regression.
    assert "drop" in combined.lower()


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_panel_renders_nothing_when_flags_are_only_at_the_old_top_level(tmp_path):
    """Negative control: the pre-137b shape must NOT satisfy the assertions
    above by accident. If the panel rendered warnings from a payload whose
    options are empty, the test above would prove nothing.
    """
    payload = {
        "file_type": "vcf",
        "analysis_info": {"file_type": "vcf"},
        "workflow": {
            "workflow_type": "genomic_analysis",
            "options": {"unsupported": False, "recommendations": [], "warnings": []},
            # pre-137b placement -- must be ignored now
            "unsupported": True,
            "unsupported_reason": "stale top-level reason",
            "warnings": ["stale top-level warning"],
            "recommendations": ["stale top-level recommendation"],
        },
    }
    rendered = _render_panel(payload, tmp_path)
    assert rendered["ok"], rendered["errors"]
    html = rendered["elements"]["warnings"]["html"]
    html += rendered["elements"]["infoAlertsLeft"]["html"]
    assert "stale top-level" not in html
    assert "d-none" in rendered["elements"]["warnings"]["classes"].split()


# --------------------------------------------------------------------------
# the rename guard
# --------------------------------------------------------------------------
def test_template_workflow_reads_are_declared_model_fields(client):
    """Every workflow field index.html reads must be one some endpoint really sends.

    Nothing tied the template's read paths to the response builder's field
    names, which is why 137b's rename went unnoticed. Rename or drop a
    WorkflowOptions field now and this fails.

    The panel is fed by two endpoints, so there are two sources of truth.
    ``POST /upload/genomic-data`` sends ``WorkflowOptions``. ``POST
    /upload/inspect-header`` sends a flat compat dict that carries a couple of
    fields ``WorkflowOptions`` cannot have: it also answers for files the upload
    would *refuse*, and a refused file has no workflow to describe. Those keys
    are taken from a real preview response rather than a hardcoded list, so
    renaming one server-side still breaks this test.
    """
    from app.api.models import WorkflowInfo, WorkflowOptions

    script = _inline_panel_script()

    # The normalizer's one hop from WorkflowInfo down to its options payload.
    assert "options" in WorkflowInfo.model_fields
    assert re.search(r"\bwf\.options\b", script), (
        "index.html no longer hops through workflow.options; the flags live "
        "there since 137b"
    )

    preview = client.post(
        "/upload/inspect-header",
        files={"file": ("preview.bam", b"BAM\x01", "application/octet-stream")},
    )
    assert preview.status_code == 200, preview.text
    preview_fields = set(preview.json()["compat"]["workflow"])

    read = {m.group(1) for m in re.finditer(r"\bwf\.([a-zA-Z_][a-zA-Z0-9_]*)", script)}
    read.discard("options")  # the hop itself, checked above

    sent = set(WorkflowOptions.model_fields) | preview_fields
    unknown = sorted(read - sent)
    assert not unknown, (
        f"index.html reads workflow fields no endpoint sends: {unknown}. "
        f"WorkflowOptions declares: {sorted(WorkflowOptions.model_fields)}; "
        f"the header preview adds: {sorted(preview_fields - set(WorkflowOptions.model_fields))}"
    )

    # The four reads that were dead must actually still be read somewhere.
    for field in ("unsupported", "unsupported_reason", "recommendations", "warnings"):
        assert field in read, f"index.html stopped rendering workflow.{field}"


def test_preflight_header_path_reads_the_flat_compat_shape():
    """/upload/inspect-header answers with a flat compat workflow dict (no
    ``options`` hop), and the View Header path reads it flat -- correct, and it
    must stay that way. The normalizer is idempotent so both call sites can
    share buildPlannedWorkflowHTML.
    """
    import inspect

    from app.api.routes import upload_router

    source = inspect.getsource(upload_router.inspect_file_header)
    assert '"compat": {"workflow": compat_workflow}' in source
    for field in ("recommendations", "warnings", "unsupported", "unsupported_reason"):
        assert f'"{field}"' in source
    assert '"options"' not in source, (
        "inspect-header started nesting under options; index.html's View Header "
        "reads are flat and would go dead"
    )

    script = _inline_panel_script()
    assert "headerInfo.workflow.unsupported_reason" in script
    assert "headerInfo.workflow.recommendations" in script
    assert "headerInfo.workflow.warnings" in script
