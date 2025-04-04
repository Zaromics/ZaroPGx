import os
from sqlalchemy import create_engine, MetaData, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

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

# Create base class for declarative models
Base = declarative_base()

# Define metadata for each schema
cpic_metadata = MetaData(schema="cpic")
user_data_metadata = MetaData(schema="user_data")
reports_metadata = MetaData(schema="reports")

# Dependency to get DB session
def get_db():
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
    # First check if patient exists
    existing = db.execute(
        text("SELECT patient_id FROM user_data.patients WHERE patient_identifier = :identifier"),
        {"identifier": patient_identifier}
    ).scalar()
    
    if existing:
        return existing
        
    # If patient doesn't exist, create new one
    result = db.execute(
        text("INSERT INTO user_data.patients (patient_identifier) VALUES (:identifier) RETURNING patient_id"),
        {"identifier": patient_identifier}
    )
    patient_id = result.scalar()
    db.commit()
    return patient_id

# Function to register genetic data for a patient
def register_genetic_data(db, patient_id, file_type, file_path, is_supplementary=False, parent_id=None):
    """
    Register genetic data file for a patient in the database.
    
    Args:
        db: Database session
        patient_id: ID of the patient
        file_type: Type of file (VCF, BAM, etc.)
        file_path: Path to the genetic data file
        is_supplementary: Whether this is a supplementary file (e.g., original WGS alongside VCF)
        parent_id: ID of the parent data record if this is supplementary
    
    Returns:
        ID of the newly created genetic data record
    """
    result = db.execute(
        text("""
        INSERT INTO user_data.genetic_data 
        (patient_id, file_type, file_path, is_supplementary, parent_data_id) 
        VALUES (:patient_id, :file_type, :file_path, :is_supplementary, :parent_id)
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
    db.commit()
    return data_id

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