"""Two input types the upload lane used to accept and then fail on: gVCF and BCF.

``upload_router``'s own docstring said it: "GVCF/BCF: accepted today, but main.nf has no
branch for either, so the job fails at workflow definition." Both were typed, given a
workflow with ``needs_pypgx``, given a patient row, a job row and a queue slot, and then
killed at ``pipelines/pgx/main.nf``'s ``error "Unsupported input type"``. The two are only
superficially the same problem, and they get opposite answers:

* **gVCF is refused.** A gVCF is not a VCF with extra rows. Its ``<NON_REF>`` symbolic
  allele and ``##GVCFBlock`` records assert reference confidence over whole spans, and
  PharmCAT has not been validated against them. Routed onto the vcf lane it would not
  error — it would emit star alleles nobody has checked. A wrong answer delivered
  confidently is worse than the failed job it replaces, so the refusal is the fix.

  Detection has to look past the extension: GATK writes gVCFs as ``sample.g.vcf[.gz]``,
  whose last suffix is ``.vcf``, so the extension table typed them VCF and analysed them
  as one. Name *and* header are consulted.

* **BCF is carried.** BCF is VCF in htslib's binary encoding. Every consumer on main.nf's
  vcf branch reads it through htslib (bcftools, pysam), which detects the encoding from
  the file's own magic bytes rather than from ``params.input_type``. So the fix is one
  alias at submission — no second branch in main.nf that would only duplicate the first.

Also pinned here: ``/upload/inspect-header`` saved its temp file under a suffix built from
the raw client-supplied ``file.filename``, putting a client-controlled string into a
filesystem path.
"""

import asyncio
import gzip
import re
import uuid
from pathlib import Path

import pytest

import app.api.routes.upload_router as ur

REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN_NF = REPO_ROOT / "pipelines" / "pgx" / "main.nf"

# A minimal single-sample VCF header plus one record. Enough for the extension sniffers
# and for the ##GVCFBlock reader, and it is never handed to a real caller.
VCF_HEADER = b"##fileformat=VCFv4.2\n"
VCF_COLUMNS = b"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tNA12878\n"
PLAIN_VCF_BYTES = VCF_HEADER + VCF_COLUMNS

# What GATK HaplotypeCaller -ERC GVCF writes into the header, and what no plain VCF
# carries: one record per reference-confidence band.
GVCF_BYTES = (
    VCF_HEADER
    + b"##GVCFBlock0-1=minGQ=0(inclusive),maxGQ=1(exclusive)\n"
    + b"##GVCFBlock1-2=minGQ=1(inclusive),maxGQ=2(exclusive)\n"
    + b'##ALT=<ID=NON_REF,Description="Represents any possible alternative allele">\n'
    + VCF_COLUMNS
)

# BCF's magic bytes. FileProcessor types BCF from its extension, so the body only has to
# not look like anything else.
BCF_BYTES = b"BCF\x02\x02" + b"\x00" * 32
BAM_BYTES = b"BAM\x01" + b"\x00" * 32


# ---------------------------------------------------------------------------
# The real endpoint, driven through the real FileProcessor
# ---------------------------------------------------------------------------
@pytest.fixture
def upload(client, monkeypatch, tmp_path):
    """POST real bytes at ``/upload/genomic-data``, through the real FileProcessor.

    Same shape as tests/test_upload_story_coherence.py: only what would reach Postgres,
    the filesystem outside ``tmp_path`` or a sibling container is replaced. The file-type
    verdict, the workflow dict and the refusal all come from production code.
    """
    monkeypatch.setattr(ur.file_processor, "temp_dir", tmp_path / "uploads")

    created_patients = []
    monkeypatch.setattr(
        ur,
        "create_patient",
        lambda db, identifier: (created_patients.append(identifier) or uuid.uuid4()),
    )
    monkeypatch.setattr(
        ur,
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

    monkeypatch.setattr(ur, "JobService", _FakeJobService)

    async def _noop_background(*args, **kwargs):
        return None

    monkeypatch.setattr(
        ur, "process_file_nextflow_background_with_db", _noop_background
    )

    def _post(name, payload):
        return client.post(
            "/upload/genomic-data",
            files=[("files", (name, payload, "application/octet-stream"))],
            data={"reference_genome": "hg38"},
        )

    _post.created_patients = created_patients
    return _post


# ---------------------------------------------------------------------------
# gVCF: refused, by name and by header
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "name,payload",
    [
        ("sample.gvcf", GVCF_BYTES),
        ("sample.gvcf.gz", gzip.compress(GVCF_BYTES)),
        # The GATK spelling. Its last suffix is ".vcf", so the extension table used to
        # type it VCF and hand it to the vcf lane as an ordinary variant call set.
        ("sample.g.vcf", GVCF_BYTES),
        ("sample.g.vcf.gz", gzip.compress(GVCF_BYTES)),
    ],
    ids=["gvcf", "gvcf.gz", "g.vcf", "g.vcf.gz"],
)
def test_a_gvcf_is_refused_before_a_job_exists(upload, name, payload):
    resp = upload(name, payload)

    assert resp.status_code == 400, resp.text
    assert "gvcf" in resp.json()["detail"].lower()
    assert upload.created_patients == [], "refusal must precede any patient/job row"


def test_a_gvcf_named_as_a_plain_vcf_is_caught_by_its_header(upload):
    """Naming is a convention, ``##GVCFBlock`` is a declaration. Trust the declaration."""
    resp = upload("sample.vcf", GVCF_BYTES)

    assert resp.status_code == 400, resp.text
    assert "gvcf" in resp.json()["detail"].lower()


def test_the_gvcf_refusal_names_the_reason_and_a_way_out(upload):
    detail = upload("sample.g.vcf", GVCF_BYTES).json()["detail"].lower()

    # the reason: reference-confidence blocks, and that nobody has validated them
    assert "reference" in detail
    assert "non_ref" in detail
    assert "##gvcfblock" in detail
    assert "pharmcat" in detail
    # the way out: convert to a plain VCF, or hand over the alignment it came from
    assert "convert" in detail
    assert "plain single-sample" in detail
    for accepted in ("bam", "cram", "sam"):
        assert accepted in detail


def test_the_gvcf_refusal_promises_no_analysis(upload):
    """The old copy said gVCFs "will be processed through PyPGx and PharmCAT"."""
    detail = upload("sample.gvcf", GVCF_BYTES).json()["detail"].lower()

    for promise in ("will be processed", "treated as vcf", "may require conversion"):
        assert promise not in detail, promise


def test_the_gvcf_refusal_carries_no_markup_of_its_own(upload):
    """One string, two sinks, and the sinks want opposite things.

    ``unsupported_reason``/``refusal_reason`` is the 400's plain-text ``detail`` *and*
    the red alert in index.html, which builds that alert by interpolating the reason
    into a template string and assigning it with innerHTML (see the ``wf.refused``
    branch there). So a literal ``<NON_REF>`` in the sentence is parsed as an unknown
    tag and vanishes — the user reads "blocks (the  allele and ...)". Escaping it
    instead would leave ``&lt;NON_REF&gt;`` sitting in the API error. Writing the allele
    without its angle brackets is what reads correctly in both, and this pins it.

    (Asserted on the string rather than through tests/js/render_workflow_panel.js: that
    harness's DOM shim stores innerHTML as a plain string and never parses it, so it
    cannot see a tag being swallowed. Recommendations are a different case — they are
    HTML by design, so the ``&lt;NON_REF&gt;`` in the bcftools bullet is correct there.)
    """
    detail = upload("sample.g.vcf", GVCF_BYTES).json()["detail"]

    assert "<" not in detail and ">" not in detail, detail
    assert "&lt;" not in detail and "&amp;" not in detail, detail


def test_a_gvcf_workflow_plans_no_steps_it_cannot_run():
    """No needs_* flag may promise an analysis that is refused at the door."""
    from app.api.models import FileType
    from app.api.utils.file_processor import FileAnalysis, FileProcessor

    workflow = FileProcessor(temp_dir="/tmp").determine_workflow(
        FileAnalysis(
            file_type=FileType.GVCF,
            is_compressed=False,
            has_index=False,
            file_size=1,
            vcf_info=None,
            is_valid=True,
            validation_errors=[],
        )
    )

    assert workflow["unsupported"] is True
    assert workflow["is_provisional"] is False
    for flag in ("needs_alignment", "needs_gatk", "needs_hla", "needs_pypgx"):
        assert workflow[flag] is False, flag


def test_the_preview_marks_a_gvcf_as_refused(client):
    """The header preview and the upload gate must reach the same verdict."""
    resp = client.post(
        "/upload/inspect-header",
        files={"file": ("sample.g.vcf", GVCF_BYTES, "application/octet-stream")},
    )

    assert resp.status_code == 200, resp.text
    workflow = resp.json()["compat"]["workflow"]
    assert workflow["refused"] is True
    assert "gvcf" in (workflow["refusal_reason"] or "").lower()


@pytest.mark.parametrize(
    "name,payload",
    [
        ("sample.vcf", PLAIN_VCF_BYTES),
        ("sample.vcf.gz", gzip.compress(PLAIN_VCF_BYTES)),
    ],
    ids=["vcf", "vcf.gz"],
)
def test_a_plain_vcf_is_not_mistaken_for_a_gvcf(tmp_path, name, payload):
    """The negative control, at the detector.

    Driven through ``_detect_file_type`` rather than the endpoint because
    FileProcessor's VCF path calls the header inspector for a sample count, which needs
    pysam or bcftools and has neither on a bare host — every VCF there reads as
    zero-sample and is refused for a reason that has nothing to do with this change.
    """
    from app.api.models import FileType
    from app.api.utils.file_processor import FileProcessor

    path = tmp_path / name
    path.write_bytes(payload)

    assert FileProcessor(temp_dir=str(tmp_path))._detect_file_type(path) is FileType.VCF


def test_an_unreadable_file_is_not_guessed_into_a_gvcf(tmp_path):
    """The header read is best effort: a truncated gzip must not become a refusal."""
    from app.api.models import FileType
    from app.api.utils.file_processor import FileProcessor

    path = tmp_path / "broken.vcf.gz"
    path.write_bytes(b"\x1f\x8b\x08\x00truncated")

    assert FileProcessor(temp_dir=str(tmp_path))._detect_file_type(path) is FileType.VCF


# ---------------------------------------------------------------------------
# BCF: carried on the vcf branch, not refused and not given a branch of its own
# ---------------------------------------------------------------------------
def _submit(monkeypatch, tmp_path, workflow):
    """Run the real Nextflow submitter and return the payload it POSTs.

    Everything replaced here is a boundary — the job store, the header inspector, the
    HTTP call and the completion poll. The payload assembly under test is production
    code.
    """
    captured = {}

    class _FakeJob:
        def __init__(self):
            self.id = "job-1"
            self.status = "running"
            self.job_metadata = {}

    job = _FakeJob()

    class _FakeJobService:
        def __init__(self, db):
            pass

        def get_job(self, job_id):
            return job

        def update_job(self, job_id, update):
            return job

        def update_job_step(self, job_id, step_name, update):
            return None

        def log_job_event(self, job_id, log_data):
            return None

    monkeypatch.setattr(ur, "JobService", _FakeJobService)
    monkeypatch.setattr(ur, "inspect_header", lambda path: {"samples": ["NA12878"]})
    monkeypatch.setattr(ur, "save_genomic_header", lambda db, path, ft, header: 1)
    monkeypatch.setattr(ur, "extract_raw_header_text", lambda path: None)

    class _RunResponse:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"job_key": "nf-1", "outdir": "/data/reports/p/job-1"}

    def _post(url, json=None, timeout=None):
        captured["payload"] = json
        return _RunResponse()

    monkeypatch.setattr(ur.requests, "post", _post)

    async def _noop_wait(*args, **kwargs):
        return None

    monkeypatch.setattr(ur, "wait_for_nextflow_completion", _noop_wait)

    asyncio.run(
        ur.process_file_nextflow_background(
            str(tmp_path / "sample.in"),
            "patient-1",
            "data-1",
            workflow,
            None,
            None,
            "job-1",
        )
    )

    assert "payload" in captured, "the submitter never reached the Nextflow POST"
    return captured["payload"]


def test_a_bcf_reaches_the_pipeline_as_a_vcf(monkeypatch, tmp_path):
    """main.nf has no bcf branch; bcftools and pysam do not need one."""
    payload = _submit(monkeypatch, tmp_path, {"file_type": "bcf", "reference": "hg38"})

    assert payload["input_type"] == "vcf"


@pytest.mark.parametrize("file_type", ["vcf", "bam", "cram", "sam"])
def test_every_other_input_type_is_passed_through_unchanged(
    monkeypatch, tmp_path, file_type
):
    payload = _submit(
        monkeypatch, tmp_path, {"file_type": file_type, "reference": "hg38"}
    )

    assert payload["input_type"] == file_type


def test_a_bcf_upload_is_not_refused(upload):
    """The gate refuses gVCF; BCF must keep travelling."""
    resp = upload("sample.bcf", BCF_BYTES)

    assert resp.status_code == 200, resp.text
    assert resp.json()["file_type"] == "bcf"


def test_main_nf_still_has_no_bcf_branch():
    """The alias is load-bearing only while the pipeline lacks a branch of its own.

    If somebody adds one, the mapping stops being the honest answer and this test says
    so rather than leaving a silent rewrite of the user's input type in place.
    """
    text = MAIN_NF.read_text(encoding="utf-8")
    branches = set(re.findall(r"input_type\s*==\s*['\"]([a-z]+)['\"]", text))

    assert branches, "main.nf no longer branches on input_type; re-audit the alias"
    assert "bcf" not in branches
    assert "vcf" in branches


# ---------------------------------------------------------------------------
# /upload/inspect-header: the client's filename must not shape the temp path
# ---------------------------------------------------------------------------
@pytest.fixture
def captured_temp_suffix(client, monkeypatch):
    """POST to /upload/inspect-header and return the suffix NamedTemporaryFile got."""
    real_named_temporary_file = ur.tempfile.NamedTemporaryFile
    seen = []

    def _spy(*args, **kwargs):
        seen.append(kwargs.get("suffix", ""))
        return real_named_temporary_file(*args, **kwargs)

    monkeypatch.setattr(ur.tempfile, "NamedTemporaryFile", _spy)

    def _inspect(name):
        resp = client.post(
            "/upload/inspect-header",
            files={"file": (name, PLAIN_VCF_BYTES, "application/octet-stream")},
        )
        assert resp.status_code == 200, resp.text
        assert seen, "NamedTemporaryFile was never called"
        return seen[-1]

    return _inspect


@pytest.mark.parametrize(
    "name",
    [
        "a/b;c.vcf",
        "../../etc/passwd.vcf",
        "..\\..\\windows\\evil.vcf",
        "$(touch pwned).vcf",
        "*.vcf",
        "a b\tc.vcf",
    ],
)
def test_a_hostile_filename_never_reaches_the_temp_file_suffix(
    captured_temp_suffix, name
):
    suffix = captured_temp_suffix(name)

    assert "/" not in suffix and "\\" not in suffix
    assert ".." not in suffix
    for hostile in (";", "|", "&", "$", "`", "*", "?", "<", ">", " ", "\t", "'", '"'):
        assert hostile not in suffix, f"{hostile!r} survived in {suffix!r}"


def test_the_extension_still_survives_into_the_suffix(captured_temp_suffix):
    """The inspector branches on the extension, so sanitising must not eat it."""
    assert captured_temp_suffix("sample.vcf").endswith(".vcf")
    assert captured_temp_suffix("sample.vcf.gz").endswith(".vcf.gz")


def test_a_pathological_filename_still_yields_a_usable_suffix(captured_temp_suffix):
    """The fence around the sanitiser, not a pin on the fix.

    ``secure_filename("...")`` returns ``""``, which would leave the suffix as a bare
    ``"_"`` — the exact shape that broke app/main.py's variant-call route, where the
    empty name turned ``os.path.join(temp_dir, "")`` back into the directory.
    ``safe_upload_basename`` substitutes a generated name for that case; this says any
    future replacement has to keep doing so.
    """
    suffix = captured_temp_suffix("...")

    assert suffix.strip("_"), f"empty suffix for a pathological name: {suffix!r}"
