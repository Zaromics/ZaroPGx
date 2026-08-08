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

import ast
import importlib.util
import logging
import logging.handlers
import os
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


COORDINATE_HEADER = b"@HD\tVN:1.6\tSO:coordinate\n@SQ\tSN:chr1\tLN:12\n"
UNSORTED_HEADER = b"@HD\tVN:1.6\tSO:unsorted\n@SQ\tSN:chr1\tLN:12\n"
QUERYNAME_HEADER = b"@HD\tVN:1.6\tSO:queryname\n@SQ\tSN:chr1\tLN:12\n"
HEADER_WITHOUT_SO = b"@HD\tVN:1.6\n@SQ\tSN:chr1\tLN:12\n"


class FakeSamtools:
    """Enough of samtools to drive the handlers, recording every argv.

    Defaults to reporting a coordinate-sorted input, which is the common case;
    tests that care set `.header` before posting.
    """

    def __init__(self):
        self.header = COORDINATE_HEADER
        self.calls = []
        # subcommand -> (returncode, stderr). Note "view" covers the header probe too.
        self.fail = {}
        # idxstats rows: (name, length, mapped, unmapped)
        self.idxstats = [("chr1", 12, 100, 3), ("*", 0, 0, 7)]
        # stderr returned by the conversion even when it exits 0
        self.conversion_stderr = b""

    def argvs(self):
        return [
            list(c["cmd"]) for c in self.calls if isinstance(c["cmd"], (list, tuple))
        ]

    def ran(self, *prefix):
        """Every recorded argv starting with `prefix`."""
        want = list(prefix)
        return [argv for argv in self.argvs() if argv[: len(want)] == want]

    def run(self, cmd, *args, **kwargs):
        argv = list(cmd) if isinstance(cmd, (list, tuple)) else str(cmd).split()
        record = {"cmd": cmd, "kwargs": kwargs, "input_bytes": None}
        self.calls.append(record)

        def done(returncode, stdout=b"", stderr=b""):
            # detect_reference() passes text=True; the conversion helpers do not.
            # Getting this wrong makes the caller's `str in bytes` blow up and the
            # detection silently return nothing, which is how a real mismatch would
            # slip through unnoticed.
            if kwargs.get("text"):
                stdout = stdout.decode("utf-8") if isinstance(stdout, bytes) else stdout
                stderr = stderr.decode("utf-8") if isinstance(stderr, bytes) else stderr
            return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)

        subcommand = argv[1] if len(argv) > 1 else ""
        if subcommand in self.fail:
            code, stderr = self.fail[subcommand]
            return done(code, stderr=stderr)

        # `samtools view -H` -- header read, no output file
        if argv[:3] == ["samtools", "view", "-H"]:
            return done(0, stdout=self.header)

        # `samtools index <bam>` -- writes the .bai beside it.
        # Only ever for an absolute path: when a mutation run reverts a sink to a
        # shell string, str(cmd).split() yields a relative fragment and this would
        # otherwise drop a junk file in the repo root.
        if argv[:2] == ["samtools", "index"]:
            if os.path.isabs(argv[-1]):
                Path(f"{argv[-1]}.bai").write_bytes(b"BAI\x01")
            return done(0)

        # `samtools idxstats <bam>` -- record counts, read from the index
        if argv[:2] == ["samtools", "idxstats"]:
            rows = "".join(
                f"{name}\t{length}\t{mapped}\t{unmapped}\n"
                for name, length, mapped, unmapped in self.idxstats
            )
            return done(0, stdout=rows.encode("utf-8"))

        # A conversion: snapshot the input now, the work dir is deleted afterwards.
        if argv[:2] in (["samtools", "view"], ["samtools", "sort"]):
            record["input_bytes"] = Path(argv[-1]).read_bytes()

        if "-o" in argv:
            Path(argv[argv.index("-o") + 1]).write_bytes(MINIMAL_BAM)
            return done(0, stderr=self.conversion_stderr)
        return done(0)


@pytest.fixture()
def samtools(gatk_api, monkeypatch):
    fake = FakeSamtools()
    monkeypatch.setattr(gatk_api.subprocess, "run", fake.run)
    return fake


def _bam_files(gatk_api):
    """Every .bam anywhere the service can write -- scratch and shared volume both."""
    found = []
    for root in (gatk_api.TEMP_DIR, gatk_api.DATA_DIR):
        found.extend(Path(root).rglob("*.bam"))
    return sorted(found)


def _conversion_argv(samtools):
    """The one recorded call that converted the input -- `view -b` or `sort`.

    Excludes the `view -H` header probe, which is a read, not a conversion.
    """
    matches = [
        argv
        for argv in samtools.argvs()
        if argv[:2] == ["samtools", "sort"]
        or (argv[:2] == ["samtools", "view"] and "-H" not in argv)
    ]
    assert len(matches) == 1, f"expected one conversion call, got {matches}"
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


def test_align_fastq_docstring_does_not_claim_ingest_blocks_fastq(gatk_api):
    """The `unsupported` flag at ingest is advisory, and the docstring must say so.

    app/api/utils/file_processor.py sets workflow["unsupported"] = True for FASTQ, but
    app/api/routes/upload_router.py only copies that into the response and the job
    metadata -- no guard skips the Nextflow submission. FASTQ uploads really do reach
    this route, so a docstring claiming otherwise would be a comfortable falsehood.
    """
    doc = (gatk_api.align_fastq.__doc__ or "").lower()
    assert "501" in doc
    assert "advisory" in doc
    assert "do reach this route" in doc
    for false_claim in (
        "no supported upload path reaches this route",
        "already rejected as unsupported at ingest",
    ):
        assert false_claim not in doc


def test_align_fastq_records_the_reason_on_the_job(gatk_api, client, monkeypatch):
    """A failed job with no recorded reason is a support ticket."""
    recorded = {}

    class RecordingClient:
        def __init__(self, job_id=None, step_name=None):
            recorded["job_id"] = job_id
            recorded["step_name"] = step_name

        async def start_step(self, message=None):
            recorded["started"] = message

        async def fail_step(self, message, details=None):
            recorded["failed"] = message

    monkeypatch.setattr(gatk_api, "JobClient", RecordingClient)

    resp = client.post(
        "/align-fastq",
        files={"file": ("sample.fastq", b"@r1\nACGT\n+\nIIII\n", "text/plain")},
        data={"reference_genome": "hg38", "job_id": "job-77"},
    )

    assert resp.status_code == 501
    assert recorded["job_id"] == "job-77"
    assert "started" in recorded
    assert "not implemented" in recorded["failed"].lower()


def test_align_fastq_still_501s_when_the_job_server_is_down(
    gatk_api, client, monkeypatch
):
    """Best-effort reporting must not turn the refusal into something else."""

    class DeadClient:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("job server unreachable")

    monkeypatch.setattr(gatk_api, "JobClient", DeadClient)

    resp = client.post(
        "/align-fastq",
        files={"file": ("sample.fastq", b"@r1\nACGT\n+\nIIII\n", "text/plain")},
        data={"reference_genome": "hg38", "job_id": "job-77"},
    )
    assert resp.status_code == 501


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

    call = [c for c in samtools.calls if c["input_bytes"] is not None][0]
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

    quickchecks = samtools.ran("samtools", "quickcheck")
    assert len(quickchecks) == 1, f"expected one quickcheck, got {quickchecks}"


def test_quickcheck_rejection_is_not_reported_as_success(
    client, gatk_api, reference_fasta, samtools
):
    """A truncated BAM has valid magic bytes and a missing EOF block."""
    samtools.fail["quickcheck"] = (1, b"EOF marker absent")
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


# --------------------------------------------------------------------------
# Command injection via the uploaded filename
#
# `file.filename` arrives in the multipart body and nothing upstream of this
# service sanitises it. On main it was joined straight into a temp path that then
# reached three separate `shell=True` call sites from /variant-call. gatk-api has
# no authentication and is reachable from the Docker host (127.0.0.1:5002) and from
# any container on the compose network, so this was live inside the trust boundary
# -- the internet-facing route (app/main.py) happens to apply secure_filename first.
#
# Both halves are pinned here: the filename is neutralised at the source, and each
# sink takes argv so the class stays closed whatever a future caller passes.
# --------------------------------------------------------------------------

PAYLOAD_FILENAME = "x;touch /tmp/pwned;.bam"
SHELL_METACHARACTERS = ";|&$`><\n"


def test_safe_upload_name_neutralises_the_payload(gatk_api):
    stored = gatk_api.safe_upload_name(PAYLOAD_FILENAME, "job-uuid")

    for char in SHELL_METACHARACTERS + "/\\ ":
        assert char not in stored, f"{char!r} survived into {stored!r}"
    # The extension still drives the endpoint's format branching.
    assert stored.endswith(".bam")
    # basename() already reduces this payload to "pwned;.bam" (it contains a "/"),
    # and the allowlist then drops the ";". Every byte left is ours or [A-Za-z0-9_-].
    assert stored == "pwned_job-uuid.bam"


@pytest.mark.parametrize(
    "hostile",
    [
        "x;touch /tmp/pwned;.bam",
        "$(id).bam",
        "`id`.cram",
        "a|nc evil 1234.sam",
        "../../../../etc/passwd.bam",
        "sample name with spaces.bam",
        "",
        None,
    ],
)
def test_safe_upload_name_output_is_always_inert(gatk_api, hostile):
    stored = gatk_api.safe_upload_name(hostile, "abc123")
    assert re.fullmatch(r"[A-Za-z0-9_-]+(\.[A-Za-z0-9.]+)?", stored), stored
    assert os.sep not in stored and "/" not in stored


def test_extension_is_preserved_so_format_branching_still_works(gatk_api):
    for name, expected in [
        ("s.bam", ".bam"),
        ("s.CRAM", ".cram"),
        ("s.sam", ".sam"),
        ("s.vcf", ".vcf"),
        ("s.vcf.gz", ".vcf.gz"),
        ("s.unknown", ""),
    ]:
        assert gatk_api.safe_upload_name(name, "u").endswith(expected)


# --- sink 1: detect_reference -------------------------------------------------


def test_sink_detect_reference_never_builds_a_shell_string(
    gatk_api, samtools, tmp_path
):
    hostile = tmp_path / PAYLOAD_FILENAME.replace("/", "_")
    hostile.write_bytes(b"\x1f\x8b")

    gatk_api.detect_reference(str(hostile), default_reference=None)

    header_calls = [c for c in samtools.calls if "view" in str(c["cmd"])]
    assert header_calls, "detect_reference did not shell out at all"
    for call in header_calls:
        assert isinstance(
            call["cmd"], (list, tuple)
        ), f"string command: {call['cmd']!r}"
        assert call["kwargs"].get("shell") is not True


# --- sink 2: samtools index ---------------------------------------------------


def test_sink_samtools_index_never_builds_a_shell_string(gatk_api, samtools, tmp_path):
    hostile = str(tmp_path / PAYLOAD_FILENAME.replace("/", "_"))

    gatk_api.index_bam_file("probe-job", hostile)

    index_calls = [c for c in samtools.calls if "index" in str(c["cmd"])]
    assert index_calls, "index_bam_file did not shell out at all"
    for call in index_calls:
        assert isinstance(
            call["cmd"], (list, tuple)
        ), f"string command: {call['cmd']!r}"
        assert call["kwargs"].get("shell") is not True
        # The path is one argv element, so `;` is data, not an operator.
        assert hostile in call["cmd"]


# --- sink 3: GATK HaplotypeCaller ---------------------------------------------


def test_sink_haplotypecaller_keeps_metacharacters_as_single_tokens(gatk_api):
    hostile_input = "/tmp/w/x;touch /tmp/pwned;.bam"
    hostile_regions = "chr1;touch /tmp/pwned2"

    argv = gatk_api.build_haplotypecaller_argv(
        "-Xmx4G",
        "/reference/hg38.fa",
        hostile_input,
        "/tmp/w/out.vcf",
        regions=hostile_regions,
        excluded_contigs=["chrEBV;id"],
    )

    assert isinstance(argv, list)
    # Each hostile value survives as exactly one element -- never split, never joined
    # into something a shell would re-parse.
    assert hostile_input in argv
    assert hostile_regions in argv
    assert "chrEBV;id" in argv
    assert argv[argv.index("-I") + 1] == hostile_input
    assert argv[argv.index("-L") + 1] == hostile_regions
    # java_options used to be wrapped in shell quotes; as argv it needs none.
    assert "-Xmx4G" in argv
    assert not any("'" in element for element in argv)


def test_module_has_no_shell_true_on_request_data(source):
    """Sweep every real `shell=True` in the module, parsed rather than grepped.

    Text matching would count this file's own prose about shell=True; the AST only
    sees actual keyword arguments, so the fence cannot be satisfied by rewording.
    """
    tree = ast.parse(source)

    # function name -> lines where it passes shell=True
    offenders = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            for kw in inner.keywords:
                if (
                    kw.arg == "shell"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is True
                ):
                    offenders.setdefault(node.name, []).append(inner.lineno)

    # ensure_reference_dictionaries() is the only permitted survivor: it interpolates
    # REFERENCE_PATHS, a module constant built from env config, never request data.
    assert set(offenders) <= {"ensure_reference_dictionaries"}, offenders


# --- end to end ---------------------------------------------------------------


def test_uploaded_payload_filename_never_lands_on_disk(
    client, gatk_api, reference_fasta, samtools, monkeypatch
):
    """The whole point: the metacharacter must not reach the filesystem either."""
    started = []

    class NoThread:
        def __init__(self, *args, **kwargs):
            started.append(kwargs.get("target") or (args[0] if args else None))

        def start(self):
            pass

    monkeypatch.setattr(gatk_api.threading, "Thread", NoThread)

    resp = client.post(
        "/variant-call",
        files={"file": (PAYLOAD_FILENAME, b"BAM\x01", "application/octet-stream")},
        data={"reference_genome": "hg38"},
    )
    assert resp.status_code == 200, resp.text

    stored = gatk_api.jobs[resp.json()["job_id"]]["input_file"]
    assert ";" not in stored, f"payload reached the filesystem: {stored}"
    assert os.path.basename(stored).endswith(".bam")
    for call in samtools.calls:
        assert isinstance(call["cmd"], (list, tuple)), call["cmd"]


# --------------------------------------------------------------------------
# The reference is caller-chosen and must be checked against the file
#
# reference_genome is a user-supplied form field that reaches this service
# unvalidated. Upstream only checks that the named reference exists, never that it
# is the right one -- the last way this module could produce a wrong BAM rather
# than no BAM.
# --------------------------------------------------------------------------

# chr1 lengths are how detect_reference() tells the two builds apart.
HG38_HEADER = b"@HD\tVN:1.6\tSO:coordinate\n@SQ\tSN:chr1\tLN:248956422\n"
HG19_HEADER = b"@HD\tVN:1.6\tSO:coordinate\n@SQ\tSN:chr1\tLN:249250621\n"


def _post_cram_with_body(client, body, reference_genome="hg38"):
    return client.post(
        "/cram-to-bam",
        files={"file": ("sample.cram", body, "application/octet-stream")},
        data={"reference_genome": reference_genome},
    )


def test_cram_whose_header_disagrees_with_the_requested_reference_is_refused(
    client, gatk_api, reference_fasta, samtools
):
    """An hg19 CRAM converted against hg38 yields real-looking, wrong coordinates."""
    samtools.header = HG19_HEADER
    before = _bam_files(gatk_api)

    resp = _post_cram_with_body(client, HG19_HEADER, reference_genome="hg38")

    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert "hg19" in detail and "hg38" in detail
    assert "mismatch" in detail.lower()
    assert samtools.ran("samtools", "sort") == []
    assert _bam_files(gatk_api) == before, "nothing may be written on a mismatch"


def test_matching_reference_is_accepted(client, gatk_api, reference_fasta, samtools):
    samtools.header = HG38_HEADER
    resp = _post_cram_with_body(client, HG38_HEADER, reference_genome="hg38")
    assert resp.status_code == 200, resp.text


def test_reference_aliases_are_not_treated_as_a_mismatch(
    client, gatk_api, reference_fasta, samtools
):
    """grch38 and hg38 name the same build; rejecting that pair is a false alarm."""
    samtools.header = HG38_HEADER
    resp = _post_cram_with_body(client, HG38_HEADER, reference_genome="grch38")
    assert resp.status_code == 200, resp.text


def test_undeterminable_reference_does_not_block_the_conversion(
    client, gatk_api, reference_fasta, samtools
):
    """detect_reference() knows only a couple of assemblies; unknown must not fail."""
    neutral = b"@HD\tVN:1.6\tSO:coordinate\n@SQ\tSN:ctg1\tLN:1234\n"
    samtools.header = neutral
    resp = _post_cram_with_body(client, neutral, reference_genome="hg38")
    assert resp.status_code == 200, resp.text


def test_reference_detection_never_reaches_a_shell(source):
    """detect_reference() is now called on uploaded filenames, so no shell=True.

    A filename survives `os.path.basename` with `;` and `$(...)` intact, so handing
    that path to /bin/sh would be command injection.
    """
    body = source[
        source.index("def detect_reference(") : source.index(
            '@app.post("/variant-call")'
        )
    ]
    code = "\n".join(
        line for line in body.splitlines() if not line.strip().startswith("#")
    )
    assert not re.search(r"shell\s*=\s*True", code)


# --------------------------------------------------------------------------
# samtools can complain on stderr and still exit 0
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "complaint",
    [
        b"[E::cram_get_ref] Failed to populate reference for id 0",
        b"[W::sam_hdr_create] Reference sequence chr1 not found",
        b"warning: MD5 checksum mismatch for chr1",
        b"[W::bam_read1] truncated file",
    ],
)
def test_reference_complaint_on_stderr_is_not_swallowed(
    client, gatk_api, reference_fasta, samtools, complaint
):
    """rc=0 plus a reference complaint means real-looking records with wrong bases."""
    samtools.conversion_stderr = complaint
    before = _bam_files(gatk_api)

    resp = _post_cram_with_body(client, HG38_HEADER)

    assert resp.status_code == 500, resp.text
    assert resp.json().get("success") is not True
    assert _bam_files(gatk_api) == before


def test_benign_stderr_still_succeeds(client, gatk_api, reference_fasta, samtools):
    """Not every word on stderr is fatal, or nothing would ever convert."""
    samtools.conversion_stderr = b"[M::bam_sort_core] merging from 2 files"
    resp = _post_cram_with_body(client, HG38_HEADER)
    assert resp.status_code == 200, resp.text


# --------------------------------------------------------------------------
# An empty BAM is not a negative result
# --------------------------------------------------------------------------


def test_zero_record_output_is_refused(client, gatk_api, reference_fasta, samtools):
    """A header-only BAM passes size, magic, quickcheck and index -- and reads
    downstream as "no variants found" rather than "the input was empty"."""
    samtools.idxstats = [("chr1", 12, 0, 0), ("*", 0, 0, 0)]
    before = _bam_files(gatk_api)
    bai_before = sorted(Path(gatk_api.DATA_DIR).rglob("*.bai"))

    resp = _post_cram_with_body(client, HG38_HEADER)

    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"].lower()
    assert "empty" in detail
    assert "no variants found" in detail, "the clinical misreading must be named"
    assert _bam_files(gatk_api) == before
    assert sorted(Path(gatk_api.DATA_DIR).rglob("*.bai")) == bai_before


def test_record_count_is_reported_and_read_from_the_index(
    client, gatk_api, reference_fasta, samtools
):
    samtools.idxstats = [("chr1", 12, 100, 3), ("*", 0, 0, 7)]
    resp = _post_cram_with_body(client, HG38_HEADER)

    assert resp.status_code == 200, resp.text
    assert resp.json()["records"] == 110
    # idxstats answers from the .bai, so counting must not stream the BAM.
    counts = samtools.ran("samtools", "idxstats")
    assert len(counts) == 1
    assert samtools.ran("samtools", "view", "-c") == []


def test_unplaced_reads_alone_still_count(client, gatk_api, reference_fasta, samtools):
    """A BAM of only unmapped reads is not empty; it lands in idxstats' '*' row."""
    samtools.idxstats = [("chr1", 12, 0, 0), ("*", 0, 0, 5)]
    resp = _post_cram_with_body(client, HG38_HEADER)
    assert resp.status_code == 200, resp.text
    assert resp.json()["records"] == 5


# --------------------------------------------------------------------------
# Sorting and indexing
#
# `samtools index` only works on coordinate-sorted input, and PyPGx, GATK and
# OptiType-from-BAM all want a sorted indexed BAM. Handing back an unsorted one
# turns "returns a fake file" into "returns a real file the pipeline cannot use".
# Sorting is the expensive step, so it is skipped when the header says it is
# already done.
# --------------------------------------------------------------------------


def _post_cram(client, samtools, header):
    samtools.header = header
    return client.post(
        "/cram-to-bam",
        files={"file": ("sample.cram", b"CRAM\x03\x00", "application/octet-stream")},
        data={"reference_genome": "hg38"},
    )


def test_coordinate_sorted_input_is_not_re_sorted(
    client, gatk_api, reference_fasta, samtools
):
    """`view -b` preserves record order, so a sorted input needs no sort."""
    resp = _post_cram(client, samtools, COORDINATE_HEADER)
    assert resp.status_code == 200, resp.text

    assert samtools.ran("samtools", "sort") == [], "re-sorted an already-sorted input"
    assert _conversion_argv(samtools)[:3] == ["samtools", "view", "-b"]
    assert resp.json()["sorted"] is False


@pytest.mark.parametrize(
    "header, why",
    [
        (UNSORTED_HEADER, "SO:unsorted"),
        (QUERYNAME_HEADER, "SO:queryname"),
        (HEADER_WITHOUT_SO, "@HD carries no SO: field"),
        (b"@SQ\tSN:chr1\tLN:12\n", "no @HD line at all"),
        (b"", "empty header"),
    ],
)
def test_input_that_is_not_coordinate_sorted_gets_sorted(
    client, gatk_api, reference_fasta, samtools, header, why
):
    """Anything not known to be coordinate-sorted must be sorted -- including a
    header that simply does not say, since guessing wrong yields an unindexable BAM."""
    resp = _post_cram(client, samtools, header)
    assert resp.status_code == 200, f"{why}: {resp.text}"

    argv = _conversion_argv(samtools)
    assert argv[:2] == ["samtools", "sort"], f"{why}: did not sort"
    assert resp.json()["sorted"] is True


def test_sort_writes_its_temp_files_into_the_scratch_dir(
    client, gatk_api, reference_fasta, samtools
):
    """samtools sort spills <prefix>.NNNN.bam. Those must never land on /data.

    The scratch dir is removed in the handler's finally block; the shared volume is
    not, so a leaked intermediate there would be the same class of bug as the one
    that put the output in container-private /tmp.
    """
    _post_cram(client, samtools, UNSORTED_HEADER)

    argv = _conversion_argv(samtools)
    assert "-T" in argv, "sort must be given an explicit temp prefix"
    prefix = Path(argv[argv.index("-T") + 1])
    scratch = Path(gatk_api.TEMP_DIR).resolve()
    assert scratch in prefix.resolve().parents
    results = (Path(gatk_api.DATA_DIR) / "results").resolve()
    assert results not in prefix.resolve().parents


def test_sort_runs_within_a_bounded_memory_budget(
    client, gatk_api, reference_fasta, samtools
):
    """This container's memory is shared with a 20 GiB GATK heap."""
    _post_cram(client, samtools, UNSORTED_HEADER)

    argv = _conversion_argv(samtools)
    assert argv[argv.index("-m") + 1] == gatk_api.SORT_MEMORY
    assert argv[argv.index("-@") + 1] == gatk_api.SORT_THREADS
    # A conservative default, not a guess-large one.
    assert gatk_api.SORT_MEMORY == "768M"
    assert gatk_api.SORT_THREADS == "2"


def test_sorting_a_cram_still_passes_the_reference(
    client, gatk_api, reference_fasta, samtools
):
    """`samtools sort` spells it --reference; -T is its temp prefix, not the FASTA."""
    _post_cram(client, samtools, UNSORTED_HEADER)

    argv = _conversion_argv(samtools)
    assert argv[argv.index("--reference") + 1] == str(reference_fasta)
    assert argv[argv.index("-T") + 1] != str(reference_fasta)


def test_output_is_indexed_whether_or_not_it_was_sorted(
    client, gatk_api, reference_fasta, samtools
):
    for header in (COORDINATE_HEADER, UNSORTED_HEADER):
        samtools.calls.clear()
        resp = _post_cram(client, samtools, header)
        assert resp.status_code == 200, resp.text

        indexes = samtools.ran("samtools", "index")
        assert len(indexes) == 1, f"{header!r}: expected one index call, got {indexes}"
        bam_path = resp.json()["bam_path"]
        assert indexes[0][-1] == bam_path
        assert resp.json()["bam_index"] == f"{bam_path}.bai"
        assert Path(f"{bam_path}.bai").exists()


def test_index_failure_is_not_reported_as_success(
    client, gatk_api, reference_fasta, samtools
):
    """An unindexed BAM is unusable downstream, so it must not ship."""
    samtools.fail["index"] = (1, b"[E::hts_idx_push] chromosome blocks not continuous")
    before = _bam_files(gatk_api)

    resp = _post_cram(client, samtools, COORDINATE_HEADER)

    assert resp.status_code == 500, resp.text
    assert resp.json().get("success") is not True
    assert "index" in resp.json()["detail"]
    assert _bam_files(gatk_api) == before, "unindexed BAM left on the shared volume"


def test_header_probe_reads_only_the_header(
    client, gatk_api, reference_fasta, samtools
):
    """`view -H` on a WGS CRAM must not stream the whole file to decide on sorting.

    Two probes run per CRAM: detect_reference()'s reference check and
    read_sort_order()'s. Both are header-only reads.
    """
    _post_cram(client, samtools, COORDINATE_HEADER)

    probes = [a for a in samtools.ran("samtools", "view") if "-H" in a]
    assert probes, "no header probe ran"
    for probe in probes:
        assert "-o" not in probe, "a probe must not write anything"
        assert "-b" not in probe, "a probe must not decode records"

    # read_sort_order() passes the reference so a CRAM header can be read at all.
    with_reference = [p for p in probes if "-T" in p]
    assert with_reference, f"no probe passed the reference: {probes}"
    assert with_reference[0][with_reference[0].index("-T") + 1] == str(reference_fasta)


def test_sam_route_sorts_an_unsorted_sam(client, gatk_api, samtools):
    """SAM is far more often unsorted than CRAM, so this path matters more here."""
    samtools.header = QUERYNAME_HEADER
    resp = client.post(
        "/sam-to-bam",
        files={"file": ("sample.sam", SAM_BODY, "text/plain")},
        data={"reference_genome": "hg38"},
    )
    assert resp.status_code == 200, resp.text

    argv = _conversion_argv(samtools)
    assert argv[:2] == ["samtools", "sort"]
    # No reference is involved for SAM, sorted or not.
    assert "--reference" not in argv
    assert len(samtools.ran("samtools", "index")) == 1


def test_cram_to_bam_passes_argv_as_a_list_not_a_shell_string(
    client, gatk_api, reference_fasta, samtools
):
    """An uploaded filename reaches this command line; never hand it to a shell."""
    client.post(
        "/cram-to-bam",
        files={"file": ("sample.cram", b"CRAM\x03\x00", "application/octet-stream")},
        data={"reference_genome": "hg38"},
    )
    for call in samtools.calls:
        assert isinstance(call["cmd"], (list, tuple)), call["cmd"]
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


def test_a_skipped_log_destination_is_reported_loudly(gatk_api, tmp_path, caplog):
    """Degrading to console must not be silent.

    252 converged all five sidecars on "warn and keep running" rather than "raise
    at import". That trade is only defensible if the operator is told: a service
    quietly dropping its progress log is a shared volume that is not mounted, and
    the app will show no progress for this stage. gatk-api is the one of the five
    that can be imported out-of-container, so this is where the warning is
    exercised for real rather than asserted against source.
    """
    missing = str(tmp_path / "no" / "such" / "dir.log")
    before = len(gatk_api._log_file_errors)

    assert gatk_api._bounded_file_handler(missing) is None
    assert len(gatk_api._log_file_errors) == before + 1
    assert gatk_api._log_file_errors[-1][0] == missing

    probe = logging.getLogger("zaropgx-unopened-log-probe")
    with caplog.at_level(logging.WARNING, logger=probe.name):
        gatk_api._warn_about_unopened_logs(probe)

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "an unopenable log destination was skipped without a word"
    said = "\n".join(r.getMessage() for r in warnings)
    assert missing in said, f"the warning does not name the path: {said}"
    assert "console only" in said

    # Leave the module-level list as it was found; it is import-time state.
    del gatk_api._log_file_errors[before:]
