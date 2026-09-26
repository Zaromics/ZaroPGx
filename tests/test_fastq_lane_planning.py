"""FASTQ is accepted, capped, and refused for reasons that are about the file.

The blanket refusal said "ZaroPGx ships no aligner". That was never true: GATK has
bundled bwa-mem as a JNI native since GATK 4, so the aligner was in the image all
along and only gatk-api's /align-fastq endpoint was missing. What replaced the
refusal is a NARROWER one, and the narrowing is the part worth pinning -- it would
be easy to accept everything now that something can align, and both remaining
refusals are real:

* above the byte cap, alignment would need several times the reads' size in scratch
  disk (three BAMs are written) and many hours on one machine;
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


def test_the_cap_refusal_explains_why_not_just_size():
    """A cap that reads as arbitrary invites 'just raise it', and the copy has to say
    what it bounds. It once said "memory", which was wrong: BwaSpark's footprint is the
    index image, loaded whole for a panel too. What grows with the reads is disk and
    time."""
    reason = _plan(FASTQ_MAX_UPLOAD_BYTES + 1, _GOOD)["unsupported_reason"].lower()
    assert "disk" in reason
    assert "memory" not in reason


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


def test_the_planner_cap_matches_the_endpoints_default():
    """Two copies of the number, guarding different points. If they drift, the plan
    promises an alignment /align-fastq then refuses while streaming."""
    endpoint = (
        Path(__file__).resolve().parent.parent / "docker" / "gatk-api" / "gatk_api.py"
    ).read_text(encoding="utf-8")

    assert "str(20 * 1024 ** 3)" in endpoint
    assert FASTQ_MAX_UPLOAD_BYTES == 20 * 1024**3


def test_the_endpoint_uses_gatks_own_bwa_rather_than_a_second_aligner():
    """The whole reason zaroalign was dropped.

    GATK already carries bwa-mem as a JNI native, so installing a second bwa was
    duplicating a binary the stack had. If someone reintroduces a standalone aligner
    they should have to delete this test and say why.
    """
    endpoint = (
        Path(__file__).resolve().parent.parent / "docker" / "gatk-api" / "gatk_api.py"
    ).read_text(encoding="utf-8")

    assert "BwaSpark" in endpoint
    assert "FastqToSam" in endpoint


def test_the_alignment_step_marks_duplicates():
    """Not incidental: PyPGx calls CYP2D6 copy number, and duplicate-inflated depth
    is exactly what corrupts a copy-number estimate. A plain BwaSpark would align
    just as well and report the wrong number of gene copies."""
    endpoint = (
        Path(__file__).resolve().parent.parent / "docker" / "gatk-api" / "gatk_api.py"
    ).read_text(encoding="utf-8")

    assert "BwaAndMarkDuplicatesPipelineSpark" in endpoint
    assert '"duplicates_marked": True' in endpoint


# --------------------------------------------------------------------------
# Long reads are detected correctly, and still refused
# --------------------------------------------------------------------------


@pytest.mark.parametrize("platform", ["ONT", "PACBIO"])
def test_a_long_read_fastq_is_refused(platform):
    """Detecting the platform is not the same as being able to use it.

    The lane aligns with bwa-mem through GATK's BwaSpark, which exposes no long-read
    preset, and types HLA with OptiType, which is short-read only. Accepting an ONT or
    PacBio FASTQ would return confident output from tools that were never meant to read
    it -- the class of answer this codebase refuses everywhere else.
    """
    call = PlatformCall(platform, f"{platform} read-name structure; mean 15000 bp")
    workflow = _plan(2 * 1024**3, call)

    assert workflow["unsupported"] is True
    assert workflow["needs_alignment"] is False
    reason = workflow["unsupported_reason"]
    assert "minimap2" in reason, "the refusal must name the tool that does fit"
    assert platform in reason


def test_a_short_read_dnbseq_fastq_is_accepted():
    call = PlatformCall("DNBSEQ", "DNBSEQ read-name structure; mean read length 150 bp")
    workflow = _plan(2 * 1024**3, call)

    assert workflow["unsupported"] is False
    assert workflow["needs_alignment"] is True


# --------------------------------------------------------------------------
# HLA on the FASTQ lane is typed from the aligned BAM, not the raw reads
# --------------------------------------------------------------------------


def _main_nf() -> str:
    return (
        Path(__file__).resolve().parent.parent / "pipelines" / "pgx" / "main.nf"
    ).read_text(encoding="utf-8")


def _fastq_branch(src: str) -> str:
    start = src.index("if (params.input_type == 'fastq') {")
    end = src.index("else if (params.input_type == 'cram')", start)
    return src[start:end]


def test_the_fastq_lane_types_hla_from_the_aligned_bam():
    """A targeted panel with no HLA capture crashed OptiType on the raw-FASTQ path.

    Measured on a real 218,682-read panel FASTQ with no MHC reads: zarohla answered
    500, "OptiType failed: Length mismatch: Expected axis has 0 elements", which fails
    the whole run. A raw FASTQ has no coordinates, so there is no observed fact to
    decide "no HLA reads" from; the aligned BAM does, and zarohla's HLA-locus probe reads it.
    The BAM keeps its unmapped reads and zarohla converts all of it back, so OptiType
    still sees essentially every read -- only the decision moves.
    """
    branch = _fastq_branch(_main_nf())
    assert "OptiTypeHLAFromBAM(bam_ch" in branch
    assert "OptiTypeHLAFromFastq" not in branch


def test_the_raw_fastq_hla_process_is_gone():
    """Nothing calls it any more, and leaving it defined invites someone back onto
    the path that crashes on every panel FASTQ."""
    assert "process OptiTypeHLAFromFastq" not in _main_nf()


# --------------------------------------------------------------------------
# Alignment is gatk-api work, so it must not be skipped as "no GATK needed"
# --------------------------------------------------------------------------
#
# Found by the first end-to-end FASTQ run through the app (2026-09-26): the planner
# accepted the file, the job was created, and it died 15 s later with
# "--skip_gatk is not compatible with fastq". upload_router derived skip_gatk from
# needs_gatk alone, and the FASTQ plan sets needs_alignment instead -- so a lane whose
# every step runs in the gatk-api container was told to skip gatk-api. Every unit test
# was green, because none of them followed a FASTQ from planner to argv.
#
# needs_gatk is deliberately NOT set for FASTQ: workflow_registry mints
# gatk_cram_sam_to_bam on needs_gatk (vetoed only by needs_conversion), and minting a
# step no process posts leaves it [pending] forever. So the fix is on the skip side.


def _router_src() -> str:
    return (
        Path(__file__).resolve().parent.parent
        / "app"
        / "api"
        / "routes"
        / "upload_router.py"
    ).read_text(encoding="utf-8")


def test_skip_gatk_counts_alignment_as_gatk_work():
    src = _router_src()
    start = src.index("skip_gatk = ")
    statement = src[start : src.index("skip_report = ", start)]
    assert (
        "needs_alignment" in statement
    ), "skip_gatk must stay false when alignment is planned; alignment runs in gatk-api"


def test_the_fastq_plan_does_not_set_needs_gatk():
    """Setting it would mint gatk_cram_sam_to_bam onto a FASTQ job, where no process
    ever posts it -- the [pending]-forever failure workflow_registry warns about."""
    workflow = _plan(2 * 1024**3, _GOOD)
    assert workflow["needs_gatk"] is False
    assert workflow["needs_alignment"] is True


def test_unticking_gatk_also_unplans_alignment():
    """The GATK toggle covers alignment -- FastqToSam, BwaSpark and MarkDuplicatesSpark
    are all GATK tools -- so disabling it must not leave an alignment planned that
    skip_gatk then contradicts. main.nf rejects skip_gatk for fastq loudly, exactly as
    it does for cram/sam, rather than the run silently ignoring the toggle."""
    src = (
        Path(__file__).resolve().parent.parent
        / "app"
        / "api"
        / "utils"
        / "file_processor.py"
    ).read_text(encoding="utf-8")
    block = src[
        src.index('if gatk_enabled is not None and not workflow["gatk_enabled"]:') :
    ]
    block = block[: block.index("if pypgx_enabled is not None")]
    assert 'workflow["needs_alignment"] = False' in block
