"""One story about which uploads ZaroPGx accepts, told at the surface that matters.

FASTQ used to be advertised in the UI and the docs, accepted by
``POST /upload/genomic-data``, given a job — and then killed minutes later, because
``docker/gatk-api``'s ``/align-fastq`` answers HTTP 501 (the image ships no aligner) and
``pipelines/pgx/main.nf``'s curls carry ``--fail-with-body``. The upload gate exempted
FASTQ on the reasoning that ``main.nf`` has a ``fastq`` branch; a branch existing is not
the same as the branch working.

The decision taken here is to refuse FASTQ at upload. Implementing alignment is the only
other honest option and it is a different, much larger piece of work; until it exists,
telling the user *now* — with the fix they can act on — beats telling them in twenty
minutes with a Nextflow traceback.

Two behaviours are pinned:

* a FASTQ upload is refused **before** any patient or job row is created, with copy that
  names the reason and the way out;
* ``process_files`` analyses ``files[0]`` only, which silently discarded the second mate
  of a paired-read upload. FASTQ is now refused outright, and for every other format the
  discard is said out loud in the workflow warnings the UI renders.

A 23andMe upload had the same shape of problem for a different reason: ``FileProcessor``
set ``is_provisional`` next to ``unsupported``, and the gate read that as "analysed
anyway, provisionally" and let it through — but there is no converter and no ``23andme``
branch in ``main.nf``, so it failed at the pipeline like the FASTA and BED cases the gate
was built to catch. The flag was aspirational there, not descriptive.

Everything here drives the real endpoint against the real ``FileProcessor`` and asserts
on the emitted strings, not on source text.
"""

import uuid

import pytest

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
    detail = upload(("reads.fastq", FASTQ_BYTES)).json()["detail"]
    lowered = detail.lower()

    # the reason
    assert "fastq" in lowered
    assert "aligner" in lowered
    # the way out: align yourself, upload the aligned file
    assert "align" in lowered
    for accepted in ("bam", "cram", "sam"):
        assert accepted in lowered


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


def test_paired_fastq_is_refused_rather_than_analysed_as_one_mate(upload):
    """``process_files`` reads files[0] only: a mate pair was half-analysed, silently."""
    resp = upload(("reads_R1.fastq", FASTQ_BYTES), ("reads_R2.fastq", FASTQ_BYTES))

    assert resp.status_code == 400, resp.text
    assert "paired" in resp.json()["detail"].lower()
    assert upload.created_patients == []


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
# 23andMe: caught by the gate, without disturbing the GRCh37 case
# ---------------------------------------------------------------------------
TWENTYTHREE_AND_ME_BYTES = (
    b"# This data file generated by 23andMe at: Sun Jan 1 00:00:00 2023\n"
    b"# rsid\tchromosome\tposition\tgenotype\n"
    b"rs4477212\t1\t82154\tAA\n"
)


def test_23andme_upload_is_refused_before_a_job_exists(upload):
    """No converter and no main.nf branch: the job could only ever fail."""
    resp = upload(("genome.txt", TWENTYTHREE_AND_ME_BYTES))

    assert resp.status_code == 400, resp.text
    assert "23andme" in resp.json()["detail"].lower()
    assert upload.created_patients == []


def test_23andme_refusal_names_the_missing_piece_and_an_accepted_input(upload):
    detail = upload(("genome.txt", TWENTYTHREE_AND_ME_BYTES)).json()["detail"].lower()

    assert "vcf" in detail  # what is missing: the conversion to VCF
    assert "not implemented" in detail or "not yet implemented" in detail


def test_23andme_workflow_is_not_flagged_provisional():
    """``is_provisional`` means "analysed anyway, provisionally". Nothing is analysed."""
    from app.api.models import FileType
    from app.api.utils.file_processor import FileAnalysis, FileProcessor

    workflow = FileProcessor(temp_dir="/tmp").determine_workflow(
        FileAnalysis(
            file_type=FileType.TWENTYTHREE_AND_ME,
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


def test_grch37_vcf_is_still_analysed_provisionally():
    """The gate change must not touch the one input that is genuinely provisional.

    The router refuses on the workflow dict, so the dict the real GRCh37 branch produces
    is what decides — assert on it directly, then on the gate's verdict for it.
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

    assert workflow["unsupported"] is True
    assert workflow["is_provisional"] is True
    assert _unanalysable_upload_reason(workflow) is None


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
