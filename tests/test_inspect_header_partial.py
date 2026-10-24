"""The header preview reads a file's start, not the whole file.

View Header posted the whole file to /upload/inspect-header: a whole genome's BAM is
tens of GB, sent over the network and written to disk only for its header to be read.
Every read the preview makes is bounded to the file's beginning (the header, and the
first records the gVCF and FASTQ checks look at), so the page now sends the first
16 MiB and the file's size. The server reads the size from that field, the one thing
a file's start cannot tell (the FASTQ size limit turns on it), and says when the start
ended before the header did, so the page sends a longer one.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
INDEX_HTML = REPO / "app" / "templates" / "index.html"
DEMO_CRAM = REPO / "app" / "static" / "demo" / "NA12878.pgx-demo.cram"
SAM = REPO / "test_data" / "pgx_ngs_example.sam"
VCF = REPO / "test_data" / "pharmcat.example.vcf"


def _inspect(client, name, body, file_size=None):
    data = {} if file_size is None else {"file_size": str(file_size)}
    response = client.post(
        "/upload/inspect-header",
        files={"file": (name, body, "application/octet-stream")},
        data=data,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_a_whole_genome_crams_start_reads_as_the_whole_file(client):
    whole = DEMO_CRAM.read_bytes()
    start = _inspect(client, DEMO_CRAM.name, whole[: 16 * 1024 * 1024], len(whole))
    full = _inspect(client, DEMO_CRAM.name, whole)

    assert start["header_incomplete"] is False
    assert start["file_size"] == len(whole) == full["file_size"]
    assert start["bytes_inspected"] == 16 * 1024 * 1024
    assert start["header_info"]["file_info"]["size"] == len(whole)
    for key in ("sequences", "sample", "samples", "format_specific"):
        assert start["header_info"].get(key) == full["header_info"].get(key)
    assert len(start["header_info"]["sequences"]) > 3000  # GRCh38 with ALTs, decoys, HLA
    assert (
        start["header_info"]["metadata"]["reference_genome"]
        == full["header_info"]["metadata"]["reference_genome"]
    )
    assert start["compat"]["workflow"] == full["compat"]["workflow"]


def test_a_start_that_ends_inside_the_header_asks_for_more(client):
    whole = DEMO_CRAM.read_bytes()
    assert _inspect(client, DEMO_CRAM.name, whole[:65536], len(whole))["header_incomplete"]

    sam = SAM.read_bytes()
    # A SAM cut inside its header reads without an error, with some of its @SQ lines.
    assert _inspect(client, SAM.name, sam[:4096], len(sam))["header_incomplete"]
    assert not _inspect(client, SAM.name, sam[:65536], len(sam))["header_incomplete"]


def test_a_vcf_cut_before_its_chrom_line_asks_for_more(client):
    vcf = VCF.read_bytes()
    chrom = vcf.index(b"\n#CHROM")
    assert _inspect(client, VCF.name, vcf[:chrom], len(vcf))["header_incomplete"]
    start = _inspect(client, VCF.name, vcf[: chrom + 200], len(vcf))
    assert not start["header_incomplete"] and start["header_info"]["samples"]


def test_a_whole_file_is_read_as_before(client):
    vcf = VCF.read_bytes()
    answer = _inspect(client, VCF.name, vcf)
    assert answer["header_incomplete"] is False
    assert answer["file_size"] == answer["bytes_inspected"] == len(vcf)


def test_the_fastq_size_limit_reads_the_declared_size(client):
    reads = "".join(
        f"@A00123:45:HXXXXDSXX:1:1101:{1000 + i}:2000 1:N:0:ATCACG\n{'A' * 151}\n+\n{'I' * 151}\n"
        for i in range(200)
    ).encode()
    small = _inspect(client, "sample.fastq", reads, len(reads))
    assert not small["compat"]["workflow"]["unsupported"]

    huge = _inspect(client, "sample.fastq", reads, 40 * 1024**3)
    workflow = huge["compat"]["workflow"]
    assert workflow["unsupported"] and "40.0 GB" in workflow["unsupported_reason"]


def test_the_page_sends_the_files_start_and_size():
    page = INDEX_HTML.read_text(encoding="utf-8")
    assert "file.slice(0, sliceBytes)" in page
    assert "formData.append('file', part, file.name)" in page
    assert "formData.append('file_size', String(file.size))" in page
    assert "result.header_incomplete && part !== file" in page
    assert "formData.append('file', file);" not in page
