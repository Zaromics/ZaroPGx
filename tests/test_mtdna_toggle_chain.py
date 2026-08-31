"""Pins the mtDNA toggle -> needs_mtdna -> skip_mtdna chain end to end.

Task 5 (2026-08-30 CI-readiness pass) asked to confirm that unticking the
mtDNA glyph in app/templates/index.html genuinely removes mtDNA from a run,
and that ticking it does the reverse. That was verified live against the
real running stack (http://localhost:8765): toggling #stageMtDNA's
data-enabled attribute and submitting a real upload flipped
workflow.options.needs_mtdna in the JSON response, flipped skip_mtdna in the
app's own log line, changed the minted step count (4 steps off / 5 steps
on), and -- with the toggle on -- the mtdna_analysis step actually ran to
completion against the real mtdna sidecar.

This module is the regression pin for the two backend hops that live
verification exercised but nothing in the test suite previously covered:

1. The user-toggle override in FileProcessor.process_files() -- the
   ``mtdna_enabled`` form field flipping workflow["needs_mtdna"] even when
   the file type would otherwise need it (file_processor.py:1364-1386).
2. The needs_mtdna -> skip_mtdna string derivation in upload_router.py's
   background Nextflow-submission path (the line the app logs as "Skip
   flags: ... skip_mtdna=...").

The third hop -- needs_mtdna -> whether the mtdna_analysis step is minted --
is already pinned by test_mtdna_workflow_flags.py; this module does not
repeat it.

A fourth hop was added 2026-08-30, upstream of hop 1: the ``/genomic-data``
endpoint (upload_router.py) used to forward an absent ``mtdna_enabled`` form
field straight through as ``None``, which hop 1's ``is not None`` guard
always treats as "no override" -- so an operator's own MTDNA_ENABLED server
setting was never consulted, and CI's MTDNA_ENABLED=false (mtdna is not
started in e2e, to keep the runner's disk usage down) did nothing. The
endpoint now defaults an absent form field to MTDNA_ENABLED before calling
process_files, so a present field still wins unchanged, and an absent one
falls through to the server's own configuration instead of silently
defaulting to "on". test_form_field_wins_over_env_default and
test_absent_form_field_falls_through_to_env_default below pin that,
end-to-end through the real endpoint.
"""

import re
import uuid
from pathlib import Path

import pytest

from app.api.models import FileType
from app.api.utils.file_processor import FileAnalysis, FileProcessor

REPO_ROOT = Path(__file__).resolve().parents[1]
UPLOAD_ROUTER = REPO_ROOT / "app" / "api" / "routes" / "upload_router.py"


def _bam_analysis() -> FileAnalysis:
    """A BAM: determine_workflow() sets needs_mtdna=True for this file type
    (file_processor.py:801), so it is real input for the override to flip."""
    return FileAnalysis(
        file_type=FileType.BAM,
        is_compressed=True,
        has_index=True,
        file_size=1024,
        is_valid=True,
        validation_errors=[],
    )


class _FakeUpload:
    """Minimal UploadFile stand-in: filename + a real, terminating async read()."""

    def __init__(self, filename: str, content: bytes = b"BAM\x01"):
        self.filename = filename
        self._content = content
        self._sent = False

    async def read(self, _size: int = -1) -> bytes:
        if self._sent:
            return b""
        self._sent = True
        return self._content


async def _process_with_toggle(tmp_path, monkeypatch, mtdna_enabled):
    """Run the real FileProcessor.process_files() toggle-override logic,
    with only the file-type analysis stubbed (a BAM is what real analysis
    would need samtools-backed content for; the override logic under test
    only cares that analyze_file resolves to a BAM FileAnalysis)."""
    processor = FileProcessor()
    processor.temp_dir = tmp_path

    async def _fake_analyze_file(_path):
        return _bam_analysis()

    monkeypatch.setattr(processor, "analyze_file", _fake_analyze_file)

    result = await processor.process_files(
        [_FakeUpload("sample.bam")],
        reference_genome="hg38",
        mtdna_enabled=mtdna_enabled,
    )
    assert result["success"], result.get("error")
    return result["workflow"]


# --------------------------------------------------------------------------
# Hop 0: the /genomic-data endpoint's MTDNA_ENABLED fallback (added
# 2026-08-30) -> the mtdna_enabled value handed to FileProcessor.process_files
# --------------------------------------------------------------------------
@pytest.fixture
def upload_capturing_mtdna_kwarg(client, monkeypatch, tmp_path):
    """POST /upload/genomic-data through the real endpoint and capture the
    ``mtdna_enabled`` kwarg it forwards to FileProcessor.process_files --
    everything else about the upload (patient/job creation, background
    Nextflow submission, file analysis) is stubbed the same way
    test_upload_unsupported_enforcement.py's ``upload`` fixture does it, so
    only the endpoint's own fallback logic is under test."""
    from app.api.routes import upload_router

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
            self.id = uuid.uuid4()
            self.status = "pending"
            self.job_metadata = {}

    class _FakeJobService:
        def __init__(self, db):
            self.job = _FakeJob()

        def create_job(self, job_create):
            return self.job

        def update_job(self, job_id, job_update):
            return self.job

    monkeypatch.setattr(upload_router, "JobService", _FakeJobService)

    async def _noop_background(*args, **kwargs):
        return None

    monkeypatch.setattr(
        upload_router, "process_file_nextflow_background_with_db", _noop_background
    )

    captured = {}

    async def _fake_process_files(files, reference_genome, **kwargs):
        captured["mtdna_enabled"] = kwargs.get("mtdna_enabled")
        stored = []
        for f in files:
            p = tmp_path / f.filename
            p.write_bytes(await f.read())
            stored.append(str(p))
        workflow = {
            "workflow_type": "genomic_analysis",
            "file_type": "bam",
            "reference": reference_genome or "hg38",
            "recommendations": [],
            "warnings": [],
            "unsupported": False,
            "unsupported_reason": None,
            "is_provisional": False,
            "needs_mtdna": True,
        }
        return {
            "success": True,
            "file_paths": stored,
            "file_analysis": _bam_analysis(),
            "workflow": workflow,
        }

    monkeypatch.setattr(
        upload_router.file_processor, "process_files", _fake_process_files
    )

    def _post(mtdna_form_value=None):
        data = {"reference_genome": "hg38"}
        if mtdna_form_value is not None:
            data["mtdna_enabled"] = mtdna_form_value
        resp = client.post(
            "/upload/genomic-data",
            files={"files": ("sample.bam", b"BAM\x01", "application/octet-stream")},
            data=data,
        )
        return resp, captured.get("mtdna_enabled")

    return _post


@pytest.mark.parametrize(
    "form_value,env_default",
    [("true", False), ("false", True), ("true", True), ("false", False)],
)
def test_form_field_wins_over_env_default(
    upload_capturing_mtdna_kwarg, monkeypatch, form_value, env_default
):
    """A present mtdna_enabled form field must reach process_files unchanged,
    regardless of what MTDNA_ENABLED says -- the user's explicit choice wins,
    exactly as before this fix."""
    monkeypatch.setattr("app.main.MTDNA_ENABLED", env_default)
    resp, captured = upload_capturing_mtdna_kwarg(mtdna_form_value=form_value)
    assert resp.status_code == 200, resp.text
    assert captured == form_value


@pytest.mark.parametrize("env_default,expected", [(True, "true"), (False, "false")])
def test_absent_form_field_falls_through_to_env_default(
    upload_capturing_mtdna_kwarg, monkeypatch, env_default, expected
):
    """No mtdna_enabled field at all (e.g. e2e's client, or any caller that
    omits it) must fall through to the server's own MTDNA_ENABLED, in both
    directions -- this is the fix: previously it always reached
    process_files as None, so file_processor.py:1383's `is not None` guard
    never applied a server-side override and MTDNA_ENABLED=false did
    nothing."""
    monkeypatch.setattr("app.main.MTDNA_ENABLED", env_default)
    resp, captured = upload_capturing_mtdna_kwarg(mtdna_form_value=None)
    assert resp.status_code == 200, resp.text
    assert captured == expected


# --------------------------------------------------------------------------
# Hop 1: mtdna_enabled form field -> workflow["needs_mtdna"]
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_toggle_off_forces_needs_mtdna_false_even_for_bam(tmp_path, monkeypatch):
    """A BAM needs mtDNA calling by default; unticking the glyph must still
    win. Mirrors file_processor.py:1383-1386's override comment verbatim."""
    workflow = await _process_with_toggle(tmp_path, monkeypatch, mtdna_enabled="false")
    assert workflow["mtdna_enabled"] is False
    assert workflow["needs_mtdna"] is False


@pytest.mark.asyncio
async def test_toggle_on_leaves_bams_needs_mtdna_true(tmp_path, monkeypatch):
    workflow = await _process_with_toggle(tmp_path, monkeypatch, mtdna_enabled="true")
    assert workflow["mtdna_enabled"] is True
    assert workflow["needs_mtdna"] is True


@pytest.mark.asyncio
async def test_toggle_absent_does_not_disable_what_the_file_type_needs(
    tmp_path, monkeypatch
):
    """No form field at all (mtdna_enabled=None) must not be treated as "off":
    only an explicit false disables it (file_processor.py:1383's `is not None`
    guard). The real UI always sends the field (index.html's toggle defaults
    to data-enabled="true"), but a caller that omits it -- e.g. a direct API
    call -- must not silently lose mtDNA."""
    workflow = await _process_with_toggle(tmp_path, monkeypatch, mtdna_enabled=None)
    assert workflow["needs_mtdna"] is True


# --------------------------------------------------------------------------
# Hop 2: workflow["needs_mtdna"] -> the skip_mtdna string sent to Nextflow
# --------------------------------------------------------------------------
def _skip_mtdna_formula():
    """Extract the exact skip_mtdna derivation from upload_router.py, the way
    test_mtdna_citation_honesty.py pins main.nf's params.skip_mtdna default:
    a regex against the real source, not a reimplementation, so a rewrite of
    the line breaks this test instead of silently drifting from the code."""
    text = UPLOAD_ROUTER.read_text(encoding="utf-8")
    match = re.search(
        r'skip_mtdna\s*=\s*"true"\s*if\s*not\s*workflow\.get\(\s*'
        r'"needs_mtdna"\s*,\s*False\s*\)\s*else\s*"false"',
        text,
    )
    assert match, (
        "could not find upload_router's skip_mtdna derivation -- it may have "
        "been rewritten; update this test's regex to match the new form and "
        "re-verify the logic still inverts needs_mtdna correctly"
    )


def test_skip_mtdna_formula_is_the_expected_shape():
    _skip_mtdna_formula()


@pytest.mark.parametrize(
    "needs_mtdna,expected_skip",
    [
        (True, "false"),
        (False, "true"),
    ],
)
def test_skip_mtdna_inverts_needs_mtdna(needs_mtdna, expected_skip):
    """The formula itself (copied verbatim from upload_router.py, pinned as
    matching that source by the regex test above): needs_mtdna=True must
    submit skip_mtdna="false" to Nextflow, and vice versa."""
    workflow = {"needs_mtdna": needs_mtdna}
    skip_mtdna = "true" if not workflow.get("needs_mtdna", False) else "false"
    assert skip_mtdna == expected_skip


def test_skip_mtdna_defaults_to_skipped_when_needs_mtdna_is_absent():
    """workflow.get("needs_mtdna", False): a workflow dict that never set the
    key at all must default to skip_mtdna="true", the same fail-safe default
    main.nf's own params.skip_mtdna carries (test_mtdna_citation_honesty.py)."""
    workflow = {}
    skip_mtdna = "true" if not workflow.get("needs_mtdna", False) else "false"
    assert skip_mtdna == "true"
