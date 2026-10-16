"""GRCh38 named by accession is GRCh38.

An alignment made against NCBI's own GRCh38 FASTA names its chromosomes by accession
(NC_000006.12, CM000668.2) rather than chr6. On 2026-10-13 a whole-genome BAM named
that way was accepted with no build, read by zarohla as having no chromosome 6 and by
the mtDNA sidecar as having no mitochondrial reads. app/genome/contig_names.py maps
each accession to its UCSC name; these tests pin the table and the detection.
"""

from __future__ import annotations

import asyncio
import os
import random
import shutil
from types import SimpleNamespace

import pysam
import pytest

import app.api.routes.upload_router as ur
from app.api.utils.file_processor import FileAnalysis, FileProcessor, FileType
from app.api.utils.header_inspector import detect_reference_assembly
from app.genome.contig_names import (
    accession_chromosome,
    grch38_accessions,
    grch38_ucsc_names,
    names_a_chromosome,
    rename_plan,
    write_renamed_copy,
)

# The 25 assembled molecules, read off NCBI's GCF_000001405.26 assembly report.
GRCH38_CHROMOSOMES = {
    "NC_000001.11": ("CM000663.2", "chr1", 248956422),
    "NC_000006.12": ("CM000668.2", "chr6", 170805979),
    "NC_000010.11": ("CM000672.2", "chr10", 133797422),
    "NC_000022.11": ("CM000684.2", "chr22", 50818468),
    "NC_000023.11": ("CM000685.2", "chrX", 156040895),
    "NC_000024.10": ("CM000686.2", "chrY", 57227415),
    "NC_012920.1": ("J01415.2", "chrM", 16569),
}


def test_both_accessions_of_each_chromosome_map_to_its_ucsc_name():
    table = grch38_accessions()
    for refseq, (genbank, ucsc, length) in GRCH38_CHROMOSOMES.items():
        assert table[refseq] == (ucsc, length)
        assert table[genbank] == (ucsc, length)


def test_the_table_covers_every_grch38_sequence():
    """455 sequences in the base release, 254 patches since: each has a RefSeq and a
    GenBank accession, bar four RefSeq accessions p14 no longer lists and that the
    base release still names."""
    table = grch38_accessions()
    ucsc = {name for name, _ in table.values()}
    # 261 alt loci, and p14's 90 novel patches, which UCSC names *_alt as well.
    assert sum(1 for name in ucsc if name.endswith("_alt")) == 261 + 90
    assert sum(1 for name in ucsc if name.endswith("_fix")) == 164
    assert {f"chr{n}" for n in [*range(1, 23), "X", "Y", "M"]} <= ucsc
    assert table["NT_113891.3"] == ("chr6_GL000251v2_alt", 4795265)
    assert len(table) == 1418


def test_a_contig_is_renamed_only_when_its_length_matches():
    renamed = grch38_ucsc_names(
        {
            "NC_000006.12": 170805979,
            "NC_000006.11": 171115067,  # GRCh37's chromosome 6
            "NC_000022.11": 1234,  # not the sequence the accession names
            "NC_000010.11": None,
            "chr1": 248956422,
        }
    )
    assert renamed == {"NC_000006.12": "chr6"}


def test_accessions_name_their_chromosome_in_either_build():
    assert accession_chromosome("NC_000006.12") == "6"
    assert accession_chromosome("NC_000006.11") == "6"
    assert accession_chromosome("CM000668.2") == "6"
    assert accession_chromosome("NC_000023.11") == "X"
    assert accession_chromosome("CM000686.2") == "Y"
    assert accession_chromosome("NC_012920.1") == "MT"
    assert accession_chromosome("NT_113891.3") is None
    assert accession_chromosome("chr6") is None


def test_an_accession_named_header_is_grch38_or_grch37_by_its_lengths():
    grch38 = detect_reference_assembly(
        contig_lengths={
            "NC_000001.11": 248956422,
            "NT_187361.1": 175055,
            "NC_000006.12": 170805979,
        }
    )
    assert grch38["assembly"] == "GRCh38"
    grch37 = detect_reference_assembly(contig_lengths={"NC_000006.11": 171115067})
    assert grch37["assembly"] == "GRCh37"
    genbank = detect_reference_assembly(contig_lengths={"CM000684.2": 50818468})
    assert genbank["assembly"] == "GRCh38"


# --------------------------------------------------------------------------
# The renamed copy
# --------------------------------------------------------------------------

# Two short sequences under chr6's and chrM's accessions. write_renamed_copy applies
# a plan and does not check lengths; rename_plan, which does, is tested above.
PLAN = {"NC_000006.12": "chr6", "NC_012920.1": "chrM"}


def _alignment(tmp_path):
    """A coordinate-sorted BAM on NC_000006.12 and NC_012920.1, its CRAM and SAM, and
    a FASTA of the same sequences under their UCSC names to decode the CRAM with."""
    rng = random.Random(7)
    seqs = {name: "".join(rng.choice("ACGT") for _ in range(600)) for name in PLAN}
    header = {
        "HD": {"VN": "1.6", "SO": "coordinate"},
        "SQ": [{"SN": name, "LN": 600} for name in PLAN],
    }
    accession_fa = tmp_path / "accessions.fa"
    ucsc_fa = tmp_path / "ucsc.fa"
    accession_fa.write_text("".join(f">{n}\n{s}\n" for n, s in seqs.items()))
    ucsc_fa.write_text("".join(f">{PLAN[n]}\n{s}\n" for n, s in seqs.items()))
    pysam.faidx(str(accession_fa))
    pysam.faidx(str(ucsc_fa))
    bam = tmp_path / "sample.bam"
    with pysam.AlignmentFile(str(bam), "wb", header=header) as out:
        for i, (tid, pos) in enumerate([(0, 10), (0, 300), (1, 40)]):
            read = pysam.AlignedSegment(out.header)
            read.query_name = f"read{i}"
            read.reference_id = tid
            read.reference_start = pos
            read.query_sequence = seqs[list(PLAN)[tid]][pos : pos + 50]
            read.cigarstring = "50M"
            read.mapping_quality = 60
            read.query_qualities = pysam.qualitystring_to_array("I" * 50)
            out.write(read)
    cram = tmp_path / "sample.cram"
    pysam.view(
        "-C", "-T", str(accession_fa), "-o", str(cram), str(bam), catch_stdout=False
    )
    sam = tmp_path / "sample.sam"
    pysam.view("-h", "-o", str(sam), str(bam), catch_stdout=False)
    return SimpleNamespace(bam=bam, cram=cram, sam=sam, ucsc_fa=ucsc_fa)


def _contigs_and_reads(path, reference=None):
    with pysam.AlignmentFile(
        str(path), reference_filename=str(reference) if reference else None
    ) as handle:
        contigs = list(handle.references)
        reads = [(r.query_name, r.reference_name, r.reference_start) for r in handle]
    return contigs, reads


@pytest.mark.parametrize("kind", ["bam", "cram", "sam"])
def test_an_alignment_is_copied_under_ucsc_names_with_every_read(tmp_path, kind):
    files = _alignment(tmp_path)
    source = getattr(files, kind)

    copy = write_renamed_copy(str(source), kind, PLAN)

    assert copy == str(tmp_path / f"ucsc_sample.{kind}"), "same suffix, beside it"
    contigs, reads = _contigs_and_reads(copy, files.ucsc_fa)
    assert contigs == ["chr6", "chrM"]
    assert reads == [
        ("read0", "chr6", 10),
        ("read1", "chr6", 300),
        ("read2", "chrM", 40),
    ]


@pytest.mark.skipif(shutil.which("bcftools") is None, reason="needs bcftools")
@pytest.mark.parametrize("name", ["sample.vcf", "sample.vcf.gz"])
def test_a_vcf_is_copied_under_ucsc_names(tmp_path, name):
    text = (
        "##fileformat=VCFv4.2\n"
        "##contig=<ID=NC_000022.11,length=50818468>\n"
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
        "NC_000022.11\t42130692\t.\tG\tA\t.\tPASS\t.\tGT\t0/1\n"
    )
    plain = tmp_path / "sample.vcf"
    plain.write_text(text)
    source = plain
    if name.endswith(".gz"):
        pysam.tabix_compress(str(plain), str(tmp_path / name))
        source = tmp_path / name

    copy = write_renamed_copy(str(source), "vcf", {"NC_000022.11": "chr22"})

    with pysam.VariantFile(copy) as vcf:
        assert list(vcf.header.contigs) == ["chr22"]
        assert [(r.chrom, r.pos) for r in vcf] == [("chr22", 42130692)]


def test_names_the_pipeline_reads():
    for name in ("chr1", "chr22", "chrX", "chrM", "1", "X", "MT", "NC_000006.12"):
        assert names_a_chromosome(name), name
    for name in ("contig1", "scaffold_7", "chrUn_KI270302v1", "NT_113891.3", ""):
        assert not names_a_chromosome(name), name


# --------------------------------------------------------------------------
# At launch: the pipeline is handed the renamed copy
# --------------------------------------------------------------------------


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
        self.logs.append(log_data.message)


def _accession_header():
    return {
        "sequences": [
            {"name": "NC_000006.12", "length": 170805979},
            {"name": "NC_012920.1", "length": 16569},
        ]
    }


def test_a_grch38_accession_named_bam_is_renamed_before_the_pipeline_reads_it(
    monkeypatch, tmp_path
):
    files = _alignment(tmp_path)
    upload = files.bam
    job = _Job(upload)
    service = _JobService(job)
    captured = {}
    monkeypatch.setattr(ur, "JobService", lambda db: service)
    # The real header says 600 bp; the plan is made from what the inspector reports,
    # which for a real genome is the real length.
    monkeypatch.setattr(ur, "inspect_header", lambda path: _accession_header())
    monkeypatch.setattr(ur, "save_genomic_header", lambda db, path, ft, header: 1)
    monkeypatch.setattr(ur, "extract_raw_header_text", lambda path: None)
    monkeypatch.delenv("KEEP_UPLOADS", raising=False)

    def post(url, json=None, timeout=None):
        captured["payload"] = json
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

    renamed = captured["payload"]["input"]
    assert renamed == str(tmp_path / "ucsc_sample.bam")
    assert _contigs_and_reads(renamed)[0] == ["chr6", "chrM"]
    assert not upload.exists(), "the upload it replaces is not kept twice"
    # The copy's index is released with it when the job ends.
    assert job.job_metadata["file_paths"] == [str(upload), renamed, renamed + ".bai"]
    assert job.job_metadata["contigs_renamed"] == 2
    completed = [u for name, u in service.steps if name == "header_analysis"][-1]
    assert completed.output_data["contigs_renamed"]


def test_keep_uploads_keeps_the_original_beside_the_copy(monkeypatch, tmp_path):
    files = _alignment(tmp_path)
    service = _JobService(_Job(files.bam))
    monkeypatch.setenv("KEEP_UPLOADS", "true")

    renamed = asyncio.run(
        ur.use_ucsc_contig_names(
            service, "job-1", str(files.bam), "bam", _accession_header()
        )
    )

    assert renamed and files.bam.exists()


def test_a_file_named_the_pipelines_way_is_left_alone(tmp_path):
    files = _alignment(tmp_path)
    header = {"sequences": [{"name": "chr6", "length": 170805979}]}

    assert (
        asyncio.run(
            ur.use_ucsc_contig_names(
                _JobService(_Job(files.bam)), "job-1", str(files.bam), "bam", header
            )
        )
        is None
    )
    assert not (tmp_path / "ucsc_sample.bam").exists()


def test_no_copy_without_the_space_for_it(monkeypatch, tmp_path):
    files = _alignment(tmp_path)
    usage = shutil.disk_usage(tmp_path)
    monkeypatch.setattr(ur.shutil, "disk_usage", lambda path: usage._replace(free=1))

    with pytest.raises(RuntimeError, match="GB is free"):
        asyncio.run(
            ur.use_ucsc_contig_names(
                _JobService(_Job(files.bam)),
                "job-1",
                str(files.bam),
                "bam",
                _accession_header(),
            )
        )
    assert files.bam.exists()


def test_the_plan_needs_a_renamed_chromosome():
    assert rename_plan(_accession_header()["sequences"]) == {
        "NC_000006.12": "chr6",
        "NC_012920.1": "chrM",
    }
    assert rename_plan([{"name": "NT_113891.3", "length": 4795265}]) == {}


# --------------------------------------------------------------------------
# At upload: an alignment with no chromosome the pipeline can read is refused
# --------------------------------------------------------------------------


def _alignment_workflow(contigs, build=None):
    analysis = FileAnalysis(
        file_type=FileType.BAM,
        is_compressed=True,
        has_index=False,
        reference_genome=build,
        alignment_contigs=contigs,
    )
    return FileProcessor(temp_dir=os.getcwd()).determine_workflow(analysis)


def test_an_alignment_with_no_readable_chromosome_is_refused():
    workflow = _alignment_workflow(["contig_1", "contig_2", "contig_3", "contig_4"])

    assert workflow["unsupported"] and not workflow["is_provisional"]
    reason = workflow["unsupported_reason"]
    assert "contig_1, contig_2, contig_3, ..." in reason
    assert "NC_000001.11" in reason
    assert ur._unanalysable_upload_reason(workflow) == reason


@pytest.mark.parametrize(
    "contigs, build",
    [
        (["chr22"], "GRCh38"),
        (["NC_000006.12", "NT_113891.3"], "GRCh38"),
        (["6"], None),
        ([], None),  # nothing to judge by; left as before
    ],
)
def test_an_alignment_with_a_readable_chromosome_is_not(contigs, build):
    assert not _alignment_workflow(contigs, build).get("unsupported")


def test_a_copy_that_cannot_be_indexed_says_so(monkeypatch, tmp_path):
    """Not "Could not rename": the renaming worked, the index did not."""
    from pathlib import Path

    from app.genome.contig_names import RenamedCopyNotIndexed

    files = _alignment(tmp_path)

    def unindexable(path, file_type, plan):
        Path(ur.renamed_path(path)).write_bytes(b"copy")
        raise RenamedCopyNotIndexed("[bam_index_core] unsorted positions")

    monkeypatch.setattr(ur, "write_renamed_copy", unindexable)
    with pytest.raises(RuntimeError, match="sorted by coordinate") as caught:
        asyncio.run(
            ur.use_ucsc_contig_names(
                _JobService(_Job(files.bam)),
                "job-1",
                str(files.bam),
                "bam",
                _accession_header(),
            )
        )
    assert "Could not rename" not in str(caught.value)
    assert "unsorted positions" in str(caught.value)
    assert not Path(ur.renamed_path(str(files.bam))).exists()
