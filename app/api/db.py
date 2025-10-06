import os
import logging
from typing import List, Optional
import uuid
import json
from datetime import datetime, timezone

# SQLAlchemy 2.0 imports with proper organization
from sqlalchemy import create_engine, text, String, Integer, DateTime, Text, JSON, ForeignKey, Boolean, Column, Enum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.sql import sqltypes

# Third-party imports
from dotenv import load_dotenv

# Import enum classes for proper type handling
from .models import WorkflowStatus, StepStatus, LogLevel

# Load environment variables
load_dotenv()

# Get database connection parameters from environment variables
DB_USER = os.getenv("DB_USER", "cpic_user")
DB_PASSWORD = os.getenv("DB_PASSWORD", "cpic_password")
DB_HOST = os.getenv("DB_HOST", "db")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "cpic_db")

# Create database URL with psycopg3
import psycopg  # psycopg3 for modern PostgreSQL connections
DATABASE_URL = f"postgresql+psycopg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# Create SQLAlchemy engine with PostgreSQL 17 and psycopg3 optimizations
engine = create_engine(
    DATABASE_URL,
    # Connection pool settings optimized for PostgreSQL 17
    pool_size=10,  # Increased for better performance with modern PostgreSQL
    max_overflow=20,  # Increased overflow for high-concurrency scenarios
    pool_pre_ping=True,  # Verify connections before reuse
    pool_recycle=3600,  # Recycle connections every hour
    pool_reset_on_return="commit",  # Reset connections properly on return

    # Performance and compatibility settings
    echo=False,  # Set to True for SQL debugging
    future=True,  # Use SQLAlchemy 2.0 style (required for modern usage)

    # PostgreSQL 17 and psycopg3 optimizations
    connect_args={
        "connect_timeout": 10,
        "application_name": "ZaroPGx",
        # Note: server_settings for JIT control may not be available in all psycopg3 versions
        # Consider setting jit=off in postgresql.conf if experiencing performance issues
    }
)

# Create session factory
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    expire_on_commit=False
)

# Create base class for declarative models using SQLAlchemy 1.4 style
Base = declarative_base()

# Define all referenced tables to ensure foreign key resolution
class Patient(Base):
    """SQLAlchemy model for user_data.patients table"""
    __tablename__ = "patients"
    __table_args__ = {"schema": "user_data"}

    patient_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_identifier = Column(String(255), nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class GeneticData(Base):
    """SQLAlchemy model for user_data.genetic_data table"""
    __tablename__ = "genetic_data"
    __table_args__ = {"schema": "user_data"}

    data_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id = Column(UUID(as_uuid=True), ForeignKey("user_data.patients.patient_id"), nullable=True)
    file_type = Column(String(20), nullable=False)
    file_path = Column(Text, nullable=False)
    is_supplementary = Column(Boolean, default=False)
    parent_data_id = Column(UUID(as_uuid=True), ForeignKey("user_data.genetic_data.data_id"), nullable=True)
    processed = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

# Job Monitoring SQLAlchemy Models with modern declarative style
class Job(Base):
    """SQLAlchemy model for job monitoring jobs table"""
    __tablename__ = "jobs"
    __table_args__ = {"schema": "job_monitoring"}
    
    # Primary key and foreign keys
    job_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id = Column(UUID(as_uuid=True), ForeignKey("user_data.patients.patient_id"), nullable=True)
    file_id = Column(UUID(as_uuid=True), ForeignKey("user_data.genetic_data.data_id"), nullable=True)

    # Status and progress fields
    status = Column(String(50), nullable=False)
    stage = Column(String(50), nullable=False)
    progress = Column(Integer, default=0)

    # Message fields
    message = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    
    # Metadata and timing fields
    job_metadata = Column(JSON, default=dict)
    started_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime(timezone=True), nullable=True)
    timeout_at = Column(DateTime(timezone=True), nullable=True)
    
    # Retry and creation fields
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    stages = relationship("JobStage", back_populates="job", cascade="all, delete-orphan")
    events = relationship("JobEvent", back_populates="job", cascade="all, delete-orphan")
    dependencies = relationship("JobDependency", back_populates="job", foreign_keys="[JobDependency.job_id]", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"Job(id={self.job_id}, status={self.status}, stage={self.stage}, progress={self.progress}%)"


class JobStage(Base):
    """SQLAlchemy model for job monitoring job_stages table"""
    __tablename__ = "job_stages"
    __table_args__ = {"schema": "job_monitoring"}
    
    # Primary key and foreign key
    stage_id = Column(Integer, primary_key=True)
    job_id = Column(UUID(as_uuid=True), ForeignKey("job_monitoring.jobs.job_id"), nullable=False)
    
    # Stage information
    stage = Column(String(50), nullable=False)
    status = Column(String(50), nullable=False)
    progress = Column(Integer, default=0)
    message = Column(Text, nullable=True)

    # Timing fields
    started_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime(timezone=True), nullable=True)
    duration_ms = Column(Integer, nullable=True)

    # Metadata and creation
    stage_metadata = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    job = relationship("Job", back_populates="stages")

    def __repr__(self) -> str:
        return f"JobStage(id={self.stage_id}, stage={self.stage}, status={self.status}, progress={self.progress}%)"


class JobEvent(Base):
    """SQLAlchemy model for job monitoring job_events table"""
    __tablename__ = "job_events"
    __table_args__ = {"schema": "job_monitoring"}
    
    # Primary key and foreign key
    event_id = Column(Integer, primary_key=True)
    job_id = Column(UUID(as_uuid=True), ForeignKey("job_monitoring.jobs.job_id"), nullable=False)

    # Event information
    event_type = Column(String(50), nullable=False)
    message = Column(Text, nullable=False)

    # Metadata and creation
    event_metadata = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    job = relationship("Job", back_populates="events")

    def __repr__(self) -> str:
        return f"JobEvent(id={self.event_id}, type={self.event_type}, message={self.message[:50]}...)"


class JobDependency(Base):
    """SQLAlchemy model for job monitoring job_dependencies table"""
    __tablename__ = "job_dependencies"
    __table_args__ = {"schema": "job_monitoring"}
    
    # Primary key and foreign keys
    dependency_id = Column(Integer, primary_key=True)
    job_id = Column(UUID(as_uuid=True), ForeignKey("job_monitoring.jobs.job_id"), nullable=False)
    depends_on_job_id = Column(UUID(as_uuid=True), ForeignKey("job_monitoring.jobs.job_id"), nullable=False)

    # Dependency information
    dependency_type = Column(String(50), default="sequential")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    job = relationship("Job", back_populates="dependencies", foreign_keys=[job_id])
    depends_on_job = relationship("Job", foreign_keys=[depends_on_job_id])

    def __repr__(self) -> str:
        return f"JobDependency(id={self.dependency_id}, job={self.job_id}, depends_on={self.depends_on_job_id})"


# ============================================================================
# NEW WORKFLOW MONITORING MODELS - Enhanced workflow tracking system
# ============================================================================

class Workflow(Base):
    """SQLAlchemy model for workflows table - Primary workflow orchestration"""
    __tablename__ = "workflows"

    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Basic workflow information
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    status = Column(Enum(WorkflowStatus, name='workflow_status_enum', values_callable=lambda obj: [e.value for e in obj]), nullable=False, default=WorkflowStatus.PENDING)
    created_by = Column(String, nullable=True)

    # Timing fields
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Progress tracking
    total_steps = Column(Integer, nullable=True)
    completed_steps = Column(Integer, nullable=True, default=0)

    # Metadata and relationships
    workflow_metadata = Column(JSON, default=dict)
    steps = relationship("WorkflowStep", back_populates="workflow", cascade="all, delete-orphan")
    logs = relationship("WorkflowLog", back_populates="workflow", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"Workflow(id={self.id}, name={self.name}, status={self.status})"


class WorkflowStep(Base):
    """SQLAlchemy model for workflow_steps table - Individual step tracking"""
    __tablename__ = "workflow_steps"

    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Foreign key to workflow
    workflow_id = Column(UUID(as_uuid=True), ForeignKey("workflows.id"), nullable=False)

    # Step information
    step_name = Column(String, nullable=False)
    step_order = Column(Integer, nullable=False)
    status = Column(Enum(StepStatus, name='step_status_enum', values_callable=lambda obj: [e.value for e in obj]), nullable=False, default=StepStatus.PENDING)
    container_name = Column(String, nullable=True)

    # Timing fields
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    duration_seconds = Column(Integer, nullable=True)

    # Data and error tracking
    output_data = Column(JSON, default=dict)
    error_details = Column(JSON, default=dict)
    retry_count = Column(Integer, default=0)

    # Relationships
    workflow = relationship("Workflow", back_populates="steps")

    def __repr__(self) -> str:
        return f"WorkflowStep(id={self.id}, step_name={self.step_name}, status={self.status})"


class WorkflowLog(Base):
    """SQLAlchemy model for workflow_logs table - Execution logs for debugging"""
    __tablename__ = "workflow_logs"

    # Primary key
    id = Column(Integer, primary_key=True)

    # Foreign key to workflow
    workflow_id = Column(UUID(as_uuid=True), ForeignKey("workflows.id"), nullable=False)

    # Log information
    step_name = Column(String, nullable=True)
    log_level = Column(Enum(LogLevel, name='log_level_enum', values_callable=lambda obj: [e.value for e in obj]), nullable=False, default=LogLevel.INFO)
    message = Column(Text, nullable=False)
    log_metadata = Column(JSON, default=dict)
    timestamp = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    workflow = relationship("Workflow", back_populates="logs")

    def __repr__(self) -> str:
        return f"WorkflowLog(id={self.id}, level={self.log_level}, message={self.message[:50]}...)"


# Dependency to get DB session using modern FastAPI pattern
def get_db():
    """Database session dependency for FastAPI"""
    db = SessionLocal()
    try:
        # Test the connection immediately
        db.execute(text("SELECT 1"))
        yield db
    except Exception as e:
        db.rollback()
        logger = logging.getLogger(__name__)
        logger.error(f"Database connection error: {e}")
        raise
    finally:
        db.close()

# Function to initialize database (tables should be created by migrations)
def init_db():
    # This is just to verify connection at startup
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    print("Database connection established successfully")

# Utility to check if a patient exists
def patient_exists(db, patient_id):
    result = db.execute(
        text("SELECT EXISTS(SELECT 1 FROM user_data.patients WHERE patient_id = :patient_id)"),
        {"patient_id": patient_id}
    )
    return result.scalar()

# Function to create a new patient or get existing one
def create_patient(db, patient_identifier):
    import logging
    logger = logging.getLogger(__name__)
    
    logger.info(f"create_patient called with identifier: {patient_identifier}")
    
    # First check if patient exists
    existing = db.execute(
        text("SELECT patient_id FROM user_data.patients WHERE patient_identifier = :identifier"),
        {"identifier": patient_identifier}
    ).scalar()
    
    logger.info(f"Existing patient query result: {existing} (type: {type(existing)})")
    
    if existing:
        logger.info(f"Returning existing patient: {existing}")
        return str(existing)
        
    # If patient doesn't exist, create new one with explicit UUID generation
    logger.info(f"Creating new patient with identifier: {patient_identifier}")
    result = db.execute(
        text("INSERT INTO user_data.patients (patient_id, patient_identifier) VALUES (uuid_generate_v4(), :identifier) RETURNING patient_id"),
        {"identifier": patient_identifier}
    )
    patient_id = result.scalar()
    logger.info(f"INSERT result: {patient_id} (type: {type(patient_id)})")
    
    db.commit()
    
    # Ensure we return the UUID as a string
    if patient_id:
        logger.info(f"Returning new patient UUID: {patient_id}")
        return str(patient_id)
    
    logger.error("No patient_id returned from INSERT")
    return None

# Function to register genetic data for a patient
def register_genetic_data(db, patient_id, file_type, file_path, is_supplementary=False, parent_id=None):
    """
    Register genetic data file for a patient in the database.
    
    Args:
        db: Database session
        patient_id: UUID of the patient
        file_type: Type of file (VCF, BAM, etc.)
        file_path: Path to the genetic data file
        is_supplementary: Whether this is a supplementary file (e.g., original WGS alongside VCF)
        parent_id: UUID of the parent data record if this is supplementary
    
    Returns:
        UUID of the newly created genetic data record
    """
    import logging
    logger = logging.getLogger(__name__)
    
    logger.info(f"register_genetic_data called with patient_id: {patient_id} (type: {type(patient_id)})")
    logger.info(f"file_type: {file_type}, file_path: {file_path}")
    
    # Use explicit UUID generation for data_id
    result = db.execute(
        text("""
        INSERT INTO user_data.genetic_data 
        (data_id, patient_id, file_type, file_path, is_supplementary, parent_data_id) 
        VALUES (uuid_generate_v4(), :patient_id, :file_type, :file_path, :is_supplementary, :parent_id) 
        RETURNING data_id
        """),
        {
            "patient_id": patient_id,
            "file_type": file_type,
            "file_path": file_path,
            "is_supplementary": is_supplementary,
            "parent_id": parent_id
        }
    )
    data_id = result.scalar()
    logger.info(f"INSERT result: {data_id} (type: {type(data_id)})")
    
    db.commit()
    
    # Ensure we return the UUID as a string
    if data_id:
        logger.info(f"Returning new data UUID: {data_id}")
        return str(data_id)
    
    logger.error("No data_id returned from INSERT")
    return None

# Function to get CPIC guidelines for a gene-drug pair
def get_guidelines_for_gene_drug(db, gene, drug):
    result = db.execute(
        text("""
        SELECT guideline_id, gene, drug, allele_combination, recommendation, activity_score
        FROM cpic.guidelines
        WHERE gene = :gene AND drug = :drug
        """),
        {"gene": gene, "drug": drug}
    )
    return result.fetchall()

# Function to store patient allele calls
def store_patient_alleles(db, patient_id, gene_id, diplotype, phenotype, activity_score, 
                          confidence_score, calling_method):
    result = db.execute(
        text("""
        INSERT INTO user_data.patient_alleles 
        (patient_id, gene_id, diplotype, phenotype, activity_score, confidence_score, calling_method)
        VALUES (:patient_id, :gene_id, :diplotype, :phenotype, :activity_score, 
                :confidence_score, :calling_method)
        RETURNING patient_allele_id
        """),
        {
            "patient_id": patient_id,
            "gene_id": gene_id,
            "diplotype": diplotype,
            "phenotype": phenotype,
            "activity_score": activity_score,
            "confidence_score": confidence_score,
            "calling_method": calling_method
        }
    )
    allele_id = result.scalar()
    db.commit()
    return allele_id

# Function to register a generated report
def register_report(db, patient_id, report_type, report_path):
    result = db.execute(
        text("""
        INSERT INTO reports.patient_reports (patient_id, report_type, report_path)
        VALUES (:patient_id, :report_type, :report_path)
        RETURNING report_id
        """),
        {"patient_id": patient_id, "report_type": report_type, "report_path": report_path}
    )
    report_id = result.scalar()
    db.commit()
    return report_id 


# Store parsed header JSON into public.genomic_file_headers
def save_genomic_header(db, file_path: str, file_format: str, header_info: dict):
    """Persist normalized header JSON to genomic_file_headers and return UUID id."""
    try:
        result = db.execute(
            text(
                """
                INSERT INTO genomic_file_headers (file_path, file_format, header_info)
                VALUES (:file_path, :file_format, :header_info)
                RETURNING id
                """
            ),
            {
                "file_path": file_path,
                "file_format": file_format.upper(),
                "header_info": json.dumps(header_info, ensure_ascii=False),
            },
        )
        header_id = result.scalar()
        db.commit()
        return str(header_id) if header_id else None
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.error(f"Error saving genomic header: {e}")
        db.rollback()
        raise