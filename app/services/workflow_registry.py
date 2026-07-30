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
        "needs_report",
        "needs_conversion",
        "is_provisional",
        "unsupported",
    ),
    step_templates=(
        StepTemplate("header_analysis", "header_inspector"),
        StepTemplate("hla_typing", "zarohla", when="needs_hla"),
        StepTemplate("pypgx_bam2vcf", "pypgx", when="needs_pypgx_bam2vcf"),
        StepTemplate("pypgx_analysis", "pypgx", when="needs_pypgx"),
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
