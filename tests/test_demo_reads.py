"""Run Demo analyses NA12878's reads, bundled with the app in app/static/demo.

The demo used to be PharmCAT's example VCF: five non-reference genotypes, no chrM and no
reads, so HLA typing, PyPGx's deletion and duplication calls and the mitochondrial
analysis never ran. It is now a CRAM cut from NA12878's 1000 Genomes 30x genome by
scripts/make-demo-cram.sh, and POST /upload/demo starts a job on it exactly as an upload
of the same files would. The browser no longer downloads the demo to upload it back.
"""

import ast
import csv
import uuid
from pathlib import Path

import pytest

from app.api.models import FileType
from app.api.utils.file_processor import FileAnalysis as DcFileAnalysis

REPO = Path(__file__).resolve().parents[1]
DEMO = REPO / "app" / "static" / "demo"
BED = DEMO / "NA12878.pgx-demo.regions.bed"


def _constant(path, name):
    for node in ast.parse((REPO / path).read_text()).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {path}")


def _covered(regions, contig, start, end):
    return any(c == contig and s <= start and end <= e for c, s, e in regions)


def test_the_demo_is_bundled_and_small():
    from app.api.routes.upload_router import DEMO_DIR, DEMO_FILES

    assert DEMO_DIR == DEMO
    cram, crai = (DEMO / name for name in DEMO_FILES)
    assert cram.read_bytes()[:4] == b"CRAM"
    assert crai.stat().st_size > 0
    # GitHub warns at 50 MB a file; the cut is about 30 MB.
    assert cram.stat().st_size < 50 * 1024**2


def test_the_cut_holds_every_region_the_pipeline_reads():
    """A gene added to PyPGx's table, or a depth window moved, needs a new cut
    (scripts/make-demo-cram.sh): without the reads the demo would lose that call."""
    regions = [
        (contig, int(start), int(end))
        for contig, start, end in (
            line.split("\t") for line in BED.read_text().split("\n") if line
        )
    ]
    missing = []
    gene_table = (REPO / "lexicon" / "gene-table.csv").read_text().splitlines()
    for row in csv.DictReader(gene_table):
        contig, span = row["GRCh38Region"].split(":")
        start, end = (int(x) for x in span.split("-"))
        if not _covered(regions, "chr" + contig, start - 1, end):
            missing.append(row["Gene"])
    for contig, start, end in _constant(
        "docker/pypgx/pypgx_wrapper.py", "WGS_PROBE_WINDOWS_GRCH38"
    ):
        if not _covered(regions, contig, start, end):
            missing.append(f"{contig}:{start}-{end}")
    for start, end in _constant("docker/zarohla/app.py", "HLA_CLASS_I_LOCI")["GRCh38"]:
        if not _covered(regions, "chr6", start, end):
            missing.append(f"chr6:{start}-{end}")
    assert not missing, f"not in the demo's cut: {missing}"


@pytest.fixture
def demo(client, monkeypatch, tmp_path):
    """POST /upload/demo with the job machinery faked; returns what reached it."""
    from app.api.routes import upload_router

    # The reference cache the demo CRAM decodes with, as the downloader marks it.
    marker = tmp_path / ".ref_cache_1kg_analysis_set.done"
    marker.write_text("3366 sequences\n")
    monkeypatch.setattr(upload_router, "DEMO_REFERENCE_MARKER", marker)

    seen = {}
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

    class _FakeJobService:
        def __init__(self, db):
            pass

        def create_job(self, job_create):
            seen["job_name"] = job_create.name
            seen["metadata"] = job_create.metadata
            return _FakeJob()

        def update_job(self, job_id, job_update):
            return _FakeJob()

    monkeypatch.setattr(upload_router, "JobService", _FakeJobService)

    async def _noop_background(*args, **kwargs):
        return None

    monkeypatch.setattr(
        upload_router, "process_file_nextflow_background_with_db", _noop_background
    )

    async def _fake_process_files(files, reference_genome, **kwargs):
        seen["files"] = [(f.filename, await f.read(4)) for f in files]
        seen["reference_genome"] = reference_genome
        seen["switches"] = kwargs
        analysis = DcFileAnalysis(
            file_type=FileType.CRAM,
            is_compressed=True,
            has_index=True,
            file_size=1,
            vcf_info=None,
            is_valid=True,
            validation_errors=[],
        )
        workflow = {
            "workflow_type": "genomic_analysis",
            "file_type": "cram",
            "reference": reference_genome,
            "recommendations": [],
            "warnings": [],
            "unsupported": False,
            "unsupported_reason": None,
            "is_provisional": False,
        }
        return {
            "success": True,
            "file_paths": [str(DEMO / files[0].filename)],
            "file_analysis": analysis,
            "workflow": workflow,
        }

    monkeypatch.setattr(
        upload_router.file_processor, "process_files", _fake_process_files
    )

    def _post(**form):
        return client.post("/upload/demo", data=form), seen

    return _post


def test_the_demo_is_analysed_as_an_upload_of_its_files(demo, monkeypatch):
    monkeypatch.setenv("PHARMCAT_ABSENT_TO_REF", "true")
    response, seen = demo(optitype_enabled="true", pypgx_enabled="false")

    assert response.status_code == 200, response.text
    assert response.json()["file_type"] == "cram"
    assert seen["files"] == [
        ("NA12878.pgx-demo.cram", b"CRAM"),
        (
            "NA12878.pgx-demo.cram.crai",
            (DEMO / "NA12878.pgx-demo.cram.crai").read_bytes()[:4],
        ),
    ]
    assert seen["reference_genome"] == "hg38"
    assert seen["job_name"] == "Genomic Analysis - NA12878-demo"
    # The stage switches reach the workflow as an upload's do.
    assert seen["switches"]["optitype_enabled"] == "true"
    assert seen["switches"]["pypgx_enabled"] == "false"
    # Reads are genotyped at every position: nothing is assumed, whatever .env says.
    assert seen["metadata"]["pharmcat_absent_to_ref"] is False
    assert seen["metadata"]["pharmcat_unspecified_to_ref"] is False


def test_a_missing_demo_is_said_plainly(demo, monkeypatch, tmp_path):
    from app.api.routes import upload_router

    monkeypatch.setattr(upload_router, "DEMO_DIR", tmp_path)
    response, seen = demo()

    assert response.status_code == 503
    assert "NA12878.pgx-demo.cram" in response.json()["detail"]
    assert "files" not in seen


def test_the_page_starts_the_demo_on_the_server():
    page = (REPO / "app" / "templates" / "index.html").read_text(encoding="utf-8")

    assert 'onclick="runDemo()"' in page
    assert "fetch('/upload/demo'" in page
    assert "/static/demo/pharmcat.example.vcf" not in page


def test_the_demo_waits_for_its_reference(demo, monkeypatch, tmp_path):
    """Without the 1000 Genomes analysis set cached, the CRAM's conversion would fail
    minutes into the job; the demo says so before starting one."""
    from app.api.routes import upload_router

    monkeypatch.setattr(
        upload_router, "DEMO_REFERENCE_MARKER", tmp_path / "not-cached-yet"
    )
    response, seen = demo()

    assert response.status_code == 503
    assert "1000 Genomes GRCh38 analysis set" in response.json()["detail"]
    assert "files" not in seen
