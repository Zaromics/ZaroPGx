"""``workflow["unsupported"]`` was computed, shown to the user, and then ignored.

``FileProcessor.determine_workflow`` sets ``unsupported`` at six sites. The upload router
only ever forwarded the flag into ``WorkflowOptions``; there was no ``if unsupported:``
anywhere, so the product told the user "Unsupported: <reason>" and analysed the file
anyway. What the user then saw depended on the category:

===============  ==============  =========================================================
category         is_provisional  what happens
===============  ==============  =========================================================
GRCh37 VCF       **yes**         analysed on its original coordinates, on purpose
FASTQ            no              main.nf has a ``fastq`` branch, but its first step POSTs
                                 to gatk-api ``/align-fastq``, which answers HTTP 501 (no
                                 aligner in the image) -> job FAILED. Refused up front.
23andMe          no              needs_conversion, but no conversion step and no main.nf
                                 branch exist -> job FAILED. Refused up front.
FASTA            no              main.nf: ``error "Unsupported input type"`` -> job FAILED
BED              no              same
unrecognised     no              same
===============  ==============  =========================================================

Everything but the GRCh37 VCF is refused up front — minutes of queueing replaced by an
immediate 400 carrying the reason the file processor already wrote. Only the GRCh37 VCF
runs, because ``is_provisional`` is this codebase's own flag for "analysed anyway,
provisionally" and the pipeline really does carry a VCF end to end.
"""

import uuid

import pytest

from app.api.models import FileType
from app.api.utils.file_processor import FileAnalysis as DcFileAnalysis

VCF_BYTES = (
    b"##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS\n"
)


@pytest.fixture
def upload(client, monkeypatch, tmp_path):
    """POST a file whose FileProcessor verdict is supplied by the caller."""
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

    def _post(file_type, **workflow_flags):
        async def _fake_process_files(files, reference_genome, **kwargs):
            stored = []
            for f in files:
                p = tmp_path / f.filename
                p.write_bytes(await f.read())
                stored.append(str(p))

            analysis = DcFileAnalysis(
                file_type=FileType(file_type),
                is_compressed=False,
                has_index=False,
                file_size=1,
                vcf_info=None,
                is_valid=True,
                validation_errors=[],
            )
            workflow = {
                "workflow_type": "genomic_analysis",
                "file_type": file_type,
                "reference": reference_genome or "hg38",
                "recommendations": [],
                "warnings": [],
                "unsupported": False,
                "unsupported_reason": None,
                "is_provisional": False,
            }
            workflow.update(workflow_flags)
            return {
                "success": True,
                "file_paths": stored,
                "file_analysis": analysis,
                "workflow": workflow,
            }

        monkeypatch.setattr(
            upload_router.file_processor, "process_files", _fake_process_files
        )
        return client.post(
            "/upload/genomic-data",
            files={"files": (f"sample.{file_type}", VCF_BYTES, "text/plain")},
            data={"reference_genome": "hg38"},
        )

    return _post


@pytest.mark.parametrize(
    "file_type,reason",
    [
        ("fasta", "FASTA files are reference genome files and cannot be analyzed."),
        ("bed", "BED files are typically downstream of sequencing / genotyping."),
        ("unknown", "Unrecognized file format: unknown."),
    ],
)
def test_unanalysable_uploads_are_refused_with_their_reason(upload, file_type, reason):
    """No pipeline branch, no provisional intent: refuse now instead of failing later."""
    resp = upload(file_type, unsupported=True, unsupported_reason=reason)

    assert resp.status_code == 400, resp.text
    assert reason in resp.json()["detail"]


def test_refusal_still_says_something_useful_without_a_reason(upload):
    resp = upload("bed", unsupported=True, unsupported_reason=None)

    assert resp.status_code == 400, resp.text
    assert "bed" in resp.json()["detail"].lower()


def test_provisional_inputs_are_still_analysed(upload):
    """A GRCh37 VCF is flagged unsupported *and* provisional: that is by design."""
    resp = upload(
        "vcf",
        unsupported=True,
        unsupported_reason="The uploaded VCF file is not aligned to GRCh38/hg38.",
        is_provisional=True,
    )

    assert resp.status_code == 200, resp.text


def test_23andme_is_refused(upload):
    """No converter and no main.nf branch: the job could only ever fail."""
    reason = "ZaroPGx cannot analyse 23andMe genotyping files."
    resp = upload("23andme", unsupported=True, unsupported_reason=reason)

    assert resp.status_code == 400, resp.text
    assert reason in resp.json()["detail"]


def test_a_provisional_flag_cannot_wave_an_unrunnable_type_past_the_gate(upload):
    """``is_provisional`` was set aspirationally on 23andMe and leaked it through.

    The flag is written by hand next to a reason string, so it records intent as
    readily as behaviour. It may only exempt an input type the pipeline can actually
    carry; on anything else it means nothing.
    """
    resp = upload(
        "23andme",
        unsupported=True,
        unsupported_reason="One day we will convert this.",
        is_provisional=True,
    )

    assert resp.status_code == 400, resp.text


def test_fastq_is_refused(upload):
    """main.nf's fastq branch dies on gatk-api's 501, so the job can only ever fail."""
    reason = "ZaroPGx cannot analyse FASTQ files."
    resp = upload("fastq", unsupported=True, unsupported_reason=reason)

    assert resp.status_code == 400, resp.text
    assert reason in resp.json()["detail"]


def test_supported_uploads_are_unaffected(upload):
    assert upload("vcf").status_code == 200
