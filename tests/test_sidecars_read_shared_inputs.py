"""Services read an alignment already on the shared /data volume in place.

Nextflow posted every alignment to each service as an upload, and each stored a full
copy: on 30x NA12878 the 41.2 GB upload plus 41.2 GB each in data/temp/mtdna and
data/temp/zarohla at once, then PyPGx's. Given ``shared_input`` (a path under
/data/uploads or /data/results), a service links the file where it would have stored
the upload and works from the link; the upload path is unchanged for direct callers.

The services are imported out of their source as the neighbouring tests do, with the
heavy tools stubbed.
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_gatk_api_no_mock_bam import (  # noqa: F401  (fixtures)
    COORDINATE_HEADER,
    _conversion_argv,
    client,
    gatk_api,
    reference_fasta,
    samtools,
)
from tests.test_sidecar_input_copies import (  # noqa: F401  (fixture)
    BAM,
    sidecars,
)
from tests.test_zarohla_no_hla_reads import zarohla  # noqa: F401  (fixture)


@pytest.fixture
def shared(tmp_path, monkeypatch):
    """A shared volume with an uploaded BAM, and a file outside its input roots."""
    root = tmp_path / "data"
    (root / "uploads").mkdir(parents=True)
    (root / "results" / "job1").mkdir(parents=True)
    (root / "reference").mkdir()
    monkeypatch.setenv("SHARED_INPUT_ROOTS", f"{root / 'uploads'}:{root / 'results'}")
    bam = root / "uploads" / "upload_ab12_NA12878.final.bam"
    bam.write_bytes(BAM)
    outside = root / "reference" / "not_an_input.bam"
    outside.write_bytes(BAM)
    return types.SimpleNamespace(root=root, bam=bam, outside=outside)


def _is_link_to(path: Path, target: Path) -> bool:
    return path.is_symlink() and path.resolve() == target.resolve()


# ---------------------------------------------------------------------------
# PyPGx /create-input-vcf
# ---------------------------------------------------------------------------


def _stub_pypgx(pypgx, monkeypatch):
    seen = {}

    def fake(alignment_path, output_vcf_gz, assembly):
        seen["input"] = Path(alignment_path)
        seen["was_link_to"] = Path(alignment_path).resolve()
        Path(output_vcf_gz).write_bytes(b"vcf")
        return {"success": True, "vcf": output_vcf_gz, "pharmcat_vcf": None}

    monkeypatch.setattr(pypgx, "run_pypgx_create_input_vcf", fake)
    return seen


def test_pypgx_reads_a_shared_bam_in_place(sidecars, shared, monkeypatch):
    seen = _stub_pypgx(sidecars.pypgx, monkeypatch)
    resp = TestClient(sidecars.pypgx.app).post(
        "/create-input-vcf",
        data={"reference_genome": "hg38", "shared_input": str(shared.bam)},
    )
    assert resp.status_code == 200, resp.text
    assert seen["was_link_to"] == shared.bam, "worked from the shared file, not a copy"
    assert not seen["input"].exists() and not seen["input"].is_symlink()  # link gone
    assert shared.bam.read_bytes() == BAM, "the shared file is left as it was"


@pytest.mark.parametrize(
    "data, files",
    [
        ({"shared_input": "OUTSIDE"}, None),  # outside the input roots
        ({}, None),  # neither
        ({"shared_input": "BAM"}, {"file": ("x.bam", BAM, "application/octet-stream")}),
    ],
)
def test_pypgx_refuses_a_bad_request_with_400(
    sidecars, shared, monkeypatch, data, files
):
    _stub_pypgx(sidecars.pypgx, monkeypatch)
    data = {
        k: {"OUTSIDE": str(shared.outside), "BAM": str(shared.bam)}.get(v, v)
        for k, v in data.items()
    }
    resp = TestClient(sidecars.pypgx.app).post(
        "/create-input-vcf", data={"reference_genome": "hg38", **data}, files=files
    )
    assert resp.status_code == 400, resp.text


def test_pypgx_uses_a_current_index_instead_of_indexing_again(
    sidecars, tmp_path, monkeypatch
):
    """A converted BAM arrives with gatk-api's index; indexing a 30x genome took 8 min."""
    pypgx = sidecars.pypgx
    fasta = tmp_path / "ref.fasta"
    fasta.write_text(">chr22\nACGT\n")
    (tmp_path / "ref.fasta.fai").write_text("chr22\t4\t7\t4\t5\n")
    monkeypatch.setattr(pypgx, "_reference_fasta_for_assembly", lambda assembly: fasta)
    indexed = []
    monkeypatch.setitem(sys.modules, "pysam", types.SimpleNamespace())
    # Indexing runs pysam in a child process (pysam_command); record it there.
    monkeypatch.setattr(
        pypgx,
        "pysam_command",
        lambda command, *args: indexed.append(args[0]) if command == "index" else None,
    )
    monkeypatch.setattr(
        pypgx.subprocess,
        "run",
        lambda *a, **k: types.SimpleNamespace(
            returncode=1, stderr="stop here", stdout=""
        ),
    )
    bam = tmp_path / "in.bam"
    bam.write_bytes(BAM)

    pypgx.run_pypgx_create_input_vcf(str(bam), str(tmp_path / "o.vcf.gz"), "GRCh38")
    assert indexed == [str(bam)], "no index: index it"

    indexed.clear()
    bai = tmp_path / "in.bam.bai"
    bai.write_bytes(b"BAI")
    os.utime(bai, (bam.stat().st_mtime + 5,) * 2)
    pypgx.run_pypgx_create_input_vcf(str(bam), str(tmp_path / "o.vcf.gz"), "GRCh38")
    assert indexed == [], "a current index is used as it is"

    os.utime(bai, (bam.stat().st_mtime - 60,) * 2)
    pypgx.run_pypgx_create_input_vcf(str(bam), str(tmp_path / "o.vcf.gz"), "GRCh38")
    assert indexed == [str(bam)], "a stale index is rebuilt"


# ---------------------------------------------------------------------------
# mtDNA /call-mtdna
# ---------------------------------------------------------------------------


def test_mtdna_reads_a_shared_bam_in_place(sidecars, shared, monkeypatch):
    seen = {}

    async def fake(upload_path, work, reference_genome, job_key, input_type):
        seen["was_link_to"] = Path(upload_path).resolve()
        seen["input"] = Path(upload_path)
        return {"mt_rnr1": None}

    monkeypatch.setattr(sidecars.mtdna, "_call_from_alignment", fake)
    resp = TestClient(sidecars.mtdna.app).post(
        "/call-mtdna",
        data={
            "patient_id": "p1",
            "report_id": "r1",
            "input_type": "bam",
            "shared_input": str(shared.bam),
        },
    )
    assert resp.status_code == 200, resp.text
    assert seen["was_link_to"] == shared.bam
    assert not seen["input"].is_symlink()
    assert shared.bam.read_bytes() == BAM


def test_mtdna_replaces_an_input_a_killed_request_left_behind(
    sidecars, shared, monkeypatch
):
    async def fake(upload_path, work, reference_genome, job_key, input_type):
        return {"mt_rnr1": None}

    monkeypatch.setattr(sidecars.mtdna, "_call_from_alignment", fake)
    work = Path(sidecars.mtdna.TEMP_DIR) / "mtdna" / "job-left"
    work.mkdir(parents=True, exist_ok=True)
    (work / shared.bam.name).symlink_to(shared.root / "reference" / "not_an_input.bam")
    resp = TestClient(sidecars.mtdna.app).post(
        "/call-mtdna",
        data={
            "patient_id": "p1",
            "report_id": "r1",
            "input_type": "bam",
            "job_id": "job-left",
            "shared_input": str(shared.bam),
        },
    )
    assert resp.status_code == 200, resp.text


def test_mtdna_refuses_a_path_outside_the_input_roots(sidecars, shared):
    resp = TestClient(sidecars.mtdna.app).post(
        "/call-mtdna",
        data={
            "patient_id": "p1",
            "report_id": "r1",
            "input_type": "bam",
            "shared_input": str(shared.outside),
        },
    )
    assert resp.status_code == 400, resp.text
    assert "outside the shared input" in resp.json()["detail"]


@pytest.mark.parametrize(
    "bad", ["/nonexistent/pharmcat.mtdna.tsv", "{root}/uploads/", "{root}/uploads/.."]
)
def test_mtdna_refuses_before_touching_the_job_s_work_dir(sidecars, shared, bad):
    """The refusal comes first: a refused name used to delete the job's file of
    the same name, and a directory (/data/uploads/, .../..) named the work dir or
    its parent, which os.remove answered with a 500."""
    work = Path(sidecars.mtdna.TEMP_DIR) / "mtdna" / "job-x"
    work.mkdir(parents=True, exist_ok=True)
    kept = work / "pharmcat.mtdna.tsv"
    kept.write_text("MT-RNR1\n")
    path = bad.format(root=shared.root)  # a string: Path() would drop the "/"
    resp = TestClient(sidecars.mtdna.app).post(
        "/call-mtdna",
        data={
            "patient_id": "p1",
            "report_id": "r1",
            "input_type": "bam",
            "job_id": "job-x",
            "shared_input": path,
        },
    )
    assert resp.status_code == 400, resp.text
    assert kept.read_text() == "MT-RNR1\n"


# ---------------------------------------------------------------------------
# ZaroHLA /call-hla
# ---------------------------------------------------------------------------


def test_zarohla_reads_a_shared_bam_in_place(zarohla, shared, monkeypatch):
    seen = {}

    async def probe(input_path, reference_genome):
        seen["was_link_to"] = Path(input_path).resolve()
        return 0  # no HLA reads: the request ends here, successfully

    monkeypatch.setattr(zarohla, "_probe_hla_read_count", probe)
    resp = TestClient(zarohla.app).post(
        "/call-hla", data={"shared_input": str(shared.bam)}
    )
    assert resp.status_code == 200, resp.text
    assert seen["was_link_to"] == shared.bam
    assert shared.bam.read_bytes() == BAM


def test_zarohla_refuses_a_bad_path_with_400_not_500(zarohla, shared):
    """The handler turns every exception into a 500; the path is checked before it."""
    resp = TestClient(zarohla.app).post(
        "/call-hla", data={"shared_input": str(shared.outside)}
    )
    assert resp.status_code == 400, resp.text
    resp = TestClient(zarohla.app).post(
        "/call-hla",
        data={"shared_input": str(shared.bam)},
        files={"file": ("x.bam", BAM, "application/octet-stream")},
    )
    assert resp.status_code == 400, resp.text


def test_zarohla_refuses_before_starting_the_step(zarohla, shared, monkeypatch):
    """Refused after start_step, the step showed "running" until Nextflow failed
    the job."""
    started = []

    class FakeJobClient:
        def __init__(self, **kwargs):
            pass

        async def is_job_cancelled(self):
            return False

        async def start_step(self, message):
            started.append(message)

    monkeypatch.setattr(zarohla, "JobClient", FakeJobClient)
    resp = TestClient(zarohla.app).post(
        "/call-hla", data={"shared_input": str(shared.outside), "job_id": "job-1"}
    )
    assert resp.status_code == 400, resp.text
    assert started == []


# ---------------------------------------------------------------------------
# gatk-api /cram-to-bam, /align-fastq
# ---------------------------------------------------------------------------


def test_cram_to_bam_converts_a_shared_cram_in_place(
    client, gatk_api, reference_fasta, samtools, shared, monkeypatch
):
    cram = shared.root / "uploads" / "upload_cd34_NA12878.final.cram"
    cram.write_bytes(b"CRAM\x03\x00")
    samtools.header = COORDINATE_HEADER
    inputs = []
    real_run = gatk_api.subprocess.run

    def watch(argv, *args, **kwargs):
        if argv[:3] == ["samtools", "view", "-b"]:
            inputs.append(Path(argv[-1]).is_symlink() and Path(argv[-1]).resolve())
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(gatk_api.subprocess, "run", watch)
    resp = client.post(
        "/cram-to-bam", data={"reference_genome": "hg38", "shared_input": str(cram)}
    )
    assert resp.status_code == 200, resp.text
    assert inputs == [cram], "samtools decoded a link to the shared CRAM, not a copy"
    converted = Path(_conversion_argv(samtools)[-1])
    assert converted.name.endswith(".cram")
    assert not converted.exists(), "the work directory, link and all, is gone"
    assert cram.read_bytes() == b"CRAM\x03\x00"


def test_cram_to_bam_refuses_a_path_outside_the_input_roots(
    client, gatk_api, reference_fasta, samtools, shared
):
    resp = client.post(
        "/cram-to-bam",
        data={"reference_genome": "hg38", "shared_input": str(shared.outside)},
    )
    assert resp.status_code == 400, resp.text


def test_align_fastq_holds_shared_reads_to_the_upload_budget(
    client, gatk_api, shared, tmp_path, monkeypatch
):
    ref = tmp_path / "align.fasta"
    ref.write_text(">chr1\nA\n")
    image = tmp_path / "align.fasta.img"
    image.write_bytes(b"img")
    monkeypatch.setattr(gatk_api, "ALIGN_REFERENCE_FASTA", str(ref))
    monkeypatch.setattr(gatk_api, "ALIGN_INDEX_IMAGE", str(image))
    monkeypatch.setattr(gatk_api, "FASTQ_MAX_UPLOAD_BYTES", 10)
    mate1 = shared.root / "uploads" / "r_1.fastq"
    mate2 = shared.root / "uploads" / "r_2.fastq"
    mate1.write_bytes(b"@r\nACGT\n+\n")  # 10 bytes: within budget alone
    mate2.write_bytes(b"@r\nACGT\n+\n")  # both mates count together
    resp = client.post(
        "/align-fastq",
        data={
            "reference_genome": "hg38",
            "shared_input": str(mate1),
            "shared_input2": str(mate2),
        },
    )
    assert resp.status_code == 413, resp.text
    assert mate1.exists() and mate2.exists()


@pytest.mark.parametrize("uploaded", ["mate1", "mate2"])
def test_align_fastq_takes_one_mate_by_path_and_one_uploaded(
    client, gatk_api, shared, tmp_path, monkeypatch, uploaded
):
    """bin/input-args decides per file, so a pair can arrive half by path; it was
    refused with a 400, which Nextflow treats as fatal. One budget still holds."""
    ref = tmp_path / "align.fasta"
    ref.write_text(">chr1\nA\n")
    image = tmp_path / "align.fasta.img"
    image.write_bytes(b"img")
    monkeypatch.setattr(gatk_api, "ALIGN_REFERENCE_FASTA", str(ref))
    monkeypatch.setattr(gatk_api, "ALIGN_INDEX_IMAGE", str(image))
    reads = b"@r\nACGT\n+\n"  # 10 bytes
    on_disk = shared.root / "uploads" / "r_2.fastq"
    on_disk.write_bytes(reads)
    if uploaded == "mate1":
        data = {"reference_genome": "hg38", "shared_input2": str(on_disk)}
        files = {"file": ("r_1.fastq", reads, "application/octet-stream")}
    else:
        data = {"reference_genome": "hg38", "shared_input": str(on_disk)}
        files = {"file2": ("r_1.fastq", reads, "application/octet-stream")}
    monkeypatch.setattr(gatk_api, "FASTQ_MAX_UPLOAD_BYTES", 10)
    resp = client.post("/align-fastq", data=data, files=files)
    assert resp.status_code == 413, resp.text  # 20 bytes of reads, one budget
    monkeypatch.setattr(gatk_api, "FASTQ_MAX_UPLOAD_BYTES", 1000)
    resp = client.post("/align-fastq", data=data, files=files)
    # Staged: refused only for what the reads are (no platform in a 1-read FASTQ).
    assert resp.status_code == 422, resp.text
    assert on_disk.read_bytes() == reads
