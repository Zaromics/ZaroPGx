from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    username: Optional[str] = None


class FileType(str, Enum):
    VCF = "vcf"
    BAM = "bam"
    CRAM = "cram"
    SAM = "sam"
    FASTQ = "fastq"
    FASTA = "fasta"
    GVCF = "gvcf"
    BCF = "bcf"
    BED = "bed"
    TWENTYTHREE_AND_ME = "23andme"
    ANCESTRY_DNA = "ancestry"
    UNKNOWN = "unknown"


class SequencingProfile(str, Enum):
    WGS = "whole_genome_seq"
    WES = "whole_exome_seq"
    TARGETED = "targeted_seq"
    T2T = "telomere-to-telomere_seq"
    SHORT_READ = "short_read_seq"
    LONG_READ = "long_read_seq"
    NGS = "next_gen_seq"
    CHIP = "chip_seq"
    UNKNOWN = "unknown"


class SequenceInfo(BaseModel):
    """Information about a sequence/contig in the file"""

    name: str = Field(..., description="Sequence/contig name (e.g., chr1, chr2)")
    length: Optional[int] = Field(None, description="Sequence length in base pairs")


class ProgramInfo(BaseModel):
    """Information about programs used in file creation/processing"""

    id: str = Field(..., description="Program identifier")
    name: Optional[str] = Field(None, description="Program name")
    version: Optional[str] = Field(None, description="Program version")
    command_line: Optional[str] = Field(None, description="Command line used")


class FileInfo(BaseModel):
    """Basic file information"""

    path: str = Field(..., description="File path")
    format: FileType = Field(..., description="File format")
    size: int = Field(..., description="File size in bytes")
    compressed: bool = Field(..., description="Whether file is compressed")
    has_index: bool = Field(..., description="Whether file has an index")


class MetadataInfo(BaseModel):
    """Metadata extracted from file header"""

    version: Optional[str] = Field(None, description="File format version")
    created_by: Optional[str] = Field(None, description="Program that created the file")
    reference_genome: Optional[str] = Field(None, description="Reference genome used")
    reference_genome_path: Optional[str] = Field(
        None, description="Path to reference genome file"
    )


class FormatSpecificInfo(BaseModel):
    """Format-specific header information"""

    sam_header_lines: Optional[List[str]] = Field(
        None, description="SAM format header lines"
    )
    programs: Optional[List[ProgramInfo]] = Field(
        None, description="Programs used in processing"
    )
    vcf_info_fields: Optional[Dict[str, str]] = Field(
        None, description="VCF INFO field descriptions"
    )
    vcf_format_fields: Optional[Dict[str, str]] = Field(
        None, description="VCF FORMAT field descriptions"
    )


class GenomicFileHeader(BaseModel):
    """Comprehensive header information for genomic files"""

    file_info: FileInfo
    metadata: MetadataInfo
    sequences: List[SequenceInfo] = Field(default_factory=list)
    sample: Optional[str] = Field(None, description="Sample identifier")
    format_specific: FormatSpecificInfo = Field(default_factory=FormatSpecificInfo)


class VCFHeaderInfo(BaseModel):
    """Legacy VCF header info - kept for backward compatibility"""

    reference_genome: str
    sequencing_platform: str
    sequencing_profile: SequencingProfile
    has_index: bool
    is_bgzipped: bool
    contigs: List[str]
    sample_count: int
    variant_count: Optional[int] = None


class FileAnalysis(BaseModel):
    file_type: FileType
    is_compressed: bool
    has_index: bool
    vcf_info: Optional[VCFHeaderInfo] = None
    file_size: Optional[int] = None
    error: Optional[str] = None
    is_valid: bool = True
    validation_errors: Optional[List[str]] = None


class WorkflowOptions(BaseModel):
    """Typed job/upload workflow toggles (137b)."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    needs_gatk: bool = False
    needs_alignment: bool = False
    needs_pypgx: bool = False
    needs_pypgx_bam2vcf: bool = False
    needs_hla: bool = False
    needs_report: bool = True
    needs_conversion: bool = False
    is_provisional: bool = False
    unsupported: bool = False
    unsupported_reason: Optional[str] = None
    requested_reference: Optional[str] = None
    recommendations: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class ResolvedStep(BaseModel):
    """A step resolved from a recipe given options."""

    step_name: str
    step_order: int
    container_name: Optional[str] = None


class WorkflowStepTemplate(BaseModel):
    """API shape for a recipe step template."""

    step_name: str
    container_name: str
    when: Optional[str] = None  # None=always; else WorkflowOptions field name


class WorkflowRecipeResponse(BaseModel):
    """API shape for a workflow recipe (read-only registry)."""

    workflow_type: str
    display_name: str
    description: str = ""
    step_templates: List[WorkflowStepTemplate] = Field(default_factory=list)
    option_fields: List[str] = Field(default_factory=list)


class WorkflowInfo(BaseModel):
    """Upload response workflow payload: recipe key + options (137b)."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    workflow_type: str = "genomic_analysis"
    options: WorkflowOptions = Field(default_factory=WorkflowOptions)


class UploadResponse(BaseModel):
    file_id: str
    job_id: str
    file_type: str
    status: str
    message: str
    analysis_info: Optional[FileAnalysis] = None
    workflow: Optional[WorkflowInfo] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ProcessingStatus(BaseModel):
    file_id: str
    job_id: str
    status: str
    progress: int = Field(ge=0, le=100, description="Progress percentage from 0 to 100")
    message: str
    current_stage: Optional[str] = None
    error: Optional[str] = None
    last_updated: datetime = Field(default_factory=datetime.utcnow)


class GeneticDataStatus(BaseModel):
    file_id: str
    job_id: str
    file_type: FileType
    status: ProcessingStatus
    created_at: datetime
    processed_at: Optional[datetime] = None
    error_message: Optional[str] = None


class Allele(BaseModel):
    """Model for individual allele information"""

    name: str = Field(..., description="Allele name (e.g., CYP2C19*17)")
    function: Optional[str] = Field(
        None, description="Functional classification of the allele"
    )
    activity_score: Optional[float] = Field(
        None, ge=0, le=1, description="Activity score from 0 to 1"
    )


# This may need to be fixed, activity score does not range from 0 to 1.
class Diplotype(BaseModel):
    """Model for diplotype information with confidence metrics"""

    gene: str = Field(..., description="Gene name (e.g., CYP2C19)")
    diplotype: str = Field(..., description="Diplotype call (e.g., *1/*17)")
    phenotype: Optional[str] = Field(None, description="Phenotype classification")
    activity_score: Optional[float] = Field(
        None, ge=0, le=1, description="Activity score from 0 to 1"
    )
    confidence: Optional[float] = Field(
        None, ge=0, le=1, description="Confidence score from 0 to 1"
    )
    calling_method: str = Field(..., description="Method used for allele calling")


class AlleleCallResult(BaseModel):
    """Model for complete allele calling results"""

    patient_id: str = Field(..., description="Patient identifier")
    file_id: str = Field(..., description="File identifier")
    job_id: str
    diplotypes: List[Diplotype] = Field(..., description="List of diplotype calls")
    created_at: datetime = Field(..., description="When the results were generated")


class DrugRecommendation(BaseModel):
    """Model for drug-specific recommendations based on genetic data"""

    drug: str = Field(..., description="Drug name")
    gene: str = Field(..., description="Gene relevant to the drug")
    guideline: str = Field(..., description="Guideline source (e.g., CPIC)")
    recommendation: str = Field(..., description="Specific recommendation text")
    classification: Optional[str] = Field(
        None,
        description="Recommendation strength when known (e.g., 'Strong', 'Moderate'); omit rather than invent",
    )
    literature_references: Optional[List[str]] = Field(
        None,
        description="Supporting literature references when known; omit rather than invent",
    )


class ReportRequest(BaseModel):
    """Model for report generation requests"""

    patient_id: str = Field(..., description="Patient identifier")
    file_id: str = Field(..., description="File identifier")
    job_id: str
    report_type: str = Field("comprehensive", description="Type of report to generate")
    include_drugs: Optional[List[str]] = Field(
        None, description="Specific drugs to include (None for all)"
    )


class ReportResponse(BaseModel):
    """Model for report generation responses"""

    report_id: str = Field(..., description="Unique report identifier")
    patient_id: str = Field(..., description="Patient identifier")
    created_at: datetime = Field(..., description="When the report was created")
    report_url: str = Field(..., description="URL to access the generated report")
    report_type: str = Field(..., description="Type of report generated")


# ============================================================================
# JOB INSTANCE MODELS - Run-instance tracking (137a)
# ============================================================================


class JobStatus(str, Enum):
    """Job (run instance) lifecycle status enumeration"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(str, Enum):
    """Step status enumeration"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class LogLevel(str, Enum):
    """Log level enumeration"""

    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


class JobCreate(BaseModel):
    """Model for creating a new job (run instance)"""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(..., description="Job name")
    description: Optional[str] = Field(None, description="Job description")
    workflow_type: str = Field(..., description="Recipe key from workflow registry")
    options: WorkflowOptions = Field(default_factory=WorkflowOptions)
    total_steps: Optional[int] = Field(
        None, description="Total number of steps in the job"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Job metadata"
    )
    created_by: Optional[str] = Field(None, description="User who created the job")


class JobUpdate(BaseModel):
    """Model for updating a job"""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: Optional[str] = Field(None, description="Job name")
    description: Optional[str] = Field(None, description="Job description")
    status: Optional[JobStatus] = Field(None, description="Job status")
    total_steps: Optional[int] = Field(None, description="Total number of steps")
    completed_steps: Optional[int] = Field(
        None, description="Number of completed steps"
    )
    metadata: Optional[Dict[str, Any]] = Field(None, description="Job metadata")


class JobResponse(BaseModel):
    """Model for job responses"""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    id: str = Field(..., description="Job ID (run instance)")
    name: str = Field(..., description="Job name")
    description: Optional[str] = Field(None, description="Job description")
    status: JobStatus = Field(..., description="Job status")
    created_at: datetime = Field(..., description="When the job was created")
    started_at: Optional[datetime] = Field(None, description="When the job started")
    completed_at: Optional[datetime] = Field(
        None, description="When the job completed"
    )
    total_steps: Optional[int] = Field(None, description="Total number of steps")
    completed_steps: Optional[int] = Field(
        None, description="Number of completed steps"
    )
    metadata: Dict[str, Any] = Field(..., description="Job metadata")
    created_by: Optional[str] = Field(None, description="User who created the job")
    workflow_type: Optional[str] = None
    workflow_snapshot: Optional[Dict[str, Any]] = None


class JobStepCreate(BaseModel):
    """Model for creating a job step"""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    step_name: str = Field(..., description="Step name")
    step_order: int = Field(..., description="Step order in the job")
    container_name: Optional[str] = Field(
        None, description="Container that will execute this step"
    )
    output_data: Dict[str, Any] = Field(
        default_factory=dict, description="Step output data"
    )


class JobStepUpdate(BaseModel):
    """Model for updating a job step"""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    status: Optional[StepStatus] = Field(None, description="Step status")
    message: Optional[str] = Field(None, description="Status message")
    container_name: Optional[str] = Field(None, description="Container name")
    output_data: Optional[Dict[str, Any]] = Field(None, description="Step output data")
    error_details: Optional[Dict[str, Any]] = Field(
        None, description="Error details if step failed"
    )
    retry_count: Optional[int] = Field(None, description="Number of retries")


class JobStepResponse(BaseModel):
    """Model for job step responses"""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
        from_attributes=True,  # allow model_validate() on SQLAlchemy ORM rows
    )

    id: str = Field(..., description="Step ID")
    job_id: str = Field(..., description="Job ID")
    step_name: str = Field(..., description="Step name")
    step_order: int = Field(..., description="Step order")
    status: StepStatus = Field(..., description="Step status")
    container_name: Optional[str] = Field(None, description="Container name")
    started_at: Optional[datetime] = Field(None, description="When the step started")
    completed_at: Optional[datetime] = Field(
        None, description="When the step completed"
    )
    duration_seconds: Optional[int] = Field(
        None, description="Step duration in seconds"
    )
    output_data: Dict[str, Any] = Field(..., description="Step output data")
    error_details: Dict[str, Any] = Field(..., description="Error details")
    retry_count: int = Field(..., description="Number of retries")

    @field_validator("id", "job_id", mode="before")
    @classmethod
    def _coerce_uuid_to_str(cls, v):
        # ORM primary keys are UUID objects; coerce to str for the str-typed fields
        return str(v) if v is not None else v


class JobLogCreate(BaseModel):
    """Model for creating a job log entry"""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    step_name: Optional[str] = Field(None, description="Step name")
    log_level: LogLevel = Field(LogLevel.INFO, description="Log level")
    message: str = Field(..., description="Log message")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Log metadata")


class JobLogResponse(BaseModel):
    """Model for job log responses"""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    id: int = Field(..., description="Log ID")
    job_id: str = Field(..., description="Job ID")
    step_name: Optional[str] = Field(None, description="Step name")
    log_level: LogLevel = Field(..., description="Log level")
    message: str = Field(..., description="Log message")
    metadata: Dict[str, Any] = Field(..., description="Log metadata")
    timestamp: datetime = Field(..., description="When the log was created")


class JobProgressResponse(BaseModel):
    """Model for job progress responses"""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    job_id: str = Field(..., description="Job ID")
    status: JobStatus = Field(..., description="Job status")
    total_steps: int = Field(..., description="Total number of steps")
    completed_steps: int = Field(..., description="Number of completed steps")
    progress_percentage: float = Field(
        ..., ge=0, le=100, description="Progress percentage"
    )
    current_step: Optional[str] = Field(None, description="Current step name")
    estimated_completion: Optional[datetime] = Field(
        None, description="Estimated completion time"
    )
    message: Optional[str] = Field(None, description="Current status message")
