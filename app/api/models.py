from pydantic import BaseModel, Field
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
    needs_gatk: bool
    needs_stargazer: bool
    needs_conversion: bool
    is_provisional: bool
    recommendations: List[str]
    warnings: List[str]


class UploadResponse(BaseModel):
    file_id: str
    file_type: str
    status: str
    message: str
    analysis_info: Optional[FileAnalysis] = None
    workflow: Optional[WorkflowInfo] = None
    created_at: datetime = datetime.utcnow()


class ProcessingStatus(BaseModel):
    file_id: str
    status: str
    progress: int
    message: str
    current_stage: Optional[str] = None
    error: Optional[str] = None
    last_updated: datetime = datetime.utcnow()


class GeneticDataStatus(BaseModel):
    file_id: str
    file_type: FileType
    status: ProcessingStatus
    created_at: datetime
    processed_at: Optional[datetime] = None
    error_message: Optional[str] = None


class Allele(BaseModel):
    name: str
    function: Optional[str] = None
    activity_score: Optional[float] = None


class Diplotype(BaseModel):
    gene: str
    diplotype: str
    phenotype: Optional[str] = None
    activity_score: Optional[float] = None
    confidence: Optional[float] = None
    calling_method: str


class AlleleCallResult(BaseModel):
    patient_id: str
    file_id: str
    diplotypes: List[Diplotype]
    created_at: datetime


class DrugRecommendation(BaseModel):
    drug: str
    gene: str
    guideline: str
    recommendation: str
    classification: str  # e.g., "Strong", "Moderate"
    literature_references: Optional[List[str]] = None


class ReportRequest(BaseModel):
    patient_id: str
    file_id: str
    report_type: str = "comprehensive"
    include_drugs: Optional[List[str]] = None  # If None, include all drugs


class ReportResponse(BaseModel):
    report_id: str
    patient_id: str
    created_at: datetime
    report_url: str
    report_type: str 