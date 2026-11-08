"""An index chosen with its data file is recognised, on the page and in the upload.

Choosing sample.cram and sample.crai, the user was told the file had no index:
the upload accepted the index and dropped it without a word, the header preview
sent only the first file chosen -- often the index itself, since sample.crai sorts
before sample.cram, so the preview called the upload unrecognisable -- and the
check for an index beside a file looked only for sample.bai, never the names
samtools and tabix write (sample.bam.bai, sample.cram.crai, sample.vcf.gz.tbi).

The pipeline makes its own index from the data, so an uploaded one is named and not
used; one that indexes some other file is a warning.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.api.utils.file_processor import FileProcessor, uploaded_index_messages
from app.api.utils.file_utils import (
    bam_index_mismatch,
    has_index_file,
    matching_index,
)
from app.reports.marks import CAUTION_SVG

VCF_BYTES = (
    b"##fileformat=VCFv4.2\n"
    b"##contig=<ID=chr10,length=133797422>\n"
    b'##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
    b"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
    b"chr10\t94761900\trs12248560\tC\tT\t100\tPASS\t.\tGT\t0/1\n"
)
INDEX_HTML = Path(__file__).resolve().parents[1] / "app" / "templates" / "index.html"


@pytest.mark.parametrize(
    "data, index, found",
    [
        ("s.bam", "s.bam.bai", True),
        ("s.bam", "s.bai", True),
        ("s.bam", "s.bam.csi", True),
        ("s.cram", "s.cram.crai", True),
        ("s.cram", "s.crai", True),
        ("s.vcf.gz", "s.vcf.gz.tbi", True),
        ("s.vcf.gz", "s.vcf.gz.csi", True),
        # The wrong kind of index, or another file's.
        ("s.cram", "s.cram.bai", False),
        ("s.bam", "t.bam.bai", False),
    ],
)
def test_an_index_is_found_by_either_name(tmp_path, data, index, found):
    (tmp_path / data).write_bytes(b"")
    (tmp_path / index).write_bytes(b"")
    assert has_index_file(tmp_path / data) is found
    assert (matching_index(data, [index]) == index) is found


def test_messages_name_the_index_and_flag_a_stranger():
    paired, notes, warnings = uploaded_index_messages(
        "sample.cram", ["sample.crai", "other<b>.bai"]
    )
    assert paired == "sample.crai"
    assert notes == [
        "<p>Index sample.crai matches sample.cram. The pipeline indexes the data "
        "itself, so this index is not used.</p>"
    ]
    (warning,) = warnings
    assert "other&lt;b&gt;.bai is not an index of sample.cram" in warning
    assert "sample.cram.crai or sample.crai" in warning


class _Upload:
    def __init__(self, filename: str, content: bytes):
        self.filename = filename
        self._content = content

    async def read(self, _size: int = -1) -> bytes:
        content, self._content = self._content, b""
        return content


def test_the_upload_recognises_the_index(tmp_path):
    processor = FileProcessor(temp_dir=str(tmp_path))
    result = asyncio.run(
        processor.process_files(
            # The index first, as a file list commonly has it.
            [_Upload("sample.vcf.idx", b"idx"), _Upload("sample.vcf", VCF_BYTES)],
            reference_genome="hg38",
        )
    )
    assert result["success"], result.get("error")
    assert result["file_analysis"].has_index is True
    messages = " ".join(
        result["workflow"]["recommendations"] + result["workflow"]["warnings"]
    )
    assert "Index sample.vcf.idx matches sample.vcf" in messages
    assert "TO DO" not in messages


def test_the_header_preview_recognises_the_index(client):
    response = client.post(
        "/upload/inspect-header",
        files={"file": ("sample.vcf", VCF_BYTES, "application/octet-stream")},
        data={"index_names": ["sample.vcf.idx", "other.tbi"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["header_info"]["file_info"]["has_index"] is True
    workflow = body["compat"]["workflow"]
    assert any("Index sample.vcf.idx" in r for r in workflow["recommendations"])
    assert any("other.tbi is not an index" in w for w in workflow["warnings"])


def test_the_preview_sends_the_data_file_not_the_first_chosen():
    """The page's own code: the preview takes the first file that is not an index,
    as the upload does, and names the index files."""
    page = INDEX_HTML.read_text(encoding="utf-8")
    assert "fileInput.files[0]" not in page
    assert "selected.find(f => !isIndex(f))" in page
    assert "formData.append('index_names'" in page


# --------------------------------------------------------------------------
# A BAM's index is used, once it is read against the data
# --------------------------------------------------------------------------

BAM = Path(__file__).resolve().parents[1] / "test_data" / "pgx_ngs_example.bam"
OTHER_BAM = (
    Path(__file__).resolve().parents[1] / "test_data" / "pgx_wgs_hla_example.bam"
)


def test_a_bams_own_index_indexes_it():
    assert bam_index_mismatch(BAM, Path(f"{BAM}.bai")) is None


def test_a_bam_whose_sequences_come_out_of_header_order_is_indexed_by_its_own(
    tmp_path,
):
    """htslib indexes a file whose sequences' blocks are in another order than the
    header's -- a cut of chr22 then chr10, as the NA12878 cut this was found on --
    so its own index must not be refused for it."""
    import pysam

    cut = tmp_path / "cut.bam"
    with pysam.AlignmentFile(str(BAM)) as source:
        reads = list(source)
        with pysam.AlignmentFile(str(cut), "wb", template=source) as out:
            for read in reads[:10]:
                moved = pysam.AlignedSegment.from_dict(read.to_dict(), out.header)
                moved.reference_name = "chr22"
                out.write(moved)
            for read in reads[10:]:
                out.write(read)
    pysam.index(str(cut))
    with pysam.AlignmentFile(str(cut)) as handle:
        assert next(handle).reference_name == "chr22"
    assert bam_index_mismatch(cut, Path(f"{cut}.bai")) is None


def test_an_index_that_holds_none_of_the_files_reads_does_not_index_it(tmp_path):
    """The index of a file with the same header and no reads: every fetch through
    it finds nothing, which would read as "no reads here" in every step."""
    import pysam

    empty = tmp_path / "empty.bam"
    with pysam.AlignmentFile(str(BAM)) as source:
        with pysam.AlignmentFile(str(empty), "wb", template=source):
            pass
    pysam.index(str(empty))
    assert bam_index_mismatch(BAM, Path(f"{empty}.bai")) == (
        "it does not find the file's first read, on chr10"
    )


def test_a_csi_index_is_read_too(tmp_path):
    import pysam

    index = tmp_path / "sample.bam.csi"
    pysam.index("-c", str(BAM), str(index))
    assert bam_index_mismatch(BAM, index) is None


def test_another_bams_index_does_not_index_it():
    """Same header, other reads: the fixture BAMs share their sequence dictionary."""
    assert (
        bam_index_mismatch(BAM, Path(f"{OTHER_BAM}.bai"))
        == "reading the file through it failed"
    )


def test_a_stale_index_of_the_same_name_does_not_index_it(tmp_path):
    """The case a name match cannot catch: the file was rewritten after it was
    indexed. Here every read moves 1 kb along, so the offsets point at the same
    bytes they always did, and the reads there are not where the index says."""
    import pysam

    stale_index = Path(f"{BAM}.bai")
    rewritten = tmp_path / "sample.bam"
    with pysam.AlignmentFile(str(BAM)) as source:
        with pysam.AlignmentFile(str(rewritten), "wb", template=source) as out:
            for read in source:
                read.reference_start += 1000
                out.write(read)
    refusal = bam_index_mismatch(rewritten, stale_index)
    assert refusal == "reading the file through it failed"
    assert str(tmp_path) not in refusal, "the reason is shown on the page"


@pytest.mark.parametrize(
    "content",
    [b"", b"not an index", b"BAI\x01", b"\x1f\x8b\x08\x04garbage"],
)
def test_a_file_that_is_no_index_does_not_index_it(tmp_path, content):
    index = tmp_path / "sample.bam.bai"
    index.write_bytes(content)
    assert bam_index_mismatch(BAM, index) == "it is not a BAM index"


def test_preview_and_upload_say_what_happens_to_a_bams_index():
    _, preview, _ = uploaded_index_messages("sample.bam", ["sample.bai"])
    assert preview == [
        "<p>Index sample.bai matches sample.bam. It is used if it indexes "
        "sample.bam, which is checked when the file is uploaded.</p>"
    ]
    paired, used, warnings = uploaded_index_messages(
        "sample.bam", ["sample.bai"], checked=True
    )
    assert (paired, used, warnings) == (
        "sample.bai",
        ["<p>Index sample.bai matches sample.bam and is used.</p>"],
        [],
    )
    paired, notes, (warning,) = uploaded_index_messages(
        "sample.bam", ["sample.bai"], checked=True, refusal="it <covers> 2"
    )
    assert paired is None and notes == []
    assert warning == (
        f"<p>{CAUTION_SVG} sample.bai does not index sample.bam (it &lt;covers&gt; 2) and was "
        "not used. The pipeline indexes the data itself.</p>"
    )


def _bam_upload(processor, index_name, index_bytes):
    return asyncio.run(
        processor.process_files(
            [
                _Upload(index_name, index_bytes),
                _Upload("sample.bam", BAM.read_bytes()),
            ],
            reference_genome="hg38",
        )
    )


def test_the_upload_puts_a_bams_index_where_every_step_finds_it(tmp_path):
    from app.utils.shared_input import current_index

    processor = FileProcessor(temp_dir=str(tmp_path))
    result = _bam_upload(processor, "sample.bam.bai", Path(f"{BAM}.bai").read_bytes())
    assert result["success"], result.get("error")

    upload, index = (Path(p) for p in result["file_paths"])
    assert index == Path(f"{upload}.bai")
    assert current_index(upload) == (index, ".bai")
    assert result["file_analysis"].has_index is True
    workflow = result["workflow"]
    assert "<p>Index sample.bam.bai matches sample.bam and is used.</p>" in (
        workflow["recommendations"]
    )
    assert not any("Creating index" in r for r in workflow["recommendations"])


def test_the_upload_drops_an_index_that_does_not_index_the_bam(tmp_path):
    processor = FileProcessor(temp_dir=str(tmp_path))
    result = _bam_upload(processor, "sample.bai", Path(f"{OTHER_BAM}.bai").read_bytes())
    assert result["success"], result.get("error")

    (upload,) = (Path(p) for p in result["file_paths"])
    assert sorted(p.name for p in tmp_path.iterdir()) == [upload.name]
    assert result["file_analysis"].has_index is False
    workflow = result["workflow"]
    assert (
        f"<p>{CAUTION_SVG} sample.bai does not index sample.bam (reading the file through it "
        "failed) and was not used. The pipeline indexes the data itself.</p>"
    ) in workflow["warnings"]
    assert not any("is used" in r for r in workflow["recommendations"])
