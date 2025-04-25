import logging
import os
import subprocess
import json
import aiohttp
from fastapi import APIRouter, File, UploadFile, Form, BackgroundTasks
from fastapi.responses import JSONResponse
import uuid
from typing import Dict, Optional
import requests
import asyncio

# Dictionary to store job status
job_status: Dict[str, Dict] = {}

# Define the router
upload_router = APIRouter()

def update_job_progress(job_id: str, stage: str, percentage: int, message: str):
    """Update the progress of a job."""
    if job_id in job_status:
        job_status[job_id].update({
            "stage": stage,
            "percentage": percentage,
            "message": message
        })

async def call_gatk_variants(job_id: str, vcf_file_path: str, reference_genome: str = "hg38"):
    """Call variants using GATK API service with async job handling."""
    logging.info(f"Job {job_id} progress: variant_calling - 10% - Calling variants with GATK")
    
    # Get the GATK service URL from environment or use default
    gatk_api_url = os.environ.get("GATK_API_URL", "http://gatk-api:5000")
    
    # Add retry logic for connection issues
    max_retries = 3
    retry_delay = 5  # seconds
    
    for attempt in range(max_retries):
        try:
            # Create the multipart/form-data request
            files = {'file': open(vcf_file_path, 'rb')}
            data = {'reference_genome': reference_genome}
            
            # Call the GATK API to start the job
            logging.info(f"Attempt {attempt+1}/{max_retries} to start GATK job at {gatk_api_url}")
            
            response = requests.post(
                f"{gatk_api_url}/variant-call",
                files=files,
                data=data,
                timeout=60  # Shorter timeout just for job submission
            )
            response.raise_for_status()
            
            # Get the job ID from the response
            result = response.json()
            gatk_job_id = result.get("job_id")
            
            if not gatk_job_id:
                raise Exception(f"No job ID returned from GATK API: {result}")
            
            logging.info(f"GATK job started with ID: {gatk_job_id}")
            
            # Poll the job status until it completes or fails
            max_poll_attempts = 120  # 2 hours with 60 second intervals
            poll_interval = 60  # seconds
            
            for poll_attempt in range(max_poll_attempts):
                logging.info(f"Polling GATK job {gatk_job_id}, attempt {poll_attempt+1}/{max_poll_attempts}")
                
                # Check job status
                status_response = requests.get(
                    f"{gatk_api_url}/job/{gatk_job_id}", 
                    timeout=30
                )
                status_response.raise_for_status()
                
                job_status = status_response.json()
                status = job_status.get("status")
                
                logging.info(f"GATK job {gatk_job_id} status: {status}")
                
                if status == "completed":
                    # Job completed successfully
                    output_vcf = job_status.get("output_file")
                    if not output_vcf:
                        raise Exception(f"No output file path in completed GATK job: {job_status}")
                    
                    logging.info(f"GATK job completed successfully, output file: {output_vcf}")
                    return output_vcf
                    
                elif status == "error":
                    # Job failed
                    error_msg = job_status.get("error", "Unknown error")
                    raise Exception(f"GATK job failed: {error_msg}")
                    
                elif status in ["pending", "running"]:
                    # Job is still running, wait and try again
                    logging.info(f"GATK job {gatk_job_id} is {status}, waiting {poll_interval} seconds...")
                    await asyncio.sleep(poll_interval)
                    
                    # Update progress message for longer-running jobs
                    if poll_attempt > 0 and poll_attempt % 5 == 0:
                        minutes_elapsed = (poll_attempt * poll_interval) // 60
                        update_job_progress(job_id, "variant_calling", 15, 
                                           f"Calling variants with GATK (running for {minutes_elapsed} minutes)")
                
                else:
                    # Unknown status
                    raise Exception(f"Unknown GATK job status: {status}")
            
            # If we get here, we've exceeded the maximum polling attempts
            raise Exception(f"GATK job timed out after {max_poll_attempts * poll_interval / 60} minutes")
                
        except requests.ConnectionError as e:
            # If this isn't the last attempt, wait and retry
            if attempt < max_retries - 1:
                error_msg = f"Connection error to GATK API (attempt {attempt+1}): {str(e)}. Retrying in {retry_delay} seconds..."
                logging.warning(error_msg)
                await asyncio.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            else:
                # Last attempt failed
                error_msg = f"GATK API connection failed after {max_retries} attempts: {str(e)}"
                logging.error(error_msg)
                raise Exception(error_msg)
                
        except requests.RequestException as e:
            error_msg = f"GATK API error: {str(e)}"
            logging.error(error_msg)
            raise Exception(error_msg)
            
        except Exception as e:
            error_msg = f"Error calling GATK variants: {str(e)}"
            logging.error(error_msg)
            raise Exception(error_msg)

async def call_pypgx(job_id: str, vcf_file: str):
    """Call CYP2D6 star alleles using PyPGx."""
    try:
        logging.info(f"Job {job_id} progress: star_allele_calling - 30% - Calling CYP2D6 star alleles with PyPGx")
        async with aiohttp.ClientSession() as session:
            data = aiohttp.FormData()
            data.add_field('file', open(vcf_file, 'rb'))
            
            async with session.post('http://pypgx:5000/genotype', data=data) as response:
                if response.status == 200:
                    result = await response.json()
                    return result.get('output_file')
                else:
                    error_text = await response.text()
                    raise Exception(f"PyPGx service returned {response.status}: {error_text}")
    except Exception as e:
        error_msg = f"PyPGx service error: {str(e)}"
        logging.error(error_msg)
        raise Exception(error_msg)

async def call_pharmcat(job_id: str, vcf_file: str):
    """Run PharmCAT analysis on VCF file."""
    try:
        logging.info(f"Job {job_id} progress: pharmcat - 60% - Running PharmCAT analysis")
        async with aiohttp.ClientSession() as session:
            data = aiohttp.FormData()
            data.add_field('file', open(vcf_file, 'rb'))
            
            async with session.post('http://pharmcat:5000/analyze', data=data) as response:
                if response.status == 200:
                    result = await response.json()
                    return result.get('report_file')
                else:
                    error_text = await response.text()
                    raise Exception(f"PharmCAT service returned {response.status}: {error_text}")
    except Exception as e:
        error_msg = f"PharmCAT service error: {str(e)}"
        logging.error(error_msg)
        raise Exception(error_msg)

async def process_sample(job_id: str, file_path: str, sample_name: str):
    """Process a sample file through the entire pipeline."""
    try:
        # Initialize job status
        update_job_progress(job_id, "initializing", 0, "Starting analysis pipeline")
        
        # Call variants with GATK
        try:
            vcf_file = await call_gatk_variants(job_id, file_path, "hg38")
            update_job_progress(job_id, "star_allele_calling", 30, "Calling CYP2D6 star alleles with PyPGx")
        except Exception as e:
            error_msg = f"GATK service error: {str(e)}"
            logging.error(error_msg)
            update_job_progress(job_id, "error", 100, error_msg)
            return
        
        # Call CYP2D6 star alleles with PyPGx
        try:
            pypgx_output = await call_pypgx(job_id, vcf_file)
            update_job_progress(job_id, "pharmcat", 60, "Running PharmCAT analysis")
        except Exception as e:
            error_msg = f"PyPGx service error: {str(e)}"
            logging.error(error_msg)
            update_job_progress(job_id, "error", 100, error_msg)
            return
        
        # Run PharmCAT
        try:
            report_file = await call_pharmcat(job_id, pypgx_output)
            update_job_progress(job_id, "complete", 100, f"Analysis complete: {report_file}")
        except Exception as e:
            error_msg = f"PharmCAT service error: {str(e)}"
            logging.error(error_msg)
            update_job_progress(job_id, "error", 100, error_msg)
            return
            
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        logging.error(error_msg)
        update_job_progress(job_id, "error", 100, error_msg)

@upload_router.post("/upload")
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    sample_name: Optional[str] = Form(None)
):
    """Upload a genome file (VCF, BAM) for analysis."""
    if not sample_name:
        sample_name = file.filename
        
    # Generate a unique job ID
    job_id = str(uuid.uuid4())
    
    # Save the uploaded file
    file_path = f"data/uploads/{job_id}_{file.filename}"
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    
    with open(file_path, "wb") as f:
        f.write(await file.read())
    
    # Initialize job status
    job_status[job_id] = {
        "id": job_id,
        "filename": file.filename,
        "sample_name": sample_name,
        "stage": "uploaded",
        "percentage": 0,
        "message": "File uploaded successfully"
    }
    
    # Process the sample in the background
    background_tasks.add_task(process_sample, job_id, file_path, sample_name)
    
    return {"job_id": job_id, "message": "File uploaded and processing started"}

@upload_router.get("/job-status/{job_id}")
async def get_job_status(job_id: str):
    """Get the status of a job."""
    if job_id in job_status:
        return job_status[job_id]
    else:
        return JSONResponse(
            status_code=404,
            content={"error": f"Job {job_id} not found"}
        ) 