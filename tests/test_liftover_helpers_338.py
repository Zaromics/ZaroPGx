"""BACKLOG 338 - coverage for the liftover pre-flight helpers.

``app/api/utils/liftover.py`` decides, from nothing but VCF header text, which
genome build a file is on.  A GRCh37 file mistaken for GRCh38 produces
confidently wrong pharmacogenomic calls, so the detection helpers are the
highest-value thing in the module to pin down.  Everything exercised here is
pure: no bcftools, no chain-file download, no network.  The one function that
shells out (``validate_liftover_input``) is driven with a stubbed
``subprocess.run`` so the parsing logic downstream of bcftools is still covered.
"""

import gzip
import os
import subprocess

import pytest

from app.api.utils.liftover import (
    _check_gwas_vcf_compliance,
    _count_vcf_samples,
    _extract_genome_info_from_header,
    _validate_chain_file,
    validate_liftover_input,
)

CHROM_LINE_SINGLE = "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tNA12878"
CHROM_LINE_SITES_ONLY = "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO"

# Real-shaped headers.  Note the contig lengths: chr1 is 249250621 on GRCh37 and
# 248956422 on GRCh38, which is the only unambiguous build marker most VCFs
# carry.  The detector ignores them entirely (see the contig-only test below).
GRCH38_HEADER = [
    "##fileformat=VCFv4.2",
    '##FILTER=<ID=PASS,Description="All filters passed">',
    '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
    '##INFO=<ID=DP,Number=1,Type=Integer,Description="Approximate read depth">',
    "##contig=<ID=chr1,length=248956422>",
    "##contig=<ID=chr22,length=50818468>",
    "##source=PharmCAT",
    "##reference=file:///reference/GRCh38/GRCh38_full_analysis_set.fa",
    CHROM_LINE_SINGLE,
]

GRCH37_HEADER = [
    "##fileformat=VCFv4.2",
    '##FILTER=<ID=PASS,Description="All filters passed">',
    '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
    '##INFO=<ID=DP,Number=1,Type=Integer,Description="Approximate read depth">',
    "##contig=<ID=1,length=249250621>",
    "##contig=<ID=22,length=51304566>",
    "##source=GATK",
    "##reference=file:///reference/hg19/ucsc.hg19.fasta",
    CHROM_LINE_SINGLE,
]


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(
        args="bcftools view -h x", returncode=returncode, stdout=stdout, stderr=stderr
    )


def _stub_bcftools(monkeypatch, *, header_lines=None, stderr="", returncode=0):
    """Make ``validate_liftover_input`` see a canned ``bcftools view -h`` result."""
    stdout = "\n".join(header_lines) if header_lines is not None else ""

    def _fake_run(*args, **kwargs):
        return _completed(stdout=stdout, stderr=stderr, returncode=returncode)

    monkeypatch.setattr(subprocess, "run", _fake_run)


# ---------------------------------------------------------------------------
# _validate_chain_file
# ---------------------------------------------------------------------------


def test_validate_chain_file_missing_path_is_false(tmp_path):
    assert _validate_chain_file(str(tmp_path / "nope.over.chain.gz")) is False


def test_validate_chain_file_empty_file_is_false(tmp_path):
    empty = tmp_path / "empty.chain"
    empty.write_bytes(b"")
    assert _validate_chain_file(str(empty)) is False


def test_validate_chain_file_accepts_plain_text_chain(tmp_path):
    chain = tmp_path / "hg19ToHg38.over.chain"
    chain.write_text(
        "chain 20851231461 chr1 249250621 + 10000 249240621 chr1 248956422 + 10000 "
        "248946422 2\n167417\t50000\t50000\n"
    )
    # Well under the 1MB "looks too small" threshold: that path warns but must
    # not fail validation.
    assert _validate_chain_file(str(chain)) is True


def test_validate_chain_file_rejects_non_chain_content(tmp_path):
    bogus = tmp_path / "not-a-chain.txt"
    bogus.write_text("track name=foo\n" + "x" * 500)
    assert _validate_chain_file(str(bogus)) is False


def test_validate_chain_file_only_reads_the_first_100_bytes(tmp_path):
    """Boundary: the magic word past byte 100 is not seen, so validation fails."""
    late = tmp_path / "late.chain"
    late.write_text("#" * 120 + "\nchain 4900 chr1 249250621 + 0 100 chr1 0 100 1\n")
    assert _validate_chain_file(str(late)) is False


def test_validate_chain_file_directory_is_false(tmp_path):
    # os.path.exists() is True for a directory; opening it raises, and the
    # helper must swallow that into a False rather than propagating.
    assert _validate_chain_file(str(tmp_path)) is False


def test_validate_chain_file_accepts_gzipped_chain(tmp_path):
    """Every UCSC chain file ships gzipped - see CHAIN_FILE_URLS.

    Reading raw bytes out of a .gz never finds the literal b"chain", so before
    the fix this returned False for every genuine chain file and
    download_chain_file() could never succeed.
    """
    chain_gz = tmp_path / "hg19ToHg38.over.chain.gz"
    # gzip.compress() emits no FNAME field, matching bytes streamed straight
    # from hgdownload.soe.ucsc.edu.  (gzip.open(path) would embed the file name
    # -- which contains "chain" -- and let the raw-byte check pass by accident.)
    chain_gz.write_bytes(
        gzip.compress(
            b"chain 20851231461 chr1 249250621 + 10000 249240621 chr1 248956422 "
            b"+ 10000 248946422 2\n167417\t50000\t50000\n"
        )
    )
    assert b"chain" not in chain_gz.read_bytes()[:100].lower()
    assert _validate_chain_file(str(chain_gz)) is True


def test_validate_chain_file_rejects_gzip_without_chain_payload(tmp_path):
    other_gz = tmp_path / "decoy.gz"
    other_gz.write_bytes(gzip.compress(b"##fileformat=VCFv4.2\n" * 20))
    assert _validate_chain_file(str(other_gz)) is False


def test_validate_chain_file_rejects_truncated_gzip(tmp_path):
    truncated = tmp_path / "truncated.chain.gz"
    truncated.write_bytes(b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03" + b"\x00" * 40)
    assert _validate_chain_file(str(truncated)) is False


# ---------------------------------------------------------------------------
# _extract_genome_info_from_header - build detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reference_line",
    [
        "##reference=file:///reference/GRCh38/GRCh38_full_analysis_set.fa",
        "##reference=GRCh38",
        "##reference=/data/hg38.fa",
        "##reference=hg38",
    ],
)
def test_extract_genome_detects_grch38_from_reference(reference_line):
    assert (
        _extract_genome_info_from_header(["##fileformat=VCFv4.2", reference_line])
        == "GRCh38/hg38"
    )


@pytest.mark.parametrize(
    "reference_line",
    [
        "##reference=file:///reference/hg19/ucsc.hg19.fasta",
        "##reference=GRCh37",
        "##reference=/data/human_g1k_GRCh37.fasta",
        "##reference=hg19",
    ],
)
def test_extract_genome_detects_grch37_from_reference(reference_line):
    assert (
        _extract_genome_info_from_header(["##fileformat=VCFv4.2", reference_line])
        == "GRCh37/hg19"
    )


def test_extract_genome_detects_build_from_assembly_line():
    assert _extract_genome_info_from_header(["##assembly=GRCh38"]) == "GRCh38/hg38"
    assert _extract_genome_info_from_header(["##assembly=hg19"]) == "GRCh37/hg19"


def test_extract_genome_full_headers_round_trip():
    assert _extract_genome_info_from_header(GRCH38_HEADER) == "GRCh38/hg38"
    assert _extract_genome_info_from_header(GRCH37_HEADER) == "GRCh37/hg19"


@pytest.mark.parametrize(
    "header",
    [
        [],
        ["##fileformat=VCFv4.2", CHROM_LINE_SINGLE],
        ["##source=freebayes v1.3.6", "##reference=/refs/genome.fa"],
    ],
)
def test_extract_genome_returns_unknown_when_undeterminable(header):
    assert _extract_genome_info_from_header(header) == "unknown"


def test_extract_genome_ignores_contig_lengths():
    """Documented detection gap, not a misdetection.

    chr1/length=248956422 is a definitive GRCh38 signature and 249250621 is a
    definitive GRCh37 one, but the helper only ever looks at the contig *ID*.
    A GATK/bcftools VCF that carries contigs and no ##reference therefore comes
    back "unknown".  "unknown" is the safe answer - if this ever starts
    returning a build, tighten the assertion rather than deleting the test.
    """
    grch38_contigs_only = [
        "##fileformat=VCFv4.2",
        "##contig=<ID=chr1,length=248956422>",
        "##contig=<ID=chr2,length=242193529>",
        CHROM_LINE_SINGLE,
    ]
    grch37_contigs_only = [
        "##fileformat=VCFv4.2",
        "##contig=<ID=1,length=249250621>",
        "##contig=<ID=2,length=243199373>",
        CHROM_LINE_SINGLE,
    ]
    assert _extract_genome_info_from_header(grch38_contigs_only) == "unknown"
    assert _extract_genome_info_from_header(grch37_contigs_only) == "unknown"


def test_extract_genome_misses_gatk_bundle_reference_naming():
    """Another documented gap: the GATK resource bundle never says "GRCh38"."""
    header = [
        "##fileformat=VCFv4.2",
        "##reference=file:///gatk/bundle/Homo_sapiens_assembly38.fasta",
        CHROM_LINE_SINGLE,
    ]
    assert _extract_genome_info_from_header(header) == "unknown"


def test_extract_genome_first_conclusive_line_wins_on_conflict():
    """A lifted-over VCF keeps its stale ##reference - the hazard case.

    Detection is first-match-by-line-order, so a file whose coordinates are now
    GRCh38 but whose original ##reference line still says hg19 is reported as
    GRCh37/hg19.  Callers must not treat this result as authoritative for
    already-lifted files.
    """
    lifted = [
        "##fileformat=VCFv4.2",
        "##reference=file:///reference/hg19/ucsc.hg19.fasta",
        "##assembly=GRCh38",
        "##source=bcftools/liftover",
        CHROM_LINE_SINGLE,
    ]
    assert _extract_genome_info_from_header(lifted) == "GRCh37/hg19"

    reordered = [
        "##fileformat=VCFv4.2",
        "##assembly=GRCh38",
        "##reference=file:///reference/hg19/ucsc.hg19.fasta",
        CHROM_LINE_SINGLE,
    ]
    assert _extract_genome_info_from_header(reordered) == "GRCh38/hg38"


@pytest.mark.parametrize(
    "header,forbidden",
    [
        (GRCH37_HEADER, "GRCh38/hg38"),
        (GRCH38_HEADER, "GRCh37/hg19"),
    ],
)
def test_extract_genome_never_reports_the_opposite_build(header, forbidden):
    """The safety property: silence is acceptable, inversion is not."""
    assert _extract_genome_info_from_header(header) != forbidden


# ---------------------------------------------------------------------------
# _check_gwas_vcf_compliance
# ---------------------------------------------------------------------------


def test_gwas_compliance_accepts_a_complete_header():
    result = _check_gwas_vcf_compliance(GRCH38_HEADER)
    assert result["compliant"] is True
    assert result["warnings"] == []


def test_gwas_compliance_names_only_the_missing_headers():
    header = [
        line
        for line in GRCH38_HEADER
        if not line.startswith(("##source", "##reference"))
    ]
    result = _check_gwas_vcf_compliance(header)

    assert result["compliant"] is False
    assert len(result["warnings"]) == 1
    warning = result["warnings"][0]
    assert "##source" in warning
    assert "##reference" in warning
    # Headers that ARE present must not be reported missing.
    assert "##FILTER" not in warning
    assert "##contig" not in warning


def test_gwas_compliance_fileformat_match_is_a_prefix_match():
    """##fileformat=VCFv4.2 has to satisfy the "##fileformat=VCF" requirement."""
    result = _check_gwas_vcf_compliance(["##fileformat=VCFv4.2"])
    assert "##fileformat=VCF" not in result["warnings"][0]


def test_gwas_compliance_empty_header_reports_everything_missing():
    result = _check_gwas_vcf_compliance([])
    assert result["compliant"] is False
    for expected in ("##fileformat=VCF", "##FILTER", "##FORMAT", "##INFO", "##contig"):
        assert expected in result["warnings"][0]


# ---------------------------------------------------------------------------
# _count_vcf_samples
# ---------------------------------------------------------------------------


def test_count_vcf_samples_single_sample():
    assert _count_vcf_samples(CHROM_LINE_SINGLE) == 1


def test_count_vcf_samples_multi_sample():
    line = CHROM_LINE_SINGLE + "\tNA12891\tNA12892"
    assert _count_vcf_samples(line) == 3


def test_count_vcf_samples_sites_only_vcf_is_zero():
    """A sites-only VCF has the 8 fixed columns and no FORMAT - zero samples.

    Before the fix the guard was ``len(parts) < 8`` and the arithmetic
    ``len(parts) - 9``, so this 8-column line returned -1.
    """
    assert _count_vcf_samples(CHROM_LINE_SITES_ONLY) == 0


def test_count_vcf_samples_format_column_without_samples_is_zero():
    assert _count_vcf_samples(CHROM_LINE_SITES_ONLY + "\tFORMAT") == 0


@pytest.mark.parametrize(
    "line",
    [
        "",
        "##fileformat=VCFv4.2",
        "#CHROM POS ID REF ALT QUAL FILTER INFO FORMAT NA12878",  # spaces, not tabs
        "#CHROM\tPOS\tID",
        "chr1\t100\t.\tA\tG\t.\tPASS\t.",
    ],
)
def test_count_vcf_samples_never_negative(line):
    assert _count_vcf_samples(line) == 0


def test_count_vcf_samples_tolerates_trailing_newline():
    assert _count_vcf_samples(CHROM_LINE_SINGLE + "\n") == 1


# ---------------------------------------------------------------------------
# validate_liftover_input
# ---------------------------------------------------------------------------


def test_validate_input_missing_file(tmp_path):
    result = validate_liftover_input(str(tmp_path / "absent.vcf"))
    assert result["valid"] is False
    assert "does not exist" in result["error"]
    assert result["metadata"] == {}


def test_validate_input_unreadable_file(tmp_path, monkeypatch):
    vcf = tmp_path / "locked.vcf"
    vcf.write_text("##fileformat=VCFv4.2\n")
    # os.access(..., R_OK) is effectively always True on Windows, so drive the
    # branch directly rather than relying on filesystem permissions.
    monkeypatch.setattr(os, "access", lambda path, mode: False)

    result = validate_liftover_input(str(vcf))
    assert result["valid"] is False
    assert "not readable" in result["error"]


def test_validate_input_happy_path_populates_metadata(tmp_path, monkeypatch):
    vcf = tmp_path / "sample.vcf.gz"
    vcf.write_bytes(b"placeholder")
    _stub_bcftools(monkeypatch, header_lines=GRCH38_HEADER)

    result = validate_liftover_input(str(vcf))

    assert result["valid"] is True
    assert result["error"] is None
    assert result["metadata"]["is_compressed"] is True
    assert result["metadata"]["file_size"] == len(b"placeholder")
    assert result["metadata"]["detected_genome"] == "GRCh38/hg38"
    assert result["metadata"]["sample_count"] == 1
    assert result["metadata"]["gwas_compliant"] is True
    # A compliant, compressed, single-sample VCF has nothing to warn about.
    assert result["warnings"] == []


def test_validate_input_grch37_is_flagged_as_grch37(tmp_path, monkeypatch):
    vcf = tmp_path / "sample.vcf.gz"
    vcf.write_bytes(b"placeholder")
    _stub_bcftools(monkeypatch, header_lines=GRCH37_HEADER)

    result = validate_liftover_input(str(vcf))
    assert result["metadata"]["detected_genome"] == "GRCh37/hg19"


@pytest.mark.parametrize(
    "name,compressed",
    [("a.vcf", False), ("a.vcf.gz", True), ("a.vcf.bgz", True)],
)
def test_validate_input_compression_detection(tmp_path, monkeypatch, name, compressed):
    vcf = tmp_path / name
    vcf.write_bytes(b"placeholder")
    _stub_bcftools(monkeypatch, header_lines=GRCH38_HEADER)

    result = validate_liftover_input(str(vcf))
    assert result["metadata"]["is_compressed"] is compressed
    warned = any("Uncompressed VCF" in w for w in result["warnings"])
    assert warned is (not compressed)


@pytest.mark.parametrize(
    "size,expected",
    [
        (2 * 1024**3, "Large input file"),
        (11 * 1024**3, "Very large input file"),
    ],
)
def test_validate_input_size_warnings(tmp_path, monkeypatch, size, expected):
    vcf = tmp_path / "big.vcf.gz"
    vcf.write_bytes(b"placeholder")
    monkeypatch.setattr(os.path, "getsize", lambda path: size)
    _stub_bcftools(monkeypatch, header_lines=GRCH38_HEADER)

    result = validate_liftover_input(str(vcf))
    assert result["valid"] is True
    assert any(expected in w for w in result["warnings"])


def test_validate_input_rejects_bcftools_failure(tmp_path, monkeypatch):
    vcf = tmp_path / "broken.vcf.gz"
    vcf.write_bytes(b"placeholder")
    _stub_bcftools(monkeypatch, returncode=255, stderr="Failed to open: unknown format")

    result = validate_liftover_input(str(vcf))
    assert result["valid"] is False
    assert "Invalid VCF format" in result["error"]
    assert "unknown format" in result["error"]


def test_validate_input_requires_chrom_line(tmp_path, monkeypatch):
    vcf = tmp_path / "headerless.vcf.gz"
    vcf.write_bytes(b"placeholder")
    _stub_bcftools(monkeypatch, header_lines=["##fileformat=VCFv4.2", "##source=x"])

    result = validate_liftover_input(str(vcf))
    assert result["valid"] is False
    assert "#CHROM" in result["error"]


def test_validate_input_warns_on_missing_fileformat(tmp_path, monkeypatch):
    vcf = tmp_path / "nofmt.vcf.gz"
    vcf.write_bytes(b"placeholder")
    header = [line for line in GRCH38_HEADER if not line.startswith("##fileformat")]
    _stub_bcftools(monkeypatch, header_lines=header)

    result = validate_liftover_input(str(vcf))
    assert result["valid"] is True
    assert any("Missing ##fileformat" in w for w in result["warnings"])


def test_validate_input_warns_on_multi_sample(tmp_path, monkeypatch):
    vcf = tmp_path / "trio.vcf.gz"
    vcf.write_bytes(b"placeholder")
    header = GRCH38_HEADER[:-1] + [CHROM_LINE_SINGLE + "\tNA12891\tNA12892"]
    _stub_bcftools(monkeypatch, header_lines=header)

    result = validate_liftover_input(str(vcf))
    assert result["metadata"]["sample_count"] == 3
    assert any("Multi-sample VCF detected (3 samples)" in w for w in result["warnings"])


def test_validate_input_sites_only_vcf_reports_zero_samples(tmp_path, monkeypatch):
    vcf = tmp_path / "sites.vcf.gz"
    vcf.write_bytes(b"placeholder")
    header = GRCH38_HEADER[:-1] + [CHROM_LINE_SITES_ONLY]
    _stub_bcftools(monkeypatch, header_lines=header)

    result = validate_liftover_input(str(vcf))
    assert result["metadata"]["sample_count"] == 0
    assert not any("Multi-sample" in w for w in result["warnings"])


def test_validate_input_handles_bcftools_timeout(tmp_path, monkeypatch):
    vcf = tmp_path / "huge.vcf.gz"
    vcf.write_bytes(b"placeholder")

    def _timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="bcftools view -h", timeout=60)

    monkeypatch.setattr(subprocess, "run", _timeout)

    result = validate_liftover_input(str(vcf))
    assert result["valid"] is False
    assert "timed out" in result["error"]


def test_validate_input_handles_unexpected_exception(tmp_path, monkeypatch):
    vcf = tmp_path / "weird.vcf.gz"
    vcf.write_bytes(b"placeholder")

    def _boom(*args, **kwargs):
        raise OSError("bcftools not on PATH")

    monkeypatch.setattr(subprocess, "run", _boom)

    result = validate_liftover_input(str(vcf))
    assert result["valid"] is False
    assert "VCF validation failed" in result["error"]
    assert "bcftools not on PATH" in result["error"]
