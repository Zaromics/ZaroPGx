"""The upload's temporary copy is released once the file is saved, not at job end.

Starlette spools each uploaded part to an unlinked temporary file and closes it when
the request ends -- after the response's background tasks, and the whole pipeline
runs in one. A 99 GB genome was saved to /data/uploads and its spooled copy, still
open in the app process, held another 99 GB for the 22 minutes the job ran
(2026-10-14). The route closes the parts as soon as process_files has saved them.
"""

import uuid

from app.api.models import FileType
from app.api.utils.file_processor import FileAnalysis as DcFileAnalysis

VCF_BYTES = (
    b"##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS\n"
)


def test_the_spooled_upload_is_closed_before_the_job_runs(
    client, monkeypatch, tmp_path
):
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
        id = uuid.uuid4()
        status = "pending"
        job_metadata = {}

    class _FakeJobService:
        def __init__(self, db):
            self.job = _FakeJob()

        def create_job(self, job_create):
            return self.job

        def update_job(self, job_id, job_update):
            return self.job

    monkeypatch.setattr(upload_router, "JobService", _FakeJobService)
    parts, seen = [], {}

    async def _fake_process_files(files, reference_genome, **kwargs):
        stored = []
        for f in files:
            parts.append(f)
            path = tmp_path / f.filename
            path.write_bytes(await f.read())
            stored.append(str(path))
        analysis = DcFileAnalysis(
            file_type=FileType("vcf"),
            is_compressed=False,
            has_index=False,
            file_size=1,
            vcf_info=None,
            is_valid=True,
            validation_errors=[],
        )
        workflow = {
            "workflow_type": "genomic_analysis",
            "file_type": "vcf",
            "reference": "hg38",
            "recommendations": [],
            "warnings": [],
            "unsupported": False,
            "unsupported_reason": None,
            "is_provisional": False,
        }
        return {
            "success": True,
            "file_paths": stored,
            "file_analysis": analysis,
            "workflow": workflow,
        }

    async def _job(*args, **kwargs):
        # The job: by now each part's temporary copy must be gone.
        seen["closed"] = [part.file.closed for part in parts]

    monkeypatch.setattr(
        upload_router.file_processor, "process_files", _fake_process_files
    )
    monkeypatch.setattr(upload_router, "process_file_nextflow_background_with_db", _job)

    resp = client.post(
        "/upload/genomic-data",
        files={"files": ("sample.vcf", VCF_BYTES, "text/plain")},
        data={"reference_genome": "hg38"},
    )

    assert resp.status_code == 200, resp.text
    assert parts and seen["closed"] == [True] * len(parts)
