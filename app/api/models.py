from pydantic import BaseModel, Field, ConfigDict
from typing import List, Dict, Optional, Any
from datetime import datetime
from enum import Enum


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
    TWENTYTHREE_AND_ME = "23andme"
    UNKNOWN = "unknown"


class SequencingProfile(str, Enum):
    WGS = "whole_genome_sequencing"
    WES = "whole_exome_sequencing"
    TARGETED = "targeted_sequencing"
    UNKNOWN = "unknown"


class VCFHeaderInfo(BaseModel):
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


class WorkflowInfo(BaseModel):
    """
    Model representing the workflow configuration for processing a genomic file.
    """
    # Processing requirements
    needs_gatk: bool = False
    needs_alignment: bool = False
    needs_pypgx: bool = False 
    needs_pypgx_bam2vcf: bool = False
    needs_conversion: bool = False
    
    # File processing flags
    is_provisional: bool = False
    go_directly_to_pharmcat: bool = False
    
    # Original file info
    original_file_type: Optional[str] = None
    original_file_id: Optional[str] = None
    using_original_file: bool = False
    
    # Reference genome info
    requested_reference: Optional[str] = None
    
    # Support status
    unsupported: bool = False
    unsupported_reason: Optional[str] = None
    
    # Messages
    recommendations: List[str] = []
    warnings: List[str] = []


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


# Enhanced Job Monitoring Models with better validation and documentation
class JobStatus(str, Enum):
    """Job status enumeration with descriptive values"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobStage(str, Enum):
    """Job stage enumeration representing the pipeline stages"""
    UPLOAD = "upload"
    ANALYSIS = "analysis"
    GATK = "gatk"
    PYPX = "pypgx"
    PHARMCAT = "pharmcat"
    REPORT = "report"
    COMPLETE = "complete"


class JobStageStatus(str, Enum):
    """Job stage status enumeration"""
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class JobEventType(str, Enum):
    """Job event type enumeration for logging"""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    DEBUG = "debug"


class JobBase(BaseModel):
    """Base model for job-related data with comprehensive validation"""
    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        extra="forbid"
    )
    
    job_id: str = Field(..., description="Unique identifier for the job")
    status: JobStatus = Field(..., description="Current status of the job")
    stage: JobStage = Field(..., description="Current stage of the job")
    progress: int = Field(..., ge=0, le=100, description="Progress percentage from 0 to 100")
    message: Optional[str] = Field(None, description="Current status message")
    error_message: Optional[str] = Field(None, description="Error message if job failed")
    job_metadata: Dict[str, Any] = Field(default_factory=dict, description="Flexible metadata storage")
    started_at: datetime = Field(..., description="When the job started")
    updated_at: datetime = Field(..., description="When the job was last updated")
    created_at: datetime = Field(..., description="When the job was created")


class JobCreate(BaseModel):
    """Model for creating a new job with validation"""
    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid"
    )
    
    patient_id: Optional[str] = Field(None, description="Optional patient identifier")
    file_id: Optional[str] = Field(None, description="Optional file identifier")
    initial_stage: JobStage = Field(JobStage.UPLOAD, description="Starting stage for the job")
    job_metadata: Dict[str, Any] = Field(default_factory=dict, description="Initial metadata for the job")


class JobUpdate(BaseModel):
    """Model for updating job status with partial updates"""
    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid"
    )
    
    stage: Optional[JobStage] = Field(None, description="New stage for the job")
    progress: Optional[int] = Field(None, ge=0, le=100, description="New progress percentage")
    message: Optional[str] = Field(None, description="New status message")
    error_message: Optional[str] = Field(None, description="Error message if applicable")
    job_metadata: Optional[Dict[str, Any]] = Field(None, description="Updated metadata")


class JobResponse(BaseModel):
    """Model for job status responses with comprehensive information"""
    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid"
    )
    
    job_id: str = Field(..., description="Unique identifier for the job")
    status: JobStatus = Field(..., description="Current status of the job")
    stage: JobStage = Field(..., description="Current stage of the job")
    progress: int = Field(..., ge=0, le=100, description="Progress percentage")
    message: Optional[str] = Field(None, description="Current status message")
    error_message: Optional[str] = Field(None, description="Error message if job failed")
    job_metadata: Dict[str, Any] = Field(..., description="Job metadata")
    started_at: datetime = Field(..., description="When the job started")
    updated_at: datetime = Field(..., description="When the job was last updated")
    completed_at: Optional[datetime] = Field(None, description="When the job completed")
    created_at: datetime = Field(..., description="When the job was created")


class JobStageResponse(BaseModel):
    """Model for job stage responses with timing information"""
    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid"
    )
    
    stage_id: int = Field(..., description="Unique identifier for the stage")
    job_id: str = Field(..., description="Job identifier this stage belongs to")
    stage: JobStage = Field(..., description="Stage name")
    status: JobStageStatus = Field(..., description="Current status of the stage")
    progress: int = Field(..., ge=0, le=100, description="Stage progress percentage")
    message: Optional[str] = Field(None, description="Stage status message")
    started_at: datetime = Field(..., description="When the stage started")
    completed_at: Optional[datetime] = Field(None, description="When the stage completed")
    duration_ms: Optional[int] = Field(None, ge=0, description="Stage duration in milliseconds")
    stage_metadata: Dict[str, Any] = Field(..., description="Stage-specific metadata")
    created_at: datetime = Field(..., description="When the stage record was created")


class JobEventResponse(BaseModel):
    """Model for job event responses with logging information"""
    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid"
    )
    
    event_id: int = Field(..., description="Unique identifier for the event")
    job_id: str = Field(..., description="Job identifier this event belongs to")
    event_type: JobEventType = Field(..., description="Type of event")
    message: str = Field(..., description="Event message")
    event_metadata: Dict[str, Any] = Field(..., description="Event-specific metadata")
    created_at: datetime = Field(..., description="When the event occurred")


class JobProgressUpdate(BaseModel):
    """Model for real-time progress updates with SSE support"""
    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid"
    )
    
    job_id: str = Field(..., description="Job identifier")
    status: JobStatus = Field(..., description="Current job status")
    stage: JobStage = Field(..., description="Current job stage")
    progress: int = Field(..., ge=0, le=100, description="Progress percentage")
    message: Optional[str] = Field(None, description="Status message")
    error_message: Optional[str] = Field(None, description="Error message if applicable")
    job_metadata: Dict[str, Any] = Field(..., description="Job metadata")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Update timestamp")
    keepalive: bool = Field(False, description="Whether this is a keepalive message")


class Allele(BaseModel):
    """Model for individual allele information"""
    name: str = Field(..., description="Allele name (e.g., CYP2C19*17)")
    function: Optional[str] = Field(None, description="Functional classification of the allele")
    activity_score: Optional[float] = Field(None, ge=0, le=1, description="Activity score from 0 to 1")

# This may need to be fixed, activity score does not range from 0 to 1.
class Diplotype(BaseModel):
    """Model for diplotype information with confidence metrics"""
    gene: str = Field(..., description="Gene name (e.g., CYP2C19)")
    diplotype: str = Field(..., description="Diplotype call (e.g., *1/*17)")
    phenotype: Optional[str] = Field(None, description="Phenotype classification")
    activity_score: Optional[float] = Field(None, ge=0, le=1, description="Activity score from 0 to 1")
    confidence: Optional[float] = Field(None, ge=0, le=1, description="Confidence score from 0 to 1")
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
    classification: str = Field(..., description="Recommendation strength (e.g., 'Strong', 'Moderate')")
    literature_references: Optional[List[str]] = Field(None, description="Supporting literature references")


class ReportRequest(BaseModel):
    """Model for report generation requests"""
    patient_id: str = Field(..., description="Patient identifier")
    file_id: str = Field(..., description="File identifier")
    job_id: str
    report_type: str = Field("comprehensive", description="Type of report to generate")
    include_drugs: Optional[List[str]] = Field(None, description="Specific drugs to include (None for all)")


class ReportResponse(BaseModel):
    """Model for report generation responses"""
    report_id: str = Field(..., description="Unique report identifier")
    patient_id: str = Field(..., description="Patient identifier")
    created_at: datetime = Field(..., description="When the report was created")
    report_url: str = Field(..., description="URL to access the generated report")
    report_type: str = Field(..., description="Type of report generated") 