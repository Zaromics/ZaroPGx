"""gatk-api /bcf-to-vcf: a real re-encode, or a loud failure. Never a quiet success.

Why this endpoint exists at all is pinned in tests/test_input_type_honesty.py: every
sidecar downstream decides what a file is from its *name*, and docker/pharmcat's
/genotype answers 400 to anything that is not ``.vcf``/``.vcf.gz``/``.vcf.bgz`` behind a
``|| true`` that swallows it. So a BCF has to be genuinely converted, and this module
pins the two halves of "genuinely":

* **The command is the conversion.** ``bcftools view -O z`` as an argv list, never a
  shell string — the input path derives from an uploaded filename — and the output on
  the shared ``/data`` volume, because the caller is a Nextflow process in another
  container that copies the result by path.
* **The result is vouched for.** BCF and VCF hold the same records, so there is no
  reference to decode against and nothing to sort: what is left to get wrong is
  bcftools exiting 0 over a file that is empty, truncated or not BGZF at all. Each of
  those must be an HTTP error, because an empty VCF reads downstream as "no variants
  found" — a clinical statement, not the truncated upload it actually is.

The module is imported out-of-container the way tests/test_liftover_endpoint.py and
tests/test_gatk_api_no_mock_bam.py do (stubbed psutil/job_client, temp data tree), and
bcftools is a recorded fake, because htslib does not exist in the unit-test environment.
"""

import gzip
import importlib.util
import logging
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
GATK_API_SOURCE = REPO_ROOT / "docker" / "gatk-api" / "gatk_api.py"

# Not a real BCF: every tool that would read one is faked below, so the body only has to
# be bytes the endpoint streams to disk and hands to bcftools.
BCF_BYTES = b"BCF\x02\x02" + (0).to_bytes(4, "little") + b"\x00" * 16


def _fake_psutil():
    module = types.ModuleType("psutil")
    module.virtual_memory = lambda: types.SimpleNamespace(total=16 * 1024**3)
    module.Process = lambda *a, **k: types.SimpleNamespace()
    return module


def _fake_job_client():
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
    root = tmp_path_factory.mktemp("gatk_api_bcf_home")
    before_handlers = list(logging.root.handlers)

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATA_DIR", str(root / "data"))
        mp.setenv("TMPDIR", str(root / "tmp"))
        mp.setenv("REFERENCE_DIR", str(root / "reference"))
        mp.setitem(sys.modules, "psutil", _fake_psutil())
        mp.setitem(sys.modules, "job_client", _fake_job_client())

        spec = importlib.util.spec_from_file_location(
            "zaropgx_gatk_api_bcf_under_test", GATK_API_SOURCE
        )
        module = importlib.util.module_from_spec(spec)
        mp.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
        yield module

    for handler in list(logging.root.handlers):
        if handler not in before_handlers:
            logging.root.removeHandler(handler)
    for handler in getattr(module, "_log_handlers", []):
        handler.close()


@pytest.fixture()
def client(gatk_api):
    return TestClient(gatk_api.app)


class FakeBcftools:
    """Stands in for the module's `subprocess` binding, recording every argv.

    `bcftools view -O z -o OUT IN` writes a gzipped VCF holding `self.rows`; setting
    `rows` to `[]` produces the header-only file that must be refused rather than
    shipped. `bcftools index -t` touches a .tbi. `returncode` and `payload` exist so a
    non-zero exit and an uncompressed output can each be provoked.

    THE INPUT PATH IS OPENED, and a missing one fails the call the way bcftools would
    (exit 2, stderr naming the file). Without that the fake wrote its output from the
    subcommand alone and never looked at what it was pointed at: rewriting the input
    argument to `/nonexistent` left 11 of this module's 13 tests green, so nothing here
    pinned that bcftools is handed the staged upload at all.
    """

    SubprocessError = subprocess.SubprocessError
    CalledProcessError = subprocess.CalledProcessError
    TimeoutExpired = subprocess.TimeoutExpired
    PIPE = subprocess.PIPE
    STDOUT = subprocess.STDOUT

    VCF_HEADER = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"

    def __init__(self):
        self.calls = []
        self.rows = [("chr10", 94781859)]
        self.returncode = 0
        self.stderr = b""
        # None means "write real BGZF"; bytes are written verbatim instead.
        self.payload = None
        self.index_writes_tbi = True

    def argvs(self):
        return [list(call) for call in self.calls]

    def ran(self, *prefix):
        want = list(prefix)
        return [argv for argv in self.argvs() if argv[: len(want)] == want]

    def _flag_value(self, argv, flag):
        return argv[argv.index(flag) + 1]

    def run(self, argv, **kwargs):
        self.calls.append(argv)
        sub = argv[1] if len(argv) > 1 else ""

        # Both commands read their last positional argument: `view ... IN` and
        # `index -t -f VCF`.
        if not os.path.exists(argv[-1]):
            return subprocess.CompletedProcess(
                argv,
                2,
                b"",
                f"bcftools: {argv[-1]}: No such file or directory".encode("utf-8"),
            )

        if self.returncode == 0 and sub == "view":
            out = self._flag_value(argv, "-o")
            if self.payload is not None:
                Path(out).write_bytes(self.payload)
            else:
                body = self.VCF_HEADER + "".join(
                    f"{chrom}\t{pos}\trsX\tG\tA\t100\tPASS\t.\n"
                    for chrom, pos in self.rows
                )
                Path(out).write_bytes(gzip.compress(body.encode("utf-8")))
        elif self.returncode == 0 and sub == "index" and self.index_writes_tbi:
            Path(f"{argv[-1]}.tbi").write_bytes(b"TBI\x01")

        return subprocess.CompletedProcess(argv, self.returncode, b"", self.stderr)


@pytest.fixture()
def bcftools(gatk_api, monkeypatch):
    tools = FakeBcftools()
    monkeypatch.setattr(gatk_api, "subprocess", tools)
    return tools


def _post(client, filename="sample.bcf", body=BCF_BYTES, **extra):
    return client.post(
        "/bcf-to-vcf",
        files={"file": (filename, body, "application/octet-stream")},
        data={"reference_genome": "hg38", **extra},
    )


# ---------------------------------------------------------------------------
# The happy path, and what it promises
# ---------------------------------------------------------------------------
def test_the_conversion_is_bcftools_view_to_bgzip(client, bcftools):
    resp = _post(client)

    assert resp.status_code == 200, resp.text
    views = bcftools.ran("bcftools", "view")
    assert len(views) == 1, bcftools.argvs()
    argv = views[0]
    assert argv[argv.index("-O") + 1] == "z", "the output must be bgzipped, not plain"
    assert argv[-1].endswith(".bcf"), "the last argument is the input file"


def test_every_bcftools_call_is_an_argv_list(client, bcftools):
    """The input path derives from an uploaded filename; no shell may re-parse it."""
    resp = _post(client, filename="x;touch pwned;.bcf")

    # A bare `for argv in ...: assert` over an EMPTY list passes, so a run that 400s
    # before bcftools is reached would satisfy this test without testing anything.
    assert resp.status_code == 200, resp.text
    assert bcftools.argvs(), "nothing ran, so this loop would assert over nothing"

    for argv in bcftools.argvs():
        assert isinstance(argv, list), argv


def test_the_upload_name_is_sanitised_and_the_output_stem_derived_from_it(
    client, bcftools
):
    resp = _post(client, filename="../../evil name;.bcf")

    assert resp.status_code == 200, resp.text
    stored = Path(bcftools.ran("bcftools", "view")[0][-1]).name
    assert stored.startswith("evilname_"), stored
    assert stored.endswith(".bcf")
    # stored_stem(), not splitext: the `.bcf` comes off, and only that.
    assert Path(resp.json()["vcf_path"]).name == stored[: -len(".bcf")] + ".vcf.gz"


def test_the_output_lands_on_the_shared_volume(client, bcftools, gatk_api):
    """The caller is a Nextflow process in another container; /tmp is invisible to it."""
    resp = _post(client, job_id="job-1")

    vcf_path = resp.json()["vcf_path"]
    assert vcf_path.startswith(os.path.join(gatk_api.DATA_DIR, "results")), vcf_path
    assert not vcf_path.startswith(gatk_api.TEMP_DIR), vcf_path


def test_the_result_is_tabix_indexed(client, bcftools):
    resp = _post(client)

    assert bcftools.ran("bcftools", "index", "-t", "-f"), bcftools.argvs()
    assert resp.json()["vcf_index"].endswith(".vcf.gz.tbi")


def test_a_failed_index_is_a_warning_not_a_lost_run(client, bcftools):
    """The pipeline re-indexes when no .tbi travels with the VCF, so this is survivable."""
    bcftools.index_writes_tbi = False

    resp = _post(client)

    assert resp.status_code == 200, resp.text
    assert resp.json()["vcf_index"] is None


def test_the_input_copy_is_not_left_on_the_container(client, bcftools, gatk_api):
    """A whole-genome BCF arrives here; nothing ever deleted these before."""
    _post(client)

    leftovers = list(Path(gatk_api.TEMP_DIR).glob("**/*.bcf"))
    assert leftovers == [], leftovers


# ---------------------------------------------------------------------------
# Vouching: the whole point of the endpoint
# ---------------------------------------------------------------------------
def test_an_empty_conversion_is_a_loud_error(client, bcftools):
    """A header-only VCF reads downstream as "no variants found". It is not that."""
    bcftools.rows = []

    resp = _post(client)

    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"].lower()
    assert "empty" in detail
    assert "no variants found" in detail


def test_a_discarded_output_does_not_survive_the_refusal(client, bcftools):
    """A refused conversion must leave nothing a later step could pick up by path.

    Asked of the exact path bcftools was told to write, not of a glob over the data
    tree: the module-scoped sidecar shares one DATA_DIR across this file's tests, so a
    glob would also see the outputs the successful conversions above legitimately left.
    """
    bcftools.rows = []

    _post(client)

    written = bcftools.ran("bcftools", "view")[0]
    output = Path(written[written.index("-o") + 1])
    assert not output.exists(), output


def test_an_uncompressed_output_is_refused(client, bcftools):
    """Everything downstream opens this file as bgzip; plain text is not a conversion.

    Named for what it pins, for the reason the gVCF module's copy of this test gives:
    `_looks_gzipped` reads the two gzip magic bytes only, so this is "plain text is
    refused", not "non-BGZF framing is refused".
    """
    bcftools.payload = b"##fileformat=VCFv4.2\nnot compressed at all\n"

    resp = _post(client)

    assert resp.status_code == 500, resp.text
    assert "not compressed at all" in resp.json()["detail"].lower()


def test_an_empty_output_file_is_refused(client, bcftools):
    bcftools.payload = b""

    resp = _post(client)

    assert resp.status_code == 500, resp.text
    assert "wrote no vcf" in resp.json()["detail"].lower()


def test_a_non_zero_bcftools_exit_is_refused(client, bcftools):
    bcftools.returncode = 3
    bcftools.stderr = b"[E::bcf_hdr_read] Invalid BCF2 magic string"

    resp = _post(client)

    assert resp.status_code == 500, resp.text
    detail = resp.json()["detail"]
    assert "exit code 3" in detail
    assert (
        "Invalid BCF2 magic string" in detail
    ), "the tool's own complaint must get out"


def test_no_reference_fasta_is_required(client, bcftools, gatk_api):
    """Unlike CRAM, this conversion is self-contained: it cannot decode against a wrong
    reference because it decodes against none. The form field is accepted and ignored,
    so a deployment with no staged FASTA still converts BCFs."""
    assert not os.path.exists(gatk_api.REFERENCE_PATHS["hg38"])

    assert _post(client).status_code == 200
    assert _post(client, reference_genome="hg19").status_code == 200
