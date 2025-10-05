import asyncio
import glob
import json
import logging
import os
import psutil
import shutil
import subprocess
import threading
import time
import uvicorn
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

import requests
from fastapi import FastAPI, HTTPException, Form, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

# Nextflow is the executor, and only the executor, of the pipeline
# Individual containers report their own progress; workflow monitoring is not required

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),  # Console output
        logging.FileHandler('/data/nextflow_progress.log')  # Progress log accessible to main app
    ]
)
logger = logging.getLogger("nextflow")

app = FastAPI(title="Nextflow Pipeline Runner", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global dictionary to track running Nextflow processes
running_jobs: Dict[str, Dict] = {}

def check_external_service_health(service_name: str) -> bool:
    """Check if an external service is healthy."""
    try:
        response = requests.get(f"http://{service_name}:5000/health", timeout=2)
        return response.status_code == 200
    except:
        return False


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}

class NextflowRunRequest(BaseModel):
    input: str
    input_type: str
    patient_id: str
    report_id: Optional[str] = None
    reference: str = "hg38"
    outdir: Optional[str] = None
    job_id: Optional[str] = None
    skip_hla: str = "false"
    skip_pypgx: str = "false"
    workflow_id: Optional[str] = None
    sample_identifier: Optional[str] = None

@app.post("/run")
async def run(request: NextflowRunRequest):
    """Run Nextflow pipeline with workflow monitoring integration."""
    
    if not request.input or not request.input_type or not request.patient_id:
        raise HTTPException(status_code=400, detail="Missing required params: input, input_type, patient_id")

    # Set defaults
    report_id = request.report_id or request.patient_id
    outdir = request.outdir or f"/data/reports/{request.patient_id}"
    job_id = request.job_id or request.patient_id

    # Nextflow is the executor, not a workflow step
    # Individual containers report their own progress
    workflow_client = None
    
    # Check if workflow has been cancelled before starting
    if request.workflow_id:
        try:
            # Use direct database query for better performance

            # Get database connection parameters
            db_user = os.getenv("DB_USER", "cpic_user")
            db_password = os.getenv("DB_PASSWORD", "test123")
            db_host = os.getenv("DB_HOST", "db")
            db_port = os.getenv("DB_PORT", "5432")
            db_name = os.getenv("DB_NAME", "cpic_db")
            
            # Create database URL and engine
            database_url = f"postgresql+psycopg://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
            engine = create_engine(database_url, connect_args={"connect_timeout": 5})
            
            # Query workflow status directly
            with Session(engine) as db:
                query = text("SELECT status FROM workflows WHERE id = :workflow_id")
                result = db.execute(query, {"workflow_id": request.workflow_id}).fetchone()
                
                if result and result[0].lower() == "cancelled":
                    logger.info(f"Workflow {request.workflow_id} is cancelled, aborting Nextflow pipeline")
                    return {"success": False, "error": "Workflow has been cancelled"}
                    
        except Exception as e:
            logger.warning(f"Could not check workflow cancellation status: {e}")
            # Continue execution if we can't check status

    # Create job tracking entry for process monitoring
    job_key = f"{request.patient_id}_{report_id}"
    running_jobs[job_key] = {
        "job_id": job_id,
        "patient_id": request.patient_id,
        "report_id": report_id,
        "workflow_id": request.workflow_id,
        "status": "starting",
        "start_time": datetime.now(timezone.utc).isoformat(),
        "message": "Initializing Nextflow pipeline",
        "cleanup_paths": [
            request.input,
            outdir,
            f"/data/temp/{request.patient_id}",
            f"/data/reports/{request.patient_id}"
        ]
    }

    # Start Nextflow in a separate thread
    thread = threading.Thread(
        target=run_nextflow_job, 
        args=(job_key, request.input, request.input_type, request.patient_id, report_id, request.reference, outdir, request.skip_hla, request.skip_pypgx, request.workflow_id, request.sample_identifier)
    )
    thread.daemon = True
    thread.start()

    return {
        "success": True,
        "job_id": job_id,
        "job_key": job_key,
        "outdir": outdir,
        "message": "Nextflow job started"
    }

def run_nextflow_job(job_key: str, input_path: str, input_type: str, patient_id: str, report_id: str, reference: str, outdir: str, skip_hla: str = 'false', skip_pypgx: str = 'false', workflow_id: Optional[str] = None, sample_identifier: Optional[str] = None):
    """Run Nextflow job in background thread. Nextflow orchestrates individual containers that report their own progress."""
    try:
        # Update job status
        running_jobs[job_key]["status"] = "running"
        running_jobs[job_key]["message"] = "Nextflow pipeline started"

        # Nextflow command - JVM options should be set via environment variables, not command line args
        cmd = [
            'nextflow',
            'run', 'pipelines/pgx/main.nf', '-profile', 'docker',
            '--input', input_path,
            '--input_type', input_type,
            '--patient_id', str(patient_id),
            '--report_id', str(report_id),
            '--reference', reference,
            '--outdir', outdir,
            '--skip_hla', skip_hla,
            '--skip_pypgx', skip_pypgx,
            '-with-report', f"{outdir}/report.html",
            '-with-trace', f"{outdir}/trace.txt",
            '-with-timeline', f"{outdir}/timeline.html",
            '-ansi-log', 'false'
        ]

        # Pass sample_identifier if provided
        if sample_identifier and str(sample_identifier).strip():
            cmd.extend(['--sample_identifier', str(sample_identifier).strip()])
        
        # Set environment variables for workflow_id passing to individual containers
        env = os.environ.copy()
        if workflow_id:
            env['WORKFLOW_ID'] = workflow_id
            env['WORKFLOW_API_BASE'] = 'http://app:8000/api/v1'

        os.makedirs(outdir, exist_ok=True)
        
        # Run Nextflow (non-blocking)
        logger.info(f"Running Nextflow command: {' '.join(cmd)}")
        logger.info(f"Input file path: {input_path}")
        logger.info(f"Input file exists: {os.path.exists(input_path)}")
        if os.path.exists(input_path):
            logger.info(f"Input file size: {os.path.getsize(input_path)} bytes")
        
        # Start Nextflow process (non-blocking)
        proc = subprocess.Popen(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        running_jobs[job_key]["nextflow_process"] = proc
        
        # Wait for process to complete in a separate thread
        def wait_for_completion():
            try:
                stdout, stderr = proc.communicate()
                logger.info(f"Nextflow stdout: {stdout}")
                logger.info(f"Nextflow stderr: {stderr}")
                logger.info(f"Nextflow return code: {proc.returncode}")
                
                # Update final status
                if proc.returncode == 0:
                    running_jobs[job_key]["status"] = "completed"
                    running_jobs[job_key]["message"] = "Nextflow pipeline completed successfully"
                else:
                    running_jobs[job_key]["status"] = "failed"
                    running_jobs[job_key]["message"] = f"Nextflow pipeline failed with return code {proc.returncode}"
                    running_jobs[job_key]["error"] = stderr[-1000:] if stderr else "Unknown error"
                
                running_jobs[job_key]["end_time"] = datetime.now(timezone.utc).isoformat()
                running_jobs[job_key]["returncode"] = proc.returncode
            except Exception as e:
                running_jobs[job_key]["status"] = "failed"
                running_jobs[job_key]["message"] = f"Nextflow job failed: {str(e)}"
                running_jobs[job_key]["error"] = str(e)
                running_jobs[job_key]["end_time"] = datetime.now(timezone.utc).isoformat()
        
        # Start completion monitoring in a separate thread
        completion_thread = threading.Thread(target=wait_for_completion)
        completion_thread.daemon = True
        completion_thread.start()
        
    except Exception as e:
        running_jobs[job_key]["status"] = "failed"
        running_jobs[job_key]["message"] = f"Nextflow job failed: {str(e)}"
        running_jobs[job_key]["error"] = str(e)
        running_jobs[job_key]["end_time"] = datetime.now(timezone.utc).isoformat()

@app.get("/status/{job_key}")
def get_job_status(job_key: str):
    """Get status of a running job."""
    if job_key not in running_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
        
    job = running_jobs[job_key]
    return {
        "job_id": job["job_id"],
        "patient_id": job["patient_id"],
        "report_id": job["report_id"],
        "status": job["status"],
        "message": job["message"],
        "start_time": job["start_time"],
        "end_time": job.get("end_time"),
        "error": job.get("error")
    }

@app.get("/status")
def get_all_jobs():
    """Get status of all jobs."""
    return {
        "jobs": {
            key: {
                "job_id": job["job_id"],
                "patient_id": job["patient_id"],
                "status": job["status"],
                "message": job["message"]
            }
            for key, job in running_jobs.items()
        }
    }

class CancelRequest(BaseModel):
    workflow_id: str
    patient_id: str
    action: str

@app.post("/cancel")
async def cancel_workflow_job(request: CancelRequest):
    """
    Cancel a running workflow job.
    
    This is the standardized cancel endpoint that all containers should implement.
    It should:
    1. Find running processes for the given workflow_id/patient_id
    2. Terminate those processes gracefully
    3. Clean up any temporary files
    4. Return success/failure status
    """
    try:
        workflow_id = request.workflow_id
        patient_id = request.patient_id
        
        logger.info(f"Cancelling workflow {workflow_id} for patient {patient_id}")
        
        # Find and terminate processes
        terminated_count = 0
        
        # Method 1: Check our stored job registry
        job_found = False
        for job_key, job in running_jobs.items():
            if (job.get("patient_id") == patient_id or 
                job.get("workflow_id") == workflow_id or
                workflow_id in job_key or
                patient_id in job_key):
                
                job_found = True
                
                # Check if job can be cancelled
                if job["status"] in ["completed", "failed", "cancelled"]:
                    logger.info(f"Job {job_key} cannot be cancelled. Current status: {job['status']}")
                    continue
                
                # Mark job as cancelled
                job["status"] = "cancelled"
                job["message"] = "Job cancelled by user"
                job["end_time"] = datetime.now(timezone.utc).isoformat()
                
                # Use Nextflow's built-in signal handling for process termination
                if "nextflow_process" in job and job["nextflow_process"]:
                    try:
                        process = job["nextflow_process"]
                        if process.poll() is None:  # Process is still running
                            # Send SIGTERM for graceful termination (Nextflow handles child process cleanup)
                            process.terminate()
                            logger.info(f"Sent SIGTERM to Nextflow process {process.pid} for job {job_key}")
                            terminated_count += 1
                            
                            # Wait for graceful termination (Nextflow will clean up child processes)
                            try:
                                process.wait(timeout=30)  # Give Nextflow time to clean up
                                logger.info(f"Nextflow process {process.pid} terminated gracefully for job {job_key}")
                            except subprocess.TimeoutExpired:
                                # If graceful termination times out, send SIGKILL
                                logger.warning(f"Graceful termination timed out, sending SIGKILL to {process.pid}")
                                process.kill()  # Sends SIGKILL
                                process.wait()
                                logger.info(f"Nextflow process {process.pid} force killed for job {job_key}")
                                
                    except Exception as e:
                        logger.error(f"Error terminating Nextflow process for job {job_key}: {e}")
                        # Try force kill as fallback
                        try:
                            process.kill()
                            process.wait()
                            logger.info(f"Force killed Nextflow process for job {job_key}")
                        except Exception as kill_error:
                            logger.error(f"Error during force kill: {kill_error}")
        
        # Clean up specific tracked file paths from jobs
        for job_key, job in running_jobs.items():
            if (job.get("patient_id") == patient_id or 
                job.get("workflow_id") == workflow_id or
                workflow_id in job_key or
                patient_id in job_key):
                
                # Clean up job-specific files
                cleanup_paths = job.get("cleanup_paths", [])
                for path in cleanup_paths:
                    try:
                        if os.path.exists(path):
                            if os.path.isdir(path):
                                shutil.rmtree(path, ignore_errors=True)
                                logger.info(f"Cleaned up directory: {path}")
                            else:
                                os.remove(path)
                                logger.info(f"Cleaned up file: {path}")
                    except Exception as e:
                        logger.warning(f"Failed to cleanup {path}: {e}")
        
        if not job_found:
            logger.warning(f"No running jobs found for workflow {workflow_id} and patient {patient_id}")
        
        return {
            "success": True,
            "message": f"Cancelled workflow {workflow_id}",
            "terminated_processes": terminated_count,
            "workflow_id": workflow_id,
            "patient_id": patient_id
        }
        
    except Exception as e:
        logger.error(f"Error cancelling workflow {request.workflow_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to cancel workflow: {str(e)}")


@app.post("/cleanup")
def cleanup_old_jobs():
    """Clean up old completed/failed jobs."""
    current_time = datetime.now(timezone.utc)
    cutoff_hours = 24  # Keep jobs for 24 hours
    
    jobs_to_remove = []
    for key, job in running_jobs.items():
        if job["status"] in ["completed", "failed", "cancelled"]:
            end_time = datetime.fromisoformat(job.get("end_time", job["start_time"]))
            if (current_time - end_time).total_seconds() > cutoff_hours * 3600:
                jobs_to_remove.append(key)
    
    for key in jobs_to_remove:
        del running_jobs[key]
    
    return {
        "cleaned_up": len(jobs_to_remove),
        "remaining_jobs": len(running_jobs)
    }

if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=5055)
    