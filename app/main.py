import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
import zipfile
from asyncio import Queue
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Tuple

import aiohttp
import httpx
import requests
from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jose import JWTError, jwt
from sqlalchemy.orm import Session
from werkzeug.utils import secure_filename

from app.api.db import get_db
from app.api.models import JobStage, Token, TokenData, WorkflowCreate, WorkflowStepCreate
from app.api.routes import report_router, upload_router
from app.api.routes.monitoring import router as monitoring_router
from app.api.routes.workflow_router import router as workflow_router
from app.api.utils.security import get_current_user, get_optional_user
from app.pharmcat import pharmcat_client
from app.pharmcat.pharmcat_client import call_pharmcat_service, normalize_pharmcat_results
from app.reports.generator import create_interactive_html_report, generate_pdf_report, generate_report
from app.services.job_status_service import JobStatusService
from app.services.workflow_service import WorkflowService
from app.services.cleanup_service import cleanup_service
from app.utils.workflow_client import WorkflowClient, create_workflow_client

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
print(f"=========== ZaroPGx STARTING UP AT: {datetime.utcnow()} ===========")
print(f"LOG LEVEL: {log_level}")
print(f"GATK SERVICE URL: {os.getenv('GATK_API_URL', 'http://gatk-api:5000')}")
print(f"PHARMCAT SERVICE URL: {os.getenv('PHARMCAT_API_URL', 'http://pharmcat:5000')}")
print(f"PYPGX SERVICE URL: {os.getenv('PYPGX_API_URL', 'http://pypgx:5000')}")

# Load environment variables
load_dotenv()

# Security configuration
SECRET_KEY = os.getenv("SECRET_KEY", "supersecretkey")  # In production, use env var
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Constants
GATK_SERVICE_URL = os.getenv("GATK_API_URL", "http://gatk-api:5000")
PYPGX_SERVICE_URL = os.getenv("PYPGX_API_URL", "http://pypgx:5000")
PHARMCAT_API_URL = os.getenv("PHARMCAT_API_URL", "http://pharmcat:5000")

# Service toggle configuration
def _env_flag(name: str, default: bool = False) -> bool:
    """Parse environment variable as boolean flag."""
    val = os.getenv(name)
    if val is None:
        return default
    return str(val).strip().lower() in {"1", "true", "yes", "on"}

# Service enablement flags
GATK_ENABLED = _env_flag("GATK_ENABLED", True)
PYPGX_ENABLED = _env_flag("PYPGX_ENABLED", True)
OPTITYPE_ENABLED = _env_flag("OPTITYPE_ENABLED", True)
GENOME_DOWNLOADER_ENABLED = _env_flag("GENOME_DOWNLOADER_ENABLED", True)
KROKI_ENABLED = _env_flag("KROKI_ENABLED", True)
HAPI_FHIR_ENABLED = _env_flag("HAPI_FHIR_ENABLED", True)
TEMP_DIR = Path("/tmp")
DATA_DIR = Path("/data")
REPORTS_DIR = Path(os.getenv("REPORT_DIR", "/data/reports"))
UPLOADS_DIR = Path(os.getenv("UPLOAD_DIR", "/data/uploads"))
BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = BASE_DIR / "templates"

# Create directories if they don't exist
TEMP_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

# Initialize templates
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

Author_Name = "Iliya Yaroshevskiy"

# ----- Legal/Attribution helpers for AGPL notices -----
def _read_author_from_pyproject() -> str:
    try:
        project_root = os.path.dirname(os.path.dirname(__file__))
        pyproject_path = os.path.join(project_root, "pyproject.toml")
        if not os.path.exists(pyproject_path):
            return Author_Name
        with open(pyproject_path, "r", encoding="utf-8") as f:
            content = f.read()
        # Extract authors array content
        authors_block_match = re.search(r"^\s*authors\s*=\s*\[(.*?)\]", content, flags=re.DOTALL | re.MULTILINE)
        block = authors_block_match.group(1) if authors_block_match else content
        name_match = re.search(r"name\s*=\s*\"([^\"]+)\"", block)
        if name_match:
            return name_match.group(1).strip()
        return Author_Name
    except Exception:
        return Author_Name


def get_author_name() -> str:
    env_author = os.getenv("AUTHOR_NAME")
    if env_author:
        return env_author
    return _read_author_from_pyproject()

# Initialize FastAPI app
app = FastAPI(
    title="ZaroPGx, an Individual Pharmacogenomic Analysis Platform",
    description="An application with an API for processing genetic data and generating pharmacogenomic reports",
    version="0.2.0"
)

# OAuth2
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

# Set up static file serving for application static assets
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# Static file serving for reports is now handled by custom routes
# app.mount("/reports", StaticFiles(directory=str(REPORTS_DIR)), name="reports")

# Mount built Sphinx documentation (if present) at /documentation
DOCS_BUILD_DIR = BASE_DIR.parent / "docs" / "_build" / "html"
if DOCS_BUILD_DIR.exists():
    app.mount("/documentation", StaticFiles(directory=str(DOCS_BUILD_DIR), html=True), name="sphinx-docs")


def _build_docs_if_missing() -> None:
    try:
        docs_index = DOCS_BUILD_DIR / "index.html"
        if not docs_index.exists():
            DOCS_BUILD_DIR.mkdir(parents=True, exist_ok=True)
            # Build docs using Sphinx if available
            cmd = [
                sys.executable,
                "-m",
                "sphinx",
                "-b",
                "html",
                "docs",
                str(DOCS_BUILD_DIR),
            ]
            subprocess.run(cmd, check=False)
        # Mount after building if not already mounted
        if "/documentation" not in {m.path for m in app.router.routes if hasattr(m, "path")} and DOCS_BUILD_DIR.exists():
            app.mount("/documentation", StaticFiles(directory=str(DOCS_BUILD_DIR), html=True), name="sphinx-docs")
    except Exception as e:
        logger.warning(f"Docs build skipped or failed: {e}")


@app.on_event("startup")
async def ensure_docs_built_on_start() -> None:
    _build_docs_if_missing()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        # Production domain -- needs to be switched to use env variable instead of hardcoded
        "https://pgx.zimerguz.net", 
        "http://pgx.zimerguz.net", # HTTP is disabled
        
        # Localhost development - main app ports
        "http://localhost:8765",  # Main FastAPI app external port
        "http://localhost:8000",  # Internal app port
        "http://127.0.0.1:8765",
        "http://127.0.0.1:8000",
        
        # Common frontend development ports
        "http://localhost:3000",
        "http://localhost:8080",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:8080",
        
        # Service-specific ports from docker-compose.yml
        "http://localhost:5050",  # genome-downloader
        "http://localhost:2323",  # pharmcat
        "http://localhost:8090",  # fhir-server
        "http://localhost:5001",  # pharmcat API port
        "http://localhost:5002",  # gatk-api
        "http://localhost:5053",  # pypgx
        "http://localhost:5444",  # PostgreSQL
        "http://localhost:5060",  # hlatyping
        "http://localhost:5055",  # nextflow

        # 127.0.0.1 equivalents
        "http://127.0.0.1:5050",
        "http://127.0.0.1:2323",
        "http://127.0.0.1:8090",
        "http://127.0.0.1:5001",
        "http://127.0.0.1:5002",
        "http://127.0.0.1:5053",
        "http://127.0.0.1:5444",
        "http://127.0.0.1:5060",
        "http://127.0.0.1:5055",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(upload_router.router)
app.include_router(report_router.router)
app.include_router(monitoring_router)
app.include_router(workflow_router)

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


# Simple wrapper page for API reference with a Back button
@app.get("/api-reference", include_in_schema=False)
async def api_reference() -> HTMLResponse:
    html = (
        """
<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>ZaroPGx API Reference</title>
  <style>
    body, html { margin: 0; padding: 0; height: 100%; }
    .topbar { display: flex; align-items: center; gap: 8px; padding: 10px; border-bottom: 1px solid #e5e7eb; }
    .topbar h1 { font-size: 16px; margin: 0; font-weight: 600; }
    .btn { display: inline-block; padding: 8px 12px; border-radius: 8px; text-decoration: none; font-size: 14px; }
    .btn-primary { background: #0d6efd; color: #fff; }
    .btn-primary:hover { background: #0b5ed7; }
    .frame { width: 100%; height: calc(100vh - 48px); border: 0; }
  </style>
  <link rel=\"icon\" href=\"data:;base64,iVBORw0KGgo=\" />
  <meta http-equiv=\"Content-Security-Policy\" content=\"frame-ancestors 'self';\" />
  <meta http-equiv=\"X-Frame-Options\" content=\"SAMEORIGIN\" />
  <meta http-equiv=\"Referrer-Policy\" content=\"no-referrer\" />
  <meta http-equiv=\"Permissions-Policy\" content=\"interest-cohort=()\" />
</head>
<body>
  <div class=\"topbar\">
    <a class=\"btn btn-primary\" href=\"/\">Back to ZaroPGx</a>
    <h1>API Reference</h1>
  </div>
  <iframe class=\"frame\" src=\"/docs\" title=\"Swagger UI\" loading=\"lazy\"></iframe>
</body>
</html>
        """
    )
    return HTMLResponse(content=html)

# Add direct routes for status and reports
@app.get("/status/{file_id}")
async def get_status(file_id: str, db: Session = Depends(get_db), current_user: str = Depends(get_optional_user)):
    """Forward to upload_router status endpoint"""
    return await upload_router.get_upload_status(file_id, db)

# Generic report file serving route removed - now handled by specific endpoints
# This route was conflicting with the specific /reports/{job_id} endpoint
# Individual report files are now served through the get_report_urls function

# Report serving routes - order matters for path matching
@app.get("/reports/job/{job_id}")
async def get_reports_by_job_id(job_id: str, current_user: str = Depends(get_optional_user)):
    """Get reports by job ID - forwards to upload_router"""
    from app.api.db import SessionLocal
    db = SessionLocal()
    try:
        return await upload_router.get_report_urls(job_id, db)
    finally:
        db.close()

@app.get("/reports/{job_id}")
async def get_reports_direct(job_id: str, current_user: str = Depends(get_optional_user)):
    """Direct reports endpoint for frontend compatibility - same as /reports/job/{job_id}"""
    from app.api.db import SessionLocal
    db = SessionLocal()
    try:
        return await upload_router.get_report_urls(job_id, db)
    finally:
        db.close()

# Add a route to serve individual report files (MUST be after the job_id routes)
@app.api_route("/reports/{patient_id}/{filename:path}", methods=["GET", "HEAD"])
async def serve_report_file(patient_id: str, filename: str, current_user: str = Depends(get_optional_user)):
    """Serve individual report files from the reports directory"""
    from pathlib import Path
    import os
    
    # Construct the file path
    file_path = REPORTS_DIR / patient_id / filename
    
    # Security check: ensure the path is within the reports directory
    try:
        file_path = file_path.resolve()
        reports_dir = REPORTS_DIR.resolve()
        if not str(file_path).startswith(str(reports_dir)):
            raise HTTPException(status_code=403, detail="Access denied")
    except Exception:
        raise HTTPException(status_code=403, detail="Invalid file path")
    
    # Check if file exists
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    
    # Determine content type based on file extension
    content_type = "application/octet-stream"
    if filename.endswith('.html'):
        content_type = "text/html"
    elif filename.endswith('.pdf'):
        content_type = "application/pdf"
    elif filename.endswith('.json'):
        content_type = "application/json"
    elif filename.endswith('.tsv'):
        content_type = "text/tab-separated-values"
    elif filename.endswith('.svg'):
        content_type = "image/svg+xml"
    elif filename.endswith('.png'):
        content_type = "image/png"
    
    # Read and return the file
    try:
        with open(file_path, 'rb') as f:
            content = f.read()
        return Response(content=content, media_type=content_type)
    except Exception as e:
        logger.error(f"Error reading file {file_path}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error reading file")

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
    """Root endpoint to serve the homepage with pharmacogenomic analysis form"""
    try:
        # Check service status before rendering page
        service_status = {"status": "ok", "message": "All services are available", "unhealthy_services": {}}
        
        # Internal check of services - don't expose network errors to users
        try:
            # Since we're in the app code, app is by definition running
            # Only check external services
            service_urls = {
                "gatk": os.getenv("GATK_API_URL", "http://gatk-api:5000") + "/health",
                "pharmcat": os.getenv("PHARMCAT_API_URL", "http://pharmcat:5000") + "/health", 
                "pypgx": "http://pypgx:5000/health"  # Force to port 5000 directly
            }
            
            unhealthy_services = []
            
            async with httpx.AsyncClient() as client:
                for service_name, url in service_urls.items():
                    try:
                        logger.info(f"Homepage check: Checking {service_name} at {url}")
                        response = await client.get(url, timeout=2.0, follow_redirects=True)
                        logger.info(f"Homepage check: {service_name} response status={response.status_code}")
                        if response.status_code < 200 or response.status_code >= 300:
                            unhealthy_services.append(service_name)
                    except Exception as e:
                        # If we can't reach a service, mark it as unhealthy
                        logger.error(f"Homepage check: Error checking {service_name}: {str(e)}")
                        unhealthy_services.append(service_name)
            
            # If any services are unhealthy, set status to error
            if unhealthy_services:
                service_status = {
                    "status": "error",
                    "message": "Some services are unavailable",
                    "unhealthy_services": unhealthy_services
                }
        except Exception as e:
            # If something goes wrong with the check, just log it
            logger.exception(f"Error checking services: {str(e)}")
        
        # Render the template with service status
        service_alert = None
        if service_status["status"] == "error":
            unhealthy_list = service_status["unhealthy_services"]
            # Format names for display
            if len(unhealthy_list) == 1:
                service_message = f"{unhealthy_list[0]} is unavailable."
            else:
                service_message = f"{', '.join(unhealthy_list)} are unavailable."
                
            service_alert = service_message
                
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "service_alert": service_alert,
                "author_name": get_author_name(),
                "license_name": "GNU Affero General Public License v3.0",
                "license_url": "https://www.gnu.org/licenses/agpl-3.0.html",
                "source_url": os.getenv("SOURCE_URL", "https://github.com/Zaroganos/ZaroPGx"),
                "current_year": datetime.now().year,
            },
        )
    except Exception as e:
        logger.exception(f"Error in home route: {str(e)}")
        return HTMLResponse(f"<h1>Error</h1><p>{str(e)}</p>")

@app.get("/license")
async def license_text():
    try:
        project_root = Path(__file__).resolve().parent.parent
        license_path = project_root / "LICENSE"
        if license_path.exists():
            return FileResponse(str(license_path), media_type="text/plain")
        return HTMLResponse("<pre>LICENSE file not found.</pre>", status_code=404)
    except Exception:
        return HTMLResponse("<pre>Unable to serve LICENSE.</pre>", status_code=500)


@app.get("/notice")
async def notice_text():
    try:
        project_root = Path(__file__).resolve().parent.parent
        notice_path = project_root / "NOTICE"
        if notice_path.exists():
            return FileResponse(str(notice_path), media_type="text/plain")
        return HTMLResponse("<pre>NOTICE file not found.</pre>", status_code=404)
    except Exception:
        return HTMLResponse("<pre>Unable to serve NOTICE.</pre>", status_code=500)


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

# REDUNDANCY ALERT
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

# Workflow management functions
# APPEARS TO BE LEGACY FUNCTION - CONSIDER REMOVING
async def create_workflow_for_job(job_id: str, file_type: str, sample_id: str, reference_genome: str, db: Session) -> str:
    """Create a workflow for a job and return the workflow ID."""
    try:
        workflow_service = WorkflowService(db)
        
        # Define workflow steps based on file type
        if file_type == 'vcf':
            steps = [
                {"name": "star_allele_calling", "order": 1, "container_name": "pypgx"},
                {"name": "pharmcat", "order": 2, "container_name": "pharmcat"},
                {"name": "report_generation", "order": 3, "container_name": "app"}
            ]
        else:
            steps = [
                {"name": "variant_calling", "order": 1, "container_name": "gatk-api"},
                {"name": "star_allele_calling", "order": 2, "container_name": "pypgx"},
                {"name": "pharmcat", "order": 3, "container_name": "pharmcat"},
                {"name": "report_generation", "order": 4, "container_name": "app"}
            ]
        
        # Create workflow
        workflow_data = WorkflowCreate(
            name=f"PGx Analysis - {sample_id}",
            description=f"Pharmacogenomic analysis for {file_type} file",
            total_steps=len(steps),
            metadata={
                "job_id": job_id,
                "file_type": file_type,
                "sample_id": sample_id,
                "reference_genome": reference_genome
            },
            created_by="system"
        )
        
        workflow = workflow_service.create_workflow(workflow_data)
        
        # Create workflow steps
        for step in steps:
            step_data = WorkflowStepCreate(
                workflow_id=workflow.id,
                step_name=step["name"],
                step_order=step["order"],
                container_name=step["container_name"]
            )
            workflow_service.create_workflow_step(step_data)
        
        logger.info(f"Created workflow {workflow.id} for job {job_id}")
        return str(workflow.id)
        
    except Exception as e:
        logger.error(f"Failed to create workflow for job {job_id}: {e}")
        raise

async def update_workflow_progress(workflow_id: str, step_name: str, status: str, message: str = None, progress: int = None, output_data: dict = None, error_details: dict = None):
    """Update workflow step progress using WorkflowClient."""
    try:
        async with WorkflowClient(workflow_id=workflow_id, step_name=step_name) as client:
            if status == "running":
                await client.start_step(message)
            elif status == "completed":
                await client.complete_step(message, output_data)
            elif status == "failed":
                await client.fail_step(message or "Step failed", error_details)
            elif status == "skipped":
                await client.skip_step(message)
            else:
                await client.update_step_status(status, message, output_data, error_details)
            
            # Log progress if provided
            if progress is not None and message:
                await client.log_progress(f"Progress: {progress}% - {message}")
                
    except Exception as e:
        logger.error(f"Failed to update workflow progress for {workflow_id}/{step_name}: {e}")

def update_workflow_progress_sync(workflow_id: str, step_name: str, status: str, message: str = None, progress: int = None, output_data: dict = None, error_details: dict = None):
    """Synchronous wrapper for update_workflow_progress."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(update_workflow_progress(workflow_id, step_name, status, message, progress, output_data, error_details))
    except RuntimeError:
        return asyncio.run(update_workflow_progress(workflow_id, step_name, status, message, progress, output_data, error_details))
    
    # If this is a completion status, log additional details
    if complete:
        status = "SUCCESS" if success else "FAILURE"
        logger.info(f"Job {job_id} complete: {status} - {message}")
        print(f"[COMPLETE] Job {job_id}: {status} - {message}")
        
    # If there's error, also log it as an error
    if not success and (complete or stage == "error"):
        logger.error(f"Job {job_id} error: {message}")
        print(f"[ERROR] Job {job_id}: {message}")

# Legacy call_gatk_variants function removed - replaced by GATK API service

# Legacy process_file_in_background function removed - replaced by process_file_background in upload_router.py

# Legacy SSE progress endpoint removed - use workflow monitoring system instead

# Legacy event_generator function removed - use workflow monitoring system instead
    
# Legacy job-status endpoint removed - use workflow monitoring system instead

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


# Cleanup endpoints
@app.post("/api/cleanup/workflow/{workflow_id}")
async def cleanup_workflow_files(workflow_id: str, patient_id: Optional[str] = None):
    """Clean up temporary files for a specific workflow."""
    try:
        result = cleanup_service.cleanup_workflow_files(
            workflow_id=workflow_id,
            patient_id=patient_id
        )
        return JSONResponse(content=result)
    except Exception as e:
        logger.error(f"Failed to cleanup workflow {workflow_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/cleanup/old-files")
async def cleanup_old_temp_files(max_age_hours: int = 24):
    """Clean up old temporary files based on age."""
    try:
        result = cleanup_service.cleanup_old_temp_files(max_age_hours=max_age_hours)
        return JSONResponse(content=result)
    except Exception as e:
        logger.error(f"Failed to cleanup old temp files: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/cleanup/status")
async def get_cleanup_status():
    """Get current status of temporary directories."""
    try:
        result = cleanup_service.get_cleanup_status()
        return JSONResponse(content=result)
    except Exception as e:
        logger.error(f"Failed to get cleanup status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

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
            
            result = await call_gatk_variants(job_id, file_path, reference_genome, db)
            
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

# Legacy GATK test endpoint removed - use proper test endpoints in services

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

@app.get("/services-config", response_class=JSONResponse)
async def services_config():
    """Get current service configuration and toggle status"""
    return {
        "services": {
            "gatk": {"enabled": GATK_ENABLED},
            "pypgx": {"enabled": PYPGX_ENABLED},
            "optitype": {"enabled": OPTITYPE_ENABLED},
            "genome_downloader": {"enabled": GENOME_DOWNLOADER_ENABLED},
            "kroki": {"enabled": KROKI_ENABLED},
            "hapi_fhir": {"enabled": HAPI_FHIR_ENABLED}
        }
    }

@app.get("/services-status", response_class=JSONResponse)
async def services_status(request: Request, current_user: str = Depends(get_optional_user)):
    """Check the status of all services and return a comprehensive health check"""
    # Log the request details for debugging
    logger.info(f"==== SERVICE STATUS CHECK REQUEST ====")
    logger.info(f"Client IP: {request.client.host}, Method: {request.method}, Path: {request.url.path}")
    logger.info(f"Headers: {request.headers}")
    
    # Only check services that are enabled
    services_to_check = {
        "app": {
            "url": "http://localhost:8000/health",  # Using internal port 8000 instead of request.base_url
            "timeout": 5,
            "enabled": True  # App is always enabled
        },
        "database": {
            "url": os.getenv("DATABASE_URL", "postgresql://cpic_user:cpic_password@db:5432/cpic_db"),
            "timeout": 5,
            "enabled": True  # Database is always enabled
        }
    }
    
    # Add enabled services only
    if GATK_ENABLED:
        services_to_check["gatk"] = {
            "url": os.getenv("GATK_API_URL", "http://gatk-api:5000") + "/health",
            "timeout": 10,
            "enabled": True
        }
    
    if PYPGX_ENABLED:
        services_to_check["pypgx"] = {
            "url": os.getenv("PYPGX_API_URL", "http://pypgx:5000") + "/health",
            "timeout": 10,
            "enabled": True
        }
    
    # PharmCAT is always enabled (core service)
    services_to_check["pharmcat"] = {
        "url": os.getenv("PHARMCAT_API_URL", "http://pharmcat:5000") + "/health",
        "timeout": 10,
        "enabled": True
    }
    
    # For debugging - log the URLs we're trying to check
    service_urls = []
    for k, v in services_to_check.items():
        if k != 'database':
            service_urls.append(f"{k}: {v['url']}")
    logger.info(f"Checking services: {', '.join(service_urls)}")
    
    # Debugging for environment variables
    logger.info(f"PYPGX_API_URL: {os.getenv('PYPGX_API_URL', 'not set')}")
    logger.info(f"GATK_API_URL: {os.getenv('GATK_API_URL', 'not set')}")
    logger.info(f"PHARMCAT_API_URL: {os.getenv('PHARMCAT_API_URL', 'not set')}")
    
    # Check each service
    unhealthy_services = {}
    service_check_results = {}
    
    # Use httpx for concurrent requests
    async with httpx.AsyncClient() as client:
        # Check app health directly first (no HTTP request)
        logger.info("Checking app health (direct check)")
        service_check_results["app"] = {"status": "healthy", "method": "direct"}
        
        # Check database separately
        db_service = services_to_check.get("database")
        if db_service:
            logger.info(f"Checking database at {db_service['url']}")
            try:
                # Try to connect to the database
                from sqlalchemy import create_engine, text
                engine = create_engine(db_service["url"])
                with engine.connect() as connection:
                    result = connection.execute(text("SELECT 1"))
                    if not result.fetchone():
                        logger.error("Database connection test failed")
                        unhealthy_services["database"] = "Database connection test failed"
                        service_check_results["database"] = {"status": "error", "message": "Connection test failed"}
                    else:
                        logger.info("Database connection test succeeded")
                        service_check_results["database"] = {"status": "healthy"}
            except Exception as e:
                logger.error(f"Database error: {str(e)}")
                unhealthy_services["database"] = f"Database error: {str(e)}"
                service_check_results["database"] = {"status": "error", "message": str(e)}
        
        # Check pypgx with retries
        pypgx_service = services_to_check.get("pypgx")
        if pypgx_service:
            logger.info(f"Checking pypgx at {pypgx_service['url']}")
            max_retries = 2
            retry_count = 0
            success = False
            
            while retry_count <= max_retries and not success:
                try:
                    logger.info(f"PyPGx check attempt {retry_count+1}/{max_retries+1}")
                    # Add some extra request headers and a very short timeout to avoid blocking
                    response = await client.get(
                        pypgx_service["url"],
                        timeout=5.0,  # Reduced timeout for faster retries
                        headers={"User-Agent": "ZaroPGx-HealthCheck"},
                        follow_redirects=True
                    )
                    
                    logger.info(f"PyPGx response: status={response.status_code}, body={response.text[:100]}...")
                    
                    # Accept 200-299 status codes as success
                    if 200 <= response.status_code < 300:
                        success = True
                        service_check_results["pypgx"] = {"status": "healthy", "response_code": response.status_code}
                        logger.info(f"PyPGx check successful on attempt {retry_count+1}")
                        break
                    else:
                        retry_count += 1
                        logger.warning(f"PyPGx returned status {response.status_code} (retry {retry_count}/{max_retries})")
                        service_check_results["pypgx"] = {"status": "error", "response_code": response.status_code, "attempt": retry_count}
                        await asyncio.sleep(0.5)  # Short delay between retries
                except Exception as e:
                    retry_count += 1
                    logger.warning(f"Error checking PyPGx health (retry {retry_count}/{max_retries}): {str(e)}")
                    service_check_results["pypgx"] = {"status": "error", "message": str(e), "attempt": retry_count}
                    await asyncio.sleep(0.5)  # Short delay between retries
            
            if not success:
                logger.error(f"PyPGx health check failed after {max_retries+1} attempts")
                unhealthy_services["pypgx"] = f"Failed after {max_retries} retries"
        
        # Check other HTTP services
        for service_name, service_info in services_to_check.items():
            # Skip services we've already checked
            if service_name in ["app", "database", "pypgx"]:
                continue
                
            logger.info(f"Checking {service_name} at {service_info['url']}")
            try:
                # Add some extra request headers and increase timeout
                response = await client.get(
                    service_info["url"],
                    timeout=service_info["timeout"],
                    headers={"User-Agent": "ZaroPGx-HealthCheck"},
                    follow_redirects=True
                )
                
                logger.info(f"{service_name} response: status={response.status_code}")
                
                # Accept 200-299 status codes as success
                if 200 <= response.status_code < 300:
                    service_check_results[service_name] = {"status": "healthy", "response_code": response.status_code}
                else:
                    unhealthy_services[service_name] = f"HTTP {response.status_code}"
                    service_check_results[service_name] = {"status": "error", "response_code": response.status_code}
                    logger.warning(f"Service {service_name} returned status {response.status_code}")
            except Exception as e:
                logger.warning(f"Error checking {service_name} health: {str(e)}")
                unhealthy_services[service_name] = str(e)
                service_check_results[service_name] = {"status": "error", "message": str(e)}
    
    # Log the final results
    logger.info(f"==== SERVICE STATUS CHECK RESULTS ====")
    for service, result in service_check_results.items():
        logger.info(f"{service}: {result}")
    
    # Return status
    if unhealthy_services:
        result = {
            "status": "error",
            "message": "Some services are unavailable",
            "unhealthy_services": unhealthy_services,
            "check_time": str(datetime.now())
        }
        logger.info(f"Returning error result: {result}")
        return result
    else:
        result = {
            "status": "ok",
            "message": "All services are available",
            "check_time": str(datetime.now())
        }
        logger.info(f"Returning success result: {result}")
        return result

# Wait for services to be ready
@app.on_event("startup")
async def startup_event():
    """Check if required services are ready before starting the app"""
    print("=================== STARTING ZaroPGx ===================")
    logger.info("Starting ZaroPGx application")
    
    # Ensure database is properly initialized
    try:
        from app.api.db import init_db
        init_db()
        logger.info("Database connection verified")
        print("✅ Database connection verified")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        print(f"❌ Database initialization failed: {e}")
        # Don't exit, let the app start and handle errors gracefully
    
    # Services to check
    services = {
        "GATK API": f"{GATK_SERVICE_URL}/health",
        "PharmCAT Wrapper": f"{os.getenv('PHARMCAT_API_URL', 'http://pharmcat:5000')}/health",
        "PyPGx": f"{os.getenv('PYPGX_API_URL', 'http://pypgx:5000')}/health"
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
    
    print(r"""
 _____                    ____  ______    
/__  /  ____ __________  / __ \/ ____/  __
  / /  / __ `/ ___/ __ \/ /_/ / / __| |/_/
 / /__/ /_/ / /  / /_/ / ____/ /_/ />  <  
/____/\__,_/_/   \____/_/    \____/_/|_|  

Welcome to ZaroPGx, an intelligent individual pharmacogenomic analysis pipeline
                      
=================== STARTUP COMPLETE ===================
ZaroPGx is ready and listening for requests!
""")
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


@app.get("/check-reports/{job_id}")
async def check_reports(job_id: str):
    """
    Check for reports and manually trigger completion notification
    """
    try:
        # Define reports directory
        reports_dir = REPORTS_DIR
        
        # Check for report files
        pdf_path = reports_dir / f"{job_id}_pgx_report.pdf"
        html_path = reports_dir / f"{job_id}_pgx_report.html"
        
        pdf_exists = pdf_path.exists()
        html_exists = html_path.exists()
        
        # Check workflow status using the new workflow system
        try:
            from app.services.workflow_service import WorkflowService
            from app.api.db import get_db
            from sqlalchemy.orm import Session
            
            # Get database session
            db = next(get_db())
            workflow_service = WorkflowService(db)
            
            # Try to find workflow by job_id
            workflow = workflow_service.get_workflow_by_name(f"job_{job_id}")
            job_data = {"status": "unknown", "complete": False}
            
            if workflow:
                job_data = {
                    "status": workflow.status,  # status is already a string from database
                    "complete": workflow.status in ["completed", "failed"],
                    "workflow_id": str(workflow.id)
                }
                
                # Update workflow status if reports exist
                if pdf_exists or html_exists and workflow.status != "completed":
                    workflow_service.update_workflow_status(
                        workflow.id, 
                        "completed",
                        "Analysis completed successfully (from check-reports)"
                    )
                    logger.info(f"Updated workflow status for job {job_id} to completed")
        except Exception as e:
            logger.warning(f"Could not check workflow status for job {job_id}: {e}")
            job_data = {"status": "unknown", "complete": False}
        
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
            "job_status": job_data,
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
    # Check if reports exist
    pdf_path = f"/data/reports/{job_id}_pgx_report.pdf"
    html_path = f"/data/reports/{job_id}_pgx_report.html"
    
    pdf_exists = os.path.exists(pdf_path)
    html_exists = os.path.exists(html_path)
    
    # Update workflow status if reports exist
    if pdf_exists or html_exists:
        try:
            from app.services.workflow_service import WorkflowService
            from app.api.db import get_db
            
            # Get database session
            db = next(get_db())
            workflow_service = WorkflowService(db)
            
            # Try to find workflow by job_id
            workflow = workflow_service.get_workflow_by_name(f"job_{job_id}")
            
            if workflow:
                workflow_service.update_workflow_status(
                    workflow.id, 
                    "completed",
                    "Analysis completed successfully (manual trigger)"
                )
                logger.info(f"Manual trigger for job {job_id} - Workflow status updated to completed")
            else:
                logger.warning(f"Manual trigger for job {job_id} - No workflow found")
        except Exception as e:
            logger.error(f"Manual trigger for job {job_id} - Error updating workflow: {e}")
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
                    <pre>{json.dumps({"job_id": job_id, "pdf_exists": pdf_exists, "html_exists": html_exists}, indent=2)}</pre>
                    
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

@app.post("/reprocess-report/{report_id}")
async def reprocess_report(report_id: str):
    """
    Reprocess an existing report by re-running PharmCAT analysis with the updated parser.
    This is primarily for testing parser changes.
    """
    try:
        logger.info(f"Reprocessing report {report_id}")
        
        # Find the VCF file for the given report ID
        report_dir = Path(f"/data/reports/{report_id}")
        uploads_dir = Path(f"/data/uploads/{report_id}")
        
        # Look for VCF files in both directories
        vcf_files = []
        for directory in [report_dir, uploads_dir]:
            if directory.exists():
                vcf_files.extend(list(directory.glob("*.vcf")) + list(directory.glob("*.vcf.gz")))
        
        if not vcf_files:
            # If no VCF files found, return an error
            logger.error(f"No VCF files found for report {report_id}")
            return {
                "success": False,
                "message": f"No VCF files found for report {report_id}"
            }
        
        # Use the first VCF file found
        vcf_path = str(vcf_files[0])
        logger.info(f"Using VCF file: {vcf_path}")
        
        # Run PharmCAT analysis with the existing report ID
        results = await pharmcat_client.async_call_pharmcat_api(vcf_path)
        
        # Check if the analysis was successful
        if not results.get("success", False):
            logger.error(f"PharmCAT analysis failed for report {report_id}: {results.get('message', 'Unknown error')}")
            return {
                "success": False,
                "message": f"PharmCAT analysis failed: {results.get('message', 'Unknown error')}"
            }
        
        # Generate reports using the updated results
        from app.reports.generator import generate_report
        
        # Create patient info dictionary
        patient_info = {
            "id": f"patient_{report_id}",
            "report_id": report_id,
            "name": f"Patient {report_id}",
            "age": "N/A",
            "sex": "N/A",
            "encounter_date": datetime.now().strftime("%Y-%m-%d")
        }
        
        # Generate report files
        report_paths = generate_report(results, f"/data/reports/{report_id}", patient_info)
        
        # Return the results with report paths
        return {
            "success": True,
            "message": "Report reprocessed successfully",
            "data": {
                "report_id": report_id,
                "report_paths": report_paths,
                "genes": results.get("data", {}).get("genes", []),
                "drugRecommendations": results.get("data", {}).get("drugRecommendations", [])
            }
        }
    
    except Exception as e:
        logger.error(f"Error reprocessing report {report_id}: {str(e)}")
        logger.error(traceback.format_exc())
        return {
            "success": False,
            "message": f"Error reprocessing report: {str(e)}"
        }