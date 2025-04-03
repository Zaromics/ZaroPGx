import os
import uuid
import shutil
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from typing import Optional
import logging
from datetime import datetime
from pathlib import Path
import asyncio
import requests
import json

from app.api.db import get_db, create_patient, register_genetic_data
from app.api.models import UploadResponse, FileType, WorkflowInfo, FileAnalysis as PydanticFileAnalysis, VCFHeaderInfo
from app.pharmcat_wrapper.pharmcat_client import call_pharmcat_service
from app.api.utils.file_processor import FileProcessor
from ..utils.security import get_current_user, get_optional_user

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize router with no dependencies - will be set at the endpoint level
router = APIRouter(
    prefix="/upload",
    tags=["upload"]
)

# Constants
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/app/data/uploads")
# Ensure upload directory exists
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Initialize file processor
file_processor = FileProcessor(temp_dir=UPLOAD_DIR)

# Initialize job status dictionary
job_status = {}

async def process_file_background(file_path: str, patient_id: str, data_id: str, workflow: dict):
    """
    Process file based on determined workflow
    """
    try:
        logger.info(f"Processing file for patient {patient_id}, file {data_id}")
        logger.info(f"Workflow: {workflow}")

        # Initialize job status
        job_status[data_id] = {
            "status": "processing",
            "percent": 0,
            "message": "Starting analysis...",
            "stage": "Upload",
            "complete": False,
            "data": {}
        }

        # Update status for each stage
        stages = ["Upload", "Analysis", "GATK", "Stargazer", "PharmCAT", "Report"]
        for i, stage in enumerate(stages):
            if i < 2:  # Only execute the first two stages
                progress = int((i / len(stages)) * 100)
                job_status[data_id].update({
                    "percent": progress,
                    "stage": stage,
                    "message": f"Processing {stage} stage..."
                })
                await asyncio.sleep(1)  # Give time for status updates

        if workflow["needs_gatk"]:
            # Call GATK service for processing
            # This would be implemented with a call to GATK
            job_status[data_id].update({
                "percent": 30,
                "stage": "GATK",
                "message": "Processing through GATK..."
            })
            pass

        if workflow["needs_stargazer"]:
            # Call Stargazer service for CYP2D6 analysis
            # This would be implemented with a call to Stargazer
            job_status[data_id].update({
                "percent": 50,
                "stage": "Stargazer",
                "message": "Processing through Stargazer..."
            })
            pass

        if workflow["needs_conversion"]:
            # Convert 23andMe data to VCF
            # This would be implemented with a conversion tool
            pass

        # Update status for PharmCAT stage
        job_status[data_id].update({
            "percent": 70,
            "stage": "PharmCAT",
            "message": "Processing through PharmCAT..."
        })

        # Call PharmCAT service for final analysis
        logger.info(f"Calling PharmCAT service with file: {file_path}")
        try:
            # Use the run_pharmcat_analysis function from main.py if available
            from app.main import run_pharmcat_analysis
            results = await run_pharmcat_analysis(file_path)
        except ImportError:
            # Fall back to direct call if the function isn't available
            logger.warning("run_pharmcat_analysis not found, falling back to direct call")
            results = call_pharmcat_service(file_path)
            
        logger.info(f"PharmCAT processing complete")
        
        try:
            # Update status for Report stage
            job_status[data_id].update({
                "percent": 90,
                "stage": "Report",
                "message": "Generating reports..."
            })
            
            # Ensure results has required structure even if the call failed
            if not isinstance(results, dict):
                logger.error(f"PharmCAT results are not a dictionary: {type(results)}")
                results = {"success": False, "message": "Invalid results format", "data": {}}
                
            if "data" not in results:
                logger.warning("PharmCAT results missing 'data' key, adding empty data structure")
                results["data"] = {}
                
            if "genes" not in results.get("data", {}):
                logger.warning("Missing 'genes' in results data, adding empty list")
                results.setdefault("data", {})["genes"] = []
                
            if "drugRecommendations" not in results.get("data", {}):
                logger.warning("Missing 'drugRecommendations' in results data, adding empty list")
                results.setdefault("data", {})["drugRecommendations"] = []
            
            # Generate reports
            if results.get("success", False):
                # Create directory for reports if it doesn't exist
                reports_dir = Path("/data/reports")
                reports_dir.mkdir(parents=True, exist_ok=True)
                
                # Set up the output paths
                report_path = reports_dir / f"{data_id}_pgx_report.pdf"
                html_report_path = reports_dir / f"{data_id}_pgx_report.html"
                
                # Extract data needed for the reports
                pharmcat_data = results.get("data", {})
                
                # Extract diplotypes from PharmCAT results - should be normalized already
                diplotypes = pharmcat_data.get("genes", [])
                logger.info(f"PharmCAT returned {len(diplotypes)} genes")
                
                # Simple diplotype format conversion if needed
                formatted_diplotypes = []
                for gene in diplotypes:
                    if isinstance(gene, dict):
                        # Handle different possible data structures
                        diplotype_obj = gene.get("diplotype", {})
                        diplotype_name = diplotype_obj
                        if isinstance(diplotype_obj, dict):
                            diplotype_name = diplotype_obj.get("name", "Unknown")
                        
                        phenotype_obj = gene.get("phenotype", {})
                        phenotype_info = phenotype_obj
                        if isinstance(phenotype_obj, dict):
                            phenotype_info = phenotype_obj.get("info", "Unknown")
                        
                        formatted_diplotypes.append({
                            "gene": gene.get("gene", ""),
                            "diplotype": diplotype_name,
                            "phenotype": phenotype_info,
                            "activity_score": diplotype_obj.get("activityScore") if isinstance(diplotype_obj, dict) else None
                        })
                
                # Log the number of diplotypes found
                logger.info(f"Extracted {len(formatted_diplotypes)} formatted diplotypes")
                
                # Extract recommendations from PharmCAT results - should be normalized already
                recommendations = pharmcat_data.get("drugRecommendations", [])
                logger.info(f"PharmCAT returned {len(recommendations)} drug recommendations")
                
                # Simple recommendation format conversion if needed
                formatted_recommendations = []
                for drug in recommendations:
                    if not isinstance(drug, dict):
                        continue
                        
                    # Handle different possible structures for drug name
                    drug_name = drug.get("drug", {})
                    if isinstance(drug_name, dict):
                        drug_name = drug_name.get("name", "Unknown")
                    
                    formatted_recommendations.append({
                        "gene": drug.get("gene", ""),
                        "drug": drug_name,
                        "guideline": drug.get("guideline", ""),
                        "recommendation": drug.get("recommendation", "Unknown"),
                        "classification": drug.get("classification", "Unknown")
                    })
                
                # Log the number of recommendations found
                logger.info(f"Extracted {len(formatted_recommendations)} formatted recommendations")
                
                # Check if the HTML report already exists (may have been copied by PharmCAT wrapper)
                html_report_exists = html_report_path.exists()
                
                # Try to copy the HTML report directly from PharmCAT output if it exists in the data
                if not html_report_exists and "html_report_url" in pharmcat_data:
                    source_path = pharmcat_data["html_report_url"]
                    if source_path.startswith("/"):
                        # This is a direct file path, not a URL
                        source_path = source_path.lstrip("/")
                        full_source_path = Path("/") / source_path
                        if full_source_path.exists():
                            shutil.copy2(full_source_path, html_report_path)
                            html_report_exists = True
                            logger.info(f"Copied HTML report from {full_source_path} to {html_report_path}")
                
                # Log warning if no data was found
                if len(formatted_diplotypes) == 0:
                    logger.warning("No diplotypes found for report. Raw data structure: " + json.dumps(pharmcat_data)[:1000])
                
                if len(formatted_recommendations) == 0:
                    logger.warning("No recommendations found for report. Raw data structure: " + json.dumps(pharmcat_data)[:1000])
                
                # ALWAYS generate our own reports, regardless of whether PharmCAT created them
                from app.reports.generator import generate_pdf_report, create_interactive_html_report
                
                # Generate PDF report (even if HTML report already exists)
                logger.info(f"Generating PDF report to {report_path}")
                generate_pdf_report(
                    patient_id=patient_id,
                    report_id=data_id,
                    diplotypes=formatted_diplotypes,
                    recommendations=formatted_recommendations,
                    report_path=str(report_path)
                )
                
                # Generate HTML report if it doesn't already exist
                if not html_report_exists:
                    logger.info(f"Generating interactive HTML report to {html_report_path}")
                    create_interactive_html_report(
                        patient_id=patient_id,
                        report_id=data_id,
                        diplotypes=formatted_diplotypes,
                        recommendations=formatted_recommendations,
                        output_path=str(html_report_path)
                    )
                
                # Update job status with report URLs
                job_status[data_id].update({
                    "data": {
                        "pdf_report_url": f"/reports/{data_id}_pgx_report.pdf",
                        "html_report_url": f"/reports/{data_id}_pgx_report.html",
                        "diplotypes": formatted_diplotypes,
                        "recommendations": formatted_recommendations
                    }
                })
                
                logger.info(f"Updated job status with report URLs: PDF=/reports/{data_id}_pgx_report.pdf, HTML=/reports/{data_id}_pgx_report.html")
            
            else:
                # Handle PharmCAT error
                error_msg = results.get("message", "Unknown error in PharmCAT processing")
                logger.error(f"PharmCAT processing failed: {error_msg}")
                job_status[data_id].update({
                    "status": "error",
                    "message": f"PharmCAT processing failed: {error_msg}"
                })
                return
                
        except Exception as report_error:
            # Handle report generation error
            logger.error(f"Error generating reports: {str(report_error)}")
            job_status[data_id].update({
                "status": "error",
                "message": f"Error generating reports: {str(report_error)}"
            })
            return
        
        # Mark job as completed
        job_status[data_id].update({
            "status": "completed",
            "percent": 100,
            "stage": "Complete",
            "message": "Analysis completed successfully",
            "complete": True
        })
        
        logger.info(f"Job {data_id} completed successfully")
        
    except Exception as e:
        # Handle any other errors
        logger.error(f"Error processing file: {str(e)}")
        job_status[data_id].update({
            "status": "error",
            "message": f"Error: {str(e)}",
            "complete": True
        })

@router.post("/genomic-data", response_model=UploadResponse)
async def upload_genomic_data(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    patient_identifier: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: str = Depends(get_optional_user)
):
    """
    Upload genomic data file for processing. Supports VCF, BAM, FASTQ, and 23andMe formats.
    """
    try:
        # Generate unique identifiers
        file_id = str(uuid.uuid4())
        if not patient_identifier:
            patient_identifier = f"patient_{uuid.uuid4()}"
        
        # Create patient record
        patient_id = create_patient(db, patient_identifier)
        
        # Create directory for patient if it doesn't exist
        patient_dir = os.path.join(UPLOAD_DIR, str(patient_id))
        os.makedirs(patient_dir, exist_ok=True)
        
        # Save uploaded file
        file_path = os.path.join(patient_dir, f"{file_id}{Path(file.filename).suffix}")
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # Analyze file and determine workflow
        result = await file_processor.process_upload(file_path, None)
        
        if result["status"] == "error":
            raise HTTPException(status_code=400, detail=result["error"])

        # Register genetic data in database
        data_id = register_genetic_data(
            db, 
            patient_id, 
            result["file_analysis"].file_type.value,
            file_path
        )
        
        # Schedule background processing
        background_tasks.add_task(
            process_file_background,
            file_path,
            str(patient_id),
            str(data_id),
            result["workflow"]
        )
        
        # Convert dataclass to Pydantic model
        file_analysis = result["file_analysis"]
        vcf_info = None
        if file_analysis.vcf_info:
            vcf_info = VCFHeaderInfo(
                reference_genome=file_analysis.vcf_info.reference_genome,
                sequencing_platform=file_analysis.vcf_info.sequencing_platform,
                sequencing_profile=file_analysis.vcf_info.sequencing_profile,
                has_index=file_analysis.vcf_info.has_index,
                is_bgzipped=file_analysis.vcf_info.is_bgzipped,
                contigs=file_analysis.vcf_info.contigs,
                sample_count=file_analysis.vcf_info.sample_count,
                variant_count=file_analysis.vcf_info.variant_count
            )
        
        analysis_info = PydanticFileAnalysis(
            file_type=file_analysis.file_type,
            is_compressed=file_analysis.is_compressed,
            has_index=file_analysis.has_index,
            vcf_info=vcf_info,
            file_size=file_analysis.file_size,
            error=file_analysis.error
        )
        
        return UploadResponse(
            file_id=str(data_id),
            file_type=file_analysis.file_type.value,
            status="queued",
            message="File uploaded successfully. Processing started.",
            analysis_info=analysis_info,
            workflow=result["workflow"]
        )
        
    except Exception as e:
        logger.error(f"Error during file upload: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error processing upload: {str(e)}")

@router.get("/status/{file_id}")
async def get_upload_status(file_id: str, db: Session = Depends(get_db)):
    """
    Check the processing status of an uploaded file.
    """
    try:
        # Get status from job_status dictionary
        if file_id not in job_status:
            raise HTTPException(status_code=404, detail="File not found")
            
        status = job_status[file_id]
        
        # Check wrapper service status if still processing
        if status["status"] == "processing":
            try:
                wrapper_response = requests.get("http://pharmcat-wrapper:5000/status")
                if wrapper_response.ok:
                    wrapper_status = wrapper_response.json()
                    # Update status with wrapper information
                    status.update({
                        "message": wrapper_status.get("processing_status", {}).get("message", status["message"]),
                        "progress": wrapper_status.get("processing_status", {}).get("progress", status["percent"])
                    })
            except Exception as e:
                logger.warning(f"Could not get wrapper status: {str(e)}")
        
        # Log current status for debugging
        logger.info(f"Status for job {file_id}: {status}")
        
        # Map the status to the expected response format
        return {
            "file_id": file_id,
            "status": "completed" if status["complete"] else "processing",
            "progress": status["percent"],
            "message": status["message"],
            "current_stage": status["stage"],
            "data": status.get("data", {})
        }
    except Exception as e:
        logger.error(f"Error getting status: {str(e)}")
        raise HTTPException(status_code=404, detail="File not found")

@router.get("/reports/{file_id}")
async def get_report_urls(file_id: str):
    """
    Get the report URLs for a completed job
    """
    try:
        # Check if job exists and is complete
        if file_id not in job_status:
            # Check for direct report files if not in job status
            pdf_path = f"/data/reports/{file_id}_pgx_report.pdf"
            html_path = f"/data/reports/{file_id}_pgx_report.html"
            
            pdf_exists = os.path.exists(pdf_path)
            html_exists = os.path.exists(html_path)
            
            if pdf_exists or html_exists:
                return {
                    "file_id": file_id,
                    "status": "completed",
                    "pdf_report_url": f"/reports/{file_id}_pgx_report.pdf" if pdf_exists else None,
                    "html_report_url": f"/reports/{file_id}_pgx_report.html" if html_exists else None
                }
            else:
                raise HTTPException(status_code=404, detail="Job not found")
                
        # Get data from job status
        status = job_status[file_id]
        data = status.get("data", {})
        
        if not status.get("complete", False):
            return {
                "file_id": file_id,
                "status": "processing",
                "message": status.get("message", "Processing in progress")
            }
            
        return {
            "file_id": file_id,
            "status": "completed" if status.get("success", False) else "failed",
            "pdf_report_url": data.get("pdf_report_url"),
            "html_report_url": data.get("html_report_url")
        }
    
    except Exception as e:
        logger.error(f"Error retrieving report URLs: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error retrieving report URLs: {str(e)}") 