"""A job's uploaded input is removed when the job ends.

An upload is stored at /data/uploads/upload_<random>_<name> and recorded in the
job's metadata as ``file_paths``. Only a cancellation deleted it (the Nextflow
runner removes its input on /cancel); the completion cleanup looked for
/data/uploads/{patient_id}, a directory uploads are never written to, and a failed
job had no cleanup. Each completed or failed 30x NA12878 run left a 41 GB BAM
behind.
"""

from __future__ import annotations

import pytest

from app.api.models import JobCreate, JobStatus, JobUpdate
from app.services.cleanup_service import cleanup_service


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    """Point the shared cleanup service at a scratch /data."""
    monkeypatch.setattr(cleanup_service, "data_dir", tmp_path)
    monkeypatch.setattr(cleanup_service, "temp_dir", tmp_path / "tmp")
    monkeypatch.setattr(cleanup_service, "uploads_dir", tmp_path / "uploads")
    monkeypatch.delenv("KEEP_UPLOADS", raising=False)
    (tmp_path / "uploads").mkdir()
    return tmp_path / "uploads"


def _job_with_upload(job_service, uploads):
    """A job whose upload is a FASTQ pair, as file_processor records one."""
    mates = [uploads / "upload_ab12_R1.fastq.gz", uploads / "upload_ab12_R2.fastq.gz"]
    for mate in mates:
        mate.write_bytes(b"@read\nACGT\n+\nIIII\n")
    job = job_service.create_job(
        JobCreate(
            workflow_type="genomic_analysis",
            name="upload-retention",
            metadata={"file_paths": [str(m) for m in mates]},
        )
    )
    return job.id, mates


@pytest.mark.parametrize(
    "status", [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED]
)
def test_an_ended_job_loses_its_upload(job_service, uploads, status):
    job_id, mates = _job_with_upload(job_service, uploads)
    job_service.update_job(job_id, JobUpdate(status=status))
    assert not any(m.exists() for m in mates)


def test_a_running_job_keeps_its_upload(job_service, uploads):
    job_id, mates = _job_with_upload(job_service, uploads)
    job_service.update_job(job_id, JobUpdate(status=JobStatus.RUNNING))
    assert all(m.exists() for m in mates)


def test_keep_uploads_keeps_them(job_service, uploads, monkeypatch):
    monkeypatch.setenv("KEEP_UPLOADS", "true")
    job_id, mates = _job_with_upload(job_service, uploads)
    job_service.update_job(job_id, JobUpdate(status=JobStatus.COMPLETED))
    assert all(m.exists() for m in mates)


def test_only_files_inside_the_uploads_directory_are_touched(uploads, tmp_path):
    outside = tmp_path / "elsewhere.bam"
    outside.write_bytes(b"x")
    link = uploads / "upload_cd34_link.bam"
    link.symlink_to(outside)
    subdir = uploads / "upload_dir"
    subdir.mkdir()

    removed = cleanup_service.discard_job_uploads(
        {
            "file_paths": [
                str(outside),
                str(link),  # resolves outside: not followed
                str(subdir),
                str(uploads),
                str(uploads / "upload_gone.vcf"),  # already gone
                str(uploads / ".." / "elsewhere.bam"),
            ]
        }
    )

    assert removed == []
    assert outside.exists() and link.is_symlink() and subdir.is_dir()


def test_a_job_without_file_paths_is_a_no_op(uploads):
    assert cleanup_service.discard_job_uploads({}) == []
    assert cleanup_service.discard_job_uploads(None) == []


def test_completion_cleanup_removes_the_mtdna_working_directory(uploads, tmp_path):
    """The mtdna sidecar works in DATA_DIR/temp/mtdna/<job>; a worker killed
    mid-request (WORKER TIMEOUT) left a 41 GB copy there for good."""
    work = tmp_path / "temp" / "mtdna" / "job-1"
    work.mkdir(parents=True)
    (work / "upload_ab12_NA12878.final.bam").write_bytes(b"x")
    cleanup_service.cleanup_job_files(job_id="job-1", patient_id="patient-1")
    assert not work.exists()
