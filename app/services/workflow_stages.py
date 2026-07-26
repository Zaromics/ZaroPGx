"""Canonical workflow Stage / Step vocabulary (Wave 4 / item 136).

Coarse stages are persisted as snake_case. Fine-grained Nextflow/container
``step_name`` strings stay unchanged and map into stages via STEP_TO_STAGE.
Title Case (and OptiType for HLA) is display-only.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Mapping, Union

logger = logging.getLogger(__name__)


class WorkflowStage(str, Enum):
    """Workflow stages for progress and monitoring.

    ``UPLOAD`` is soft: the upload bar owns that UX; calculator ranges start
    at ``ANALYSIS``.
    """

    UPLOAD = "upload"
    ANALYSIS = "analysis"
    GATK = "gatk"
    HLA = "hla"
    PYPGX = "pypgx"
    PHARMCAT = "pharmcat"
    REPORT = "report"
    COMPLETED = "completed"


STEP_ALIASES: Mapping[str, str] = {
    "workflow_diagram": "diagram_generation",
}

STEP_TO_STAGE: Mapping[str, WorkflowStage] = {
    "header_analysis": WorkflowStage.ANALYSIS,
    "gatk_cram_sam_to_bam": WorkflowStage.GATK,
    "gatk_alignment": WorkflowStage.GATK,
    "hla_typing": WorkflowStage.HLA,
    "pypgx_analysis": WorkflowStage.PYPGX,
    "pypgx_bam2vcf": WorkflowStage.PYPGX,
    "pharmcat_analysis": WorkflowStage.PHARMCAT,
    "diagram_generation": WorkflowStage.REPORT,
    "report_generation": WorkflowStage.REPORT,
    "completed": WorkflowStage.COMPLETED,
}

_STAGE_DISPLAY: Mapping[WorkflowStage, str] = {
    WorkflowStage.UPLOAD: "Upload",
    WorkflowStage.ANALYSIS: "Analysis",
    WorkflowStage.GATK: "GATK",
    WorkflowStage.HLA: "OptiType",
    WorkflowStage.PYPGX: "PyPGx",
    WorkflowStage.PHARMCAT: "PharmCAT",
    WorkflowStage.REPORT: "Report",
    WorkflowStage.COMPLETED: "Complete",
}

_STAGE_ALIASES: Mapping[str, str] = {
    "uploading": "upload",
    "complete": "completed",
}


def normalize_step_name(raw: str) -> str:
    """Return the canonical step_name for an alias or passthrough."""
    if not raw:
        return ""
    return STEP_ALIASES.get(raw, raw)


def stage_from_step(step_name: str) -> WorkflowStage:
    """Map a step_name (possibly aliased) to a WorkflowStage."""
    canonical = normalize_step_name(step_name or "")
    return STEP_TO_STAGE.get(canonical, WorkflowStage.ANALYSIS)


def parse_stage(raw: str) -> WorkflowStage:
    """Parse a persisted/API stage string; unknown values become ANALYSIS."""
    if not raw:
        return WorkflowStage.ANALYSIS
    key = _STAGE_ALIASES.get(raw, raw)
    try:
        return WorkflowStage(key)
    except ValueError:
        logger.warning("Unknown workflow stage %r; defaulting to analysis", raw)
        return WorkflowStage.ANALYSIS


def stage_display_name(stage: Union[WorkflowStage, str]) -> str:
    """Human-facing label for a stage (Title Case / OptiType / PharmCAT)."""
    if isinstance(stage, str):
        stage = parse_stage(stage)
    return _STAGE_DISPLAY.get(stage, stage.value)
