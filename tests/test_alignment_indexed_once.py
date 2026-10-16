"""An alignment is indexed once, not once per step.

The copy of an NCBI-named alignment under UCSC names (app/genome/contig_names.py)
had no index: the upload's own does not fit it, because the new header's length
moves every offset. The HLA, mtDNA and PyPGx steps each indexed it, and zarohla and
the mtDNA service indexed every alignment they were given even when a current index
came with it (app/utils/shared_input.py copies one beside the input). PyPGx took 8
minutes to index NA12878's 41 GB BAM; a 107 GB genome took about twenty minutes per
step. The copy is now indexed when it is made, and each step uses a current index.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from app.utils.shared_input import current_index
from tests.test_sidecar_input_copies import sidecars  # noqa: F401  (fixture)
from tests.test_zarohla_no_hla_reads import (  # noqa: F401  (fixtures)
    fake_tools,
    zarohla,
)

pysam = pytest.importorskip("pysam")


@pytest.mark.parametrize("kind", ["bam", "cram"])
def test_the_renamed_copy_comes_with_its_own_index(tmp_path, kind):
    from app.genome.contig_names import write_renamed_copy
    from tests.test_grch38_contig_names import PLAN, _alignment

    files = _alignment(tmp_path)
    copy = write_renamed_copy(str(getattr(files, kind)), kind, PLAN)

    found = current_index(Path(copy))
    assert found is not None, "an index at least as new as the copy, beside it"
    assert found[1] == (".bai" if kind == "bam" else ".crai")
    with pysam.AlignmentFile(copy, reference_filename=str(files.ucsc_fa)) as aln:
        assert [r.query_name for r in aln.fetch("chrM")] == ["read2"]
        assert [r.reference_start for r in aln.fetch("chr6", 0, 600)] == [10, 300]


def _bam_with_index(tmp_path, index_age):
    bam = tmp_path / "in.bam"
    bam.write_bytes(b"BAM")
    if index_age is not None:
        bai = tmp_path / "in.bam.bai"
        bai.write_bytes(b"BAI")
        os.utime(bai, (bam.stat().st_mtime + index_age,) * 2)
    return bam


@pytest.mark.parametrize("index_age, indexes", [(None, True), (5, False), (-60, True)])
def test_zarohla_indexes_only_an_input_without_a_current_index(
    zarohla, fake_tools, tmp_path, index_age, indexes  # noqa: F811
):
    bam = _bam_with_index(tmp_path, index_age)
    count = asyncio.run(zarohla._probe_hla_read_count(bam, "hg38"))
    assert count == 1
    ran_index = any(c[1] == "index" for c in fake_tools.calls)
    assert ran_index is indexes


@pytest.mark.parametrize("index_age, indexes", [(None, True), (5, False), (-60, True)])
def test_mtdna_indexes_only_an_input_without_a_current_index(
    sidecars, monkeypatch, tmp_path, index_age, indexes  # noqa: F811
):
    mtdna = sidecars.mtdna
    bam = _bam_with_index(tmp_path, index_age)
    ran = []

    async def grch38_chrm(bam_path, work, job_key):
        return "chrM", 16569

    class Stop(Exception):
        pass

    async def record(argv, cwd, job_key):
        ran.append(argv)
        if argv[:2] == ["samtools", "view"]:
            raise Stop  # past the index decision

    monkeypatch.setattr(mtdna, "_read_mito_sq_header", grch38_chrm)
    monkeypatch.setattr(mtdna, "_run", record)
    with pytest.raises(Stop):
        asyncio.run(
            mtdna._call_from_alignment(str(bam), str(tmp_path), "hg38", "job-1")
        )
    assert (["samtools", "index", str(bam)] in ran) is indexes


def test_an_unsorted_copy_fails_as_not_indexed_not_as_not_renamed(tmp_path):
    """The renaming worked; only the index could not be built, and the job says so
    (it used to say "Could not rename the file's chromosomes")."""
    from app.genome.contig_names import RenamedCopyNotIndexed, write_renamed_copy
    from tests.test_grch38_contig_names import PLAN

    bam = tmp_path / "unsorted.bam"
    header = {
        "HD": {"VN": "1.6", "SO": "unsorted"},
        "SQ": [{"SN": name, "LN": 600} for name in PLAN],
    }
    with pysam.AlignmentFile(str(bam), "wb", header=header) as out:
        for name, start in (("a", 300), ("b", 10)):
            read = pysam.AlignedSegment(out.header)
            read.query_name = name
            read.reference_id = 0
            read.reference_start = start
            read.cigarstring = "10M"
            read.query_sequence = "ACGTACGTAC"
            read.query_qualities = pysam.qualitystring_to_array("I" * 10)
            read.mapping_quality = 60
            out.write(read)
    with pytest.raises(RenamedCopyNotIndexed):
        write_renamed_copy(str(bam), "bam", PLAN)


def test_mtdna_drops_an_index_a_killed_request_left_behind(
    sidecars, monkeypatch, tmp_path  # noqa: F811
):
    """A retry reuses the job's work dir. An index left there is newer than the shared
    file it is compared with, so it passed for current beside the new link, and a
    truncated one failed the retry at samtools view."""
    from fastapi.testclient import TestClient

    mtdna = sidecars.mtdna
    root = tmp_path / "data"
    (root / "uploads").mkdir(parents=True)
    monkeypatch.setenv("SHARED_INPUT_ROOTS", str(root / "uploads"))
    bam = root / "uploads" / "in.bam"
    bam.write_bytes(b"BAM")
    work = Path(mtdna.TEMP_DIR) / "mtdna" / "job-retry"
    work.mkdir(parents=True, exist_ok=True)
    (work / "in.bam").symlink_to(bam)
    (work / "in.bam.bai").write_bytes(b"truncated")
    seen = {}

    async def fake(upload_path, work_dir, reference_genome, job_key, input_type):
        seen["index"] = current_index(Path(upload_path))
        return {"mt_rnr1": None}

    monkeypatch.setattr(mtdna, "_call_from_alignment", fake)
    resp = TestClient(mtdna.app).post(
        "/call-mtdna",
        data={
            "patient_id": "p1",
            "report_id": "r1",
            "input_type": "bam",
            "job_id": "job-retry",
            "shared_input": str(bam),
        },
    )
    assert resp.status_code == 200, resp.text
    assert seen["index"] is None
