"""Code registry of Workflow recipes (137b). Source of truth for step templates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from app.api.models import ResolvedStep, WorkflowOptions


@dataclass(frozen=True)
class StepTemplate:
    step_name: str
    container_name: str
    when: Optional[str] = None  # WorkflowOptions field name, or None = always
    # A second WorkflowOptions field name that VETOES the step when truthy, checked
    # after `when`. Exists because different conversions share one flag:
    # FileProcessor.determine_workflow sets needs_gatk for CRAM, SAM, BCF *and* gVCF,
    # but a BCF's gatk-api work is bcf_to_vcf and a gVCF's is gvcf_to_vcf, not the
    # CRAM/SAM->BAM conversion. Gating gatk_cram_sam_to_bam on needs_gatk alone would
    # mint it onto every BCF and gVCF job, where no process ever posts it -- the same
    # [pending]-forever symptom this file's comments keep recording, arrived at from
    # the opposite direction. It is used a second time to split needs_conversion
    # between the two VCF-lane conversions. Kept as a plain field name rather than a
    # predicate so the recipe stays declarative and serialisable. NOTE: the read-only
    # recipe API
    # (app/api/routes/workflow_recipe_router.py) still emits only step_name /
    # container_name / when, so a client reading it sees every step that uses this
    # field as gated on its `when` alone. Harmless today -- nothing consumes that
    # endpoint to decide anything -- but add "unless" there (and to
    # models.WorkflowStepTemplate) before anything starts planning from it. Two
    # templates rely on it now (gatk_cram_sam_to_bam and bcf_to_vcf), so the gap is
    # wider than when it was written.
    unless: Optional[str] = None


@dataclass(frozen=True)
class WorkflowRecipe:
    workflow_type: str
    display_name: str
    description: str = ""
    step_templates: tuple[StepTemplate, ...] = ()
    option_fields: tuple[str, ...] = ()


GENOMIC_ANALYSIS = WorkflowRecipe(
    workflow_type="genomic_analysis",
    display_name="Genomic Analysis",
    description="Pharmacogenomic analysis pipeline (VCF/BAM/FASTQ family).",
    option_fields=(
        "needs_gatk",
        "needs_alignment",
        "needs_pypgx",
        "needs_pypgx_bam2vcf",
        "needs_hla",
        "needs_mtdna",
        "needs_report",
        "needs_conversion",
        "needs_gvcf_genotyping",
        "needs_liftover",
        "is_provisional",
        "unsupported",
    ),
    step_templates=(
        StepTemplate("header_analysis", "header_inspector"),
        # BCF -> bgzipped VCF via gatk-api's bcftools endpoint (/bcf-to-vcf).
        # Registered for the same reason as "liftover" below -- main.nf's BcfToVCF
        # process posts step_name=bcf_to_vcf, and a step name with no template is
        # never minted onto the Job, so the sidecar's status update 404s and the UI
        # hangs that step at [pending] forever. Ordered first among the conversions
        # because a GRCh37 BCF is converted and *then* lifted; the liftover only ever
        # sees the VCF this step produced.
        #
        # needs_conversion means "a gatk-api conversion into the VCF lane". It meant
        # "a BCF" and nothing else until the gVCF lane landed; it was WIDENED rather
        # than left alone with a third independent flag beside it, because the
        # gatk_cram_sam_to_bam veto below is `unless="needs_conversion"` and a
        # conversion flag that veto does not know about mints that step onto a job no
        # process posts it for -- the [pending]-forever failure this file's comments
        # keep recording. One general flag, vetoed once; the specific converter is
        # chosen by needs_gvcf_genotyping.
        #
        # The 23andMe branch of FileProcessor.determine_workflow used to set
        # needs_conversion too, for a conversion that has never existed -- harmless
        # while nothing read the flag, and a mint-a-step-nobody-posts bug the moment
        # this template landed. That branch no longer sets it. Keep it that way: a flag
        # that names a step must only be set by an input that step can actually finish.
        # The two gVCF refusals (_refuse_non_gatk_gvcf, _refuse_grch37_gvcf) follow the
        # same rule and set neither flag.
        StepTemplate(
            "bcf_to_vcf",
            "gatk-api",
            when="needs_conversion",
            unless="needs_gvcf_genotyping",
        ),
        # gVCF -> plain genotyped VCF via gatk-api's GATK GenotypeGVCFs endpoint
        # (/gvcf-to-vcf). Registered for the same reason as every conversion here --
        # main.nf's GVCFToVCF process posts step_name=gvcf_to_vcf, and a step name with
        # no template is never minted onto the Job, so the sidecar's status update 404s
        # and the UI hangs that step at [pending] forever.
        #
        # It never co-occurs with bcf_to_vcf (a job has one input type, and the two
        # flags are set by mutually exclusive branches), so the pair above and here is
        # a two-armed switch rather than an ordering. Both sit before "liftover" for
        # the same reason: a file that needs converting is converted and THEN lifted.
        StepTemplate("gvcf_to_vcf", "gatk-api", when="needs_gvcf_genotyping"),
        # GRCh37/hg19 VCF -> GRCh38 via gatk-api's Picard LiftoverVcf. Registered
        # here because main.nf's LiftoverVCF process posts step_name=liftover to the
        # JobClient: a step name with no template is never minted onto the Job, so
        # the sidecar's status update 404s and the UI shows the step hanging
        # [pending] forever. Ordered before the PyPGx/PharmCAT steps because the
        # lift happens before either ever sees the file.
        StepTemplate("liftover", "gatk-api", when="needs_liftover"),
        # CRAM/SAM -> coordinate-sorted indexed BAM, via gatk-api's /cram-to-bam and
        # /sam-to-bam. Registered for the same reason as the two above, and missing for
        # longer: main.nf's CramToBAM and SamToBAM used to post gatk_cram_to_bam and
        # gatk_sam_to_bam, names NOTHING app-side knew, while every other app-side file
        # (workflow_stages.STEP_TO_STAGE, the progress calculator's bands, index.html's
        # glyph map) carried this single name and this file carried no template at all.
        # So a CRAM or SAM upload -- both supported, shipping input types -- had its
        # conversion 404 its status updates and sit at [pending] until the whole step
        # finished. main.nf now posts this name from both processes; one name is right
        # because a job has one input type, so only one of those processes can ever run.
        #
        # unless="needs_conversion" is load-bearing: determine_workflow sets needs_gatk
        # for BCF and gVCF too (their /bcf-to-vcf and /gvcf-to-vcf work runs in the same
        # container), and without the veto every such job would mint this step as well
        # as its own conversion and hang on the one no process posts.
        #
        # Ordered after the VCF-lane conversions and before hla_typing/pypgx_bam2vcf: the
        # BAM this produces is what OptiType and PyPGx are handed. It never co-occurs
        # with bcf_to_vcf/liftover (aligned input vs variant calls), so its position
        # relative to those two only has to match the progress calculator's
        # CANONICAL_STEP_ORDER, which it does.
        StepTemplate(
            "gatk_cram_sam_to_bam",
            "gatk-api",
            when="needs_gatk",
            unless="needs_conversion",
        ),
        StepTemplate("hla_typing", "zarohla", when="needs_hla"),
        # FASTQ -> BAM, via gatk-api's /align-fastq. main.nf's FastqToBAM posts
        # step_name=gatk_alignment and the progress calculator already gives it a band
        # (35-49) and a slot in CANONICAL_STEP_ORDER, so leaving it out kept the registry
        # and the calculator disagreeing about whether the name exists.
        #
        # It is UNREACHABLE and this template does not pretend otherwise: FASTQ is
        # refused at ingest (ZaroPGx ships no aligner; /align-fastq answers HTTP 501), so
        # determine_workflow's FASTQ branch sets no needs_* flag at all -- and nothing,
        # anywhere, sets needs_alignment. That is exactly why it is the gate: the step
        # can never mint while FASTQ is refused, and the day FASTQ is genuinely supported
        # the name is already registered instead of 404ing on its first run.
        StepTemplate("gatk_alignment", "gatk-api", when="needs_alignment"),
        StepTemplate("pypgx_bam2vcf", "pypgx", when="needs_pypgx_bam2vcf"),
        StepTemplate("pypgx_analysis", "pypgx", when="needs_pypgx"),
        # Mitochondrial calling via the mtdna sidecar. Registered here because
        # main.nf's MtdnaCall process posts step_name=mtdna_analysis to the
        # JobClient -- same rule as "liftover" above: a step name with no
        # template is never minted onto the Job, so the sidecar's status
        # update 404s and the UI shows the step hanging [pending] forever.
        # Ordered before pharmcat_analysis because its outside call
        # (pharmcat.mtdna.tsv) has to exist before PharmCAT reads
        # combined_outside.tsv.
        StepTemplate("mtdna_analysis", "mtdna", when="needs_mtdna"),
        StepTemplate("pharmcat_analysis", "pharmcat"),
        StepTemplate("report_generation", "report_generator", when="needs_report"),
    ),
)

_REGISTRY: dict[str, WorkflowRecipe] = {
    GENOMIC_ANALYSIS.workflow_type: GENOMIC_ANALYSIS,
}


def list_recipes() -> List[WorkflowRecipe]:
    return list(_REGISTRY.values())


def get_recipe(workflow_type: str) -> Optional[WorkflowRecipe]:
    return _REGISTRY.get(workflow_type)


def resolve_steps(workflow_type: str, options: WorkflowOptions) -> List[ResolvedStep]:
    recipe = get_recipe(workflow_type)
    if recipe is None:
        raise ValueError(f"Unknown workflow_type: {workflow_type}")

    resolved: List[ResolvedStep] = []
    order = 1
    for tmpl in recipe.step_templates:
        if tmpl.when is not None:
            if not bool(getattr(options, tmpl.when, False)):
                continue
        if tmpl.unless is not None:
            if bool(getattr(options, tmpl.unless, False)):
                continue
        resolved.append(
            ResolvedStep(
                step_name=tmpl.step_name,
                step_order=order,
                container_name=tmpl.container_name,
            )
        )
        order += 1
    return resolved


def build_snapshot(
    workflow_type: str, options: WorkflowOptions, steps: List[ResolvedStep]
) -> dict:
    return {
        "workflow_type": workflow_type,
        "options": options.model_dump(),
        "resolved_steps": [s.model_dump() for s in steps],
    }
