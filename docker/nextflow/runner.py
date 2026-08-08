import asyncio
import glob
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uvicorn
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, Optional

import requests
from fastapi import FastAPI, HTTPException, Form, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

# Nextflow is the executor, and only the executor, of the pipeline
# Individual containers report their own progress; workflow monitoring is not required

# Configure logging
# /data is the volume shared with the main app, so the progress log has to be size
# bounded (252) - an unrotated handler here grows until the shared volume fills.
PROGRESS_LOG_PATH = os.getenv('NEXTFLOW_PROGRESS_LOG', '/data/nextflow_progress.log')
PROGRESS_LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB per file
PROGRESS_LOG_BACKUP_COUNT = 5  # 60 MB ceiling for the whole set

_log_handlers = [logging.StreamHandler()]  # Console output
_progress_log_error = None
try:
    _log_handlers.append(
        RotatingFileHandler(
            PROGRESS_LOG_PATH,
            maxBytes=PROGRESS_LOG_MAX_BYTES,
            backupCount=PROGRESS_LOG_BACKUP_COUNT,
        )  # Progress log accessible to main app
    )
except OSError as exc:
    # /data only exists inside the container; outside it, log to console only so the
    # module stays importable (tests exercise the request model and argv builder).
    # In the container this path means the shared volume is missing - say so loudly
    # rather than degrading to console-only in silence.
    _progress_log_error = exc

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=_log_handlers
)
logger = logging.getLogger("nextflow")

if _progress_log_error is not None:
    logger.warning(
        "Could not open progress log %s (%s) - logging to console only. "
        "Inside the container this means the /data volume is not mounted, and the "
        "main app will not see pipeline progress.",
        PROGRESS_LOG_PATH,
        _progress_log_error,
    )

app = FastAPI(title="Nextflow Pipeline Runner", version="0.2.8", description="REST API wrapper around Nextflow for the ZaroPGx pipeline")
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

# Strings that get interpolated into main.nf's bash `shell:` blocks must not carry
# shell metacharacters: Nextflow escapes `path` inputs but not `val`/`params` strings,
# so a `"` in one of them breaks out of the quoting and the rest runs as code - inside
# a container holding the Docker socket. sample_identifier is additionally passed via
# the environment (never interpolated), but reference IS still interpolated, so this
# allowlist is what keeps it safe. The alphabet matches the app's boundary validator:
# alphanumerics plus dot/underscore/hyphen, 1-64 chars, first char alphanumeric.
_PIPELINE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _require_pipeline_token(value: str, field_name: str) -> str:
    if not _PIPELINE_TOKEN_RE.match(value or ""):
        raise ValueError(
            f"invalid {field_name}: only letters, digits, '.', '_' and '-' are "
            "allowed (1-64 characters, first character alphanumeric)"
        )
    return value

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
    # 406: the app posts skip_gatk/skip_report too. Undeclared fields are silently
    # dropped by pydantic, which is how the GATK and Report toggles used to be lost.
    skip_gatk: str = "false"
    skip_report: str = "false"
    sample_identifier: Optional[str] = None
    pharmcat_absent_to_ref: str = "false"
    pharmcat_unspecified_to_ref: str = "false"

    @field_validator("reference")
    @classmethod
    def _validate_reference(cls, v: str) -> str:
        # reference is interpolated verbatim into several curl argv in main.nf; reject
        # anything outside the safe alphabet before it can reach the shell.
        return _require_pipeline_token((v or "").strip(), "reference")

    @field_validator("sample_identifier")
    @classmethod
    def _validate_sample_identifier(cls, v: Optional[str]) -> Optional[str]:
        # Optional: an absent/blank identifier is fine. When present it must be a safe
        # token even though it now travels via the environment - defence in depth, and
        # it keeps a hostile value from ever being persisted or logged downstream.
        if v is None or not str(v).strip():
            return None
        return _require_pipeline_token(str(v).strip(), "sample_identifier")

@app.post("/run")
async def run(request: NextflowRunRequest):
    """Run Nextflow pipeline with workflow monitoring integration."""
    
    if not request.input or not request.input_type or not request.patient_id:
        raise HTTPException(status_code=400, detail="Missing required params: input, input_type, patient_id")

    # Set defaults (display report_id = job_id; nest under patient/job)
    report_id = request.report_id or request.job_id or request.patient_id
    outdir = request.outdir or f"/data/reports/{request.patient_id}/{report_id}"
    job_id = request.job_id or request.patient_id

    # Nextflow is the executor, not a workflow step
    # Individual containers report their own progress
    # Check if workflow has been cancelled before starting
    if request.job_id:
        try:
            # Use direct database query for better performance

            # Get database connection parameters (no shared default password)
            db_user = os.getenv("DB_USER", "zaropgx_user")
            db_password = os.getenv("DB_PASSWORD")
            db_host = os.getenv("DB_HOST", "db")
            db_port = os.getenv("DB_PORT", "5432")
            db_name = os.getenv("DB_NAME", "zaropgx_db")
            if not db_password:
                raise RuntimeError(
                    "DB_PASSWORD is not set in the nextflow container; "
                    "set it in .env and recreate the stack."
                )

            # Create database URL and engine
            database_url = (
                f"postgresql+psycopg://{db_user}:{db_password}"
                f"@{db_host}:{db_port}/{db_name}"
            )
            engine = create_engine(database_url, connect_args={"connect_timeout": 5})
            
            # Query job status directly
            with Session(engine) as db:
                query = text("SELECT status FROM jobs WHERE id = :job_id")
                result = db.execute(query, {"job_id": request.job_id}).fetchone()
                
                if result and result[0].lower() == "cancelled":
                    logger.info(f"Job {request.job_id} is cancelled, aborting Nextflow pipeline")
                    return {"success": False, "error": "Job has been cancelled"}
                    
        except Exception as e:
            logger.warning(f"Could not check job cancellation status: {e}")
            # Continue execution if we can't check status

    # job_key identifies a Nextflow runner process; distinct from ORM jobs.id / API job_id (137c).
    job_key = f"{request.patient_id}_{report_id}"
    running_jobs[job_key] = {
        "job_id": job_id,
        "patient_id": request.patient_id,
        "report_id": report_id,
        "status": "starting",
        "start_time": datetime.now(timezone.utc).isoformat(),
        "message": "Initializing Nextflow pipeline",
        "cleanup_paths": [
            request.input,
            outdir,
            f"/data/temp/{request.patient_id}",
            f"/data/reports/{request.patient_id}/{report_id}",
            f"/data/reports/{request.patient_id}",
        ]
    }

    # Start Nextflow in a separate thread
    thread = threading.Thread(
        target=run_nextflow_job, 
        args=(job_key, request.input, request.input_type, request.patient_id, report_id, request.reference, outdir, request.skip_hla, request.skip_pypgx, request.job_id, request.sample_identifier, request.pharmcat_absent_to_ref, request.pharmcat_unspecified_to_ref, request.skip_gatk, request.skip_report)
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

def summarize_nextflow_failure(stdout: Optional[str], stderr: Optional[str], tail: int = 1000) -> str:
    """Build the user-facing reason a Nextflow run failed.

    Nextflow writes its console output to stdout - including the text of any
    error() raised by the pipeline itself, such as the skip_gatk guard in
    main.nf. stderr usually carries nothing but the 'a newer version is
    available' nag. Reporting stderr alone therefore handed the user an upgrade
    advertisement as the reason their job failed; stdout has to be included or
    the guard's message never reaches them (406).

    Both streams are kept and each is truncated to its tail, because whatever
    ended the run is at the end of it.
    """
    parts = []
    for label, stream in (('stdout', stdout), ('stderr', stderr)):
        if stream and stream.strip():
            parts.append(f"{label}:\n{stream.strip()[-tail:]}")
    return "\n\n".join(parts) if parts else "Unknown error"

def build_nextflow_command(input_path: str, input_type: str, patient_id: str, report_id: str, reference: str, outdir: str, skip_hla: str = 'false', skip_pypgx: str = 'false', skip_gatk: str = 'false', skip_report: str = 'false', pharmcat_absent_to_ref: str = 'false', pharmcat_unspecified_to_ref: str = 'false'):
    """Build the Nextflow argv. Pure and side-effect free so it can be unit tested.

    Every skip flag the request model accepts must be emitted here; a flag that
    stops at the runner is indistinguishable, from the user's side, from a toggle
    that does nothing (406).

    sample_identifier is DELIBERATELY not on the argv: it is a user-controlled string
    and main.nf now reads it from the SAMPLE_IDENTIFIER environment variable (set in
    run_nextflow_job) instead of interpolating it into the shell. Passing it as a
    --param would put an attacker-controlled value back onto a path that Nextflow does
    not escape.
    """
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
        '--skip_gatk', skip_gatk,
        '--skip_report', skip_report,
        '-with-report', f"{outdir}/report.html",
        '-with-trace', f"{outdir}/trace.txt",
        '-with-timeline', f"{outdir}/timeline.html",
        '-ansi-log', 'false'
    ]

    # NOTE: sample_identifier is intentionally NOT emitted here - it reaches main.nf as
    # the SAMPLE_IDENTIFIER environment variable (see run_nextflow_job), not as a param.

    cmd.extend([
        '--pharmcat_absent_to_ref', pharmcat_absent_to_ref,
        '--pharmcat_unspecified_to_ref', pharmcat_unspecified_to_ref,
    ])

    return cmd

def run_nextflow_job(job_key: str, input_path: str, input_type: str, patient_id: str, report_id: str, reference: str, outdir: str, skip_hla: str = 'false', skip_pypgx: str = 'false', job_id: Optional[str] = None, sample_identifier: Optional[str] = None, pharmcat_absent_to_ref: str = 'false', pharmcat_unspecified_to_ref: str = 'false', skip_gatk: str = 'false', skip_report: str = 'false'):
    """Run Nextflow job in background thread. Nextflow orchestrates individual containers that report their own progress."""
    try:
        # Update job status
        running_jobs[job_key]["status"] = "running"
        running_jobs[job_key]["message"] = "Nextflow pipeline started"

        cmd = build_nextflow_command(
            input_path=input_path,
            input_type=input_type,
            patient_id=patient_id,
            report_id=report_id,
            reference=reference,
            outdir=outdir,
            skip_hla=skip_hla,
            skip_pypgx=skip_pypgx,
            skip_gatk=skip_gatk,
            skip_report=skip_report,
            pharmcat_absent_to_ref=pharmcat_absent_to_ref,
            pharmcat_unspecified_to_ref=pharmcat_unspecified_to_ref,
        )

        # Set environment variables for job_id passing to individual containers
        env = os.environ.copy()
        if job_id:
            env['JOB_ID'] = job_id
            env['JOB_API_BASE'] = 'http://app:8000/api/v1'

        # sample_identifier travels to PharmCATRun through the environment, exactly as
        # JOB_ID does, and is referenced there as "$SAMPLE_IDENTIFIER" - never
        # interpolated into the shell with !{...}. A bash variable expansion is inert
        # data whatever it holds, so this is the structural fix for the injection, not a
        # cleverer escape. The value is already allowlist-validated by NextflowRunRequest.
        if sample_identifier and str(sample_identifier).strip():
            env['SAMPLE_IDENTIFIER'] = str(sample_identifier).strip()

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
                    running_jobs[job_key]["error"] = summarize_nextflow_failure(stdout, stderr)
                
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
    job_id: str
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
        job_id = request.job_id
        patient_id = request.patient_id
        
        logger.info(f"Cancelling job {job_id} for patient {patient_id}")
        
        # Find and terminate processes
        terminated_count = 0
        
        # Method 1: Check our stored job registry
        job_found = False
        for job_key, job in running_jobs.items():
            if (job.get("patient_id") == patient_id or
                job.get("job_id") == job_id or
                job.get("workflow_id") == job_id or
                job_id in job_key or
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
                job.get("job_id") == job_id or
                job.get("workflow_id") == job_id or
                job_id in job_key or
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
            logger.warning(f"No running jobs found for job {job_id} and patient {patient_id}")
        
        return {
            "success": True,
            "message": f"Cancelled job {job_id}",
            "terminated_processes": terminated_count,
            "job_id": job_id,
            "patient_id": patient_id
        }
        
    except Exception as e:
        logger.error(f"Error cancelling job {request.job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to cancel job: {str(e)}")


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
    