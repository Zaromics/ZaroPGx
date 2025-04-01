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

from app.api.db import get_db, create_patient, register_genetic_data
from app.api.models import UploadResponse, FileType, WorkflowInfo, FileAnalysis as PydanticFileAnalysis, VCFHeaderInfo
from app.pharmcat_wrapper.pharmcat_client import call_pharmcat_service
from app.api.utils.file_processor import FileProcessor
from ..utils.security import get_current_user

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize router
router = APIRouter(
    prefix="/upload",
    tags=["upload"],
    dependencies=[Depends(get_current_user)]
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
            pass

        if workflow["needs_stargazer"]:
            # Call Stargazer service for CYP2D6 analysis
            # This would be implemented with a call to Stargazer
            pass

        if workflow["needs_conversion"]:
            # Convert 23andMe data to VCF
            # This would be implemented with a conversion tool
            pass

        # Call PharmCAT service for final analysis
        results = call_pharmcat_service(file_path)
        logger.info(f"Processing complete: {results}")
        
        # Update job status with results
        if results.get("success"):
            job_status[data_id].update({
                "status": "completed",
                "percent": 100,
                "message": "Analysis completed successfully",
                "stage": "Report",
                "complete": True,
                "data": {
                    "report_url": results.get("data", {}).get("report_url")
                }
            })
        else:
            job_status[data_id].update({
                "status": "failed",
                "percent": 0,
                "message": results.get("message", "Analysis failed"),
                "stage": "Error",
                "complete": True,
                "error": results.get("message")
            })

    except Exception as e:
        logger.error(f"Error processing file: {str(e)}")
        # Update status in database to failed
        if data_id in job_status:
            job_status[data_id].update({
                "status": "failed",
                "percent": 0,
                "message": str(e),
                "stage": "Error",
                "complete": True,
                "error": str(e)
            })

@router.post("/genomic-data", response_model=UploadResponse)
async def upload_genomic_data(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    original_wgs: Optional[UploadFile] = File(None),
    patient_identifier: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    """
    Upload genomic data file for processing. Supports VCF, BAM, FASTQ, and 23andMe formats.
    Optionally accepts original WGS data for improved analysis.
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

        # Save original WGS if provided
        original_wgs_path = None
        if original_wgs:
            original_wgs_path = os.path.join(patient_dir, f"{file_id}_original_wgs{Path(original_wgs.filename).suffix}")
            with open(original_wgs_path, "wb") as buffer:
                shutil.copyfileobj(original_wgs.file, buffer)

        # Analyze file and determine workflow
        result = await file_processor.process_upload(file_path, original_wgs_path)
        
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