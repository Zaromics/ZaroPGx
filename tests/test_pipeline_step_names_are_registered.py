"""Every step_name pipelines/pgx/main.nf posts must exist app-side.

A Nextflow process reports its status by POSTing ``job_id`` + ``step_name`` to its
sidecar, which forwards it to the app. ``JobService.update_job_step`` looks the row
up by EXACT ``step_name`` (``app/services/job_service.py``) and returns None -> 404
when there is none. Steps are only ever minted from the StepTemplates in
``app/services/workflow_registry.py``, so a name main.nf posts that the registry does
not carry never gets a row: every update 404s into a log nobody reads and the UI
leaves that step at [pending] for exactly as long as the step runs. To a user that is
indistinguishable from a stalled job.

That is not hypothetical -- it has now happened three times. ``liftover`` and
``mtdna_analysis`` were each added to main.nf before the registry knew them, and
CramToBAM/SamToBAM posted ``gatk_cram_to_bam``/``gatk_sam_to_bam`` while every
app-side file said ``gatk_cram_sam_to_bam`` and the registry had no GATK conversion
template at all -- so the CRAM and SAM lanes, both supported shipping input types,
hung there on every run.

The per-step tests those incidents left behind each pin one process. This one is
cross-boundary and general: it reads main.nf's own source for what is posted and the
registry/stage map for what exists, so a process added later fails the build instead
of hanging the UI. Neither side is hand-copied; a copy would rot the moment either
moved.

Conditionality is handled by asserting on TEMPLATES, not on the steps any single
``resolve_steps`` call happens to mint: most of these steps are gated on a
``needs_*`` flag and are legitimately absent from most jobs. What must never be
absent is the template.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.api.models import FileType, SequencingProfile, VCFHeaderInfo, WorkflowOptions
from app.api.utils.file_processor import GVCF_GATK_ALLELE, FileAnalysis, FileProcessor
from app.services.workflow_registry import GENOMIC_ANALYSIS, resolve_steps
from app.services.workflow_stages import STEP_TO_STAGE, WorkflowStage

MAIN_NF = Path(__file__).resolve().parents[1] / "pipelines" / "pgx" / "main.nf"

# Every input type a job can exist for. FASTQ is absent on purpose: it is refused at
# ingest, determine_workflow sets no needs_* flag for it, and no Job is ever created --
# there is no lane to check.
ANALYSED_TYPES = [
    FileType.VCF,
    FileType.BCF,
    FileType.GVCF,
    FileType.BAM,
    FileType.CRAM,
    FileType.SAM,
]

# Deliberately anchored on the curl form flag: `-F step_name=`. Prose in this file's
# comments mentions step names too, and matching those would let a comment satisfy the
# test for a process that posts something else.
_POSTED = re.compile(r"-F step_name=([A-Za-z0-9_]+)")


def _posted_step_names() -> set[str]:
    return set(_POSTED.findall(MAIN_NF.read_text(encoding="utf-8")))


def _templates() -> dict[str, object]:
    """The registry's templates by step_name -- and a real dict, not a silent merge.

    ``{t.step_name: t for t in ...}`` keeps the LAST template of a duplicated name and
    drops the rest, so the two tests below that iterate template names could never
    observe a duplicate: the parametrize list would carry one entry and the lookup
    would answer with one object. ``resolve_steps`` has no such forgiveness -- it
    returns a LIST -- so a name registered twice mints two JobStep rows with two
    step_orders, and ``JobService.update_job_step`` looks a step up by name.
    """
    names = [t.step_name for t in GENOMIC_ANALYSIS.step_templates]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    assert not duplicates, (
        f"workflow_registry.py registers {duplicates} more than once: resolve_steps "
        f"returns a list, so each duplicate mints a second JobStep row for the same "
        f"name and every status update for it resolves to whichever row the query "
        f"returns first."
    )
    return {t.step_name: t for t in GENOMIC_ANALYSIS.step_templates}


def _analysis_for(file_type: FileType) -> FileAnalysis:
    """A GRCh38 FileAnalysis of `file_type`, minimal but real.

    GRCh38 throughout because that is the only build every one of these types is
    analysed on: a GRCh37 BAM/CRAM/SAM and a T2T anything are refused, and a refused
    upload has no Job and therefore no steps.
    """
    vcf_types = (FileType.VCF, FileType.BCF, FileType.GVCF)
    return FileAnalysis(
        file_type=file_type,
        is_compressed=True,
        has_index=True,
        file_size=1,
        is_valid=True,
        validation_errors=[],
        vcf_info=(
            VCFHeaderInfo(
                reference_genome="GRCh38",
                sequencing_platform="Illumina",
                sequencing_profile=SequencingProfile.WGS,
                has_index=True,
                is_bgzipped=True,
                contigs=["chr1"],
                sample_count=1,
                variant_count=None,
            )
            if file_type in vcf_types
            else None
        ),
        reference_genome=None if file_type in vcf_types else "GRCh38",
        # The gVCF lane converts with GATK GenotypeGVCFs, which reads <NON_REF> blocks
        # and nothing else; anything else is refused before a workflow is planned.
        gvcf_symbolic_allele=GVCF_GATK_ALLELE if file_type is FileType.GVCF else None,
    )


def _options_for(file_type: FileType) -> WorkflowOptions:
    """The WorkflowOptions a real upload of `file_type` produces.

    determine_workflow's own output, mapped the way upload_router maps it (which is
    where the needs_report default of True comes from -- no branch sets that key). Not
    a transcription of what the branches are believed to set: the CRAM/SAM lanes shipped
    for months posting a step their flags never minted, and every hand-written option
    set in this module agreed with the bug rather than with main.nf.
    """
    workflow = FileProcessor(temp_dir="/tmp").determine_workflow(
        _analysis_for(file_type)
    )
    values = {f: workflow.get(f, False) for f in GENOMIC_ANALYSIS.option_fields}
    # No branch of determine_workflow sets needs_report; upload_router defaults it on.
    values["needs_report"] = workflow.get("needs_report", True)
    return WorkflowOptions(**values)


def test_the_parser_actually_finds_the_posts():
    """Guard the parser: a silent parse failure would pass every test below."""
    posted = _posted_step_names()
    # The floor is the count at the time of writing (10 distinct names across 12 curl
    # call sites); it only ever grows.
    assert len(posted) >= 10, posted
    assert {
        "pharmcat_analysis",
        "liftover",
        "gatk_cram_sam_to_bam",
        "gvcf_to_vcf",
    } <= posted


@pytest.mark.parametrize("step_name", sorted(_posted_step_names()))
def test_every_posted_step_name_has_a_template(step_name):
    templates = _templates()
    assert step_name in templates, (
        f"pipelines/pgx/main.nf posts step_name={step_name!r} but "
        f"app/services/workflow_registry.py mints no such step: its status updates "
        f"will 404 and the UI will hang that step at [pending]. Add a StepTemplate "
        f"gated on the flag the input type actually sets."
    )


@pytest.mark.parametrize("step_name", sorted(_posted_step_names()))
def test_every_posted_step_name_has_a_stage(step_name):
    assert step_name in STEP_TO_STAGE, (
        f"main.nf posts {step_name!r} but STEP_TO_STAGE has no entry, so "
        f"stage_from_step() silently reports it as ANALYSIS -- the progress bar and "
        f"the glyph row will name the wrong stage for the whole step."
    )


@pytest.mark.parametrize(
    "step_name", sorted(t.step_name for t in GENOMIC_ANALYSIS.step_templates)
)
def test_every_gate_names_a_real_option_field(step_name):
    """A typo'd gate is a step that silently never mints.

    ``resolve_steps`` reads gates with ``getattr(options, name, False)``, so a
    misspelled field is not an error -- it is a permanently false condition, and the
    symptom is the same 404 as a missing template.
    """
    tmpl = _templates()[step_name]
    options = WorkflowOptions()
    for gate in (tmpl.when, tmpl.unless):
        if gate is not None:
            assert hasattr(options, gate), (
                f"StepTemplate {step_name!r} gates on {gate!r}, which is not a "
                f"WorkflowOptions field: the step would never mint."
            )
            assert gate in GENOMIC_ANALYSIS.option_fields, (
                f"StepTemplate {step_name!r} gates on {gate!r}, which the recipe does "
                f"not declare in option_fields."
            )


# --- the CRAM/SAM lane, which is what was actually broken ---------------------


def _cram_or_sam_options() -> WorkflowOptions:
    """What FileProcessor.determine_workflow's CRAM and SAM branches set.

    Derived, not transcribed. The hand-written version of this said "needs_gatk /
    needs_pypgx / needs_mtdna and nothing else; needs_hla is left False there" -- which
    was an accurate description of a bug (main.nf runs PyPGxBam2Vcf and
    OptiTypeHLAFromBAM on both lanes) written down as if it were the design, in a helper
    the one test that would have caught it did not use.
    """
    return _options_for(FileType.CRAM)


def test_a_cram_or_sam_job_mints_the_conversion_step():
    steps = resolve_steps("genomic_analysis", _cram_or_sam_options())
    by_name = {s.step_name: s for s in steps}
    assert "gatk_cram_sam_to_bam" in by_name, [s.step_name for s in steps]
    assert by_name["gatk_cram_sam_to_bam"].container_name == "gatk-api"


def test_the_conversion_runs_before_the_steps_that_read_its_bam():
    """PyPGx and OptiType are handed the BAM this step produces.

    On the options a real CRAM/SAM job actually has, deliberately: this test used to
    hand-build a set no such job ever carried (needs_hla and needs_pypgx_bam2vcf were
    both False on those lanes), so it asserted an ordering among steps that lane never
    minted.
    """
    ordered = [
        s.step_name for s in resolve_steps("genomic_analysis", _cram_or_sam_options())
    ]
    assert ordered.index("gatk_cram_sam_to_bam") < ordered.index("hla_typing")
    assert ordered.index("gatk_cram_sam_to_bam") < ordered.index("pypgx_bam2vcf")
    assert ordered.index("gatk_cram_sam_to_bam") < ordered.index("pypgx_analysis")


def test_the_conversion_step_lands_in_the_gatk_stage():
    assert STEP_TO_STAGE["gatk_cram_sam_to_bam"] is WorkflowStage.GATK


def test_a_plain_vcf_job_does_not_mint_the_conversion():
    names = [
        s.step_name
        for s in resolve_steps("genomic_analysis", WorkflowOptions(needs_pypgx=True))
    ]
    assert "gatk_cram_sam_to_bam" not in names


def test_a_bcf_job_mints_bcf_to_vcf_and_not_the_cram_sam_conversion():
    """The two conversions share needs_gatk and must not share a step.

    determine_workflow's BCF branch sets needs_conversion AND needs_gatk (bcftools
    runs in the gatk-api container). Gating the CRAM/SAM conversion on needs_gatk
    alone would mint it onto every BCF job, where no process posts it -- the same
    [pending]-forever failure, arrived at from the opposite direction.
    """
    names = [
        s.step_name
        for s in resolve_steps(
            "genomic_analysis",
            WorkflowOptions(needs_conversion=True, needs_gatk=True, needs_pypgx=True),
        )
    ]
    assert "bcf_to_vcf" in names
    assert "gatk_cram_sam_to_bam" not in names


def test_a_cram_job_does_not_mint_bcf_to_vcf():
    """The veto in the other direction: needs_conversion is what mints bcf_to_vcf."""
    names = [
        s.step_name for s in resolve_steps("genomic_analysis", _cram_or_sam_options())
    ]
    assert "bcf_to_vcf" not in names


# --- the two VCF-lane conversions, which share one flag --------------------------


def _gvcf_options() -> WorkflowOptions:
    """What FileProcessor.determine_workflow's GVCF branch sets.

    needs_conversion AND needs_gvcf_genotyping: the first says a gatk-api conversion is
    planned, the second says which one. needs_gatk because GenotypeGVCFs runs in the
    gatk-api container and upload_router turns that flag into --skip_gatk.
    """
    return _options_for(FileType.GVCF)


def test_a_gvcf_job_mints_gvcf_to_vcf_and_neither_other_conversion():
    """Three conversions, two shared flags, and only one may ever mint.

    needs_conversion is shared with BCF and needs_gatk with CRAM/SAM, so a gVCF job
    that minted any of the other two would hang that step at [pending] forever -- no
    process posts it, so its status updates 404. This is the same failure the CRAM and
    SAM lanes shipped with, reached from a third direction.
    """
    names = [s.step_name for s in resolve_steps("genomic_analysis", _gvcf_options())]

    assert "gvcf_to_vcf" in names
    assert "bcf_to_vcf" not in names
    assert "gatk_cram_sam_to_bam" not in names


def test_a_bcf_job_still_mints_bcf_to_vcf_and_not_the_gvcf_conversion():
    """The veto in the other direction: bcf_to_vcf is needs_conversion MINUS gVCF."""
    names = [
        s.step_name
        for s in resolve_steps(
            "genomic_analysis",
            WorkflowOptions(needs_conversion=True, needs_gatk=True, needs_pypgx=True),
        )
    ]

    assert "bcf_to_vcf" in names
    assert "gvcf_to_vcf" not in names


def test_the_gvcf_conversion_runs_before_the_steps_that_read_its_vcf():
    ordered = [s.step_name for s in resolve_steps("genomic_analysis", _gvcf_options())]

    assert ordered.index("gvcf_to_vcf") < ordered.index("pypgx_analysis")
    assert ordered.index("gvcf_to_vcf") < ordered.index("mtdna_analysis")
    assert ordered.index("gvcf_to_vcf") < ordered.index("pharmcat_analysis")


def test_the_gvcf_conversion_step_lands_in_the_gatk_stage():
    """GenotypeGVCFs runs in the gatk-api container, so the glyph row must light there.

    Without this entry stage_from_step() silently answers ANALYSIS and the progress bar
    names the wrong stage for the whole step.
    """
    assert STEP_TO_STAGE["gvcf_to_vcf"] is WorkflowStage.GATK


def test_the_gvcf_conversion_is_banded_and_ordered_like_the_other_conversions():
    """A step with no band is dropped by _active_ordered_steps(), which filters on
    membership of STEP_BASE_BANDS -- the defect that froze the bar for the whole
    liftover."""
    from app.services.workflow_progress_calculator import WorkflowProgressCalculator

    assert "gvcf_to_vcf" in WorkflowProgressCalculator.STEP_BASE_BANDS
    assert "gvcf_to_vcf" in WorkflowProgressCalculator.CANONICAL_STEP_ORDER


def test_the_progress_calculator_plans_the_conversion_the_registry_mints():
    """Two independent readers of the same pair of flags; they must not disagree."""
    from app.services.workflow_progress_calculator import WorkflowProgressCalculator

    calculator = WorkflowProgressCalculator()
    gvcf_plan = calculator._planned_steps_from_config(
        {
            "file_type": "gvcf",
            "needs_conversion": True,
            "needs_gvcf_genotyping": True,
            "needs_gatk": True,
            "needs_pypgx": True,
        }
    )
    assert "gvcf_to_vcf" in gvcf_plan
    assert "bcf_to_vcf" not in gvcf_plan
    # A gVCF is a variant call set by the time PyPGx sees it, so it must not be given
    # the BAM->VCF step, exactly as a BCF is not.
    assert "pypgx_bam2vcf" not in gvcf_plan

    bcf_plan = calculator._planned_steps_from_config(
        {
            "file_type": "bcf",
            "needs_conversion": True,
            "needs_gatk": True,
            "needs_pypgx": True,
        }
    )
    assert "bcf_to_vcf" in bcf_plan
    assert "gvcf_to_vcf" not in bcf_plan


# --- gatk_alignment: registered, and still unreachable ------------------------


def test_gatk_alignment_is_registered_so_the_registry_and_the_bar_agree():
    """It has a progress band and a canonical-order slot; it needed a template too."""
    from app.services.workflow_progress_calculator import WorkflowProgressCalculator

    assert "gatk_alignment" in _templates()
    assert "gatk_alignment" in WorkflowProgressCalculator.STEP_BASE_BANDS
    assert "gatk_alignment" in WorkflowProgressCalculator.CANONICAL_STEP_ORDER


def test_gatk_alignment_is_gated_on_a_flag_no_real_upload_sets():
    """Registering the name must not amount to claiming FASTQ works.

    FASTQ is refused at ingest -- ZaroPGx ships no aligner and gatk-api's
    /align-fastq answers HTTP 501 -- so determine_workflow's FASTQ branch sets no
    needs_* flag at all, and nothing anywhere sets needs_alignment. The gate is
    therefore false on every job a real upload can produce.
    """
    assert _templates()["gatk_alignment"].when == "needs_alignment"
    for options in (
        WorkflowOptions(),  # plain VCF
        WorkflowOptions(
            needs_conversion=True, needs_gatk=True, needs_pypgx=True
        ),  # BCF
        _gvcf_options(),  # gVCF
        WorkflowOptions(needs_liftover=True, needs_pypgx=True),  # GRCh37 VCF
        _cram_or_sam_options(),  # CRAM / SAM
        WorkflowOptions(  # BAM
            needs_hla=True,
            needs_pypgx=True,
            needs_pypgx_bam2vcf=True,
            needs_mtdna=True,
        ),
    ):
        names = [s.step_name for s in resolve_steps("genomic_analysis", options)]
        assert "gatk_alignment" not in names, options


# --- what each LANE posts vs. what that lane's job actually mints -----------------
#
# The tests above ask whether a posted name has a template ANYWHERE. That is the weaker
# half of the question and it passed throughout the CRAM/SAM bug: pypgx_bam2vcf has had
# a template all along, gated on needs_pypgx_bam2vcf, which the CRAM and SAM branches
# did not set -- so main.nf posted it on both lanes, no row existed, every update 404'd
# and the step sat [pending] for its whole duration. hla_typing was the same shape with
# a worse symptom: needs_hla left False becomes --skip_hla=true, so OptiType simply never
# ran and a CRAM silently got no HLA typing where a BAM holding the same reads did.
#
# So this section asks the real question per input type: run determine_workflow, mint
# the steps upload_router would mint, and compare against the processes main.nf runs on
# that lane. The lane -> process mapping below is the one hand-written part; the step
# names come from main.nf's own source, and every process named is checked to exist
# there, so a renamed or deleted process fails here rather than drifting quietly.


_PROCESS = re.compile(r"^process\s+([A-Za-z0-9_]+)\s*\{", re.M)

# Steps app-side code mints and posts for itself; no Nextflow process posts them.
APP_SIDE_STEPS = {"header_analysis", "report_generation"}

LANE_PROCESSES = {
    # The quick lane: no HLA, no BAM->VCF -- the file is already a call set.
    FileType.VCF: ("PyPGxGenotypeAll", "MtdnaCall", "PharmCATRun"),
    FileType.BCF: ("BcfToVCF", "PyPGxGenotypeAll", "MtdnaCall", "PharmCATRun"),
    FileType.GVCF: ("GVCFToVCF", "PyPGxGenotypeAll", "MtdnaCall", "PharmCATRun"),
    # The alignment lanes. CRAM and SAM are the BAM lane with a conversion in front:
    # main.nf hands OptiTypeHLAFromBAM and PyPGxBam2Vcf the BAM the conversion made.
    FileType.BAM: (
        "OptiTypeHLAFromBAM",
        "PyPGxBam2Vcf",
        "PyPGxGenotypeAll",
        "MtdnaCall",
        "PharmCATRun",
    ),
    FileType.CRAM: (
        "CramToBAM",
        "OptiTypeHLAFromBAM",
        "PyPGxBam2Vcf",
        "PyPGxGenotypeAll",
        "MtdnaCall",
        "PharmCATRun",
    ),
    FileType.SAM: (
        "SamToBAM",
        "OptiTypeHLAFromBAM",
        "PyPGxBam2Vcf",
        "PyPGxGenotypeAll",
        "MtdnaCall",
        "PharmCATRun",
    ),
}


def _step_name_by_process() -> dict[str, str]:
    """Each main.nf process -> the step_name it posts, read out of main.nf."""
    text = MAIN_NF.read_text(encoding="utf-8")
    starts = [(m.group(1), m.start()) for m in _PROCESS.finditer(text)]
    posted: dict[str, str] = {}
    for index, (name, start) in enumerate(starts):
        end = starts[index + 1][1] if index + 1 < len(starts) else len(text)
        names = set(_POSTED.findall(text[start:end]))
        if not names:
            continue
        assert len(names) == 1, (
            f"process {name} posts more than one step_name ({sorted(names)}); the "
            f"lane mapping below assumes one process reports as one step."
        )
        posted[name] = names.pop()
    return posted


def test_every_process_named_in_the_lane_map_exists_in_main_nf():
    """The hand-written half, checked. A renamed process must fail here, not drift."""
    posted = _step_name_by_process()

    for file_type, processes in LANE_PROCESSES.items():
        for process in processes:
            assert process in posted, (
                f"{file_type.value}: main.nf has no process {process!r} that posts a "
                f"step_name. Known: {sorted(posted)}"
            )


@pytest.mark.parametrize("file_type", ANALYSED_TYPES, ids=lambda t: t.value)
def test_a_real_job_mints_exactly_the_steps_its_lane_posts(file_type):
    """The guard the CRAM/SAM bug needed: flags, registry and pipeline as one claim.

    Posted-but-not-minted is the [pending]-forever 404. Minted-but-not-posted is a step
    that never leaves [pending] either, because nothing ever reports it finished.
    """
    posted = _step_name_by_process()
    expected = {posted[p] for p in LANE_PROCESSES[file_type]} | APP_SIDE_STEPS
    minted = {
        s.step_name for s in resolve_steps("genomic_analysis", _options_for(file_type))
    }

    assert minted == expected, (
        f"{file_type.value}: main.nf posts {sorted(expected - minted)} that this job "
        f"never mints, and mints {sorted(minted - expected)} that nothing on this lane "
        f"ever posts."
    )


@pytest.mark.parametrize("file_type", ANALYSED_TYPES, ids=lambda t: t.value)
def test_no_analysed_input_type_is_planned_as_unsupported(file_type):
    """Negative control for the fixtures above: these are all GRCh38 and all runnable,
    so a fixture that accidentally trips a refusal would empty the minted set and make
    the comparison above meaningless."""
    options = _options_for(file_type)

    assert options.unsupported is False
    assert options.is_provisional is False


def test_the_alignment_lanes_all_get_hla_typing():
    """A CRAM and a SAM hold the same reads a BAM does, and main.nf runs
    OptiTypeHLAFromBAM on all three -- on the BAM the conversion produced, for the two
    that need converting. needs_hla is what upload_router turns into --skip_hla, so
    leaving it False on those two lanes did not merely mis-draw the plan: OptiType never
    ran, and the docs promised it did."""
    for file_type in (FileType.BAM, FileType.CRAM, FileType.SAM):
        options = _options_for(file_type)
        assert options.needs_hla is True, file_type
        assert options.needs_pypgx_bam2vcf is True, file_type


def test_the_variant_call_lanes_get_neither():
    """The other half of that claim: a VCF/BCF/gVCF is already a call set, so there is
    no BAM to type HLA from and nothing for PyPGx's BAM->VCF step to do."""
    for file_type in (FileType.VCF, FileType.BCF, FileType.GVCF):
        options = _options_for(file_type)
        assert options.needs_hla is False, file_type
        assert options.needs_pypgx_bam2vcf is False, file_type
