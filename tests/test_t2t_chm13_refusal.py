"""A T2T-CHM13 file is detected and refused, not analysed as if it were GRCh38.

The bug this closes is a silent one. ``detect_reference_assembly`` could only
ever answer GRCh38, GRCh37 or None, and ``determine_workflow`` collapses None to
``"unknown"`` -- so a genuine CHM13 VCF matched no branch at all: no warning, no
flag, not provisional, analysed as GRCh38. A CHM13 BAM was equally exposed,
because the GRCh37-alignment refusal keys on ``grch37|hg19|b37`` and deliberately
leaves an undetected build alone.

Nothing downstream would have caught it. PharmCAT's VCF preprocessor has no
assembly check whatsoever: it runs ``bcftools norm -c ws`` against GRCh38.p13,
and ``s`` *swaps* a mismatched REF/ALT and rewrites GT rather than erroring, and
its only header inspection looks at chromosome *names* (``chr1`` vs ``1``). So a
CHM13 file would emerge with rewritten reference alleles and produce wrong
diplotypes rather than a failure -- the confident-wrong-answer class the GRCh37
refusals exist to stop.

Refusal rather than a liftover lane is the decision, and it is not a placeholder
for one: the published T2T chains exclude GRCh38's ALT contigs by construction
(PyPGx documents GSTT1 on ``chr22_KI270879v1_alt``), only ~60% of T2T's segmental
duplications have a clear GRCh38 orthologue and the CYP2D6/2D7/2D8 cluster is one
such region, a published T2T->GRCh38 lift of HG002 dropped 30.4% of GRCh38-derived
SNVs, and no published work characterises CYP2D6 or CYP2C19 in CHM13 at all.

These tests assert on the workflow dict the real ``FileProcessor`` emits and on
the real upload gate's verdict, not on source text.

Most of them hand the build in as a string, because what they are about is what the
workflow does with a build once it is named. That leaves the detector unexercised, and
for a while nothing else joined the two: every T2T row could be deleted from
``CONTIG_LENGTH_ASSEMBLIES`` and all twelve refusal tests stayed green while a real
CHM13 upload went back to being analysed as GRCh38. The two tests at the bottom of this
module close that gap by starting from the shipped fixture's header instead.
"""

from pathlib import Path

import pytest

from app.api.models import FileType, SequencingProfile, VCFHeaderInfo
from app.api.routes.upload_router import _unanalysable_upload_reason
from app.api.utils.file_processor import FileAnalysis, FileProcessor
from app.api.utils.header_inspector import (
    detect_reference_assembly,
    parse_vcf_contig_lengths,
)

ALIGNMENT_TYPES = [FileType.BAM, FileType.CRAM, FileType.SAM]
T2T = "T2T-CHM13v2"
T2T_FIXTURE = (
    Path(__file__).resolve().parents[1] / "test_data" / "t2t_chm13_pgx_snps.vcf"
)


def _vcf_workflow(build: str) -> dict:
    workflow = FileProcessor(temp_dir="/tmp").determine_workflow(
        FileAnalysis(
            file_type=FileType.VCF,
            is_compressed=False,
            has_index=True,
            file_size=1,
            vcf_info=VCFHeaderInfo(
                reference_genome=build,
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
    return workflow


def _alignment_workflow(file_type: FileType, build: str) -> dict:
    workflow = FileProcessor(temp_dir="/tmp").determine_workflow(
        FileAnalysis(
            file_type=file_type,
            is_compressed=True,
            has_index=True,
            file_size=1,
            vcf_info=None,
            is_valid=True,
            validation_errors=[],
            reference_genome=build,
        )
    )
    workflow["file_type"] = file_type.value
    return workflow


def _all_user_visible_strings(workflow: dict) -> list:
    strings = list(workflow.get("recommendations") or [])
    strings += list(workflow.get("warnings") or [])
    if workflow.get("unsupported_reason"):
        strings.append(workflow["unsupported_reason"])
    return strings


# --- VCF ---------------------------------------------------------------------


def test_a_t2t_vcf_is_refused_at_the_gate():
    """The flag only matters if the gate turns it into a refusal."""
    workflow = _vcf_workflow(T2T)

    assert workflow["unsupported"] is True
    # NOT provisional. is_provisional means "analysed anyway, provisionally" and
    # exempts a vcf from the gate -- which here would mean analysing CHM13
    # coordinates as GRCh38's.
    assert workflow["is_provisional"] is False
    assert _unanalysable_upload_reason(workflow), "a T2T VCF must be refused"


def test_a_t2t_vcf_is_not_routed_through_the_liftover_lane():
    """No CHM13 chain is staged, and none is being staged."""
    workflow = _vcf_workflow(T2T)

    assert workflow["needs_liftover"] is False
    assert "source_build" not in workflow


def test_the_t2t_vcf_refusal_names_the_build_and_a_way_out():
    workflow = _vcf_workflow(T2T)
    reason = workflow["unsupported_reason"]

    assert T2T in reason
    assert "GRCh38" in reason
    # The way out is a real one: call or realign against GRCh38.
    lowered = reason.lower()
    assert "realign" in lowered or "call your variants" in lowered


def test_the_t2t_refusal_says_why_nothing_downstream_would_catch_it():
    """The reason it is refused rather than warned about: PharmCAT normalises
    against GRCh38.p13 without checking the assembly, so the failure mode is a
    wrong answer, not an error."""
    reason = _vcf_workflow(T2T)["unsupported_reason"].lower()

    assert "pharmcat" in reason
    assert "grch38.p13" in reason


def test_the_t2t_copy_names_the_two_caveats_of_lifting_it_yourself():
    """A user told "we do not lift this" will reach for liftOver next. The copy
    has to say what that costs, or the refusal just relocates the wrong answer."""
    strings = " ".join(_all_user_visible_strings(_vcf_workflow(T2T))).lower()

    assert "gstt1" in strings
    assert "alternate haplotype" in strings or "alt contig" in strings
    assert "cyp2d6" in strings


def test_the_t2t_copy_does_not_call_the_results_provisional():
    """The retired verdict. "Provisional" means analysed anyway, which this is
    not, and it is also the flag that used to wave such a file past the gate."""
    for text in _all_user_visible_strings(_vcf_workflow(T2T)):
        assert "provisional" not in text.lower(), text


def test_the_t2t_copy_promises_no_automatic_conversion():
    for text in _all_user_visible_strings(_vcf_workflow(T2T)):
        lowered = text.lower()
        for promise in ("will be lifted", "will lift", "we will convert", "(to do)"):
            assert promise not in lowered, text


@pytest.mark.parametrize("build", ["GRCh38", "hg38", "GRCh37", "hg19"])
def test_the_supported_builds_are_untouched(build):
    """The negative control: the refusal must not catch either supported lane."""
    workflow = _vcf_workflow(build)

    assert workflow["unsupported"] is False
    assert _unanalysable_upload_reason(workflow) is None


# --- BAM / CRAM / SAM --------------------------------------------------------


@pytest.mark.parametrize("file_type", ALIGNMENT_TYPES)
def test_a_t2t_alignment_is_refused(file_type):
    workflow = _alignment_workflow(file_type, T2T)

    assert workflow["unsupported"] is True
    assert workflow["is_provisional"] is False
    reason = _unanalysable_upload_reason(workflow)
    assert reason, "a T2T-aligned file must be refused, not queued"
    assert T2T in reason
    assert file_type.value.upper() in reason


@pytest.mark.parametrize("file_type", ALIGNMENT_TYPES)
def test_the_t2t_alignment_refusal_does_not_offer_the_grch37_way_out(file_type):
    """GRCh37's way out -- call variants yourself, we lift the VCF -- does not
    exist here: a T2T VCF is refused by the branch above for the same reason."""
    recs = " ".join(_alignment_workflow(file_type, T2T)["recommendations"]).lower()

    assert "realign" in recs
    assert "lifts that over" not in recs
    assert "will not help" in recs


@pytest.mark.parametrize("file_type", ALIGNMENT_TYPES)
def test_the_grch37_alignment_refusal_still_offers_its_own_way_out(file_type):
    """The negative control for splitting that copy in two."""
    recs = " ".join(_alignment_workflow(file_type, "GRCh37")["recommendations"]).lower()

    assert "lifts that over to grch38 automatically" in recs
    assert "will not help" not in recs


# --- detection joined to refusal ---------------------------------------------
#
# Every test above is handed the string "T2T-CHM13v2". Nothing above notices if the
# detector stops producing it, which is the half that actually protects a user: the
# workflow only ever sees a build the header inspector named.


def _fixture_contig_records() -> list:
    """The shipped CHM13 fixture's ##contig lines, and nothing else.

    The ##reference= line is dropped on purpose. It is a second, independent route to
    the same verdict (a filename containing "chm13"), so leaving it in would let the
    contig-length table rot undetected -- which is the exact gap these two tests exist
    to close. Most real VCFs carry contigs; many carry no ##reference at all.
    """
    records = T2T_FIXTURE.read_text(encoding="utf-8").splitlines()
    return [r for r in records if r.startswith("##contig=")]


def test_a_real_chm13_vcf_header_is_detected_and_then_refused():
    """Fixture header -> detect_reference_assembly -> determine_workflow -> the gate."""
    detected = detect_reference_assembly(header_records=_fixture_contig_records())

    assert detected["assembly"] == T2T, (
        "the shipped CHM13 fixture's contig lengths no longer name T2T-CHM13v2, so a "
        "real CHM13 VCF is back to being analysed as GRCh38"
    )
    assert detected["source"] == "contig_lengths"

    workflow = _vcf_workflow(detected["assembly"])

    assert workflow["unsupported"] is True
    assert _unanalysable_upload_reason(workflow)


@pytest.mark.parametrize("file_type", ALIGNMENT_TYPES)
def test_a_real_chm13_alignment_header_is_detected_and_then_refused(file_type):
    """The same chain for BAM/CRAM/SAM, which had no fixture of their own at all.

    ``inspect_header``'s alignment branch calls
    ``detect_reference_assembly(contig_lengths={@SQ name: length})``, so the evidence is
    the same numbers a VCF's ##contig records carry -- which is why the VCF fixture's
    lengths stand in for a binary CHM13 BAM this repo does not ship.
    """
    detected = detect_reference_assembly(
        contig_lengths=parse_vcf_contig_lengths(_fixture_contig_records())
    )

    assert detected["assembly"] == T2T

    workflow = _alignment_workflow(file_type, detected["assembly"])

    assert workflow["unsupported"] is True
    reason = _unanalysable_upload_reason(workflow)
    assert reason and T2T in reason
