"""A GRCh37/hg19-aligned BAM/CRAM/SAM is refused, not silently mis-analysed.

The liftover lane converts a *called* file's coordinates. Aligned reads reach
PharmCAT only by having variants called out of them first, and that call is made
against gene regions PyPGx looks up by assembly -- so a GRCh37-aligned file
analysed as GRCh38 reads each gene from the wrong locus (GRCh38's CYP2D6 window
sits ~400 kb from GRCh37's) and reports star alleles that are not the patient's.
Nothing downstream errors on this, which is what makes it worse than the gVCF
case: it is the "confident wrong answer, not a failure" that
``_unanalysable_upload_reason`` exists to stop.

Before this guard, ``FileAnalysis`` carried the alignment header's *ambiguity*
evidence but not the build it declared, so the BAM/CRAM/SAM branches could not
tell GRCh37 from GRCh38 and treated both as GRCh38.

These tests assert on the workflow dict the real ``FileProcessor`` emits and on
the real upload gate's verdict, not on source text.
"""

import pytest

from app.api.models import FileType
from app.api.routes.upload_router import _unanalysable_upload_reason
from app.api.utils.file_processor import FileAnalysis, FileProcessor

ALIGNMENT_TYPES = [FileType.BAM, FileType.CRAM, FileType.SAM]


def _alignment_analysis(file_type: FileType, build: str | None) -> FileAnalysis:
    return FileAnalysis(
        file_type=file_type,
        is_compressed=True,
        has_index=True,
        file_size=1,
        vcf_info=None,
        is_valid=True,
        validation_errors=[],
        reference_genome=build,
    )


def _workflow_for(file_type: FileType, build: str | None) -> dict:
    workflow = FileProcessor(temp_dir="/tmp").determine_workflow(
        _alignment_analysis(file_type, build)
    )
    # The gate keys off file_type, which the router stamps on before consulting it.
    workflow["file_type"] = file_type.value
    return workflow


@pytest.mark.parametrize("file_type", ALIGNMENT_TYPES)
@pytest.mark.parametrize("build", ["GRCh37", "hg19", "grch37", "HG19", "b37"])
def test_grch37_aligned_file_is_unsupported(file_type, build):
    workflow = _workflow_for(file_type, build)

    assert workflow["unsupported"] is True
    # Not provisional: nothing is analysed. is_provisional means "analysed
    # anyway, provisionally" and would wave this past the gate (the 23andMe bug).
    assert workflow["is_provisional"] is False


@pytest.mark.parametrize("file_type", ALIGNMENT_TYPES)
def test_grch37_aligned_file_is_actually_refused_by_the_upload_gate(file_type):
    """The flag only matters if the gate turns it into a refusal."""
    workflow = _workflow_for(file_type, "GRCh37")

    reason = _unanalysable_upload_reason(workflow)

    assert reason, "a GRCh37-aligned file must be refused, not queued"
    assert "GRCh38" in reason or "hg38" in reason


@pytest.mark.parametrize("file_type", ALIGNMENT_TYPES)
def test_refusal_names_the_build_and_both_ways_out(file_type):
    workflow = _workflow_for(file_type, "GRCh37")
    reason = workflow["unsupported_reason"]
    recs = " ".join(workflow["recommendations"]).lower()

    assert "GRCh37" in reason  # which build the file actually is
    assert file_type.value.upper() in reason  # and which file
    # Way out 1: call variants yourself, upload the VCF -- that lane is supported.
    assert "vcf" in recs
    # Way out 2: realign to GRCh38.
    assert "realign" in recs or "grch38" in recs


@pytest.mark.parametrize("file_type", ALIGNMENT_TYPES)
def test_refusal_does_not_promise_liftover_of_aligned_reads(file_type):
    """Copy must not imply ZaroPGx will lift the BAM itself -- it will not."""
    workflow = _workflow_for(file_type, "GRCh37")
    reason = workflow["unsupported_reason"].lower()

    for promise in ("will be lifted", "will lift", "we will convert"):
        assert promise not in reason


@pytest.mark.parametrize("file_type", ALIGNMENT_TYPES)
def test_grch38_aligned_file_is_untouched(file_type):
    """The negative control: the guard must not refuse the supported lane."""
    workflow = _workflow_for(file_type, "GRCh38")

    assert workflow["unsupported"] is False
    assert _unanalysable_upload_reason(workflow) is None


@pytest.mark.parametrize("file_type", ALIGNMENT_TYPES)
def test_undetectable_build_is_not_refused(file_type):
    """No usable @SQ evidence is 'unknown', not 'GRCh37'.

    Refusing a file whose build simply could not be read would reject most
    minimal and hand-made BAMs -- a different defect. The ambiguity check beside
    this one stays silent on no-evidence for the same reason.
    """
    workflow = _workflow_for(file_type, None)

    assert workflow["unsupported"] is False
    assert _unanalysable_upload_reason(workflow) is None


def test_grch37_vcf_still_uses_the_liftover_lane_not_this_refusal():
    """The VCF lane is genuinely supported; this guard must not catch it."""
    from app.api.models import SequencingProfile, VCFHeaderInfo

    workflow = FileProcessor(temp_dir="/tmp").determine_workflow(
        FileAnalysis(
            file_type=FileType.VCF,
            is_compressed=False,
            has_index=True,
            file_size=1,
            vcf_info=VCFHeaderInfo(
                reference_genome="GRCh37",
                sequencing_platform="Illumina",
                sequencing_profile=SequencingProfile.WGS,
                has_index=True,
                is_bgzipped=False,
                contigs=["chr1"],
                sample_count=1,
                variant_count=None,
            ),
            is_valid=True,
            validation_errors=[],
        )
    )
    workflow["file_type"] = FileType.VCF.value

    assert workflow["needs_liftover"] is True
    assert _unanalysable_upload_reason(workflow) is None
