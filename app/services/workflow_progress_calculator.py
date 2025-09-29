"""
Workflow Progress Calculator

Centralized progress calculation system for ZaroPGx workflows based on workflow_logic.md.
This module provides a single source of truth for workflow progress percentage and stage mapping.

The progress calculation follows the workflow stages defined in updated workflow_logic.md:
- 1-9% - ANALYSIS: File info and Header inspection
- 10-19% - GATK: Conversion to BAM from SAM/CRAM (skip if n/a)
- 20-34% - HLA: OptiType/hlatyping step (skip if n/a)
- 35-49% - GATK: Conversion to BAM from FASTQ (skip if n/a)
- 50-64% - PYPGX: PyPGx main step (skip if n/a)
- 65-74% - PYPGX: PyPGx bam2vcf conversion step (skip if n/a)
- 75-89% - PHARMCAT: PharmCAT step
- 90-94% - REPORT: Generating workflow diagram
- 95-99% - REPORT: Generating PDF and HTML reports
- 100% - COMPLETE: Processing complete!

Note: File uploading has its own progress bar and is no longer a workflow stage.
"""

from typing import Dict, List, Optional, Tuple, Union
from dataclasses import dataclass
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class WorkflowStage(str, Enum):
    """Workflow stages as defined in workflow_logic.md"""
    UPLOADING = "uploading"
    ANALYSIS = "analysis"
    GATK = "gatk"
    HLA = "hla"
    PYPGX = "pypgx"
    PHARMCAT = "pharmcat"
    REPORT = "report"
    COMPLETED = "completed"


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
            "is_skippable": False
        },
        WorkflowStage.GATK: {
            "min_progress": 10,
            "max_progress": 49,
            "message": "GATK processing",
            "is_skippable": True
        },
        WorkflowStage.HLA: {
            "min_progress": 20,
            "max_progress": 34,
            "message": "hlatyping processing:HLA typing with OptiType",
            "is_skippable": True
        },
        WorkflowStage.PYPGX: {
            "min_progress": 50,
            "max_progress": 74,
            "message": "PyPGx processing",
            "is_skippable": True
        },
        WorkflowStage.PHARMCAT: {
            "min_progress": 75,
            "max_progress": 89,
            "message": "PharmCAT processing",
            "is_skippable": False
        },
        WorkflowStage.REPORT: {
            "min_progress": 90,
            "max_progress": 100,
            "message": "Generating reports and visualizations",
            "is_skippable": False
        },
        WorkflowStage.COMPLETED: {
            "min_progress": 100,
            "max_progress": 100,
            "message": "Processing complete!",
            "is_skippable": False
        }
    }
    
    def __init__(self):
        """Initialize the progress calculator."""
        self.logger = logging.getLogger(__name__)
        # Cache to track previous progress for each workflow to prevent decreases
        self._previous_progress_cache = {}
    
    def calculate_progress_from_steps(
        self, 
        steps: List[Dict], 
        workflow_config: Optional[Dict] = None,
        workflow_id: Optional[str] = None
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
                stage=WorkflowStage.UPLOADING,
                progress_percentage=0,
                message="Starting workflow"
            )
        
        # Determine current stage based on completed steps and workflow config
        current_stage = self._determine_current_stage(steps, workflow_config)
        
        # Calculate progress based on the current stage and completed steps
        # This now includes container progress mapping
        calculated_progress = self._calculate_stage_progress_with_container_mapping(current_stage, steps, workflow_config)
        
        # STRICT NO-DECREASE RULE: Progress can never go backward
        # This is a safety net to prevent any edge cases from causing progress to decrease
        if workflow_id:
            previous_progress = self._previous_progress_cache.get(workflow_id, 0)
            progress_percentage = max(calculated_progress, previous_progress)
            
            if progress_percentage != calculated_progress:
                self.logger.warning(f"Workflow {workflow_id}: Progress prevented from decreasing from {previous_progress}% to {calculated_progress}%. Using {progress_percentage}% instead.")
            
            # Update cache with the final progress
            self._previous_progress_cache[workflow_id] = progress_percentage
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
            current_step_name=current_step_name
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
        self, 
        steps: List[Dict], 
        workflow_config: Optional[Dict] = None
    ) -> WorkflowStage:
        """Determine the current stage based on workflow progress, not step counting."""
        if not steps:
            return WorkflowStage.ANALYSIS
        
        # Check if report generation is completed - this means workflow is done
        report_completed = any(
            step.get("step_name") == "report_generation" and step.get("status") == "completed"
            for step in steps
        )
        if report_completed:
            return WorkflowStage.COMPLETED
        
        # Find the current running step
        for step in steps:
            if step.get("status") == "running":
                return self._map_step_name_to_stage(step.get("step_name", ""), workflow_config)
        
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
        workflow_config: Optional[Dict] = None
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
            step.get("step_name") == "report_generation" and step.get("status") == "completed"
            for step in steps
        )
        if report_completed:
            return 100
        
        # Determine if this is a VCF-based workflow (no bam2vcf conversion needed)
        is_vcf_workflow = self._is_vcf_workflow(workflow_config)
        
        # Map step names to their progress ranges based on workflow_logic.md
        # For VCF workflows, pypgx_analysis uses the full PyPGx range (50-74%)
        step_progress_mapping = {
            "header_analysis": (1, 9),        # 1-9%: ANALYSIS - File info and Header inspection
            "gatk_cram_sam_to_bam": (10, 19), # 10-19%: GATK - CRAM/SAM→BAM conversion  
            "gatk_alignment": (35, 49),       # 35-49%: GATK - FASTQ→BAM alignment
            "hla_typing": (20, 34),           # 20-34%: HLA - OptiType/hlatyping step
            "pypgx_analysis": (50, 74) if is_vcf_workflow else (50, 64),  # Full range for VCF, split for BAM. NOT WORKING! NEEDS FIXING.
            "pypgx_bam2vcf": (65, 74),        # 65-74%: PYPGX - bam2vcf conversion (only for non-VCF)
            "pharmcat_analysis": (75, 89),    # 75-89%: PHARMCAT - PharmCAT step
            "diagram_generation": (90, 94),   # 90-94%: REPORT - Workflow diagram generation
            "report_generation": (90, 100)    # 90-100%: REPORT - PDF/HTML report generation
        }
        
        # Find the highest progress achieved by any completed step
        max_achieved_progress = 0
        for step in steps:
            if step.get("status") == "completed":
                step_name = step.get("step_name")
                if step_name in step_progress_mapping:
                    _, step_max = step_progress_mapping[step_name]
                    max_achieved_progress = max(max_achieved_progress, step_max)
        
        # Find the current running step and use its container progress
        for step in steps:
            if step.get("status") == "running":
                current_step_name = step.get("step_name")
                container_progress = self._extract_container_progress(step, current_stage)
                
                if container_progress is not None and current_step_name in step_progress_mapping:
                    step_min, step_max = step_progress_mapping[current_step_name]
                    # Map container progress (0-100%) to the step's overall range
                    mapped_progress = step_min + (container_progress / 100.0) * (step_max - step_min)
                    return max(max_achieved_progress, int(mapped_progress))
                elif current_step_name in step_progress_mapping:
                    # No container progress, use step minimum
                    step_min, _ = step_progress_mapping[current_step_name]
                    return max(max_achieved_progress, step_min)
        
        # No running step, return highest achieved progress
        return max_achieved_progress

    def _extract_container_progress(self, step: Dict, current_stage: WorkflowStage) -> Optional[int]:
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

    # POSSIBLY DEPRECATED, MAY BE REMOVED IN THE FUTURE
    def _calculate_stage_progress(
        self, 
        current_stage: WorkflowStage, 
        steps: List[Dict], 
        workflow_config: Optional[Dict] = None
    ) -> int:
        """Calculate progress percentage based on current stage, not step counting."""
        # Special case: if workflow is completed, return 100%
        if current_stage == WorkflowStage.COMPLETED:
            return 100
        
        # Check if report generation is completed - this means 100% regardless of stage
        report_completed = any(
            step.get("step_name") == "report_generation" and step.get("status") == "completed"
            for step in steps
        )
        if report_completed:
            return 100
        
        stage_def = self.STAGE_DEFINITIONS.get(current_stage, {})
        min_progress = stage_def.get("min_progress", 0)
        max_progress = stage_def.get("max_progress", 100)
        
        # Map step names to their progress ranges (legacy method - not currently used)
        # Determine if this is a VCF-based workflow for consistency
        is_vcf_workflow = self._is_vcf_workflow(workflow_config)
        
        step_progress_mapping = {
            "file_upload": (0, 10),
            "header_analysis": (10, 20),
            "hla_typing": (20, 30),
            "pypgx_analysis": (30, 60) if is_vcf_workflow else (30, 50),  # Full range for VCF
            "pypgx_bam2vcf": (50, 60),  # Only for non-VCF workflows
            "pharmcat_analysis": (60, 80),
            "report_generation": (80, 100)
        }
        
        # Find the highest progress achieved by any completed step
        max_achieved_progress = min_progress
        for step in steps:
            if step.get("status") == "completed":
                step_name = step.get("step_name")
                if step_name in step_progress_mapping:
                    _, step_max = step_progress_mapping[step_name]
                    max_achieved_progress = max(max_achieved_progress, step_max)
        
        # Find the current running step
        current_step_name = None
        for step in steps:
            if step.get("status") == "running":
                current_step_name = step.get("step_name")
                break
        
        # If there's a running step, use its minimum progress, but never go backward
        if current_step_name and current_step_name in step_progress_mapping:
            step_min, step_max = step_progress_mapping[current_step_name]
            # Use the minimum progress for the current step, but never go backward
            return max(max_achieved_progress, step_min)
        else:
            # No running step, use the highest achieved progress
            return max_achieved_progress
    
    def _map_step_name_to_stage(self, step_name: str, workflow_config: Optional[Dict] = None) -> WorkflowStage:
        """Map step name to workflow stage."""
        step_mapping = {
            "header_analysis": WorkflowStage.ANALYSIS,
            "gatk_cram_sam_to_bam": WorkflowStage.GATK,
            "gatk_alignment": WorkflowStage.GATK,
            "hla_typing": WorkflowStage.HLA,
            "pypgx_analysis": WorkflowStage.PYPGX,
            "pypgx_bam2vcf": WorkflowStage.PYPGX,
            "pharmcat_analysis": WorkflowStage.PHARMCAT,
            "diagram_generation": WorkflowStage.REPORT,
            "report_generation": WorkflowStage.REPORT,
            "completed": WorkflowStage.COMPLETED
        }
        
        return step_mapping.get(step_name, WorkflowStage.ANALYSIS)
    
    def _get_current_step_name(self, current_stage: WorkflowStage, steps: List[Dict]) -> str:
        """Get the current step name for the given stage."""
        # Map stages to their corresponding step names
        stage_to_step_mapping = {
            WorkflowStage.ANALYSIS: "header_analysis", 
            WorkflowStage.GATK: "gatk_processing",  # Generic fallback for GATK stage
            WorkflowStage.HLA: "hla_typing",
            WorkflowStage.PYPGX: "pypgx_analysis",
            WorkflowStage.PHARMCAT: "pharmcat_analysis",
            WorkflowStage.REPORT: "report_generation",
            WorkflowStage.COMPLETED: "completed"
        }
        
        # First, try to find a running step that matches the current stage
        for step in steps:
            if step.get("status") == "running":
                step_name = step.get("step_name", "")
                if self._map_step_name_to_stage(step_name) == current_stage:
                    return step_name
        
        # If no running step found, return the default step name for the stage
        return stage_to_step_mapping.get(current_stage, "unknown")
    
    def _should_skip_stage(self, stage: WorkflowStage, workflow_config: Dict) -> bool:
        """Check if a stage should be skipped based on workflow configuration."""
        skip_mapping = {
            WorkflowStage.GATK: not workflow_config.get("needs_gatk", False),
            WorkflowStage.HLA: not workflow_config.get("needs_hla", False),
            WorkflowStage.PYPGX: not workflow_config.get("needs_pypgx", True)
        }
        
        return skip_mapping.get(stage, False)
    
    def _get_stage_message(
        self, 
        stage: WorkflowStage, 
        steps: List[Dict], 
        workflow_config: Optional[Dict] = None
    ) -> str:
        """Get the appropriate message for a stage."""
        stage_def = self.STAGE_DEFINITIONS.get(stage, {})
        base_message = stage_def.get("message", "Processing...")
        
        # Add stage-specific details
        if stage == WorkflowStage.UPLOADING:
            return f"{base_message} - {len(steps)} steps remaining"
        elif stage == WorkflowStage.ANALYSIS:
            return f"{base_message} - Inspecting file headers"
        elif stage == WorkflowStage.GATK:
            if workflow_config and workflow_config.get("needs_gatk", False):
                return f"{base_message} - Converting file format"
            else:
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
