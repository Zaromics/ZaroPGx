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
    MTDNA = "mtdna"
    PHARMCAT = "pharmcat"
    REPORT = "report"
    COMPLETED = "completed"


STEP_ALIASES: Mapping[str, str] = {
    "workflow_diagram": "diagram_generation",
}

STEP_TO_STAGE: Mapping[str, WorkflowStage] = {
    "header_analysis": WorkflowStage.ANALYSIS,
    # One name for both alignment-format conversions: main.nf's CramToBAM and
    # SamToBAM both post gatk_cram_sam_to_bam (a job has one input type, so only
    # one of them can ever run). They used to post gatk_cram_to_bam /
    # gatk_sam_to_bam, which this map, the progress calculator and the registry
    # all had no entry for, so CRAM and SAM uploads 404'd their status updates and
    # sat at [pending] for the whole conversion. Do not re-split the name without
    # adding StepTemplates and glyphs for both halves.
    "gatk_cram_sam_to_bam": WorkflowStage.GATK,
    # FASTQ->BAM. Mapped, banded and registered, but unreachable: FASTQ is refused
    # at ingest (no aligner ships). See workflow_registry's template comment.
    "gatk_alignment": WorkflowStage.GATK,
    # BCF -> bgzipped VCF runs inside the gatk-api container (bcftools), so it
    # surfaces under the GATK stage rather than falling through
    # stage_from_step()'s ANALYSIS default -- same grouping as "liftover" below.
    # Keep in sync with index.html's GlyphManager.stepMapping.
    "bcf_to_vcf": WorkflowStage.GATK,
    # gVCF -> plain VCF runs GATK GenotypeGVCFs in the gatk-api container, same
    # grouping and same reason as "bcf_to_vcf" above and "liftover" below.
    # Keep in sync with index.html's GlyphManager.stepMapping.
    "gvcf_to_vcf": WorkflowStage.GATK,
    # GRCh37->GRCh38 liftover runs inside the gatk-api container (Picard
    # LiftoverVcf), so it surfaces under the GATK stage rather than falling
    # through stage_from_step()'s ANALYSIS default.
    "liftover": WorkflowStage.GATK,
    "hla_typing": WorkflowStage.HLA,
    "pypgx_analysis": WorkflowStage.PYPGX,
    "pypgx_bam2vcf": WorkflowStage.PYPGX,
    # mtDNA calling (its own "mtdna" sidecar) supplies PharmCAT's MT-RNR1
    # outside call and runs immediately before it in the registry. It had no
    # dedicated stage at first and grouped with PHARMCAT so the glyph row
    # would not go dark; now it has its own stage/glyph (BACKLOG 46, 78).
    # Keep in sync with index.html's GlyphManager.stepMapping.
    "mtdna_analysis": WorkflowStage.MTDNA,
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
    WorkflowStage.MTDNA: "mtDNA",
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
