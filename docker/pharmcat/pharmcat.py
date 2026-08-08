#!/usr/bin/env python3
"""
PharmCAT Wrapper Service for ZaroPGx

This service provides a REST API wrapper around PharmCAT, enabling:
- VCF file upload and processing
- PharmCAT pipeline execution (Named Allele Matcher → Phenotyper → Reporter)
- Multiple output formats: JSON, HTML, and TSV
- Integration with the ZaroPGx reporting system
- Workflow monitoring integration

Output Formats:
- JSON (.report.json): Complete gene calls, phenotypes, and drug recommendations. Tedious to parse.
- HTML (.report.html): Human-readable report with formatting
- TSV (.report.tsv): Calls only Tab-separated values for easy parsing and integration

The service automatically detects and processes all available output formats,
providing both file URLs and content in the API response.
"""

import os
import json
import logging
from logging.handlers import RotatingFileHandler
import subprocess
import gzip
import shutil
from pathlib import Path
import psutil
import time
from datetime import datetime
import re
import tempfile
import uuid
import traceback
from typing import Dict, Any, Optional

import uvicorn
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Import pysam for VCF sample extraction
try:
    import pysam  # type: ignore
    PYSAM_AVAILABLE = True
except ImportError:
    PYSAM_AVAILABLE = False
    # Note: logger will be defined later in the file

# Import shared workflow client for integration
import sys
sys.path.append('/job-client')
from job_client import JobClient, create_job_client  # pyright: ignore[reportMissingImports]

# --------------------------------------------------------------------------
# Bounded logging (BACKLOG 252). Same block, same values, same failure
# behaviour in every ZaroPGx sidecar: docker/gatk-api/gatk_api.py,
# docker/nextflow/runner.py, docker/pypgx/pypgx_wrapper.py and
# docker/zarohla/app.py. tests/test_log_rotation_252.py pins all five against
# one rule, so an edit here that is not made there fails the suite.
#
# It is duplicated rather than imported: these are five separate images, and
# the logging block runs before sys.path is extended with the shared
# /job-client directory. See gatk_api.py for the full argument.
#
# /data is the volume shared with the main app, so the progress log has to be
# size bounded - an unrotated handler here grows until the shared volume fills.
# 10 MiB x 5 backups caps the destination at 60 MiB.
PROGRESS_LOG_PATH = os.environ.get('PHARMCAT_PROGRESS_LOG', '/data/pharmcat_progress.log')
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 5

# (path, error) for every destination that could not be opened. Reported by
# _warn_about_unopened_logs() once `logger` exists -- the failure is loud, but
# it is not fatal.
_log_file_errors = []


def _bounded_file_handler(path):
    """Return a size-capped handler for `path`, or None if it cannot be opened.

    A log destination that is missing or read-only must not take the service down at
    import time. Building this handler inline in basicConfig(), as this module used
    to, meant a missing /data killed the container inside logging setup -- before
    `logger` existed, so nothing ever said why, and with `restart: unless-stopped`
    that became a silent crash loop. The handler is not the job: if /data really is
    unmounted, the first read or write of actual pipeline data fails with an error
    that names the real operation. Degrading keeps stdout carrying the full stream
    for `docker logs`, and the caller warns.
    """
    try:
        return RotatingFileHandler(
            path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT
        )
    except OSError as exc:
        _log_file_errors.append((path, exc))
        return None


def _warn_about_unopened_logs(log):
    """Say loudly, once logging works, which destinations were skipped."""
    for path, exc in _log_file_errors:
        log.warning(
            "Could not open log file %s (%s) - logging to console only. If that path "
            "is on /data, the shared volume is not mounted and the main app will not "
            "see this service's progress; stdout still carries the full stream.",
            path,
            exc,
        )


_log_handlers = [logging.StreamHandler()]  # Console output
# Progress log accessible to main app
_progress_handler = _bounded_file_handler(PROGRESS_LOG_PATH)
if _progress_handler is not None:
    _log_handlers.append(_progress_handler)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=_log_handlers
)
logger = logging.getLogger("pharmcat")
_warn_about_unopened_logs(logger)


def _translate_uploaded_outside_tsv(outside_path: str) -> None:
    """Best-effort synonym-gene rewrite; never raise into the request handler."""
    try:
        import sys

        if "/lexicon-lib" not in sys.path:
            sys.path.insert(0, "/lexicon-lib")
        # Repo / test layout fallback
        repo_utils = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "app",
            "utils",
        )
        if os.path.isdir(repo_utils) and repo_utils not in sys.path:
            sys.path.insert(0, repo_utils)
        from allele_translate import translate_outside_tsv_file

        translate_outside_tsv_file(outside_path)
        logger.info("Applied PyPGx→PharmCAT synonym translation to %s", outside_path)
    except Exception as e:
        logger.warning(
            "Outside-call synonym translation skipped for %s: %s", outside_path, e
        )

# Log pysam availability after logger is defined
if not PYSAM_AVAILABLE:
    logger.warning("pysam not available - VCF sample extraction will use bcftools fallback")

# Initialize FastAPI app
app = FastAPI(
    title="PharmCAT Wrapper API",
    description="REST API wrapper around PharmCAT for the ZaroPGx pipeline",
    version="0.2.8"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Data directory for VCF files
DATA_DIR = os.environ.get("DATA_DIR", "/data")
# Temporary directory for processing files
TEMP_DIR = os.environ.get("TEMP_DIR", "/tmp/pharmcat")
# Path to the PharmCAT JAR file (for backward compatibility)
PHARMCAT_JAR = os.environ.get("PHARMCAT_JAR", "/pharmcat/pharmcat.jar")
# Path to the PharmCAT pipeline directory
PHARMCAT_PIPELINE_DIR = os.environ.get("PHARMCAT_PIPELINE_DIR", "/pharmcat/pipeline")
# Path to PharmCAT reference files (where PharmCAT expects them)
PHARMCAT_REFERENCE_DIR = os.environ.get("PHARMCAT_REFERENCE_DIR", "/pharmcat")
# OUTSIDE_CALLS_OVERRIDE_PATH used to be read here, for the manual
# HLA/MT-RNR1/CYP2D6 override. It is gone with the branch that used it -- see
# /genotype, and app/utils/outside_calls_override.py, which is now the single
# place the override file is located and the flag that gates it is parsed.

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)
# Ensure temp directory has proper permissions
os.chmod(TEMP_DIR, 0o777)

print(f"Starting PharmCAT wrapper service with DATA_DIR={DATA_DIR}, TEMP_DIR={TEMP_DIR}")
print(f"PharmCAT JAR location: {PHARMCAT_JAR}")

# Add these global variables after the existing ones
processing_status = {
    "current_file": None,
    "start_time": None,
    "status": "idle",
    "progress": 0,
    "last_error": None
}

# Store running processes by workflow_id for cancellation
running_processes: Dict[str, Dict[str, Any]] = {}

class CancelRequest(BaseModel):
    job_id: str
    patient_id: str
    action: str

def register_process(workflow_id: str, pid: int, process_info: Dict[str, Any] = None):
    """Register a running process for a workflow."""
    running_processes[workflow_id] = {
        "pid": pid,
        "start_time": time.time(),
        **(process_info or {})
    }
    logger.info(f"Registered process {pid} for workflow {workflow_id}")

def unregister_process(job_id: str):
    """Unregister a process when it completes normally."""
    if job_id in running_processes:
        del running_processes[job_id]
        logger.info(f"Unregistered process for job {job_id}")

def extract_sample_id_from_vcf(vcf_path: str) -> Optional[str]:
    """
    Extract the sample ID from a VCF file.
    
    Args:
        vcf_path: Path to the VCF file
        
    Returns:
        Sample ID from the VCF file, or None if not found
    """
    try:
        # Try using pysam first (more reliable)
        if PYSAM_AVAILABLE:
            try:
                with pysam.VariantFile(vcf_path) as vcf:
                    samples = list(vcf.header.samples)
                    if samples:
                        sample_id = samples[0]  # Use first sample
                        logger.info(f"Extracted sample ID from VCF using pysam: {sample_id}")
                        return sample_id
            except Exception as e:
                logger.warning(f"Failed to extract sample ID using pysam: {e}")
        
        # Fallback to bcftools
        try:
            # Use bcftools query to get sample names
            cmd = ["bcftools", "query", "-l", vcf_path]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0 and result.stdout.strip():
                samples = result.stdout.strip().split('\n')
                if samples and samples[0]:
                    sample_id = samples[0].strip()
                    logger.info(f"Extracted sample ID from VCF using bcftools: {sample_id}")
                    return sample_id
            else:
                logger.warning(f"bcftools query failed: {result.stderr}")
        except Exception as e:
            logger.warning(f"Failed to extract sample ID using bcftools: {e}")
        
        # Final fallback: try to parse VCF header manually
        try:
            with open(vcf_path, 'r') as f:
                for line in f:
                    if line.startswith('#CHROM'):
                        # Parse the header line to get sample names
                        parts = line.strip().split('\t')
                        if len(parts) > 9:  # Should have at least CHROM, POS, ID, REF, ALT, QUAL, FILTER, INFO, FORMAT, and sample(s)
                            sample_id = parts[9]  # First sample is at index 9
                            logger.info(f"Extracted sample ID from VCF header manually: {sample_id}")
                            return sample_id
                        break
        except Exception as e:
            logger.warning(f"Failed to extract sample ID manually: {e}")
        
        logger.warning(f"Could not extract sample ID from VCF file: {vcf_path}")
        return None
        
    except Exception as e:
        logger.error(f"Error extracting sample ID from VCF: {e}")
        return None

# Simple home endpoint
@app.get("/")
def home():
    return {
        "service": "PharmCAT Wrapper",
        "status": "running",
        "endpoints": ["/", "/health", "/genotype", "/status"],
        "workflow_monitoring": "enabled"
    }

# Health check endpoint
@app.get("/health")
def health_check():
    """API endpoint to check if the service is running."""
    logger.info("Health check called")
    
    # Check if the JAR file exists
    jar_exists = os.path.exists(PHARMCAT_JAR)
    
    # Test if pharmcat_pipeline command is working
    pharmcat_working = False
    pharmcat_version = "Unknown"
    try:
        result = subprocess.run(
            ["pharmcat_pipeline", "--version"], 
            capture_output=True, 
            text=True, 
            timeout=10
        )
        if result.returncode == 0:
            pharmcat_working = True
            pharmcat_version = result.stdout.strip()
    except Exception as e:
        logger.warning(f"PharmCAT version check failed: {str(e)}")
    
    return {
        "status": "ok" if jar_exists and pharmcat_working else "degraded",
        "service": "pharmcat",
        "java_version": subprocess.check_output(["java", "-version"], stderr=subprocess.STDOUT).decode(),
        "pharmcat_jar_exists": jar_exists,
        "pharmcat_jar_path": PHARMCAT_JAR,
        "pharmcat_working": pharmcat_working,
        "pharmcat_version": pharmcat_version,
        "data_dir_exists": os.path.exists(DATA_DIR),
        "data_dir_contents": os.listdir(DATA_DIR)
    }

@app.post("/genotype")
async def process_genotype(
    file: UploadFile = File(...),
    patient_id: Optional[str] = Form(None),
    report_id: Optional[str] = Form(None),
    job_id: Optional[str] = Form(None),
    step_name: Optional[str] = Form("pharmcat_analysis"),
    outside_tsv: Optional[UploadFile] = File(None),
    sample_identifier: Optional[str] = Form(None),
    pharmcat_absent_to_ref: Optional[str] = Form(None),
    pharmcat_unspecified_to_ref: Optional[str] = Form(None),
):
    """
    API endpoint to process VCF files with PharmCAT.
    """
    try:
        # Validate file
        if file.filename == '':
            raise HTTPException(status_code=400, detail="Empty filename")
            
        if not file.filename.endswith(('.vcf', '.vcf.gz', '.vcf.bgz')):
            raise HTTPException(status_code=400, detail="File must be a VCF (.vcf or .vcf.gz or .vcf.bgz)")
        
        # Initialize workflow client if job_id is provided
        job_client = None
        if job_id:
            try:
                job_client = JobClient(job_id=job_id, step_name=step_name)
                
                # Check if workflow has been cancelled before starting
                if await job_client.is_job_cancelled():
                    logger.info(f"Workflow {job_id} is cancelled, aborting PharmCAT processing")
                    return {"success": False, "error": "Workflow has been cancelled"}
                
                await job_client.start_step(f"Starting PharmCAT analysis for {file.filename}")
                await job_client.log_progress(f"Processing {file.filename} with PharmCAT", {
                    "filename": file.filename,
                    "file_size": 0  # Will be updated after file is saved
                })
            except Exception as e:
                logger.warning(f"Failed to initialize workflow client: {e}")
                job_client = None
        
        # Save file to temporary directory
        file_path = os.path.join(TEMP_DIR, file.filename)
        logger.info(f"Saving file to: {file_path}")
        logger.info(f"TEMP_DIR exists: {os.path.exists(TEMP_DIR)}")
        logger.info(f"TEMP_DIR permissions: {oct(os.stat(TEMP_DIR).st_mode) if os.path.exists(TEMP_DIR) else 'N/A'}")
        
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)
        
        logger.info(f"VCF file saved to {file_path}")
        logger.info(f"File size after save: {os.path.getsize(file_path)}")
        logger.info(f"File permissions: {oct(os.stat(file_path).st_mode)}")
        
        # Update workflow with file information
        if job_client:
            file_size = os.path.getsize(file_path)
            await job_client.log_progress(f"File uploaded: {file_size} bytes", {
                "file_size_bytes": file_size,
                "file_path": file_path
            })
        
        # Now process the file with PharmCAT
        try:
            # Create a temporary directory for this job
            with tempfile.TemporaryDirectory() as temp_dir:
                # Determine base name for file naming (existing precedence)
                # 1) patient_id 2) report_id 3) random
                if patient_id:
                    base_name = str(patient_id)
                    logger.info(f"Using provided patient ID for base name: {base_name}")
                elif report_id:
                    base_name = str(report_id)
                    logger.info(f"Using provided report ID for base name: {base_name}")
                else:
                    # Generate a random ID if none provided
                    base_name = str(uuid.uuid4())[:8]
                    logger.info(f"Generated random base name: {base_name}")
                
                # Log user sample identifier for display purposes only
                if sample_identifier and str(sample_identifier).strip():
                    logger.info(f"User sample identifier (display only): {sample_identifier}")
                
                # Save the uploaded file to temp directory with proper extension handling
                if file.filename.endswith('.vcf.gz'):
                    # For compressed VCF files, keep the original extension
                    vcf_path = os.path.join(temp_dir, f"{base_name}.vcf.gz")
                    shutil.copy2(file_path, vcf_path)
                    logger.info(f"Copied compressed VCF file to temp directory: {vcf_path}")
                elif file.filename.endswith('.vcf.bgz'):
                    # For compressed VCF files, keep the original extension
                    vcf_path = os.path.join(temp_dir, f"{base_name}.vcf.bgz")
                    shutil.copy2(file_path, vcf_path)
                    logger.info(f"Copied compressed VCF file to temp directory: {vcf_path}")
                else:
                    # For uncompressed VCF files, use .vcf extension
                    vcf_path = os.path.join(temp_dir, f"{base_name}.vcf")
                    shutil.copy2(file_path, vcf_path)
                    logger.info(f"Copied VCF file to temp directory: {vcf_path}")
                
                # Handle outside TSV file.
                #
                # This service does NOT read OUTSIDECALLSOVERRIDE. It used to, with
                # its own `os.environ.get(...).lower()` parse that -- unlike the
                # app's -- did not .strip(), so `OUTSIDECALLSOVERRIDE='true '` in a
                # .env file disabled the override here while enabling it there.
                # That branch is deleted rather than repaired, for two reasons:
                #
                #  * It was unreachable under Compose. compose.yml passes the
                #    variable to no service but `app` (the only one with an
                #    `env_file:`), so `OUTSIDECALLSOVERRIDE` is unset in this
                #    container and the branch was unconditionally False.
                #  * Repairing it would mean a second parser for a flag the repo
                #    has deliberately consolidated into exactly one resolver,
                #    app/utils/outside_calls_override.py:is_override_enabled() --
                #    and a second parser on the far side of an image boundary,
                #    where no test can cross-check it against the first.
                #
                # Where the override is applied now, stated precisely rather than
                # as "it still works end to end":
                #
                #  * app/pharmcat/pharmcat_client.py:614 resolves it with that single
                #    resolver and posts the file as the `outside_tsv` multipart part,
                #    which the branch below handles -- and which also applies the
                #    PyPGx->PharmCAT synonym translation the deleted branch skipped.
                #  * The Nextflow path does NOT go through that client.
                #    pipelines/pgx/main.nf POSTs to http://pharmcat:5000/genotype
                #    directly, with an `outside_tsv` assembled from PyPGx and HLA
                #    output only. So on that path -- the primary production path --
                #    a manual lexicon/outside_calls.tsv is applied by neither side.
                #
                # That gap is NOT introduced here: the branch removed above was
                # already inert, because the variable never reached this container.
                # It is a real, separate gap in the Nextflow lane and is worth its
                # own backlog item; deleting dead code is not the place to close it.
                outside_path = os.path.join(temp_dir, f"{base_name}.outside.tsv")

                if outside_tsv:
                    with open(outside_path, "wb") as f:
                        content = await outside_tsv.read()
                        f.write(content)
                    logger.info(f"Saved uploaded outside call TSV to {outside_path}")
                    _translate_uploaded_outside_tsv(outside_path)
                else:
                    outside_path = None
                    logger.info("No outside calls file provided")
                
                # Extract actual sample ID from VCF file for PharmCAT -s parameter
                vcf_sample_id = extract_sample_id_from_vcf(vcf_path)
                if vcf_sample_id:
                    logger.info(f"Extracted VCF sample ID for PharmCAT: {vcf_sample_id}")
                else:
                    logger.warning("Could not extract sample ID from VCF file - PharmCAT may fail")

                # PharmCAT pipeline does not take an explicit CLI arg for outside calls.
                # It detects outside calls based on a file naming convention in the same directory.
                # For single-sample VCFs, <base>.outside.tsv works.
                # For multi-sample VCFs, <base>.<sample_id>.outside.tsv is accepted.
                if outside_path and os.path.exists(outside_path) and vcf_sample_id:
                    multisample_outside_path = os.path.join(temp_dir, f"{base_name}.{vcf_sample_id}.outside.tsv")
                    try:
                        shutil.copy2(outside_path, multisample_outside_path)
                        logger.info(f"Also wrote outside calls file for multi-sample naming: {multisample_outside_path}")
                    except Exception as e:
                        logger.warning(f"Failed to write multi-sample outside calls file: {e}")
                
                # Update processing status
                processing_status.update({
                    "status": "processing",
                    "progress": 10,
                    "message": "Starting PharmCAT pipeline"
                })
                
                # Update workflow with processing start
                if job_client:
                    await job_client.log_progress("Starting PharmCAT pipeline", {
                        "base_name": base_name,
                        "vcf_path": vcf_path
                    })
                    
                    # Update step with progress for proper mapping
                    await job_client.update_step_status(
                        "running",
                        "Starting PharmCAT pipeline",
                        output_data={"progress_percent": 10}
                    )
                
                # Run PharmCAT
                logger.info(f"Running PharmCAT on {vcf_path}")
                
                # Configure paths for PharmCAT
                output_dir = temp_dir
                
                # Before running PharmCAT, set up Java options for memory management
                java_options = "-Xmx2g"
                
                # Build the PharmCAT command using pharmcat_pipeline as per official documentation
                # Format: pharmcat_pipeline [options] <input_file>
                # The default pipeline runs: Named Allele Matcher → Phenotyper → Reporter
                # To get drug recommendations, we need the Reporter step
                pharmcat_cmd = [
                    "pharmcat_pipeline",
                    # "-G",  # Bypass gVCF check
                    "-v",  # Verbose output
                    "-o", output_dir,  # Specify output directory explicitly
                    "-reporterJson",  # Generate reporter JSON with drug recommendations
                    "-reporterHtml",  # Generate HTML report
                    "-reporterCallsOnlyTsv",   # Generate TSV report for easy parsing
                ]
                
                # Form overrides env for assume-ref flags (helpers from /assume-ref-lib)
                from pharmcat_assume_ref import (
                    resolve_assume_ref_flags,
                    pharmcat_cli_ref_flags,
                )

                absent, unspecified = resolve_assume_ref_flags(
                    form_absent=pharmcat_absent_to_ref,
                    form_unspecified=pharmcat_unspecified_to_ref,
                    env_absent=os.environ.get("PHARMCAT_ABSENT_TO_REF"),
                    env_unspecified=os.environ.get("PHARMCAT_UNSPECIFIED_TO_REF"),
                )
                logger.info(
                    "PharmCAT assume-ref effective: absent=%s unspecified=%s (form_absent=%r form_unspec=%r)",
                    absent,
                    unspecified,
                    pharmcat_absent_to_ref,
                    pharmcat_unspecified_to_ref,
                )
                for flag in pharmcat_cli_ref_flags(absent, unspecified):
                    pharmcat_cmd.append(flag)
                    logger.info("Using PharmCAT flag %s", flag)
                
                # Add sample ID parameter only if we successfully extracted it from VCF
                if vcf_sample_id:
                    pharmcat_cmd.extend(["-s", vcf_sample_id])
                    logger.info(f"Using VCF sample ID for PharmCAT -s parameter: {vcf_sample_id}")
                else:
                    logger.warning("No VCF sample ID available - PharmCAT will use default behavior")
                
                # Add input file as the last argument
                pharmcat_cmd.append(vcf_path)
                
                # Note: By default, pharmcat_pipeline runs the complete pipeline:
                # 1. NamedAlleleMatcher (generates .match.json)
                # 2. Phenotyper (generates .phenotype.json) 
                # 3. Reporter (generates HTML, JSON, TSV reports with drug recommendations)
                # The -reporterJson, -reporterHtml, and -reporterCallsOnlyTsv flags ensure we get all formats
                
                # Set environment variables
                env = os.environ.copy()
                env["JAVA_TOOL_OPTIONS"] = "-Xmx4g -XX:+UseG1GC"
                env["PHARMCAT_LOG_LEVEL"] = "DEBUG"
                
                # Log important environment info for debugging
                logger.info(f"PHARMCAT_JAR location: {PHARMCAT_JAR}")
                logger.info(f"PHARMCAT_PIPELINE_DIR: {PHARMCAT_PIPELINE_DIR}")
                logger.info(f"PATH environment: {env.get('PATH', 'Not set')}")
                
                # Check if pharmcat_pipeline exists and is executable
                try:
                    subprocess.run(["which", "pharmcat_pipeline"], check=True, capture_output=True, text=True)
                    logger.info("pharmcat_pipeline command found in PATH")
                except subprocess.CalledProcessError:
                    logger.warning("pharmcat_pipeline command NOT found in PATH")
                
                # Prepare per-job report directory before execution to support tee'd logs
                reports_dir = Path(os.getenv("REPORT_DIR", "/data/reports"))
                reports_dir.mkdir(parents=True, exist_ok=True)
                # Nest under /data/reports/{patient_id}/{job_id}/ when possible (137c)
                job_dir_id = job_id or report_id
                if patient_id and job_dir_id:
                    patient_dir = reports_dir / str(patient_id) / str(job_dir_id)
                elif patient_id:
                    patient_dir = reports_dir / str(patient_id)
                else:
                    patient_dir = reports_dir / base_name
                patient_dir.mkdir(parents=True, exist_ok=True)

                # Determine tee behavior and log path
                tee_enabled = os.environ.get("PHARMCAT_TEE", "true").lower() in ("1", "true", "yes", "on")
                tee_log_path = str(patient_dir / f"{base_name}_pharmcat_pipeline.log") if tee_enabled else None

                # Run PharmCAT pipeline
                logger.info(f"Executing PharmCAT command: {' '.join(pharmcat_cmd)}")
                logger.info(f"Working directory: {temp_dir}")
                logger.info(f"Input VCF file: {vcf_path}")
                logger.info(f"Output directory: {output_dir}")
                logger.info(f"File exists: {os.path.exists(vcf_path)}")
                logger.info(f"File size: {os.path.getsize(vcf_path) if os.path.exists(vcf_path) else 'N/A'}")
                logger.info(f"Directory permissions: {oct(os.stat(temp_dir).st_mode) if os.path.exists(temp_dir) else 'N/A'}")
                logger.info(f"Environment variables: JAVA_TOOL_OPTIONS={env.get('JAVA_TOOL_OPTIONS')}, PHARMCAT_LOG_LEVEL={env.get('PHARMCAT_LOG_LEVEL')}")
                
                # Update processing status
                processing_status.update({
                    "status": "processing",
                    "progress": 30,
                    "message": "PharmCAT pipeline running..."
                })
                
                # Update workflow with execution start
                if job_client:
                    await job_client.log_progress("Executing PharmCAT pipeline", {
                        "command": " ".join(pharmcat_cmd),
                        "timeout_seconds": 300
                    })
                    
                    # Update step with progress for proper mapping
                    await job_client.update_step_status(
                        "running",
                        "Executing PharmCAT pipeline",
                        output_data={"progress_percent": 30}
                    )
                
                try:
                    # Helper to stream process output to Docker logs and optionally tee to a file
                    def _run_and_stream(cmd, env_vars, working_dir, workflow_identifier=None, timeout_seconds=300, tee_file_path=None):
                        """Run a subprocess, merging stderr into stdout, stream to logger, optionally tee to file, enforce timeout."""
                        tee_file_handle = open(tee_file_path, "a", encoding="utf-8") if tee_file_path else None
                        try:
                            process_local = subprocess.Popen(
                                cmd,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,  # 2>&1 equivalent
                                text=True,
                                bufsize=1,
                                env=env_vars,
                                cwd=working_dir
                            )
                            if workflow_identifier:
                                # Track specific file paths for cleanup
                                cleanup_paths_local = [
                                    working_dir,
                                    file_path,
                                    (
                                        f"/data/reports/{patient_id}/{job_dir_id}"
                                        if patient_id and job_dir_id
                                        else (
                                            f"/data/reports/{patient_id}"
                                            if patient_id
                                            else None
                                        )
                                    ),
                                ]
                                cleanup_paths_local = [p for p in cleanup_paths_local if p is not None]
                                register_process(workflow_identifier, process_local.pid, {
                                    "temp_dir": working_dir,
                                    "patient_id": patient_id,
                                    "cleanup_paths": cleanup_paths_local
                                })

                            start_time_local = time.time()
                            stdout_length_local = 0
                            # Stream line by line
                            assert process_local.stdout is not None
                            for line in process_local.stdout:
                                line_to_log = line.rstrip("\n")
                                stdout_length_local += len(line)
                                logger.info(f"[pharmcat-pipeline] {line_to_log}")
                                if tee_file_handle:
                                    try:
                                        tee_file_handle.write(line)
                                    except Exception:
                                        # Don't break pipeline on tee errors
                                        pass
                                # Check timeout
                                if (time.time() - start_time_local) > timeout_seconds:
                                    process_local.kill()
                                    raise subprocess.TimeoutExpired(cmd, timeout_seconds)

                            # Ensure process exits and capture return code
                            return_code_local = process_local.wait(timeout=5)
                            return stdout_length_local, 0, return_code_local
                        finally:
                            if workflow_identifier:
                                unregister_process(workflow_identifier)
                            if tee_file_handle:
                                try:
                                    tee_file_handle.flush()
                                    tee_file_handle.close()
                                except Exception:
                                    pass

                    # Execute with streaming depending on workflow context
                    if job_id:
                        stdout_length, stderr_length, return_code = _run_and_stream(
                            pharmcat_cmd, env, temp_dir, workflow_identifier=job_id, timeout_seconds=300, tee_file_path=tee_log_path
                        )
                    else:
                        stdout_length, stderr_length, return_code = _run_and_stream(
                            pharmcat_cmd, env, temp_dir, workflow_identifier=None, timeout_seconds=300, tee_file_path=tee_log_path
                        )

                    logger.info("PharmCAT process completed successfully")
                    
                    # Update processing status for success
                    processing_status.update({
                        "status": "processing",
                        "progress": 70,
                        "message": "PharmCAT completed, processing results..."
                    })
                    
                    # Update workflow with execution success
                    if job_client:
                        await job_client.log_progress("PharmCAT pipeline completed successfully", {
                            "stdout_length": stdout_length,
                            "stderr_length": stderr_length,
                            "tee_log_path": tee_log_path
                        })
                        
                        # Update step with progress for proper mapping
                        await job_client.update_step_status(
                            "running",
                            "PharmCAT completed, processing results...",
                            output_data={"progress_percent": 70}
                        )
                        
                except subprocess.TimeoutExpired:
                    error_msg = "PharmCAT process timed out after 5 minutes"
                    logger.error(error_msg)
                    
                    # Update processing status for timeout error
                    processing_status.update({
                        "status": "error",
                        "progress": 0,
                        "last_error": error_msg,
                        "current_file": None,
                        "start_time": None
                    })
                    
                    # Update workflow with timeout error
                    if job_client:
                        await job_client.fail_step(error_msg, {
                            "error_type": "timeout",
                            "timeout_seconds": 300
                        })
                    
                    raise HTTPException(status_code=500, detail=error_msg)
                    
                except subprocess.CalledProcessError as e:
                    error_msg = f"PharmCAT process failed with exit code {e.returncode}"
                    logger.error(f"{error_msg}. stdout: {e.stdout}, stderr: {e.stderr}")
                except subprocess.TimeoutExpired:
                    error_msg = "PharmCAT process timed out after 5 minutes"
                    logger.error(error_msg)
                    
                    # Update processing status for process error
                    processing_status.update({
                        "status": "error",
                        "progress": 0,
                        "last_error": error_msg,
                        "current_file": None,
                        "start_time": None
                    })
                    
                    # Update workflow with process error
                    if job_client:
                        await job_client.fail_step(error_msg, {
                            "error_type": "process_error",
                            "return_code": e.returncode,
                            "stdout": e.stdout,
                            "stderr": e.stderr
                        })
                    
                    raise HTTPException(status_code=500, detail=error_msg)
                
                # List all files in temp directory for debugging
                all_temp_files = os.listdir(temp_dir)
                logger.info(f"All files in temp directory after PharmCAT: {all_temp_files}")
                
                # Check for results files with the correct base name
                # Look for all report formats
                actual_files = [f for f in all_temp_files if f.endswith(('.json', '.html', '.tsv'))]
                logger.info(f"PharmCAT output files found: {actual_files}")
                
                # Find all report files by format - PharmCAT v3uses different naming patterns
                # Based on the example: phenotype.json, match.json, report.html, report.json (with genes/drugs structure)
                report_json_file = None
                report_html_file = None
                report_tsv_file = None
                
                # Look for PharmCAT v3 format files
                phenotype_file = next((f for f in actual_files if f.endswith('.phenotype.json')), None)
                match_file = next((f for f in actual_files if f.endswith('.match.json')), None)
                reporter_json_file = next((f for f in actual_files if f.endswith('.report.json')), None)
                
                # Prioritize reporter.json as it contains the complete genes/drugs structure
                if reporter_json_file:
                    report_json_file = reporter_json_file
                    logger.info(f"Using PharmCAT v3reporter.json with complete genes/drugs structure: {reporter_json_file}")
                elif phenotype_file:
                    # Use phenotype.json as fallback (contains gene calls but limited drug info)
                    report_json_file = phenotype_file
                    logger.info(f"Using PharmCAT v3phenotype.json as fallback: {phenotype_file}")
                else:
                    logger.error("Required PharmCAT v3output files not found")
                    logger.error(f"Available files: {actual_files}")
                    
                    # Update workflow with missing files error
                    if job_client:
                        await job_client.fail_step("Required PharmCAT output files not found", {
                            "error_type": "missing_output_files",
                            "available_files": actual_files
                        })
                    
                    raise HTTPException(status_code=500, detail="Required PharmCAT v3output files not found")
                
                # Look for HTML report
                report_html_file = next((f for f in actual_files if f.endswith('.report.html') or f.endswith('.html')), None)
                
                # Look for TSV report (PharmCAT v3generates .report.tsv by default)
                # Also check for other possible TSV naming patterns
                report_tsv_file = next((f for f in actual_files if f.endswith('.report.tsv')), None)
                if not report_tsv_file:
                    # Fallback to any .tsv file
                    report_tsv_file = next((f for f in actual_files if f.endswith('.tsv')), None)
                
                # Log what we found for debugging
                logger.info(f"Report files found - JSON: {report_json_file}, HTML: {report_html_file}, TSV: {report_tsv_file}")
                logger.info(f"PharmCAT v3files - Phenotype: {phenotype_file}, Match: {match_file}, Reporter: {reporter_json_file}")
                logger.info(f"Using file for main report: {report_json_file}")
                
                # Log TSV availability
                if report_tsv_file:
                    logger.info(f"TSV report found: {report_tsv_file}")
                else:
                    logger.warning("No TSV report found - this may indicate the Reporter module didn't run or TSV generation failed")
                
                # Create nested report directory: /data/reports/{patient_id}/{job_id}/
                reports_dir = Path(os.getenv("REPORT_DIR", "/data/reports"))
                reports_dir.mkdir(parents=True, exist_ok=True)

                job_dir_id = job_id or report_id
                if patient_id and job_dir_id:
                    patient_dir = reports_dir / str(patient_id) / str(job_dir_id)
                elif patient_id:
                    patient_dir = reports_dir / str(patient_id)
                else:
                    patient_dir = reports_dir / base_name
                patient_dir.mkdir(parents=True, exist_ok=True)
                logger.info(f"Created patient-specific directory: {patient_dir}")

                # Filenames use job_id when available (display report_id = job_id)
                name_base = (
                    str(job_dir_id)
                    if job_dir_id
                    else (str(patient_id) if patient_id else base_name)
                )

                # Copy PharmCAT HTML report to patient directory if available
                if report_html_file:
                    # Save with pharmcat-specific filename to avoid colliding with our own HTML report
                    dest_html_path = patient_dir / f"{name_base}_pgx_pharmcat.html"
                    src_html_path = Path(temp_dir) / report_html_file
                    
                    logger.info(f"Copying PharmCAT report from {src_html_path} to {dest_html_path}")
                    
                    if os.path.exists(src_html_path):
                        shutil.copy2(src_html_path, dest_html_path)
                        logger.info(f"HTML report copied to {dest_html_path}")
                
                # Copy PharmCAT TSV report to /data/reports if available
                if report_tsv_file:
                    dest_tsv_path = patient_dir / f"{name_base}_pgx_pharmcat.tsv"
                    src_tsv_path = Path(temp_dir) / report_tsv_file
                    
                    logger.info(f"Copying PharmCAT TSV report from {src_tsv_path} to {dest_tsv_path}")
                    
                    if os.path.exists(src_tsv_path):
                        # Copy the TSV file
                        shutil.copy2(src_tsv_path, dest_tsv_path)
                        logger.info(f"TSV report copied to {dest_tsv_path}")
                        
                        # Read TSV content for inclusion in response
                        try:
                            with open(src_tsv_path, 'r', encoding='utf-8') as f:
                                tsv_content = f.read()
                            
                            # Validate TSV content
                            if tsv_content.strip():
                                # Count lines and basic structure
                                lines = tsv_content.strip().split('\n')
                                logger.info(f"Loaded TSV content successfully: {len(lines)} lines, {len(tsv_content)} characters")
                                
                                # Log first few lines for debugging
                                if lines:
                                    logger.info(f"TSV header: {lines[0]}")
                                    if len(lines) > 1:
                                        logger.info(f"TSV first data row: {lines[1]}")
                                
                                # Basic TSV validation - should have tab-separated values
                                # Check the header line (second line, index 1) for tab separators
                                if len(lines) > 1 and '\t' in lines[1]:
                                    logger.info("TSV format validated - contains tab separators")
                                else:
                                    logger.warning("TSV may not be properly formatted - no tab separators found in header")
                                    
                            else:
                                logger.warning("TSV file is empty or contains only whitespace")
                                tsv_content = None
                                
                        except UnicodeDecodeError as e:
                            logger.warning(f"Unicode decode error reading TSV: {str(e)}")
                            # Try with different encoding
                            try:
                                with open(src_tsv_path, 'r', encoding='latin-1') as f:
                                    tsv_content = f.read()
                                logger.info("Successfully read TSV with latin-1 encoding")
                            except Exception as e2:
                                logger.error(f"Failed to read TSV with latin-1 encoding: {str(e2)}")
                                tsv_content = None
                        except Exception as e:
                            logger.warning(f"Failed to read TSV content: {str(e)}")
                            tsv_content = None
                    else:
                        logger.warning(f"TSV source file not found at {src_tsv_path}")
                        tsv_content = None
                else:
                    tsv_content = None
                    logger.info("No TSV report file found - TSV content will be None")
                
                # Copy the report.json file to reports directory for inspection
                if report_json_file:
                    # Save with pharmcat-specific filename to avoid colliding with our own JSON export
                    dest_json_path = patient_dir / f"{name_base}_pgx_pharmcat.json"
                    src_json_path = Path(temp_dir) / report_json_file
                    
                    logger.info(f"Copying JSON report from {src_json_path} to {dest_json_path}")
                    
                    # Check if the JSON file actually exists before trying to copy it
                    if os.path.exists(src_json_path):
                        try:
                            shutil.copy2(src_json_path, dest_json_path)
                            logger.info(f"JSON report copied to {dest_json_path}")
                            # Verify the file was copied successfully
                            if os.path.exists(dest_json_path):
                                logger.info(f"Verified JSON report exists at {dest_json_path}")
                            else:
                                logger.error(f"Failed to copy JSON report to {dest_json_path}")
                        except Exception as e:
                            logger.error(f"Error copying JSON report: {str(e)}")
                    else:
                        logger.error(f"JSON report not found at {src_json_path}")
                        # List all files in the temp directory for debugging
                        all_files = os.listdir(temp_dir)
                        logger.info(f"All files in temp directory: {all_files}")
                
                # Process report.json file - our single source of truth
                report_json_path = Path(temp_dir) / report_json_file
                
                # Read and parse the report JSON file
                with open(report_json_path, 'r') as f:
                    report_data = json.load(f)
                
                logger.info(f"Loaded report data successfully. Keys: {list(report_data.keys())}")
                
                # Always create a permanent copy of the raw report.json in the patient directory
                # Try a more direct approach to ensure the file is created
                # Keep raw report named with job_id/patient_id for consistency
                raw_name_base = name_base
                raw_report_path = patient_dir / f"{raw_name_base}_raw_report.json"
                try:
                    with open(raw_report_path, 'w') as f:
                        json.dump(report_data, f, indent=2)
                    logger.info(f"Raw report.json saved to {raw_report_path}")
                    
                    # Double check the file exists
                    if os.path.exists(raw_report_path):
                        logger.info(f"Verified raw report.json file exists at {raw_report_path} with size {os.path.getsize(raw_report_path)}")
                    else:
                        logger.error(f"Failed to create raw report.json at {raw_report_path}")
                except Exception as e:
                    logger.error(f"Error saving raw report.json: {str(e)}")
                
                # Create a standard JSON report in the patient directory
                try:
                    standard_name_base = name_base
                    standard_json_path = patient_dir / f"{standard_name_base}_pgx_report.json"
                    with open(standard_json_path, 'w') as f:
                        json.dump(report_data, f, indent=2)
                    logger.info(f"Standard JSON report saved to {standard_json_path}")
                except Exception as e:
                    logger.error(f"Error saving standard JSON report: {str(e)}")
                
                # Update processing status for completion
                processing_status.update({
                    "status": "completed",
                    "progress": 100,
                    "message": "PharmCAT analysis completed successfully",
                    "current_file": None,
                    "start_time": None
                })
                
                # Complete workflow step
                if job_client:
                    await job_client.complete_step(f"PharmCAT analysis completed successfully", {
                        "total_genes": len(report_data.get("genes", [])),
                        "total_drugs": len(report_data.get("drugs", [])),
                        "output_files": {
                            "json": report_json_file,
                            "html": report_html_file,
                            "tsv": report_tsv_file
                        },
                        "patient_dir": str(patient_dir)
                    })
                
                # Return success response with report URLs
                return {
                    "success": True,
                    "message": "PharmCAT analysis completed successfully",
                    "data": {
                        "job_id": base_name,
                        "sample_identifier": sample_identifier,  # User-entered identifier for display
                        "vcf_sample_id": vcf_sample_id,  # Actual sample ID from VCF file
                        "html_report_url": f"/reports/{base_name}/interactive_report_{base_name}.html" if report_html_file else None,
                        "json_report_url": f"/reports/{base_name}/{base_name}_pgx_report.json" if report_json_file else None,
                        "tsv_report_url": f"/reports/{base_name}/{base_name}_pgx_pharmcat.tsv" if report_tsv_file else None,
                        "raw_report_url": f"/reports/{base_name}/{base_name}_raw_report.json",
                        # Add standard URLs for our custom reports
                        "pdf_report_url": f"/reports/{base_name}/pharmacogenomic_report_{base_name}.pdf",
                        "interactive_html_report_url": f"/reports/{base_name}/interactive_report_{base_name}.html",
                        # Add normalized URLs for PharmCAT original reports
                        "pharmcat_html_report_url": f"/reports/{base_name}/{base_name}_pgx_pharmcat.html" if report_html_file else None,
                        "pharmcat_json_report_url": f"/reports/{base_name}/{base_name}_pgx_pharmcat.json" if report_json_file else None,
                        "pharmcat_tsv_report_url": f"/reports/{base_name}/{base_name}_pgx_pharmcat.tsv" if report_tsv_file else None,
                        # Include the actual report content for the client to process
                        "report_json": report_data,
                        "report_tsv": tsv_content,  # Will be populated if TSV exists
                        # Add TSV metadata for better client integration
                        "tsv_metadata": {
                            "available": report_tsv_file is not None,
                            "filename": report_tsv_file,
                            "line_count": len(tsv_content.strip().split('\n')) if tsv_content else 0,
                            "size_bytes": len(tsv_content.encode('utf-8')) if tsv_content else 0,
                            "format": "tab-separated values (TSV)",
                            "description": "PharmCAT Reporter module output with gene calls, phenotypes, and drug recommendations"
                        } if report_tsv_file else None,
                        "genes": report_data.get("genes", []),
                        "drugs": report_data.get("drugs", []),
                        "messages": report_data.get("messages", [])
                    }
                }
                
        except subprocess.CalledProcessError as e:
            logger.error(f"PharmCAT process error: {e.stderr}")
            
            # Update workflow with process error
            if job_client:
                await job_client.fail_step(f"PharmCAT process error: {e.stderr}", {
                    "error_type": "process_error",
                    "return_code": e.returncode,
                    "stdout": e.stdout,
                    "stderr": e.stderr
                })
            
            raise HTTPException(status_code=500, detail=f"PharmCAT process error: {e.stderr}")
            
        except Exception as e:
            logger.error(f"Error running PharmCAT: {str(e)}")
            logger.error(traceback.format_exc())
            
            # Update workflow with general error
            if job_client:
                await job_client.fail_step(f"Error running PharmCAT: {str(e)}", {
                    "error_type": "general_error",
                    "error": str(e),
                    "traceback": traceback.format_exc()
                })
            
            raise HTTPException(status_code=500, detail=f"Error running PharmCAT: {str(e)}")
    
    except Exception as e:
        logger.error(f"Error processing request: {str(e)}")
        logger.error(traceback.format_exc())
        
        # Update workflow with request error
        if job_client:
            await job_client.fail_step(f"Error processing request: {str(e)}", {
                "error_type": "request_error",
                "error": str(e)
            })
        
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/status")
def get_status():
    """API endpoint to get the current processing status."""
    try:
        # Get container stats
        container_stats = {
            "cpu_percent": psutil.cpu_percent(),
            "memory_percent": psutil.virtual_memory().percent,
            "disk_usage": psutil.disk_usage('/').percent
        }
        
        # Get process stats if we're processing a file
        process_stats = None
        if processing_status["current_file"]:
            process_stats = {
                "file": processing_status["current_file"],
                "start_time": processing_status["start_time"].isoformat() if processing_status["start_time"] else None,
                "elapsed_time": (datetime.now() - processing_status["start_time"]).total_seconds() if processing_status["start_time"] else 0
            }
        
        return {
            "status": "ok",
            "service": "pharmcat",
            "processing_status": processing_status,
            "process_stats": process_stats,
            "container_stats": container_stats,
            "pharmcat_jar_exists": os.path.exists(PHARMCAT_JAR),
            "data_dir_contents": os.listdir(DATA_DIR)
        }
    except Exception as e:
        logger.error(f"Error getting status: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

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
        
        # Check our stored process registry
        if job_id in running_processes:
            process_info = running_processes[job_id]
            pid = process_info.get("pid")
            
            if pid and psutil.pid_exists(pid):
                try:
                    process = psutil.Process(pid)
                    process.terminate()
                    logger.info(f"Terminated process {pid} for job {job_id}")
                    terminated_count += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
                    logger.warning(f"Could not terminate process {pid}: {e}")
            
            # Clean up specific tracked file paths
            cleanup_paths = process_info.get("cleanup_paths", [])
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
            
            # Remove from registry
            del running_processes[job_id]
        else:
            logger.warning(f"No running process found for job {job_id}")
        
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

if __name__ == '__main__':
    # Start the FastAPI app
    logger.info("Starting PharmCAT wrapper service with DATA_DIR=%s, TEMP_DIR=%s", os.getenv('DATA_DIR', '/data'), os.getenv('TEMP_DIR', '/data/temp'))
    logger.info("PharmCAT JAR location: %s", PHARMCAT_JAR)
    
    # Configure uvicorn to listen on all interfaces
    uvicorn.run(
        "pharmcat:app",
        host='0.0.0.0',
        port=5000,
        reload=False
    )
