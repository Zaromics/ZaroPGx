import logging
import os
import subprocess
import json
import aiohttp
from fastapi import APIRouter, File, UploadFile, Form, BackgroundTasks
from fastapi.responses import JSONResponse
import uuid
from typing import Dict, Optional

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
    """Call variants using GATK through direct Docker command instead of HTTP API."""
    try:
        logging.info(f"Job {job_id} progress: variant_calling - 10% - Calling variants with GATK")
        
        # Define the output VCF path
        output_vcf = os.path.join(os.path.dirname(vcf_file_path), f"{os.path.splitext(os.path.basename(vcf_file_path))[0]}_gatk.vcf")
        
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
        cmd = f"docker exec pgx_gatk gatk HaplotypeCaller -R {reference_path} -I {vcf_file_path} -O {output_vcf}"
        process = subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True)
        
        return output_vcf
    except subprocess.CalledProcessError as e:
        error_msg = f"GATK command failed: {e.stderr}"
        logging.error(error_msg)
        raise Exception(error_msg)
    except Exception as e:
        error_msg = f"Error calling GATK variants: {str(e)}"
        logging.error(error_msg)
        raise Exception(error_msg)

async def call_stargazer(job_id: str, vcf_file: str):
    """Call CYP2D6 star alleles using Stargazer."""
    try:
        logging.info(f"Job {job_id} progress: star_allele_calling - 30% - Calling CYP2D6 star alleles with Stargazer")
        async with aiohttp.ClientSession() as session:
            data = aiohttp.FormData()
            data.add_field('file', open(vcf_file, 'rb'))
            
            async with session.post('http://stargazer:5000/genotype', data=data) as response:
                if response.status == 200:
                    result = await response.json()
                    return result.get('output_file')
                else:
                    error_text = await response.text()
                    raise Exception(f"Stargazer service returned {response.status}: {error_text}")
    except Exception as e:
        error_msg = f"Stargazer service error: {str(e)}"
        logging.error(error_msg)
        raise Exception(error_msg)

async def call_pharmcat(job_id: str, vcf_file: str):
    """Run PharmCAT analysis on VCF file."""
    try:
        logging.info(f"Job {job_id} progress: pharmcat - 60% - Running PharmCAT analysis")
        async with aiohttp.ClientSession() as session:
            data = aiohttp.FormData()
            data.add_field('file', open(vcf_file, 'rb'))
            
            async with session.post('http://pharmcat-wrapper:5000/analyze', data=data) as response:
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
            update_job_progress(job_id, "star_allele_calling", 30, "Calling CYP2D6 star alleles with Stargazer")
        except Exception as e:
            error_msg = f"GATK service error: {str(e)}"
            logging.error(error_msg)
            update_job_progress(job_id, "error", 100, error_msg)
            return
        
        # Call CYP2D6 star alleles with Stargazer
        try:
            stargazer_output = await call_stargazer(job_id, vcf_file)
            update_job_progress(job_id, "pharmcat", 60, "Running PharmCAT analysis")
        except Exception as e:
            error_msg = f"Stargazer service error: {str(e)}"
            logging.error(error_msg)
            update_job_progress(job_id, "error", 100, error_msg)
            return
        
        # Run PharmCAT
        try:
            report_file = await call_pharmcat(job_id, stargazer_output)
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