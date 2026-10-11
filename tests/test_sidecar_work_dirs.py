"""Sidecar working directories belong to a job, and go when the job does.

PyPGx and zarohla worked in DATA_DIR/temp/<random uuid>: nothing could tie such a
directory to a job, so whatever a request did not remove itself stayed for good.
PyPGx's /genotype removed nothing, and also copied its per-gene pipelines and a
summary JSON into /data/reports/<patient>/ -- the patient's root, outside every
job directory, where nothing reads them. After the 30x NA12878 runs data/temp held
24 such directories, and each patient root a set of PyPGx copies per job.

Now each works in DATA_DIR/temp/<service>/<job id>/<random>; /genotype removes its
own directory when it answers; and the app removes temp/<service>/<job id> for any
job that ends (completed, failed or cancelled), which covers outputs Nextflow
copies after a response and anything a killed worker left behind.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.api.models import JobCreate, JobStatus, JobUpdate
from app.services.cleanup_service import cleanup_service
from tests.test_sidecar_chunked_io import (
    PYPGX_SOURCE,
    ZAROHLA_SOURCE,
    _close_module_log_handlers,
    _import_sidecar,
)

JOB = "0cb185b2-5878-456f-8e61-ed253c0f6c95"


def _sidecar(name, source, tmp_path_factory):
    root = tmp_path_factory.mktemp(name)
    before = list(logging.root.handlers)
    with pytest.MonkeyPatch.context() as mp:
        module = _import_sidecar(f"zaropgx_{name}_work_dirs_test", source, mp, root)
        yield module
    _close_module_log_handlers(module, before)


@pytest.fixture(scope="module")
def pypgx_api(tmp_path_factory):
    yield from _sidecar("pypgx", PYPGX_SOURCE, tmp_path_factory)


@pytest.fixture(scope="module")
def zarohla_api(tmp_path_factory):
    yield from _sidecar("zarohla", ZAROHLA_SOURCE, tmp_path_factory)


@pytest.mark.parametrize("service", ["pypgx", "zarohla"])
def test_work_dirs_sit_under_the_job(service, pypgx_api, zarohla_api):
    module = {"pypgx": pypgx_api, "zarohla": zarohla_api}[service]
    assert module.job_work_dir(JOB, "w1") == module.TEMP_DIR / service / JOB / "w1"
    # Not a plain token: never a path component.
    for unsafe in (None, "", "../reports", "a/b", "-rf"):
        assert module.job_work_dir(unsafe, "w1") == (
            module.TEMP_DIR / service / "no-job" / "w1"
        )


def test_genotype_leaves_nothing_behind(pypgx_api):
    reports = pypgx_api.REPORT_DIR
    response = TestClient(pypgx_api.app).post(
        "/genotype",
        data={"genes": "CYP2D6", "patient_id": "patient-1", "report_id": "job-1"},
        files={"file": ("sample.vcf", b"##fileformat=VCFv4.2\n", "text/plain")},
    )
    # PyPGx itself is not installed here, so genotyping fails after the save;
    # the working directory must go either way.
    assert response.status_code in (200, 500), response.text
    job_dirs = pypgx_api.TEMP_DIR / "pypgx" / "no-job"
    assert job_dirs.is_dir() and not any(job_dirs.iterdir())
    assert not reports.exists() or not any(reports.rglob("*")), list(reports.rglob("*"))
    assert not list(pypgx_api.DATA_DIR.glob("*_pypgx_results.json"))


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    monkeypatch.setattr(cleanup_service, "data_dir", tmp_path)
    monkeypatch.setattr(cleanup_service, "temp_dir", tmp_path / "tmp")
    monkeypatch.setattr(cleanup_service, "uploads_dir", tmp_path / "uploads")
    return tmp_path


@pytest.mark.parametrize(
    "status", [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED]
)
def test_an_ended_job_loses_its_sidecar_work_dirs(job_service, scratch, status):
    job = job_service.create_job(
        JobCreate(
            workflow_type="genomic_analysis",
            name="work-dirs",
            metadata={"patient_id": "patient-1"},
        )
    )
    mine = [
        scratch / "temp" / s / str(job.id) / "w1" for s in ("pypgx", "zarohla", "mtdna")
    ]
    for work in mine:
        work.mkdir(parents=True)
        (work / "out.vcf.gz").write_bytes(b"x")
    # Another job of the same patient, still running.
    other = scratch / "temp" / "pypgx" / "another-job" / "w1"
    other.mkdir(parents=True)

    job_service.update_job(job.id, JobUpdate(status=status))

    assert not any(work.parent.exists() for work in mine)
    assert other.exists()
