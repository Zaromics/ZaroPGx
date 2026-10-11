"""Cancelling a job reaches every sidecar's running process.

job_router.cancel_container_job posts `POST <service>/cancel` with the job id in a
JSON body to each service in cancel_container_jobs' list. mtDNA-Server 2 exposed only
`POST /cancel/{job_key}`, so its cancel was a 404 that the app logged as "no running
job", and mutserve kept running after the job was cancelled.
"""

import importlib.util
import re
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
JOB_ROUTER = REPO_ROOT / "app/api/routes/job_router.py"
MTDNA_APP = REPO_ROOT / "docker/mtdna-server-2/app.py"

SERVICE_SOURCES = {
    "gatk-api": "docker/gatk-api/gatk_api.py",
    "zarohla": "docker/zarohla/app.py",
    "mtdna": "docker/mtdna-server-2/app.py",
    "pypgx": "docker/pypgx/pypgx_wrapper.py",
    "pharmcat": "docker/pharmcat/pharmcat.py",
}


def _cancelled_services():
    source = JOB_ROUTER.read_text(encoding="utf-8")
    body = source[source.index("async def cancel_container_jobs") :]
    body = body[: body.index("async def cancel_container_job(")]
    return re.findall(r'\{"name": "([^"]+)", "url": "http://[^"]+"\}', body)


def test_every_service_the_app_cancels_has_the_route_it_posts_to():
    services = _cancelled_services()
    assert sorted(services) == sorted(SERVICE_SOURCES)
    for name in services:
        source = (REPO_ROOT / SERVICE_SOURCES[name]).read_text(encoding="utf-8")
        assert '@app.post("/cancel")' in source, f"{name} has no POST /cancel"


@pytest.fixture
def mtdna(tmp_path):
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATA_DIR", str(tmp_path / "data"))
        mp.setitem(sys.modules, "psutil", types.ModuleType("psutil"))
        job_client = types.ModuleType("job_client")
        job_client.JobClient = object
        mp.setitem(sys.modules, "job_client", job_client)
        mp.syspath_prepend(str(REPO_ROOT / "app"))
        spec = importlib.util.spec_from_file_location(
            "zaropgx_mtdna_cancel_under_test", MTDNA_APP
        )
        module = importlib.util.module_from_spec(spec)
        mp.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
        yield module


class _Proc:
    def __init__(self):
        self.killed = False

    def kill(self):
        self.killed = True


def test_mtdna_kills_the_job_named_in_the_apps_cancel_body(mtdna):
    proc = _Proc()
    mtdna.running_processes["job-1"] = {"mutserve": proc}
    other = _Proc()
    mtdna.running_processes["job-2"] = {"mutserve": other}

    response = TestClient(mtdna.app).post(
        "/cancel",
        json={"job_id": "job-1", "patient_id": "patient-1", "action": "cancel"},
    )

    assert response.status_code == 200
    assert response.json()["killed"] == 1
    assert proc.killed
    assert not other.killed
    assert "job-1" not in mtdna.running_processes


def test_mtdna_keeps_the_path_form(mtdna):
    proc = _Proc()
    mtdna.running_processes["job-3"] = {"bcftools": proc}

    response = TestClient(mtdna.app).post("/cancel/job-3")

    assert response.status_code == 200
    assert proc.killed
