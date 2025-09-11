"""
Workflow Stage Manager

This module provides proper mapping between workflow stages and progress percentages
as defined in workflow_logic.md. It handles the transition between different stages
and ensures progress updates align with expected milestones.
"""

from enum import Enum
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass


class WorkflowStage(Enum):
    """Workflow stages as defined in workflow_logic.md - aligned with JobStage enum"""
    UPLOAD_START = "upload_start"           # 0%
    HEADER_INSPECTION = "header_inspection" # 5%
    UPLOAD_COMPLETE = "upload_complete"     # 10%
    GATK_CONVERSION = "gatk_conversion"     # 20%
    HLA_TYPING = "hla_typing"              # 30%
    FASTQ_CONVERSION = "fastq_conversion"   # 40%
    PYPX_ANALYSIS = "pypgx_analysis"        # 50%
    PYPX_BAM2VCF = "pypgx_bam2vcf"         # 60%
    PHARMCAT_ANALYSIS = "pharmcat_analysis" # 70%
    WORKFLOW_DIAGRAM = "workflow_diagram"   # 80%
    REPORT_GENERATION = "report_generation" # 90%
    COMPLETE = "complete"                   # 100%
    
    # Legacy stage mappings for backward compatibility
    ANALYSIS = "analysis"                   # Legacy - maps to PYPX_ANALYSIS
    GATK = "gatk"                          # Legacy - maps to GATK_CONVERSION
    PYPX = "pypgx"                         # Legacy - maps to PYPX_ANALYSIS
    PHARMCAT = "pharmcat"                  # Legacy - maps to PHARMCAT_ANALYSIS
    REPORT = "report"                      # Legacy - maps to REPORT_GENERATION


@dataclass
class WorkflowMilestone:
    """Represents a workflow milestone with its progress percentage and description"""
    stage: WorkflowStage
    percentage: int
    description: str
    nextflow_processes: List[str] = None  # Nextflow process names that trigger this stage
    skip_conditions: List[str] = None     # Conditions that would skip this stage


class WorkflowStageManager:
    """
    Manages workflow stage transitions and progress mapping.
    
    This class provides the proper mapping between workflow stages and progress
    percentages as defined in workflow_logic.md, ensuring consistent progress
    tracking across the entire pipeline.
    """
    
    def __init__(self):
        """Initialize the workflow stage manager with defined milestones"""
        self.milestones = self._create_milestones()
        self.current_stage = WorkflowStage.UPLOAD_START
        self.current_progress = 0
        
    def _create_milestones(self) -> List[WorkflowMilestone]:
        """Create the workflow milestones as defined in workflow_logic.md"""
        return [
            WorkflowMilestone(
                stage=WorkflowStage.UPLOAD_START,
                percentage=0,
                description="File uploading starts"
            ),
            WorkflowMilestone(
                stage=WorkflowStage.HEADER_INSPECTION,
                percentage=5,
                description="File info and Header inspection finished"
            ),
            WorkflowMilestone(
                stage=WorkflowStage.UPLOAD_COMPLETE,
                percentage=10,
                description="File upload finished"
            ),
            WorkflowMilestone(
                stage=WorkflowStage.GATK_CONVERSION,
                percentage=20,
                description="Conversion to BAM with GATK",
                nextflow_processes=["fastqtobam", "cramtobam", "samtobam", "gatk", "alignment"],
                skip_conditions=["vcf_input", "no_gatk_needed"]
            ),
            WorkflowMilestone(
                stage=WorkflowStage.HLA_TYPING,
                percentage=30,
                description="OptiType/hlatyping step",
                nextflow_processes=["optitype", "hlatyping", "hla", "optitypehlafromfastq", "optitypehlafrombam"],
                skip_conditions=["vcf_input", "no_hla_needed"]
            ),
            WorkflowMilestone(
                stage=WorkflowStage.FASTQ_CONVERSION,
                percentage=40,
                description="Conversion to BAM from FASTQ",
                nextflow_processes=["fastqtobam", "fastq", "alignment"],
                skip_conditions=["not_fastq_input", "already_bam"]
            ),
            WorkflowMilestone(
                stage=WorkflowStage.PYPX_ANALYSIS,
                percentage=50,
                description="PyPGx step",
                nextflow_processes=["pypgx", "pypgxgenotypeall", "genotype", "pypgxgenotype", "starallele"]
            ),
            WorkflowMilestone(
                stage=WorkflowStage.PYPX_BAM2VCF,
                percentage=60,
                description="PyPGx bam2vcf conversion step",
                nextflow_processes=["pypgxbam2vcf", "createinputvcf", "bam2vcf", "vcfcreation"],
                skip_conditions=["not_bam_input", "already_vcf"]
            ),
            WorkflowMilestone(
                stage=WorkflowStage.PHARMCAT_ANALYSIS,
                percentage=70,
                description="PharmCAT step",
                nextflow_processes=["pharmcat", "pharmcatrun", "interpretation", "pharmcatanalysis", "drugrecommendation"]
            ),
            WorkflowMilestone(
                stage=WorkflowStage.WORKFLOW_DIAGRAM,
                percentage=80,
                description="Generating workflow diagram",
                nextflow_processes=["workflowdiagram", "diagram", "workflow", "visualization"]
            ),
            WorkflowMilestone(
                stage=WorkflowStage.REPORT_GENERATION,
                percentage=90,
                description="Generating PDF and HTML reports",
                nextflow_processes=["pdfgeneration", "htmlgeneration", "report", "pdf", "html", "reportgeneration"]
            ),
            WorkflowMilestone(
                stage=WorkflowStage.COMPLETE,
                percentage=100,
                description="Processing complete!"
            )
        ]
    
    def get_milestone_by_stage(self, stage: WorkflowStage) -> Optional[WorkflowMilestone]:
        """Get milestone by stage"""
        for milestone in self.milestones:
            if milestone.stage == stage:
                return milestone
        return None
    
    def get_milestone_by_percentage(self, percentage: int) -> Optional[WorkflowMilestone]:
        """Get milestone by percentage"""
        for milestone in self.milestones:
            if milestone.percentage == percentage:
                return milestone
        return None
    
    def get_next_milestone(self, current_stage: WorkflowStage) -> Optional[WorkflowMilestone]:
        """Get the next milestone after the current stage"""
        current_index = None
        for i, milestone in enumerate(self.milestones):
            if milestone.stage == current_stage:
                current_index = i
                break
        
        if current_index is not None and current_index + 1 < len(self.milestones):
            return self.milestones[current_index + 1]
        return None
    
    def should_skip_stage(self, stage: WorkflowStage, workflow_config: Dict) -> bool:
        """Determine if a stage should be skipped based on workflow configuration"""
        milestone = self.get_milestone_by_stage(stage)
        if not milestone or not milestone.skip_conditions:
            return False
        
        for condition in milestone.skip_conditions:
            if condition in workflow_config and workflow_config[condition]:
                return True
        return False
    
    def get_stage_for_nextflow_process(self, process_name: str, workflow_config: Dict) -> Optional[WorkflowStage]:
        """Get the appropriate stage for a Nextflow process"""
        process_name_lower = process_name.lower()
        
        for milestone in self.milestones:
            if milestone.nextflow_processes:
                for np in milestone.nextflow_processes:
                    if np in process_name_lower:
                        # Check if this stage should be skipped
                        if not self.should_skip_stage(milestone.stage, workflow_config):
                            return milestone.stage
        return None
    
    def calculate_progress_for_stage(self, stage: WorkflowStage, workflow_config: Dict) -> Tuple[int, str]:
        """Calculate progress percentage and message for a given stage"""
        milestone = self.get_milestone_by_stage(stage)
        if not milestone:
            return 0, "Unknown stage"
        
        # Check if this stage should be skipped
        if self.should_skip_stage(stage, workflow_config):
            # Move to next stage
            next_milestone = self.get_next_milestone(stage)
            if next_milestone:
                return next_milestone.percentage, f"Skipped {milestone.description}"
            else:
                return milestone.percentage, milestone.description
        
        return milestone.percentage, milestone.description
    
    def update_stage(self, new_stage: WorkflowStage, workflow_config: Dict) -> Tuple[int, str]:
        """Update the current stage and return progress info"""
        self.current_stage = new_stage
        progress, message = self.calculate_progress_for_stage(new_stage, workflow_config)
        self.current_progress = progress
        return progress, message
    
    def get_stage_sequence(self, workflow_config: Dict) -> List[WorkflowStage]:
        """Get the sequence of stages that should be executed for this workflow"""
        sequence = []
        for milestone in self.milestones:
            if not self.should_skip_stage(milestone.stage, workflow_config):
                sequence.append(milestone.stage)
        return sequence
    
    def get_ui_stage_mapping(self) -> Dict[str, str]:
        """Get mapping from backend stage names to UI stage names"""
        return {
            "upload_start": "Upload",
            "header_inspection": "Upload", 
            "upload_complete": "Upload",
            "gatk_conversion": "GATK",
            "hla_typing": "HLA",
            "fastq_conversion": "GATK",
            "pypgx_analysis": "PyPGx",
            "pypgx_bam2vcf": "PyPGx",
            "pharmcat_analysis": "PharmCAT",
            "workflow_diagram": "Report",
            "report_generation": "Report",
            "complete": "Report"
        }
    
    def get_ui_stage_for_backend_stage(self, backend_stage: str) -> str:
        """Convert backend stage name to UI stage name"""
        mapping = self.get_ui_stage_mapping()
        return mapping.get(backend_stage, "Analysis")
    
    def map_legacy_stage_to_workflow_stage(self, legacy_stage: str) -> WorkflowStage:
        """Map legacy stage names to WorkflowStage enum values"""
        legacy_mapping = {
            "analysis": WorkflowStage.PYPX_ANALYSIS,
            "gatk": WorkflowStage.GATK_CONVERSION,
            "pypgx": WorkflowStage.PYPX_ANALYSIS,
            "pharmcat": WorkflowStage.PHARMCAT_ANALYSIS,
            "report": WorkflowStage.REPORT_GENERATION,
            "upload": WorkflowStage.UPLOAD_COMPLETE
        }
        return legacy_mapping.get(legacy_stage, WorkflowStage.UPLOAD_START)
    
    def get_workflow_stage_from_string(self, stage_string: str) -> WorkflowStage:
        """Get WorkflowStage enum from string, handling both new and legacy names"""
        # First try to match exact enum values
        for stage in WorkflowStage:
            if stage.value == stage_string:
                return stage
        
        # If not found, try legacy mapping
        return self.map_legacy_stage_to_workflow_stage(stage_string)


# Global instance for use throughout the application
workflow_stage_manager = WorkflowStageManager()
