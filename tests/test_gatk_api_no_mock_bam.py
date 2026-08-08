"""The GATK API must never report success over a file it did not produce.

Three conversion endpoints used to save the upload, write a ~21-byte ASCII string
into a `.bam`, and answer `{"success": true}`. `pipelines/pgx/main.nf` copies the
returned path forward, so that placeholder entered the pipeline as if it were a real
alignment and every downstream caller (HLA, PyPGx, GATK) consumed it. BACKLOG 0 /
51 / 112 / 113 / 115.

`docker/gatk-api/gatk_api.py` runs in the `gatk-api` sidecar and is not importable
the way `app.*` is: it reads `/job-client` off `sys.path` and imports `psutil`, which
the dev venv does not ship. The fixture below stubs both and repoints DATA_DIR /
TMPDIR / REFERENCE_DIR at a temp tree, which buys real behavioural coverage of the
handlers -- status codes, the samtools argv, and what is left on disk after a
failure -- rather than a grep over the source. The two source-level assertions that
remain are regression fences around the exact shape of the old defect.
"""

import importlib.util
import logging
import logging.handlers
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
GATK_API_SOURCE = REPO_ROOT / "docker" / "gatk-api" / "gatk_api.py"

# BGZF (and therefore BAM) starts with the gzip magic bytes. Anything else is not a
# BAM, which is precisely what the old placeholder was.
BAM_MAGIC = b"\x1f\x8b"

MINIMAL_BAM = BAM_MAGIC + b"\x08\x04" + b"\x00" * 60


@pytest.fixture(scope="module")
def source():
    return GATK_API_SOURCE.read_text(encoding="utf-8")


def _fake_psutil():
    module = types.ModuleType("psutil")
    module.virtual_memory = lambda: types.SimpleNamespace(total=16 * 1024**3)
    module.Process = lambda *a, **k: types.SimpleNamespace()
    return module


def _fake_job_client():
    """Stand-in for app/utils/job_client.py, which lives at /job-client in the image."""
    module = types.ModuleType("job_client")

    class JobClient:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("no job server in tests")

    module.JobClient = JobClient
    module.create_job_client = lambda *a, **k: JobClient()
    return module


@pytest.fixture(scope="module")
def gatk_api(tmp_path_factory):
    """Import the sidecar module out-of-container, pointed at a temp data tree."""
    root = tmp_path_factory.mktemp("gatk_api_home")
    before_handlers = list(logging.root.handlers)

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATA_DIR", str(root / "data"))
        mp.setenv("TMPDIR", str(root / "tmp"))
        mp.setenv("REFERENCE_DIR", str(root / "reference"))
        mp.setitem(sys.modules, "psutil", _fake_psutil())
        mp.setitem(sys.modules, "job_client", _fake_job_client())

        spec = importlib.util.spec_from_file_location(
            "zaropgx_gatk_api_under_test", GATK_API_SOURCE
        )
        module = importlib.util.module_from_spec(spec)
        mp.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
        yield module

    # basicConfig() may have attached the module's handlers to the root logger; on
    # Windows an open file handle also blocks temp-dir cleanup.
    for handler in list(logging.root.handlers):
        if handler not in before_handlers:
            logging.root.removeHandler(handler)
    for handler in getattr(module, "_log_handlers", []):
        handler.close()


@pytest.fixture()
def client(gatk_api):
    return TestClient(gatk_api.app)


@pytest.fixture()
def reference_fasta(gatk_api):
    """Materialise the hg38 reference the CRAM path requires."""
    path = Path(gatk_api.REFERENCE_PATHS["hg38"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(">chr1\nACGTACGTACGT\n", encoding="utf-8")
    return path


@pytest.fixture()
def samtools(gatk_api, monkeypatch):
    """Record every subprocess argv and fake a successful samtools conversion."""
    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append({"cmd": cmd, "kwargs": kwargs})
        argv = cmd if isinstance(cmd, (list, tuple)) else str(cmd).split()
        if "-o" in argv:
            Path(argv[argv.index("-o") + 1]).write_bytes(MINIMAL_BAM)
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(gatk_api.subprocess, "run", fake_run)
    return calls


def _bam_files(gatk_api):
    return sorted(Path(gatk_api.TEMP_DIR).rglob("*.bam"))


def _conversion_argv(calls):
    """The one recorded call that is a samtools conversion."""
    matches = []
    for call in calls:
        cmd = call["cmd"]
        argv = list(cmd) if isinstance(cmd, (list, tuple)) else str(cmd).split()
        if argv[:2] == ["samtools", "view"]:
            matches.append(argv)
    assert len(matches) == 1, f"expected one `samtools view` call, got {matches}"
    return matches[0]


# --------------------------------------------------------------------------
# Regression fences on the exact shape of the old defect
# --------------------------------------------------------------------------


def test_no_mock_bam_payload_literal_survives(source):
    offenders = [
        f"{n}: {line.strip()}"
        for n, line in enumerate(source.splitlines(), 1)
        if re.search(r"mock\s*bam", line, re.IGNORECASE)
    ]
    assert offenders == [], "placeholder BAM payload still present:\n" + "\n".join(
        offenders
    )


def test_module_never_hand_writes_a_bam(source):
    """No BAM may be produced by this module except through samtools.

    Renaming the placeholder string would slip past the assertion above; opening a
    `.bam` for binary write would not.
    """
    offenders = re.findall(
        r"open\(\s*(\w*bam\w*)\s*,\s*[\"']wb[\"']", source, re.IGNORECASE
    )
    assert offenders == [], f"BAM written by hand rather than by samtools: {offenders}"


def test_no_endpoint_advertises_a_mock_implementation(source):
    assert "mock implementation" not in source.lower()


# --------------------------------------------------------------------------
# /align-fastq: refuse, do not fabricate
# --------------------------------------------------------------------------


def test_align_fastq_returns_501(client):
    resp = client.post(
        "/align-fastq",
        files={"file": ("sample.fastq", b"@r1\nACGT\n+\nIIII\n", "text/plain")},
        data={"reference_genome": "hg38"},
    )
    assert resp.status_code == 501, resp.text
    detail = resp.json()["detail"].lower()
    assert "not implemented" in detail
    assert "fastq" in detail


def test_align_fastq_leaves_no_bam_behind(client, gatk_api):
    before = _bam_files(gatk_api)
    client.post(
        "/align-fastq",
        files={"file": ("sample.fastq", b"@r1\nACGT\n+\nIIII\n", "text/plain")},
        data={"reference_genome": "hg38"},
    )
    assert _bam_files(gatk_api) == before


def test_align_fastq_docstring_explains_the_501(gatk_api):
    doc = (gatk_api.align_fastq.__doc__ or "").lower()
    assert "501" in doc
    # The ingest layer already rejects FASTQ; the 501 only makes the API agree.
    assert "file_processor" in doc


# --------------------------------------------------------------------------
# /cram-to-bam
# --------------------------------------------------------------------------


def test_cram_to_bam_shells_out_to_samtools_view(
    client, gatk_api, reference_fasta, samtools
):
    resp = client.post(
        "/cram-to-bam",
        files={"file": ("sample.cram", b"CRAM\x03\x00", "application/octet-stream")},
        data={"reference_genome": "hg38"},
    )
    assert resp.status_code == 200, resp.text

    argv = _conversion_argv(samtools)
    assert argv[:3] == ["samtools", "view", "-b"]
    # CRAM is reference-compressed: -T is not optional.
    assert "-T" in argv
    assert argv[argv.index("-T") + 1] == str(reference_fasta)

    body = resp.json()
    assert body["success"] is True
    out = Path(body["bam_path"])
    assert argv[argv.index("-o") + 1] == str(out)
    assert out.read_bytes()[:2] == BAM_MAGIC


def test_upload_reaches_samtools_byte_for_byte(
    client, gatk_api, reference_fasta, samtools
):
    """The upload is streamed to disk in chunks; it must arrive intact."""
    payload = b"CRAM\x03\x00" + bytes(range(256)) * 512

    client.post(
        "/cram-to-bam",
        files={"file": ("sample.cram", payload, "application/octet-stream")},
        data={"reference_genome": "hg38"},
    )

    argv = _conversion_argv(samtools)
    assert Path(argv[-1]).read_bytes() == payload


def test_uploaded_filename_cannot_escape_the_work_directory(
    client, gatk_api, reference_fasta, samtools
):
    """A crafted multipart filename must not steer where anything is written."""
    client.post(
        "/cram-to-bam",
        files={
            "file": (
                "../../../../etc/evil.cram",
                b"CRAM\x03\x00",
                "application/octet-stream",
            )
        },
        data={"reference_genome": "hg38"},
    )

    argv = _conversion_argv(samtools)
    work_dir = Path(gatk_api.TEMP_DIR).resolve()
    for path in (Path(argv[argv.index("-o") + 1]), Path(argv[-1])):
        assert work_dir in path.resolve().parents


def test_cram_to_bam_passes_argv_as_a_list_not_a_shell_string(
    client, gatk_api, reference_fasta, samtools
):
    """An uploaded filename reaches this command line; never hand it to a shell."""
    client.post(
        "/cram-to-bam",
        files={"file": ("sample.cram", b"CRAM\x03\x00", "application/octet-stream")},
        data={"reference_genome": "hg38"},
    )
    call = [c for c in samtools if "view" in str(c["cmd"])][0]
    assert isinstance(call["cmd"], (list, tuple))
    assert call["kwargs"].get("shell") is not True


def test_cram_to_bam_without_reference_fails_and_writes_nothing(
    client, gatk_api, monkeypatch
):
    missing = Path(gatk_api.TEMP_DIR) / "absent" / "nope.fasta"
    monkeypatch.setitem(gatk_api.REFERENCE_PATHS, "hg38", str(missing))
    calls = []
    monkeypatch.setattr(
        gatk_api.subprocess,
        "run",
        lambda cmd, *a, **k: calls.append(cmd),
    )
    before = _bam_files(gatk_api)

    resp = client.post(
        "/cram-to-bam",
        files={"file": ("sample.cram", b"CRAM\x03\x00", "application/octet-stream")},
        data={"reference_genome": "hg38"},
    )

    assert resp.status_code == 400, resp.text
    assert "reference" in resp.json()["detail"].lower()
    assert calls == [], "no conversion should be attempted without a reference"
    assert _bam_files(gatk_api) == before


def test_cram_to_bam_reports_failure_when_samtools_fails(
    client, gatk_api, reference_fasta, monkeypatch
):
    monkeypatch.setattr(
        gatk_api.subprocess,
        "run",
        lambda cmd, *a, **k: subprocess.CompletedProcess(
            cmd, 1, stdout=b"", stderr=b"[E::cram_get_ref] failed to populate reference"
        ),
    )
    before = _bam_files(gatk_api)

    resp = client.post(
        "/cram-to-bam",
        files={"file": ("sample.cram", b"CRAM\x03\x00", "application/octet-stream")},
        data={"reference_genome": "hg38"},
    )

    assert resp.status_code == 500, resp.text
    assert resp.json().get("success") is not True
    assert "cram_get_ref" in resp.json()["detail"]
    assert _bam_files(gatk_api) == before


def test_cram_to_bam_rejects_a_silent_no_op(
    client, gatk_api, reference_fasta, monkeypatch
):
    """samtools exiting 0 without writing output must not become `success: true`."""
    monkeypatch.setattr(
        gatk_api.subprocess,
        "run",
        lambda cmd, *a, **k: subprocess.CompletedProcess(
            cmd, 0, stdout=b"", stderr=b""
        ),
    )
    resp = client.post(
        "/cram-to-bam",
        files={"file": ("sample.cram", b"CRAM\x03\x00", "application/octet-stream")},
        data={"reference_genome": "hg38"},
    )
    assert resp.status_code == 500, resp.text
    assert resp.json().get("success") is not True


def test_cram_to_bam_rejects_output_that_is_not_a_bam(
    client, gatk_api, reference_fasta, monkeypatch
):
    """The whole defect was a non-BAM going downstream; check the magic bytes."""

    def fake_run(cmd, *args, **kwargs):
        argv = list(cmd)
        Path(argv[argv.index("-o") + 1]).write_bytes(b"Not a BAM at all")
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(gatk_api.subprocess, "run", fake_run)
    before = _bam_files(gatk_api)

    resp = client.post(
        "/cram-to-bam",
        files={"file": ("sample.cram", b"CRAM\x03\x00", "application/octet-stream")},
        data={"reference_genome": "hg38"},
    )

    assert resp.status_code == 500, resp.text
    assert _bam_files(gatk_api) == before, "a non-BAM output must not be left on disk"


# --------------------------------------------------------------------------
# /sam-to-bam
# --------------------------------------------------------------------------

SAM_BODY = b"@HD\tVN:1.6\tSO:coordinate\n@SQ\tSN:chr1\tLN:12\n"


def test_sam_to_bam_shells_out_to_samtools_view(client, gatk_api, samtools):
    resp = client.post(
        "/sam-to-bam",
        files={"file": ("sample.sam", SAM_BODY, "text/plain")},
        data={"reference_genome": "hg38"},
    )
    assert resp.status_code == 200, resp.text

    argv = _conversion_argv(samtools)
    assert argv[:3] == ["samtools", "view", "-b"]
    # SAM carries its own @SQ header; no reference FASTA is involved.
    assert "-T" not in argv

    body = resp.json()
    assert body["success"] is True
    out = Path(body["bam_path"])
    assert argv[argv.index("-o") + 1] == str(out)
    assert out.read_bytes()[:2] == BAM_MAGIC


def test_sam_to_bam_reports_failure_when_samtools_fails(client, gatk_api, monkeypatch):
    monkeypatch.setattr(
        gatk_api.subprocess,
        "run",
        lambda cmd, *a, **k: subprocess.CompletedProcess(
            cmd, 1, stdout=b"", stderr=b"[E::sam_parse1] missing SAM header"
        ),
    )
    before = _bam_files(gatk_api)

    resp = client.post(
        "/sam-to-bam",
        files={"file": ("sample.sam", b"garbage\n", "text/plain")},
        data={"reference_genome": "hg38"},
    )

    assert resp.status_code == 500, resp.text
    assert resp.json().get("success") is not True
    assert "sam_parse1" in resp.json()["detail"]
    assert _bam_files(gatk_api) == before


def test_sam_to_bam_does_not_need_a_reference_genome(
    client, gatk_api, monkeypatch, samtools
):
    """A missing reference FASTA is fatal for CRAM but irrelevant for SAM."""
    monkeypatch.setitem(
        gatk_api.REFERENCE_PATHS, "hg38", str(Path(gatk_api.TEMP_DIR) / "absent.fasta")
    )
    resp = client.post(
        "/sam-to-bam",
        files={"file": ("sample.sam", SAM_BODY, "text/plain")},
        data={"reference_genome": "hg38"},
    )
    assert resp.status_code == 200, resp.text


# --------------------------------------------------------------------------
# BACKLOG 252 (gatk-api share): the shared-volume log must be bounded
# --------------------------------------------------------------------------


def test_progress_log_handler_is_rotating(gatk_api):
    """/data is a bind mount shared with the app; an unbounded log fills the host.

    Asserted against the handler list the module builds, not against
    `logging.root.handlers`: `basicConfig()` is a no-op once any handler is attached,
    which is exactly what has happened by the time pytest imports this module.
    """
    rotating = [
        h
        for h in gatk_api._log_handlers
        if isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    assert rotating, "no RotatingFileHandler built"

    progress = [h for h in rotating if h.baseFilename.endswith("gatk_progress.log")]
    assert progress, f"progress log not rotated: {[h.baseFilename for h in rotating]}"

    for handler in progress:
        assert 0 < handler.maxBytes <= 64 * 1024 * 1024
        assert handler.backupCount >= 1


def test_rotation_actually_caps_the_file(gatk_api, tmp_path, monkeypatch):
    """The bound has to hold in practice, not just as an attribute value."""
    monkeypatch.setattr(gatk_api, "LOG_MAX_BYTES", 512)
    monkeypatch.setattr(gatk_api, "LOG_BACKUP_COUNT", 1)
    target = tmp_path / "progress.log"

    handler = gatk_api._bounded_file_handler(str(target))
    probe = logging.getLogger("zaropgx-rotation-probe")
    probe.propagate = False
    probe.addHandler(handler)
    try:
        for _ in range(200):
            probe.error("x" * 100)
    finally:
        probe.removeHandler(handler)
        handler.close()

    assert target.stat().st_size <= 512 + 200, "live log outgrew maxBytes"
    assert (tmp_path / "progress.log.1").exists(), "no backup was rotated out"
    # backupCount=1 means exactly one backup is retained, never an unbounded chain.
    assert not (tmp_path / "progress.log.2").exists()


def test_plain_file_handlers_are_gone(source):
    assert "logging.FileHandler(" not in source


def test_unopenable_log_destination_is_skipped_not_fatal(gatk_api, tmp_path):
    """A read-only or absent log dir must not take the service down at import."""
    assert (
        gatk_api._bounded_file_handler(str(tmp_path / "no" / "such" / "dir.log"))
        is None
    )
    # And the proof that it does not: this module imported at all on a host with
    # no /var/log.
    assert gatk_api.app is not None
