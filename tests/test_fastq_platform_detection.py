"""``@RG PL:`` is established from the reads, or not established at all.

GATK needs a platform and a bare FASTQ has no field that states one. Both easy
answers are wrong: ``PL:ILLUMINA`` for everything is the guess this codebase
refuses to make elsewhere, and asking the uploader moves the guess rather than
removing it -- the person who exported a FASTQ from a service often does not know
what produced it.

The platform is, however, written into the read names, because each instrument
derives them from its own geometry (Illumina flowcell coordinates, ONT channel and
run ids, PacBio ZMW numbers, MGI/DNBSEQ grid references). This module pins that
detection AND, just as
importantly, pins the cases where it must refuse: an SRA round-trip strips the
naming, and a name that disagrees with the read lengths is evidence of a file that
is not what it claims, not a tie to be broken.
"""

from __future__ import annotations

import gzip
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api.utils.fastq_platform import (  # noqa: E402
    DNBSEQ,
    ILLUMINA,
    ONT,
    PACBIO,
    detect_fastq_platform,
)


def _write_fastq(path: Path, records, gz: bool = False) -> Path:
    body = "".join(f"@{name}\n{seq}\n+\n{'I' * len(seq)}\n" for name, seq in records)
    if gz:
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            fh.write(body)
    else:
        path.write_text(body, encoding="utf-8")
    return path


def _illumina_records(n=50, length=151):
    return [
        (f"A00123:45:HXXXXDSXX:1:1101:{1000 + i}:{2000 + i} 1:N:0:ATCACG", "A" * length)
        for i in range(n)
    ]


def _ont_records(n=20):
    return [
        (
            f"0000{i:04d}-aaaa-bbbb-cccc-dddddddddddd runid=abc123 read={i} "
            f"ch={100 + i} start_time=2026-01-01T00:00:00Z",
            "ACGT" * (2000 + i * 50),
        )
        for i in range(n)
    ]


def _pacbio_records(n=20):
    return [
        (
            f"m54238_180901_011437/{4194374 + i}/0_{5000 + i * 40}",
            "ACGT" * (1500 + i * 30),
        )
        for i in range(n)
    ]


# --------------------------------------------------------------------------
# The four platforms
# --------------------------------------------------------------------------


def test_illumina_casava_names_are_detected(tmp_path):
    call = detect_fastq_platform(
        _write_fastq(tmp_path / "r.fastq", _illumina_records())
    )
    assert call.platform == ILLUMINA
    assert call.determined


def test_illumina_legacy_names_are_detected(tmp_path):
    records = [(f"HWUSI-EAS100R:6:73:941:{1973 + i}#0/1", "A" * 100) for i in range(30)]
    call = detect_fastq_platform(_write_fastq(tmp_path / "r.fastq", records))
    assert call.platform == ILLUMINA


def test_nanopore_names_are_detected(tmp_path):
    call = detect_fastq_platform(_write_fastq(tmp_path / "r.fastq", _ont_records()))
    assert call.platform == ONT


def test_pacbio_names_are_detected(tmp_path):
    call = detect_fastq_platform(_write_fastq(tmp_path / "r.fastq", _pacbio_records()))
    assert call.platform == PACBIO


def test_mgi_dnbseq_names_are_detected(tmp_path):
    """Real read names from a consumer WGS CRAM sequenced on DNBSEQ.

    This arm exists because of that file: without it the detector called a
    perfectly legible platform "no recognisable structure" and refused the upload.
    The vendor prefix before the grid reference is part of the real name and must
    not stop the match.
    """
    records = [
        (
            f"NG1GNACLXB_2022_12_28_206677_ProPhase_BP_FP200007900L1C{i:03d}R038{i:08d}",
            "A" * 100,
        )
        for i in range(1, 31)
    ]
    call = detect_fastq_platform(_write_fastq(tmp_path / "r.fastq", records))

    assert call.platform == DNBSEQ


def test_dnbseq_is_treated_as_a_short_read_platform(tmp_path):
    """It is short-read, so the length corroboration must not veto it."""
    records = [(f"V300026873L1C001R001{i:07d}", "ACGT" * 25) for i in range(1, 31)]
    call = detect_fastq_platform(_write_fastq(tmp_path / "r.fastq", records))

    assert call.platform == DNBSEQ
    assert call.determined


def test_gzipped_fastq_is_read(tmp_path):
    """Sniffed by magic, so a .gz that was renamed still works."""
    path = _write_fastq(tmp_path / "r.fastq.gz", _illumina_records(), gz=True)
    assert detect_fastq_platform(path).platform == ILLUMINA


# --------------------------------------------------------------------------
# The refusals -- the half that keeps this honest
# --------------------------------------------------------------------------


def test_sra_normalised_names_are_not_guessed(tmp_path):
    """An SRA round-trip throws the instrument naming away. That must not become
    an Illumina call just because most PGx data is Illumina."""
    records = [(f"SRR1234567.{i} {i} length=100", "A" * 100) for i in range(30)]
    call = detect_fastq_platform(_write_fastq(tmp_path / "r.fastq", records))

    assert call.platform is None
    assert not call.determined
    assert "do not identify one" in (call.reason or "")


def test_illumina_names_on_long_reads_are_refused_not_resolved(tmp_path):
    """The contradiction case. Names say Illumina, lengths say otherwise -- the
    file is not what it claims and neither answer may be stamped onto the BAM."""
    records = [
        (f"A00123:45:HXXXXDSXX:1:1101:{1000 + i}:{2000 + i}", "ACGT" * 4000)
        for i in range(30)
    ]
    call = detect_fastq_platform(_write_fastq(tmp_path / "r.fastq", records))

    assert call.platform is None
    assert "disagree" in (call.reason or "")


def test_mixed_platform_reads_are_refused(tmp_path):
    records = _illumina_records(n=15) + _ont_records(n=15)
    call = detect_fastq_platform(_write_fastq(tmp_path / "r.fastq", records))

    assert call.platform is None
    assert "more than one platform" in (call.reason or "")


def test_an_empty_file_is_undetermined_not_illumina(tmp_path):
    path = tmp_path / "empty.fastq"
    path.write_text("", encoding="utf-8")
    call = detect_fastq_platform(path)

    assert call.platform is None
    assert "no readable reads" in (call.reason or "")


def test_evidence_is_always_reported(tmp_path):
    """A refusal has to be able to quote what it saw, or the uploader cannot act."""
    for records in (_illumina_records(), _ont_records(), _pacbio_records()):
        call = detect_fastq_platform(_write_fastq(tmp_path / "r.fastq", records))
        assert call.evidence
        assert "read length" in call.evidence


def test_the_scan_is_bounded(tmp_path):
    """A 20 GB FASTQ must not be read to answer a question the first reads settle."""
    path = _write_fastq(tmp_path / "r.fastq", _illumina_records(n=5000))
    call = detect_fastq_platform(path, max_records=10)
    assert call.platform == ILLUMINA
    assert "over 10 reads" in call.evidence


def test_dnbseq_names_with_a_mate_suffix_are_detected(tmp_path):
    """Real MGI instrument FASTQs end every read name in /1 or /2.

    The first cut of this pattern was anchored on the grid reference at the very end
    of the name, so `...R03305448507/1` -- the form the sequencer actually writes --
    matched nothing and a legible DNBSEQ run was refused as "no recognisable
    platform". The fixture that caught it came from samtools, which does not add the
    suffix; the instrument does.
    """
    records = [
        (
            f"NG1GNACLXB_2022_12_28_206677_ProPhase_BP_FP200007900L1C{i:03d}R038{i:08d}/1",
            "A" * 100,
        )
        for i in range(1, 31)
    ]
    call = detect_fastq_platform(_write_fastq(tmp_path / "r.fastq", records))

    assert call.platform == DNBSEQ


def test_short_and_long_read_platforms_are_distinguished():
    """The alignment lane is short-read only, so callers need to ask which is which."""
    from app.api.utils.fastq_platform import SHORT_READ_PLATFORMS

    assert ILLUMINA in SHORT_READ_PLATFORMS
    assert DNBSEQ in SHORT_READ_PLATFORMS
    assert ONT not in SHORT_READ_PLATFORMS
    assert PACBIO not in SHORT_READ_PLATFORMS


# --------------------------------------------------------------------------
# The header panel must agree with the plan
# --------------------------------------------------------------------------
#
# Seen on the live upload page (2026-09-26): for a DNBSEQ FASTQ the header panel said
# "Sequencing Platform: Unknown" directly above a recommendation reading "Detected
# platform: DNBSEQ", and listed the first read's name as the "Sample ID". The panel is
# fed by header_inspector, which knew neither.


def test_the_fastq_header_reports_the_detected_platform(tmp_path):
    from app.api.utils.header_inspector import inspect_header

    records = [
        (
            f"NG1GNACLXB_2022_12_28_206677_ProPhase_BP_FP200007900L1C{i:03d}R038{i:08d}",
            "A" * 150,
        )
        for i in range(1, 31)
    ]
    info = inspect_header(str(_write_fastq(tmp_path / "reads.fastq", records)))

    assert info["metadata"]["sequencing_platform"] == DNBSEQ


def test_the_fastq_header_does_not_present_a_read_name_as_the_sample(tmp_path):
    """A FASTQ carries no sample name. The first read's identifier is not one, and
    showing it as "Sample ID" invites someone to take it for theirs."""
    from app.api.utils.header_inspector import inspect_header

    info = inspect_header(
        str(_write_fastq(tmp_path / "reads.fastq", _illumina_records()))
    )

    assert info.get("sample") is None


# --------------------------------------------------------------------------
# Mate pairs: two FASTQs are one run only if their read names pair up
# --------------------------------------------------------------------------


def test_casava_mates_pair(tmp_path):
    from app.api.utils.fastq_platform import mate_names_agree

    r1 = _write_fastq(tmp_path / "r1.fastq", _illumina_records())
    r2_records = [
        (n.replace(" 1:N:", " 2:N:"), "C" * len(s)) for n, s in _illumina_records()
    ]
    r2 = _write_fastq(tmp_path / "r2.fastq", r2_records)
    ok, evidence = mate_names_agree(r1, r2)
    assert ok, evidence


def test_slash_suffixed_mates_pair(tmp_path):
    """MGI writes /1 and /2 onto every name; the suffix is what differs, not the read."""
    from app.api.utils.fastq_platform import mate_names_agree

    base = [f"FP200007900L1C{i:03d}R038{i:08d}" for i in range(1, 31)]
    r1 = _write_fastq(tmp_path / "r1.fastq", [(n + "/1", "A" * 100) for n in base])
    r2 = _write_fastq(tmp_path / "r2.fastq", [(n + "/2", "C" * 100) for n in base])
    ok, evidence = mate_names_agree(r1, r2)
    assert ok, evidence


def test_two_unrelated_fastqs_do_not_pair(tmp_path):
    """Two single-end runs uploaded together are not a pair, and aligning them as
    one would invent mate relationships between strangers."""
    from app.api.utils.fastq_platform import mate_names_agree

    r1 = _write_fastq(tmp_path / "a.fastq", _illumina_records())
    other = [
        (f"A00999:7:HYYYYDSXX:2:2202:{5000 + i}:{6000 + i} 1:N:0:GGGGGG", "A" * 151)
        for i in range(50)
    ]
    r2 = _write_fastq(tmp_path / "b.fastq", other)
    ok, evidence = mate_names_agree(r1, r2)
    assert not ok
    assert "pair" in evidence


@pytest.mark.parametrize(
    "name",
    [
        "sample_R1.fastq.gz",
        "sample_R2_001.fastq.gz",
        "sample_1.fq",
        "sample.R2.fq.gz",
        "SAMPLE-r1.FASTQ",
    ],
)
def test_a_mate_filename_is_recognised(name):
    from app.api.utils.fastq_platform import looks_like_one_mate

    assert looks_like_one_mate(name)


@pytest.mark.parametrize(
    "name", ["sample.fastq.gz", "chr1.fastq", "sample1.fq", "run_3.fastq", "", None]
)
def test_a_plain_filename_is_not_taken_for_a_mate(name):
    from app.api.utils.fastq_platform import looks_like_one_mate

    assert not looks_like_one_mate(name)


def test_a_file_paired_with_its_own_copy_is_not_a_pair(tmp_path):
    """Stripping the mate marker makes a file agree with itself; the sequences don't
    lie. Accepted, it would be single-end data labelled paired-end."""
    from app.api.utils.fastq_platform import mate_names_agree

    records = [(f"FP200007900L1C{i:03d}R038{i:08d}/1", "ACGT" * 25) for i in range(30)]
    r1 = _write_fastq(tmp_path / "r1.fastq", records)
    copy = _write_fastq(tmp_path / "r1_copy.fastq", records)
    ok, evidence = mate_names_agree(r1, copy)
    assert not ok
    assert "same reads" in evidence


def test_a_truncated_gzip_ends_the_scan_instead_of_raising(tmp_path):
    """A gzip stream cut short raises EOFError from readline(); the reads before the
    cut are still read, as for any other truncated record."""
    whole = gzip.compress(
        "".join(
            f"@A00123:45:HXXXXDSXX:1:1101:{1000 + i}:2000 1:N:0:ATCACG\n{'A' * 151}\n+\n"
            f"{'I' * 151}\n"
            for i in range(400)
        ).encode()
    )
    path = tmp_path / "cut.fastq.gz"
    path.write_bytes(whole[: len(whole) // 2])

    call = detect_fastq_platform(path)
    assert call.platform == ILLUMINA
