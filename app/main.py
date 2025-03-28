from fastapi import FastAPI, Depends, HTTPException, status, Request, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from jose import JWTError, jwt
from datetime import datetime, timedelta
from typing import Optional
import os
import shutil
from pathlib import Path
from dotenv import load_dotenv
import logging
import requests
import json
import aiohttp

from app.api.routes import upload_router, report_router
from app.api.models import Token, TokenData
from app.pharmcat_wrapper.pharmcat_client import call_pharmcat_service
from app.reports.generator import generate_pdf_report, create_interactive_html_report

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Security configuration
SECRET_KEY = os.getenv("SECRET_KEY", "supersecretkey")  # In production, use env var
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Directory setup
BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = BASE_DIR / "templates"
UPLOAD_DIR = Path("/data/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

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

@app.post("/upload-vcf")
async def upload_vcf_file(
    request: Request,
    vcfFile: UploadFile = File(...),
    sampleId: str = Form(None)
):
    logger.info(f"Received VCF file upload: {vcfFile.filename}, Sample ID: {sampleId}")
    
    # Generate a unique filename if no sample ID provided
    filename = f"{sampleId or 'sample'}-{datetime.now().strftime('%Y%m%d%H%M%S')}.vcf"
    file_path = UPLOAD_DIR / filename
    
    # Save the uploaded file
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(vcfFile.file, buffer)
        
        # Call PharmCAT service for analysis
        pharmcat_results = call_pharmcat_service(str(file_path))
        logger.info(f"PharmCAT analysis completed for {filename}")
        
        # Process with Aldy service (for multiple genes)
        try:
            with open(file_path, 'rb') as f:
                # Ensure we're using the correct URL for the Aldy service
                aldy_url = os.environ.get("ALDY_API_URL", "http://aldy:5000")
                logger.info(f"Calling Aldy service at {aldy_url}/multi_genotype")
                
                # Call Aldy's multi_genotype endpoint to analyze multiple genes
                # Note: The actual sample name in the VCF is used by Aldy wrapper,
                # regardless of what we pass here
                aldy_response = requests.post(
                    f"{aldy_url}/multi_genotype",
                    files={"file": f},
                    data={
                        "genes": "CYP2D6,CYP2C19,CYP2C9,CYP2B6,CYP1A2",
                        "sequencing_profile": "illumina"
                        # Removed profile parameter - the Aldy wrapper will use the sample name from the VCF
                    },
                    timeout=180  # Increased timeout for processing multiple genes
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
                    logger.info(f"Aldy gene {gene} status: {gene_status}")
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
                    logger.error(f"Aldy error details for {gene_name}: {gene_data.get('error', 'No error message')}")
                    logger.error(f"Aldy stderr for {gene_name}: {gene_data.get('stderr', 'No stderr')}")
                    
            # Process successful gene results
            for gene, gene_data in aldy_results.get("genes", {}).items():
                if gene_data.get("status") == "success":
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
                else:
                    logger.warning(f"Skipping Aldy gene {gene} due to status: {gene_data.get('status')}")
        elif aldy_results.get("status") == "success":
            # Backward compatibility for single gene response
            if "CYP2D6" not in added_genes:
                diplotype_value = aldy_results.get("diplotype", "Unknown")
                activity_score = aldy_results.get("activity_score")
                
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
                    "gene": "CYP2D6",
                    "diplotype": diplotype_value,
                    "phenotype": phenotype,
                    "activity_score": activity_score,
                    "source": "Aldy"
                })
                added_genes.add("CYP2D6")
                logger.info(f"Added single Aldy gene: CYP2D6 with diplotype {diplotype_value}")
        
        # Log final diplotype count
        logger.info(f"Final diplotype count: {len(diplotypes)}")
        logger.info(f"Genes in report: {[d['gene'] for d in diplotypes]}")
        
        # Generate report paths
        reports_dir = Path("/data/reports") / patient_id
        reports_dir.mkdir(parents=True, exist_ok=True)
        
        report_id = f"PGX_{datetime.now().strftime('%Y%m%d%H%M%S')}"
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
                "message": f"File {vcfFile.filename} processed successfully! Reports generated." + 
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