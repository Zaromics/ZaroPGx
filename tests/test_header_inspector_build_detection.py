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


@pytest.mark.parametrize(
    "contig,length,expected",
    [
        # chr22/CYP2D6 and chr10/CYP2C19-CYP2C9 are the two that matter most: a
        # PGx panel that carries nothing else must still resolve to a build.
        ("chr22", 50818468, "GRCh38"),
        ("chr22", 51304566, "GRCh37"),
        ("chr10", 133797422, "GRCh38"),
        ("chr10", 135534747, "GRCh37"),
        ("chr12", 133275309, "GRCh38"),  # SLCO1B1
        ("chr12", 133851895, "GRCh37"),
        ("chr16", 90338345, "GRCh38"),  # VKORC1
        ("chr16", 90354753, "GRCh37"),
        ("chr6", 170805979, "GRCh38"),  # HLA class I
        ("chr6", 171115067, "GRCh37"),
        ("chr7", 159345973, "GRCh38"),  # CYP3A4/CYP3A5
        ("chr7", 159138663, "GRCh37"),
        ("chr19", 58617616, "GRCh38"),  # CYP4F2
        ("chr19", 59128983, "GRCh37"),
    ],
)
def test_pgx_chromosomes_alone_identify_the_build(contig, length, expected):
    """A gene-panel file carries no chr1/2/3/X, only the PGx chromosomes.

    Before these entries such a file read as "no evidence" and was analysed as
    GRCh38 whatever build it was on. Lengths re-read from the shipped sequence
    dictionaries on 2026-08-29.
    """
    result = detect_reference_assembly(
        header_records=[f"##contig=<ID={contig},length={length}>"]
    )

    assert result["assembly"] == expected
    assert result["source"] == "contig_lengths"
    assert result["ambiguous"] is False


def test_unprefixed_pgx_chromosome_matches_too():
    """b37 panel data spells it `22`, and must reach the same verdict."""
    assert (
        detect_reference_assembly(contig_lengths={"22": 51304566})["assembly"]
        == "GRCh37"
    )


def test_no_length_is_shared_between_the_builds():
    """Every entry must be decisive: a length may not name two builds.

    Checked as a MISSING ROW rather than as a duplicate, which is the only shape
    the defect can take. CONTIG_LENGTH_ASSEMBLIES is keyed on (name, length), so
    a collision cannot be *expressed*: two literals with the same key make one
    dict entry and the earlier build silently disappears. The previous version of
    this test grouped by that same key and could therefore never fail.

    Every chromosome in the table carries one row per build. If a future length
    were transcribed from the wrong column and happened to equal another build's,
    that chromosome would come up one build short here.

    GRCh37/GRCh38 verified against the shipped sequence dictionaries on
    2026-08-29; T2T-CHM13v2 against UCSC hs1.chrom.sizes and NCBI GCF_009914755.1
    on 2026-08-31.
    """
    from app.api.utils.header_inspector import CONTIG_LENGTH_ASSEMBLIES

    builds = sorted(set(CONTIG_LENGTH_ASSEMBLIES.values()))
    by_name = {}
    for (name, _length), assembly in CONTIG_LENGTH_ASSEMBLIES.items():
        by_name.setdefault(name, []).append(assembly)

    for name, assemblies in sorted(by_name.items()):
        assert sorted(assemblies) == builds, (
            f"contig {name} names {sorted(assemblies)}, not {builds}: a length "
            f"collision has swallowed a row"
        )


# ---------------------------------------------------------------------------
# T2T-CHM13v2: recognised so that it can be refused
# ---------------------------------------------------------------------------
T2T_CONTIGS = [
    "##contig=<ID=chr1,length=248387328>",
    "##contig=<ID=chr2,length=242696752>",
    "##contig=<ID=chr3,length=201105948>",
    "##contig=<ID=chrX,length=154259566>",
]


def test_t2t_contig_set_is_detected_from_the_lengths_alone():
    """A CHM13 file used to match nothing here and read as "no evidence", which
    determine_workflow treats as "not GRCh37, carry on" -- i.e. analysed as
    GRCh38, on coordinates hundreds of kb from the pharmacogenes' real ones."""
    result = detect_reference_assembly(header_records=T2T_CONTIGS)

    assert result["assembly"] == "T2T-CHM13v2"
    assert result["source"] == "contig_lengths"
    assert result["ambiguous"] is False


@pytest.mark.parametrize(
    "contig,length",
    [
        ("chr22", 51324926),  # CYP2D6
        ("chr10", 134758134),  # CYP2C19/CYP2C9
        ("chr12", 133324548),  # SLCO1B1
        ("chr16", 96330374),  # VKORC1
        ("chr6", 172126628),  # HLA class I
        ("chr7", 160567428),  # CYP3A4/CYP3A5
        ("chr19", 61707364),  # CYP4F2
    ],
)
def test_a_t2t_pgx_chromosome_alone_identifies_the_build(contig, length):
    """Same reason the GRCh37/38 PGx rows exist: a panel carries no chr1/2/3/X."""
    result = detect_reference_assembly(
        header_records=[f"##contig=<ID={contig},length={length}>"]
    )

    assert result["assembly"] == "T2T-CHM13v2"
    assert result["ambiguous"] is False


@pytest.mark.parametrize(
    "value",
    [
        "/references/chm13/chm13v2.0.fa",
        "file:///refs/chm13v2.0_maskedY_rCRS.fa",
        "/refs/T2T-CHM13v2.0.fasta",
        "/data/hs1.fa",
        "/refs/GCA_009914755.4.fna",
        "/refs/GCF_009914755.1.fna",
    ],
)
def test_t2t_reference_line_spellings_seen_in_the_wild(value):
    """No contig lengths to go on, so the reference line breaks the tie."""
    result = detect_reference_assembly(header_records=[f"##reference={value}"])

    assert result["assembly"] == "T2T-CHM13v2"
    assert result["source"] == "reference_line"


def test_t2t_chrm_is_not_evidence_of_anything():
    """chrM is 16569 in CHM13 and GRCh38 alike, so it is deliberately absent from
    the table: adding it would make every GRCh38 file ambiguous, and it does not
    mean the two are interchangeable -- CHM13's chrM is rCRS rotated 576 bp (see
    app/mtdna/builds.py, which refuses it on the build label)."""
    result = detect_reference_assembly(
        header_records=["##contig=<ID=chrM,length=16569>"]
    )

    assert result["assembly"] is None
    assert result["candidates"] == []


def test_grch38_detection_is_unchanged_by_the_t2t_rows():
    """The negative control for the whole table addition."""
    assert (
        detect_reference_assembly(header_records=GRCH38_CONTIGS)["assembly"] == "GRCh38"
    )
    assert (
        detect_reference_assembly(header_records=GRCH37_CONTIGS)["assembly"] == "GRCh37"
    )
    assert detect_reference_assembly(header_records=B37_CONTIGS)["assembly"] == "GRCh37"


def test_a_header_naming_grch38_and_t2t_is_undetectable():
    header = [
        "##contig=<ID=chr1,length=248956422>",  # GRCh38
        "##contig=<ID=chr2,length=242696752>",  # T2T-CHM13v2
    ]

    result = detect_reference_assembly(header_records=header)

    assert result["assembly"] is None
    assert result["ambiguous"] is True
    assert result["candidates"] == ["GRCh38", "T2T-CHM13v2"]


def test_the_shipped_t2t_fixture_is_detected_as_t2t():
    """test_data/t2t_chm13_pgx_snps.vcf, read by the detector that has to name it.

    This is the DETECTION half only. The refusal half lives in
    tests/test_t2t_chm13_refusal.py, which drives this same fixture's header through
    detect_reference_assembly and into determine_workflow -- deliberately, because for a
    while the two halves were joined by nothing: the refusal tests passed the build name
    in as a string, so every T2T row could be deleted from CONTIG_LENGTH_ASSEMBLIES and
    all twelve of them stayed green.
    """
    from pathlib import Path

    from app.api.utils.header_inspector import (
        parse_vcf_contig_lengths,
        reference_values_from_header,
    )

    fixture = (
        Path(__file__).resolve().parents[1] / "test_data" / "t2t_chm13_pgx_snps.vcf"
    )
    records = fixture.read_text(encoding="utf-8").splitlines()

    # Both kinds of evidence are present, and either alone must be enough.
    assert reference_values_from_header(records)
    assert parse_vcf_contig_lengths(records)
    assert detect_reference_assembly(header_records=records)["assembly"] == (
        "T2T-CHM13v2"
    )
    contigs_only = [r for r in records if not r.startswith("##reference=")]
    assert detect_reference_assembly(header_records=contigs_only)["assembly"] == (
        "T2T-CHM13v2"
    )
    reference_only = [r for r in records if not r.startswith("##contig=")]
    result = detect_reference_assembly(header_records=reference_only)
    assert result["assembly"] == "T2T-CHM13v2"
    assert result["source"] == "reference_line"


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
