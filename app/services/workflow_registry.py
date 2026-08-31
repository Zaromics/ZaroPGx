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
        # needs_conversion is also set by the 23andMe branch of
        # FileProcessor.determine_workflow, which is refused at ingest, so no job
        # exists for it to mint this step onto.
        StepTemplate("bcf_to_vcf", "gatk-api", when="needs_conversion"),
        # GRCh37/hg19 VCF -> GRCh38 via gatk-api's Picard LiftoverVcf. Registered
        # here because main.nf's LiftoverVCF process posts step_name=liftover to the
        # JobClient: a step name with no template is never minted onto the Job, so
        # the sidecar's status update 404s and the UI shows the step hanging
        # [pending] forever. Ordered before the PyPGx/PharmCAT steps because the
        # lift happens before either ever sees the file.
        StepTemplate("liftover", "gatk-api", when="needs_liftover"),
        StepTemplate("hla_typing", "zarohla", when="needs_hla"),
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
