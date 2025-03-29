from fastapi import FastAPI, Depends, HTTPException, status, Request, File, UploadFile, Form, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse, StreamingResponse
from jose import JWTError, jwt
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, AsyncGenerator
import os
import shutil
from pathlib import Path
from dotenv import load_dotenv
import logging
import requests
import json
import aiohttp
import uuid
import tempfile
import subprocess
import time
import mimetypes
import zipfile
import asyncio
import threading
import re
from werkzeug.utils import secure_filename

from app.api.routes import upload_router, report_router
from app.api.models import Token, TokenData
from app.pharmcat_wrapper.pharmcat_client import call_pharmcat_service
from app.reports.generator import generate_pdf_report, create_interactive_html_report

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("app")

# Load environment variables
load_dotenv()

# Security configuration
SECRET_KEY = os.getenv("SECRET_KEY", "supersecretkey")  # In production, use env var
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Constants
GATK_SERVICE_URL = os.getenv("GATK_API_URL", "http://gatk:8000")
STARGAZER_SERVICE_URL = os.getenv("STARGAZER_API_URL", "http://stargazer:5000")
PHARMCAT_SERVICE_URL = os.getenv("PHARMCAT_SERVICE_URL", "http://pharmcat:8080/match")
TEMP_DIR = Path("/tmp")
DATA_DIR = Path("/data")
REPORTS_DIR = Path("/data/reports")
BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = BASE_DIR / "templates"

# Create directories if they don't exist
TEMP_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# Initialize templates
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

# Initialize FastAPI app
app = FastAPI(
    title="ZaroPGx - Pharmacogenomic Report Generator",
    description="API for processing genetic data and generating pharmacogenomic reports",
    version="0.1.0"
)

# OAuth2
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

# Set up static file serving for reports
app.mount("/reports", StaticFiles(directory="/data/reports"), name="reports")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify exact origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(upload_router.router)
app.include_router(report_router.router)

# JWT token functions
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        token_data = TokenData(username=username)
    except JWTError:
        raise credentials_exception
    # In a real app, get user from database
    if token_data.username != "testuser":  # Mock user validation
        raise credentials_exception
    return token_data.username

# Authentication endpoint
@app.post("/token", response_model=Token)
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends()):
    # In a real app, validate against database
    if form_data.username != "testuser" or form_data.password != "testpassword":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": form_data.username}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/api")
async def api_root():
    return {"message": "Welcome to ZaroPGx API", "docs": "/docs"}

# Make the health check endpoint simple and dependency-free
@app.get("/health")
async def health_check():
    logger.info("Health check called")
    return {"status": "healthy", "timestamp": str(datetime.utcnow())}

@app.get("/api/genome-download-status")
async def genome_download_status():
    """Proxy endpoint to get genome download status"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{os.getenv('GENOME_DOWNLOADER_API_URL', 'http://genome-downloader:5050')}/status",
                timeout=5.0
            ) as response:
                return await response.json()
    except Exception as e:
        logger.error(f"Error fetching genome download status: {str(e)}")
        return {
            "in_progress": False, 
            "completed": False, 
            "error": str(e),
            "genomes": {},
            "overall_progress": 0
        }

@app.post("/api/start-genome-download")
async def start_genome_download():
    """Proxy endpoint to start genome download"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{os.getenv('GENOME_DOWNLOADER_API_URL', 'http://genome-downloader:5050')}/start-download",
                timeout=5.0
            ) as response:
                return await response.json()
    except Exception as e:
        logger.error(f"Error starting genome download: {str(e)}")
        return {"status": "error", "error": str(e)}

def detect_file_type(file_path: str) -> str:
    """
    Detect the type of genomic file based on extension and file content.
    Returns one of 'vcf', 'bam', 'sam', 'cram', or 'unknown'
    """
    file_ext = os.path.splitext(file_path.lower())[1]
    
    # Check extensions first
    if file_ext in ['.vcf', '.vcf.gz']:
        return 'vcf'
    elif file_ext == '.bam':
        return 'bam'
    elif file_ext == '.sam':
        return 'sam'
    elif file_ext == '.cram':
        return 'cram'
    elif file_ext == '.zip':
        return 'zip'
    
    # For files without typical extensions, check file signature (magic bytes)
    try:
        with open(file_path, 'rb') as f:
            header = f.read(8)  # Read first 8 bytes
            
            # BAM files start with "BAM\1"
            if header.startswith(b'BAM\1'):
                return 'bam'
            
            # CRAM files start with "CRAM"
            if header.startswith(b'CRAM'):
                return 'cram'
            
            # ZIP files start with PK\x03\x04
            if header.startswith(b'PK\x03\x04'):
                return 'zip'
            
            # Check if it might be a text-based VCF or SAM
            f.seek(0)
            first_line = f.readline().decode('utf-8', errors='ignore')
            if first_line.startswith('##fileformat=VCF'):
                return 'vcf'
            elif first_line.startswith('@HD') or first_line.startswith('@SQ'):
                return 'sam'
    except Exception as e:
        logger.warning(f"Error detecting file type: {str(e)}")
    
    # Default to unknown if we couldn't determine
    return 'unknown'

def determine_sequencing_profile(file_type: str) -> str:
    """
    Determine the sequencing profile based on file type
    Returns 'illumina' (default), 'pacbio', 'nanopore', etc.
    """
    # For now, just return the default
    return "illumina"

def sanitize_filename(filename):
    """Sanitize the filename to remove potential security issues"""
    # Remove path information
    filename = os.path.basename(filename)
    
    # Replace potentially problematic characters
    filename = re.sub(r'[^\w\.\-]', '_', filename)
    
    # Ensure the filename isn't empty after sanitization
    if not filename:
        filename = "unnamed_file"
    
    return filename

def extract_zip_file(zip_path):
    """Extract contents of a zip file to a temporary directory"""
    extract_dir = tempfile.mkdtemp(dir=TEMP_DIR)
    
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        # Get the list of files
        file_list = zip_ref.namelist()
        
        # Extract all files
        zip_ref.extractall(extract_dir)
        
        # Find genomic files
        vcf_files = [f for f in file_list if f.lower().endswith(('.vcf', '.vcf.gz'))]
        bam_files = [f for f in file_list if f.lower().endswith('.bam')]
        sam_files = [f for f in file_list if f.lower().endswith('.sam')]
        cram_files = [f for f in file_list if f.lower().endswith('.cram')]
        
        # Prioritize file types: VCF > BAM > CRAM > SAM
        genomic_files = vcf_files or bam_files or cram_files or sam_files
        
        if genomic_files:
            # Return the path to the first genomic file found
            return os.path.join(extract_dir, genomic_files[0]), extract_dir
    
    return None, extract_dir

# Progress tracking for jobs
job_status = {}

def update_job_progress(job_id: str, stage: str, percent: int, message: str, 
                        complete: bool = False, success: bool = False, data: Dict = None):
    """Update the status of a processing job"""
    job_status[job_id] = {
        "job_id": job_id,
        "stage": stage,
        "percent": percent,
        "message": message,
        "complete": complete,
        "success": success,
        "timestamp": datetime.utcnow().isoformat(),
        "data": data or {}
    }
    logger.info(f"Job {job_id} progress: {stage} - {percent}% - {message}")

def call_gatk_variants(job_id, file_path, reference_genome="hg38"):
    """Call variants using GATK through direct Docker command instead of HTTP API."""
    try:
        logger.info(f"Job {job_id} progress: variant_calling - 10% - Calling variants with GATK")
        
        # Define the output VCF path
        output_vcf = os.path.join(os.path.dirname(file_path), f"{os.path.splitext(os.path.basename(file_path))[0]}_gatk.vcf")
        
        # Map reference genome to path
        reference_paths = {
            'hg19': "/gatk/reference/hg19/ucsc.hg19.fasta",
            'hg38': "/gatk/reference/hg38/Homo_sapiens_assembly38.fasta",
            'grch37': "/gatk/reference/grch37/human_g1k_v37.fasta",
            'grch38': "/gatk/reference/hg38/Homo_sapiens_assembly38.fasta"  # symlink
        }
        
        reference_path = reference_paths.get(reference_genome)
        if not reference_path:
            raise ValueError(f"Unsupported reference genome: {reference_genome}")
        
        # Execute GATK using docker exec command
        cmd = f"docker exec pgx_gatk gatk HaplotypeCaller -R {reference_path} -I {file_path} -O {output_vcf}"
        logger.info(f"Running GATK command: {cmd}")
        process = subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True)
        
        return output_vcf
    except subprocess.CalledProcessError as e:
        error_msg = f"GATK command failed: {e.stderr}"
        logger.error(error_msg)
        raise Exception(error_msg)
    except Exception as e:
        error_msg = f"Error calling GATK variants: {str(e)}"
        logger.error(error_msg)
        raise Exception(error_msg)

def process_file_in_background(job_id, file_path, file_type, sample_id, reference_genome, has_index=False):
    """
    Process the genomic file through the pipeline:
    1. Call variants with GATK (if not a VCF)
    2. Call CYP2D6 star alleles with Stargazer
    3. Call PharmCAT for overall PGx annotation
    4. Generate a report
    """
    try:
        # Start job tracking
        update_job_progress(job_id, "initializing", 0, "Starting analysis pipeline")
        
        # Create a directory for this job
        job_dir = os.path.join(TEMP_DIR, job_id)
        os.makedirs(job_dir, exist_ok=True)
        
        vcf_path = file_path
        
        # Step 1: Process with GATK if not already a VCF
        if file_type != 'vcf':
            update_job_progress(job_id, "variant_calling", 10, "Calling variants with GATK")
            
            try:
                # Use direct Docker command instead of HTTP API
                vcf_path = call_gatk_variants(job_id, file_path, reference_genome)
                update_job_progress(job_id, "variant_calling", 30, "Variant calling completed")
                
            except Exception as e:
                logger.error(f"GATK service error: {str(e)}")
                update_job_progress(
                    job_id, "error", 100, f"GATK service error: {str(e)}", 
                    complete=True, success=False
                )
                return
        else:
            # Already a VCF, just update progress
            update_job_progress(job_id, "variant_calling", 30, "Using provided VCF file")
        
        # Step 2: Process CYP2D6 with Stargazer
        update_job_progress(job_id, "star_allele_calling", 40, "Calling CYP2D6 star alleles with Stargazer")
        
        cyp2d6_results = {}
        try:
            files = {'file': open(vcf_path, 'rb')}
            data = {
                'gene': 'CYP2D6',
                'reference_genome': reference_genome
            }
            
            response = requests.post(
                f"{STARGAZER_SERVICE_URL}/genotype",
                files=files,
                data=data
            )
            response.raise_for_status()
            
            cyp2d6_results = response.json()
            
            # Save CYP2D6 results
            with open(os.path.join(job_dir, f"{job_id}_cyp2d6.json"), 'w') as f:
                json.dump(cyp2d6_results, f, indent=2)
            
            update_job_progress(
                job_id, "star_allele_calling", 60, 
                f"CYP2D6 calling completed: {cyp2d6_results.get('diplotype', 'Unknown')}"
            )
            
        except requests.RequestException as e:
            logger.error(f"Stargazer service error: {str(e)}")
            # Continue even if Stargazer fails
            update_job_progress(
                job_id, "star_allele_calling", 60, 
                f"CYP2D6 calling error: {str(e)}"
            )
        
        # Step 3: Process with PharmCAT
        update_job_progress(job_id, "pharmcat", 70, "Running PharmCAT analysis")
        
        pharmcat_results = None
        try:
            # Call PharmCAT through our wrapper
            pharmcat_result_path = os.path.join(job_dir, f"{job_id}_pharmcat_results.json")
            
            # Use the pharmcat_client module to call PharmCAT
            result = call_pharmcat_service(
                vcf_path=vcf_path,
                output_json=pharmcat_result_path,
                sample_id=sample_id or job_id
            )
            
            if result.get('success'):
                # Load the PharmCAT results
                with open(pharmcat_result_path, 'r') as f:
                    pharmcat_results = json.load(f)
                
                update_job_progress(
                    job_id, "pharmcat", 80, 
                    f"PharmCAT analysis completed with {len(pharmcat_results.get('drug_recommendations', []))} recommendations"
                )
            else:
                update_job_progress(
                    job_id, "pharmcat", 80, 
                    f"PharmCAT warning: {result.get('message', 'Unknown error')}"
                )
        
        except Exception as e:
            logger.error(f"PharmCAT error: {str(e)}")
            update_job_progress(
                job_id, "pharmcat", 80, 
                f"PharmCAT error: {str(e)}"
            )
        
        # Step 4: Generate the report
        update_job_progress(job_id, "report_generation", 90, "Generating report")
        
        # Merge results
        combined_results = {
            "job_id": job_id,
            "sample_id": sample_id or job_id,
            "cyp2d6": cyp2d6_results,
            "pharmcat": pharmcat_results,
            "processing_date": datetime.utcnow().isoformat()
        }
        
        # Save combined results
        combined_results_path = os.path.join(job_dir, f"{job_id}_combined_results.json")
        with open(combined_results_path, 'w') as f:
            json.dump(combined_results, f, indent=2)
        
        # Generate PDF report
        report_path = os.path.join(REPORTS_DIR, f"{job_id}_pgx_report.pdf")
        html_report_path = os.path.join(REPORTS_DIR, f"{job_id}_pgx_report.html")
        
        try:
            # Generate PDF
            generate_pdf_report(combined_results, report_path)
            
            # Generate interactive HTML
            create_interactive_html_report(combined_results, html_report_path)
            
            # Update progress with report URLs
            update_job_progress(
                job_id, "complete", 100, "Analysis complete",
                complete=True, success=True,
                data={
                    "pdf_report_url": f"/reports/{os.path.basename(report_path)}",
                    "html_report_url": f"/reports/{os.path.basename(html_report_path)}",
                    "results": combined_results
                }
            )
            
        except Exception as e:
            logger.error(f"Report generation error: {str(e)}")
            update_job_progress(
                job_id, "report_generation", 95,
                f"Report generation error: {str(e)}",
                complete=True, success=False
            )
    
    except Exception as e:
        logger.exception(f"Error in background processing: {str(e)}")
        update_job_progress(
            job_id, "error", 100,
            f"Processing error: {str(e)}",
            complete=True, success=False
        )
    finally:
        # Clean up temp files if needed
        pass

@app.post("/upload-vcf", response_class=JSONResponse)
async def upload_vcf(
    background_tasks: BackgroundTasks,
    genomicFile: UploadFile = File(...),
    sampleId: Optional[str] = Form(None),
    referenceGenome: str = Form("hg19")
):
    """
    Upload a genomic file for PGx analysis.
    
    Accepts:
    - VCF files (variant calls)
    - BAM/CRAM/SAM files (aligned reads)
    - ZIP files containing any of the above
    
    The file will be processed to extract PGx variants and generate a report.
    """
    # Validate reference genome
    valid_references = ["hg19", "hg38", "grch37", "grch38"]
    if referenceGenome not in valid_references:
        return JSONResponse(
            status_code=400,
            content={"detail": f"Invalid reference genome. Supported: {valid_references}"}
        )
    
    # Generate a unique job ID
    job_id = str(uuid.uuid4())
    
    # Sanitize the filename
    original_filename = sanitize_filename(genomicFile.filename)
    
    # Create a temporary file
    with tempfile.NamedTemporaryFile(delete=False, dir=TEMP_DIR, suffix=os.path.splitext(original_filename)[1]) as tmp:
        # Copy the uploaded file to the temporary file
        shutil.copyfileobj(genomicFile.file, tmp)
        tmp_path = tmp.name
    
    try:
        # Detect the file type
        file_type = detect_file_type(tmp_path)
        logger.info(f"Detected file type: {file_type}")
        
        extract_dir = None
        if file_type == 'zip':
            # Extract the zip file to get the genomic file
            extracted_file, extract_dir = extract_zip_file(tmp_path)
            if not extracted_file:
                return JSONResponse(
                    status_code=400,
                    content={"detail": "No valid genomic file found in the ZIP archive"}
                )
            
            # Update the file path and detect the actual genomic file type
            tmp_path = extracted_file
            file_type = detect_file_type(tmp_path)
        
        if file_type not in ['vcf', 'bam', 'sam', 'cram']:
            return JSONResponse(
                status_code=400,
                content={"detail": f"Unsupported file type: {file_type}. Supported: vcf, bam, sam, cram"}
            )
        
        # Initialize job status
        update_job_progress(job_id, "uploaded", 0, "File received, starting processing")
        
        # Start background processing
        background_tasks.add_task(
            process_file_in_background,
            job_id=job_id,
            file_path=tmp_path,
            file_type=file_type,
            sample_id=sampleId,
            reference_genome=referenceGenome
        )
        
        return JSONResponse(
            status_code=202,
            content={
                "job_id": job_id,
                "status": "processing",
                "message": "File uploaded successfully and queued for processing",
                "progress_url": f"/progress/{job_id}",
                "status_url": f"/job-status/{job_id}"
            }
        )
    
    except Exception as e:
        logger.exception(f"Error processing upload: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"detail": f"Error processing file: {str(e)}"}
        )

@app.get("/progress/{job_id}")
async def get_progress(job_id: str):
    """
    SSE endpoint to stream progress updates for a job
    """
    if job_id not in job_status:
        raise HTTPException(status_code=404, detail="Job not found")
    
    async def event_generator() -> AsyncGenerator[str, None]:
        last_status = None
        
        while True:
            current_status = job_status.get(job_id)
            
            # If status has changed, send an update
            if current_status != last_status:
                yield f"data: {json.dumps(current_status)}\n\n"
                last_status = current_status.copy() if current_status else None
            
            # If job is complete, stop streaming
            if current_status and current_status.get("complete", False):
                break
            
            # Wait a bit before checking again
            await asyncio.sleep(1)
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream"
    )

@app.get("/job-status/{job_id}")
async def get_job_status(job_id: str):
    """
    Get the current status of a job
    """
    if job_id not in job_status:
        raise HTTPException(status_code=404, detail="Job not found")
    
    return job_status[job_id]

async def handle_pgx_report(vcf_path, sample_id=None):
    """
    Process a VCF file through the PGx pipeline and return the report
    """
    # This would be similar to the background process but synchronous and returning the report paths
    # Implement as needed
    pass

@app.post("/api/variant-call")
async def call_variants(
    file: UploadFile = File(...),
    reference_genome: str = Form("hg38"),
    regions: Optional[str] = Form(None)
):
    """Call variants using GATK HaplotypeCaller via direct Docker command."""
    # Save uploaded file to a temporary location
    temp_dir = tempfile.mkdtemp(dir="./data")
    input_path = os.path.join(temp_dir, secure_filename(file.filename))
    
    with open(input_path, "wb") as temp_file:
        content = await file.read()
        temp_file.write(content)
    
    # Map reference genome to path
    reference_paths = {
        'hg19': os.path.join("/gatk/reference", 'hg19', 'ucsc.hg19.fasta'),
        'hg38': os.path.join("/gatk/reference", 'hg38', 'Homo_sapiens_assembly38.fasta'),
        'grch37': os.path.join("/gatk/reference", 'grch37', 'human_g1k_v37.fasta'),
        'grch38': os.path.join("/gatk/reference", 'hg38', 'Homo_sapiens_assembly38.fasta')  # symlink
    }
    
    if reference_genome not in reference_paths:
        return JSONResponse(
            status_code=400, 
            content={"error": f"Unsupported reference genome: {reference_genome}"}
        )
    
    reference_path = reference_paths[reference_genome]
    output_vcf = os.path.join(temp_dir, f"{os.path.splitext(os.path.basename(input_path))[0]}.vcf")
    
    # Define regions argument if provided
    regions_arg = f"-L {regions}" if regions else ""
    
    try:
        # Execute GATK using docker exec command
        cmd = f"docker exec pgx_gatk gatk HaplotypeCaller -R {reference_path} -I {input_path} -O {output_vcf} {regions_arg}"
        process = subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True)
        
        return JSONResponse(
            status_code=200,
            content={
                "message": "Variant calling complete",
                "output_file": output_vcf,
                "command": cmd
            }
        )
    except subprocess.CalledProcessError as e:
        logging.error(f"GATK command failed: {e.stderr}")
        return JSONResponse(
            status_code=500,
            content={
                "error": "Variant calling failed",
                "details": e.stderr
            }
        )
    except Exception as e:
        logging.error(f"Unexpected error: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )

# Additional endpoints would go here 