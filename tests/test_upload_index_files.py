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
from app.api.utils.file_utils import has_index_file, matching_index

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
