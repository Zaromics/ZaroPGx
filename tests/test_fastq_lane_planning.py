"""FASTQ is accepted, capped, and refused for reasons that are about the file.

The blanket refusal ("ZaroPGx ships no aligner") was true and is no longer: the
zaroalign sidecar carries bwa and a PyPGx-compliant reference. What replaced it is
a NARROWER refusal, and the narrowing is the part worth pinning -- it would be easy
to accept everything now that something can align, and both remaining refusals are
real:

* above the byte cap, alignment exhausts memory regardless of time given, because
  peak RSS is a function of the index rather than the read count;
* with no detectable platform there is no honest ``@RG PL:``, which GATK and PyPGx
  both read.

The accepted path must set exactly the BAM lane's flags, because from the aligned
BAM onward it *is* the BAM lane.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api.models import FileType  # noqa: E402
from app.api.utils.fastq_platform import ILLUMINA, PlatformCall  # noqa: E402
from app.api.utils.file_processor import (  # noqa: E402
    FASTQ_MAX_UPLOAD_BYTES,
    FileAnalysis,
    FileProcessor,
)


def _plan(size_bytes: int, call):
    analysis = FileAnalysis(
        file_type=FileType.FASTQ,
        is_compressed=False,
        has_index=False,
        file_size=size_bytes,
        fastq_platform=call,
    )
    return FileProcessor().determine_workflow(analysis)


_GOOD = PlatformCall(ILLUMINA, "ILLUMINA read-name structure; mean read length 151 bp")
_UNKNOWN = PlatformCall(None, "read names carry no structure", "SRA-normalised names")


# --------------------------------------------------------------------------
# Accepted
# --------------------------------------------------------------------------


def test_a_panel_fastq_with_a_known_platform_is_accepted():
    workflow = _plan(2 * 1024**3, _GOOD)

    assert workflow["unsupported"] is False
    assert workflow["unsupported_reason"] is None


def test_the_accepted_lane_sets_the_bam_lanes_flags():
    """From the aligned BAM onward this is the BAM lane, so it must plan like one."""
    workflow = _plan(2 * 1024**3, _GOOD)

    assert workflow["needs_alignment"] is True
    assert workflow["needs_hla"] is True
    assert workflow["needs_mtdna"] is True
    assert workflow["needs_pypgx"] is True
    assert workflow["needs_pypgx_bam2vcf"] is True


def test_the_detected_platform_is_shown_to_the_uploader():
    workflow = _plan(2 * 1024**3, _GOOD)
    assert any("ILLUMINA" in r for r in workflow["recommendations"])


# --------------------------------------------------------------------------
# Still refused, for reasons about the file
# --------------------------------------------------------------------------


def test_above_the_cap_is_refused():
    workflow = _plan(FASTQ_MAX_UPLOAD_BYTES + 1, _GOOD)

    assert workflow["unsupported"] is True
    assert "limit" in workflow["unsupported_reason"]


def test_the_cap_refusal_explains_memory_not_just_size():
    """A cap that reads as arbitrary invites 'just raise it'. It cannot be raised
    into WGS on this hardware, and the copy has to say why."""
    workflow = _plan(FASTQ_MAX_UPLOAD_BYTES + 1, _GOOD)
    assert "memory" in workflow["unsupported_reason"].lower()


def test_an_oversized_fastq_plans_no_work():
    workflow = _plan(FASTQ_MAX_UPLOAD_BYTES + 1, _GOOD)
    assert workflow["needs_alignment"] is False
    assert workflow["needs_pypgx"] is False


def test_an_undetectable_platform_is_refused_not_defaulted():
    """The whole reason the detector exists. ILLUMINA is the likeliest answer and
    must still not be the automatic one."""
    workflow = _plan(2 * 1024**3, _UNKNOWN)

    assert workflow["unsupported"] is True
    assert "PL:" in workflow["unsupported_reason"]
    assert workflow["needs_alignment"] is False


def test_the_platform_refusal_quotes_what_was_observed():
    workflow = _plan(2 * 1024**3, _UNKNOWN)
    assert "read names carry no structure" in workflow["unsupported_reason"]


def test_a_missing_platform_call_does_not_crash_or_default():
    """Defensive: analysis may not have run the detector at all."""
    workflow = _plan(2 * 1024**3, None)

    assert workflow["unsupported"] is True
    assert workflow["needs_alignment"] is False


# --------------------------------------------------------------------------
# The cap has to agree with the thing that enforces it
# --------------------------------------------------------------------------


def test_the_planner_cap_matches_the_sidecars_default():
    """Two copies of the number, guarding different points. If they drift, the plan
    promises an alignment that zaroalign then refuses while streaming."""
    sidecar = (
        Path(__file__).resolve().parent.parent / "docker" / "zaroalign" / "app.py"
    ).read_text(encoding="utf-8")

    assert "str(20 * 1024**3)" in sidecar
    assert FASTQ_MAX_UPLOAD_BYTES == 20 * 1024**3
