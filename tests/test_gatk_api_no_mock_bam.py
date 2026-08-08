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
    """Record every subprocess argv and fake a successful samtools conversion.

    The input file is snapshotted at call time because the handler deletes its work
    directory once the conversion returns.
    """
    calls = []

    def fake_run(cmd, *args, **kwargs):
        argv = list(cmd) if isinstance(cmd, (list, tuple)) else str(cmd).split()
        record = {"cmd": cmd, "kwargs": kwargs, "input_bytes": None}
        if argv[:2] == ["samtools", "view"]:
            record["input_bytes"] = Path(argv[-1]).read_bytes()
        calls.append(record)
        if "-o" in argv:
            Path(argv[argv.index("-o") + 1]).write_bytes(MINIMAL_BAM)
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(gatk_api.subprocess, "run", fake_run)
    return calls


def _bam_files(gatk_api):
    """Every .bam anywhere the service can write -- scratch and shared volume both."""
    found = []
    for root in (gatk_api.TEMP_DIR, gatk_api.DATA_DIR):
        found.extend(Path(root).rglob("*.bam"))
    return sorted(found)


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


def test_module_never_writes_a_literal_payload_to_a_file(source):
    """No output may be conjured from a literal. Only samtools writes alignments.

    Renaming the placeholder string would slip past the assertion above. Matching on
    the *write* rather than on the variable name means `open(out_path, "wb")` cannot
    slip past either: the only way to fabricate a file is to write bytes at it, and
    every legitimate write in this module comes from an upload or from json.dumps.
    """
    offenders = re.findall(r"\.write\(\s*b[\"'][^\"']", source)
    assert offenders == [], f"literal bytes written to a file: {offenders}"

    handwritten = re.findall(
        r"open\(\s*(\w*bam\w*)\s*,\s*[\"']wb[\"']", source, re.IGNORECASE
    )
    assert handwritten == [], f"BAM opened for writing outside samtools: {handwritten}"


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


def test_align_fastq_leaves_no_bam_behind(client, gatk_api, reference_fasta):
    """Takes `reference_fasta` deliberately: the old handler checked the reference
    first and would 400 before fabricating, so without it this test would pass
    against the unfixed code purely on test-ordering luck."""
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
    client, gatk_api, reference_fasta, samtools, monkeypatch
):
    """The upload is streamed to disk in chunks; it must arrive intact.

    The chunk size is shrunk so the copy loop really iterates -- at the production
    8 MiB a test payload would fit in a single read and a non-chunked implementation
    would pass.
    """
    monkeypatch.setattr(gatk_api, "UPLOAD_CHUNK_BYTES", 4096)
    payload = b"CRAM\x03\x00" + bytes(range(256)) * 512
    assert len(payload) > 4096 * 8, "payload must span many chunks"

    client.post(
        "/cram-to-bam",
        files={"file": ("sample.cram", payload, "application/octet-stream")},
        data={"reference_genome": "hg38"},
    )

    call = [c for c in samtools if c["input_bytes"] is not None][0]
    assert call["input_bytes"] == payload


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
    scratch = Path(gatk_api.TEMP_DIR).resolve()
    results = (Path(gatk_api.DATA_DIR) / "results").resolve()
    assert scratch in Path(argv[-1]).resolve().parents
    assert results in Path(argv[argv.index("-o") + 1]).resolve().parents


def test_returned_bam_lives_on_the_shared_volume_not_container_private_tmp(
    client, gatk_api, reference_fasta, samtools
):
    """The caller resolves bam_path from another container.

    Nextflow processes run inside the nextflow container (`process.executor = 'local'`,
    no `container:` directive) and do `cp "$BAM_PATH" .`. The only filesystem both it
    and gatk-api mount is ./data. A path under TMPDIR=/tmp/gatk_temp is on this
    container's private layer, so returning one is success over an unreadable file.
    """
    resp = client.post(
        "/cram-to-bam",
        files={"file": ("sample.cram", b"CRAM\x03\x00", "application/octet-stream")},
        data={"reference_genome": "hg38"},
    )
    assert resp.status_code == 200, resp.text

    bam_path = Path(resp.json()["bam_path"]).resolve()
    data_dir = Path(gatk_api.DATA_DIR).resolve()
    assert data_dir in bam_path.parents, f"{bam_path} is not under DATA_DIR"
    assert Path(gatk_api.TEMP_DIR).resolve() not in bam_path.parents
    assert bam_path.exists()


def test_output_is_scoped_to_the_job_id_the_cleanup_service_reaps(
    client, gatk_api, reference_fasta, samtools
):
    """app/services/cleanup_service.py removes /data/results/{job_id}."""
    resp = client.post(
        "/cram-to-bam",
        files={"file": ("sample.cram", b"CRAM\x03\x00", "application/octet-stream")},
        data={"reference_genome": "hg38", "job_id": "job-4242"},
    )
    assert resp.status_code == 200, resp.text

    scope = (Path(gatk_api.DATA_DIR) / "results" / "job-4242").resolve()
    assert scope in Path(resp.json()["bam_path"]).resolve().parents


def test_job_id_cannot_traverse_out_of_the_results_tree(
    client, gatk_api, reference_fasta, samtools
):
    """job_id is a multipart form field, so it cannot be path-joined unchecked."""
    resp = client.post(
        "/cram-to-bam",
        files={"file": ("sample.cram", b"CRAM\x03\x00", "application/octet-stream")},
        data={"reference_genome": "hg38", "job_id": "../../../../etc/pwned"},
    )
    assert resp.status_code == 200, resp.text

    results = (Path(gatk_api.DATA_DIR) / "results").resolve()
    assert results in Path(resp.json()["bam_path"]).resolve().parents


def test_scratch_directory_is_not_left_behind(
    client, gatk_api, reference_fasta, samtools
):
    """A WGS input is full size; nothing used to delete it, on success or failure."""
    before = sorted(Path(gatk_api.TEMP_DIR).iterdir())

    client.post(
        "/cram-to-bam",
        files={"file": ("sample.cram", b"CRAM\x03\x00", "application/octet-stream")},
        data={"reference_genome": "hg38"},
    )
    assert sorted(Path(gatk_api.TEMP_DIR).iterdir()) == before

    client.post(
        "/sam-to-bam",
        files={"file": ("sample.sam", SAM_BODY, "text/plain")},
        data={"reference_genome": "hg38"},
    )
    assert sorted(Path(gatk_api.TEMP_DIR).iterdir()) == before


def test_failed_conversion_leaves_nothing_on_the_shared_volume(
    client, gatk_api, reference_fasta, monkeypatch
):
    results = Path(gatk_api.DATA_DIR) / "results"
    results.mkdir(parents=True, exist_ok=True)
    before = sorted(p.name for p in results.iterdir())

    monkeypatch.setattr(
        gatk_api.subprocess,
        "run",
        lambda cmd, *a, **k: subprocess.CompletedProcess(cmd, 1, b"", b"boom"),
    )
    resp = client.post(
        "/cram-to-bam",
        files={"file": ("sample.cram", b"CRAM\x03\x00", "application/octet-stream")},
        data={"reference_genome": "hg38"},
    )

    assert resp.status_code == 500
    assert sorted(p.name for p in results.iterdir()) == before
    assert sorted(Path(gatk_api.TEMP_DIR).iterdir()) == []


def test_output_is_verified_with_samtools_quickcheck(
    client, gatk_api, reference_fasta, samtools
):
    """Magic bytes only prove BGZF framing; a bgzipped VCF would pass that."""
    client.post(
        "/cram-to-bam",
        files={"file": ("sample.cram", b"CRAM\x03\x00", "application/octet-stream")},
        data={"reference_genome": "hg38"},
    )

    quickchecks = [
        list(c["cmd"])
        for c in samtools
        if isinstance(c["cmd"], (list, tuple)) and "quickcheck" in list(c["cmd"])
    ]
    assert len(quickchecks) == 1, f"expected one quickcheck, got {quickchecks}"
    assert quickchecks[0][:2] == ["samtools", "quickcheck"]


def test_quickcheck_rejection_is_not_reported_as_success(
    client, gatk_api, reference_fasta, monkeypatch
):
    """A truncated BAM has valid magic bytes and a missing EOF block."""

    def fake_run(cmd, *args, **kwargs):
        argv = list(cmd)
        if "quickcheck" in argv:
            return subprocess.CompletedProcess(cmd, 1, b"", b"EOF marker absent")
        Path(argv[argv.index("-o") + 1]).write_bytes(MINIMAL_BAM)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(gatk_api.subprocess, "run", fake_run)
    before = _bam_files(gatk_api)

    resp = client.post(
        "/cram-to-bam",
        files={"file": ("sample.cram", b"CRAM\x03\x00", "application/octet-stream")},
        data={"reference_genome": "hg38"},
    )

    assert resp.status_code == 500, resp.text
    assert resp.json().get("success") is not True
    assert "quickcheck" in resp.json()["detail"]
    assert _bam_files(gatk_api) == before


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
    # 200 alone would also hold for the placeholder implementation; the conversion
    # having actually run is what makes this a test of the fix rather than of a
    # hypothetical over-correction.
    assert _conversion_argv(samtools)[:3] == ["samtools", "view", "-b"]


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
    """A read-only or absent log dir must not take the service down at import.

    That it does not is already proven by the `gatk_api` fixture importing at all on
    a host with no /var/log -- a raise there would error every test in this module.
    """
    assert (
        gatk_api._bounded_file_handler(str(tmp_path / "no" / "such" / "dir.log"))
        is None
    )
