from fastapi import FastAPI, Depends, HTTPException, status, Request, Response, BackgroundTasks, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse, StreamingResponse
from sse_starlette.sse import EventSourceResponse
from jose import JWTError, jwt
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, AsyncGenerator, Callable
from asyncio import Queue
from dotenv import load_dotenv
from sqlalchemy.orm import Session
import os
import re
import shutil
import json
import uuid
import asyncio
import aiohttp
import tempfile
import logging
from pathlib import Path
import zipfile
import time
import requests
from werkzeug.utils import secure_filename
import traceback
import httpx
from app.pharmcat_wrapper import pharmcat_client

from app.api.models import Token, TokenData
from app.pharmcat_wrapper.pharmcat_client import call_pharmcat_service, normalize_pharmcat_results
from app.reports.generator import generate_pdf_report, create_interactive_html_report, generate_report
from app.api.db import get_db
from app.api.utils.security import get_current_user, get_optional_user
from app.api.routes import upload_router, report_router

# Configure more detailed logging
log_level = os.getenv("LOG_LEVEL", "DEBUG").upper()
logging.basicConfig(
    level=getattr(logging, log_level),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)

# Set specific loggers to DEBUG level
for logger_name in ["app", "uvicorn", "fastapi", "aiohttp.client"]:
    logging.getLogger(logger_name).setLevel(logging.DEBUG)

logger = logging.getLogger("app")
logger.info(f"Starting app with log level: {log_level}")

# Add more aggressive console logging for debugging
print(f"=========== ZaroPGx STARTUP AT {datetime.utcnow()} ===========")
print(f"LOG LEVEL: {log_level}")
print(f"GATK SERVICE URL: {os.getenv('GATK_API_URL', 'http://gatk-api:5000')}")
print(f"PHARMCAT SERVICE URL: {os.getenv('PHARMCAT_SERVICE_URL', 'http://pharmcat:8080/match')}")
print(f"STARGAZER SERVICE URL: {os.getenv('STARGAZER_API_URL', 'http://stargazer:5000')}")

# Load environment variables
load_dotenv()

# Security configuration
SECRET_KEY = os.getenv("SECRET_KEY", "supersecretkey")  # In production, use env var
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Constants
GATK_SERVICE_URL = os.getenv("GATK_API_URL", "http://gatk-api:5000")
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

# Set up static file serving for application static assets
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

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

# Override and disable authentication in development mode
if os.getenv("ZAROPGX_DEV_MODE", "true").lower() == "true":
    # Print warning about development mode
    print("🔓 WARNING: RUNNING IN DEVELOPMENT MODE - AUTHENTICATION DISABLED 🔓")
    logger.warning("Running in development mode - authentication is disabled!")
    
    # Create a dummy authentication that never fails
    from fastapi.security import OAuth2
    from fastapi.openapi.models import OAuthFlows as OAuthFlowsModel
    from fastapi import Request
    from typing import Optional, Dict, List, Any
    
    class NoAuthOAuth2(OAuth2):
        def __init__(self, tokenUrl: str):
            flows = OAuthFlowsModel(password={"tokenUrl": tokenUrl, "scopes": {}})
            super().__init__(flows=flows, auto_error=False)
            
        async def __call__(self, request: Request) -> Optional[str]:
            return "test_dev_user"
            
    # Replace the original OAuth2 scheme
    from fastapi import security
    from app.api.utils.security import oauth2_scheme, get_current_user
    
    # Override the dependencies
    async def get_current_user_override(token: str = "dummy_token"):
        return "test_dev_user"
        
    # Apply overrides to all routers and endpoints
    for route in app.routes:
        if hasattr(route, "dependencies"):
            # Remove authentication dependencies
            new_dependencies = []
            for dep in route.dependencies:
                if dep.dependency != get_current_user:
                    new_dependencies.append(dep)
            route.dependencies = new_dependencies
            
    # Apply overrides to included routers
    for router in [upload_router.router, report_router.router]:
        router.dependencies = [d for d in router.dependencies if d.dependency != get_current_user]
        # Update route dependencies
        for route in router.routes:
            if hasattr(route, "dependencies"):
                route.dependencies = [d for d in route.dependencies if d.dependency != get_current_user]

# Add direct routes for status and reports
@app.get("/status/{file_id}")
async def get_status(file_id: str, db: Session = Depends(get_db), current_user: str = Depends(get_optional_user)):
    """Forward to upload_router status endpoint"""
    return await upload_router.get_upload_status(file_id, db)

@app.get("/reports/{file_id}")
async def get_reports(file_id: str, current_user: str = Depends(get_optional_user)):
    """Forward to upload_router reports endpoint"""
    return await upload_router.get_report_urls(file_id)

@app.get("/reports/{filename}")
async def serve_report(filename: str):
    """
    Serve report files from the reports directory.
    """
    try:
        # Define reports directory
        reports_dir = Path("/data/reports")
        reports_dir.mkdir(parents=True, exist_ok=True)
        
        # Check if the file exists
        report_path = reports_dir / filename
        
        if not report_path.exists():
            logger.warning(f"Requested report not found: {filename}")
            return JSONResponse(
                status_code=404,
                content={"detail": f"Report not found: {filename}"}
            )
        
        # Determine content type based on file extension
        content_type = "application/octet-stream"  # Default
        if filename.endswith(".pdf"):
            content_type = "application/pdf"
        elif filename.endswith(".html"):
            content_type = "text/html"
        elif filename.endswith(".json"):
            content_type = "application/json"
        
        # Serve the file
        logger.info(f"Serving report: {report_path}")
        return FileResponse(
            path=report_path,
            filename=filename,
            media_type=content_type
        )
    except Exception as e:
        logger.exception(f"Error serving report: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"detail": f"Error serving report: {str(e)}"}
        )

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
    if token_data.username != "test":  # Mock user validation
        raise credentials_exception
    return token_data.username

# Optional authentication for development mode
async def get_optional_user(token: Optional[str] = Depends(oauth2_scheme)):
    # For development, allow requests without authentication
    if os.getenv("ZAROPGX_DEV_MODE", "true").lower() == "true":
        return "test"  # Return a default user
    
    # If not in dev mode, use the normal authentication
    return await get_current_user(token)

# Modify the router dependencies for development mode
if os.getenv("ZAROPGX_DEV_MODE", "true").lower() == "true":
    # Override the router dependencies to use optional authentication
    logger.info("Running in development mode - authentication is optional")
    # Remove auth dependencies from the routers
    upload_router.router.dependencies = []
    report_router.router.dependencies = []
else:
    logger.info("Running in production mode - authentication is required")

# Authentication endpoint
@app.post("/token", response_model=Token)
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends()):
    # In a real app, validate against database
    if form_data.username != "test" or form_data.password != "test":
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
    
    logger.info(f"Extracting ZIP file: {zip_path} to {extract_dir}")
    print(f"[ZIP] Extracting {os.path.basename(zip_path)} to temporary directory")
    
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            # Get the list of files
            file_list = zip_ref.namelist()
            
            # Log the contents
            logger.info(f"ZIP file contains: {file_list}")
            print(f"[ZIP] Contains {len(file_list)} files")
            
            # Look for large files before extraction
            large_files = []
            for info in zip_ref.infolist():
                if info.file_size > 50_000_000:  # 50MB
                    large_files.append((info.filename, info.file_size))
                    logger.info(f"Large file in ZIP: {info.filename} ({info.file_size / 1_000_000:.2f} MB)")
                    print(f"[ZIP] Large file detected: {info.filename} ({info.file_size / 1_000_000:.2f} MB)")
            
            # Extract all files
            zip_ref.extractall(extract_dir)
            logger.info(f"Extraction completed to {extract_dir}")
            
            # Find genomic files, prioritizing larger files which are likely BAM files
            vcf_files = [f for f in file_list if f.lower().endswith(('.vcf', '.vcf.gz'))]
            bam_files = [f for f in file_list if f.lower().endswith('.bam')]
            sam_files = [f for f in file_list if f.lower().endswith('.sam')]
            cram_files = [f for f in file_list if f.lower().endswith('.cram')]
            
            # If we have multiple file types, prioritize based on size and type
            genomic_files = []
            
            # First check for large BAM files - these are likely the most important
            if bam_files:
                # Sort BAM files by size (largest first)
                bam_paths = [(f, os.path.getsize(os.path.join(extract_dir, f))) for f in bam_files if os.path.exists(os.path.join(extract_dir, f))]
                if bam_paths:
                    bam_paths.sort(key=lambda x: x[1], reverse=True)
                    largest_bam = bam_paths[0][0]
                    logger.info(f"Selected largest BAM file: {largest_bam} ({bam_paths[0][1] / 1_000_000:.2f} MB)")
                    print(f"[ZIP] Selected BAM file: {largest_bam}")
                    genomic_files.append(largest_bam)
            
            # Then check for VCF files
            if not genomic_files and vcf_files:
                # Sort VCF files by size (largest first)
                vcf_paths = [(f, os.path.getsize(os.path.join(extract_dir, f))) for f in vcf_files if os.path.exists(os.path.join(extract_dir, f))]
                if vcf_paths:
                    vcf_paths.sort(key=lambda x: x[1], reverse=True)
                    largest_vcf = vcf_paths[0][0]
                    logger.info(f"Selected largest VCF file: {largest_vcf} ({vcf_paths[0][1] / 1_000_000:.2f} MB)")
                    print(f"[ZIP] Selected VCF file: {largest_vcf}")
                    genomic_files.append(largest_vcf)
            
            # Fall back to other types if needed
            if not genomic_files:
                genomic_files = vcf_files or bam_files or cram_files or sam_files
                if genomic_files:
                    logger.info(f"Using first available genomic file: {genomic_files[0]}")
                    print(f"[ZIP] Using file: {genomic_files[0]}")
            
            if genomic_files:
                # Return the path to the first genomic file found
                return os.path.join(extract_dir, genomic_files[0]), extract_dir
            else:
                logger.warning(f"No recognized genomic files found in ZIP: {file_list}")
                print(f"[ZIP WARNING] No recognized genomic files found in archive")
    except zipfile.BadZipFile as e:
        logger.error(f"Bad ZIP file: {str(e)}")
        print(f"[ZIP ERROR] Invalid ZIP file: {str(e)}")
    except Exception as e:
        logger.error(f"Error extracting ZIP: {str(e)}")
        print(f"[ZIP ERROR] Extraction failed: {str(e)}")
    
    return None, extract_dir

# Progress tracking for jobs
job_status = {}

def update_job_progress(job_id: str, stage: str, percent: int, message: str, 
                        complete: bool = False, success: bool = False, data: Dict = None):
    """Update the status of a processing job"""
    # Map stage names to frontend-friendly names for display
    stage_map = {
        "initializing": "Upload",
        "uploaded": "Upload",
        "variant_calling": "GATK",
        "star_allele_calling": "Stargazer",
        "pharmcat": "PharmCAT",
        "report_generation": "Report",
        "complete": "Report",
        "error": "Error"
    }
    
    # Ensure stage is mapped correctly for frontend
    display_stage = stage_map.get(stage, stage)
    
    # Store the job status
    job_status[job_id] = {
        "job_id": job_id,
        "stage": display_stage,
        "percent": percent,
        "message": message,
        "complete": complete,
        "success": success,
        "timestamp": datetime.utcnow().isoformat(),
        "data": data or {}
    }
    logger.info(f"Job {job_id} progress: {stage} - {percent}% - {message}")
    print(f"[PROGRESS] Job {job_id}: {stage} - {percent}% - {message}")
    
    # If this is a completion status, log additional details
    if complete:
        status = "SUCCESS" if success else "FAILURE"
        logger.info(f"Job {job_id} complete: {status} - {message}")
        print(f"[COMPLETE] Job {job_id}: {status} - {message}")
        
    # If there's error, also log it as an error
    if not success and (complete or stage == "error"):
        logger.error(f"Job {job_id} error: {message}")
        print(f"[ERROR] Job {job_id}: {message}")

async def call_gatk_variants(job_id, bam_file_path, reference_genome="hg38"):
    """Call variants using the GATK API with retry, stream upload for large files, and better error handling"""
    if job_id not in job_status:
        logger.warning(f"Job {job_id} not found in job_status dictionary - job might have been deleted")
        return None
    
    # Import mutex for job status updates to ensure thread safety
    from threading import Lock
    job_status_lock = Lock()
    
    # Check if file exists and is readable
    if not os.path.exists(bam_file_path):
        logger.error(f"BAM file not found: {bam_file_path}")
        with job_status_lock:
            job_status[job_id]["progress"] = 100
            job_status[job_id]["success"] = False
            job_status[job_id]["complete"] = True
            job_status[job_id]["message"] = f"BAM file not found: {bam_file_path}"
        return None
    
    # Maximum number of retries for GATK API health check
    max_retries = 3
    retry_count = 0
    
    # First, verify that the GATK API is healthy before attempting to process
    gateway_url = os.getenv("GATK_API_URL", "http://gatk-api:5000")
    
    # Try to access the health endpoint to check if the service is available
    while retry_count < max_retries:
        try:
            logger.info(f"Checking GATK API health (attempt {retry_count + 1}/{max_retries})")
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{gateway_url}/health", timeout=10) as response:
                    if response.status == 200:
                        health_data = await response.json()
                        logger.info(f"GATK API is healthy: {health_data}")
                        break
                    else:
                        logger.warning(f"GATK API health check failed with status {response.status}")
                        retry_count += 1
                        if retry_count >= max_retries:
                            logger.error("Maximum retries reached for GATK API health check")
                            with job_status_lock:
                                job_status[job_id]["progress"] = 100
                                job_status[job_id]["success"] = False
                                job_status[job_id]["complete"] = True
                                job_status[job_id]["message"] = "GATK API service is not available"
                            return None
                        await asyncio.sleep(5)  # Wait before retry
        except Exception as e:
            logger.error(f"Error checking GATK API health: {str(e)}")
            retry_count += 1
            if retry_count >= max_retries:
                logger.error("Maximum retries reached for GATK API health check")
                with job_status_lock:
                    job_status[job_id]["progress"] = 100
                    job_status[job_id]["success"] = False
                    job_status[job_id]["complete"] = True
                    job_status[job_id]["message"] = f"Cannot connect to GATK API: {str(e)}"
                return None
            await asyncio.sleep(5)  # Wait before retry
    
    # Update job progress to inform user about the upload process
    with job_status_lock:
        job_status[job_id]["progress"] = 30
        job_status[job_id]["message"] = "Preparing to upload file to GATK service"
    
    # Get file size
    file_size = os.path.getsize(bam_file_path)
    file_size_gb = file_size / (1024 * 1024 * 1024)
    
    # Special handling for large files
    is_large_file = file_size_gb > 1.0  # 1 GB threshold
    
    if is_large_file:
        logger.info(f"Large file detected ({file_size_gb:.2f} GB), using streaming upload")
        with job_status_lock:
            job_status[job_id]["message"] = f"Preparing large BAM file ({file_size_gb:.2f} GB) for processing"
    
    # Prepare the file for upload
    try:
        # Use different upload approach based on file size
        if is_large_file:
            # For large files, use streaming upload with aiohttp
            url = f"{gateway_url}/variant-call"
            
            # Create form data with metadata
            form = aiohttp.FormData()
            form.add_field('job_id', job_id)
            form.add_field('reference_genome', reference_genome)
            
            # Add file with optimized chunk size for large files
            # Larger chunk size reduces the number of network roundtrips
            # but increases memory usage - set to 8MB for better performance
            chunk_size = 8 * 1024 * 1024  # 8MB chunks
            
            # Add the file using a helper to avoid loading all at once
            with open(bam_file_path, 'rb') as f:
                form.add_field('file', 
                              f, 
                              filename=os.path.basename(bam_file_path),
                              content_type='application/octet-stream')
                
                # Use aiohttp with increased timeout for large files
                # and set larger buffer sizes
                upload_timeout = max(300, int(file_size_gb * 60))  # At least 5 minutes, or 1 min per GB
                
                logger.info(f"Starting large file upload with timeout {upload_timeout}s, chunk size {chunk_size/1024/1024}MB")
                with job_status_lock:
                    job_status[job_id]["message"] = f"Uploading {file_size_gb:.2f} GB file to GATK service"
                
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=upload_timeout)) as session:
                    try:
                        async with session.post(url, data=form, chunked=True, read_bufsize=chunk_size) as response:
                            if response.status in (200, 202):
                                try:
                                    result = await response.json()
                                    logger.info(f"GATK API response for job {job_id}: {result}")
                                    
                                    gatk_job_id = result.get('job_id')
                                    if not gatk_job_id:
                                        logger.error(f"No job_id in GATK API response for job {job_id}")
                                        with job_status_lock:
                                            job_status[job_id]["message"] = "GATK API did not return a job ID"
                                            job_status[job_id]["success"] = False
                                            job_status[job_id]["complete"] = True
                                            job_status[job_id]["progress"] = 100
                                        return None
                                    
                                    # Check if the output file is provided immediately
                                    output_file = result.get('output_file')
                                    if output_file and response.status == 200:
                                        # Job was completed immediately
                                        with job_status_lock:
                                            job_status[job_id]["progress"] = 100
                                            job_status[job_id]["message"] = "GATK variant calling completed"
                                            job_status[job_id]["output_file"] = output_file
                                            job_status[job_id]["success"] = True
                                            job_status[job_id]["complete"] = True
                                        
                                        # Check if output file exists
                                        if not os.path.exists(output_file):
                                            logger.warning(f"GATK output file does not exist: {output_file}")
                                            with job_status_lock:
                                                job_status[job_id]["message"] = f"GATK output file not found: {output_file}"
                                                job_status[job_id]["success"] = False
                                                job_status[job_id]["complete"] = True
                                            return None
                                        
                                        return output_file
                                    
                                    # Otherwise, this is an async job - poll for status
                                    gatk_status_url = f"{gateway_url}/job/{gatk_job_id}"
                                    
                                    # Update progress to 40% after upload completion
                                    with job_status_lock:
                                        job_status[job_id]["progress"] = 40
                                        job_status[job_id]["message"] = "File uploaded, GATK processing started"
                                    
                                    max_poll_retries = 300  # 5 hours (300 * 60 seconds)
                                    poll_retry = 0
                                    
                                    # Poll for job completion with exponential backoff
                                    backoff_base = 5  # Start with 5 seconds
                                    backoff_cap = 60  # Maximum 60 seconds between polls
                                    
                                    while poll_retry < max_poll_retries:
                                        # Calculate backoff time (5, 10, 20, 40, 60, 60, ...)
                                        backoff_time = min(backoff_base * (2 ** min(poll_retry, 4)), backoff_cap)
                                        
                                        # Wait before polling
                                        await asyncio.sleep(backoff_time)
                                        
                                        try:
                                            async with session.get(gatk_status_url) as status_response:
                                                if status_response.status == 200:
                                                    status_data = await status_response.json()
                                                    logger.debug(f"GATK job status for {gatk_job_id}: {status_data}")
                                                    
                                                    # Update progress based on GATK's reported progress
                                                    gatk_progress = status_data.get('progress', 0)
                                                    gatk_status = status_data.get('status')
                                                    gatk_message = status_data.get('message', '')
                                                    
                                                    # Scale GATK progress to our 40-90% range
                                                    our_progress = 40 + int(gatk_progress * 0.5)  # 40-90%
                                                    
                                                    with job_status_lock:
                                                        job_status[job_id]["progress"] = our_progress
                                                        job_status[job_id]["message"] = gatk_message
                                                    
                                                    # Check if the job completed or had an error
                                                    if gatk_status == 'completed':
                                                        output_file = status_data.get('output_file')
                                                        if output_file:
                                                            with job_status_lock:
                                                                job_status[job_id]["progress"] = 100
                                                                job_status[job_id]["message"] = "GATK variant calling completed"
                                                                job_status[job_id]["output_file"] = output_file
                                                                job_status[job_id]["success"] = True
                                                                job_status[job_id]["complete"] = True
                                                            
                                                            # Check if output file exists
                                                            if not os.path.exists(output_file):
                                                                logger.warning(f"GATK output file does not exist: {output_file}")
                                                                with job_status_lock:
                                                                    job_status[job_id]["message"] = f"GATK output file not found: {output_file}"
                                                                    job_status[job_id]["success"] = False
                                                                    job_status[job_id]["complete"] = True
                                                                return None
                                                            
                                                            return output_file
                                                        else:
                                                            logger.error(f"GATK job completed but no output file provided for job {gatk_job_id}")
                                                            with job_status_lock:
                                                                job_status[job_id]["message"] = "GATK job completed but no output file was provided"
                                                                job_status[job_id]["success"] = False
                                                                job_status[job_id]["complete"] = True
                                                                job_status[job_id]["progress"] = 100
                                                            return None
                                                    elif gatk_status == 'error':
                                                        error_msg = status_data.get('error', gatk_message)
                                                        logger.error(f"GATK job error for {gatk_job_id}: {error_msg}")
                                                        with job_status_lock:
                                                            job_status[job_id]["message"] = f"GATK service error: {error_msg}"
                                                            job_status[job_id]["success"] = False
                                                            job_status[job_id]["complete"] = True
                                                            job_status[job_id]["progress"] = 100
                                                        return None
                                                else:
                                                    logger.warning(f"Failed to get GATK job status for {gatk_job_id}: HTTP {status_response.status}")
                                        except Exception as poll_error:
                                            logger.error(f"Error polling GATK job status for {gatk_job_id}: {str(poll_error)}")
                                        
                                        poll_retry += 1
                                    
                                    # If we get here, we've reached the maximum number of polling attempts
                                    logger.error(f"Maximum polling attempts reached for GATK job {gatk_job_id}")
                                    with job_status_lock:
                                        job_status[job_id]["message"] = "GATK job timed out - processing took too long"
                                        job_status[job_id]["success"] = False
                                        job_status[job_id]["complete"] = True
                                        job_status[job_id]["progress"] = 100
                                    
                                    return None
                                    
                                except Exception as parse_err:
                                    logger.exception(f"Error parsing GATK API response for job {job_id}: {str(parse_err)}")
                                    with job_status_lock:
                                        job_status[job_id]["message"] = f"Error processing GATK response: {str(parse_err)}"
                                        job_status[job_id]["success"] = False
                                        job_status[job_id]["complete"] = True
                                        job_status[job_id]["progress"] = 100
                                    return None
                            else:
                                error_text = await response.text()
                                logger.error(f"GATK API request failed with status {response.status}: {error_text}")
                                with job_status_lock:
                                    job_status[job_id]["message"] = f"GATK API request failed: HTTP {response.status}"
                                    job_status[job_id]["success"] = False
                                    job_status[job_id]["complete"] = True
                                    job_status[job_id]["progress"] = 100
                                return None
                    except Exception as upload_error:
                        logger.exception(f"Error uploading to GATK API for job {job_id}: {str(upload_error)}")
                        with job_status_lock:
                            job_status[job_id]["message"] = f"Error uploading to GATK API: {str(upload_error)}"
                            job_status[job_id]["success"] = False
                            job_status[job_id]["complete"] = True
                            job_status[job_id]["progress"] = 100
                        return None
    except Exception as e:
        logger.exception(f"Error in call_gatk_variants: {str(e)}")
        with job_status_lock:
            job_status[job_id]["message"] = f"Error in call_gatk_variants: {str(e)}"
            job_status[job_id]["success"] = False
            job_status[job_id]["complete"] = True
            job_status[job_id]["progress"] = 100
        return None

async def process_file_in_background(job_id, file_path, file_type, sample_id, reference_genome, has_index=False):
    """
    Process the genomic file through the pipeline:
    1. Call variants with GATK (if not a VCF)
    2. Call CYP2D6 star alleles with Stargazer
    3. Call PharmCAT for overall PGx annotation
    4. Generate a report
    """
    try:
        logger.info(f"Starting background processing for job {job_id}")
        print(f"[PROCESSING] Starting job {job_id} for file: {file_path}")
        
        # Check if job status exists - create if missing
        if job_id not in job_status:
            logger.warning(f"Job {job_id} missing from job_status at start of background processing - recreating")
            print(f"[PROCESSING WARNING] Job {job_id} status missing - creating new entry")
            update_job_progress(job_id, "initializing", 0, "Starting analysis pipeline")
        
        # Register job in a global tracking variable for debugging
        process_tracking = {}
        process_tracking[job_id] = {
            "start_time": time.time(),
            "file_path": file_path,
            "file_type": file_type,
            "sample_id": sample_id,
            "reference_genome": reference_genome,
            "status": "initializing"
        }
        
        # Create a directory for this job
        job_dir = os.path.join(TEMP_DIR, job_id)
        os.makedirs(job_dir, exist_ok=True)
        
        vcf_path = file_path
        
        # Step 1: Process with GATK if not already a VCF
        if file_type != 'vcf':
            # Verify job is still tracked
            if job_id not in job_status:
                logger.warning(f"Job {job_id} missing before GATK step - recreating status")
                print(f"[PROCESSING WARNING] Job {job_id} missing before GATK - recreating") 
                update_job_progress(job_id, "variant_calling", 10, "Calling variants with GATK")
                
            update_job_progress(job_id, "variant_calling", 10, "Calling variants with GATK")
            logger.info(f"Job {job_id}: Calling variants with GATK for {file_type} file")
            print(f"[GATK] Job {job_id}: Calling variants for {file_type} file")
            
            # Mark job status in tracking
            if job_id in process_tracking:
                process_tracking[job_id]["status"] = "gatk_calling"
            
            # Retry mechanism for GATK variant calling
            max_retries = 2
            retry_count = 0
            last_error = None
            
            while retry_count <= max_retries:
                try:
                    # If this is a retry, inform the user
                    if retry_count > 0:
                        retry_message = f"Retrying GATK variant calling (attempt {retry_count}/{max_retries})"
                        update_job_progress(job_id, "variant_calling", 10, retry_message)
                        logger.info(f"Job {job_id}: {retry_message}")
                        print(f"[GATK] Job {job_id}: {retry_message}")
                        await asyncio.sleep(2)  # Small delay before retry
                    
                    # Call the GATK variant calling function with await
                    vcf_path = await call_gatk_variants(job_id, file_path, reference_genome)
                    
                    # If the GATK call returned None, it means there was an error
                    if vcf_path is None:
                        raise Exception(f"GATK variant calling failed for job {job_id}")
                    
                    logger.info(f"Job {job_id}: GATK variant calling completed, output at {vcf_path}")
                    print(f"[GATK] Job {job_id}: Variant calling completed")
                    
                    # Update tracking info
                    if job_id in process_tracking:
                        process_tracking[job_id]["gatk_complete"] = True
                        process_tracking[job_id]["vcf_path"] = vcf_path
                        process_tracking[job_id]["status"] = "gatk_complete"
                    
                    # Verify job is still tracked after GATK
                    if job_id not in job_status:
                        logger.warning(f"Job {job_id} missing after GATK step - recreating status")
                        print(f"[PROCESSING WARNING] Job {job_id} missing after GATK - recreating")
                        
                    update_job_progress(job_id, "variant_calling", 30, "Variant calling completed")
                    
                    # If successful, break out of retry loop
                    break
                    
                except Exception as e:
                    last_error = e
                    retry_count += 1
                    logger.error(f"Job {job_id}: GATK service error (attempt {retry_count}/{max_retries}): {str(e)}")
                    print(f"[GATK ERROR] Job {job_id}: Error in attempt {retry_count}/{max_retries}: {str(e)}")
                    
                    # Make sure job status is updated even during errors
                    if job_id not in job_status:
                        logger.warning(f"Job {job_id} missing during GATK error - recreating status")
                        print(f"[PROCESSING WARNING] Job {job_id} missing during error - recreating")
                        update_job_progress(job_id, "variant_calling", 10, f"GATK error: {str(e)}")
                    
                    # If we've reached max retries, fail the job
                    if retry_count > max_retries:
                        logger.error(f"Job {job_id}: Maximum retries exceeded for GATK variant calling")
                        print(f"[GATK ERROR] Job {job_id}: Maximum retries exceeded")
                        
                        # Update tracking info
                        if job_id in process_tracking:
                            process_tracking[job_id]["status"] = "gatk_failed"
                            process_tracking[job_id]["error"] = str(last_error)
                        
                        update_job_progress(
                            job_id, "error", 100, f"GATK service error: {str(last_error) if last_error else 'Unknown error'}", 
                            complete=True, success=False
                        )
                        return
            
            # If we exited the loop without success
            if vcf_path is None:
                # Ensure job status is maintained
                update_job_progress(
                    job_id, "error", 100, f"GATK service error: {str(last_error) if last_error else 'Unknown error'}", 
                    complete=True, success=False
                )
                return
        else:
            # Already a VCF, just update progress
            logger.info(f"Job {job_id}: Using provided VCF file at {vcf_path}")
            print(f"[PROCESSING] Job {job_id}: Using provided VCF file")
            
            # Verify job is still tracked 
            if job_id not in job_status:
                logger.warning(f"Job {job_id} missing for VCF processing - recreating status")
                print(f"[PROCESSING WARNING] Job {job_id} missing for VCF - recreating")
                
            update_job_progress(job_id, "variant_calling", 30, "Using provided VCF file")
        
        # Verify VCF file exists and is not empty
        if not os.path.exists(vcf_path) or os.path.getsize(vcf_path) == 0:
            error_msg = f"VCF file missing or empty: {vcf_path}"
            logger.error(f"Job {job_id}: {error_msg}")
            print(f"[ERROR] Job {job_id}: {error_msg}")
            
            # Update tracking info
            if job_id in process_tracking:
                process_tracking[job_id]["status"] = "vcf_missing"
                process_tracking[job_id]["error"] = error_msg
                
            update_job_progress(
                job_id, "error", 100, error_msg,
                complete=True, success=False
            )
            return
            
        # Verify job is still being tracked before Stargazer step
        if job_id not in job_status:
            logger.warning(f"Job {job_id} missing before Stargazer - recreating status")
            print(f"[PROCESSING WARNING] Job {job_id} missing before Stargazer - recreating")
            update_job_progress(job_id, "star_allele_calling", 35, "Preparing for star allele calling")
        
        # Step 2: Process CYP2D6 with Stargazer
        update_job_progress(job_id, "star_allele_calling", 40, "Calling CYP2D6 star alleles with Stargazer")
        logger.info(f"Job {job_id}: Calling CYP2D6 star alleles with Stargazer")
        print(f"[STARGAZER] Job {job_id}: Calling CYP2D6 star alleles")
        
        cyp2d6_results = {}
        try:
            files = {'file': open(vcf_path, 'rb')}
            data = {
                'gene': 'CYP2D6',
                'reference_genome': reference_genome
            }
            
            stargazer_url = f"{STARGAZER_SERVICE_URL}/genotype"
            logger.info(f"Job {job_id}: Calling Stargazer at {stargazer_url}")
            print(f"[STARGAZER] Job {job_id}: Sending request to {stargazer_url}")
            
            response = requests.post(
                stargazer_url,
                files=files,
                data=data
            )
            response.raise_for_status()
            
            cyp2d6_results = response.json()
            logger.info(f"Job {job_id}: Stargazer returned: {cyp2d6_results}")
            print(f"[STARGAZER] Job {job_id}: Results received: {cyp2d6_results.get('diplotype', 'Unknown')}")
            
            # Save CYP2D6 results
            cyp2d6_output_path = os.path.join(job_dir, f"{job_id}_cyp2d6.json")
            with open(cyp2d6_output_path, 'w') as f:
                json.dump(cyp2d6_results, f, indent=2)
            
            logger.info(f"Job {job_id}: Saved CYP2D6 results to {cyp2d6_output_path}")
            print(f"[STARGAZER] Job {job_id}: Saved results to {cyp2d6_output_path}")
            
            # Verify job tracking is maintained
            if job_id not in job_status:
                logger.warning(f"Job {job_id} missing after Stargazer - recreating status")
                print(f"[PROCESSING WARNING] Job {job_id} missing after Stargazer - recreating")
            
            update_job_progress(
                job_id, "star_allele_calling", 60, 
                f"CYP2D6 calling completed: {cyp2d6_results.get('diplotype', 'Unknown')}"
            )
            
        except requests.RequestException as e:
            logger.error(f"Job {job_id}: Stargazer service error: {str(e)}")
            print(f"[STARGAZER ERROR] Job {job_id}: {str(e)}")
            # Continue even if Stargazer fails
            
            # Verify job still exists
            if job_id not in job_status:
                logger.warning(f"Job {job_id} missing during Stargazer error - recreating status")
                print(f"[PROCESSING WARNING] Job {job_id} missing during Stargazer error - recreating")
                
            update_job_progress(
                job_id, "star_allele_calling", 60, 
                f"CYP2D6 calling error: {str(e)}"
            )
            
        # Check job status before PharmCAT
        if job_id not in job_status:
            logger.warning(f"Job {job_id} missing before PharmCAT - recreating status")
            print(f"[PROCESSING WARNING] Job {job_id} missing before PharmCAT - recreating")
            update_job_progress(job_id, "pharmcat", 65, "Preparing for PharmCAT analysis")
        
        # Step 3: Process with PharmCAT
        update_job_progress(job_id, "pharmcat", 70, "Running PharmCAT analysis")
        logger.info(f"Job {job_id}: Running PharmCAT analysis")
        print(f"[PHARMCAT] Job {job_id}: Starting PharmCAT analysis")
        
        pharmcat_results = None
        try:
            # Call PharmCAT through our wrapper
            pharmcat_result_path = os.path.join(job_dir, f"{job_id}_pharmcat_results.json")
            
            logger.info(f"Job {job_id}: Calling PharmCAT service with VCF path: {vcf_path}")
            print(f"[PHARMCAT] Job {job_id}: Calling service with file {os.path.basename(vcf_path)}")
            
            # Use the pharmcat_client module to call PharmCAT
            result = call_pharmcat_service(
                vcf_path=vcf_path,
                output_json=pharmcat_result_path,
                sample_id=sample_id or job_id
            )
            
            logger.info(f"Job {job_id}: PharmCAT service call result: {result}")
            print(f"[PHARMCAT] Job {job_id}: Result: {result}")
            
            if result.get('success'):
                # Load the PharmCAT results
                if os.path.exists(pharmcat_result_path):
                    with open(pharmcat_result_path, 'r') as f:
                        pharmcat_results = json.load(f)
                    
                    rec_count = len(pharmcat_results.get('drug_recommendations', []))
                    logger.info(f"Job {job_id}: PharmCAT analysis completed with {rec_count} recommendations")
                    print(f"[PHARMCAT] Job {job_id}: Analysis completed with {rec_count} recommendations")
                    
                    # Verify job tracking
                    if job_id not in job_status:
                        logger.warning(f"Job {job_id} missing after PharmCAT - recreating status")
                        print(f"[PROCESSING WARNING] Job {job_id} missing after PharmCAT - recreating")
                        
                    update_job_progress(
                        job_id, "pharmcat", 80, 
                        f"PharmCAT analysis completed with {rec_count} recommendations"
                    )
                else:
                    error_msg = f"PharmCAT results file not found at {pharmcat_result_path}"
                    logger.warning(f"Job {job_id}: {error_msg}")
                    print(f"[PHARMCAT WARNING] Job {job_id}: {error_msg}")
                    
                    # Verify job tracking
                    if job_id not in job_status:
                        logger.warning(f"Job {job_id} missing - recreating status")
                        print(f"[PROCESSING WARNING] Job {job_id} missing - recreating")
                        
                    update_job_progress(
                        job_id, "pharmcat", 80, error_msg
                    )
            else:
                error_msg = f"PharmCAT warning: {result.get('message', 'Unknown error')}"
                logger.warning(f"Job {job_id}: {error_msg}")
                print(f"[PHARMCAT WARNING] Job {job_id}: {error_msg}")
                
                # Verify job tracking
                if job_id not in job_status:
                    logger.warning(f"Job {job_id} missing - recreating status")
                    print(f"[PROCESSING WARNING] Job {job_id} missing - recreating")
                    
                update_job_progress(
                    job_id, "pharmcat", 80, error_msg
                )
        
        except Exception as e:
            error_msg = f"PharmCAT error: {str(e)}"
            logger.error(f"Job {job_id}: {error_msg}")
            print(f"[PHARMCAT ERROR] Job {job_id}: {error_msg}")
            
            # Verify job tracking
            if job_id not in job_status:
                logger.warning(f"Job {job_id} missing during PharmCAT error - recreating status")
                print(f"[PROCESSING WARNING] Job {job_id} missing during error - recreating")
                
            update_job_progress(
                job_id, "pharmcat", 80, error_msg
            )
            
        # Verify job tracking before report
        if job_id not in job_status:
            logger.warning(f"Job {job_id} missing before report generation - recreating status")
            print(f"[PROCESSING WARNING] Job {job_id} missing before report - recreating")
            update_job_progress(job_id, "report_generation", 85, "Preparing to generate report")
        
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
            # Extract data needed for report generation
            diplotypes = []
            recommendations = []
            
            # Extract diplotypes from PharmCAT results if available
            if pharmcat_results and 'data' in pharmcat_results and 'genes' in pharmcat_results['data']:
                diplotypes = pharmcat_results['data']['genes']
                logger.info(f"Extracted {len(diplotypes)} genes from PharmCAT results")
                
                # Log the first gene for debugging
                if len(diplotypes) > 0:
                    logger.info(f"First gene data: {json.dumps(diplotypes[0])}")
            else:
                logger.warning("No genes found in PharmCAT results structure")
                if pharmcat_results:
                    logger.warning(f"PharmCAT results keys: {list(pharmcat_results.keys())}")
                    if 'data' in pharmcat_results:
                        logger.warning(f"PharmCAT data keys: {list(pharmcat_results['data'].keys())}")
            
            # Extract recommendations from PharmCAT results if available
            if pharmcat_results and 'data' in pharmcat_results and 'drugRecommendations' in pharmcat_results['data']:
                recommendations = pharmcat_results['data']['drugRecommendations']
                logger.info(f"Extracted {len(recommendations)} drug recommendations from PharmCAT results")
                
                # Log the first recommendation for debugging
                if len(recommendations) > 0:
                    logger.info(f"First recommendation data: {json.dumps(recommendations[0])}")
            else:
                logger.warning("No drug recommendations found in PharmCAT results structure")
            
            # Add CYP2D6 results if available
            if cyp2d6_results and 'diplotype' in cyp2d6_results:
                cyp2d6_diplotype = {
                    "gene": "CYP2D6",
                    "diplotype": cyp2d6_results.get('diplotype', "*1/*1"),
                    "phenotype": cyp2d6_results.get('phenotype', "Unknown"),
                    "activity_score": cyp2d6_results.get('activity_score', 0)
                }
                diplotypes.append(cyp2d6_diplotype)
                logger.info(f"Added CYP2D6 data: {json.dumps(cyp2d6_diplotype)}")
            
            # Ensure we have some data for reports even if extraction failed
            if len(diplotypes) == 0:
                logger.warning(f"No diplotypes found for report. Using empty list. Raw data structure: {json.dumps(pharmcat_results)[:500]}")
                # No longer using dummy data - will proceed with empty list
            
            # Ensure we have recommendations for reports even if extraction failed
            if len(recommendations) == 0:
                logger.warning("No recommendations found for report. Using empty list.")
                # No longer using dummy data - will proceed with empty list
            
            # Generate PDF
            generate_pdf_report(
                patient_id=sample_id or job_id,
                report_id=job_id,
                diplotypes=diplotypes,
                recommendations=recommendations,
                report_path=report_path
            )
            
            # Generate interactive HTML
            create_interactive_html_report(
                patient_id=sample_id or job_id,
                report_id=job_id,
                diplotypes=diplotypes,
                recommendations=recommendations,
                output_path=html_report_path
            )
            
            # Final check for job tracking 
            if job_id not in job_status:
                logger.warning(f"Job {job_id} missing before completion - recreating status")
                print(f"[PROCESSING WARNING] Job {job_id} missing before completion - recreating")
            
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
            
            # Final check for job tracking
            if job_id not in job_status:
                logger.warning(f"Job {job_id} missing during report error - recreating status")
                print(f"[PROCESSING WARNING] Job {job_id} missing during error - recreating")
                
            update_job_progress(
                job_id, "report_generation", 95,
                f"Report generation error: {str(e)}",
                complete=True, success=False
            )
    
    except Exception as e:
        logger.exception(f"Error in background processing: {str(e)}")
        
        # Update tracking info
        if 'process_tracking' in locals() and job_id in process_tracking:
            process_tracking[job_id]["status"] = "failed"
            process_tracking[job_id]["error"] = str(e)
            process_tracking[job_id]["end_time"] = time.time()
            if "start_time" in process_tracking[job_id]:
                process_tracking[job_id]["duration"] = process_tracking[job_id]["end_time"] - process_tracking[job_id]["start_time"]
        
        # Final check for job tracking
        if job_id not in job_status:
            logger.warning(f"Job {job_id} missing during final error - recreating status")
            print(f"[PROCESSING WARNING] Job {job_id} missing during final error - recreating")
            
        update_job_progress(
            job_id, "error", 100,
            f"Processing error: {str(e)}",
            complete=True, success=False
        )
    finally:
        # Record processing completion
        if 'process_tracking' in locals() and job_id in process_tracking:
            process_tracking[job_id]["end_time"] = time.time()
            process_tracking[job_id]["duration"] = process_tracking[job_id]["end_time"] - process_tracking[job_id]["start_time"]
            logger.info(f"Job {job_id} total processing time: {process_tracking[job_id]['duration']:.2f} seconds")
            print(f"[PROCESSING] Job {job_id} completed in {process_tracking[job_id]['duration']:.2f} seconds")

@app.post("/upload-vcf", response_class=JSONResponse)
async def upload_vcf(
    background_tasks: BackgroundTasks,
    genomicFile: UploadFile = File(...),
    sampleId: Optional[str] = Form(None),
    referenceGenome: str = Form("hg19"),
    current_user: str = Depends(get_optional_user)
):
    """Upload a VCF file for analysis."""
    logger.info(f"Received file upload request: {genomicFile.filename}, Sample ID: {sampleId}, Reference: {referenceGenome}")
    print(f"[UPLOAD] Received file: {genomicFile.filename}, Sample ID: {sampleId}, Reference: {referenceGenome}")
    
    # Validate reference genome
    valid_references = ["hg19", "hg38", "grch37", "grch38"]
    if referenceGenome not in valid_references:
        logger.warning(f"Invalid reference genome: {referenceGenome}")
        print(f"[UPLOAD ERROR] Invalid reference genome: {referenceGenome}")
        return JSONResponse(
            status_code=400,
            content={"detail": f"Invalid reference genome. Supported: {valid_references}", "success": False}
        )
    
    # Generate a unique job ID
    job_id = str(uuid.uuid4())
    logger.info(f"Generated job ID: {job_id}")
    print(f"[UPLOAD] Generated job ID: {job_id}")
    
    # Sanitize the filename
    original_filename = sanitize_filename(genomicFile.filename)
    
    # Create a temporary file
    try:
        with tempfile.NamedTemporaryFile(delete=False, dir=TEMP_DIR, suffix=os.path.splitext(original_filename)[1]) as tmp:
            # Copy the uploaded file to the temporary file
            shutil.copyfileobj(genomicFile.file, tmp)
            tmp_path = tmp.name
        
        logger.info(f"Saved uploaded file to: {tmp_path}")
        print(f"[UPLOAD] Saved file to: {tmp_path}")
    except Exception as e:
        logger.error(f"Error saving uploaded file: {str(e)}")
        print(f"[UPLOAD ERROR] Error saving file: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"detail": f"Error saving uploaded file: {str(e)}", "success": False}
        )
    
    try:
        # Detect the file type
        file_type = detect_file_type(tmp_path)
        logger.info(f"Detected file type: {file_type}")
        print(f"[UPLOAD] Detected file type: {file_type}")
        
        extract_dir = None
        if file_type == 'zip':
            # Extract the zip file to get the genomic file
            extracted_file, extract_dir = extract_zip_file(tmp_path)
            if not extracted_file:
                logger.warning("No valid genomic file found in ZIP archive")
                print("[UPLOAD ERROR] No valid genomic file found in ZIP archive")
                return JSONResponse(
                    status_code=400,
                    content={"detail": "No valid genomic file found in the ZIP archive", "success": False}
                )
            
            # Update the file path and detect the actual genomic file type
            tmp_path = extracted_file
            file_type = detect_file_type(tmp_path)
            logger.info(f"Extracted file type: {file_type}, path: {tmp_path}")
            print(f"[UPLOAD] Extracted file type: {file_type}, path: {tmp_path}")
        
        if file_type not in ['vcf', 'bam', 'sam', 'cram']:
            logger.warning(f"Unsupported file type: {file_type}")
            print(f"[UPLOAD ERROR] Unsupported file type: {file_type}")
            return JSONResponse(
                status_code=400,
                content={"detail": f"Unsupported file type: {file_type}. Supported: vcf, bam, sam, cram", "success": False}
            )
        
        # Initialize job status
        update_job_progress(job_id, "uploaded", 0, "File received, starting processing")
        
        # Start background processing using asyncio task instead of background_tasks
        logger.info(f"Starting background processing for job: {job_id}")
        print(f"[UPLOAD] Starting background processing for job: {job_id}")
        
        # Create a new task that doesn't block the response
        asyncio.create_task(
            process_file_in_background(
                job_id=job_id,
                file_path=tmp_path,
                file_type=file_type,
                sample_id=sampleId,
                reference_genome=referenceGenome
            )
        )
        
        return JSONResponse(
            status_code=202,
            content={
                "job_id": job_id,
                "success": True,
                "status": "processing",
                "message": "File uploaded successfully and queued for processing",
                "progress_url": f"/progress/{job_id}",
                "status_url": f"/job-status/{job_id}"
            }
        )
    
    except Exception as e:
        logger.exception(f"Error processing upload: {str(e)}")
        print(f"[UPLOAD ERROR] Processing error: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"detail": f"Error processing file: {str(e)}", "success": False}
        )

@app.get("/progress/{job_id}")
async def get_progress(job_id: str, current_user: str = Depends(get_optional_user)):
    """Stream job progress as Server-Sent Events"""
    # Initialize response headers for SSE
    return StreamingResponse(
        event_generator(job_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Content-Type": "text/event-stream",
        }
    )

async def event_generator(job_id: str) -> AsyncGenerator[str, None]:
    """Separate generator function for progress events"""
    last_status = None
    keepalive_count = 0
    last_update_time = time.time()
    connection_start_time = time.time()
    max_connection_time = 30 * 60  # 30 minutes maximum connection time
    
    while True:
        # Check if connection has been open too long and should be recycled
        current_time = time.time()
        if current_time - connection_start_time > max_connection_time:
            logger.info(f"Progress stream for job {job_id} timed out after {max_connection_time} seconds")
            print(f"[PROGRESS] Connection for job {job_id} timed out after {max_connection_time/60:.1f} minutes")
            # Return a message asking client to reconnect
            yield f"data: {json.dumps({'reconnect': True, 'message': 'Connection timed out - please reconnect'})}\n\n"
            break
            
        # Get current status - using .get() with None default to handle race conditions  
        current_status = job_status.get(job_id)
        
        # Safety check in case the job was removed since we started
        if current_status is None:
            logger.warning(f"Job {job_id} status disappeared during streaming")
            print(f"[PROGRESS WARNING] Job {job_id} status gone during streaming - recreating")
            
            # If job status disappeared, recreate it with GATK status
            job_status[job_id] = {
                "job_id": job_id,
                "stage": "gatk",
                "percent": 20,
                "message": "Processing genomic data with GATK - connection reestablished",
                "complete": False,
                "success": None,
                "timestamp": datetime.utcnow().isoformat(),
                "reconnected": True
            }
            current_status = job_status[job_id]
        
        # If status has changed, send an update
        if current_status != last_status:
            # Send the updated status
            status_json = json.dumps(current_status)
            yield f"data: {status_json}\n\n"
            
            # Debug logging for status changes
            logger.debug(f"Sent updated status for job {job_id}: stage={current_status.get('stage')}, percent={current_status.get('percent')}")
            
            # Update last sent status and time
            last_status = current_status.copy()
            last_update_time = current_time
        
        # Determine the keepalive interval based on the stage
        # For GATK/variant_calling, use shorter intervals
        keepalive_interval = 2.0  # Default to 2 seconds
        
        # For BAM processing (GATK stage), send even more frequent updates 
        if current_status and (current_status.get("stage") == "gatk" or current_status.get("stage") == "variant_calling"):
            # Check if this is a BAM file processing (from the message)
            message = current_status.get("message", "").lower()
            if "bam" in message or "processing" in message:
                keepalive_interval = 1.0  # More frequent for BAM files (every 1 second)
            else:
                keepalive_interval = 2.0  # Standard for GATK
        
        # If no updates for the specified interval, send keepalive
        elif current_time - last_update_time > keepalive_interval:
            # Send a keepalive message during long-running operations
            keepalive_count += 1
            if current_status:
                # Clone and modify to avoid changing the original
                keepalive_status = current_status.copy() 
                keepalive_status["keepalive"] = True
                keepalive_status["keepalive_count"] = keepalive_count
                
                # For GATK stages, add special animation
                if current_status.get("stage") == "gatk" or current_status.get("stage") == "variant_calling":
                    message = keepalive_status.get("message", "")
                    if not message.endswith("..."):
                        keepalive_status["message"] = message + "..."
                    
                    # Every 5th keepalive, update the message to show progress animation
                    if keepalive_count % 5 == 0:
                        dots = "." * ((keepalive_count // 5) % 4 + 1)
                        base_message = message.rstrip('.')
                        keepalive_status["message"] = f"{base_message}{dots}"
                        
                    # Log occasional keepalives for debugging
                    if keepalive_count % 20 == 0:
                        logger.debug(f"Sent keepalive #{keepalive_count} for job {job_id}")
                
                yield f"data: {json.dumps(keepalive_status)}\n\n"
            else:
                # If status is unavailable, send a basic keepalive
                yield f"data: {json.dumps({'keepalive': True, 'job_id': job_id})}\n\n"
            last_update_time = current_time
        
        # If job is complete, send a final update and break the loop
        if current_status and current_status.get("complete", False):
            # Log completion
            logger.info(f"Job {job_id} complete, ending progress stream")
            print(f"[PROGRESS] Job {job_id} complete, ending progress stream")
            
            # Make sure we send the final status
            yield f"data: {json.dumps(current_status)}\n\n"
            break
        
        # Wait a bit before checking again - use shorter interval for more responsive updates
        await asyncio.sleep(0.25)  # Check 4 times per second

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
    """Call variants using GATK API service."""
    try:
        # Save the file to a temporary location to get its path
        temp_dir = tempfile.mkdtemp(dir="./data")
        input_path = os.path.join(temp_dir, secure_filename(file.filename))
        
        with open(input_path, "wb") as temp_file:
            content = await file.read()
            temp_file.write(content)
        
        # Prepare the multipart/form-data
        files = {'file': open(input_path, 'rb')}
        data = {
            'reference_genome': reference_genome
        }
        if regions:
            data['regions'] = regions
            
        # Call the GATK API service
        response = requests.post(
            f"{GATK_SERVICE_URL}/variant-call",
            files=files,
            data=data,
            timeout=3600  # Allow up to 1 hour for large files
        )
        response.raise_for_status()
        
        # Return the API response
        return JSONResponse(
            status_code=200,
            content=response.json()
        )
    except requests.RequestException as e:
        logging.error(f"GATK API error: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={
                "error": "Variant calling failed",
                "details": str(e)
            }
        )
    except Exception as e:
        logging.error(f"Unexpected error: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )
    finally:
        # Clean up the file (optional)
        try:
            if 'files' in locals() and 'file' in files:
                files['file'].close()
        except:
            pass

    reports_dir = os.path.join(os.path.dirname(__file__), "..", "reports")
    return FileResponse(os.path.join(reports_dir, filename))

    """Test endpoint for BAM file processing using GATK"""
    try:
        # Save the uploaded file
        temp_dir = tempfile.mkdtemp(dir=str(TEMP_DIR))
        file_path = os.path.join(temp_dir, secure_filename(file.filename))
        
        with open(file_path, "wb") as f:
            contents = await file.read()
            f.write(contents)
        
        # Log file details
        file_size = os.path.getsize(file_path)
        file_type = detect_file_type(file_path)
        
        logger.info(f"Test BAM processing: File saved to {file_path}, size: {file_size}, type: {file_type}")
        print(f"[TEST] BAM file saved: {file_path}, size: {file_size}, type: {file_type}")
        
        # Try to call GATK variants
        try:
            print(f"[TEST] Calling GATK with file {os.path.basename(file_path)}")
            # Special test job ID
            job_id = f"test_{uuid.uuid4()}"
            
            result = await call_gatk_variants(job_id, file_path, reference_genome)
            
            return {
                "success": True,
                "message": "BAM processing successful",
                "input_file": file_path,
                "output_file": result,
                "details": {
                    "input_size": file_size,
                    "input_type": file_type,
                    "output_size": os.path.getsize(result) if os.path.exists(result) else 0
                }
            }
        except Exception as e:
            logger.exception(f"Error in test BAM processing: {str(e)}")
            print(f"[TEST ERROR] GATK processing failed: {str(e)}")
            return {
                "success": False,
                "message": f"BAM processing failed: {str(e)}",
                "input_file": file_path,
                "error": str(e)
            }
    except Exception as e:
        logger.exception(f"Error setting up test BAM processing: {str(e)}")
        return {
            "success": False,
            "message": f"Error: {str(e)}"
        }

@app.get("/gatk-test", response_class=JSONResponse)
async def test_gatk_api():
    """Test the GATK API with a test job"""
    try:
        logger.info("Testing GATK API connectivity with test job")
        
        # Make a request to the GATK API test-job endpoint
        async with aiohttp.ClientSession() as session:
            gatk_api_url = os.getenv("GATK_API_URL", "http://gatk-api:5000")
            test_url = f"{gatk_api_url}/test-job"
            
            logger.info(f"Sending request to GATK API test endpoint: {test_url}")
            async with session.get(test_url) as response:
                if response.status == 200 or response.status == 202:
                    response_data = await response.json()
                    job_id = response_data.get("job_id")
                    logger.info(f"GATK test job created successfully with ID: {job_id}")
                    
                    # Wait for a moment to let the job progress a bit
                    await asyncio.sleep(5)
                    
                    # Check job status
                    job_url = f"{gatk_api_url}/job/{job_id}"
                    logger.info(f"Checking job status at: {job_url}")
                    
                    async with session.get(job_url) as job_response:
                        if job_response.status == 200:
                            job_data = await job_response.json()
                            return {
                                "success": True,
                                "message": "GATK API test successful",
                                "test_job": response_data,
                                "job_status": job_data
                            }
                        else:
                            error_text = await job_response.text()
                            logger.error(f"Failed to get job status: {error_text}")
                            return {
                                "success": False,
                                "message": f"Could not get job status: HTTP {job_response.status}",
                                "error": error_text
                            }
                else:
                    error_text = await response.text()
                    logger.error(f"Failed to create test job: {error_text}")
                    return {
                        "success": False,
                        "message": f"Failed to create test job: HTTP {response.status}",
                        "error": error_text
                    }
    except Exception as e:
        logger.exception(f"Error testing GATK API: {str(e)}")
        return {
            "success": False,
            "message": f"Error testing GATK API: {str(e)}",
            "error": traceback.format_exc()
        }

@app.get("/api-status", response_class=JSONResponse)
async def api_status():
    """Endpoint to check all API services and list available routes"""
    try:
        # Get the router routes
        routes = []
        for route in app.routes:
            if hasattr(route, "methods") and hasattr(route, "path"):
                routes.append({
                    "path": route.path,
                    "methods": list(route.methods),
                    "name": route.name
                })
        
        # Check GATK API status
        gatk_status = {"available": False, "message": "Not checked"}
        try:
            gatk_api_url = os.getenv("GATK_API_URL", "http://gatk-api:5000")
            logger.info(f"Checking GATK API status at {gatk_api_url}/health")
            
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{gatk_api_url}/health", timeout=5) as response:
                    if response.status == 200:
                        gatk_data = await response.json()
                        gatk_status = {
                            "available": True,
                            "message": "Healthy",
                            "details": gatk_data
                        }
                    else:
                        gatk_status = {
                            "available": False,
                            "message": f"Unhealthy (Status: {response.status})",
                            "response": await response.text()
                        }
        except Exception as e:
            gatk_status = {
                "available": False,
                "message": f"Error connecting: {str(e)}"
            }
        
        # Try to connect directly to the test-job endpoint
        test_job_status = {"available": False, "message": "Not checked"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{gatk_api_url}/test-job", timeout=5) as response:
                    if response.status in (200, 202):
                        test_data = await response.json()
                        test_job_status = {
                            "available": True,
                            "message": "Test endpoint working",
                            "job_id": test_data.get("job_id")
                        }
                    else:
                        test_job_status = {
                            "available": False,
                            "message": f"Test endpoint failed (Status: {response.status})",
                            "response": await response.text()
                        }
        except Exception as e:
            test_job_status = {
                "available": False,
                "message": f"Error connecting to test-job: {str(e)}"
            }
            
        return {
            "timestamp": time.time(),
            "gatk_api": gatk_status,
            "test_job_endpoint": test_job_status,
            "routes": routes,
            "app_name": "ZaroPGx API",
            "version": "1.0.0"
        }
    except Exception as e:
        logger.exception(f"Error in api-status endpoint: {str(e)}")
        return {
            "error": str(e),
            "traceback": traceback.format_exc()
        }

@app.get("/services-status", response_class=JSONResponse)
async def services_status(current_user: str = Depends(get_optional_user)):
    """Check status of all connected services"""
    try:
        services = {
            "pharmcat": {
                "url": os.getenv("PHARMCAT_SERVICE_URL", "http://pharmcat:8080/match"),
                "timeout": 5
            },
            "gatk": {
                "url": os.getenv("GATK_API_URL", "http://gatk-api:5000/health"),
                "timeout": 5
            },
            "stargazer": {
                "url": os.getenv("STARGAZER_API_URL", "http://stargazer:5000/health"),
                "timeout": 5
            },
            "database": {
                "url": os.getenv("DATABASE_URL", "postgresql://cpic_user:cpic_password@db:5432/cpic_db"),
                "timeout": 3
            }
        }
        
        # Check each service
        unhealthy_services = {}
        
        # Use httpx for concurrent requests
        async with httpx.AsyncClient() as client:
            for service_name, service_info in services.items():
                if service_name == "database":
                    # Special handling for database check
                    try:
                        # Try to connect to the database
                        from sqlalchemy import create_engine, text
                        engine = create_engine(service_info["url"])
                        with engine.connect() as connection:
                            result = connection.execute(text("SELECT 1"))
                            if not result.fetchone():
                                unhealthy_services[service_name] = "Database connection test failed"
                    except Exception as e:
                        unhealthy_services[service_name] = f"Database error: {str(e)}"
                else:
                    # HTTP services
                    try:
                        response = await client.get(
                            service_info["url"],
                            timeout=service_info["timeout"]
                        )
                        if response.status_code != 200:
                            unhealthy_services[service_name] = f"HTTP {response.status_code}"
                    except Exception as e:
                        unhealthy_services[service_name] = str(e)
        
        # Return status
        if unhealthy_services:
            return {
                "status": "error",
                "message": "Some services are unavailable",
                "unhealthy_services": unhealthy_services
            }
        else:
            return {
                "status": "ok",
                "message": "All services are available"
            }
    except Exception as e:
        logger.error(f"Error checking services status: {str(e)}")
        return {
            "status": "error",
            "message": f"Error checking services: {str(e)}"
        }

# Wait for services to be ready
@app.on_event("startup")
async def startup_event():
    """Check if required services are ready before starting the app"""
    print("=================== STARTING ZaroPGx ===================")
    logger.info("Starting ZaroPGx application")
    
    # Services to check
    services = {
        "GATK API": f"{GATK_SERVICE_URL}/health",
        "PharmCAT Wrapper": f"{os.getenv('PHARMCAT_API_URL', 'http://pharmcat-wrapper:5000')}/health",
        "Stargazer": f"{STARGAZER_SERVICE_URL}/health"
    }
    
    max_retries = 12  # Increased from 6 to 12
    retry_delay = 5  # Reduced from 10 to 5 seconds
    
    for service_name, service_url in services.items():
        logger.info(f"Checking if {service_name} is ready at {service_url}...")
        print(f"Checking {service_name} at {service_url}")
        
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(service_url, timeout=5.0)
                    if response.status_code == 200:
                        logger.info(f"{service_name} is ready!")
                        print(f"✅ {service_name} is ready!")
                        break
                    else:
                        logger.warning(f"{service_name} returned status {response.status_code}")
                        print(f"⚠️ {service_name} returned status {response.status_code}")
            except Exception as e:
                logger.warning(f"{service_name} not ready yet (attempt {attempt + 1}/{max_retries}): {str(e)}")
                print(f"⚠️ {service_name} not ready (attempt {attempt + 1}/{max_retries})")
            
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
            else:
                logger.warning(f"{service_name} health check failed after {max_retries} attempts, but we'll continue anyway")
                print(f"⚠️ {service_name} health check failed after {max_retries} attempts, continuing anyway")
    
    # Check temp and data directories
    for dir_path in [TEMP_DIR, DATA_DIR, REPORTS_DIR]:
        if not os.path.exists(dir_path):
            try:
                os.makedirs(dir_path, exist_ok=True)
                logger.info(f"Created directory: {dir_path}")
                print(f"Created directory: {dir_path}")
            except Exception as e:
                logger.error(f"Failed to create directory {dir_path}: {str(e)}")
                print(f"❌ Failed to create directory {dir_path}: {str(e)}")
        else:
            logger.info(f"Directory exists: {dir_path}")
            print(f"✅ Directory exists: {dir_path}")
    
    # Check environment variables
    required_vars = ["SECRET_KEY"]
    for var in required_vars:
        if not os.getenv(var):
            logger.warning(f"Environment variable {var} is not set!")
            print(f"⚠️ Environment variable {var} is not set!")
    
    print("=================== STARTUP COMPLETE ===================")
    logger.info("ZaroPGx startup complete")

# Add middleware to log all requests
@app.middleware("http")
async def log_requests(request: Request, call_next):
    print(f"[REQUEST] {request.method} {request.url.path}")
    logger.info(f"[REQUEST] {request.method} {request.url.path}")
    try:
        response = await call_next(request)
        print(f"[RESPONSE] {request.method} {request.url.path} - Status: {response.status_code}")
        logger.info(f"[RESPONSE] {request.method} {request.url.path} - Status: {response.status_code}")
        return response
    except Exception as e:
        print(f"[ERROR] {request.method} {request.url.path} - Error: {str(e)}")
        logger.exception(f"Error handling request {request.method} {request.url.path}")
        raise

# Additional endpoints would go here 

@app.get("/gatk-test-file-upload")
async def gatk_test(background_tasks: BackgroundTasks):
    """Test endpoint to verify GATK API integration with a sample file"""
    try:
        # Create a new unique job ID
        job_id = str(uuid.uuid4())
        
        # Initialize job status
        job_status[job_id] = {
            "job_id": job_id,
            "stage": "gatk",
            "percent": 5,
            "message": "Starting GATK test job",
            "complete": False,
            "success": False,
            "time": time.time()
        }
        
        # Log the test job
        logger.info(f"Starting GATK test job with ID: {job_id}")
        
        # Create a background task to process the test
        @background_tasks.add_task
        async def process_test():
            try:
                # Path to a sample BAM file in the test data folder
                sample_paths = [
                    "/app/test_data/NA12878.mini.bam",
                    "/data/test_data/sample1.bam",
                    "/data/test_data/NA12878_small.bam",
                    "/app/test_data/test.bam"
                ]
                
                # Find the first available sample file
                sample_file = None
                for path in sample_paths:
                    if os.path.exists(path):
                        sample_file = path
                        break
                
                if not sample_file:
                    # No sample file found, create a message about it
                    logger.error("No sample BAM file found for GATK test")
                    job_status[job_id]["message"] = "No sample BAM file found for testing"
                    job_status[job_id]["success"] = False
                    job_status[job_id]["complete"] = True
                    job_status[job_id]["percent"] = 100
                    return
                
                # Update status to show file was found
                logger.info(f"Using sample file for GATK test: {sample_file}")
                job_status[job_id]["message"] = f"Using sample file: {os.path.basename(sample_file)}"
                job_status[job_id]["percent"] = 10
                
                # Call GATK variant calling
                vcf_path = await call_gatk_variants(job_id, sample_file, reference_genome="hg38")
                
                if vcf_path and os.path.exists(vcf_path):
                    logger.info(f"GATK test completed successfully, VCF: {vcf_path}")
                    job_status[job_id]["message"] = f"GATK test successful, VCF file: {os.path.basename(vcf_path)}"
                    job_status[job_id]["success"] = True
                    job_status[job_id]["output_file"] = vcf_path
                else:
                    logger.error(f"GATK test failed, no VCF file generated")
                    job_status[job_id]["message"] = "GATK test failed, no VCF generated"
                    job_status[job_id]["success"] = False
                
                # Mark as complete
                job_status[job_id]["complete"] = True
                job_status[job_id]["percent"] = 100
                
            except Exception as e:
                logger.exception(f"Error in GATK test job {job_id}: {str(e)}")
                job_status[job_id]["message"] = f"Error in GATK test: {str(e)}"
                job_status[job_id]["success"] = False
                job_status[job_id]["complete"] = True
                job_status[job_id]["percent"] = 100
        
        # Return the job ID to allow status monitoring
        return {
            "job_id": job_id, 
            "message": "GATK test started",
            "status_url": f"/job-status/{job_id}"
        }
        
    except Exception as e:
        logger.exception(f"Error starting GATK test: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error starting GATK test: {str(e)}")

@app.get("/check-reports/{job_id}")
async def check_reports(job_id: str):
    """
    Check for reports and manually trigger completion notification
    """
    try:
        # Define reports directory
        reports_dir = Path("/data/reports")
        
        # Check for report files
        pdf_path = reports_dir / f"{job_id}_pgx_report.pdf"
        html_path = reports_dir / f"{job_id}_pgx_report.html"
        
        pdf_exists = pdf_path.exists()
        html_exists = html_path.exists()
        
        # Get job status if it exists
        from app.api.routes.upload_router import job_status
        job_data = job_status.get(job_id, {"status": "unknown", "complete": False})
        
        # CRITICAL: Update job status to ensure completion
        if pdf_exists or html_exists:
            # Ensure job status is updated
            job_status[job_id] = {
                "status": "completed",
                "percent": 100,
                "message": "Analysis completed successfully (from check-reports)",
                "stage": "Report",
                "complete": True,
                "success": True,
                "data": {
                    "job_id": job_id,
                    "pdf_report_url": f"/reports/{job_id}_pgx_report.pdf" if pdf_exists else None,
                    "html_report_url": f"/reports/{job_id}_pgx_report.html" if html_exists else None
                }
            }
            logger.info(f"Manually updated job status for job {job_id} to completed")
        
        return {
            "job_id": job_id,
            "reports": {
                "pdf_exists": pdf_exists,
                "pdf_path": str(pdf_path) if pdf_exists else None,
                "pdf_url": f"/reports/{job_id}_pgx_report.pdf" if pdf_exists else None,
                "html_exists": html_exists,
                "html_path": str(html_path) if html_exists else None,
                "html_url": f"/reports/{job_id}_pgx_report.html" if html_exists else None
            },
            "job_status": job_status.get(job_id),
            "instructions": "To check your report, click on the PDF or HTML URL link."
        }
    except Exception as e:
        logger.exception(f"Error checking reports: {str(e)}")
        return {"status": "error", "message": f"Error checking reports: {str(e)}"}

@app.get("/trigger-completion/{job_id}", response_class=HTMLResponse)
async def trigger_completion(job_id: str):
    """
    A troubleshooting endpoint to manually trigger completion flow and provide direct report links.
    This is a backup method when the SSE progress monitor fails to notify the frontend.
    """
    from app.api.routes.upload_router import job_status
    
    # Check if job exists
    if job_id not in job_status:
        job_status[job_id] = {"status": "unknown", "data": {}}
    
    # Check if reports exist
    pdf_path = f"/data/reports/{job_id}_pgx_report.pdf"
    html_path = f"/data/reports/{job_id}_pgx_report.html"
    
    pdf_exists = os.path.exists(pdf_path)
    html_exists = os.path.exists(html_path)
    
    # Create job status showing completion
    if pdf_exists or html_exists:
        # If reports exist, mark job as complete
        job_status[job_id] = {
            "status": "completed",
            "message": "Analysis completed successfully",
            "data": {
                "success": True,
                "pdf_report_url": f"/reports/{job_id}_pgx_report.pdf",
                "html_report_url": f"/reports/{job_id}_pgx_report.html"
            }
        }
        
        logger.info(f"Manual trigger for job {job_id} - Reports found and job status updated")
    else:
        logger.error(f"Manual trigger for job {job_id} - No reports found at expected locations")
    
    # Return an HTML page with direct links and troubleshooting help
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>PharmGx Report Manual Access</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/css/bootstrap.min.css" rel="stylesheet">
        <style>
            body {{ padding: 20px; }}
            .report-link {{ margin: 10px 0; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1 class="mb-4">PharmGx Report Manual Access</h1>
            
            <div class="card mb-4">
                <div class="card-header bg-primary text-white">
                    <h3>Job Status and Reports</h3>
                </div>
                <div class="card-body">
                    <p><strong>Job ID:</strong> {job_id}</p>
                    <p><strong>PDF Report:</strong> {"Available" if pdf_exists else "Not found"}</p>
                    <p><strong>HTML Report:</strong> {"Available" if html_exists else "Not found"}</p>
                    
                    <div class="report-link">
                        <h4>Direct Report Links:</h4>
                        {"<a href='/reports/" + job_id + "_pgx_report.pdf' class='btn btn-primary' target='_blank'>View PDF Report</a>" if pdf_exists else "<span class='text-danger'>PDF report not found</span>"}
                    </div>
                    
                    <div class="report-link">
                        {"<a href='/reports/" + job_id + "_pgx_report.html' class='btn btn-info' target='_blank'>View HTML Report</a>" if html_exists else "<span class='text-danger'>HTML report not found</span>"}
                    </div>
                </div>
            </div>
            
            <div class="card">
                <div class="card-header bg-info text-white">
                    <h3>Troubleshooting Information</h3>
                </div>
                <div class="card-body">
                    <p>If the main interface doesn't display your reports, you can use the links above to access them directly.</p>
                    <p>Job Status Information:</p>
                    <pre>{json.dumps(job_status.get(job_id, {}), indent=2)}</pre>
                    
                    <div class="mt-3">
                        <a href="/" class="btn btn-secondary">Return to Main Page</a>
                    </div>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    
    return HTMLResponse(content=html_content)

@app.post("/analyze")
async def analyze_genome(
    request: Request,
    background_tasks: BackgroundTasks,
    genome_file: UploadFile = File(...),
    patient_id: Optional[str] = Form(None),
    patient_name: Optional[str] = Form(None),
    patient_mrn: Optional[str] = Form(None)
):
    """
    Analyze a genome file using PharmCAT and return the results
    
    This endpoint accepts a genome file upload and patient information,
    then processes the file using PharmCAT in the background and
    returns Server-Sent Events with progress updates
    """
    try:
        # Generate a unique ID for this analysis
        analysis_id = str(uuid.uuid4())
        logger.info(f"Starting genome analysis {analysis_id} for patient {patient_id or 'unknown'}")
        
        # Create a queue for SSE messages
        client_queue = Queue()
        
        # Define a function to send SSE updates to client
        async def send_update(message: Dict[str, Any]):
            await client_queue.put(message)
        
        # Start the analysis in the background
        background_tasks.add_task(
            process_genome_analysis,
            upload_file=genome_file,
            genome_id=analysis_id,
            patient_id=patient_id,
            patient_name=patient_name,
            patient_mrn=patient_mrn,
            notify_client=send_update
        )
        
        # Return SSE response
        return StreamingResponse(
            sse_results(client_queue, analysis_id),
            media_type="text/event-stream"
        )
        
    except Exception as e:
        logger.error(f"Error starting genome analysis: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error starting analysis: {str(e)}")


async def sse_results(queue: Queue, task_id: str):
    """
    Server-Sent Events generator for streaming progress updates
    
    Args:
        queue: Queue for receiving progress updates
        task_id: Unique ID for the task
        
    Yields:
        SSE formatted messages
    """
    try:
        while True:
            message = await queue.get()
            
            # Add task_id to the message if not present
            if "task_id" not in message:
                message["task_id"] = task_id
                
            # Convert message to JSON and yield as SSE
            yield f"data: {json.dumps(message)}\n\n"
            
            # If status is complete or error, break the loop
            if message.get("status") in ["completed", "error"]:
                break
                
    except asyncio.CancelledError:
        logger.info(f"SSE connection for task {task_id} was cancelled")
    except Exception as e:
        logger.error(f"Error in SSE stream for task {task_id}: {str(e)}")
        yield f"data: {json.dumps({'task_id': task_id, 'status': 'error', 'message': str(e)})}\n\n"
    finally:
        logger.info(f"SSE connection for task {task_id} closed")


async def process_genome_analysis(
    upload_file: UploadFile,
    genome_id: str,
    patient_id: Optional[str] = None,
    patient_name: Optional[str] = None,
    patient_mrn: Optional[str] = None,
    notify_client: Callable = None
) -> Dict[str, Any]:
    """
    Process genome analysis task in the background
    
    Args:
        upload_file: The uploaded genome file
        genome_id: Unique ID for this genome analysis
        patient_id: Optional patient ID
        patient_name: Optional patient name
        patient_mrn: Optional patient medical record number
        notify_client: Callback to notify client of progress
        
    Returns:
        Dict with analysis results
    """
    try:
        # Create a temporary directory for processing
        with tempfile.TemporaryDirectory() as temp_dir:
            # Update client on progress - start
            if notify_client:
                await notify_client({
                    "status": "processing",
                    "progress": 10,
                    "message": "Starting genome analysis..."
                })
            
            # Save the uploaded file
            file_path = os.path.join(temp_dir, secure_filename(upload_file.filename))
            with open(file_path, "wb") as f:
                content = await upload_file.read()
                f.write(content)
            
            logger.info(f"Saved uploaded genome file to: {file_path}")
            
            # Update client on progress - file saved
            if notify_client:
                await notify_client({
                    "status": "processing",
                    "progress": 20,
                    "message": "Running PharmCAT analysis..."
                })
            
            # Run PharmCAT analysis
            pharmcat_results = await run_pharmcat_analysis(file_path)
            
            if not pharmcat_results.get("success", False):
                error_msg = pharmcat_results.get("message", "Unknown error in PharmCAT analysis")
                logger.error(f"PharmCAT analysis failed: {error_msg}")
                
                # Update client on error
                if notify_client:
                    await notify_client({
                        "status": "error",
                        "message": f"PharmCAT analysis failed: {error_msg}"
                    })
                
                return {
                    "success": False,
                    "message": f"PharmCAT analysis failed: {error_msg}"
                }
            
            # Update client - PharmCAT analysis done
            if notify_client:
                await notify_client({
                    "status": "processing",
                    "progress": 70,
                    "message": "PharmCAT analysis completed, generating report..."
                })
            
            # Get the PharmCAT results
            genes = pharmcat_results.get("data", {}).get("genes", [])
            drug_recommendations = pharmcat_results.get("data", {}).get("drugRecommendations", [])
            pdf_report_url = pharmcat_results.get("data", {}).get("pdf_report_url", "")
            html_report_url = pharmcat_results.get("data", {}).get("html_report_url", "")
            
            logger.info(f"PharmCAT analysis completed with {len(genes)} genes and {len(drug_recommendations)} drug recommendations")
            
            # Generate PDF report (placeholder for now - will be implemented separately)
            report_result = {
                "report_id": genome_id,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                "pdf_url": pdf_report_url,
                "html_url": html_report_url
            }
            
            # Update client - report generated
            if notify_client:
                await notify_client({
                    "status": "completed",
                    "progress": 100,
                    "message": "Analysis completed successfully",
                    "data": {
                        "genes": genes,
                        "drugRecommendations": drug_recommendations,
                        "report": report_result
                    }
                })
            
            # Return the results
            return {
                "success": True,
                "message": "Genome analysis completed successfully",
                "data": {
                    "analysis_id": genome_id,
                    "patient_id": patient_id,
                    "patient_name": patient_name,
                    "genes": genes,
                    "drugRecommendations": drug_recommendations,
                    "report": report_result,
                    "pdf_report_url": pdf_report_url,
                    "html_report_url": html_report_url
                }
            }
    
    except Exception as e:
        logger.error(f"Error in genome analysis: {str(e)}")
        logger.error(traceback.format_exc())
        
        # Update client on error
        if notify_client:
            await notify_client({
                "status": "error",
                "message": f"Error in genome analysis: {str(e)}"
            })
        
        return {
            "success": False,
            "message": f"Error in genome analysis: {str(e)}"
        }

async def run_pharmcat_analysis(genome_path: str) -> Dict[str, Any]:
    """
    Run PharmCAT analysis on genome file and extract data from report.json
    
    Args:
        genome_path: Path to the genome file
        
    Returns:
        Dict with PharmCAT results and status
    """
    try:
        logger.info(f"Starting PharmCAT analysis for {genome_path}")
        
        # Call the PharmCAT service
        response = await pharmcat_client.async_call_pharmcat_api(genome_path)
        
        # Check if we received a proper response
        if not isinstance(response, dict):
            logger.error(f"Invalid response from PharmCAT service: {response}")
            return {
                "success": False,
                "message": "Invalid response from PharmCAT service"
            }
        
        # Check if the response is in the report.json format (has expected keys)
        required_keys = ["title", "timestamp", "pharmcatVersion", "genes", "drugs"]
        has_report_json_format = all(key in response for key in required_keys)
        
        # If we have a direct report.json format
        if has_report_json_format:
            logger.info("Received direct report.json format from PharmCAT service")
            
            # Normalize the results
            normalized_results = pharmcat_client.normalize_pharmcat_results(response)
            return {
                "success": normalized_results["success"],
                "message": normalized_results["message"],
                "data": {
                    "genes": normalized_results["genes"],
                    "drugRecommendations": normalized_results["drugRecommendations"],
                    "pdf_report_url": normalized_results["pdf_report_url"],
                    "html_report_url": normalized_results["html_report_url"]
                }
            }
        
        # If not direct report.json format but we have a success key and nested data
        if "success" in response and response.get("success", False):
            logger.info("Received standard PharmCAT API response, extracting report.json content")
            
            # Extract report.json content
            genes = response.get("genes", {})
            drugs = response.get("drugs", {})
            
            # Build a report.json compatible structure
            report_json_compatible = {
                "success": True,
                "data": response.get("data", {}),
                "title": response.get("title", ""),
                "timestamp": response.get("timestamp", ""),
                "pharmcatVersion": response.get("pharmcatVersion", ""),
                "genes": genes,
                "drugs": drugs,
                "messages": response.get("messages", [])
            }
            
            # Normalize the results
            normalized_results = pharmcat_client.normalize_pharmcat_results(report_json_compatible)
            return {
                "success": normalized_results["success"],
                "message": normalized_results["message"],
                "data": {
                    "genes": normalized_results["genes"],
                    "drugRecommendations": normalized_results["drugRecommendations"],
                    "pdf_report_url": normalized_results["pdf_report_url"],
                    "html_report_url": normalized_results["html_report_url"]
                }
            }
        
        # If response indicates failure
        if "success" in response and not response.get("success", True):
            error_msg = response.get("message", "Unknown error in PharmCAT analysis")
            logger.error(f"PharmCAT analysis failed: {error_msg}")
            return {
                "success": False,
                "message": f"PharmCAT analysis failed: {error_msg}"
            }
        
        # If none of the above, return a generic error
        logger.error(f"Unexpected PharmCAT response format: {response}")
        return {
            "success": False,
            "message": "Unexpected PharmCAT response format"
        }
        
    except Exception as e:
        logger.error(f"Error in PharmCAT analysis: {str(e)}")
        logger.error(traceback.format_exc())
        return {
            "success": False,
            "message": f"Error in PharmCAT analysis: {str(e)}"
        }


