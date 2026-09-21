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
    assert "do not identify a platform" in (call.reason or "")


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
