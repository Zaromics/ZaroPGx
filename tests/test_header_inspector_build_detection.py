"""Genome-build detection from a genomic file header.

The rule these tests pin: structured evidence decides the assembly. The
``##contig=<ID=...,length=...>`` records describe the coordinate system every
position in the file is expressed in; the free-text ``##reference=`` line
describes whatever the tool that wrote the file was pointed at, which survives a
liftover unchanged. So contig lengths outrank the reference line, and a header
that names two builds is reported as undetectable rather than resolved to
whichever appeared first -- a wrong assembly here means the sample is analysed
against the wrong coordinates with nothing in the output saying so.
"""

import pytest

from app.api.utils import header_inspector
from app.api.utils.header_inspector import (
    GenomicHeaderInspector,
    detect_reference_assembly,
    inspect_header,
    parse_vcf_contig_lengths,
)

# Assembly-defining lengths, as they appear in a real header.
GRCH38_CONTIGS = [
    "##contig=<ID=chr1,length=248956422>",
    "##contig=<ID=chr2,length=242193529>",
    "##contig=<ID=chr3,length=198295559>",
    "##contig=<ID=chrX,length=156040895>",
]
GRCH37_CONTIGS = [
    "##contig=<ID=chr1,length=249250621>",
    "##contig=<ID=chr2,length=243199373>",
    "##contig=<ID=chr3,length=198022430>",
    "##contig=<ID=chrX,length=155270560>",
]
B37_CONTIGS = [
    "##contig=<ID=1,length=249250621>",
    "##contig=<ID=2,length=243199373>",
    "##contig=<ID=X,length=155270560>",
]


def test_contig_lengths_beat_a_stale_reference_line():
    """The lifted-over case: GRCh38 records, a ##reference= left over from hg19."""
    header = (
        ["##fileformat=VCFv4.2", "##reference=file:///refs/hg19/ucsc.hg19.fasta"]
        + GRCH38_CONTIGS
        + ["#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE1"]
    )

    result = detect_reference_assembly(header_records=header)

    assert result["assembly"] == "GRCh38"
    assert result["source"] == "contig_lengths"
    assert result["ambiguous"] is False


def test_contig_records_naming_two_builds_are_undetectable():
    """A header whose own records disagree is not resolved to the first match."""
    header = [
        "##fileformat=VCFv4.2",
        "##contig=<ID=chr1,length=248956422>",  # GRCh38
        "##contig=<ID=chr2,length=243199373>",  # GRCh37
    ]

    result = detect_reference_assembly(header_records=header)

    assert result["assembly"] is None
    assert result["ambiguous"] is True
    assert result["candidates"] == ["GRCh37", "GRCh38"]


def test_reference_line_naming_two_builds_is_undetectable():
    """Free text naming both builds is a conflict, not a first-match win."""
    header = [
        "##fileformat=VCFv4.2",
        "##reference=file:///refs/hg19_to_hg38_lifted.fasta",
    ]

    result = detect_reference_assembly(header_records=header)

    assert result["assembly"] is None
    assert result["ambiguous"] is True
    assert result["candidates"] == ["GRCh37", "GRCh38"]


def test_two_reference_lines_naming_two_builds_are_undetectable():
    header = [
        "##reference=file:///refs/ucsc.hg19.fasta",
        "##reference=file:///refs/Homo_sapiens_assembly38.fasta",
    ]

    result = detect_reference_assembly(header_records=header)

    assert result["assembly"] is None
    assert result["ambiguous"] is True


def test_plain_hg19_reference_line_is_detected_as_grch37():
    """No contig lengths to go on, so the reference line breaks the tie."""
    header = [
        "##fileformat=VCFv4.2",
        "##reference=file:///refs/hg19/ucsc.hg19.fasta",
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO",
    ]

    result = detect_reference_assembly(header_records=header)

    assert result["assembly"] == "GRCh37"
    assert result["source"] == "reference_line"
    assert result["ambiguous"] is False


@pytest.mark.parametrize(
    "value,expected",
    [
        ("file:///refs/hg19/ucsc.hg19.fasta", "GRCh37"),
        ("/reference/grch37/human_g1k_v37.fasta", "GRCh37"),
        ("/reference/bundle/Homo_sapiens_assembly19.fasta", "GRCh37"),
        ("/reference/hg38/Homo_sapiens_assembly38.fasta", "GRCh38"),
        ("file:///refs/GRCh38.d1.vd1.fa", "GRCh38"),
    ],
)
def test_reference_files_in_use_are_recognised(value, expected):
    """The names this deployment's own reference files actually carry."""
    result = detect_reference_assembly(header_records=[f"##reference={value}"])

    assert result["assembly"] == expected
    assert result["source"] == "reference_line"


def test_hg19_contig_set_is_detected_as_grch37():
    result = detect_reference_assembly(header_records=GRCH37_CONTIGS)

    assert result["assembly"] == "GRCh37"
    assert result["source"] == "contig_lengths"


def test_hg38_contig_set_is_detected_as_grch38():
    """No ##reference= line at all -- the records are enough."""
    result = detect_reference_assembly(header_records=GRCH38_CONTIGS)

    assert result["assembly"] == "GRCh38"
    assert result["source"] == "contig_lengths"
    assert result["ambiguous"] is False


def test_unprefixed_b37_contig_names_match_the_same_table():
    """`1` and `chr1` are the same chromosome for the purpose of the lengths."""
    result = detect_reference_assembly(header_records=B37_CONTIGS)

    assert result["assembly"] == "GRCh37"
    assert result["source"] == "contig_lengths"


def test_a_reference_line_cannot_rescue_conflicting_contigs():
    """Free text is a tie-breaker, never an override of the records."""
    header = [
        "##reference=file:///refs/Homo_sapiens_assembly38.fasta",
        "##contig=<ID=chr1,length=248956422>",  # GRCh38
        "##contig=<ID=chrX,length=155270560>",  # GRCh37
    ]

    result = detect_reference_assembly(header_records=header)

    assert result["assembly"] is None
    assert result["ambiguous"] is True


def test_conflicting_contigs_across_naming_conventions_are_undetectable():
    """The conflict must be seen even when the two contigs use different prefixes.

    A file that carries `chr1` at its GRCh38 length and an unprefixed `2` at its
    GRCh37 length only reads as a conflict if both names are normalised into the
    same table *before* the disagreement is judged. Every other ambiguity test
    keeps one naming convention throughout; this one crosses it, so a regression
    that compared raw names would resolve to a single build here and silently
    analyse against the wrong coordinates.
    """
    header = [
        "##contig=<ID=chr1,length=248956422>",  # GRCh38, chr-prefixed
        "##contig=<ID=2,length=243199373>",  # GRCh37, unprefixed
    ]

    result = detect_reference_assembly(header_records=header)

    assert result["assembly"] is None
    assert result["ambiguous"] is True
    assert result["candidates"] == ["GRCh37", "GRCh38"]


def test_no_evidence_is_undetectable_but_not_ambiguous():
    header = ["##fileformat=VCFv4.2", "##contig=<ID=chrUn_KI270302v1,length=2274>"]

    result = detect_reference_assembly(header_records=header)

    assert result["assembly"] is None
    assert result["ambiguous"] is False
    assert result["candidates"] == []


def test_contig_lengths_may_be_supplied_directly():
    """The bcftools fallback already parses them; the SAM path has them too."""
    result = detect_reference_assembly(contig_lengths={"chr1": 248956422, "2": None})

    assert result["assembly"] == "GRCh38"
    assert result["source"] == "contig_lengths"


@pytest.mark.parametrize(
    "line",
    [
        "##contig=<ID=chr1,length=248956422>",
        "##contig=<ID=chr1,assembly=GRCh37,length=248956422>",
        "##contig=<ID=chr1,length=248956422,md5=2648ae1bacce4ec4b6cf337dcae37816>",
    ],
)
def test_contig_lines_are_parsed_field_wise(line):
    """Field order varies and other fields may carry a build name of their own."""
    assert parse_vcf_contig_lengths([line]) == {"chr1": 248956422}

    assert detect_reference_assembly(header_records=[line])["assembly"] == "GRCh38"


def test_contig_line_without_a_length_is_kept_without_one():
    assert parse_vcf_contig_lengths(["##contig=<ID=chr1>"]) == {"chr1": None}
    assert parse_vcf_contig_lengths(["##contig=<ID=chr1,length=nope>"]) == {
        "chr1": None
    }


def test_non_string_records_are_ignored():
    assert detect_reference_assembly(header_records=[None, 42])["assembly"] is None


def _canned_vcf(monkeypatch, tmp_path, header_records):
    """Point inspect_header's VCF reader at a header we control.

    pysam and bcftools are both optional here (neither is installed in the test
    environment), so the reader is stubbed rather than fed a real file.
    """
    path = tmp_path / "sample.vcf"
    path.write_text("\n".join(header_records) + "\n", encoding="utf-8")

    def _fake_inspect(self, filepath):
        return {
            "format": "VCF/BCF",
            "file": filepath,
            "samples": ["SAMPLE1"],
            "num_samples": 1,
            "contigs": list(parse_vcf_contig_lengths(header_records).keys()),
            "info_fields": [],
            "format_fields": [],
            "filter_fields": [],
            "header_records": list(header_records),
        }

    monkeypatch.setattr(GenomicHeaderInspector, "_inspect_vcf_bcf", _fake_inspect)
    return path


def test_inspect_header_reports_the_contig_evidence_not_the_stale_reference(
    monkeypatch, tmp_path
):
    header = [
        "##fileformat=VCFv4.2",
        "##reference=file:///refs/hg19/ucsc.hg19.fasta",
    ] + GRCH38_CONTIGS
    path = _canned_vcf(monkeypatch, tmp_path, header)

    metadata = inspect_header(str(path))["metadata"]

    assert metadata["reference_genome"] == "GRCh38"
    # The path itself is still reported verbatim -- it is what the file says.
    assert metadata["reference_genome_path"] == "file:///refs/hg19/ucsc.hg19.fasta"


def test_inspect_header_reports_an_ambiguous_header_as_undetectable(
    monkeypatch, tmp_path
):
    header = [
        "##fileformat=VCFv4.2",
        "##contig=<ID=chr1,length=248956422>",
        "##contig=<ID=chr2,length=243199373>",
    ]
    path = _canned_vcf(monkeypatch, tmp_path, header)

    metadata = inspect_header(str(path))["metadata"]

    assert metadata["reference_genome"] is None


def test_inspect_header_fills_sequence_lengths_from_the_contig_records(
    monkeypatch, tmp_path
):
    path = _canned_vcf(monkeypatch, tmp_path, GRCH38_CONTIGS)

    sequences = inspect_header(str(path))["sequences"]

    assert {"name": "chr1", "length": 248956422} in sequences


def _canned_bam(monkeypatch, tmp_path, sq_records):
    """Point inspect_header's alignment reader at an @SQ dictionary we control."""
    path = tmp_path / "sample.bam"
    path.write_bytes(b"not really a BAM")

    def _fake_inspect(self, filepath):
        return {
            "format": "SAM/BAM/CRAM",
            "file": filepath,
            "header_dict": {"SQ": list(sq_records)},
        }

    monkeypatch.setattr(header_inspector, "_HAS_PYSAM", True)
    monkeypatch.setattr(GenomicHeaderInspector, "_inspect_sam_bam_cram", _fake_inspect)
    return path


def test_bam_sq_records_name_the_assembly(monkeypatch, tmp_path):
    """The @SQ dictionary is the same evidence a ##contig record is."""
    path = _canned_bam(
        monkeypatch,
        tmp_path,
        [{"SN": "chr1", "LN": 248956422}, {"SN": "chrX", "LN": 156040895}],
    )

    metadata = inspect_header(str(path))["metadata"]

    assert metadata["reference_genome"] == "GRCh38"
    assert metadata["reference_genome_source"] == "contig_lengths"


def test_bam_sq_records_naming_two_assemblies_are_undetectable(monkeypatch, tmp_path):
    path = _canned_bam(
        monkeypatch,
        tmp_path,
        [{"SN": "chr1", "LN": 248956422}, {"SN": "chrX", "LN": 155270560}],
    )

    metadata = inspect_header(str(path))["metadata"]

    assert metadata["reference_genome"] is None
    assert metadata["reference_genome_ambiguous"] is True
