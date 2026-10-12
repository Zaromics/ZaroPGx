"""
Workflow Progress Calculator

Centralized progress calculation system for ZaroPGx workflows based on workflow_logic.md.
This module provides a single source of truth for workflow progress percentage.

Stage / step vocabulary (enum, step→stage map, display names) lives in
``app.services.workflow_stages`` — import ``WorkflowStage`` from there (re-exported
here for existing call sites).

The bar is a weighted sum over the job's own steps, not a sequence of bands: each
step weighs what it takes on a 30x whole genome (STEP_EXPECTED_SECONDS), counts in
full once it has finished, and while it runs counts by the progress it reports, or
else by its elapsed time against that expectation. The pipeline runs steps side by
side (mtDNA and ZaroHLA beside PyPGx's), so no order can be assumed.

Note: File uploading has its own progress bar; ``WorkflowStage.UPLOAD`` is soft only.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Union

from app.services.workflow_stages import (
    WorkflowStage,
    normalize_step_name,
    stage_from_step,
)

logger = logging.getLogger(__name__)


@dataclass
class ProgressInfo:
    """Progress information for a workflow step"""

    stage: WorkflowStage
    progress_percentage: int
    message: str
    is_skippable: bool = False
    current_step_name: str = None


class WorkflowProgressCalculator:
    """
    Centralized workflow progress calculator based on workflow_logic.md specifications.

    This class provides a single source of truth for:
    - Progress percentage calculation
    - Stage mapping and transitions
    - Message generation for each stage
    - Skippable step detection
    """

    # Stage definitions with their progress ranges and messages based on workflow_logic.md
    STAGE_DEFINITIONS = {
        WorkflowStage.ANALYSIS: {
            "min_progress": 1,
            "max_progress": 9,
            "message": "Analyzing file headers and metadata. Determining workflow.",
            "is_skippable": False,
        },
        WorkflowStage.GATK: {
            "min_progress": 10,
            "max_progress": 49,
            "message": "GATK processing",
            "is_skippable": True,
        },
        WorkflowStage.HLA: {
            "min_progress": 20,
            "max_progress": 34,
            "message": "ZaroHLA processing:HLA typing with OptiType",
            "is_skippable": True,
        },
        WorkflowStage.PYPGX: {
            "min_progress": 50,
            "max_progress": 74,
            "message": "PyPGx processing",
            "is_skippable": True,
        },
        WorkflowStage.MTDNA: {
            "min_progress": 75,
            "max_progress": 79,
            "message": "mtDNA-Server 2 processing",
            "is_skippable": True,
        },
        WorkflowStage.PHARMCAT: {
            "min_progress": 80,
            "max_progress": 89,
            "message": "PharmCAT processing",
            "is_skippable": False,
        },
        WorkflowStage.REPORT: {
            "min_progress": 90,
            "max_progress": 100,
            "message": "Generating reports and visualizations",
            "is_skippable": False,
        },
        WorkflowStage.COMPLETED: {
            "min_progress": 100,
            "max_progress": 100,
            "message": "Processing complete!",
            "is_skippable": False,
        },
    }

    # Seconds a step takes on a 30x whole genome: its weight in the bar, and the pace
    # of a running step that reports no progress of its own. From the step rows'
    # started_at/completed_at, 2026-10-07: NA12878's BAM and CRAM and a 237 MB WGS
    # VCF. (*) marks an estimate, no whole-genome run measured yet.
    #
    # The bar used to lay these steps end to end in a fixed order, each in a band,
    # and show the end of the furthest finished one. The pipeline runs them side by
    # side, so a step that finished early pulled the bar to its band's end and held
    # it there: on the BAM, mtDNA (beside ZaroHLA) took it to 65% at 4 minutes, where
    # it sat for the 15 minutes of bam2vcf and PyPGx; on the WGS VCF, 51% at 27
    # seconds, held through PyPGx's 5 minutes; on the CRAM, the 33-minute conversion
    # reports nothing and sat at its band's start.
    STEP_EXPECTED_SECONDS: Dict[str, int] = {
        "header_analysis": 2,
        "bcf_to_vcf": 60,  # (*) bcftools view, a few minutes at most
        "gvcf_to_vcf": 120,  # (*) GenotypeGVCFs at PharmCAT's positions only
        "liftover": 60,  # (*) Picard LiftoverVcf
        "gatk_cram_sam_to_bam": 1970,
        "hla_typing": 245,
        "gatk_alignment": 3600,  # (*) bwa-mem; FASTQ uploads are capped at 20 GB
        "pypgx_bam2vcf": 590,
        "pypgx_analysis": 320,
        "mtdna_analysis": 240,
        "pharmcat_analysis": 15,
        "diagram_generation": 5,  # (*)
        "report_generation": 15,
    }
    # mtDNA from a variant file reads only its chrM records: 27 s on the WGS VCF.
    VARIANT_INPUT_MTDNA_SECONDS = 30
    _ALIGNMENT_FILE_TYPES = frozenset(
        {"bam", "cram", "sam", "fastq", "fq", "fastq.gz", "fq.gz"}
    )
    # A step the table does not name still counts rather than vanishing from the bar
    # (unlisted, liftover once froze it for a whole lift).
    DEFAULT_STEP_SECONDS = 60
    # A running step paced by its elapsed time stops short of its full share: an
    # estimate must not claim the step is done, however long it overruns.
    PACED_CEILING = 0.9

    # The order the pipeline's steps come in, for anything listing them.
    CANONICAL_STEP_ORDER: List[str] = [
        "header_analysis",
        # A GRCh37 BCF is converted first and lifted second.
        "bcf_to_vcf",
        "gvcf_to_vcf",
        "liftover",
        "gatk_cram_sam_to_bam",
        "hla_typing",
        "gatk_alignment",
        # PyPGxBam2Vcf produces the VCF PyPGxGenotypeAll reads.
        "pypgx_bam2vcf",
        "pypgx_analysis",
        "mtdna_analysis",
        "pharmcat_analysis",
        "diagram_generation",
        "report_generation",
    ]

    def _expected_seconds(
        self, step_name: str, workflow_config: Optional[Dict] = None
    ) -> int:
        if step_name == "mtdna_analysis":
            cfg = workflow_config or {}
            file_type = str(
                (cfg.get("file_analysis") or {}).get("file_type")
                or cfg.get("file_type")
                or ""
            ).lower()
            if file_type and file_type not in self._ALIGNMENT_FILE_TYPES:
                return self.VARIANT_INPUT_MTDNA_SECONDS
        return self.STEP_EXPECTED_SECONDS.get(step_name, self.DEFAULT_STEP_SECONDS)

    def _step_fraction(self, step: Dict, expected: int, now: datetime) -> float:
        """How much of a step is done: all of it once it has ended (a failed step
        is over too), its own reported progress while it runs, else its elapsed
        time against what it is expected to take, up to PACED_CEILING."""
        # A StepStatus member until the row is read back; str() of it is
        # "StepStatus.COMPLETED", not its value.
        status = step.get("status")
        status = str(getattr(status, "value", status) or "").lower()
        if status in ("completed", "skipped", "failed"):
            return 1.0
        if status != "running":
            return 0.0
        reported = self._extract_container_progress(step, None)
        if reported is not None:
            return reported / 100.0
        started = step.get("started_at")
        if isinstance(started, str):
            try:
                started = datetime.fromisoformat(started.replace("Z", "+00:00"))
            except ValueError:
                started = None
        if not isinstance(started, datetime):
            return 0.0
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        elapsed = (now - started).total_seconds()
        return max(0.0, min(self.PACED_CEILING, elapsed / max(1, expected)))

    # Highest progress already reported per job, so the bar can never run
    # backwards. CLASS level, not instance: both call sites
    # (job_service.get_job_progress and upload_router's status endpoint) build a
    # fresh WorkflowProgressCalculator() on every request, so an instance cache
    # was empty every time and the no-decrease rule below never fired once. Seen
    # live as 50% -> 40% mid-PyPGx, where the container's own reported progress
    # dipped.
    #
    # Bounded because it is process-lifetime state keyed by job id: without the
    # trim a long-running app accumulates one int per job forever.
    _previous_progress_cache: Dict[str, int] = {}
    _PROGRESS_CACHE_MAX = 512

    def __init__(self):
        """Initialize the progress calculator."""
        self.logger = logging.getLogger(__name__)

    def calculate_progress_from_steps(
        self,
        steps: List[Dict],
        workflow_config: Optional[Dict] = None,
        workflow_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> ProgressInfo:
        """
        Calculate progress based on workflow steps and configuration.
        Args:
            steps: List of workflow steps with status information (and started_at,
                which paces a running step that reports no progress)
            workflow_config: Optional workflow configuration dict
            workflow_id: Optional workflow ID for progress caching to prevent decreases
            now: The time to pace running steps against (default: the clock)
        Returns:
            ProgressInfo with current stage, progress, and message
        """
        if not steps:
            return ProgressInfo(
                stage=WorkflowStage.UPLOAD,
                progress_percentage=0,
                message="Starting workflow",
            )

        # Determine current stage based on completed steps and workflow config
        current_stage = self._determine_current_stage(steps, workflow_config)

        # Calculate progress based on the current stage and completed steps
        # This now includes container progress mapping
        calculated_progress = self._calculate_stage_progress_with_container_mapping(
            current_stage, steps, workflow_config, now
        )

        # STRICT NO-DECREASE RULE: Progress can never go backward
        # This is a safety net to prevent any edge cases from causing progress to decrease
        if workflow_id:
            previous_progress = self._previous_progress_cache.get(workflow_id, 0)
            progress_percentage = max(calculated_progress, previous_progress)

            if progress_percentage != calculated_progress:
                self.logger.warning(
                    f"Workflow {workflow_id}: Progress prevented from decreasing from {previous_progress}% to {calculated_progress}%. Using {progress_percentage}% instead."
                )

            # Update cache with the final progress
            cache = type(self)._previous_progress_cache
            if (
                workflow_id not in cache
                and len(cache) >= type(self)._PROGRESS_CACHE_MAX
            ):
                # Oldest insertion first (dicts preserve insertion order). A job
                # evicted here can only lose its floor, never its real progress.
                for stale in list(cache)[: len(cache) // 2]:
                    del cache[stale]
            cache[workflow_id] = progress_percentage
        else:
            progress_percentage = calculated_progress

        # Get stage message
        message = self._get_stage_message(current_stage, steps, workflow_config)

        # Get the current step name for the current stage
        current_step_name = self._get_current_step_name(current_stage, steps)

        return ProgressInfo(
            stage=current_stage,
            progress_percentage=progress_percentage,
            message=message,
            is_skippable=self.STAGE_DEFINITIONS[current_stage]["is_skippable"],
            current_step_name=current_step_name,
        )

    def get_stage_progress_range(self, stage: WorkflowStage) -> Tuple[int, int]:
        """
        Get the progress range for a specific stage.
        Args:
            stage: The workflow stage
        Returns:
            Tuple of (min_progress, max_progress)
        """
        if stage not in self.STAGE_DEFINITIONS:
            return (0, 0)

        definition = self.STAGE_DEFINITIONS[stage]
        return (definition["min_progress"], definition["max_progress"])

    def is_stage_skippable(self, stage: WorkflowStage) -> bool:
        """
        Check if a stage can be skipped based on workflow configuration.
        Args:
            stage: The workflow stage
        Returns:
            True if the stage can be skipped, False otherwise
        """
        if stage not in self.STAGE_DEFINITIONS:
            return False

        return self.STAGE_DEFINITIONS[stage]["is_skippable"]

    def _determine_current_stage(
        self, steps: List[Dict], workflow_config: Optional[Dict] = None
    ) -> WorkflowStage:
        """Determine the current stage based on workflow progress, not step counting."""
        if not steps:
            return WorkflowStage.ANALYSIS

        # Check if report generation is completed - this means workflow is done
        report_completed = any(
            step.get("step_name") == "report_generation"
            and step.get("status") == "completed"
            for step in steps
        )
        if report_completed:
            return WorkflowStage.COMPLETED

        # Find the current running step
        for step in steps:
            if step.get("status") == "running":
                return self._map_step_name_to_stage(
                    step.get("step_name", ""), workflow_config
                )

        # If no running step, stay in the stage of the last completed step
        # until a new step actually starts running
        completed_steps = [step for step in steps if step.get("status") == "completed"]
        if not completed_steps:
            return WorkflowStage.ANALYSIS

        # Sort by step_order to get the sequence
        completed_steps.sort(key=lambda x: x.get("step_order", 0))
        last_completed_step = completed_steps[-1]
        last_step_name = last_completed_step.get("step_name", "")
        return self._map_step_name_to_stage(last_step_name, workflow_config)

    def _calculate_stage_progress_with_container_mapping(
        self,
        current_stage: WorkflowStage,
        steps: List[Dict],
        workflow_config: Optional[Dict] = None,
        now: Optional[datetime] = None,
    ) -> int:
        """The share of the job's expected work done, 0-99 until the report is
        generated, then 100. Each step counts once, whatever order the pipeline
        runs it in (see STEP_EXPECTED_SECONDS)."""
        if current_stage == WorkflowStage.COMPLETED:
            return 100
        if any(
            normalize_step_name(step.get("step_name") or "") == "report_generation"
            and step.get("status") == "completed"
            for step in steps
        ):
            return 100

        now = now or datetime.now(timezone.utc)
        seen = set()
        total = done = 0.0
        for step in steps:
            name = normalize_step_name(step.get("step_name") or "")
            if not name or name in seen:
                continue
            seen.add(name)
            expected = self._expected_seconds(name, workflow_config)
            total += expected
            done += expected * self._step_fraction(step, expected, now)
        if total <= 0:
            return 0
        return min(99, int(done * 100 / total))

    def _extract_container_progress(
        self, step: Dict, current_stage: WorkflowStage
    ) -> Optional[int]:
        """
        Extract container progress percentage from step data.

        This method looks for progress information in various places where
        containers might report their internal progress.
        """
        # Check step metadata for progress information
        metadata = step.get("metadata", {})
        if isinstance(metadata, dict):
            # Look for common progress field names
            for field in ["progress_percent", "progress_percentage", "progress"]:
                if field in metadata:
                    try:
                        progress = int(metadata[field])
                        if 0 <= progress <= 100:
                            return progress
                    except (ValueError, TypeError):
                        continue

        # Check step output_data for progress information
        output_data = step.get("output_data", {})
        if isinstance(output_data, dict):
            for field in ["progress_percent", "progress_percentage", "progress"]:
                if field in output_data:
                    try:
                        progress = int(output_data[field])
                        if 0 <= progress <= 100:
                            return progress
                    except (ValueError, TypeError):
                        continue

        # For specific stages, we might need to look in logs or other places
        # This is a fallback - containers should ideally report progress in metadata
        return None

    def _map_step_name_to_stage(
        self, step_name: str, workflow_config: Optional[Dict] = None
    ) -> WorkflowStage:
        """Map step name to workflow stage via shared vocabulary."""
        return stage_from_step(step_name or "")

    def _get_current_step_name(
        self, current_stage: WorkflowStage, steps: List[Dict]
    ) -> str:
        """Get the current step name for the given stage."""
        # Map stages to their corresponding step names
        stage_to_step_mapping = {
            WorkflowStage.ANALYSIS: "header_analysis",
            WorkflowStage.GATK: "gatk_processing",  # Generic fallback for GATK stage
            WorkflowStage.HLA: "hla_typing",
            WorkflowStage.PYPGX: "pypgx_analysis",
            WorkflowStage.MTDNA: "mtdna_analysis",
            WorkflowStage.PHARMCAT: "pharmcat_analysis",
            WorkflowStage.REPORT: "report_generation",
            WorkflowStage.COMPLETED: "completed",
        }

        # First, try to find a running step that matches the current stage
        for step in steps:
            if step.get("status") == "running":
                step_name = step.get("step_name", "")
                if self._map_step_name_to_stage(step_name) == current_stage:
                    return normalize_step_name(step_name) or step_name

        # If no running step found, return the default step name for the stage
        return stage_to_step_mapping.get(current_stage, "unknown")

    @staticmethod
    def _is_step_running(steps: List[Dict], step_name: str) -> bool:
        """True when `step_name` is the step currently running."""
        return any(
            normalize_step_name(step.get("step_name") or "") == step_name
            and step.get("status") == "running"
            for step in steps or []
        )

    def _should_skip_stage(self, stage: WorkflowStage, workflow_config: Dict) -> bool:
        """Check if a stage should be skipped based on workflow configuration."""
        skip_mapping = {
            # needs_liftover counts as needing GATK: Picard LiftoverVcf runs in the
            # gatk-api container and "liftover" maps to WorkflowStage.GATK. Keying
            # on needs_gatk alone called the stage skipped while it was running.
            # needs_conversion is here for the same reason: the VCF-lane conversions
            # (BCF->VCF via bcftools, gVCF->VCF via GATK GenotypeGVCFs) run in that
            # container too and map to the same stage. One flag covers both, which is
            # why widening its meaning did not need a second entry here.
            WorkflowStage.GATK: not (
                workflow_config.get("needs_gatk", False)
                or workflow_config.get("needs_liftover", False)
                or workflow_config.get("needs_conversion", False)
            ),
            WorkflowStage.HLA: not workflow_config.get("needs_hla", False),
            WorkflowStage.PYPGX: not workflow_config.get("needs_pypgx", True),
            WorkflowStage.MTDNA: not workflow_config.get("needs_mtdna", False),
        }

        return skip_mapping.get(stage, False)

    def _get_stage_message(
        self,
        stage: WorkflowStage,
        steps: List[Dict],
        workflow_config: Optional[Dict] = None,
    ) -> str:
        """Get the appropriate message for a stage."""
        stage_def = self.STAGE_DEFINITIONS.get(stage, {})
        base_message = stage_def.get("message", "Processing...")

        # Add stage-specific details
        if stage == WorkflowStage.UPLOAD:
            return f"{base_message} - {len(steps)} steps remaining"
        elif stage == WorkflowStage.ANALYSIS:
            return f"{base_message} - Inspecting file headers"
        elif stage == WorkflowStage.GATK:
            cfg = workflow_config or {}
            # Step first, config second. A GRCh37 VCF has needs_gatk=False and
            # needs_liftover=True, so keying on needs_gatk alone announced
            # "Skipping GATK processing" while Picard LiftoverVcf was running --
            # the stage is GATK precisely because the liftover step maps to it.
            if self._is_step_running(steps, "bcf_to_vcf"):
                return f"{base_message} - Converting BCF to a bgzipped VCF"
            if self._is_step_running(steps, "gvcf_to_vcf"):
                return f"{base_message} - Genotyping gVCF into a plain VCF"
            if self._is_step_running(steps, "liftover"):
                return f"{base_message} - Lifting GRCh37/hg19 over to GRCh38"
            if cfg.get("needs_gatk", False):
                return f"{base_message} - Converting file format"
            if cfg.get("needs_liftover", False):
                return f"{base_message} - Liftover to GRCh38"
            return "Skipping GATK processing - not required"
        elif stage == WorkflowStage.HLA:
            if workflow_config and workflow_config.get("needs_hla", False):
                return f"{base_message} - Determining HLA types"
            else:
                return "Skipping HLA typing - not required"
        elif stage == WorkflowStage.PYPGX:
            if workflow_config and workflow_config.get("needs_pypgx", True):
                return f"{base_message} - Analyzing pharmacogenomic variants"
            else:
                return "Skipping PyPGx analysis - not required"
        elif stage == WorkflowStage.MTDNA:
            if workflow_config and workflow_config.get("needs_mtdna", False):
                return f"{base_message} - Calling mitochondrial variants and haplogroup"
            else:
                return "Skipping mtDNA calling - not required"
        elif stage == WorkflowStage.PHARMCAT:
            return f"{base_message} - Generating drug recommendations"
        elif stage == WorkflowStage.REPORT:
            return f"{base_message} - Creating final reports"
        elif stage == WorkflowStage.COMPLETED:
            return f"{base_message} - All processing finished"

        return base_message

    def _is_vcf_workflow(self, workflow_config: Optional[Dict] = None) -> bool:
        """
        Determine if this is a VCF-based workflow by checking the file type in workflow metadata.
        Args: workflow_config: Workflow configuration containing file analysis data
        Returns: True if this is a VCF workflow, False otherwise
        """
        if not workflow_config:
            return False

        # Check file_analysis.file_type in workflow metadata
        file_analysis = workflow_config.get("file_analysis", {})
        file_type = file_analysis.get("file_type", "").lower()

        # VCF file types that don't need bam2vcf conversion
        vcf_types = ["vcf", "vcf.gz", "bcf", "bcf.gz"]

        return file_type in vcf_types
