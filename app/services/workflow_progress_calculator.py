"""
Workflow Progress Calculator

Centralized progress calculation system for ZaroPGx workflows based on workflow_logic.md.
This module provides a single source of truth for workflow progress percentage.

Stage / step vocabulary (enum, step→stage map, display names) lives in
``app.services.workflow_stages`` — import ``WorkflowStage`` from there (re-exported
here for existing call sites).

The progress calculation follows the workflow stages defined in updated workflow_logic.md:
- 1-9% - ANALYSIS: File info and Header inspection
- 10-19% - GATK: Conversion to BAM from SAM/CRAM (skip if n/a)
- 20-34% - HLA / OptiType: ZaroHLA step (skip if n/a)
- 35-49% - GATK: Conversion to BAM from FASTQ (skip if n/a)
- 50-64% - PYPGX: PyPGx main step (skip if n/a)
- 65-74% - PYPGX: PyPGx bam2vcf conversion step (skip if n/a)
- 75-79% - MTDNA: mtDNA-Server 2 calling (skip if n/a)
- 80-89% - PHARMCAT: PharmCAT step
- 90-94% - REPORT: Generating workflow diagram
- 95-99% - REPORT: Generating PDF and HTML reports
- 100% - COMPLETE: Processing complete!

Note: File uploading has its own progress bar; ``WorkflowStage.UPLOAD`` is soft only.
"""

import logging
from dataclasses import dataclass
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

    STEP_BASE_BANDS: Dict[str, Tuple[int, int]] = {
        "header_analysis": (1, 9),
        # Picard LiftoverVcf in the gatk-api container, before anything else on a
        # GRCh37 VCF. Without a band here _active_ordered_steps() dropped it (it
        # filters on membership of this dict), so `ranges` had no entry, the
        # running-step loop in _calculate_stage_progress_with_container_mapping()
        # hit `continue`, and the bar froze at the end of header_analysis for the
        # entire lift. Same weight as the other GATK conversion; it never co-occurs
        # with them (VCF vs aligned input), so the relative order is free.
        "liftover": (10, 19),
        "gatk_cram_sam_to_bam": (10, 19),
        "hla_typing": (20, 34),
        "gatk_alignment": (35, 49),
        "pypgx_analysis": (50, 64),
        "pypgx_bam2vcf": (65, 74),
        # Same weight as liftover/gatk_cram_sam_to_bam above: mtDNA calling
        # (mutserve + haplogrep3 + haplocheck, or the lighter VCF-only path)
        # runs after PyPGx and immediately before PharmCAT, matching
        # workflow_registry.py's step order.
        "mtdna_analysis": (75, 84),
        "pharmcat_analysis": (75, 89),
        "diagram_generation": (90, 94),
        "report_generation": (90, 100),
    }

    CANONICAL_STEP_ORDER: List[str] = [
        "header_analysis",
        "liftover",
        "gatk_cram_sam_to_bam",
        "hla_typing",
        "gatk_alignment",
        "pypgx_analysis",
        "pypgx_bam2vcf",
        "mtdna_analysis",
        "pharmcat_analysis",
        "diagram_generation",
        "report_generation",
    ]

    @staticmethod
    def _base_weight(step_name: str) -> int:
        band = WorkflowProgressCalculator.STEP_BASE_BANDS.get(step_name)
        if not band:
            return 0
        lo, hi = band
        return max(0, hi - lo)

    def _planned_steps_from_config(
        self, workflow_config: Optional[Dict] = None
    ) -> List[str]:
        cfg = workflow_config or {}
        fa = cfg.get("file_analysis") or {}
        file_type = str(fa.get("file_type") or cfg.get("file_type") or "").lower()
        is_vcf = file_type in {"vcf", "vcf.gz", "bcf", "bcf.gz"}
        needs_gatk = bool(cfg.get("needs_gatk", False))
        needs_hla = bool(cfg.get("needs_hla", False))
        needs_pypgx = bool(cfg.get("needs_pypgx", True))
        default_bam2vcf = bool(needs_pypgx and not is_vcf)
        needs_bam2vcf = bool(cfg.get("needs_pypgx_bam2vcf", default_bam2vcf))
        needs_mtdna = bool(cfg.get("needs_mtdna", False))

        planned: List[str] = ["header_analysis"]
        if bool(cfg.get("needs_liftover", False)):
            planned.append("liftover")
        if needs_gatk:
            if file_type in {"cram", "sam"}:
                planned.append("gatk_cram_sam_to_bam")
            if file_type in {"fastq", "fq", "fastq.gz", "fq.gz"}:
                planned.append("gatk_alignment")
        if needs_hla:
            planned.append("hla_typing")
        if needs_pypgx:
            planned.append("pypgx_analysis")
        if needs_bam2vcf:
            planned.append("pypgx_bam2vcf")
        if needs_mtdna:
            planned.append("mtdna_analysis")
        planned.extend(["pharmcat_analysis", "diagram_generation", "report_generation"])
        return planned

    def _active_ordered_steps(
        self, steps: List[Dict], workflow_config: Optional[Dict] = None
    ) -> List[str]:
        from_job = []
        for step in steps or []:
            name = normalize_step_name(step.get("step_name") or "")
            if name and name not in from_job and name in self.STEP_BASE_BANDS:
                from_job.append(name)
        planned = self._planned_steps_from_config(workflow_config)
        union = set(from_job) | set(planned)
        return [s for s in self.CANONICAL_STEP_ORDER if s in union]

    def _renormalized_ranges(
        self, active_steps: List[str]
    ) -> Dict[str, Tuple[int, int]]:
        if not active_steps:
            return {}
        weights = [self._base_weight(s) for s in active_steps]
        total = sum(weights)
        if total <= 0:
            return {}
        raw = [w * 100.0 / total for w in weights]
        floors = [int(x) for x in raw]
        remainders = sorted(
            ((raw[i] - floors[i], i) for i in range(len(active_steps))),
            key=lambda t: (-t[0], t[1]),
        )
        need = 100 - sum(floors)
        widths = floors[:]
        for k in range(need):
            widths[remainders[k][1]] += 1
        ranges: Dict[str, Tuple[int, int]] = {}
        start = 0
        for step, width in zip(active_steps, widths):
            if width <= 0:
                continue
            end = start + width - 1
            ranges[step] = (start, end)
            start = end + 1
        if ranges and active_steps:
            last = active_steps[-1]
            if last in ranges:
                rmin, _ = ranges[last]
                ranges[last] = (rmin, 100)
        return ranges

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
    ) -> ProgressInfo:
        """
        Calculate progress based on workflow steps and configuration.
        Args:
            steps: List of workflow steps with status information
            workflow_config: Optional workflow configuration dict
            workflow_id: Optional workflow ID for progress caching to prevent decreases
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
            current_stage, steps, workflow_config
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
    ) -> int:
        """
        Calculate progress percentage using actual container progress data.

        This method uses the progress_percent data that containers send via
        workflow_client.update_step_status() in their output_data.
        """
        # Special case: if workflow is completed, return 100%
        if current_stage == WorkflowStage.COMPLETED:
            return 100

        # Check if report generation is completed - this means 100% regardless of stage
        report_completed = any(
            step.get("step_name") == "report_generation"
            and step.get("status") == "completed"
            for step in steps
        )
        if report_completed:
            return 100

        active = self._active_ordered_steps(steps, workflow_config)
        ranges = self._renormalized_ranges(active)
        if not ranges:
            return 0

        max_achieved_progress = 0
        for step in steps:
            if step.get("status") == "completed":
                step_name = normalize_step_name(step.get("step_name") or "")
                if step_name in ranges:
                    _, rmax = ranges[step_name]
                    max_achieved_progress = max(max_achieved_progress, rmax)

        for step in steps:
            if step.get("status") == "running":
                current_step_name = normalize_step_name(step.get("step_name") or "")
                if current_step_name not in ranges:
                    continue

                rmin, rmax = ranges[current_step_name]
                container_progress = self._extract_container_progress(
                    step, current_stage
                )

                if container_progress is not None:
                    width = rmax - rmin + 1
                    mapped = rmin + int((container_progress / 100.0) * width)
                    if mapped > rmax:
                        mapped = rmax
                    return max(max_achieved_progress, mapped)
                return max(max_achieved_progress, rmin)

        return max_achieved_progress

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
            WorkflowStage.GATK: not (
                workflow_config.get("needs_gatk", False)
                or workflow_config.get("needs_liftover", False)
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
