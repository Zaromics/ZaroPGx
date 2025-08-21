import os
from typing import List, Optional
from sqlalchemy import create_engine, MetaData, text, String, Integer, DateTime, Text, JSON, ForeignKey, CheckConstraint, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, relationship
from dotenv import load_dotenv
from datetime import datetime, timezone
import uuid

# Load environment variables
load_dotenv()

# Get database connection parameters from environment variables
DB_USER = os.getenv("DB_USER", "cpic_user")
DB_PASSWORD = os.getenv("DB_PASSWORD", "cpic_password")
DB_HOST = os.getenv("DB_HOST", "db")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "cpic_db")

# Create database URL
DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# Create SQLAlchemy engine
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,  # Check connection before using
    pool_size=5,  # Number of connections to keep open
    max_overflow=10,  # Max additional connections when pool is full
)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create base class for declarative models using modern style
class Base(DeclarativeBase):
    pass

# Define all referenced tables to ensure foreign key resolution
class Patient(Base):
    """SQLAlchemy model for user_data.patients table"""
    __tablename__ = "patients"
    __table_args__ = {"schema": "user_data"}
    
    patient_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_identifier: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class GeneticData(Base):
    """SQLAlchemy model for user_data.genetic_data table"""
    __tablename__ = "genetic_data"
    __table_args__ = {"schema": "user_data"}
    
    data_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("user_data.patients.patient_id"), nullable=True)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    is_supplementary: Mapped[bool] = mapped_column(Boolean, default=False)
    parent_data_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("user_data.genetic_data.data_id"), nullable=True)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

# Job Monitoring SQLAlchemy Models with modern declarative style
class Job(Base):
    """SQLAlchemy model for job monitoring jobs table"""
    __tablename__ = "jobs"
    __table_args__ = {"schema": "job_monitoring"}
    
    # Primary key and foreign keys
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("user_data.patients.patient_id"), nullable=True)
    file_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("user_data.genetic_data.data_id"), nullable=True)
    
    # Status and progress fields
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    stage: Mapped[str] = mapped_column(String(50), nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    
    # Message fields
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Metadata and timing fields
    job_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    timeout_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Retry and creation fields
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    
    # Relationships
    stages: Mapped[List["JobStage"]] = relationship("JobStage", back_populates="job", cascade="all, delete-orphan")
    events: Mapped[List["JobEvent"]] = relationship("JobEvent", back_populates="job", cascade="all, delete-orphan")
    dependencies: Mapped[List["JobDependency"]] = relationship("JobDependency", back_populates="job", foreign_keys="[JobDependency.job_id]", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"Job(id={self.job_id}, status={self.status}, stage={self.stage}, progress={self.progress}%)"


class JobStage(Base):
    """SQLAlchemy model for job monitoring job_stages table"""
    __tablename__ = "job_stages"
    __table_args__ = {"schema": "job_monitoring"}
    
    # Primary key and foreign key
    stage_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("job_monitoring.jobs.job_id"), nullable=False)
    
    # Stage information
    stage: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Timing fields
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    
    # Metadata and creation
    stage_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    
    # Relationships
    job: Mapped["Job"] = relationship("Job", back_populates="stages")

    def __repr__(self) -> str:
        return f"JobStage(id={self.stage_id}, stage={self.stage}, status={self.status}, progress={self.progress}%)"


class JobEvent(Base):
    """SQLAlchemy model for job monitoring job_events table"""
    __tablename__ = "job_events"
    __table_args__ = {"schema": "job_monitoring"}
    
    # Primary key and foreign key
    event_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("job_monitoring.jobs.job_id"), nullable=False)
    
    # Event information
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    
    # Metadata and creation
    event_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    
    # Relationships
    job: Mapped["Job"] = relationship("Job", back_populates="events")

    def __repr__(self) -> str:
        return f"JobEvent(id={self.event_id}, type={self.event_type}, message={self.message[:50]}...)"


class JobDependency(Base):
    """SQLAlchemy model for job monitoring job_dependencies table"""
    __tablename__ = "job_dependencies"
    __table_args__ = {"schema": "job_monitoring"}
    
    # Primary key and foreign keys
    dependency_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("job_monitoring.jobs.job_id"), nullable=False)
    depends_on_job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("job_monitoring.jobs.job_id"), nullable=False)
    
    # Dependency information
    dependency_type: Mapped[str] = mapped_column(String(50), default="sequential")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    
    # Relationships
    job: Mapped["Job"] = relationship("Job", back_populates="dependencies", foreign_keys=[job_id])
    depends_on_job: Mapped["Job"] = relationship("Job", foreign_keys=[depends_on_job_id])

    def __repr__(self) -> str:
        return f"JobDependency(id={self.dependency_id}, job={self.job_id}, depends_on={self.depends_on_job_id})"


# Dependency to get DB session using modern FastAPI pattern
def get_db():
    """Database session dependency for FastAPI"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Function to initialize database (tables should be created by migrations)
def init_db():
    # This is just to verify connection at startup
    with engine.connect() as conn:
        conn.execute("SELECT 1")
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
        """
        SELECT guideline_id, gene, drug, allele_combination, recommendation, activity_score
        FROM cpic.guidelines
        WHERE gene = :gene AND drug = :drug
        """,
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