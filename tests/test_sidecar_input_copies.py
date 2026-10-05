"""The PyPGx and mtDNA sidecars must not keep their copy of the uploaded alignment.

Each stores the upload in its temp tree, works from it, and returns paths to what it
produced. Nothing removed the stored copy, so a 30x NA12878 BAM run (2026-10-05) left
41 GB in data/temp/<job> from PyPGx's /create-input-vcf and another 41 GB in
data/temp/mtdna/<job> from /call-mtdna, on top of the upload itself, per job. The copy
and the index written beside it now go when the request ends, success or failure;
the outputs stay.

Both modules are imported out of their source with psutil and the job client stubbed,
as in tests/test_sidecar_chunked_io.py and tests/test_mtdna_vcf_path.py; the heavy
work is stubbed so these run without samtools, bcftools or PyPGx.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
import types
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
PYPGX = REPO / "docker" / "pypgx" / "pypgx_wrapper.py"
MTDNA = REPO / "docker" / "mtdna-server-2" / "app.py"


def _fake_psutil():
    module = types.ModuleType("psutil")
    module.virtual_memory = lambda: types.SimpleNamespace(
        total=16 * 1024**3, available=8 * 1024**3, used=8 * 1024**3, percent=50.0
    )
    module.Process = lambda *a, **k: types.SimpleNamespace()
    module.pid_exists = lambda pid: False
    module.NoSuchProcess = type("NoSuchProcess", (Exception,), {})
    module.AccessDenied = type("AccessDenied", (Exception,), {})
    return module


def _fake_job_client():
    module = types.ModuleType("job_client")

    class JobClient:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("no job server in tests")

    module.JobClient = JobClient
    module.create_job_client = lambda *a, **k: JobClient()
    return module


def _import(name, source, root, mp, extra_path=None):
    mp.setenv("DATA_DIR", str(root / "data"))
    mp.setenv("TMPDIR", str(root / "tmp"))
    mp.setenv("REFERENCE_DIR", str(root / "reference"))
    mp.setenv("PYPGX_PROGRESS_LOG", str(root / "pypgx_progress.log"))
    mp.setitem(sys.modules, "psutil", _fake_psutil())
    mp.setitem(sys.modules, "job_client", _fake_job_client())
    if extra_path:
        mp.syspath_prepend(str(extra_path))
    spec = importlib.util.spec_from_file_location(name, source)
    module = importlib.util.module_from_spec(spec)
    mp.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def sidecars(tmp_path_factory):
    before = list(logging.root.handlers)
    with pytest.MonkeyPatch.context() as mp:
        pypgx = _import(
            "zaropgx_pypgx_input_copies", PYPGX, tmp_path_factory.mktemp("p"), mp
        )
        mtdna = _import(
            "zaropgx_mtdna_input_copies",
            MTDNA,
            tmp_path_factory.mktemp("m"),
            mp,
            extra_path=REPO / "app",
        )
        yield types.SimpleNamespace(pypgx=pypgx, mtdna=mtdna)
    for handler in list(logging.root.handlers):
        if handler not in before:
            logging.root.removeHandler(handler)
    for module in (pypgx, mtdna):
        for handler in getattr(module, "_log_handlers", []):
            handler.close()


BAM = b"\x1f\x8b\x08\x04" + b"\x00" * 60


# ---------------------------------------------------------------------------
# PyPGx /create-input-vcf
# ---------------------------------------------------------------------------


def _stub_create_input_vcf(pypgx, monkeypatch, success):
    seen = {}

    def fake(alignment_path, output_vcf_gz, assembly):
        seen["input"] = Path(alignment_path)
        Path(alignment_path + ".bai").write_bytes(b"index")
        if not success:
            return {"success": False, "error": "boom"}
        Path(output_vcf_gz).write_bytes(b"vcf")
        seen["vcf"] = Path(output_vcf_gz)
        return {"success": True, "vcf": output_vcf_gz, "pharmcat_vcf": None}

    monkeypatch.setattr(pypgx, "run_pypgx_create_input_vcf", fake)
    return seen


def _post_create_input_vcf(pypgx):
    return TestClient(pypgx.app).post(
        "/create-input-vcf",
        files={"file": ("sample.bam", BAM, "application/octet-stream")},
        data={"reference_genome": "hg38"},
    )


def test_pypgx_drops_its_input_copy_and_keeps_the_vcf(sidecars, monkeypatch):
    seen = _stub_create_input_vcf(sidecars.pypgx, monkeypatch, success=True)

    resp = _post_create_input_vcf(sidecars.pypgx)

    assert resp.status_code == 200, resp.text
    assert not seen["input"].exists()
    assert not Path(str(seen["input"]) + ".bai").exists()
    assert seen["vcf"].exists(), "the VCF is the result; it must stay"
    assert "input_file" not in resp.json(), "no path to a file that is gone"


def test_pypgx_drops_its_input_copy_when_conversion_fails(sidecars, monkeypatch):
    seen = _stub_create_input_vcf(sidecars.pypgx, monkeypatch, success=False)

    resp = _post_create_input_vcf(sidecars.pypgx)

    assert resp.status_code == 500
    assert not seen["input"].exists()
    assert not Path(str(seen["input"]) + ".bai").exists()


# ---------------------------------------------------------------------------
# mtDNA /call-mtdna
# ---------------------------------------------------------------------------


def _stub_call_from_alignment(mtdna, monkeypatch, fail):
    seen = {}

    async def fake(upload_path, work, reference_genome, job_key, input_type):
        seen["input"] = Path(upload_path)
        Path(upload_path + ".bai").write_bytes(b"index")
        report = Path(work) / "report.html"
        report.write_text("<html></html>")
        seen["report"] = report
        if fail:
            raise HTTPException(status_code=422, detail="no chrM reads")
        return {"report_html": str(report), "mt_rnr1": None}

    monkeypatch.setattr(mtdna, "_call_from_alignment", fake)
    return seen


def _post_call_mtdna(mtdna):
    return TestClient(mtdna.app).post(
        "/call-mtdna",
        files={"file": ("sample.bam", BAM, "application/octet-stream")},
        data={"patient_id": "p1", "report_id": "r1", "input_type": "bam"},
    )


def test_mtdna_drops_its_input_copy_and_keeps_the_report(sidecars, monkeypatch):
    seen = _stub_call_from_alignment(sidecars.mtdna, monkeypatch, fail=False)

    resp = _post_call_mtdna(sidecars.mtdna)

    assert resp.status_code == 200, resp.text
    assert not seen["input"].exists()
    assert not Path(str(seen["input"]) + ".bai").exists()
    assert seen["report"].exists(), "the report is the result; it must stay"


def test_mtdna_drops_its_input_copy_when_calling_fails(sidecars, monkeypatch):
    seen = _stub_call_from_alignment(sidecars.mtdna, monkeypatch, fail=True)

    resp = _post_call_mtdna(sidecars.mtdna)

    assert resp.status_code == 422
    assert not seen["input"].exists()
    assert not Path(str(seen["input"]) + ".bai").exists()
