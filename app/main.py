from fastapi import FastAPI, Depends, HTTPException, status, Request, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from jose import JWTError, jwt
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
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

# Directory setup
BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = BASE_DIR / "templates"
UPLOAD_DIR = Path("/tmp")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_DIR = Path("/data/reports")
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

def detect_file_type(file_path, filename):
    """
    Detect the file type based on extension and header
    
    Args:
        file_path: Path to the uploaded file
        filename: Original filename with extension
        
    Returns:
        Tuple of (file_type, file_extension, is_compressed)
    """
    lower_filename = filename.lower()
    is_compressed = False
    
    # Check by extension first
    if lower_filename.endswith('.bam'):
        return 'BAM', 'bam', False
    elif lower_filename.endswith('.sam'):
        return 'SAM', 'sam', False
    elif lower_filename.endswith('.cram'):
        return 'CRAM', 'cram', False
    elif lower_filename.endswith('.vcf.gz'):
        return 'VCF', 'vcf.gz', True
    elif lower_filename.endswith('.vcf'):
        return 'VCF', 'vcf', False
    
    # Check file header for more accurate detection
    try:
        with open(file_path, 'rb') as f:
            header = f.read(10)  # Read first few bytes
            
            # BAM magic header
            if header.startswith(b'BAM\x01'):
                return 'BAM', 'bam', False
            
            # Gzip magic header
            if header.startswith(b'\x1f\x8b'):
                is_compressed = True
                # Likely a compressed VCF
                return 'VCF', 'vcf.gz', True
                
            # Check if it looks like a SAM/VCF text file
            if header.startswith(b'@HD\t') or header.startswith(b'@SQ\t'):
                return 'SAM', 'sam', False
            
            # Check for VCF header
            if header.startswith(b'##fileformat=VCF'):
                return 'VCF', 'vcf', False
    except Exception as e:
        logger.warning(f"Error checking file header: {str(e)}")
    
    # Default to VCF if can't determine
    return 'VCF', 'vcf', False

def determine_sequencing_profile(file_type, reference_genome='hg19'):
    """
    Determine the appropriate sequencing profile for Aldy
    
    Args:
        file_type: Type of the genomic file (BAM, SAM, CRAM, VCF)
        reference_genome: Reference genome used (default: hg19)
        
    Returns:
        Appropriate sequencing profile string for Aldy
    """
    # For most WGS/WXS data, illumina is the default profile
    return "illumina"

def sanitize_filename(filename):
    """
    Sanitize a filename to remove invalid characters
    
    Args:
        filename: Original filename
        
    Returns:
        Sanitized filename
    """
    if not filename:
        return None
    # Replace invalid characters with underscores
    for char in ['/', '\\', ':', '*', '?', '"', '<', '>', '|', ' ']:
        filename = filename.replace(char, '_')
    return filename

@app.post("/upload-vcf")
async def upload_genomic_file(
    request: Request,
    genomicFile: UploadFile = File(...),
    sampleId: str = Form(None),
    referenceGenome: str = Form("hg19")  # For CRAM files
):
    logger.info(f"Received genomic file upload: {genomicFile.filename}, Sample ID: {sampleId}")
    
    # Create a temporary file to store the upload
    temp_file_path = UPLOAD_DIR / f"temp_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    with open(temp_file_path, "wb") as buffer:
        shutil.copyfileobj(genomicFile.file, buffer)
    
    # Detect file type
    file_type, file_ext, is_compressed = detect_file_type(temp_file_path, genomicFile.filename)
    logger.info(f"Detected file type: {file_type}, extension: {file_ext}, compressed: {is_compressed}")
    
    # Generate a unique filename with the correct extension
    filename = f"{sampleId or 'sample'}-{datetime.now().strftime('%Y%m%d%H%M%S')}.{file_ext}"
    file_path = UPLOAD_DIR / filename
    
    # Rename the temporary file
    shutil.move(temp_file_path, file_path)
    logger.info(f"Saved uploaded file to {file_path}")
    
    # Determine the appropriate sequencing profile
    profile = determine_sequencing_profile(file_type, referenceGenome)
    logger.info(f"Using sequencing profile: {profile}")
    
    # Prepare file for analysis
    try:
        # For BAM files, ensure they're indexed
        if file_type == 'BAM':
            logger.info(f"Indexing BAM file: {file_path}")
            try:
                index_cmd = f"samtools index {file_path}"
                subprocess.run(index_cmd, shell=True, check=True)
                logger.info(f"BAM indexing completed for {filename}")
            except subprocess.CalledProcessError as e:
                logger.error(f"Error indexing BAM file: {str(e)}")
                return templates.TemplateResponse(
                    "index.html", 
                    {
                        "request": request, 
                        "message": f"Error indexing BAM file: {str(e)}",
                        "success": False
                    }
                )
        
        # Call PharmCAT service for analysis
        pharmcat_results = call_pharmcat_service(str(file_path))
        logger.info(f"PharmCAT analysis completed for {filename}")
        
        # Process with Aldy service for CYP2D6 and other genes
        aldy_results = {"status": "unknown"}
        try:
            # Prepare file for Aldy
            aldy_file = file_path
            
            # Ensure we're using the correct URL for the Aldy service
            aldy_url = os.environ.get("ALDY_API_URL", "http://aldy:5000")
            logger.info(f"Calling Aldy service at {aldy_url}/multi_genotype")
            
            # Call Aldy's multi_genotype endpoint to analyze multiple genes
            with open(aldy_file, 'rb') as f:
                aldy_response = requests.post(
                    f"{aldy_url}/multi_genotype",
                    files={"file": (os.path.basename(aldy_file), f)},
                    data={
                        "genes": "CYP2D6,CYP2C19,CYP2C9,CYP2B6,CYP1A2",
                        "sequencing_profile": profile,
                        "file_type": file_type.lower(),
                        "reference_genome": referenceGenome if file_type == 'CRAM' else None
                    },
                    timeout=300  # Increased timeout for processing multiple genes
                )
                
                logger.info(f"Aldy response status code: {aldy_response.status_code}")
                
                # Try to log the raw response content for debugging
                try:
                    logger.info(f"Aldy raw response: {aldy_response.text[:500]}...")
                except:
                    logger.info("Could not log Aldy raw response")
                
                aldy_response.raise_for_status()
                aldy_results = aldy_response.json()
                logger.info(f"Aldy multi-gene analysis completed for {filename}")
                logger.info(f"Aldy response status: {aldy_results.get('status', 'unknown')}")
                logger.info(f"Aldy genes analyzed: {list(aldy_results.get('genes', {}).keys())}")
                
                # Log all status values to help debug issues
                for gene, gene_data in aldy_results.get('genes', {}).items():
                    gene_status = gene_data.get('status', 'unknown')
                    diplotype = gene_data.get('diplotype')
                    output_format = gene_data.get('output_format', 'json')
                    
                    logger.info(f"Aldy gene {gene} status: {gene_status}, format: {output_format}, diplotype: {diplotype}")
                    
                    # For TSV output, also log the solution data
                    if output_format == 'tsv' and 'solution_data' in gene_data:
                        logger.info(f"Aldy gene {gene} solution data: {gene_data.get('solution_data')}")
                    
                    if gene_status != 'success':
                        logger.warning(f"Aldy gene {gene} error: {gene_data.get('error', 'unknown error')}")
                        
                # Add a final debug log of the diplotypes being created
                logger.info("Creating diplotypes from results...")
        except Exception as aldy_error:
            logger.error(f"Error calling Aldy service: {str(aldy_error)}")
            # Try to get more diagnostic information
            try:
                logger.error(f"Aldy response content: {aldy_response.content}")
            except:
                pass
            aldy_results = {"status": "error", "error": str(aldy_error)}
        
        # Generate patient ID if none provided
        patient_id = sampleId or f"PATIENT_{datetime.now().strftime('%Y%m%d%H%M')}"
        
        # Create a report from the results
        diplotypes = []
        
        # Track which genes have been added to avoid duplicates
        added_genes = set()
        
        # Extract PharmCAT diplotypes
        for gene, data in pharmcat_results.get("genes", {}).items():
            diplotypes.append({
                "gene": gene,
                "diplotype": data.get("diplotype", "Unknown"),
                "phenotype": data.get("phenotype", "Unknown"),
                "activity_score": data.get("activity_score"),
                "source": "PharmCAT"
            })
            added_genes.add(gene)
            logger.info(f"Added PharmCAT gene: {gene} with diplotype {data.get('diplotype', 'Unknown')}")
        
        # Add Aldy results for multiple genes if available
        if aldy_results.get("status") == "success" and "genes" in aldy_results:
            logger.info(f"Processing Aldy results for {len(aldy_results.get('genes', {}))} genes")
            
            # Debug: log all gene results
            for gene_name, gene_data in aldy_results.get("genes", {}).items():
                logger.info(f"Aldy result for {gene_name}: status={gene_data.get('status')}, " +
                           f"diplotype={gene_data.get('diplotype')}, " +
                           f"activity_score={gene_data.get('activity_score')}")
                
                # Log the full error for debugging
                if gene_data.get("status") != "success":
                    logger.error(f"Aldy error details for {gene_name}: {gene_data.get('error', 'No error')}")
                    logger.error(f"Aldy stderr for {gene_name}: {gene_data.get('stderr', 'No stderr')}")
                    
            # Process successful gene results
            for gene, gene_data in aldy_results.get("genes", {}).items():
                # Accept both "success" and "partial" status for gene calls
                # partial means we have a diplotype but might be missing some details
                if gene_data.get("status") in ["success", "partial"] and gene_data.get("diplotype"):
                    # Skip if the gene is already in diplotypes from PharmCAT
                    if gene not in added_genes:
                        diplotype_value = gene_data.get("diplotype", "Unknown")
                        activity_score = gene_data.get("activity_score")
                        
                        # Generate a phenotype based on activity score if available
                        phenotype = "Unknown"
                        if activity_score is not None:
                            if activity_score == 0:
                                phenotype = "Poor Metabolizer"
                            elif 0 < activity_score < 1.0:
                                phenotype = "Intermediate Metabolizer"
                            elif 1.0 <= activity_score < 2.0:
                                phenotype = "Normal Metabolizer"
                            elif activity_score >= 2.0:
                                phenotype = "Ultra-rapid Metabolizer"
                        
                        diplotypes.append({
                            "gene": gene,
                            "diplotype": diplotype_value,
                            "phenotype": phenotype,
                            "activity_score": activity_score,
                            "source": "Aldy"
                        })
                        added_genes.add(gene)
                        logger.info(f"Added Aldy gene: {gene} with diplotype {diplotype_value}")
        
        # Generate a report ID for linking related files
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        report_id = f"PGX_{timestamp}"
        
        # Generate report paths
        reports_dir = Path("/data/reports") / patient_id
        reports_dir.mkdir(parents=True, exist_ok=True)
        
        pdf_path = reports_dir / f"{report_id}.pdf"
        html_path = reports_dir / f"{report_id}.html"
        
        # Generate reports
        pdf_report = generate_pdf_report(
            patient_id=patient_id,
            report_id=report_id,
            diplotypes=diplotypes,
            recommendations=pharmcat_results.get("recommendations", []),
            report_path=str(pdf_path)
        )
        
        html_report = create_interactive_html_report(
            patient_id=patient_id,
            report_id=report_id,
            diplotypes=diplotypes,
            recommendations=pharmcat_results.get("recommendations", []),
            output_path=str(html_path)
        )
        
        # Determine if pdf_report is actually PDF or fallback HTML
        is_pdf_fallback = pdf_report.endswith('.html')
        
        return templates.TemplateResponse(
            "index.html", 
            {
                "request": request, 
                "message": f"File {genomicFile.filename} processed successfully! Reports generated." + 
                           (" (PDF generation failed, HTML fallback used)" if is_pdf_fallback else ""),
                "success": True,
                "report_id": report_id,
                "report_pdf": f"/reports/{patient_id}/{report_id}" + (".html" if is_pdf_fallback else ".pdf"),
                "report_html": f"/reports/{patient_id}/{report_id}.html"
            }
        )
    except Exception as e:
        logger.error(f"Error processing file upload: {str(e)}")
        return templates.TemplateResponse(
            "index.html", 
            {
                "request": request, 
                "message": f"Error processing file: {str(e)}",
                "success": False
            }
        )

async def handle_pgx_report(vcf_path, sample_id=None):
    """
    Process uploaded VCF file for pharmacogenomic analysis
    
    Args:
        vcf_path: Path to the VCF file
        sample_id: Optional sample ID (default: auto-detected from VCF)
        
    Returns:
        Dictionary with report data
    """
    try:
        logging.info(f"Handling PGx report for VCF: {vcf_path}")
        
        # Use the current timestamp in the file ID for uniqueness
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        
        # Create a valid filename with the sample ID and timestamp
        if sample_id:
            sample_id = sanitize_filename(sample_id)
            filename = f"{sample_id}-{timestamp}.vcf"
        else:
            # If no sample ID provided, use a generic name
            filename = f"upload-{timestamp}.vcf"
            
        # Copy VCF file to a unique filename to avoid conflicts
        dest_path = os.path.join("/tmp", filename)
        shutil.copy2(vcf_path, dest_path)
        logging.info(f"Copied VCF to {dest_path}")
        
        # Call PharmCAT for genotype calling - future step
        pharmpcat_results = {}
        try:
            logging.info("Calling PharmCAT service")
            # pharmcat_url = "http://pharmcat:8080/rest/genotype"
            # TODO: Implement PharmCAT integration
        except Exception as e:
            logging.error(f"Error calling PharmCAT service: {str(e)}")
        
        # Call Aldy for multi-gene genotyping
        aldy_results = {}
        genes_analyzed = 0
        gene_results = {}
        
        try:
            # Copy file to Aldy service data directory
            aldy_file_path = os.path.join("/tmp", f"{sample_id or 'sample'}-{timestamp}.vcf")
            shutil.copy2(vcf_path, aldy_file_path)
            logging.info(f"Copied VCF for Aldy to {aldy_file_path}")
            
            # List of genes to analyze
            gene_list = ["CYP2D6", "CYP2C19", "CYP2C9", "CYP2B6", "CYP1A2"]
            
            # Call Aldy's multi-gene endpoint
            logging.info(f"Calling Aldy service for multi-gene analysis: {gene_list}")
            aldy_url = "http://aldy:5000/multi_genotype"
            
            # Create a multipart form-data request
            with open(aldy_file_path, 'rb') as f:
                files = {'file': (os.path.basename(aldy_file_path), f)}
                data = {
                    'genes': ','.join(gene_list),
                    'sequencing_profile': 'illumina'
                    # No profile parameter - Aldy will use the sample from VCF
                }
                
                async with aiohttp.ClientSession() as session:
                    # Increase timeout for multi-gene analysis
                    async with session.post(aldy_url, data=data, files=files, timeout=180) as response:
                        if response.status == 200:
                            aldy_results = await response.json()
                            logging.info(f"Aldy returned results with status: {aldy_results.get('status')}")
                            
                            # Process multi-gene results
                            if 'genes' in aldy_results:
                                genes = aldy_results['genes']
                                genes_analyzed = len(genes)
                                logging.info(f"Processing results for {genes_analyzed} genes")
                                
                                for gene_name, gene_data in genes.items():
                                    gene_status = gene_data.get('status', 'unknown')
                                    diplotype = gene_data.get('diplotype')
                                    activity_score = gene_data.get('activity_score')
                                    
                                    logging.info(f"Gene {gene_name}: status={gene_status}, diplotype={diplotype}, activity={activity_score}")
                                    
                                    # Handle different output formats (JSON or TSV)
                                    output_format = gene_data.get('output_format', 'json')
                                    
                                    if gene_status == 'success':
                                        gene_results[gene_name] = {
                                            'diplotype': diplotype,
                                            'activity_score': activity_score,
                                            'output_format': output_format,
                                            'status': 'success'
                                        }
                                        
                                        # Handle solution data if present (TSV format)
                                        if 'solution_data' in gene_data:
                                            gene_results[gene_name]['solution_data'] = gene_data['solution_data']
                                    else:
                                        error_msg = gene_data.get('error', 'Unknown error')
                                        stderr = gene_data.get('stderr', '')
                                        logging.error(f"Error for gene {gene_name}: {error_msg}")
                                        if stderr:
                                            logging.error(f"Stderr for {gene_name}: {stderr[:200]}...")
                                            
                                        gene_results[gene_name] = {
                                            'status': 'error',
                                            'error': error_msg,
                                            'output_format': output_format
                                        }
                        else:
                            error_text = await response.text()
                            logging.error(f"Aldy service returned error: {response.status}, {error_text}")
        except Exception as e:
            logging.exception(f"Error calling Aldy service: {str(e)}")
            
        # Generate basic report
        report_data = {
            'sample_id': sample_id,
            'timestamp': timestamp,
            'report_id': f"PGX-{timestamp}",
            'genes_analyzed': genes_analyzed,
            'gene_results': gene_results,
            'aldy_results': aldy_results
        }
        
        # Return report data
        return report_data
    except Exception as e:
        logging.exception(f"Error in handle_pgx_report: {str(e)}")
        return {'error': str(e)} 