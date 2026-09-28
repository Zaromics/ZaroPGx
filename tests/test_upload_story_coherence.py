"""One story about which uploads ZaroPGx accepts, told at the surface that matters.

FASTQ used to be advertised in the UI and the docs, accepted by
``POST /upload/genomic-data``, given a job — and then killed minutes later, because
``docker/gatk-api``'s ``/align-fastq`` answered HTTP 501 (on the stated grounds that the
image shipped no aligner — see below, it did) and ``pipelines/pgx/main.nf``'s curls carry
``--fail-with-body``. The upload gate exempted
FASTQ on the reasoning that ``main.nf`` has a ``fastq`` branch; a branch existing is not
the same as the branch working.

The decision taken then was to refuse FASTQ at upload, on the reasoning that implementing
alignment was the only other honest option and was a different, much larger piece of
work. **That reasoning rested on a false premise**, found 2026-09-20: GATK has bundled
bwa-mem as a JNI native (``libbwa.Linux.so``, inside the fat jar) since GATK 4, so an
aligner was in the stack the whole time and only gatk-api's ``/align-fastq`` endpoint
was missing. It is now implemented, against a PyPGx-compliant GRCh38, so FASTQ is
accepted.

The refusal therefore NARROWED rather than disappearing, and what survives of it is what
this module now pins. What is still refused is a property of the file rather than a
missing feature:

* **an undetectable sequencing platform.** ``@RG PL:`` is read by GATK and PyPGx, a FASTQ
  states it nowhere, and it is detected from read-name structure corroborated against read
  length. An SRA re-export strips that naming. ``PL:ILLUMINA`` is the likeliest answer and
  must still never be the automatic one — a guessed platform writes an unverified claim
  about the sample into everything downstream.
* **above the 20 GB cap.** Alignment writes the reads out three times on one machine, so a
  whole-genome set needs several times its size in scratch disk and many hours.
* **two files that are not one pair.** A mate pair is accepted and travels as ``input2``;
  two unrelated read sets, or one file twice, are refused rather than aligned as mates.

For every other format the ``files[0]`` discard is still said out loud in the workflow
warnings the UI renders, rather than refused.

A 23andMe upload had the same shape of problem for a different reason: ``FileProcessor``
set ``is_provisional`` next to ``unsupported``, and the gate read that as "analysed
anyway, provisionally" and let it through — but there is no converter and no ``23andme``
branch in ``main.nf``, so it failed at the pipeline like the FASTA and BED cases the gate
was built to catch. The flag was aspirational there, not descriptive.

Its copy was aspirational too, and that was fixed separately (2026-08-31): "converted to
VCF first, and that conversion is not implemented yet" promised a feature by describing
its absence, and misnamed the problem besides. The conversion is a one-liner; the reason
consumer arrays are refused is that ZaroPGx runs PyPGx alongside PharmCAT and hands
PyPGx's calls over as outside calls, and PyPGx reads a position the chip does not carry
as homozygous reference rather than as a no-call. AncestryDNA joined 23andMe in the same
refusal at the same time — it was previously refused as "Unrecognized file format".

Everything here drives the real endpoint against the real ``FileProcessor`` and asserts
on the emitted strings, not on source text.
"""

import json
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = REPO_ROOT / "app" / "templates" / "index.html"
RENDERER = Path(__file__).resolve().parent / "js" / "render_workflow_panel.js"

FASTQ_BYTES = b"@read1\nACGTACGTAC\n+\nIIIIIIIIII\n"

# BAM, not VCF, for the accepted-upload cases: FileProcessor's VCF path calls the header
# inspector, which needs pysam or bcftools and has neither on a bare host, so every VCF
# there reads as zero-sample and is refused by the one-sample policy. BAM is typed from
# its extension and carries no such policy, so these tests exercise the real code path
# rather than a host-dependent one.
BAM_BYTES = b"BAM\x01" + b"\x00" * 32


@pytest.fixture
def upload(client, monkeypatch, tmp_path):
    """POST real bytes at the real endpoint, through the real FileProcessor.

    Only the things that would reach Postgres, the filesystem outside ``tmp_path`` or a
    sibling container are replaced. The file-type verdict, the workflow dict and the
    refusal all come from production code.
    """
    from app.api.routes import upload_router

    monkeypatch.setattr(upload_router.file_processor, "temp_dir", tmp_path / "uploads")

    created_patients = []
    monkeypatch.setattr(
        upload_router,
        "create_patient",
        lambda db, identifier: (created_patients.append(identifier) or uuid.uuid4()),
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

    def _post(*uploaded):
        return client.post(
            "/upload/genomic-data",
            files=[
                ("files", (name, payload, "application/octet-stream"))
                for name, payload in uploaded
            ],
            data={"reference_genome": "hg38"},
        )

    _post.created_patients = created_patients
    return _post


# ---------------------------------------------------------------------------
# FASTQ: refused, with copy the user can act on
# ---------------------------------------------------------------------------
def test_fastq_upload_is_refused_before_a_job_exists(upload):
    resp = upload(("reads.fastq", FASTQ_BYTES))

    assert resp.status_code == 400, resp.text
    assert upload.created_patients == [], "refusal must precede any patient/job row"


def test_fastq_refusal_says_why_and_what_to_do_instead(upload):
    """FASTQ_BYTES has the read name ``@read1`` -- no instrument structure at all --
    so this is the undetectable-platform refusal, not the old no-aligner one.

    The copy names the platform rather than talking vaguely about "the format",
    because the fix available to the user is specific: upload the original run files,
    or align the reads themselves.
    """
    detail = upload(("reads.fastq", FASTQ_BYTES)).json()["detail"]
    lowered = detail.lower()

    # the reason
    assert "fastq" in lowered
    assert "platform" in lowered
    # the way out: align yourself, upload the aligned file
    assert "align" in lowered
    for accepted in ("bam", "cram", "sam"):
        assert accepted in lowered


def test_the_fastq_refusal_no_longer_claims_there_is_no_aligner(upload):
    """The old reason is now false, and a false reason is worse than a vague one:
    it sends the user to align a file ZaroPGx would have aligned for them."""
    detail = upload(("reads.fastq", FASTQ_BYTES)).json()["detail"].lower()

    assert "ships no aligner" not in detail
    assert "no aligner" not in detail


@pytest.mark.parametrize(
    "promise",
    [
        "does not support this workflow yet",
        "once support reaches completion",
        "can be uploaded as inputs",
        "is being reviewed",
    ],
)
def test_fastq_refusal_carries_no_leftover_promise(upload, promise):
    """The old copy invited the user to come back with a pair of FASTQs."""
    detail = upload(("reads.fastq", FASTQ_BYTES)).json()["detail"]

    assert promise not in detail.lower()


def test_gzipped_fastq_is_refused_too(upload):
    """The extension sniffer routes .fq.gz to FileType.FASTQ; the gate must follow."""
    import gzip

    resp = upload(("reads.fq.gz", gzip.compress(FASTQ_BYTES)))

    assert resp.status_code == 400, resp.text
    assert "fastq" in resp.json()["detail"].lower()


def _dnbseq_mate(mate: int, n: int = 40, offset: int = 0) -> bytes:
    """A DNBSEQ FASTQ mate as the instrument writes it: /1 or /2 on every name."""
    return b"".join(
        b"@FP200007900L1C%03dR038%08d/%d\n%s\n+\n%s\n"
        % (i, i + offset, mate, (b"ACGT" if mate == 1 else b"TGCA") * 25, b"I" * 100)
        for i in range(1, n + 1)
    )


def _capture_workflow(monkeypatch):
    """Record the workflow the upload hands to the pipeline, instead of discarding it."""
    from app.api.routes import upload_router

    seen = {}

    async def capture(file_path, patient_id, data_id, workflow, *args, **kwargs):
        seen["file_path"] = file_path
        seen["workflow"] = workflow

    monkeypatch.setattr(
        upload_router, "process_file_nextflow_background_with_db", capture
    )
    return seen


def test_a_mate_pair_is_aligned_together_not_half_analysed(upload, monkeypatch):
    """``process_files`` used to read files[0] only, so a mate pair was half-analysed
    silently. Both mates now travel to the pipeline: R1 as the input, R2 as input2."""
    seen = _capture_workflow(monkeypatch)
    resp = upload(
        ("reads_R1.fastq", _dnbseq_mate(1)), ("reads_R2.fastq", _dnbseq_mate(2))
    )

    assert resp.status_code == 200, resp.text
    assert seen["workflow"].get("input2"), "the second mate never reached the pipeline"
    assert seen["workflow"]["input2"] != seen["file_path"]


def test_a_mate_pair_is_not_reported_as_an_ignored_file(upload, monkeypatch):
    """R2 is used, so the generic "one data file per job ... was ignored" warning
    would be false."""
    _capture_workflow(monkeypatch)
    resp = upload(
        ("reads_R1.fastq", _dnbseq_mate(1)), ("reads_R2.fastq", _dnbseq_mate(2))
    )

    warnings = " ".join(resp.json()["workflow"]["options"]["warnings"]).lower()
    assert "ignored" not in warnings


def _warnings(resp) -> str:
    return " ".join(resp.json()["workflow"]["options"]["warnings"]).lower()


def test_one_mate_uploaded_alone_is_aligned_but_warned(upload, monkeypatch):
    """R1 without R2 still aligns (single-end), but misses hets in paralogous genes:
    against a WGS CRAM of one sample, R1 alone lost CYP2B6 785A>G and NAT2 590G>A,
    which the pair called. Accepted, and the user is told what the missing mate costs.
    """
    seen = _capture_workflow(monkeypatch)
    resp = upload(("reads_R1_001.fastq", _dnbseq_mate(1)))

    assert resp.status_code == 200, resp.text
    assert not seen["workflow"].get("input2")
    warnings = _warnings(resp)
    assert "one mate of a paired-end run" in warnings
    assert "upload r1 and r2 together" in warnings


def test_a_single_end_fastq_is_not_warned_about_a_mate(upload, monkeypatch):
    _capture_workflow(monkeypatch)
    resp = upload(("reads.fastq", _dnbseq_mate(1)))

    assert resp.status_code == 200, resp.text
    assert "paired-end" not in _warnings(resp)


def test_a_mate_pair_is_not_warned_about_a_missing_mate(upload, monkeypatch):
    _capture_workflow(monkeypatch)
    resp = upload(
        ("reads_R1.fastq", _dnbseq_mate(1)), ("reads_R2.fastq", _dnbseq_mate(2))
    )

    assert "one mate of a paired-end run" not in _warnings(resp)


def test_two_fastqs_that_are_not_mates_are_refused(upload):
    """Two unrelated read sets uploaded together must not be aligned as pairs --
    that would invent mate relationships between reads that never shared a fragment."""
    resp = upload(
        ("a_R1.fastq", _dnbseq_mate(1)),
        ("b_R2.fastq", _dnbseq_mate(2, offset=500000)),
    )

    assert resp.status_code == 400, resp.text
    assert "not mates" in resp.json()["detail"]
    assert upload.created_patients == []


def test_three_fastqs_are_refused(upload, monkeypatch):
    """A pair is R1 and R2; a third file has no place in one run."""
    # Real DNBSEQ reads, so the primary passes platform detection and the upload
    # reaches the pairing step, where three data files is refused on the count alone.
    resp = upload(
        ("r1.fastq", _dnbseq_mate(1)),
        ("r2.fastq", _dnbseq_mate(2)),
        ("r3.fastq", _dnbseq_mate(1)),
    )
    assert resp.status_code == 400, resp.text
    assert "3 data files" in resp.json()["detail"]


def test_a_refused_pair_leaves_no_bytes_behind(upload, tmp_path):
    """The mate is saved before it is checked, so it must be cleaned on refusal too."""
    uploads = tmp_path / "uploads"
    upload(
        ("a_R1.fastq", _dnbseq_mate(1)),
        ("b_R2.fastq", _dnbseq_mate(2, offset=500000)),
    )
    assert list(uploads.glob("*")) == []


def test_a_refused_upload_leaves_no_bytes_behind(upload, tmp_path):
    """No patient row means the cleanup service will never sweep these files."""
    uploads = tmp_path / "uploads"

    assert upload(("reads.fastq", FASTQ_BYTES)).status_code == 400
    assert list(uploads.glob("*")) == []


def test_an_accepted_upload_keeps_its_bytes(upload, tmp_path):
    """The negative control: the pipeline is handed this path and must find a file."""
    uploads = tmp_path / "uploads"

    assert upload(("sample.bam", BAM_BYTES)).status_code == 200
    names = [p.name for p in uploads.glob("*")]
    assert len(names) == 1
    assert re.fullmatch(r"upload_[0-9a-f]{12}_sample\.bam", names[0]), names


def test_identically_named_uploads_never_share_a_path(upload, tmp_path):
    """The saved path is the pipeline's --input, read when the job runs. Named from
    the client's filename alone, a second "sample.bam" rewrote the first while its
    job was queued -- one patient's report carrying another patient's data."""
    uploads = tmp_path / "uploads"

    assert upload(("sample.bam", BAM_BYTES)).status_code == 200
    assert upload(("sample.bam", BAM_BYTES + b"other")).status_code == 200
    saved = sorted(uploads.glob("*"))
    assert len(saved) == 2
    assert {p.read_bytes() for p in saved} == {BAM_BYTES, BAM_BYTES + b"other"}


def test_a_mate_pair_shares_its_upload_id_and_never_a_path(upload, monkeypatch):
    seen = _capture_workflow(monkeypatch)
    upload(("reads_R1.fastq", _dnbseq_mate(1)), ("reads_R2.fastq", _dnbseq_mate(2)))

    r1 = Path(seen["file_path"]).name
    r2 = Path(seen["workflow"]["input2"]).name
    upload_id = r1.split("_")[1]
    assert r2.startswith(f"upload_{upload_id}_mate2_")


def test_an_error_while_pairing_leaves_no_bytes_behind(upload, tmp_path, monkeypatch):
    """An exception is not a refusal: it returns through process_files' error path,
    which the router turns into a 400 without discarding anything. Found by review:
    a truncated gzip mate left both files in the upload root, which nothing sweeps."""
    from app.api.utils import file_processor

    def boom(*args, **kwargs):
        raise RuntimeError("unreadable mate")

    monkeypatch.setattr(file_processor, "mate_names_agree", boom)
    resp = upload(("a_R1.fastq", _dnbseq_mate(1)), ("a_R2.fastq", _dnbseq_mate(2)))

    assert resp.status_code == 400, resp.text
    assert list((tmp_path / "uploads").glob("*")) == []


def test_the_same_file_twice_is_refused_as_a_pair(upload, tmp_path):
    resp = upload(("a_R1.fastq", _dnbseq_mate(1)), ("a_R2.fastq", _dnbseq_mate(1)))

    assert resp.status_code == 400, resp.text
    assert "same reads" in resp.json()["detail"]
    assert list((tmp_path / "uploads").glob("*")) == []


def test_fastq_workflow_plans_no_steps_it_cannot_run():
    """No needs_* flag may promise an alignment this stack cannot perform."""
    from app.api.models import FileType
    from app.api.utils.file_processor import FileAnalysis, FileProcessor

    workflow = FileProcessor(temp_dir="/tmp").determine_workflow(
        FileAnalysis(
            file_type=FileType.FASTQ,
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


# ---------------------------------------------------------------------------
# Consumer arrays: caught by the gate, without disturbing the GRCh37 case
# ---------------------------------------------------------------------------
TWENTYTHREE_AND_ME_BYTES = (
    b"# This data file generated by 23andMe at: Sun Jan 1 00:00:00 2023\n"
    b"# rsid\tchromosome\tposition\tgenotype\n"
    b"rs4477212\t1\t82154\tAA\n"
)


def test_23andme_upload_is_refused_before_a_job_exists(upload):
    """No main.nf branch, and no intention of building one: see the refusal copy."""
    resp = upload(("genome.txt", TWENTYTHREE_AND_ME_BYTES))

    assert resp.status_code == 400, resp.text
    assert "23andme" in resp.json()["detail"].lower()
    assert upload.created_patients == []


def test_23andme_refusal_gives_the_real_reason_not_a_missing_converter(upload):
    """The old copy said the conversion "is not implemented yet", which promised
    the feature by describing its absence -- and was not the reason anyway. The
    conversion is trivial; what is wrong is that PyPGx reads the positions a chip
    lacks as homozygous reference and its call overrides PharmCAT's no-call."""
    detail = upload(("genome.txt", TWENTYTHREE_AND_ME_BYTES)).json()["detail"].lower()

    assert "not implemented" not in detail
    assert "in development" in detail  # refused for now, support to come
    assert "cyp2d6" in detail  # the gene the coverage gap actually ruins
    assert "homozygous reference" in detail  # the mechanism
    # And an accepted input to go and get instead.
    for accepted in ("vcf", "bam", "cram", "sam"):
        assert accepted in detail


def _array_workflow(file_type):
    from app.api.utils.file_processor import FileAnalysis, FileProcessor

    return FileProcessor(temp_dir="/tmp").determine_workflow(
        FileAnalysis(
            file_type=file_type,
            is_compressed=False,
            has_index=False,
            file_size=1,
            vcf_info=None,
            is_valid=True,
            validation_errors=[],
        )
    )


@pytest.mark.parametrize("vendor", ["TWENTYTHREE_AND_ME", "ANCESTRY_DNA"])
def test_array_workflow_is_not_flagged_provisional(vendor):
    """``is_provisional`` means "analysed anyway, provisionally". Nothing is analysed."""
    from app.api.models import FileType

    workflow = _array_workflow(getattr(FileType, vendor))

    assert workflow["unsupported"] is True
    assert workflow["is_provisional"] is False


@pytest.mark.parametrize("vendor", ["TWENTYTHREE_AND_ME", "ANCESTRY_DNA"])
def test_a_refused_array_does_not_plan_a_bcf_conversion(vendor):
    """``needs_conversion`` mints a real bcf_to_vcf step (workflow_registry).

    The 23andMe branch used to set it for a conversion that did not exist, back
    when the flag drew nothing. Now it would plan a bcftools re-encode of a file
    that is not a BCF and is not being analysed -- so it must stay False, along
    with every other needs_* flag on a refused input.
    """
    from app.api.models import FileType

    workflow = _array_workflow(getattr(FileType, vendor))

    for flag in (
        "needs_conversion",
        "needs_gatk",
        "needs_alignment",
        "needs_hla",
        "needs_pypgx",
        "needs_pypgx_bam2vcf",
        "needs_mtdna",
        "needs_liftover",
    ):
        assert workflow[flag] is False, flag


def test_grch37_vcf_is_analysed_supported_via_liftover():
    """The gate change must not touch the input the pipeline genuinely converts.

    A GRCh37 VCF is now lifted over to GRCh38 (GATK Picard LiftoverVcf via gatk-api)
    before analysis, so it is supported outright: not unsupported, not provisional,
    and never refused. The router refuses on the workflow dict, so the dict the real
    GRCh37 branch produces is what decides — assert on it directly, then on the
    gate's verdict for it.
    """
    from app.api.models import FileType, SequencingProfile, VCFHeaderInfo
    from app.api.routes.upload_router import _unanalysable_upload_reason
    from app.api.utils.file_processor import FileAnalysis, FileProcessor

    workflow = FileProcessor(temp_dir="/tmp").determine_workflow(
        FileAnalysis(
            file_type=FileType.VCF,
            is_compressed=False,
            has_index=True,
            file_size=1,
            vcf_info=VCFHeaderInfo(
                reference_genome="GRCh37",
                sequencing_platform="Illumina",
                sequencing_profile=SequencingProfile.WGS,
                has_index=True,
                is_bgzipped=False,
                contigs=["chr1"],
                sample_count=1,
                variant_count=None,
            ),
            is_valid=True,
            validation_errors=[],
        )
    )
    workflow["file_type"] = FileType.VCF.value

    assert workflow["unsupported"] is False
    assert workflow["is_provisional"] is False
    assert workflow["needs_liftover"] is True
    assert _unanalysable_upload_reason(workflow) is None


# ---------------------------------------------------------------------------
# The pre-upload preview must not draw a workflow the upload would refuse
# ---------------------------------------------------------------------------
def _inspect(client, name, payload):
    return client.post(
        "/upload/inspect-header",
        files={"file": (name, payload, "application/octet-stream")},
    )


def test_preview_marks_a_fastq_as_refused(client):
    """The header preview and the upload gate must reach the same verdict."""
    resp = _inspect(client, "reads.fastq", FASTQ_BYTES)

    assert resp.status_code == 200, resp.text
    workflow = resp.json()["compat"]["workflow"]
    assert workflow["refused"] is True
    assert "fastq" in (workflow["refusal_reason"] or "").lower()


def test_preview_does_not_mark_an_accepted_file_refused(client):
    resp = _inspect(client, "sample.bam", BAM_BYTES)

    assert resp.status_code == 200, resp.text
    assert resp.json()["compat"]["workflow"]["refused"] is False


def test_preview_of_a_grch37_vcf_is_not_refused():
    """A GRCh37 VCF is supported (lifted over to GRCh38), so no verdict may refuse it.

    It is not even `unsupported` any more — the liftover made it a first-class
    input — and the gate must agree. Driven through the gate rather than the
    endpoint, because FileProcessor's VCF path needs pysam or bcftools to read a
    sample count and neither is on a bare host.
    """
    from app.api.models import FileType, SequencingProfile, VCFHeaderInfo
    from app.api.routes.upload_router import _unanalysable_upload_reason
    from app.api.utils.file_processor import FileAnalysis, FileProcessor

    workflow = FileProcessor(temp_dir="/tmp").determine_workflow(
        FileAnalysis(
            file_type=FileType.VCF,
            is_compressed=False,
            has_index=True,
            file_size=1,
            vcf_info=VCFHeaderInfo(
                reference_genome="GRCh37",
                sequencing_platform="Illumina",
                sequencing_profile=SequencingProfile.WGS,
                has_index=True,
                is_bgzipped=False,
                contigs=["chr1"],
                sample_count=1,
                variant_count=None,
            ),
            is_valid=True,
            validation_errors=[],
        )
    )
    workflow["file_type"] = FileType.VCF.value

    assert workflow["unsupported"] is False
    assert workflow["needs_liftover"] is True
    assert _unanalysable_upload_reason(workflow) is None


def _render_panel(payload: dict, tmp_path) -> dict:
    """Execute index.html's real inline <script> against ``payload`` in Node.

    Same driver as tests/test_ui_workflow_flag_reads.py: asserting on template source
    text is weak evidence, so the panel is actually rendered and its HTML inspected.
    """
    payload_path = tmp_path / "panel_payload.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    proc = subprocess.run(
        [shutil.which("node"), str(RENDERER), str(TEMPLATE), str(payload_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    return json.loads(proc.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_panel_draws_no_workflow_for_a_refused_file(tmp_path):
    """A red "cannot analyse" alert beside a full pipeline plan said both at once."""
    rendered = _render_panel(
        {
            "file_type": "fastq",
            "analysis_info": {"file_type": "fastq"},
            "workflow": {
                "recommendations": [],
                "warnings": [],
                "unsupported": True,
                "unsupported_reason": "ZaroPGx cannot analyse FASTQ files.",
                "refused": True,
                "refusal_reason": "ZaroPGx cannot analyse FASTQ files.",
            },
        },
        tmp_path,
    )

    planned = rendered["elements"]["plannedWorkflowInfo"]["html"]
    assert "No workflow will run" in planned
    assert "ZaroPGx cannot analyse FASTQ files." in planned
    for step in ("GATK Processing", "PyPGx Star Allele Calling", "PharmCAT Analysis"):
        assert step not in planned, step


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_panel_still_draws_the_workflow_for_an_accepted_file(tmp_path):
    """The negative control: refusing must not blank the plan for everything else."""
    rendered = _render_panel(
        {
            "file_type": "vcf",
            "analysis_info": {"file_type": "vcf"},
            "workflow": {
                "options": {
                    "needs_pypgx": True,
                    "recommendations": [],
                    "warnings": [],
                    # `unsupported` alone must not blank the plan: only `refused`
                    # (the gate's own verdict, absent here) does that. No input
                    # class sets unsupported+provisional any more — T2T-CHM13 was
                    # the last, and it is refused outright as of 2026-08-31 — but
                    # the panel must still key off `refused`, because reading
                    # `unsupported` instead would blank the plan for anything the
                    # gate lets through.
                    "unsupported": True,
                    "unsupported_reason": "Flagged, but the gate let it through.",
                    "is_provisional": True,
                }
            },
        },
        tmp_path,
    )

    planned = rendered["elements"]["plannedWorkflowInfo"]["html"]
    assert "No workflow will run" not in planned
    assert "PyPGx Star Allele Calling" in planned
    assert "PharmCAT Analysis" in planned


# ---------------------------------------------------------------------------
# "only files[0] is analysed" must never again be silent
# ---------------------------------------------------------------------------
def test_extra_data_file_is_reported_as_ignored(upload):
    resp = upload(("first.bam", BAM_BYTES), ("second.bam", BAM_BYTES))

    assert resp.status_code == 200, resp.text
    warnings = " ".join(resp.json()["workflow"]["options"]["warnings"])
    assert "second.bam" in warnings
    assert "ignored" in warnings.lower()
    assert "first.bam" in warnings


def test_the_ignored_warning_names_the_file_the_user_chose(upload):
    """Sanitising the name here would report a file the user never selected."""
    resp = upload(("sample.bam", BAM_BYTES), ("my run (2).bam", BAM_BYTES))

    assert resp.status_code == 200, resp.text
    warnings = " ".join(resp.json()["workflow"]["options"]["warnings"])
    assert "my run (2).bam" in warnings


def test_a_hostile_filename_cannot_inject_markup_into_the_warning(upload):
    """The panel assigns warnings with innerHTML, so these strings must be inert."""
    resp = upload(
        ("sample.bam", BAM_BYTES), ("<img src=x onerror=alert(1)>.bam", BAM_BYTES)
    )

    assert resp.status_code == 200, resp.text
    warnings = " ".join(resp.json()["workflow"]["options"]["warnings"])
    assert "<img" not in warnings
    assert "&lt;img" in warnings


def test_an_index_file_listed_first_does_not_hide_the_data_file(upload):
    """A browser FileList is often alphabetical, so sample.bai precedes sample.bam."""
    resp = upload(("sample.bai", b"BAI\x01"), ("sample.bam", BAM_BYTES))

    assert resp.status_code == 200, resp.text
    assert resp.json()["file_type"] == "bam"
    warnings = " ".join(resp.json()["workflow"]["options"]["warnings"])
    assert "ignored" not in warnings.lower()


def test_a_companion_index_file_is_not_reported_as_ignored(upload):
    """An index alongside the data file is the documented two-file upload, not a loss."""
    resp = upload(("sample.bam", BAM_BYTES), ("sample.bam.bai", b"BAI\x01"))

    assert resp.status_code == 200, resp.text
    warnings = " ".join(resp.json()["workflow"]["options"]["warnings"])
    assert "ignored" not in warnings.lower()


def test_single_file_upload_gains_no_ignored_warning(upload):
    resp = upload(("sample.bam", BAM_BYTES))

    assert resp.status_code == 200, resp.text
    warnings = " ".join(resp.json()["workflow"]["options"]["warnings"])
    assert "ignored" not in warnings.lower()
