"""Chunked upload I/O and the gatk-api to_thread concurrency cap (queue task 3;
extended by queue task 9 for docker/pharmcat/pharmcat.py).

Four upload save sites across three sidecars used to buffer the whole upload
into memory with `content = await file.read(); f.write(content)` (or the
equivalent one-liner), then write it out in a single synchronous call:

  * docker/pypgx/pypgx_wrapper.py, both `/create-input-vcf` and `/genotype`.
  * docker/zarohla/app.py's `/call-hla`, all three save sites (the paired
    file1/file2 upload and the single-file upload).
  * docker/gatk-api/gatk_api.py's `/variant-call` -- this one was missed in
    the first pass: `/cram-to-bam` and `/sam-to-bam` in the same module
    already streamed, and "gatk_api.py already shows the chunked shape" was
    true only of those two, not of `/variant-call`'s own save site.

All now stream in UPLOAD_CHUNK_BYTES-sized chunks. This module pins the
regression shut with a source fence (the old whole-file pattern must not
reappear, in any of the four) and proves the new shape is real by driving
each save site through its actual FastAPI route with
`starlette.datastructures.UploadFile.read` spied on, so the assertion is
about what the running handler actually calls, not a transcription of it.

It also covers gatk_api.py's TO_THREAD_CONCURRENCY_LIMIT semaphore: the two
asyncio.to_thread() call sites that drive convert_to_indexed_bam() (a
`samtools sort`, ~2.3 GiB per worker at the module's own defaults) used to be
bounded only by the default executor's own cap of up to 32 concurrent workers,
which a 28 G container sharing a 20 g Java heap cannot survive. A semaphore
now gates both call sites; this file proves it actually bounds concurrency and
that the source wraps the right calls with it.

A fifth save site, found by the rescan that produced queue task 9:
docker/pharmcat/pharmcat.py's `/genotype` had the identical whole-file-buffer
shape, AND -- unlike the four above -- built its save path straight from the
unsanitised `file.filename`: `os.path.join(TEMP_DIR, file.filename)`, a
path-write primitive (`../../evil.vcf` walks out of TEMP_DIR) on top of the
memory issue. Both are fixed together below: chunked reads via the same
UPLOAD_CHUNK_BYTES idiom, and a `safe_upload_name()` uuid4-hex rename modelled
on pypgx_wrapper.py's / zarohla/app.py's own. pharmcat.py's copy does not need
either of those two's unrecognised-extension fallback: this route's own
extension guard, a few lines above the save site (load-bearing for Task 1's
BCF-refusal rationale), has already restricted file.filename to
.vcf/.vcf.gz/.vcf.bgz by the time safe_upload_name() ever runs.

A sixth site, found in review one call after the fifth: `/genotype`'s
optional `outside_tsv` upload had the same whole-file-buffer read, but its
destination path (`outside_path`, built from `base_name`) was never
filename-derived and so was never unsafe -- only the read needed to change,
to the same UPLOAD_CHUNK_BYTES idiom, with no renaming involved.

Each sidecar is imported the way tests/test_gatk_api_no_mock_bam.py already
imports gatk_api.py: out of its source file with `psutil` and `job_client`
(the /job-client stub every sidecar Dockerfile copies in) faked in sys.modules,
and DATA_DIR/TMPDIR/REFERENCE_DIR repointed at a temp tree, because none of
these four modules are importable as-is outside their own container image.
pharmcat.py additionally does `from pharmcat_assume_ref import ...` inside the
route handler itself; in the real image that resolves via the PYTHONPATH
entry docker/pharmcat/Dockerfile adds for /assume-ref-lib (a copy of
app/utils/pharmcat_assume_ref.py). Here it resolves the same bare module name
off app/utils directly, prepended to sys.path for the fixture's lifetime.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import logging
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile as StarletteUploadFile

REPO_ROOT = Path(__file__).resolve().parent.parent
GATK_API_SOURCE = REPO_ROOT / "docker" / "gatk-api" / "gatk_api.py"
PYPGX_SOURCE = REPO_ROOT / "docker" / "pypgx" / "pypgx_wrapper.py"
ZAROHLA_SOURCE = REPO_ROOT / "docker" / "zarohla" / "app.py"
PHARMCAT_SOURCE = REPO_ROOT / "docker" / "pharmcat" / "pharmcat.py"
# Where pharmcat.py's own `from pharmcat_assume_ref import ...` resolves from
# outside the image -- see the module docstring above.
PHARMCAT_ASSUME_REF_LIB = REPO_ROOT / "app" / "utils"


def _fake_psutil():
    """Enough of psutil to satisfy import-time and the routes exercised here."""
    module = types.ModuleType("psutil")
    module.virtual_memory = lambda: types.SimpleNamespace(
        total=16 * 1024**3, available=8 * 1024**3, used=8 * 1024**3, percent=50.0
    )
    module.Process = lambda *a, **k: types.SimpleNamespace()
    module.pid_exists = lambda pid: False
    module.NoSuchProcess = type("NoSuchProcess", (Exception,), {})
    module.AccessDenied = type("AccessDenied", (Exception,), {})
    module.TimeoutExpired = type("TimeoutExpired", (Exception,), {})
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


def _import_sidecar(spec_name, source_path, mp, root):
    """Exec a sidecar module out of its source, isolated under `root`."""
    mp.setenv("DATA_DIR", str(root / "data"))
    mp.setenv("TMPDIR", str(root / "tmp"))
    mp.setenv("REFERENCE_DIR", str(root / "reference"))
    mp.setenv("PYPGX_PROGRESS_LOG", str(root / "pypgx_progress.log"))
    mp.setenv("ZAROHLA_PROGRESS_LOG", str(root / "zarohla_progress.log"))
    mp.setitem(sys.modules, "psutil", _fake_psutil())
    mp.setitem(sys.modules, "job_client", _fake_job_client())

    spec = importlib.util.spec_from_file_location(spec_name, source_path)
    module = importlib.util.module_from_spec(spec)
    mp.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def _close_module_log_handlers(module, before_handlers):
    for handler in list(logging.root.handlers):
        if handler not in before_handlers:
            logging.root.removeHandler(handler)
    for handler in getattr(module, "_log_handlers", []):
        handler.close()


@pytest.fixture(scope="module")
def gatk_api(tmp_path_factory):
    root = tmp_path_factory.mktemp("gatk_api_home")
    before_handlers = list(logging.root.handlers)
    with pytest.MonkeyPatch.context() as mp:
        module = _import_sidecar(
            "zaropgx_gatk_api_chunk_io_test", GATK_API_SOURCE, mp, root
        )
        yield module
    _close_module_log_handlers(module, before_handlers)


@pytest.fixture(scope="module")
def pypgx_api(tmp_path_factory):
    root = tmp_path_factory.mktemp("pypgx_home")
    before_handlers = list(logging.root.handlers)
    with pytest.MonkeyPatch.context() as mp:
        module = _import_sidecar(
            "zaropgx_pypgx_wrapper_chunk_io_test", PYPGX_SOURCE, mp, root
        )
        yield module
    _close_module_log_handlers(module, before_handlers)


@pytest.fixture(scope="module")
def zarohla_api(tmp_path_factory):
    root = tmp_path_factory.mktemp("zarohla_home")
    before_handlers = list(logging.root.handlers)
    with pytest.MonkeyPatch.context() as mp:
        module = _import_sidecar(
            "zaropgx_zarohla_chunk_io_test", ZAROHLA_SOURCE, mp, root
        )
        yield module
    _close_module_log_handlers(module, before_handlers)


@pytest.fixture(scope="module")
def pharmcat_api(tmp_path_factory):
    root = tmp_path_factory.mktemp("pharmcat_home")
    before_handlers = list(logging.root.handlers)
    with pytest.MonkeyPatch.context() as mp:
        # pharmcat.py-specific env vars, kept out of _import_sidecar() (which
        # the other three fixtures also use) rather than added there: unlike
        # TEMP_DIR/PHARMCAT_PROGRESS_LOG, REPORT_DIR is also read by
        # docker/pypgx/pypgx_wrapper.py at module level, so setting it in the
        # shared helper would have redirected pypgx's REPORT_DIR too --
        # harmless in practice (better sandboxing, if anything), but not
        # something a shared helper should do silently for a module that did
        # not ask for it.
        mp.setenv("TEMP_DIR", str(root / "pharmcat_temp"))
        mp.setenv("REPORT_DIR", str(root / "reports"))
        mp.setenv("PHARMCAT_PROGRESS_LOG", str(root / "pharmcat_progress.log"))
        # Makes `from pharmcat_assume_ref import ...`, called inside the
        # /genotype handler, resolve the same way docker/pharmcat/Dockerfile's
        # PYTHONPATH entry for /assume-ref-lib does in the real image.
        mp.syspath_prepend(str(PHARMCAT_ASSUME_REF_LIB))
        module = _import_sidecar(
            "zaropgx_pharmcat_chunk_io_test", PHARMCAT_SOURCE, mp, root
        )
        yield module
    _close_module_log_handlers(module, before_handlers)


def _spy_on_upload_reads(monkeypatch):
    """Patch starlette.datastructures.UploadFile.read and return the recorded sizes.

    Every sidecar route below declares its upload parameter as `fastapi.UploadFile`,
    but FastAPI's own multipart form parsing constructs plain
    `starlette.datastructures.UploadFile` instances at request time -- the FastAPI
    subclass exists for typing and is never what actually reaches the handler, as
    confirmed by printing `type(file)` inside a running route. The patch has to
    land on the Starlette class for the recorded sizes to reflect what the route
    actually calls.
    """
    read_sizes: list[int] = []
    original_read = StarletteUploadFile.read

    async def spy_read(self, size=-1):
        read_sizes.append(size)
        return await original_read(self, size)

    monkeypatch.setattr(StarletteUploadFile, "read", spy_read)
    return read_sizes


# ---------------------------------------------------------------------------
# Source fence: the old whole-file-in-memory shape must not come back
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def gatk_api_source():
    return GATK_API_SOURCE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def pypgx_source():
    return PYPGX_SOURCE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def zarohla_source():
    return ZAROHLA_SOURCE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def pharmcat_source():
    return PHARMCAT_SOURCE.read_text(encoding="utf-8")


def test_gatk_api_upload_sites_no_longer_buffer_whole_file(gatk_api_source):
    """/variant-call used the old whole-file shape after /cram-to-bam and
    /sam-to-bam had already been converted to the chunked one -- the brief's
    "gatk_api.py already shows the chunked shape" was true only of the latter
    two. All three of this module's own upload save sites must now match.
    """
    assert "content = await file.read()" not in gatk_api_source, (
        "gatk_api.py reads an entire upload into memory with a bare "
        "await file.read(); every save site must stream via UPLOAD_CHUNK_BYTES"
    )
    # /variant-call, /cram-to-bam, /sam-to-bam: one chunked read loop each.
    assert gatk_api_source.count("await file.read(UPLOAD_CHUNK_BYTES)") == 3


def test_pypgx_upload_sites_no_longer_buffer_whole_file(pypgx_source):
    # The exact old bug shape: the whole upload into one `bytes`, then a
    # separate synchronous write. (A bare, argument-less `await file.read()`
    # is also named in this module's own explanatory comment, so checking for
    # that substring alone would false-positive on the comment.)
    assert "content = await file.read()" not in pypgx_source, (
        "pypgx_wrapper.py reads an entire upload into memory with a bare "
        "await file.read(); both save sites must stream via UPLOAD_CHUNK_BYTES"
    )
    # /create-input-vcf and /genotype: exactly one chunked read loop each.
    assert pypgx_source.count("await file.read(UPLOAD_CHUNK_BYTES)") == 2


def test_zarohla_upload_sites_no_longer_buffer_whole_file(zarohla_source):
    for bad in (
        "f.write(await file.read())",
        "f.write(await file1.read())",
        "f.write(await file2.read())",
    ):
        assert bad not in zarohla_source, (
            f"zarohla/app.py still has the old one-line save shape {bad!r}; "
            "it must stream via UPLOAD_CHUNK_BYTES"
        )
    # The paired upload (file1, file2) and the single-file upload: one chunked
    # read loop apiece.
    assert zarohla_source.count("await file.read(UPLOAD_CHUNK_BYTES)") == 1
    assert zarohla_source.count("await file1.read(UPLOAD_CHUNK_BYTES)") == 1
    assert zarohla_source.count("await file2.read(UPLOAD_CHUNK_BYTES)") == 1


def test_pharmcat_upload_sites_no_longer_buffer_whole_file(pharmcat_source):
    """/genotype has two upload save sites -- the VCF `file` (queue task 9's
    original finding, and the only one whose path was also built off the raw
    filename) and the optional `outside_tsv` (found in review one call after
    the first: same whole-file-buffer shape, but its path was already
    base_name-derived and safe, so only its read needed to change).
    """
    for bad in (
        "content = await file.read()",
        "content = await outside_tsv.read()",
    ):
        assert bad not in pharmcat_source, (
            f"pharmcat.py still has the old whole-file-buffer shape {bad!r}; "
            "it must stream via UPLOAD_CHUNK_BYTES"
        )
    # One chunked read loop apiece -- checked explicitly per site rather than
    # a >=1 total, so a regression at either site is caught by name.
    assert pharmcat_source.count("await file.read(UPLOAD_CHUNK_BYTES)") == 1
    assert pharmcat_source.count("await outside_tsv.read(UPLOAD_CHUNK_BYTES)") == 1


def test_pharmcat_upload_site_no_longer_builds_path_from_raw_filename(pharmcat_source):
    """The other half of the same finding: the destination path must no
    longer be `os.path.join(TEMP_DIR, file.filename)` -- a hostile filename
    (`../../evil.vcf`, an embedded path separator) could write outside
    TEMP_DIR or collide with a concurrent upload. safe_upload_name() must be
    the only thing that decides the on-disk name.
    """
    assert "os.path.join(TEMP_DIR, file.filename)" not in pharmcat_source, (
        "pharmcat.py still joins the raw client filename onto TEMP_DIR; "
        "the save path must come from safe_upload_name() instead"
    )
    assert "def safe_upload_name(" in pharmcat_source
    assert "os.path.join(TEMP_DIR, safe_upload_name(file.filename))" in pharmcat_source


# ---------------------------------------------------------------------------
# Behavioural: the chunked writes actually work, driven through the real routes
# ---------------------------------------------------------------------------


def test_pypgx_create_input_vcf_streams_upload_in_chunks(pypgx_api, monkeypatch):
    # A tiny chunk size makes a modest payload span many read() calls without
    # a slow multi-megabyte test fixture.
    monkeypatch.setattr(pypgx_api, "UPLOAD_CHUNK_BYTES", 4)
    # The actual PyPGx CLI is not installed in this environment; only the save
    # step is under test here.
    monkeypatch.setattr(
        pypgx_api, "run_pypgx_create_input_vcf", lambda *a, **k: {"success": True}
    )
    read_sizes = _spy_on_upload_reads(monkeypatch)

    payload = bytes(range(37))  # not a multiple of the 4-byte chunk size
    client = TestClient(pypgx_api.app)
    response = client.post(
        "/create-input-vcf",
        files={"file": ("reads.bam", payload, "application/octet-stream")},
    )

    assert response.status_code == 200, response.text
    written = Path(response.json()["input_file"]).read_bytes()
    assert written == payload, "the saved file does not match what was uploaded"

    assert read_sizes, "file.read() was never called"
    assert set(read_sizes) == {
        4
    }, f"expected every read() call to request the configured chunk size, got {read_sizes}"
    assert len(read_sizes) > 1, "the whole upload was still read in a single call"


def test_pypgx_genotype_streams_upload_in_chunks(pypgx_api, monkeypatch):
    monkeypatch.setattr(pypgx_api, "UPLOAD_CHUNK_BYTES", 4)
    # /genotype never returns its saved path in the response body (unlike
    # /create-input-vcf), and nothing here rmtree's job_dir -- so the path is
    # made predictable instead, by pinning the two uuid4() calls that build it
    # (local_job_id, then safe_upload_name()'s own stem) to one fixed value.
    fixed_uuid = pypgx_api.uuid.uuid4()
    monkeypatch.setattr(pypgx_api.uuid, "uuid4", lambda: fixed_uuid)
    read_sizes = _spy_on_upload_reads(monkeypatch)

    payload = bytes(range(29))
    client = TestClient(pypgx_api.app)
    response = client.post(
        "/genotype",
        data={"genes": "CYP2D6"},
        files={"file": ("sample.vcf", payload, "application/octet-stream")},
    )

    # PyPGx itself is not installed, so genotyping fails after the save --
    # that failure is not what this test is about.
    assert response.status_code in (200, 500), response.text

    expected_path = pypgx_api.TEMP_DIR / str(fixed_uuid) / f"{fixed_uuid.hex}.vcf"
    assert (
        expected_path.read_bytes() == payload
    ), "the saved file does not match what was uploaded"

    assert read_sizes, "file.read() was never called"
    assert set(read_sizes) == {4}
    assert len(read_sizes) > 1, "the whole upload was still read in a single call"


def test_gatk_api_variant_call_streams_upload_in_chunks(gatk_api, monkeypatch):
    # /variant-call checks the reference FASTA exists on disk before it looks
    # at the file type at all -- materialise a minimal one so the request can
    # reach a clean 200 rather than failing on an unrelated missing file.
    reference_path = Path(gatk_api.REFERENCE_PATHS["hg38"])
    reference_path.parent.mkdir(parents=True, exist_ok=True)
    reference_path.write_text(">chr1\nACGTACGTACGT\n", encoding="utf-8")

    monkeypatch.setattr(gatk_api, "UPLOAD_CHUNK_BYTES", 4)
    read_sizes = _spy_on_upload_reads(monkeypatch)

    # A VCF with no `##reference=` line, so detect_reference() has nothing to
    # override the requested (default) hg38 with -- the upload is already a
    # VCF, so /variant-call returns it directly instead of calling out to GATK.
    payload = b"##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\n"
    client = TestClient(gatk_api.app)
    response = client.post(
        "/variant-call",
        files={"file": ("sample.vcf", payload, "application/octet-stream")},
    )

    assert response.status_code == 200, response.text
    output_file = response.json()["output_file"]
    assert output_file, "no output_file in the /variant-call response"
    assert (
        Path(output_file).read_bytes() == payload
    ), "the saved file does not match what was uploaded"

    assert read_sizes, "file.read() was never called"
    assert set(read_sizes) == {
        4
    }, f"expected every read() call to request the configured chunk size, got {read_sizes}"
    assert len(read_sizes) > 1, "the whole upload was still read in a single call"


def test_zarohla_call_hla_streams_single_file_upload_in_chunks(
    zarohla_api, monkeypatch
):
    monkeypatch.setattr(zarohla_api, "UPLOAD_CHUNK_BYTES", 4)
    read_sizes = _spy_on_upload_reads(monkeypatch)

    captured: dict = {}

    class _FakeProcess:
        pid = 4321
        returncode = 1  # a clean, deliberate "OptiType failed"

        async def communicate(self):
            return b"", b"stub optitype failure"

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        if "-i" in cmd:
            input_arg = Path(cmd[cmd.index("-i") + 1])
            # Read while the file still exists -- the route rmtree's job_dir
            # in a `finally` once this call returns.
            captured["content"] = input_arg.read_bytes()
        captured["cmd"] = cmd
        return _FakeProcess()

    monkeypatch.setattr(
        zarohla_api.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )

    payload = bytes(range(53))
    client = TestClient(zarohla_api.app)
    response = client.post(
        "/call-hla",
        files={"file": ("reads.fastq", payload, "application/octet-stream")},
    )

    assert response.status_code == 500, response.text  # the stubbed OptiType "failed"
    assert captured.get("cmd", [""])[0] == "optitype", "OptiType was never invoked"
    assert (
        captured.get("content") == payload
    ), "the file OptiType was pointed at does not match what was uploaded"

    assert read_sizes, "file.read() was never called"
    assert set(read_sizes) == {4}
    assert len(read_sizes) > 1, "the whole upload was still read in a single call"


def test_pharmcat_genotype_streams_upload_in_chunks(pharmcat_api, monkeypatch):
    monkeypatch.setattr(pharmcat_api, "UPLOAD_CHUNK_BYTES", 4)
    # The saved path is never in the response body -- pin the one uuid4() call
    # safe_upload_name() makes (patient_id below means base_name's own
    # fallback uuid4() branch, line ~414, never runs) to make it predictable.
    fixed_uuid = pharmcat_api.uuid.uuid4()
    monkeypatch.setattr(pharmcat_api.uuid, "uuid4", lambda: fixed_uuid)
    read_sizes = _spy_on_upload_reads(monkeypatch)

    payload = bytes(range(29))  # not a multiple of the 4-byte chunk size
    client = TestClient(pharmcat_api.app)
    response = client.post(
        "/genotype",
        data={"patient_id": "patient123"},
        files={"file": ("sample.vcf", payload, "application/octet-stream")},
    )

    # The real pharmcat_pipeline binary is not installed in this environment,
    # so processing fails after the save -- that failure is not what this
    # test is about, only the save that happens before it.
    assert response.status_code in (200, 500), response.text

    expected_path = Path(pharmcat_api.TEMP_DIR) / f"{fixed_uuid.hex}.vcf"
    assert (
        expected_path.read_bytes() == payload
    ), "the saved file does not match what was uploaded"

    assert read_sizes, "file.read() was never called"
    assert set(read_sizes) == {4}
    assert len(read_sizes) > 1, "the whole upload was still read in a single call"


def test_pharmcat_genotype_streams_outside_tsv_upload_in_chunks(
    pharmcat_api, monkeypatch
):
    """The second finding from review: outside_tsv's save had the same
    whole-file-buffer read (its path was already base_name-derived and safe,
    so only the read needed to change).
    """
    monkeypatch.setattr(pharmcat_api, "UPLOAD_CHUNK_BYTES", 4)
    read_sizes = _spy_on_upload_reads(monkeypatch)

    captured: dict = {}

    def fake_translate(outside_path):
        # Read while the file still exists -- the enclosing
        # tempfile.TemporaryDirectory() that holds it is torn down (its
        # `with` block exits) before the response reaches this test, the
        # same constraint zarohla's job_dir rmtree imposes on its own test.
        captured["content"] = Path(outside_path).read_bytes()

    monkeypatch.setattr(pharmcat_api, "_translate_uploaded_outside_tsv", fake_translate)

    vcf_payload = b"##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\n"
    outside_payload = b"Gene\tDiplotype\nCYP2D6\t*1/*1\n"  # not a multiple of 4
    client = TestClient(pharmcat_api.app)
    response = client.post(
        "/genotype",
        data={"patient_id": "patient789"},
        files={
            "file": ("sample.vcf", vcf_payload, "application/octet-stream"),
            "outside_tsv": (
                "outside.tsv",
                outside_payload,
                "text/tab-separated-values",
            ),
        },
    )

    assert response.status_code in (200, 500), response.text
    assert (
        captured.get("content") == outside_payload
    ), "the outside_tsv save does not match what was uploaded"

    # Both uploads share the same UPLOAD_CHUNK_BYTES=4: if either had reverted
    # to a single unchunked read(), that call's default size=-1 would show up
    # here and break the {4}-only set.
    assert read_sizes, "file.read() was never called"
    assert set(read_sizes) == {4}
    assert len(read_sizes) > 1, "at least one upload was still read in a single call"


def test_pharmcat_genotype_sanitises_hostile_filename(pharmcat_api, monkeypatch):
    """A client-controlled filename must never reach the filesystem, in any
    form -- the vulnerability the source-fence test above pins shut.
    """
    fixed_uuid = pharmcat_api.uuid.uuid4()
    monkeypatch.setattr(pharmcat_api.uuid, "uuid4", lambda: fixed_uuid)

    payload = b"##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\n"
    # Passes process_genotype()'s own .vcf extension guard but carries a
    # traversal segment and shell metacharacters -- the same payload shape
    # tests/test_command_injection_hardening.py uses for pypgx/zarohla.
    hostile_name = "../../evil;touch pwned.vcf"
    client = TestClient(pharmcat_api.app)
    response = client.post(
        "/genotype",
        data={"patient_id": "patient456"},
        files={"file": (hostile_name, payload, "application/octet-stream")},
    )

    assert response.status_code in (200, 500), response.text

    temp_dir = Path(pharmcat_api.TEMP_DIR)
    on_disk = {p.name for p in temp_dir.iterdir()}
    assert not any(
        "evil" in name or "pwned" in name or ".." in name for name in on_disk
    ), f"the hostile filename leaked into an on-disk name: {on_disk}"

    expected_path = temp_dir / f"{fixed_uuid.hex}.vcf"
    assert (
        expected_path.read_bytes() == payload
    ), "the sanitised path was not written with the uploaded content"


# ---------------------------------------------------------------------------
# gatk-api: TO_THREAD_CONCURRENCY_LIMIT
# ---------------------------------------------------------------------------


def test_to_thread_semaphore_default_and_shape(gatk_api):
    assert gatk_api.TO_THREAD_CONCURRENCY_LIMIT == 2
    assert isinstance(gatk_api._to_thread_semaphore, asyncio.Semaphore)


def test_to_thread_concurrency_limit_is_env_overridable(tmp_path_factory):
    root = tmp_path_factory.mktemp("gatk_api_home_override")
    before_handlers = list(logging.root.handlers)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("TO_THREAD_CONCURRENCY_LIMIT", "5")
        module = _import_sidecar(
            "zaropgx_gatk_api_chunk_io_override_test", GATK_API_SOURCE, mp, root
        )
        try:
            assert module.TO_THREAD_CONCURRENCY_LIMIT == 5
            # asyncio.Semaphore has no public way to read its starting count;
            # this is the same private attribute the stdlib itself relies on
            # to report the value, and it is set once at construction.
            assert module._to_thread_semaphore._value == 5
        finally:
            _close_module_log_handlers(module, before_handlers)


def test_to_thread_semaphore_caps_concurrency(gatk_api):
    """The semaphore actually serialises access, not merely exists."""
    limit = gatk_api.TO_THREAD_CONCURRENCY_LIMIT
    current = 0
    max_seen = 0

    async def worker():
        nonlocal current, max_seen
        async with gatk_api._to_thread_semaphore:
            current += 1
            max_seen = max(max_seen, current)
            await asyncio.sleep(0.02)
            current -= 1

    async def run_all():
        await asyncio.gather(*(worker() for _ in range(limit * 3)))

    asyncio.run(run_all())
    assert (
        max_seen == limit
    ), f"expected concurrency to saturate at exactly {limit}, saw {max_seen}"


def test_to_thread_semaphore_wraps_the_heavy_call_sites():
    """cram_to_bam and sam_to_bam's to_thread(convert_to_indexed_bam, ...) calls
    must each sit inside `async with _to_thread_semaphore:` -- a semaphore that
    exists but does not wrap the call site it was added for caps nothing.
    """
    tree = ast.parse(
        GATK_API_SOURCE.read_text(encoding="utf-8"), filename=str(GATK_API_SOURCE)
    )

    guarded_to_thread_calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncWith):
            continue
        guarded = any(
            isinstance(item.context_expr, ast.Name)
            and item.context_expr.id == "_to_thread_semaphore"
            for item in node.items
        )
        if not guarded:
            continue
        for call in ast.walk(node):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "to_thread"
            ):
                guarded_to_thread_calls.append(call)

    assert len(guarded_to_thread_calls) == 2, (
        f"expected 2 semaphore-guarded asyncio.to_thread() calls "
        f"(cram_to_bam, sam_to_bam), found {len(guarded_to_thread_calls)}"
    )
    for call in guarded_to_thread_calls:
        first_arg = call.args[0]
        assert (
            isinstance(first_arg, ast.Name) and first_arg.id == "convert_to_indexed_bam"
        ), "a semaphore-guarded to_thread() call does not drive convert_to_indexed_bam"
