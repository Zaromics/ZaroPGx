"""A BAM upload is indexed once, beside itself, before any step reads it.

Each step reads a BAM upload in place and is handed the index beside it when there is
a current one (app/utils/shared_input.py). A BAM uploaded alone had none: ZaroHLA and
PyPGx each indexed their own link to it, one after the other, and the read-phase
check, which builds no index, answered "not_run: the alignment has no index beside
it" for every BAM upload, so no report made from a BAM said anything about phase.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pysam
import pytest

import app.api.routes.upload_router as ur
from app.genome.alignment_index import index_bam
from app.utils.shared_input import current_index, link_shared_input

BAM = Path(__file__).resolve().parents[1] / "test_data" / "pgx_ngs_example.bam"


class _Job:
    def __init__(self, upload):
        self.id = "job-1"
        self.status = "running"
        self.job_metadata = {"file_paths": [str(upload)], "patient_id": "p1"}


class _JobService:
    def __init__(self, job):
        self.job = job
        self.logs = []
        self.steps = []

    def get_job(self, job_id):
        return self.job

    def update_job(self, job_id, update):
        if update.metadata is not None:
            self.job.job_metadata = update.metadata
        return self.job

    def update_job_step(self, job_id, step_name, update):
        self.steps.append((step_name, update))

    def log_job_event(self, job_id, log_data):
        self.logs.append((log_data.log_level, log_data.message))


def _upload(tmp_path) -> Path:
    """The fixture BAM as an upload, with no index beside it."""
    upload = tmp_path / "upload_0123abcd_sample.bam"
    shutil.copy(BAM, upload)
    return upload


def test_a_bam_upload_is_indexed_beside_itself(tmp_path):
    upload = _upload(tmp_path)
    job = _Job(upload)
    service = _JobService(job)

    index = asyncio.run(ur.index_bam_upload(service, "job-1", str(upload), "bam"))

    assert index == str(upload) + ".bai"
    # What every step is handed: shared_input copies a current index beside its link.
    assert current_index(upload) == (Path(index), ".bai")
    link = tmp_path / "step" / "linked.bam"
    link.parent.mkdir()
    link_shared_input(str(upload), link, roots=[tmp_path])
    with pysam.AlignmentFile(str(link)) as reads:
        assert reads.has_index()
        assert reads.count("chr10") > 0
    # Released with the upload when the job ends.
    assert job.job_metadata["file_paths"] == [str(upload), index]


@pytest.mark.parametrize("input_type", ["vcf", "cram", "sam", "fastq"])
def test_only_a_bam_is_indexed_here(tmp_path, input_type):
    """A CRAM, SAM or FASTQ becomes a BAM that gatk-api indexes; a VCF has no reads."""
    upload = _upload(tmp_path)
    service = _JobService(_Job(upload))

    assert (
        asyncio.run(ur.index_bam_upload(service, "job-1", str(upload), input_type))
        is None
    )
    assert current_index(upload) is None
    assert service.logs == []


def test_a_current_index_is_not_made_again(tmp_path):
    upload = _upload(tmp_path)
    existing = index_bam(upload)
    before = existing.stat().st_mtime_ns
    service = _JobService(_Job(upload))

    assert (
        asyncio.run(ur.index_bam_upload(service, "job-1", str(upload), "bam")) is None
    )
    assert existing.stat().st_mtime_ns == before
    assert service.job.job_metadata["file_paths"] == [str(upload)]


def test_an_unindexable_bam_is_left_as_it_was(tmp_path):
    """Not sorted by coordinate: no index, no partial one left to pass for current,
    and the log says why."""
    upload = tmp_path / "upload_0123abcd_unsorted.bam"
    with pysam.AlignmentFile(str(BAM)) as source:
        # The fixture's reads all start at one position: move each a little further
        # back than the one before, so the file runs backwards.
        reads = list(source)
        with pysam.AlignmentFile(str(upload), "wb", template=source) as out:
            for i, read in enumerate(reads):
                read.reference_start -= 10 * i
                out.write(read)
    service = _JobService(_Job(upload))

    assert (
        asyncio.run(ur.index_bam_upload(service, "job-1", str(upload), "bam")) is None
    )
    assert not Path(str(upload) + ".bai").exists()
    assert current_index(upload) is None
    level, message = service.logs[-1]
    assert level == ur.LogLevel.WARN
    assert "could not be indexed" in message
    assert service.job.job_metadata["file_paths"] == [str(upload)]


def test_index_bam_raises_on_a_file_that_is_not_a_bam(tmp_path):
    bogus = tmp_path / "upload_0123abcd_bogus.bam"
    bogus.write_bytes(b"not a bam")
    with pytest.raises(subprocess.CalledProcessError):
        index_bam(bogus)
    assert not Path(str(bogus) + ".bai").exists()


def test_the_pipeline_is_handed_the_indexed_upload(monkeypatch, tmp_path):
    upload = _upload(tmp_path)
    job = _Job(upload)
    service = _JobService(job)
    captured = {}
    monkeypatch.setattr(ur, "JobService", lambda db: service)
    monkeypatch.setattr(ur, "inspect_header", lambda path: {"sequences": []})
    monkeypatch.setattr(ur, "save_genomic_header", lambda db, path, ft, header: 1)
    monkeypatch.setattr(ur, "extract_raw_header_text", lambda path: None)

    def post(url, json=None, timeout=None):
        captured["payload"] = json
        captured["index_then"] = current_index(Path(json["input"]))
        return SimpleNamespace(
            status_code=200, text="", json=lambda: {"job_key": "nf-1"}
        )

    async def no_wait(*args, **kwargs):
        return None

    monkeypatch.setattr(ur.requests, "post", post)
    monkeypatch.setattr(ur, "wait_for_nextflow_completion", no_wait)

    asyncio.run(
        ur.process_file_nextflow_background(
            str(upload),
            "p1",
            "d1",
            {"file_type": "bam", "reference": "hg38"},
            None,
            None,
            "job-1",
        )
    )

    assert captured["payload"]["input"] == str(upload)
    # The index exists before the pipeline is asked to read the upload.
    assert captured["index_then"] == (Path(str(upload) + ".bai"), ".bai")
    completed = [u for name, u in service.steps if name == "header_analysis"][-1]
    assert completed.output_data["alignment_indexed"] is True


def test_a_bams_steps_are_weighed_as_they_now_run():
    """Header analysis holds the indexing; ZaroHLA, mtDNA and bam2vcf no longer index
    the file themselves. Other inputs keep the table's figures."""
    from app.services.workflow_progress_calculator import WorkflowProgressCalculator

    calc = WorkflowProgressCalculator()
    bam, cram = {"file_type": "bam"}, {"file_type": "cram"}
    assert calc._expected_seconds("header_analysis", bam) == 135
    assert calc._expected_seconds("hla_typing", bam) == 55
    assert calc._expected_seconds("header_analysis", cram) == 2
    assert calc._expected_seconds("hla_typing", cram) == 245
