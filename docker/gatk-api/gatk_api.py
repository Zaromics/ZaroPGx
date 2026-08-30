#!/usr/bin/env python3
"""
GATK API wrapper service. This provides an HTTP API that makes appropriate
calls to the GATK container.
"""

import os
import json
import logging
import logging.handlers
import tempfile
import subprocess
import requests
import threading
import uuid
import time
import sys
import platform
import psutil
import shutil
import traceback
import re  # Add regex module for header parsing
import random
import gzip  # bgzipped VCF headers -- see detect_reference()
from typing import List, Dict, Optional, Any
import asyncio

import uvicorn
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Import shared workflow client for integration
import sys
sys.path.append('/job-client')
from job_client import JobClient, create_job_client  # pyright: ignore[reportMissingImports]

# Configuration. Read before logging is configured because the progress-log handler
# below writes into DATA_DIR.
GATK_CONTAINER = os.environ.get('GATK_CONTAINER', 'gatk')
DATA_DIR = os.environ.get('DATA_DIR', '/data')
TEMP_DIR = os.environ.get('TMPDIR', '/tmp/gatk_temp')
REFERENCE_DIR = os.environ.get('REFERENCE_DIR', '/reference')
MAX_MEMORY = os.environ.get('MAX_MEMORY', '20g')  # Default to 20g per NIH recommendation

# Create directories
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(os.path.join(DATA_DIR, 'uploads'), exist_ok=True)
os.makedirs(os.path.join(DATA_DIR, 'results'), exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

# --------------------------------------------------------------------------
# Bounded logging (BACKLOG 252). Same block, same values, same failure
# behaviour in every ZaroPGx sidecar: docker/nextflow/runner.py,
# docker/pharmcat/pharmcat.py, docker/pypgx/pypgx_wrapper.py and
# docker/zarohla/app.py. tests/test_log_rotation_252.py pins all five against
# one rule, so an edit here that is not made there fails the suite.
#
# It is duplicated rather than imported: these are five separate images. The
# repo does have a mechanism for sharing a pure-Python module into all of them
# (every Dockerfile does `COPY app/utils/job_client.py /job-client/`), but the
# logging block runs at the very top of each module, before sys.path is
# extended, so a shared import would have to be bootstrapped by hand -- more
# moving parts than the ~20 lines it would save, across five images that would
# all need rebuilding to pick it up.
#
# The progress log lives on the ./data bind mount shared with the app
# container, so an unrotated handler there grows without limit and takes the
# host filesystem with it. 10 MiB x 5 backups caps each destination at 60 MiB.
# That is deliberately generous rather than frugal: a whole-genome run at this
# service's DEBUG level produces a lot of lines, the volume it shares is
# already holding tens of gigabytes of BAM/CRAM, and the failures these logs
# exist to explain are diagnosed hours after the fact. The item is about
# *unbounded* growth, not about saving 40 MiB.
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 5

# (path, error) for every destination that could not be opened. Reported by
# _warn_about_unopened_logs() once `logger` exists -- the failure is loud, but
# it is not fatal.
_log_file_errors = []


def _bounded_file_handler(path):
    """Return a size-capped handler for `path`, or None if it cannot be opened.

    A log destination that is missing or read-only must not take the service down at
    import time. The handler is not the job: if /data really is unmounted, the first
    read or write of actual pipeline data fails with an error that names the real
    operation, whereas raising here reports the wrong cause -- and does it before
    `logger` exists, so nothing in the container ever says why it died. Degrading
    keeps stdout carrying the full stream for `docker logs`, and the caller warns.
    """
    try:
        return logging.handlers.RotatingFileHandler(
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


# Set up more verbose logging with both file and console handlers
_log_handlers = [logging.StreamHandler(sys.stdout)]
_log_handlers.extend(
    handler
    for handler in (
        _bounded_file_handler('/var/log/gatk_api.log'),
        # Progress log accessible to main app
        _bounded_file_handler(os.path.join(DATA_DIR, 'gatk_progress.log')),
    )
    if handler is not None
)

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=_log_handlers
)

logger = logging.getLogger(__name__)
_warn_about_unopened_logs(logger)
logger.info("Starting GATK API service with enhanced debugging")
logger.info(f"Data directories ready: DATA_DIR={DATA_DIR}, TEMP_DIR={TEMP_DIR}")

# Initialize FastAPI app
app = FastAPI(
    title="GATK API Wrapper",
    description="REST API wrapper around GATK for the ZaroPGx pipeline",
    version="0.3.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Store startup time
app_start_time = time.time()

# Write version manifest at startup
try:
    versions_dir = os.path.join(DATA_DIR, "versions")
    os.makedirs(versions_dir, exist_ok=True)
    try:
        result = subprocess.run(["gatk", "--version"], capture_output=True, text=True, timeout=10)
        gatk_version = (result.stdout or result.stderr or "").strip().splitlines()[0]
    except Exception:
        gatk_version = "unknown"
    with open(os.path.join(versions_dir, "gatk.json"), "w") as vf:
        # Keep only the first token if it's a long banner
        ver_token = gatk_version.replace("GATK", "").strip()
        vf.write(json.dumps({"name": "GATK", "version": ver_token}))
except Exception:
    pass

# Which assembly each reference name denotes. hg38 and grch38 are the same build
# (and here the same FASTA); hg19 and grch37 are the same build under different
# contig naming. Comparing builds rather than names keeps the CRAM reference check
# below from rejecting a caller who said "grch38" over a header that reads hg38.
REFERENCE_BUILDS = {
    'hg19': 'GRCh37',
    'grch37': 'GRCh37',
    'hg38': 'GRCh38',
    'grch38': 'GRCh38',
}

# Map reference genome names to file paths
REFERENCE_PATHS = {
    'hg19': os.path.join(REFERENCE_DIR, 'hg19', 'ucsc.hg19.fasta'),
    'hg38': os.path.join(REFERENCE_DIR, 'hg38', 'Homo_sapiens_assembly38.fasta'),
    'grch37': os.path.join(REFERENCE_DIR, 'grch37', 'human_g1k_v37.fasta'),
    'grch38': os.path.join(REFERENCE_DIR, 'hg38', 'Homo_sapiens_assembly38.fasta')  # symlink
}

# Liftover chain files, keyed by (source build, target build) -- REFERENCE_PATHS-style,
# under the same /reference bind mount, so genome-downloader (or an operator fetching
# by hand) can stage them once and every recreation of this container sees them.
#
# There is exactly one entry on purpose. The only liftover ZaroPGx performs is
# GRCh37 -> GRCh38, with UCSC's hg19ToHg38.over.chain.gz
# (https://hgdownload.soe.ucsc.edu/goldenPath/hg19/liftOver/hg19ToHg38.over.chain.gz).
# htsjdk reads the chain through a decompressing reader, so it stays gzipped on disk.
#
# The chain's *source* contig names are UCSC-style (`chr1`) -- see the contig-prefix
# normalisation in run_liftover_pipeline(), which exists solely because of that fact.
LIFTOVER_CHAIN_PATHS = {
    ('GRCh37', 'GRCh38'): os.path.join(REFERENCE_DIR, 'chain', 'hg19ToHg38.over.chain.gz'),
}

# Builds /liftover-vcf accepts as `source_build`. An allowlist, not a passthrough:
# source_build is a caller-supplied form field, and everything it selects -- the chain,
# the log lines, the output naming -- must come from constants in this module, never
# from the request. Values are compared lowercased; both spellings of the one
# supported source build are accepted because the app-side detector says "GRCh37"
# while UCSC tooling and this module's own REFERENCE_PATHS say "hg19".
LIFTOVER_SOURCE_BUILDS = {
    'grch37': 'GRCh37',
    'hg19': 'GRCh37',
}

# Reject-rate ceiling for /liftover-vcf, as a fraction of all input records. A real
# GRCh37 -> GRCh38 liftover of NGS data rejects well under 1% of records (the builds
# differ in coordinates, not in most content), so a majority-rejected run does not
# mean "difficult file" -- it means the chain did not match the input at all, which
# in practice is a contig-naming mismatch or a wrong source build. Returning the
# few survivors as a "successful" VCF would silently drop most of the patient's
# variants and produce a clinically wrong report, so past this ceiling the run is
# an error, loudly. Env-overridable for deliberate experiments, never lowered by
# request fields.
LIFTOVER_MAX_REJECT_FRACTION = float(os.environ.get('LIFTOVER_MAX_REJECT_FRACTION', '0.5'))

# In-memory GATK run tracker only — not the ORM Job /api/v1/jobs entity (137c).
jobs = {}  # job_id -> job_info

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

# Job status tracking
JOB_STATUS_PENDING = "pending"
JOB_STATUS_INDEXING = "indexing"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_COMPLETED = "completed"
JOB_STATUS_ERROR = "error"

# Extensions this service recognises, longest first so `.vcf.gz` wins over `.gz`.
# The stored filename's extension is always one of these literals, never a slice of
# the upload's own name.
#
# The compound `.gz` forms come first: an extension is chosen by the first
# `endswith` that matches, so a bare `.gz` variant added later must never precede
# the two-part form it is a suffix of.
#
# Kept byte-for-byte in step with app/main.py's copy -- tests/test_upload_name_sanitiser.py
# asserts the two tuples are equal, so neither may be edited alone.
SAFE_UPLOAD_EXTENSIONS = (
    ".vcf.gz",
    ".fastq.gz",
    ".fq.gz",
    ".vcf",
    ".bcf",
    ".bam",
    ".cram",
    ".sam",
    ".fastq",
    ".fq",
)


def safe_upload_name(filename, local_job_id):
    """Return a shell- and filesystem-safe name to store an upload under.

    `file.filename` is attacker-controlled: it arrives in the multipart body and
    nothing upstream of this service sanitises it. `os.path.basename` is not enough
    -- `;`, `|`, `$(...)` and backticks are all legal in a POSIX filename, so a name
    like `x;touch /tmp/pwned;.bam` is stored happily and then means something to any
    shell the path reaches.

    werkzeug's secure_filename would be the obvious reuse, but werkzeug is not
    installed in this image -- see Dockerfile.gatk-api, which installs only fastapi,
    uvicorn, httpx, requests, psutil and python-multipart. Rather than add a
    dependency or hand-roll a filter that has to be right about every metacharacter,
    the name is rebuilt from parts this module controls:

      <allowlisted fragment of the original>_<our uuid><extension from the tuple above>

    Every byte of the result is therefore either [A-Za-z0-9_-], our own uuid, or one
    of the literal extensions above. The fragment is kept only so logs and on-disk
    debugging still resemble the upload; correctness does not depend on it.

    app/main.py carries a deliberate, behaviourally identical copy of this function
    (it dropped its bare secure_filename, which returned "" for a name like "...").
    The two are kept in step by tests/test_upload_name_sanitiser.py, which execs
    this one out of the source and asserts both return the same name for every
    entry in an adversarial corpus. Change one and you must change the other.
    """
    original = os.path.basename(filename or "")
    lowered = original.lower()

    extension = ""
    for candidate in SAFE_UPLOAD_EXTENSIONS:
        if lowered.endswith(candidate):
            extension = candidate
            break

    stem_source = original[: len(original) - len(extension)] if extension else original
    # Allowlist, not denylist: anything not explicitly safe is dropped.
    fragment = re.sub(r"[^A-Za-z0-9_-]", "", stem_source)[:40]

    return f"{fragment or 'upload'}_{local_job_id}{extension}"


def stored_extension(name):
    """Return the SAFE_UPLOAD_EXTENSIONS suffix of a stored upload name, or "".

    Deliberately not `os.path.splitext(name)[1]`, which every caller here used
    to use. splitext returns only the *final* component, so a `.vcf.gz` upload
    came back as `.gz` -- and every membership test downstream was written
    against the literal `'.vcf.gz'`, which `.gz` can never equal. A bgzipped VCF
    therefore fell past `['.vcf', '.vcf.gz']`, past `['.bam', '.cram', '.sam']`,
    and into the `else`, where /variant-call answered
    `400 Unsupported file format: .gz`; reference auto-detection was skipped for
    the same reason, one branch earlier. The tests are in
    tests/test_gatk_api_no_mock_bam.py.

    `name` is always the output of safe_upload_name(), so by construction its
    extension is one of the SAFE_UPLOAD_EXTENSIONS literals or nothing at all.
    Matching longest-first against that same tuple -- the one the sanitiser
    chose from -- is exact rather than a heuristic, and keeps the two halves
    from drifting: an extension the sanitiser learns to preserve is one this
    function learns to recognise, in the same edit.
    """
    lowered = (name or "").lower()
    for candidate in SAFE_UPLOAD_EXTENSIONS:
        if lowered.endswith(candidate):
            return candidate
    return ""


def stored_stem(name):
    """`name` with its SAFE_UPLOAD_EXTENSIONS suffix removed.

    The other half of the same defect: `os.path.splitext(name)[0]` leaves the
    `.vcf` on a `.vcf.gz` name, so the derived output was `<name>.vcf.vcf`.
    """
    extension = stored_extension(name)
    return name[: len(name) - len(extension)] if extension else name


def build_haplotypecaller_argv(
    java_options, reference_path, input_path, output_path, regions=None, excluded_contigs=None
):
    """Build the GATK HaplotypeCaller command as an argv list.

    A list, never a string: `input_path` derives from an uploaded filename and
    `regions` is a caller-supplied form field, and both used to be interpolated into
    a `shell=True` command string. As argv, every element is one token no matter what
    it contains, so there is nothing for a shell to reinterpret.
    """
    argv = [
        "gatk",
        "--java-options",
        java_options,
        "HaplotypeCaller",
        "-R",
        reference_path,
        "-I",
        input_path,
        "-O",
        output_path,
    ]
    if regions:
        argv += ["-L", str(regions)]
    for contig in excluded_contigs or []:
        argv += ["-XL", str(contig)]
    argv += ["--verbosity", "INFO"]
    return argv


def index_bam_file(job_id, bam_path):
    """
    Create an index for a BAM file using samtools.

    Args:
        job_id: ID of the job for tracking
        bam_path: Path to the BAM file to index

    Returns:
        tuple: (success, message)
    """
    try:
        logger.info(f"Job {job_id}: Indexing BAM file: {bam_path}")
        update_job_status(job_id, JOB_STATUS_INDEXING, progress=10, message="Starting BAM indexing")

        # Check if samtools is installed
        try:
            # Check samtools without text decoding to avoid encoding issues
            subprocess.run(["samtools", "--version"], capture_output=True, check=True)
            logger.debug(f"Job {job_id}: samtools is installed")
            update_job_status(job_id, JOB_STATUS_INDEXING, progress=15, message="samtools found")
            
            # List argv, not shell=True: bam_path is built from an uploaded filename.
            cmd = ["samtools", "index", bam_path]
            logger.info(f"Job {job_id}: Running command: {' '.join(cmd)}")
            update_job_status(job_id, JOB_STATUS_INDEXING, progress=20, message="Running samtools index")

            process = subprocess.run(cmd, check=True, capture_output=True)
        except (subprocess.SubprocessError, FileNotFoundError):
            error_msg = "samtools not found - check container configuration"
            logger.error(f"Job {job_id}: {error_msg}")
            update_job_status(job_id, JOB_STATUS_ERROR, progress=100, message=error_msg)
            return False, error_msg

        # Check if index file was created
        index_path = f"{bam_path}.bai"
        if os.path.exists(index_path):
            logger.info(f"Job {job_id}: Successfully indexed BAM file, index at {index_path}")
            update_job_status(job_id, JOB_STATUS_INDEXING, progress=30, 
                             message="BAM file indexed successfully", output_file=index_path)
            return True, f"Created index at {index_path}"
        else:
            logger.warning(f"Job {job_id}: Index command completed but no index file found at {index_path}")
            update_job_status(job_id, JOB_STATUS_ERROR, progress=100, 
                             message=f"Index command completed but no index file found at {index_path}")
            return False, "Index command completed but no index file found"

    except subprocess.CalledProcessError as e:
        error_msg = f"Error indexing BAM file: {str(e)}"
        logger.error(f"Job {job_id}: {error_msg}")
        update_job_status(job_id, JOB_STATUS_ERROR, progress=100, message=error_msg)
        return False, error_msg
    except Exception as e:
        error_msg = f"Unexpected error while indexing BAM file: {str(e)}"
        logger.error(f"Job {job_id}: {error_msg}")
        update_job_status(job_id, JOB_STATUS_ERROR, progress=100, message=error_msg)
        return False, error_msg

def update_job_status(job_id, status, progress=None, message=None, output_file=None, error=None, extras=None):
    """Update job status with more detailed information and logging"""
    if job_id not in jobs:
        logger.warning(f"Attempting to update non-existent job: {job_id}")
        return
    
    job = jobs[job_id]
    
    # Update values if provided
    if status is not None:
        job["status"] = status
    if progress is not None:
        job["progress"] = progress
    if message is not None:
        job["message"] = message
    if output_file is not None:
        job["output_file"] = output_file
    if error is not None:
        job["error"] = error
    if extras is not None:
        if "extras" not in job:
            job["extras"] = {}
        # Update extras with new information
        job["extras"].update(extras)
    
    # Update timestamp
    job["updated_at"] = time.time()
    
    # Log the update
    logger.info(f"Job {job_id}: Status updated to {status}, Progress: {progress}%, Message: {message}")

async def run_variant_calling(local_job_id, input_path, output_path, reference_path, regions=None, zaro_job_id=None, patient_id=None):
    """Run GATK HaplotypeCaller with dynamic memory allocation based on input file size."""
    try:
        # Initialize job client if Zaro Job PK is provided
        job_client = None
        if zaro_job_id:
            try:
                job_client = JobClient(job_id=zaro_job_id, step_name="gatk_variant_calling")
            except Exception as e:
                logger.warning(f"Failed to initialize workflow client: {e}")
                job_client = None
        # Check file size and customize memory settings
        file_size = os.path.getsize(input_path)
        file_size_gb = file_size / (1024 * 1024 * 1024)
        
        # Get available memory from container
        try:
            total_memory_bytes = psutil.virtual_memory().total
            total_memory_gb = total_memory_bytes / (1024 * 1024 * 1024)
            
            # Log memory information
            logger.info(f"Job {local_job_id}: System memory - Total: {total_memory_gb:.2f}GB, File size: {file_size_gb:.2f}GB")
            
            # For very large files (> 2GB), use 70% of available memory
            # For smaller files, use default MAX_MEMORY
            if file_size_gb > 2.0:
                # Use 70% of available memory, but cap at MAX_MEMORY if set
                memory_to_use = min(int(total_memory_gb * 0.7), 
                                   int(MAX_MEMORY.replace('g', '')) if MAX_MEMORY.endswith('g') else int(MAX_MEMORY))
                java_options = f"-Xms{memory_to_use}G -Xmx{memory_to_use}G -XX:ParallelGCThreads=2 -XX:+UseG1GC"
                logger.info(f"Job {local_job_id}: Large file detected, using {memory_to_use}g memory for Java")
            else:
                # Use standard memory settings
                java_options = f"-Xms20G -Xmx20G -XX:ParallelGCThreads=2"
                logger.info(f"Job {local_job_id}: Using standard memory setting {MAX_MEMORY}")
        except Exception as mem_error:
            # Fall back to default if we can't get memory info
            logger.warning(f"Job {local_job_id}: Failed to get system memory info: {str(mem_error)}. Using default {MAX_MEMORY}")
            java_options = f"-Xms20G -Xmx20G -XX:ParallelGCThreads=2"
                
        # Update job status
        update_job_status(local_job_id, JOB_STATUS_RUNNING, progress=30, 
                         message="Running GATK HaplotypeCaller for variant calling")
        
        # Define regions argument if provided
        regions_arg = ""
        if regions:
            regions_arg = f"-L {regions}"
        
        # Create directory for output if it doesn't exist
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        # Track excluded contigs in case we need to retry
        excluded_contigs = []
        max_retries = 2  # Allow up to 2 retries for contig issues
        
        # Store information about non-human contigs found
        non_human_contigs = {
            "detected": [],
            "excluded": [],
            "identified_types": []
        }
        
        # Viral and non-human contig information for reporting
        contig_info = {
            "chrEBV": {
                "name": "Epstein-Barr virus (EBV)", 
                "type": "viral",
                "description": "Human herpesvirus 4, commonly present in saliva and associated with mononucleosis"
            },
            "chrHPV": {
                "name": "Human Papillomavirus (HPV)", 
                "type": "viral",
                "description": "DNA virus associated with various types of cancer"
            },
            "NC_007605": {
                "name": "Epstein-Barr virus (EBV)", 
                "type": "viral",
                "description": "Alternative contig name for EBV"
            },
            "chrVirus": {
                "name": "Unspecified viral sequences", 
                "type": "viral",
                "description": "Generic viral contig"
            },
            "chrMito": {
                "name": "Mitochondrial DNA", 
                "type": "mitochondrial",
                "description": "Mitochondrial genome sequence, often with different ploidy"
            }
        }
        
        for attempt in range(max_retries + 1):
            try:
                if excluded_contigs:
                    logger.info(f"Job {local_job_id}: Excluding contigs: {', '.join(excluded_contigs)}")

                # Set up the command as argv -- input_path comes from an uploaded
                # filename and regions is a caller-supplied form field.
                cmd = build_haplotypecaller_argv(
                    java_options,
                    reference_path,
                    input_path,
                    output_path,
                    regions=regions,
                    excluded_contigs=excluded_contigs,
                )

                # Log the command being run
                logger.info(f"Job {local_job_id}: Running GATK command (attempt {attempt+1}/{max_retries+1}): {' '.join(cmd)}")

                # Prepare the subprocess
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    universal_newlines=True
                )
                
                # Register process for cancellation if we have workflow context
                if zaro_job_id:
                    # Track specific file paths for cleanup
                    cleanup_paths = [input_path, output_path]
                    register_process(zaro_job_id, process.pid, {
                        "job_id": local_job_id,
                        "patient_id": patient_id,
                        "cleanup_paths": cleanup_paths
                    })
                
                # Track the current chromosome and progress
                current_chromosome = "Unknown"
                chromosomes_processed = []
                chromosomes_expected = []
                
                # Check for contig errors during execution
                contig_error = None
                
                # Get a list of expected chromosomes from the reference
                try:
                    # Use samtools to list expected chromosomes from the reference
                    fai_path = f"{reference_path}.fai"
                    if os.path.exists(fai_path):
                        with open(fai_path, 'r') as f:
                            chromosomes_expected = [line.split()[0] for line in f]
                            logger.info(f"Job {local_job_id}: Found {len(chromosomes_expected)} chromosomes in reference: {', '.join(chromosomes_expected[:5])}...")
                    else:
                        logger.warning(f"Job {local_job_id}: Reference index file not found at {fai_path}")
                except Exception as e:
                    logger.warning(f"Job {local_job_id}: Could not determine chromosome list: {str(e)}")
                
                # Function to update progress based on chromosome position
                def update_progress_by_chromosome(chrom):
                    # If we don't have an expected list, guess based on standard human genome
                    if not chromosomes_expected:
                        # Approximate chromosomes for human genome
                        standard_chroms = [f"chr{i}" for i in range(1, 23)] + ["chrX", "chrY", "chrM"]
                        alt_chroms = [str(i) for i in range(1, 23)] + ["X", "Y", "MT"]
                        
                        if chrom in standard_chroms:
                            idx = standard_chroms.index(chrom)
                            progress = 30 + min(60 * idx / len(standard_chroms), 60)
                        elif chrom in alt_chroms:
                            idx = alt_chroms.index(chrom)
                            progress = 30 + min(60 * idx / len(alt_chroms), 60)
                        else:
                            # Unknown chromosome format, use count-based
                            progress = 30 + min(60 * len(chromosomes_processed) / 24, 60)
                    else:
                        # We have the expected chromosome list
                        if chrom in chromosomes_expected:
                            idx = chromosomes_expected.index(chrom)
                            progress = 30 + min(60 * idx / len(chromosomes_expected), 60)
                        else:
                            # Unknown chromosome
                            progress = 30 + min(60 * len(chromosomes_processed) / len(chromosomes_expected), 60)
                    
                    return int(progress)
                
                # Function to get GATK process memory usage
                def get_gatk_memory_usage():
                    try:
                        # Find all Java processes
                        java_processes = []
                        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                            try:
                                if proc.name() == 'java' and any('gatk' in cmd.lower() if cmd else False for cmd in proc.cmdline()):
                                    java_processes.append(proc)
                            except (psutil.NoSuchProcess, psutil.AccessDenied):
                                pass
                        
                        # Get memory usage of all Java processes that might be GATK
                        if java_processes:
                            total_memory = 0
                            for proc in java_processes:
                                try:
                                    mem_info = proc.memory_info()
                                    total_memory += mem_info.rss
                                except:
                                    pass
                            return total_memory / (1024 * 1024)  # Return in MB
                        
                        # If we can't find GATK process, try to get system-wide Java memory
                        java_mem = 0
                        for proc in psutil.process_iter(['pid', 'name']):
                            try:
                                if proc.name() == 'java':
                                    java_mem += proc.memory_info().rss
                            except:
                                pass
                        return java_mem / (1024 * 1024)  # Return in MB
                    except Exception as e:
                        logger.debug(f"Job {local_job_id}: Could not get GATK memory usage: {str(e)}")
                        return None
                
                # Process output line by line
                for line in iter(process.stdout.readline, ''):
                    # Log the GATK output
                    logger.debug(f"Job {local_job_id}: GATK output: {line.strip()}")
                    
                    # Check for contig not present errors
                    contig_not_present_match = re.search(r'Contig\s+(\S+)\s+not\s+present', line)
                    if contig_not_present_match:
                        missing_contig = contig_not_present_match.group(1)
                        contig_error = f"Contig {missing_contig} not present in reference"
                        logger.warning(f"Job {local_job_id}: {contig_error}")
                        
                        # Add to excluded contigs for next attempt
                        if missing_contig not in excluded_contigs:
                            excluded_contigs.append(missing_contig)
                            
                            # Add to non-human contigs detected list if not already there
                            if missing_contig in contig_info:
                                contig_type = contig_info[missing_contig]["type"]
                                if contig_type not in non_human_contigs["identified_types"]:
                                    non_human_contigs["identified_types"].append(contig_type)
                    
                    # Extract chromosome information
                    if 'Starting traversal' in line or 'Start traversal' in line or 'Processing' in line:
                        # Capture the current chromosome being processed
                        chrom_match = re.search(r'(chr\w+|scaffold\w+|\d+|X|Y|MT)', line)
                        if chrom_match:
                            new_chromosome = chrom_match.group(0)
                            if new_chromosome != current_chromosome:
                                logger.info(f"Job {local_job_id}: GATK processing chromosome: {new_chromosome}")
                                current_chromosome = new_chromosome
                                if new_chromosome not in chromosomes_processed:
                                    chromosomes_processed.append(new_chromosome)
                            
                            # Update progress based on chromosome position
                            progress = update_progress_by_chromosome(current_chromosome)
                            
                            # Get memory usage
                            memory_usage = get_gatk_memory_usage()
                            memory_info = f"({int(memory_usage)}MB used)" if memory_usage else ""
                            
                            # Update job status
                            update_job_status(local_job_id, JOB_STATUS_RUNNING, progress=progress,
                                            message=f"Processing chromosome {current_chromosome} {memory_info}")
                            
                            # Update workflow step with progress for proper mapping
                            if zaro_job_id and job_client:
                                try:
                                    await job_client.update_step_status(
                                        "running",
                                        f"Processing chromosome {current_chromosome} {memory_info}",
                                        output_data={"progress_percent": progress}
                                    )
                                except Exception as e:
                                    logger.warning(f"Failed to update workflow step progress: {e}")
                    
                    # Look for progress information
                    elif 'Progress:' in line:
                        # Try to extract percentage if GATK outputs it
                        progress_match = re.search(r'(\d+\.\d+)%', line)
                        if progress_match:
                            gatk_progress = float(progress_match.group(1))
                            # Scale to our 30-90% range
                            progress = 30 + min(gatk_progress * 0.6, 60)
                            
                            # Get memory usage
                            memory_usage = get_gatk_memory_usage()
                            memory_info = f"({int(memory_usage)}MB used)" if memory_usage else ""
                            
                            update_job_status(local_job_id, JOB_STATUS_RUNNING, progress=int(progress),
                                            message=f"GATK Progress: {gatk_progress:.1f}% {memory_info}")
                            
                            # Update workflow step with progress for proper mapping
                            if zaro_job_id and job_client:
                                try:
                                    await job_client.update_step_status(
                                        "running",
                                        f"GATK Progress: {gatk_progress:.1f}% {memory_info}",
                                        output_data={"progress_percent": int(progress)}
                                    )
                                except Exception as e:
                                    logger.warning(f"Failed to update workflow step progress: {e}")
                    
                    # Check for errors
                    elif 'ERROR' in line:
                        logger.error(f"Job {local_job_id}: GATK error: {line.strip()}")
                    
                    # Periodically update memory usage even without progress update
                    elif line.strip() and random.random() < 0.1:  # 10% chance to update on any output line
                        # Get memory usage
                        memory_usage = get_gatk_memory_usage()
                        if memory_usage:
                            # Get progress based on chromosomes
                            if chromosomes_processed:
                                progress = update_progress_by_chromosome(chromosomes_processed[-1])
                            else:
                                progress = 30  # Default starting progress
                            
                            update_job_status(local_job_id, JOB_STATUS_RUNNING, progress=progress,
                                            message=f"Running GATK HaplotypeCaller ({int(memory_usage)}MB used)")
                
                # Wait for process to complete and get return code
                return_code = process.wait()
                
                # Unregister process when done
                if zaro_job_id:
                    unregister_process(zaro_job_id)
                
                # If we had a contig error but the process exited with a non-zero code, retry
                if return_code != 0 and contig_error and attempt < max_retries:
                    logger.warning(f"Job {local_job_id}: GATK failed with contig error. Will retry excluding: {', '.join(excluded_contigs)}")
                    continue  # Try again with excluded contigs
                
                # If we reach here, either the process succeeded, or it failed without a contig error
                if return_code != 0:
                    raise subprocess.CalledProcessError(return_code, cmd)
                
                # Command completed successfully
                logger.info(f"Job {local_job_id}: GATK command completed successfully")
                
                # Update non-human contigs excluded list
                non_human_contigs["excluded"] = excluded_contigs.copy()
                
                # Prepare extras data for reporting
                extras_data = {
                    "non_human_contigs": non_human_contigs
                }
                
                # Add detailed contig information if available
                if non_human_contigs["detected"]:
                    extras_data["contig_details"] = {}
                    for contig in non_human_contigs["detected"]:
                        if contig in contig_info:
                            extras_data["contig_details"][contig] = contig_info[contig]
                        else:
                            extras_data["contig_details"][contig] = {
                                "name": f"Unknown contig ({contig})",
                                "type": "unknown",
                                "description": "Contig not found in reference genome"
                            }
                
                # Verify the output file exists
                if not os.path.exists(output_path):
                    raise Exception(f"GATK completed but output file not found: {output_path}")
                
                # Update job status
                update_job_status(local_job_id, JOB_STATUS_COMPLETED, progress=100, 
                                message=f"Variant calling complete{' (excluded: ' + ', '.join(excluded_contigs) + ')' if excluded_contigs else ''}",
                                output_file=output_path,
                                extras=extras_data)
                
                return output_path
                
            except subprocess.CalledProcessError as e:
                # If this was our last attempt, raise the error
                if attempt == max_retries:
                    error_message = f"GATK command failed with exit code {e.returncode}"
                    logger.error(f"Job {local_job_id}: {error_message}")
                    
                    update_job_status(local_job_id, JOB_STATUS_ERROR, progress=100, 
                                    message="GATK variant calling failed",
                                    error=error_message)
                    return None
                else:
                    # If no contig error was detected but we still failed, check if we should retry
                    if not excluded_contigs:
                        # No specific contig issues detected, but let's try a general fix
                        # Add common non-human contigs that might cause issues
                        common_viral_contigs = ["chrEBV", "chrHPV", "NC_007605", "chrVirus"]
                        for viral_contig in common_viral_contigs:
                            if viral_contig not in excluded_contigs:
                                excluded_contigs.append(viral_contig)
                                # Add to detected list for reporting
                                if viral_contig not in non_human_contigs["detected"]:
                                    non_human_contigs["detected"].append(viral_contig)
                                    # Add contig type if available
                                    if viral_contig in contig_info:
                                        contig_type = contig_info[viral_contig]["type"]
                                        if contig_type not in non_human_contigs["identified_types"]:
                                            non_human_contigs["identified_types"].append(contig_type)
                                            
                                logger.info(f"Job {local_job_id}: Proactively excluding viral contig {viral_contig} for retry")
                    
                    logger.warning(f"Job {local_job_id}: GATK attempt {attempt+1} failed, will retry excluding {len(excluded_contigs)} contigs")
                    continue
        
    except Exception as e:
        error_message = f"Error running GATK HaplotypeCaller: {str(e)}"
        logger.exception(f"Job {local_job_id}: {error_message}")
        
        update_job_status(local_job_id, JOB_STATUS_ERROR, progress=100, 
                         message="GATK variant calling failed",
                         error=error_message)
        return None

@app.get("/health")
def health_check():
    """Health check endpoint with enhanced information"""
    # Count jobs by status for monitoring
    status_counts = {}
    for job in jobs.values():
        status = job.get("status", "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    
    return {
        "status": "healthy",
        "timestamp": time.time(),
        "jobs_count": len(jobs),
        "jobs_by_status": status_counts,
        "reference_genomes": list(REFERENCE_PATHS.keys()),
    }

@app.get("/jobs")
def list_jobs(status: Optional[str] = None):
    """List all jobs (with optional filtering)"""
    result = []
    for job_id, job_info in jobs.items():
        # Apply status filter if specified
        if status and job_info.get('status') != status:
            continue
            
        # Include basic info for each job
        result.append({
            "job_id": job_id,
            "status": job_info.get("status"),
            "progress": job_info.get("progress"),
            "message": job_info.get("message"),
            "created_at": job_info.get("created_at"),
            "updated_at": job_info.get("updated_at"),
        })
    
    return result

# Assembly-defining sequence lengths, keyed by the @SQ SN name with any `chr`
# prefix removed (GRCh37/b37 headers say `1`, hg19/hg38 headers say `chr1`) and
# valued with the *assembly*, not with a reference key -- which reference file
# an assembly corresponds to also depends on the naming convention, below.
#
# These are exact facts about the assemblies, not heuristics: no two GRC builds
# give the same chromosome the same length, and a nine-digit number appearing as
# the LN of the correspondingly named @SQ record cannot be a coincidence the way
# a three-byte substring can.
#
# Every value below was read out of the sequence dictionaries this deployment
# actually ships, not recalled: reference/hg38/Homo_sapiens_assembly38.dict,
# reference/hg19/ucsc.hg19.dict and reference/grch37/human_g1k_v37.dict. They
# cannot be pinned by a test -- reference/ is gitignored (.gitignore:203), the
# same reason tests/test_pharmcat_schema_gate_265.py's data/reports glob is empty
# in a fresh checkout -- so re-read them if you touch this table.
SQ_LENGTH_ASSEMBLIES = {
    ('1', 248956422): 'GRCh38',
    ('1', 249250621): 'GRCh37',
    ('2', 242193529): 'GRCh38',
    ('2', 243199373): 'GRCh37',
    ('3', 198295559): 'GRCh38',
    ('3', 198022430): 'GRCh37',
    ('X', 156040895): 'GRCh38',
    ('X', 155270560): 'GRCh37',
}

# (assembly, sequences are chr-prefixed) -> the REFERENCE_PATHS key whose FASTA
# a file of that description can actually be aligned against.
#
# The naming convention is evidence, not cosmetics. The two GRCh37 dictionaries
# this deployment ships carry the same four lengths under different names --
# ucsc.hg19.dict says `SN:chr1`, human_g1k_v37.dict says `SN:1` -- and they are
# two different FASTAs, so a b37-named file run against ucsc.hg19.fasta fails on
# incompatible contigs (and vice versa). Throwing the prefix away and answering
# only "GRCh37" would leave the caller a 50/50 guess between them on the one axis
# that decides whether the run can work at all.
#
# They are not even the same sequence throughout: hg19 chr3 is
# M5:641e4338fa8d52a5b781bd2a2c08d3c3 and b37 3 is
# M5:fdfd811849cc2fadebc929bb925902e5, identical length notwithstanding.
#
# GRCh38 has one entry because it has one file: REFERENCE_PATHS['grch38'] is the
# same path string as REFERENCE_PATHS['hg38'], and reference/grch38/ holds a
# symlink to it. There is no un-prefixed GRCh38 FASTA here to point at.
ASSEMBLY_REFERENCE_KEYS = {
    ('GRCh38', True): 'hg38',
    ('GRCh38', False): 'hg38',
    ('GRCh37', True): 'hg19',
    ('GRCh37', False): 'grch37',
}


def is_chr_prefixed(name):
    """True when an @SQ SN uses UCSC-style `chr1` rather than Ensembl/b37 `1`."""
    return (name or '').strip().lower().startswith('chr')


def normalise_sq_name(name):
    """`chr1` / `1` / `CHR1` -> `1`, so both naming conventions match one table."""
    name = (name or '').strip()
    if name.lower().startswith('chr'):
        name = name[3:]
    return name.upper()


def reference_from_sam_header(header):
    """Return the REFERENCE_PATHS key a SAM/BAM/CRAM header states, or None.

    Reads the @SQ records, which are the header's actual description of the
    coordinate system every alignment in the file is expressed in -- as opposed
    to a three-character token appearing somewhere in the first 10 KiB, which
    over BGZF is deflate output and carries no information at all.

    Answers with a *reference key* (`hg38` / `hg19` / `grch37`), not merely an
    assembly, because naming the assembly is not enough to pick a FASTA: GRCh37
    ships here twice, as chr-prefixed ucsc.hg19.fasta and un-prefixed
    human_g1k_v37.fasta, and aligning against the wrong one of those pair fails
    on incompatible contigs. The SN prefix says which, so it is read rather than
    discarded -- see ASSEMBLY_REFERENCE_KEYS.

    Parses field-wise rather than matching a literal `"SN:chr1\\tLN:248956422"`:
    SAM tags are unordered, so a header carrying `SN:chr1 M5:... LN:248956422`
    describes exactly the same assembly and used to read as undetectable.

    A header whose @SQ records point at two different assemblies -- or at one
    assembly under both naming conventions -- is reported as *undetectable*, not
    as whichever appeared first. Either combination means the file is not what
    any single answer would claim, and guessing is precisely the failure being
    removed here.

    Falls back to the `-R <path>` of an @PG line only when the @SQ records said
    nothing -- a recorded command line is evidence about the tool run, not about
    the records in this file, and can survive a subsequent liftover.
    """
    seen = set()
    program_lines = []

    for line in (header or '').splitlines():
        if line.startswith('@PG'):
            program_lines.append(line)
            continue
        if not line.startswith('@SQ'):
            continue

        raw_name = None
        length = None
        for field in line.split('\t')[1:]:
            if field.startswith('SN:'):
                raw_name = field[3:]
            elif field.startswith('LN:'):
                try:
                    length = int(field[3:].strip())
                except ValueError:
                    length = None
        if raw_name is None or length is None:
            continue
        assembly = SQ_LENGTH_ASSEMBLIES.get((normalise_sq_name(raw_name), length))
        if assembly:
            seen.add((assembly, is_chr_prefixed(raw_name)))

    if len(seen) == 1:
        detected = ASSEMBLY_REFERENCE_KEYS[seen.pop()]
        logger.info(f"Detected {detected} from the @SQ records in the header")
        return detected
    if len(seen) > 1:
        logger.warning(
            f"Header @SQ records describe more than one reference "
            f"({sorted(seen)}); reporting it as undetectable rather than "
            f"picking one"
        )
        return None

    for line in program_lines:
        # `-R` is GATK's reference flag, but it is *bwa mem's read-group* flag,
        # where the token is an @RG string and any hg19/hg38 inside it is a
        # sample or library name. Requiring the capture to look like a FASTA
        # path keeps a read group from being read as a reference.
        ref_path_match = re.search(r'-R\s+(\S+)', line)
        if not ref_path_match:
            continue
        ref_path = ref_path_match.group(1)
        if not re.search(r'\.(fa|fasta|fna)(\.gz)?$', ref_path, re.IGNORECASE):
            logger.info(
                f"Ignoring @PG -R value {ref_path!r}: not a FASTA path, so it is "
                f"a read group rather than a reference"
            )
            continue
        logger.info(f"Found reference path in @PG line: {ref_path}")
        if "hg38" in ref_path or "GRCh38" in ref_path:
            return "hg38"
        if "hg19" in ref_path:
            return "hg19"
        if "GRCh37" in ref_path or "g1k_v37" in ref_path:
            return "grch37"

    return None


def looks_like_text(blob):
    """True when a byte string is plausibly a text header rather than a container.

    The token scan below is only meaningful over text. Over compressed bytes --
    BGZF, which is what BAM, CRAM, BCF and bgzipped VCF all are -- `b38` and
    `b37` are three-byte substrings that turn up by chance roughly once in a few
    hundred files, and a chance hit used to *override* the caller's own
    reference_genome. A NUL byte or an undecodable sequence is proof the buffer
    is not a header, so the scan is skipped rather than believed.

    `blob` is a fixed-size read off the front of a file, so its last few bytes
    may be half of a multi-byte character that the rest of the file completes.
    Up to three trailing bytes are therefore dropped before giving up -- a
    truncated character is an artefact of the read, not evidence of a container,
    and failing on it would silently skip the scan for any UTF-8 text file whose
    10240th byte happened to land mid-character.
    """
    if not blob or b'\x00' in blob:
        return False
    for trim in range(0, 4):
        candidate = blob[: len(blob) - trim] if trim else blob
        if not candidate:
            return False
        try:
            candidate.decode('utf-8')
            return True
        except UnicodeDecodeError:
            continue
    return False


def detect_reference(file_path, default_reference='hg38'):
    """
    Detect the reference genome a genomic file is expressed against.

    Structured header evidence first, and for the compressed formats *only*
    structured header evidence: `samtools view -H` for BAM/CRAM/SAM, the
    `##reference=` line for VCF (through gzip when bgzipped). The raw-bytes
    token scan that used to run first is now the last resort, and only over a
    buffer that is actually text -- see looks_like_text().

    Returns `default_reference` when nothing conclusive was found. Callers must
    treat that as "no answer" rather than as a detection: a wrong answer here
    means an analysis against the wrong coordinates, which is worse than no
    answer at all.
    """
    try:
        logger.info(f"Attempting to detect reference genome from file: {file_path}")
        # stored_extension(), not os.path.splitext()[1]: file_path is built from a
        # safe_upload_name() result, so a bgzipped VCF gave `.gz` here and the
        # `.vcf.gz` branch below could never run for the one format it names.
        file_ext = stored_extension(os.path.basename(file_path))
        gzipped_vcf = file_ext == '.vcf.gz'

        # For BAM/CRAM/SAM the header is the evidence, and it is the *first*
        # thing consulted -- not a fallback for when a text scan over BGZF
        # "failed". BAM and CRAM are compressed containers, so the scan could
        # only ever produce noise over them, and this is the main upload path.
        if file_ext in ['.bam', '.cram', '.sam']:
            try:
                logger.info(f"Reading the alignment header with samtools")
                # Use samtools to get the header. List argv, not shell=True: file_path
                # is built from an uploaded filename, and the conversion routes now
                # call this function, so a name like `a;rm -rf /.cram` would otherwise
                # reach /bin/sh.
                result = subprocess.run(
                    ["samtools", "view", "-H", file_path],
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    detected = reference_from_sam_header(result.stdout)
                    if detected:
                        return detected
                    logger.info("The alignment header does not name a known assembly")
                else:
                    logger.warning(
                        f"samtools could not read the header (rc={result.returncode})"
                    )
            except Exception as e:
                logger.warning(f"Samtools detection failed: {str(e)}")

            # Deliberately no raw-bytes fallback for these three. A BAM or CRAM
            # whose header samtools cannot read is not a file to guess about.
            logger.warning(f"Could not determine reference genome, using default: {default_reference}")
            return default_reference

        # For VCF files, check header lines explicitly
        if file_ext in ['.vcf', '.vcf.gz']:
            try:
                # Open as text; through gzip when the VCF is bgzipped, which is
                # the whole reason this branch was unreachable before -- plain
                # open() on deflate output yields nothing that starts with '#'.
                opener = gzip.open if gzipped_vcf else open
                with opener(file_path, 'rt', errors='ignore') as f:
                    for line in f:
                        if not line.startswith('#'):
                            break
                        # Look for reference in header lines
                        if '##reference=' in line:
                            ref_field = line.strip().split('=')[1]
                            if any(x in ref_field for x in ['GRCh38', 'hg38']):
                                logger.info(f"Detected hg38 from VCF header reference field")
                                return 'hg38'
                            elif any(x in ref_field for x in ['GRCh37', 'hg19']):
                                logger.info(f"Detected hg19 from VCF header reference field") 
                                return 'hg19'
            except Exception as e:
                logger.warning(f"VCF header parsing failed: {str(e)}")

        else:
            # Last resort, for a format with no structured reader here (.bcf, or
            # an upload whose name carried no allowlisted extension at all).
            #
            # Guarded twice over what it used to be: it never runs for a format
            # whose header was read properly above, and it never runs over bytes
            # that are not text, so it can no longer answer from deflate output.
            # `b38`/`b37` additionally need word boundaries -- unanchored, three
            # characters is not a token, it is a substring.
            try:
                logger.info(f"Falling back to a text scan of the first 10 KiB")
                with open(file_path, 'rb') as f:
                    blob = f.read(10240)

                if looks_like_text(blob):
                    # errors='ignore' only for the scan itself: looks_like_text()
                    # has already made the is-this-text decision strictly, so all
                    # this can drop is a character the 10 KiB read cut in half.
                    header = blob.decode('utf-8', errors='ignore')
                    if 'GRCh38' in header or 'hg38' in header or re.search(r'\bb38\b', header):
                        logger.info(f"Detected hg38/GRCh38 reference via text search")
                        return 'hg38'
                    if 'GRCh37' in header or 'hg19' in header or re.search(r'\bb37\b', header):
                        logger.info(f"Detected hg19/GRCh37 reference via text search")
                        return 'hg19'
                    logger.info(f"Text scan did not find reference genome information")
                else:
                    logger.info(
                        f"First 10 KiB is not text, so it carries no readable header; "
                        f"not guessing a reference from compressed bytes"
                    )
            except Exception as e:
                logger.warning(f"Text scan failed: {str(e)}")

        # If we can't determine, return default
        logger.warning(f"Could not determine reference genome, using default: {default_reference}")
        return default_reference
    except Exception as e:
        logger.error(f"Error detecting reference genome: {str(e)}")
        return default_reference

@app.post("/variant-call")
async def variant_call(
    file: UploadFile = File(...),
    reference_genome: str = Form("hg38"),
    regions: Optional[str] = Form(None),
    job_id: Optional[str] = Form(None),  # Zaro Job PK for JobClient
    test_mode: bool = Form(False),
    patient_id: Optional[str] = Form(None),
    step_name: Optional[str] = Form("gatk_variant_calling")
):
    """
    Start a variant calling job using GATK HaplotypeCaller

    This endpoint starts an asynchronous GATK job and returns a job ID
    that can be used to check the status of the job.
    """
    try:
        local_job_id = str(uuid.uuid4())
        logger.info(f"Generated local GATK call id: {local_job_id}")
        zaro_job_id = job_id  # Form value; may be None

        if file.filename == '':
            logger.error("No filename specified in request")
            raise HTTPException(status_code=400, detail="No filename specified")

        logger.info(f"Job {local_job_id}: Request received - File: {file.filename}, Reference: {reference_genome}, Regions: {regions}")

        # Initialize job client if Zaro Job PK is provided
        job_client = None
        if zaro_job_id:
            try:
                job_client = JobClient(job_id=zaro_job_id, step_name=step_name)
                
                # Check if job has been cancelled before starting
                if await job_client.is_job_cancelled():
                    logger.info(f"Job {zaro_job_id} is cancelled, aborting GATK processing")
                    return {"success": False, "error": "Workflow has been cancelled"}
                
                await job_client.start_step(f"Starting GATK variant calling for {file.filename}")
                await job_client.log_progress(f"Processing {file.filename} with GATK", {
                    "filename": file.filename,
                    "reference_genome": reference_genome,
                    "regions": regions
                })
            except Exception as e:
                logger.warning(f"Failed to initialize workflow client: {e}")
                job_client = None

        # Save uploaded file to a temporary directory. The stored name is rebuilt
        # from parts this module controls -- the uploaded one reaches three separate
        # subprocess call sites (detect_reference, samtools index, HaplotypeCaller)
        # and is attacker-controlled. file.filename is still used for the log lines
        # and JobClient messages below, so the original is not lost to an operator.
        original_filename = file.filename
        filename = safe_upload_name(original_filename, local_job_id)
        input_dir = tempfile.mkdtemp(dir=TEMP_DIR)
        input_path = os.path.join(input_dir, filename)
        output_path = os.path.join(input_dir, f"{stored_stem(filename)}.vcf")

        if filename != original_filename:
            logger.info(
                f"Job {local_job_id}: storing upload {original_filename!r} as {filename!r}"
            )

        logger.info(f"Job {local_job_id}: Saving file to {input_path}")
        # Chunked for the same reason as the CRAM/SAM routes: a whole-genome BAM
        # does not fit as a single in-memory `bytes`, and the old single
        # `f.write(await file.read())` blocked the event loop for the whole read.
        with open(input_path, "wb") as f:
            while chunk := await file.read(UPLOAD_CHUNK_BYTES):
                f.write(chunk)
        logger.info(f"Job {local_job_id}: Saved uploaded file to {input_path}")
        
        # Update workflow with file information
        if job_client:
            file_size = os.path.getsize(input_path)
            await job_client.log_progress(f"File uploaded: {file_size} bytes", {
                "file_size_bytes": file_size,
                "input_path": input_path
            })
        
        # Check if file exists
        if not os.path.exists(input_path):
            logger.error(f"Job {local_job_id}: Failed to save uploaded file to {input_path}")
            if job_client:
                await job_client.fail_step(f"Failed to save uploaded file to {input_path}", {
                    "error_type": "file_save_error",
                    "input_path": input_path
                })
            raise HTTPException(status_code=500, detail=f"Failed to save uploaded file to {input_path}")
        
        # Log file details
        file_size = os.path.getsize(input_path)
        logger.info(f"Job {local_job_id}: File saved: {input_path}, size: {file_size} bytes")
        
        # Auto-detect reference genome for all genomic file types. The extension
        # comes from stored_extension(), not os.path.splitext(): splitext returns
        # `.gz` for a `.vcf.gz` name, which matches nothing in this list, so
        # bgzipped VCFs silently skipped detection.
        #
        # Detection is still allowed to override reference_genome, and that is a
        # deliberate choice rather than an oversight. The form field cannot be
        # trusted as an explicit statement: it is declared `Form("hg38")`, and
        # pipelines/pgx/main.nf always sends *something* for it, so this service
        # cannot tell a user's considered choice from a default that was never
        # touched. The file's own header, by contrast, describes the coordinate
        # system the records are actually in. Between a header and a form
        # default, the header wins.
        #
        # What changed is that detect_reference() no longer answers from a
        # three-byte substring of compressed bytes -- BAM and CRAM go through
        # `samtools view -H` and the @SQ records, and there is no raw-bytes
        # fallback for them -- so an override now follows evidence rather than
        # noise. That is what makes keeping the override defensible.
        #
        # The comparison is on the FASTA each name resolves to, not on the name
        # and not on the assembly. Those three differ in ways that matter:
        #
        #   hg38 vs grch38 -> REFERENCE_PATHS gives the *same path string* for
        #       both (reference/grch38/ holds a symlink), so overriding one with
        #       the other is churn with no effect. Comparing names would do it.
        #   hg19 vs grch37 -> same assembly, two different FASTAs with different
        #       contig naming (`chr1` vs `1`). Aligning against the wrong one of
        #       that pair fails on incompatible contigs. Comparing *assemblies*
        #       would suppress this override, which is the one case where it is
        #       most needed -- and detect_reference() now reads the SN prefix
        #       precisely so it can tell them apart.
        #
        # "Would this change which file we align against?" is the question the
        # override is actually asking, so it is the one asked here.
        file_ext = stored_extension(filename)
        if file_ext in ['.bam', '.cram', '.sam', '.vcf', '.vcf.gz']:
            detected_reference = detect_reference(input_path, default_reference=reference_genome)
            requested_fasta = REFERENCE_PATHS.get((reference_genome or '').lower())
            detected_fasta = REFERENCE_PATHS.get((detected_reference or '').lower())
            if detected_fasta and detected_fasta != requested_fasta:
                logger.warning(f"Job {local_job_id}: Detected reference ({detected_reference}) differs from specified reference ({reference_genome})")
                logger.warning(f"Job {local_job_id}: Using detected reference: {detected_reference}")
                reference_genome = detected_reference
            elif detected_reference != reference_genome:
                logger.info(
                    f"Job {local_job_id}: detected {detected_reference} resolves to the "
                    f"same FASTA as the requested {reference_genome}; keeping the "
                    f"caller's choice"
                )

        # Validate reference genome
        if reference_genome not in REFERENCE_PATHS:
            logger.error(f"Job {local_job_id}: Unsupported reference genome: {reference_genome}")
            raise HTTPException(status_code=400, detail=f"Unsupported reference genome: {reference_genome}")

        reference_path = REFERENCE_PATHS[reference_genome]
        if not os.path.exists(reference_path):
            logger.error(f"Job {local_job_id}: Reference genome file not found: {reference_path}")
            raise HTTPException(status_code=500, detail=f"Reference genome file not found: {reference_path}")

        # Initialize job info
        jobs[local_job_id] = {
            "status": JOB_STATUS_PENDING,
            "progress": 0,
            "message": "Job initialized",
            "input_file": input_path,
            "output_file": None,
            "reference_genome": reference_genome,
            "regions": regions,
            "job_id": local_job_id,
            # The Zaro workflow's job id, distinct from the "job_id" key above
            # (this module's own local_job_id). Cancel matches on this one --
            # see cancel_workflow_job().
            "zaro_job_id": zaro_job_id,
            "patient_id": patient_id,
            "created_at": time.time(),
            "updated_at": time.time()
        }

        # Determine if it's a BAM/CRAM or VCF file. Same reason as above for
        # stored_extension(): with splitext, a `.vcf.gz` upload reported `.gz`,
        # missed both branches below and was rejected by the `else` as an
        # "Unsupported file format" -- despite safe_upload_name() having
        # deliberately preserved `.vcf.gz` so this branch could see it.
        file_ext = stored_extension(filename)

        if file_ext in ['.vcf', '.vcf.gz']:
            # If it's already a VCF, just return the path
            logger.info(f"Job {local_job_id}: File is already a VCF, returning directly")
            update_job_status(local_job_id, JOB_STATUS_COMPLETED, progress=100, 
                             message="File already contains variants",
                             output_file=input_path)
            
            # Complete workflow step for VCF files
            if job_client:
                await job_client.complete_step("File already contains variants", {
                    "file_type": file_ext,
                    "output_file": input_path
                })
            
            return {
                "job_id": local_job_id,
                "status": JOB_STATUS_COMPLETED,
                "progress": 100,
                "message": "File already contains variants",
                "output_file": input_path
            }

        elif file_ext in ['.bam', '.cram', '.sam']:
            logger.info(f"Job {local_job_id}: Starting processing for {file_ext} file")
            
            # Update workflow with processing start
            if job_client:
                await job_client.log_progress(f"Starting variant calling for {file_ext} file", {
                    "file_type": file_ext,
                    "reference_genome": reference_genome,
                    "regions": regions
                })
            
            # For BAM files, create an index first
            if file_ext == '.bam':
                # Update status to indexing first
                update_job_status(local_job_id, JOB_STATUS_PENDING, progress=5, 
                                 message="Starting BAM file indexing")
                
                # Start the processing in a background thread
                threading.Thread(
                    target=process_bam_file,
                    args=(local_job_id, input_path, output_path, reference_path, regions, zaro_job_id, patient_id),
                    daemon=True
                ).start()
            else:
                # For other formats, start variant calling directly
                update_job_status(local_job_id, JOB_STATUS_PENDING, progress=5, 
                                 message=f"Starting variant calling for {file_ext} file")
                
                # Start variant calling in a background thread
                import asyncio
                def run_async_variant_calling():
                    # Wait for a variant-calling slot so concurrent jobs don't each
                    # launch a full-heap HaplotypeCaller at once (see the semaphore note).
                    with _variant_calling_semaphore:
                        asyncio.run(run_variant_calling(local_job_id, input_path, output_path, reference_path, regions, zaro_job_id, patient_id))

                threading.Thread(
                    target=run_async_variant_calling,
                    daemon=True
                ).start()

            return {
                "job_id": local_job_id,
                "status": JOB_STATUS_PENDING,
                "progress": 5,
                "message": f"Processing started for {file_ext} file"
            }

        else:
            logger.error(f"Job {local_job_id}: Unsupported file format: {file_ext}")
            if job_client:
                await job_client.fail_step(f"Unsupported file format: {file_ext}", {
                    "error_type": "unsupported_format",
                    "file_ext": file_ext
                })
            raise HTTPException(status_code=400, detail=f"Unsupported file format: {file_ext}")

    except Exception as e:
        logger.exception(f"Unexpected error in variant-call endpoint: {str(e)}")
        if job_client:
            await job_client.fail_step(f"Unexpected error: {str(e)}", {
                "error_type": "unexpected_error",
                "error_message": str(e)
            })
        raise HTTPException(status_code=500, detail=str(e))

def process_bam_file(local_job_id, input_path, output_path, reference_path, regions, zaro_job_id=None, patient_id=None):
    """Process a BAM file: first index it, then call variants"""
    try:
        # First, index the BAM file
        success, message = index_bam_file(local_job_id, input_path)
        
        if not success:
            logger.error(f"Job {local_job_id}: BAM indexing failed: {message}")
            update_job_status(local_job_id, JOB_STATUS_ERROR, progress=100, 
                             message=f"BAM indexing failed: {message}",
                             error=message)
            return
            
        # If indexing succeeded, continue with variant calling. Indexing above is
        # light; gate only the heavy HaplotypeCaller run on a variant-calling slot.
        logger.info(f"Job {local_job_id}: BAM indexing completed, proceeding to variant calling")
        with _variant_calling_semaphore:
            asyncio.run(run_variant_calling(local_job_id, input_path, output_path, reference_path, regions, zaro_job_id, patient_id))
        
    except Exception as e:
        logger.exception(f"Job {local_job_id}: Error in BAM file processing: {str(e)}")
        update_job_status(local_job_id, JOB_STATUS_ERROR, progress=100, 
                         message=f"Error in BAM file processing",
                         error=str(e))

@app.get("/job/{job_id}")
async def job_status(job_id: str):
    """Get the status of a variant calling job with enhanced details"""
    if job_id not in jobs:
        logger.warning(f"Job status request for unknown job: {job_id}")
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]
    response = {
        "job_id": job_id,
        "status": job.get("status"),
        "progress": job.get("progress"),
        "message": job.get("message"),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
    }

    if job.get("status") == JOB_STATUS_COMPLETED:
        response["output_file"] = job.get("output_file")
        
        # Include extras if available
        if "extras" in job:
            response["extras"] = job.get("extras")
            
    elif job.get("status") == JOB_STATUS_ERROR:
        response["error"] = job.get("error", "Unknown error")

    logger.info(f"Job status request for job {job_id}: {job.get('status')}, progress: {job.get('progress')}%")
    return response

def ensure_reference_dictionaries():
    """Check if GATK dictionaries exist for reference genomes and create them if needed"""
    for genome_name, fasta_path in REFERENCE_PATHS.items():
        if os.path.exists(fasta_path):
            # Check if dictionary exists
            dict_path = os.path.splitext(fasta_path)[0] + '.dict'
            if not os.path.exists(dict_path):
                logger.info(f"Creating sequence dictionary for {genome_name} at {dict_path}")
                try:
                    cmd = f"gatk CreateSequenceDictionary -R {fasta_path}"
                    subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True)
                    logger.info(f"Created sequence dictionary for {genome_name}")
                except Exception as e:
                    logger.error(f"Failed to create sequence dictionary for {genome_name}: {str(e)}")

            else:
                logger.info(f"Sequence dictionary for {genome_name} already exists at {dict_path}")
        else:
            logger.warning(f"Reference genome {genome_name} not found at {fasta_path}")

@app.get("/diagnostic")
async def diagnostic():
    """Provide a comprehensive diagnostic overview of the GATK API service"""
    try:
        # Collect system information
        system_info = {
            "python_version": sys.version,
            "platform": sys.platform,
            "processor": platform.processor(),
            "cpu_count": os.cpu_count(),
            "memory": psutil.virtual_memory()._asdict() if 'psutil' in sys.modules else "psutil not available",
            "filesystem": {path: {"free": shutil.disk_usage(path).free, "total": shutil.disk_usage(path).total} 
                         for path in ["/data", "/tmp", "/app"] if os.path.exists(path)},
            "environment_variables": {k: v for k, v in os.environ.items() 
                                      if not k.lower() in ["password", "secret", "key", "token"]},
        }
        
        # Collect GATK information
        gatk_info = {}
        try:
            result = subprocess.run(["gatk", "--version"], capture_output=True, text=True, timeout=10)
            gatk_info["version"] = result.stdout.strip()
            gatk_info["status"] = "available"
        except Exception as e:
            gatk_info["status"] = "error"
            gatk_info["error"] = str(e)
        
        # Reference genome information
        reference_info = {}
        for name, path in REFERENCE_PATHS.items():
            reference_info[name] = {
                "path": path,
                "exists": os.path.exists(path),
                "size": os.path.getsize(path) if os.path.exists(path) else None,
                "dict_path": os.path.splitext(path)[0] + '.dict',
                "dict_exists": os.path.exists(os.path.splitext(path)[0] + '.dict')
            }
            
        # Jobs information
        jobs_info = {
            "total": len(jobs),
            "by_status": {},
            "recent_jobs": []
        }
        
        # Count jobs by status
        for job in jobs.values():
            status = job.get("status", "unknown")
            if status not in jobs_info["by_status"]:
                jobs_info["by_status"][status] = 0
            jobs_info["by_status"][status] += 1
            
        # Get 5 most recent jobs
        recent_jobs = sorted(
            [(job_id, job.get("updated_at", 0)) for job_id, job in jobs.items()],
            key=lambda x: x[1],
            reverse=True
        )[:5]
        
        for job_id, _ in recent_jobs:
            if job_id in jobs:
                job_data = jobs[job_id].copy()
                # Remove potentially large data
                if "output_file" in job_data and job_data["output_file"]:
                    job_data["output_file_exists"] = os.path.exists(job_data["output_file"])
                    job_data["output_file_size"] = os.path.getsize(job_data["output_file"]) if os.path.exists(job_data["output_file"]) else None
                    job_data["output_file"] = os.path.basename(job_data["output_file"])
                
                if "input_file" in job_data and job_data["input_file"]:
                    job_data["input_file_exists"] = os.path.exists(job_data["input_file"])
                    job_data["input_file_size"] = os.path.getsize(job_data["input_file"]) if os.path.exists(job_data["input_file"]) else None
                    job_data["input_file"] = os.path.basename(job_data["input_file"])
                    
                jobs_info["recent_jobs"].append({
                    "job_id": job_id,
                    "data": job_data
                })
        
        return {
            "timestamp": time.time(),
            "status": "running",
            "uptime": time.time() - app_start_time,
            "system": system_info,
            "gatk": gatk_info,
            "reference_genomes": reference_info,
            "jobs": jobs_info,
            "routes": [{"endpoint": route.name, "methods": list(route.methods), "path": route.path} 
                      for route in app.routes]
        }
    except Exception as e:
        logger.exception(f"Error in diagnostic endpoint: {str(e)}")
        return {
            "status": "error",
            "timestamp": time.time(),
            "error": str(e),
            "traceback": traceback.format_exc() if 'traceback' in sys.modules else "traceback not available"
        }

# Uploads are streamed to disk in chunks of this size rather than read into memory.
UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024

# `samtools sort` budget. Deliberately small: this container's memory limit is shared
# with a GATK Java heap sized by MAX_MEMORY (20g by default), so a conversion must
# never be able to starve a concurrent variant-calling run. -m is per thread, so the
# ceiling is roughly SORT_THREADS x SORT_MEMORY -- ~2.3 GiB at these defaults.
# 768M is samtools' own default; both are env-overridable for a bigger host.
SORT_MEMORY = os.environ.get("SAMTOOLS_SORT_MEMORY", "768M")
SORT_THREADS = os.environ.get("SAMTOOLS_SORT_THREADS", "2")

# asyncio.to_thread() hands the call to the loop's default executor, whose own cap
# is min(32, os.cpu_count() + 4) workers -- not this container's memory. Each worker
# running convert_to_indexed_bam() can drive a `samtools sort` at up to roughly
# SORT_THREADS x SORT_MEMORY (~2.3 GiB at the defaults above); the executor handing
# out anywhere near its own 32-worker ceiling would multiply that past this
# container's limit, which is shared with a GATK Java heap sized by MAX_MEMORY (20g
# by default). This semaphore caps how many of those to_thread calls may run at
# once, independently of the executor's own worker count. Small by default and
# env-overridable for a bigger host.
TO_THREAD_CONCURRENCY_LIMIT = int(os.environ.get("TO_THREAD_CONCURRENCY_LIMIT", "2"))
_to_thread_semaphore = asyncio.Semaphore(TO_THREAD_CONCURRENCY_LIMIT)

# /variant-call runs GATK HaplotypeCaller in its own daemon thread with its own event
# loop (asyncio.run), so the loop-bound _to_thread_semaphore above cannot reach it and
# concurrent requests would each spawn a HaplotypeCaller with a full Java heap
# (MAX_MEMORY, 20g by default) -> OOM. This is a threading.Semaphore (loop-agnostic, so
# it works across those per-job threads) capping how many variant-calling jobs run at
# once; the default is 1 given the heap size, env-overridable for a bigger host.
GATK_VARIANT_CALL_CONCURRENCY = int(
    os.environ.get("GATK_VARIANT_CALL_CONCURRENCY", "1")
)
_variant_calling_semaphore = threading.Semaphore(GATK_VARIANT_CALL_CONCURRENCY)


def _discard_output(path):
    """Delete an untrustworthy conversion output. Best effort, never raises."""
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError as exc:
        logger.warning(f"Could not remove {path}: {exc}")


def _cleanup_dir(path):
    """Remove a scratch directory and everything under it. Never raises."""
    if path:
        shutil.rmtree(path, ignore_errors=True)


def _safe_scope(value, fallback):
    """Sanitise a caller-supplied id before it becomes a directory name.

    job_id and patient_id arrive as multipart form fields, so they cannot be joined
    into a path unchecked.
    """
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "", str(value or "")).strip(".")
    return cleaned or fallback


def conversion_output_dir(local_job_id, job_id=None, patient_id=None):
    """Return the directory a converted BAM must be written to.

    Not this container's /tmp. The caller is a Nextflow process running in the
    nextflow container, which copies `bam_path` by path; the only filesystem both
    containers share is the ./data bind mount (`/data` in each). A BAM under
    TMPDIR=/tmp/gatk_temp lives on this container's private writable layer and is
    unreadable to the caller, so reporting it as a success would repeat the defect
    this module was fixed for in a subtler form. pypgx already follows this
    convention -- its TEMP_DIR is DATA_DIR/'temp' for the same reason.

    Scoped by the Zaro job id where there is one, because
    app/services/cleanup_service.py already reaps /data/results/{job_id}.
    """
    scope = _safe_scope(job_id, _safe_scope(patient_id, ""))
    parts = [DATA_DIR, "results"]
    if scope:
        parts.append(scope)
    parts.append(local_job_id)
    output_dir = os.path.join(*parts)
    os.makedirs(output_dir, exist_ok=True)
    return output_dir


# Things samtools can say on stderr while still exiting 0, which must nonetheless
# sink the conversion. htslib downgrades some genuinely corrupting conditions to a
# warning -- anything about the reference above all, since a CRAM decoded against the
# wrong or an unfetchable reference yields real-looking records with wrong bases.
STDERR_FATAL_MARKERS = (
    ("[e::", "an htslib error"),
    ("reference", "a reference problem"),
    ("@sq", "a sequence-dictionary problem"),
    ("md5", "a reference checksum problem"),
    ("truncat", "a truncated file"),
    ("corrupt", "a corrupt file"),
)


def _fatal_stderr(stderr):
    """Return a description of the fatal complaint in `stderr`, or None if benign."""
    haystack = (stderr or "").lower()
    for marker, description in STDERR_FATAL_MARKERS:
        if marker in haystack:
            return description
    return None


def _looks_like_bam(path):
    """A real BAM is BGZF-framed, so it opens with the gzip magic bytes.

    One 2-byte read is cheap insurance against the failure this module used to ship:
    a 21-byte ASCII string saved as `.bam` and carried through the whole pipeline.
    """
    try:
        with open(path, "rb") as handle:
            return handle.read(2) == b"\x1f\x8b"
    except OSError:
        return False


def run_samtools_conversion(job_label, argv, output_bam):
    """Run a samtools conversion (`view -b` or `sort`) and prove it made a real BAM.

    Every failure path raises HTTPException *and* removes the output, so a caller
    that returns normally can promise `output_bam` exists and is a BAM. No endpoint
    in this module may answer `success: true` over a file samtools did not write
    (BACKLOG 0 / 51 / 113 / 115).

    `argv` is a list, never a shell string: an uploaded filename reaches this
    command line and must not be re-parsed by /bin/sh.

    Returns:
        int: size of the produced BAM in bytes.
    """
    logger.info(f"Job {job_label}: running {' '.join(argv)}")
    try:
        result = subprocess.run(argv, capture_output=True, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        _discard_output(output_bam)
        message = f"samtools could not be executed - check container configuration ({exc})"
        logger.error(f"Job {job_label}: {message}")
        raise HTTPException(status_code=500, detail=message)

    stderr = (result.stderr or b"").decode("utf-8", errors="replace").strip()

    if result.returncode != 0:
        _discard_output(output_bam)
        message = f"{argv[1]} failed with exit code {result.returncode}: {stderr}"
        logger.error(f"Job {job_label}: {message}")
        raise HTTPException(status_code=500, detail=message)

    fatal = _fatal_stderr(stderr)
    if fatal:
        _discard_output(output_bam)
        message = (
            f"samtools {argv[1]} exited 0 but reported {fatal}: {stderr}. "
            "Refusing to treat this output as a valid conversion."
        )
        logger.error(f"Job {job_label}: {message}")
        raise HTTPException(status_code=500, detail=message)

    if not os.path.exists(output_bam) or os.path.getsize(output_bam) == 0:
        _discard_output(output_bam)
        message = f"samtools {argv[1]} exited 0 but wrote no BAM to {output_bam}: {stderr}"
        logger.error(f"Job {job_label}: {message}")
        raise HTTPException(status_code=500, detail=message)

    if not _looks_like_bam(output_bam):
        _discard_output(output_bam)
        message = f"samtools {argv[1]} produced {output_bam}, which is not a BGZF-framed BAM"
        logger.error(f"Job {job_label}: {message}")
        raise HTTPException(status_code=500, detail=message)

    # The magic bytes only prove BGZF framing -- a bgzipped VCF would pass. quickcheck
    # parses the BAM header and verifies the EOF block, so it also catches a run that
    # died halfway and left a plausible-looking truncated file.
    try:
        check = subprocess.run(
            ["samtools", "quickcheck", "-v", output_bam],
            capture_output=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _discard_output(output_bam)
        message = f"could not verify {output_bam} with samtools quickcheck ({exc})"
        logger.error(f"Job {job_label}: {message}")
        raise HTTPException(status_code=500, detail=message)

    if check.returncode != 0:
        _discard_output(output_bam)
        complaint = (
            (check.stdout or b"") + b" " + (check.stderr or b"")
        ).decode("utf-8", errors="replace").strip()
        message = f"samtools quickcheck rejected {output_bam}: {complaint}"
        logger.error(f"Job {job_label}: {message}")
        raise HTTPException(status_code=500, detail=message)

    if stderr:
        logger.warning(f"Job {job_label}: samtools stderr: {stderr}")

    size = os.path.getsize(output_bam)
    logger.info(f"Job {job_label}: wrote {output_bam} ({size} bytes)")
    return size


def verify_reference_matches(job_label, input_path, reference_genome):
    """Reject a CRAM whose header disagrees with the reference the caller asked for.

    `reference_genome` is a user-supplied form field that reaches this service
    unvalidated (app/api/routes/upload_router.py takes it from the upload form and
    passes it through Nextflow). Everything upstream only checks that the named
    reference *exists*, never that it is the right one for this file -- so an hg19
    CRAM converted against the hg38 FASTA is the one remaining way this module can
    produce a wrong BAM rather than no BAM.

    In practice htslib usually errors out on its own, because the @SQ SN/LN records
    will not match. That is not guaranteed though -- M5 is optional in SAM -- so this
    is a deliberate belt-and-braces check in front of it.

    Inconclusive detection is not an error: detect_reference() only recognises a
    couple of assemblies, and refusing everything it cannot name would break
    legitimate conversions.
    """
    detected = detect_reference(input_path, default_reference=None)
    if not detected:
        logger.info(f"Job {job_label}: reference not determinable from header; proceeding")
        return

    wanted_build = REFERENCE_BUILDS.get((reference_genome or "").lower())
    detected_build = REFERENCE_BUILDS.get(detected.lower())
    if not wanted_build or not detected_build or wanted_build == detected_build:
        logger.info(f"Job {job_label}: header reference {detected} agrees with {reference_genome}")
        return

    message = (
        f"Reference mismatch: the file's header indicates {detected} "
        f"({detected_build}) but the request asked for {reference_genome} "
        f"({wanted_build}). Converting against the wrong reference produces a BAM "
        f"with incorrect coordinates, so this is refused rather than converted."
    )
    logger.error(f"Job {job_label}: {message}")
    raise HTTPException(status_code=400, detail=message)


def read_sort_order(job_label, input_path, reference_path=None):
    """Return the `@HD SO:` value from a SAM/CRAM header, or None if it does not say.

    `samtools view -H` reads the header and stops, so this stays cheap even on a
    whole-genome CRAM -- which is the point: it lets an already-coordinate-sorted
    input skip the sort entirely.

    Any failure returns None, which callers treat as "not known to be sorted" and
    therefore sort. Guessing "sorted" here would produce an unindexable BAM.
    """
    argv = ["samtools", "view", "-H"]
    if reference_path:
        argv += ["-T", reference_path]
    argv.append(input_path)

    try:
        result = subprocess.run(argv, capture_output=True, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(f"Job {job_label}: could not read header ({exc}); assuming unsorted")
        return None

    if result.returncode != 0:
        stderr = (result.stderr or b"").decode("utf-8", errors="replace").strip()
        logger.warning(f"Job {job_label}: header read failed ({stderr}); assuming unsorted")
        return None

    header = (result.stdout or b"").decode("utf-8", errors="replace")
    for line in header.splitlines():
        if not line.startswith("@HD"):
            continue
        for field in line.split("\t")[1:]:
            if field.startswith("SO:"):
                return field[3:].strip()
        return None
    return None


def index_output_bam(job_label, output_bam):
    """Index the converted BAM, or delete it and raise.

    Indexing requires coordinate-sorted input, so this doubles as the final proof
    that the sort decision above was right. Every downstream consumer -- PyPGx,
    GATK HaplotypeCaller, OptiType-from-BAM -- wants an indexed BAM, so handing back
    an unindexed one would be another success over an unusable file.

    Deliberately not reusing index_bam_file(): that one drives the in-memory `jobs`
    tracker these conversions are not registered in (every update would log a
    warning), and it shells out with shell=True on a path built from an uploaded
    filename.
    """
    index_path = f"{output_bam}.bai"
    argv = ["samtools", "index", output_bam]
    logger.info(f"Job {job_label}: running {' '.join(argv)}")

    try:
        result = subprocess.run(argv, capture_output=True, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        _discard_output(output_bam)
        message = f"samtools index could not be executed ({exc})"
        logger.error(f"Job {job_label}: {message}")
        raise HTTPException(status_code=500, detail=message)

    if result.returncode != 0:
        stderr = (result.stderr or b"").decode("utf-8", errors="replace").strip()
        _discard_output(index_path)
        _discard_output(output_bam)
        message = f"samtools index failed with exit code {result.returncode}: {stderr}"
        logger.error(f"Job {job_label}: {message}")
        raise HTTPException(status_code=500, detail=message)

    if not os.path.exists(index_path):
        _discard_output(output_bam)
        message = f"samtools index exited 0 but wrote no index at {index_path}"
        logger.error(f"Job {job_label}: {message}")
        raise HTTPException(status_code=500, detail=message)

    logger.info(f"Job {job_label}: indexed {output_bam}")
    return index_path


def count_records(job_label, output_bam, index_path):
    """Return the number of alignment records in an indexed BAM.

    Reads `samtools idxstats`, which answers from the .bai rather than by streaming
    the BAM, so this is cheap even on a whole genome.

    Zero is treated as fatal by the caller. A header-only BAM passes every other gate
    here -- non-empty, BGZF-framed, quickcheck-clean, indexable -- and then reads
    downstream as "no variants found" rather than "the input was empty". Those are
    very different clinical statements, and this pipeline has no legitimate use for
    an empty alignment: the input is a patient's sequencing data, so zero records
    means a truncated or wrong upload, never a real negative result.
    """
    argv = ["samtools", "idxstats", output_bam]
    try:
        result = subprocess.run(argv, capture_output=True, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        _discard_output(index_path)
        _discard_output(output_bam)
        message = f"could not count records in {output_bam} ({exc})"
        logger.error(f"Job {job_label}: {message}")
        raise HTTPException(status_code=500, detail=message)

    if result.returncode != 0:
        stderr = (result.stderr or b"").decode("utf-8", errors="replace").strip()
        _discard_output(index_path)
        _discard_output(output_bam)
        message = f"samtools idxstats failed with exit code {result.returncode}: {stderr}"
        logger.error(f"Job {job_label}: {message}")
        raise HTTPException(status_code=500, detail=message)

    total = 0
    for line in (result.stdout or b"").decode("utf-8", errors="replace").splitlines():
        fields = line.split("\t")
        if len(fields) < 4:
            continue
        try:
            # mapped + unmapped; the trailing '*' row carries unplaced reads.
            total += int(fields[2]) + int(fields[3])
        except ValueError:
            continue
    return total


def convert_to_indexed_bam(job_label, input_path, output_bam, work_dir, reference_path=None):
    """Convert SAM/CRAM to a coordinate-sorted, indexed BAM.

    Blocking -- call it through asyncio.to_thread.

    Sorting is conditional, because it is the expensive step. `samtools view -b`
    preserves record order, so an input whose header already says
    `@HD ... SO:coordinate` (the normal case for CRAM) only needs the format
    conversion. Anything else -- explicitly unsorted, queryname-sorted, or a header
    that does not say -- gets `samtools sort`.

    Sorting spills to disk: worst case it writes a full second copy of the data as
    `<prefix>.NNNN.bam`. `-T` puts those inside `work_dir`, the caller's scratch
    directory, which is removed in a finally block -- never on the shared /data
    volume, where a leaked intermediate would be exactly the class of bug this
    module was just fixed for.

    Returns:
        tuple: (bam size in bytes, index path, whether a sort was performed)
    """
    sort_order = read_sort_order(job_label, input_path, reference_path)
    needs_sort = sort_order != "coordinate"

    if needs_sort:
        logger.info(
            f"Job {job_label}: header sort order is {sort_order!r}; sorting "
            f"(-m {SORT_MEMORY} -@ {SORT_THREADS})"
        )
        argv = [
            "samtools", "sort",
            "-O", "bam",
            "-m", SORT_MEMORY,
            "-@", SORT_THREADS,
            "-T", os.path.join(work_dir, "samtools_sort"),
            "-o", output_bam,
        ]
        if reference_path:
            # `samtools sort` spells the reference --reference; -T is its temp prefix,
            # unlike `samtools view` where -T is the reference. Easy to get backwards.
            argv += ["--reference", reference_path]
        argv.append(input_path)
    else:
        logger.info(f"Job {job_label}: already coordinate-sorted; converting without a sort")
        argv = ["samtools", "view", "-b"]
        if reference_path:
            argv += ["-T", reference_path]
        argv += ["-o", output_bam, input_path]

    size = run_samtools_conversion(job_label, argv, output_bam)
    index_path = index_output_bam(job_label, output_bam)

    records = count_records(job_label, output_bam, index_path)
    if records == 0:
        _discard_output(index_path)
        _discard_output(output_bam)
        message = (
            f"The conversion produced a valid but empty BAM (0 alignment records) "
            f"from {os.path.basename(input_path)}. That is a truncated or wrong "
            f"input, not a negative result -- shipping it would read downstream as "
            f"'no variants found'."
        )
        logger.error(f"Job {job_label}: {message}")
        raise HTTPException(status_code=422, detail=message)

    logger.info(f"Job {job_label}: {records} alignment records")
    return size, index_path, needs_sort, records


async def _fail_step(job_client, message):
    """Report a step failure, best effort. A dead job server must not mask the error."""
    if not job_client:
        return
    try:
        await job_client.fail_step(message, {"error": message})
    except Exception as exc:
        logger.warning(f"Could not report step failure to job server: {exc}")


# ---------------------------------------------------------------------------
# GRCh37 -> GRCh38 liftover (/liftover-vcf)
#
# Real coordinate conversion via Picard's LiftoverVcf (bundled in GATK), not a
# chromosome rename. The repo's previous liftover module fed the UCSC chain file to
# `bcftools annotate --rename-chrs`, which only rewrites contig *names* and leaves
# every position untouched -- a GRCh37 file came out labelled GRCh38 with every
# coordinate still GRCh37, which is the worst possible outcome for a clinical
# pipeline. That module was deleted (commit 7b665cc) and this endpoint is its
# from-scratch replacement: LiftoverVcf maps each record through the chain intervals,
# re-checks the REF allele against the target FASTA, and writes anything it cannot
# carry over into a separate reject VCF with the reason in the FILTER column.
# ---------------------------------------------------------------------------


def _open_vcf_text(path):
    """Open a VCF for line reading, through gzip when it is (b)gzipped.

    Sniffs the magic bytes rather than trusting the extension: the paths handed to
    the helpers below are intermediate files this module itself named, but the very
    first one is the upload, and a `.vcf` upload that is secretly bgzipped (or the
    reverse) should be read correctly rather than parsed as binary noise. BGZF is
    valid gzip framing, so Python's gzip module reads bgzipped VCFs fine.
    """
    with open(path, 'rb') as probe:
        magic = probe.read(2)
    opener = gzip.open if magic == b'\x1f\x8b' else open
    return opener(path, 'rt', errors='ignore')


def vcf_contig_naming(job_label, path, sample_lines=200):
    """Return 'chr' or 'plain' for the contig naming a VCF actually uses.

    THE WHOLE LIFTOVER HINGES ON THIS. Picard LiftoverVcf matches each record's
    contig name against the chain's *source* contig names, and UCSC's
    hg19ToHg38.over.chain uses `chr1`-style names throughout. A b37/GRCh37 VCF
    very commonly names the same chromosome `1`. On a mismatch LiftoverVcf does
    not error -- it simply finds no chain for any record and rejects every single
    variant (`NoTarget`), which without the reject-rate guard below would come
    back as a "successful" near-empty VCF. So the naming is detected here and
    unprefixed input is renamed to `chr` form BEFORE LiftoverVcf ever sees it.

    Evidence order:
      1. `##contig=<ID=...>` header records -- the file's own declaration.
      2. The CHROM column of the first `sample_lines` data lines, for headers
         that carry no contig records (legal, and common in hand-made VCFs).

    Mixed naming in one file is refused rather than guessed about: half the
    records would lift and half would not, and no single rename map fixes a file
    that disagrees with itself.
    """
    header_names = []
    body_names = []
    try:
        with _open_vcf_text(path) as handle:
            body_seen = 0
            for line in handle:
                if line.startswith('##'):
                    if line.startswith('##contig=<'):
                        for field in line.strip()[len('##contig=<'):].rstrip('>').split(','):
                            key, sep, value = field.partition('=')
                            if sep and key.strip() == 'ID' and value.strip():
                                header_names.append(value.strip())
                    continue
                if line.startswith('#'):
                    continue
                name = line.split('\t', 1)[0].strip()
                if name:
                    body_names.append(name)
                    body_seen += 1
                    if body_seen >= sample_lines:
                        break
    except OSError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Could not read the VCF to determine its contig naming: {exc}",
        )

    names = header_names or body_names
    if not names:
        raise HTTPException(
            status_code=400,
            detail=(
                "The VCF carries no ##contig records and no data lines, so its "
                "contig naming cannot be determined and it cannot be lifted over."
            ),
        )

    prefixed = [n for n in names if n.lower().startswith('chr')]
    unprefixed = [n for n in names if not n.lower().startswith('chr')]
    if prefixed and unprefixed:
        raise HTTPException(
            status_code=400,
            detail=(
                "The VCF mixes chr-prefixed and unprefixed contig names "
                f"(e.g. {prefixed[0]!r} and {unprefixed[0]!r}). No single rename "
                "can make such a file match the liftover chain; normalise its "
                "contig naming and upload it again."
            ),
        )

    naming = 'chr' if prefixed else 'plain'
    logger.info(f"Job {job_label}: input VCF uses {naming!r} contig naming")
    return naming


# GRCh37/b37 -> UCSC hg19 contig spellings, for the pre-liftover rename. Only the
# primary assembly is mapped: the chain has no entries for GL* unplaced scaffolds
# anyway, so those keep their names and fall out of LiftoverVcf as individually
# rejected records (--WARN_ON_MISSING_CONTIG keeps that from being fatal). Both
# `MT` (b37) and bare `M` map to `chrM`, the UCSC spelling.
#
# MT/M stay in this table -- do not delete them -- but on real b37 input they
# never reach LiftoverVcf through it. Read on before "tidying" that.
#
# For every other entry here the rename is purely cosmetic: b37's `1` and
# hg19's `chr1` name the SAME sequence at the SAME coordinates, so
# rename-then-lift is correct. MT is the one contig where the spelling
# difference is also a sequence difference:
#
#   build          contig   sequence            length
#   GRCh38         chrM     NC_012920 (rCRS)     16569
#   GRCh37 / b37   MT       NC_012920 (rCRS)     16569   <- already the target
#   hg19           chrM     NC_001807 (Yoruba)   16571   <- genuinely different
#
# b37's MT is already rCRS -- already in GRCh38's coordinate system. The chain
# staged for this lift (UCSC's hg19ToHg38.over.chain.gz) is sourced from
# hg19's chrM, not b37's MT -- its own header says so:
#   chain 1560271 chrM 16571 + 0 16571 chrM 16569 + 0 16569
# so renaming a b37 MT record to chrM and pushing it through that chain
# applies hg19->rCRS's constant -2bp shift to a record that is already in rCRS
# coordinates. Inside MT-RNR1 that turns a real variant like m.1555A>G into
# 1553 -- silently wrong, on a build that never needed lifting to begin with.
#
# This was introduced in 93b2878 ("feat(liftover): real GRCh37/hg19 ->
# GRCh38 VCF liftover"), which added this rename map to fix a real failure:
# the UCSC chain is chr-prefixed and b37 VCFs are not, so without the rename
# LiftoverVcf rejected every record. MT was added by pattern-completion with
# its 26 neighbours. That commit's A/B check was sound and passed -- "7
# textbook PGx SNPs lift to their exact known GRCh38 positions, 0 rejected"
# -- but all seven are autosomal, so MT fell outside its scope, and nothing
# downstream reads chrM to catch the drift later. Found while designing the
# mtDNA integration (2026-08-30).
#
# So: on unprefixed (b37) input, run_liftover_pipeline calls
# split_mt_records() to hold MT/M out of the file handed to LiftoverVcf
# entirely, and glues them back into the lifted output afterwards renamed to
# chrM with coordinates UNTOUCHED -- a pure rename, never a pass through this
# chain. hg19's chrM is NOT touched by this: it is a genuinely different
# sequence from GRCh38's chrM and does need converting, and its MT-RNR1
# window lifts exactly (one ungapped 2796bp block), so the existing
# rename-then-lift path stays correct for a chr-prefixed (hg19) upload.
MT_IS_ALREADY_RCRS = frozenset({'MT', 'M'})

PLAIN_TO_CHR_CONTIGS = {
    **{str(i): f"chr{i}" for i in range(1, 23)},
    'X': 'chrX',
    'Y': 'chrY',
    'MT': 'chrM',
    'M': 'chrM',
}


def write_chr_rename_map(work_dir):
    """Write the bcftools --rename-chrs map (old-name TAB new-name per line)."""
    map_path = os.path.join(work_dir, 'plain_to_chr.txt')
    with open(map_path, 'w', encoding='utf-8') as handle:
        for old, new in PLAIN_TO_CHR_CONTIGS.items():
            handle.write(f"{old}\t{new}\n")
    return map_path


def rename_vcf_contigs_to_chr(job_label, input_path, output_path, work_dir):
    """Rename `1`-style contigs to `chr1` so the input matches the UCSC chain.

    `bcftools annotate --rename-chrs` here is doing the one thing it is actually
    good for -- renaming -- as a *preparation* for the real coordinate conversion,
    not instead of it. It rewrites both the ##contig header records and the CHROM
    column, streaming, no index required. Names absent from the map pass through
    unchanged. List argv, never shell=True: input_path derives from an uploaded
    filename.
    """
    map_path = write_chr_rename_map(work_dir)
    argv = [
        'bcftools', 'annotate',
        '--rename-chrs', map_path,
        '-O', 'z',
        '-o', output_path,
        input_path,
    ]
    logger.info(f"Job {job_label}: running {' '.join(argv)}")
    try:
        result = subprocess.run(argv, capture_output=True, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"bcftools could not be executed - check container configuration ({exc})",
        )
    if result.returncode != 0:
        stderr = (result.stderr or b'').decode('utf-8', errors='replace').strip()
        raise HTTPException(
            status_code=500,
            detail=f"Contig renaming before liftover failed (bcftools annotate): {stderr}",
        )
    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise HTTPException(
            status_code=500,
            detail="bcftools annotate exited 0 but wrote no renamed VCF",
        )
    return output_path


def build_liftover_argv(input_path, lifted_path, chain_path, reject_path, reference_path):
    """Build the Picard LiftoverVcf command as an argv list.

    A list, never a shell string: input_path derives from an uploaded filename.
    Flag notes (GATK's Barclay wrapper around the Picard tool):
      -I / -O          input and output VCF; a `.vcf.gz` output is written as BGZF
                       by htsjdk, i.e. it comes out already bgzipped.
      -C               the chain file (--CHAIN); may be gzipped.
      --REJECT         where every unliftable record goes, reason in FILTER.
      -R               the *target* reference FASTA (--REFERENCE_SEQUENCE); its
                       .dict supplies the output header's contig records and its
                       sequence is what each lifted REF allele is checked against.
      --WARN_ON_MISSING_CONTIG true
                       records on contigs the chain does not know (b37 GL*
                       scaffolds, say) become individual rejects instead of
                       killing the whole run.
      --RECOVER_SWAPPED_REF_ALT true
                       the handful of sites where GRCh38 adopted the minor allele
                       as reference get their REF/ALT (and genotypes) swapped
                       instead of rejected as MismatchedRefAllele.
    """
    return [
        'gatk', 'LiftoverVcf',
        '-I', input_path,
        '-O', lifted_path,
        '-C', chain_path,
        '--REJECT', reject_path,
        '-R', reference_path,
        '--WARN_ON_MISSING_CONTIG', 'true',
        '--RECOVER_SWAPPED_REF_ALT', 'true',
    ]


def count_vcf_records(path):
    """Count data lines in a (possibly bgzipped) VCF. 0 for a missing file."""
    if not os.path.exists(path):
        return 0
    count = 0
    with _open_vcf_text(path) as handle:
        for line in handle:
            if line.strip() and not line.startswith('#'):
                count += 1
    return count


def summarise_reject_reasons(path, top_n=5):
    """Tally the FILTER column of LiftoverVcf's reject VCF, most common first.

    LiftoverVcf writes *why* each record failed as the FILTER value (NoTarget,
    MismatchedRefAllele, IndelStraddlesMultipleIntervals, CannotLiftOver, ...).
    Surfacing the tally costs one pass and turns "812 variants were dropped" into
    something an operator can actually act on -- a wall of NoTarget means the
    chain never matched (naming/build), a spread of MismatchedRefAllele means the
    file was probably not GRCh37 to begin with.
    """
    if not os.path.exists(path):
        return {}
    reasons = {}
    with _open_vcf_text(path) as handle:
        for line in handle:
            if not line.strip() or line.startswith('#'):
                continue
            fields = line.split('\t')
            reason = fields[6].strip() if len(fields) > 6 else '.'
            reasons[reason] = reasons.get(reason, 0) + 1
    ranked = sorted(reasons.items(), key=lambda item: (-item[1], item[0]))
    return dict(ranked[:top_n])


def split_mt_records(job_label, input_path, work_dir):
    """Pull MT/M records out of a plain (b37) VCF before LiftoverVcf ever sees them.

    See MT_IS_ALREADY_RCRS's docstring above PLAIN_TO_CHR_CONTIGS for why: b37's MT
    is already rCRS -- already in the target's coordinate system -- so the correct
    "lift" for it is a pure rename, never a pass through a chain sourced from
    hg19's chrM.

    Called only from run_liftover_pipeline's unprefixed ('plain') branch. Returns
    `(rest_path, mt_path)`:
      rest_path  everything BUT MT/M. `input_path` itself, unchanged, when the
                 file carries no MT/M records at all -- the common case for a
                 targeted panel -- so nothing is filtered that was never there.
                 None when the file is MT/M-only: there is then nothing left to
                 hand to LiftoverVcf at all, which the caller must check for and
                 skip the conversion entirely (see run_liftover_pipeline) --
                 handing it a header-only VCF would make LiftoverVcf produce
                 zero records and trip the "implausible near-empty result"
                 guard on an input that needed no lifting in the first place.
      mt_path    the held-back MT/M records renamed to `chrM`, coordinates
                 untouched, ready to be spliced back into the lifted output by
                 merge_mt_passthrough(). None when there was nothing to hold back.
    """
    mt_raw = os.path.join(work_dir, 'mt_raw.vcf.gz')
    argv = ['bcftools', 'view', '-t', 'MT,M', '-Oz', '-o', mt_raw, input_path]
    logger.info(f"Job {job_label}: running {' '.join(argv)}")
    try:
        result = subprocess.run(argv, capture_output=True, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"bcftools could not be executed - check container configuration ({exc})",
        )
    if result.returncode != 0:
        stderr = (result.stderr or b'').decode('utf-8', errors='replace').strip()
        raise HTTPException(
            status_code=500,
            detail=f"Extracting MT/M from the input before liftover failed (bcftools view): {stderr}",
        )

    if count_vcf_records(mt_raw) == 0:
        # No MT/M in this file -- nothing to hold back. A real bcftools always
        # writes a (header-only) output on success, so this is a genuine "zero
        # records" read, not a mistaken read of a file that was never written.
        _discard_output(mt_raw)
        return input_path, None

    # From here on MT/M records definitely exist, so mt_path is needed either
    # way -- whether or not anything else is left to lift. Rename MT/M -> chrM
    # now, once. This reuses the same rename map LiftoverVcf's other 26 entries
    # use -- correct here too, because for this file the map is doing nothing
    # but the rename its name promises: mt_raw contains only MT/M records, and
    # their coordinates are left exactly as they were. No chain is involved.
    mt_path = os.path.join(work_dir, 'mt_chrm.vcf.gz')
    rename_vcf_contigs_to_chr(job_label, mt_raw, mt_path, work_dir)

    rest_path = os.path.join(work_dir, 'non_mt.vcf.gz')
    argv = ['bcftools', 'view', '-t', '^MT,M', '-Oz', '-o', rest_path, input_path]
    logger.info(f"Job {job_label}: running {' '.join(argv)}")
    try:
        result = subprocess.run(argv, capture_output=True, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"bcftools could not be executed - check container configuration ({exc})",
        )
    if result.returncode != 0:
        stderr = (result.stderr or b'').decode('utf-8', errors='replace').strip()
        raise HTTPException(
            status_code=500,
            detail=f"Removing MT/M from the input before liftover failed (bcftools view): {stderr}",
        )
    if not os.path.exists(rest_path) or os.path.getsize(rest_path) == 0:
        raise HTTPException(
            status_code=500,
            detail="bcftools view exited 0 but wrote no non-MT VCF",
        )

    if count_vcf_records(rest_path) == 0:
        # MT/M-only input: a header-only VCF is a nonzero-size file (the header
        # alone compresses to something), so the size check above does not catch
        # this -- it has to be a record count. There is nothing left to hand to
        # LiftoverVcf; the caller skips it entirely rather than running it on an
        # input guaranteed to come back with zero records.
        _discard_output(rest_path)
        return None, mt_path

    return rest_path, mt_path


def merge_mt_passthrough(job_label, lifted_path, mt_path, work_dir):
    """Splice held-back MT/M records into a lifted VCF, and re-sort.

    `lifted_path` was produced by LiftoverVcf from a file with MT/M already
    removed, so it carries none. `mt_path` (from split_mt_records) carries only
    chrM, at its original b37 coordinates. `bcftools concat` unions the two
    record sets under `lifted_path`'s header -- the one with the real GRCh38
    sequence dictionary -- and `bcftools sort` puts chrM back in the position
    that dictionary says it belongs, rather than trailing the file wherever
    concat happened to append it.
    """
    merged = os.path.join(work_dir, 'lifted_with_mt.vcf.gz')
    argv = ['bcftools', 'concat', '-Oz', '-o', merged, lifted_path, mt_path]
    logger.info(f"Job {job_label}: running {' '.join(argv)}")
    try:
        result = subprocess.run(argv, capture_output=True, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"bcftools could not be executed - check container configuration ({exc})",
        )
    if result.returncode != 0:
        stderr = (result.stderr or b'').decode('utf-8', errors='replace').strip()
        raise HTTPException(
            status_code=500,
            detail=f"Merging MT back into the lifted output failed (bcftools concat): {stderr}",
        )
    if not os.path.exists(merged) or os.path.getsize(merged) == 0:
        raise HTTPException(
            status_code=500,
            detail="bcftools concat exited 0 but wrote no merged VCF",
        )

    sorted_path = os.path.join(work_dir, 'lifted_with_mt.sorted.vcf.gz')
    argv = ['bcftools', 'sort', '-T', work_dir, '-Oz', '-o', sorted_path, merged]
    logger.info(f"Job {job_label}: running {' '.join(argv)}")
    try:
        result = subprocess.run(argv, capture_output=True, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"bcftools could not be executed - check container configuration ({exc})",
        )
    if result.returncode != 0:
        stderr = (result.stderr or b'').decode('utf-8', errors='replace').strip()
        raise HTTPException(
            status_code=500,
            detail=f"Sorting the MT-merged output failed (bcftools sort): {stderr}",
        )
    if not os.path.exists(sorted_path) or os.path.getsize(sorted_path) == 0:
        raise HTTPException(
            status_code=500,
            detail="bcftools sort exited 0 but wrote no sorted VCF",
        )

    # shutil.move, not os.replace: `sorted_path` is under work_dir (TEMP_DIR) and
    # `lifted_path` is on the shared /data volume -- typically a different
    # filesystem, where a bare rename raises EXDEV. shutil.move falls back to a
    # copy when a same-filesystem rename is not possible.
    shutil.move(sorted_path, lifted_path)


def run_liftover_pipeline(job_label, input_path, work_dir, lifted_path, reject_path,
                          chain_path, reference_path):
    """Normalise contig naming, run LiftoverVcf, index, and vouch for the result.

    Synchronous by design -- the endpoint runs it via asyncio.to_thread under
    _to_thread_semaphore, exactly like the CRAM/SAM conversions, so a long lift
    cannot stall the event loop (and with it /health).

    Every failure raises HTTPException, so a caller that returns normally can
    promise `lifted_path` exists, is BGZF, and passed the reject-rate guard.

    Returns:
        dict with n_lifted, n_rejected, reject_reasons, renamed_contigs, tbi path.
    """
    # Step 1: make the input's contig naming match the chain's (see
    # vcf_contig_naming's docstring for why every variant is silently rejected
    # otherwise). A chr-prefixed file is passed to LiftoverVcf untouched.
    naming = vcf_contig_naming(job_label, input_path)
    renamed_contigs = naming == 'plain'
    mt_path = None
    mt_only_input = False
    if renamed_contigs:
        # b37's MT is already rCRS -- already GRCh38's coordinate system -- so it
        # must never go through this hg19-sourced chain. See MT_IS_ALREADY_RCRS's
        # docstring above PLAIN_TO_CHR_CONTIGS. hg19's chrM (the 'chr' branch,
        # below) is a genuinely different sequence and is NOT touched by this --
        # it keeps going through LiftoverVcf like every other contig.
        rename_input, mt_path = split_mt_records(job_label, input_path, work_dir)
        if rename_input is None:
            # MT/M-only input (split_mt_records only returns None here once it
            # has confirmed mt_path is not). There is nothing left for
            # LiftoverVcf to do -- see the comment at the honesty gate below for
            # why this does not, and must not, run LiftoverVcf on a header-only
            # file just to prove that.
            mt_only_input = True
        else:
            chr_input = os.path.join(work_dir, 'chr_named_input.vcf.gz')
            rename_vcf_contigs_to_chr(job_label, rename_input, chr_input, work_dir)
            liftover_input = chr_input
    else:
        liftover_input = input_path

    if mt_only_input:
        # Every record in the input was MT/M, already rCRS, already renamed to
        # chrM with coordinates untouched by split_mt_records -- there is
        # nothing to convert, so LiftoverVcf never runs. shutil.move, not
        # os.replace: mt_path is under work_dir (TEMP_DIR) and lifted_path is on
        # the shared /data volume, typically a different filesystem, where a
        # bare rename raises EXDEV (see merge_mt_passthrough's docstring).
        shutil.move(mt_path, lifted_path)
    else:
        # Step 2: the actual coordinate conversion.
        argv = build_liftover_argv(
            liftover_input, lifted_path, chain_path, reject_path, reference_path
        )
        logger.info(f"Job {job_label}: running {' '.join(argv)}")
        try:
            result = subprocess.run(argv, capture_output=True, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise HTTPException(
                status_code=500,
                detail=f"GATK could not be executed - check container configuration ({exc})",
            )
        if result.returncode != 0:
            stderr = (result.stderr or b'').decode('utf-8', errors='replace').strip()
            # The tail is where Picard puts its actual complaint; the head is banner.
            raise HTTPException(
                status_code=500,
                detail=f"LiftoverVcf failed with exit code {result.returncode}: {stderr[-2000:]}",
            )

        if not os.path.exists(lifted_path) or os.path.getsize(lifted_path) == 0:
            raise HTTPException(
                status_code=500,
                detail=f"LiftoverVcf exited 0 but wrote no output to {lifted_path}",
            )
        with open(lifted_path, 'rb') as handle:
            if handle.read(2) != b'\x1f\x8b':
                raise HTTPException(
                    status_code=500,
                    detail=f"LiftoverVcf produced {lifted_path}, which is not a BGZF-framed VCF",
                )

        # Step 2b: splice the held-back MT/M records (untouched coordinates,
        # renamed to chrM) back into the lifted output, before the honesty gate
        # below counts it. That ordering matters: LIFTOVER_MAX_REJECT_FRACTION is
        # a fraction of *input* records, and MT/M were part of the input, so they
        # must be back in `n_lifted` before that fraction is computed, or
        # removing them from LiftoverVcf's view would silently shrink the
        # denominator instead of just rerouting records that were never going to
        # be rejected anyway.
        if mt_path:
            merge_mt_passthrough(job_label, lifted_path, mt_path, work_dir)

    # Step 3: the honesty gate. LiftoverVcf "succeeding" while rejecting nearly
    # everything is exactly what a chain/prefix mismatch looks like, and the cost
    # of letting it through is a clinical report computed from a fraction of the
    # patient's variants. See LIFTOVER_MAX_REJECT_FRACTION.
    #
    # This does not, and must not, fire for `mt_only_input`: LiftoverVcf never
    # ran, `reject_path` was therefore never written (count_vcf_records treats a
    # missing file as 0 -- see its docstring), and `n_lifted` is the MT/M
    # passthrough's own record count, which is > 0 by construction (split_mt_
    # records only reports an MT-only input once it has confirmed there is at
    # least one MT/M record to report). This guard exists to catch a
    # chain/prefix mismatch producing a near-empty *lifted* result -- it is not
    # "most of the input is missing", it is "every record present needed no
    # lifting", and those are not the same failure.
    n_lifted = count_vcf_records(lifted_path)
    n_rejected = count_vcf_records(reject_path)
    reject_reasons = summarise_reject_reasons(reject_path)
    total = n_lifted + n_rejected
    if total == 0:
        raise HTTPException(
            status_code=500,
            detail=(
                "LiftoverVcf produced no records at all - the input VCF appears to "
                "contain no variants, so there is nothing to lift over."
            ),
        )
    reject_fraction = n_rejected / total
    if reject_fraction > LIFTOVER_MAX_REJECT_FRACTION:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Liftover rejected {n_rejected} of {total} variants "
                f"({reject_fraction:.0%}), which is implausible for a genuine "
                f"GRCh37 file (reject reasons: {reject_reasons}). This usually "
                "means the chain did not match the input - a wrong source build, "
                "or a contig-naming mismatch. Refusing to hand a near-empty VCF "
                "to the pipeline as if it were the patient's data."
            ),
        )

    # Step 4: tabix index. `bcftools index -t` writes the same .tbi `tabix -p vcf`
    # would (both are htslib); the standalone bgzip/tabix binaries are not in this
    # image, bcftools is. Best-effort on purpose: LiftoverVcf already sorted the
    # output against the target dictionary, and the pipeline re-indexes when no
    # .tbi travels with the VCF, so a failed index is a warning, not a lost run.
    tbi_path = f"{lifted_path}.tbi"
    try:
        index_result = subprocess.run(
            ['bcftools', 'index', '-t', '-f', lifted_path],
            capture_output=True,
            check=False,
        )
        if index_result.returncode != 0 or not os.path.exists(tbi_path):
            stderr = (index_result.stderr or b'').decode('utf-8', errors='replace').strip()
            logger.warning(f"Job {job_label}: could not tabix-index {lifted_path}: {stderr}")
            tbi_path = None
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(f"Job {job_label}: could not tabix-index {lifted_path}: {exc}")
        tbi_path = None

    logger.info(
        f"Job {job_label}: lifted {n_lifted} records, rejected {n_rejected} "
        f"({reject_reasons or 'no rejects'})"
    )
    return {
        'n_lifted': n_lifted,
        'n_rejected': n_rejected,
        'reject_reasons': reject_reasons,
        'renamed_contigs': renamed_contigs,
        'vcf_index': tbi_path,
    }


@app.post("/liftover-vcf")
async def liftover_vcf(
    file: UploadFile = File(...),
    source_build: str = Form(...),
    reference_genome: str = Form("hg38"),
    patient_id: Optional[str] = Form(None),
    report_id: Optional[str] = Form(None),
    job_id: Optional[str] = Form(None),
    step_name: Optional[str] = Form("liftover")
):
    """
    Lift a GRCh37/hg19 VCF over to GRCh38 coordinates with Picard LiftoverVcf.

    Returns the lifted, bgzipped, tabix-indexed VCF's path on the shared /data
    volume, plus the reject VCF and honest statistics: how many records made it,
    how many were dropped and why. Variants that cannot be lifted (no chain
    interval, REF mismatch after lifting, indel straddling chain gaps) are NOT in
    the output -- callers surfacing this to a user must say the drop count out
    loud rather than presenting the lifted file as the complete original.

    A rejection rate above LIFTOVER_MAX_REJECT_FRACTION fails the request: that
    pattern means the chain never matched the input (wrong build or contig
    naming), and a near-empty "success" would produce wrong clinical results.
    """
    job_client = None
    work_dir = None
    output_dir = None
    try:
        # Initialize job client if Zaro Job PK is provided
        if job_id:
            try:
                job_client = JobClient(job_id=job_id, step_name=step_name)
                await job_client.start_step(f"Starting GRCh37->GRCh38 liftover for {file.filename}")
                await job_client.log_progress(f"Lifting {file.filename} to GRCh38", {
                    "filename": file.filename,
                    "source_build": source_build,
                    "reference_genome": reference_genome
                })
            except Exception as e:
                logger.warning(f"Failed to initialize workflow client: {e}")
                job_client = None

        # source_build is caller-supplied: allowlist it before it selects anything.
        canonical_source = LIFTOVER_SOURCE_BUILDS.get((source_build or "").strip().lower())
        if not canonical_source:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unsupported source_build {source_build!r} for liftover; "
                    f"supported: {sorted(LIFTOVER_SOURCE_BUILDS)}"
                ),
            )

        # The target must be GRCh38: it is the only build a chain exists for, and
        # the only build the rest of the pipeline accepts. Checked as a *build*
        # (hg38 and grch38 both pass) the same way the CRAM route reasons about
        # REFERENCE_BUILDS.
        target_key = (reference_genome or "hg38").strip().lower()
        if REFERENCE_BUILDS.get(target_key) != 'GRCh38':
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Liftover targets GRCh38 only; got reference_genome={reference_genome!r}. "
                    "Post hg38 (or grch38), or omit the field."
                ),
            )
        reference_path = REFERENCE_PATHS[target_key]
        if not os.path.exists(reference_path):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Liftover requires the GRCh38 reference FASTA, which is not present "
                    f"at {reference_path}. Fetch the reference before lifting."
                ),
            )

        chain_path = LIFTOVER_CHAIN_PATHS[(canonical_source, 'GRCh38')]
        if not os.path.exists(chain_path):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"The {canonical_source}->GRCh38 chain file is not present at "
                    f"{chain_path}. Stage UCSC's hg19ToHg38.over.chain.gz there "
                    "(genome-downloader fetches it on a fresh deploy)."
                ),
            )

        # Sanitised for the same reason as every other route here: file.filename is
        # attacker-controlled and this name reaches subprocess argv and the shared
        # volume. See safe_upload_name's docstring.
        local_job_id = str(uuid.uuid4())
        work_dir = tempfile.mkdtemp(dir=TEMP_DIR)
        filename = safe_upload_name(file.filename or "input.vcf", local_job_id)
        input_path = os.path.join(work_dir, filename)
        # Outputs go on the shared volume, not this container's /tmp -- the caller
        # is a Nextflow process in another container and the ./data bind mount is
        # the only filesystem both can see. Same contract as conversion_output_dir's
        # docstring spells out for the BAM conversions.
        output_dir = conversion_output_dir(local_job_id, job_id, patient_id)
        # stored_stem(), not splitext: a `.vcf.gz` upload would otherwise become
        # `<name>.vcf.grch38.vcf.gz`.
        lifted_path = os.path.join(output_dir, f"{stored_stem(filename)}.grch38.vcf.gz")
        reject_path = os.path.join(output_dir, f"{stored_stem(filename)}.reject.vcf.gz")

        # Chunked for the same reason as the CRAM route: never buffer the upload.
        with open(input_path, "wb") as f:
            while chunk := await file.read(UPLOAD_CHUNK_BYTES):
                f.write(chunk)

        logger.info(f"Job {local_job_id}: Saved VCF for liftover to {input_path}")

        if job_client:
            file_size = os.path.getsize(input_path)
            await job_client.log_progress(f"File uploaded: {file_size} bytes", {
                "file_size_bytes": file_size,
                "input_path": input_path
            })

        # to_thread under the semaphore, exactly like the CRAM/SAM conversions:
        # LiftoverVcf on a WGS VCF runs for minutes, and a blocking call here would
        # take /health (and the container healthcheck) down with it.
        async with _to_thread_semaphore:
            stats = await asyncio.to_thread(
                run_liftover_pipeline,
                local_job_id,
                input_path,
                work_dir,
                lifted_path,
                reject_path,
                chain_path,
                reference_path,
            )

        if job_client:
            await job_client.log_progress("Liftover completed", {
                "vcf_path": lifted_path,
                "reject_path": reject_path,
                "n_lifted": stats["n_lifted"],
                "n_rejected": stats["n_rejected"],
                "reject_reasons": stats["reject_reasons"],
            })
            # output_data, not just the message: the message is broadcast live and
            # filed in job_logs, but only output_data lands on the JobStep row, and
            # the report needs the counts after the run to state on which build --
            # and at what cost -- these results were produced.
            await job_client.complete_step(
                f"Lifted {stats['n_lifted']} variants to GRCh38 "
                f"({stats['n_rejected']} could not be lifted and were dropped)",
                output_data={
                    "n_lifted": stats["n_lifted"],
                    "n_rejected": stats["n_rejected"],
                    "reject_reasons": stats["reject_reasons"],
                    "source_build": canonical_source,
                    "target_build": "GRCh38",
                },
            )

        return {
            "success": True,
            "job_id": local_job_id,
            "vcf_path": lifted_path,
            "vcf": lifted_path,  # Alternative field name, matching the BAM routes
            "vcf_index": stats["vcf_index"],
            "reject_path": reject_path,
            "n_lifted": stats["n_lifted"],
            "n_rejected": stats["n_rejected"],
            "reject_reasons": stats["reject_reasons"],
            "renamed_contigs": stats["renamed_contigs"],
            "source_build": canonical_source,
            "target_build": "GRCh38",
            "message": (
                f"Lifted {filename} to GRCh38: {stats['n_lifted']} variants kept, "
                f"{stats['n_rejected']} dropped as unliftable"
            ),
        }

    except HTTPException as e:
        # Do not relabel a deliberate 4xx as a 500 on the way out.
        _cleanup_dir(output_dir)
        logger.error(f"Liftover failed: {e.detail}")
        await _fail_step(job_client, f"Liftover failed: {e.detail}")
        raise
    except Exception as e:
        _cleanup_dir(output_dir)
        logger.exception(f"Error in liftover: {e}")
        await _fail_step(job_client, f"Liftover failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Liftover failed: {str(e)}")
    finally:
        # The input copy (and any renamed intermediate) is never needed again.
        _cleanup_dir(work_dir)


@app.post("/align-fastq")
async def align_fastq(
    file: UploadFile = File(...),
    reference_genome: str = Form("hg38"),
    patient_id: Optional[str] = Form(None),
    report_id: Optional[str] = Form(None),
    job_id: Optional[str] = Form(None),
    step_name: Optional[str] = Form("gatk_alignment")
):
    """
    Not implemented. Returns HTTP 501 whenever it is reached.

    (Reached, not "always": the form parameters below are still declared, so Starlette
    parses the multipart body before this function runs. A malformed body is a 422 and
    a body that fills the disk is a 500, neither of which this code sees. The
    parameters are kept so the OpenAPI schema still documents what the route takes.)

    Aligning FASTQ needs an aligner (bwa-mem2 for short reads, minimap2 for long),
    a prebuilt reference index and a large-RAM profile, none of which this image
    carries. Until that lands this endpoint refuses instead of inventing an
    alignment: it used to write a 21-byte ASCII placeholder to a `.bam` and answer
    `success: true` (BACKLOG 0 / 51 / 112 / 113). Implementing alignment for real is
    tracked as BACKLOG 3 / 112.

    No ZaroPGx upload reaches this route. app/api/utils/file_processor.py sets
    workflow["unsupported"] = True for FileType.FASTQ, and app/api/routes/upload_router.py
    now acts on it: `_unanalysable_upload_reason` turns that verdict into a 400 before a
    patient or job row exists, and that gate sits ahead of the only place the app submits
    to Nextflow. So the app can no longer mint a run with `--input_type fastq`.

    The 501 is kept as defence in depth, not as a formality behind a closed door.
    gatk-api is its own service on the compose network, so anything that can reach
    http://gatk-api:5000 can POST here: pipelines/pgx/main.nf still has a fastq branch
    whose FastqToBAM process calls this route, so a hand-run `nextflow run
    --input_type fastq` arrives here directly, and so would a regression in the upload
    gate. Refusing is still right -- the alternative is a fabricated BAM.

    Because a caller that gets here is mid-run, a JobClient step is opened and failed
    with the reason, so the operator sees why the job stopped instead of a bare
    "exit status (1)" from Nextflow.
    """
    detail = (
        "FASTQ alignment is not implemented: this service ships no aligner. "
        "ZaroPGx refuses FASTQ at upload with a 400, so reaching this route means "
        "the pipeline was invoked outside the app. Align the reads outside ZaroPGx "
        "and upload the resulting BAM, CRAM or VCF."
    )
    logger.warning(
        f"Refused /align-fastq for {file.filename}: FASTQ alignment is not implemented"
    )

    # Best effort: a dead job server must not turn the 501 into something else.
    if job_id:
        try:
            job_client = JobClient(job_id=job_id, step_name=step_name)
            await job_client.start_step(f"FASTQ alignment requested for {file.filename}")
            await _fail_step(job_client, detail)
        except Exception as exc:
            logger.warning(f"Could not record the FASTQ refusal on job {job_id}: {exc}")

    raise HTTPException(status_code=501, detail=detail)


@app.post("/cram-to-bam")
async def cram_to_bam(
    file: UploadFile = File(...),
    reference_genome: str = Form("hg38"),
    patient_id: Optional[str] = Form(None),
    report_id: Optional[str] = Form(None),
    job_id: Optional[str] = Form(None),
    step_name: Optional[str] = Form("gatk_cram_to_bam")
):
    """
    Convert CRAM to a coordinate-sorted, indexed BAM.

    `samtools view -b -T <reference>` when the CRAM header already says
    `SO:coordinate` (the normal case), `samtools sort` when it does not. The result
    is always indexed, because every consumer of this endpoint's output -- PyPGx,
    GATK, OptiType-from-BAM -- needs a sorted indexed BAM, and `samtools index`
    itself only works on coordinate-sorted input.

    CRAM stores differences against a reference, so the reference FASTA is not
    optional: without the exact one the records cannot be decoded. A missing
    reference is therefore a hard 400 here rather than a file we cannot vouch for.
    """
    job_client = None
    work_dir = None
    output_dir = None
    try:
        # Initialize job client if Zaro Job PK is provided
        if job_id:
            try:
                job_client = JobClient(job_id=job_id, step_name=step_name)
                await job_client.start_step(f"Starting CRAM to BAM conversion for {file.filename}")
                await job_client.log_progress(f"Converting {file.filename} to BAM", {
                    "filename": file.filename,
                    "reference_genome": reference_genome
                })
            except Exception as e:
                logger.warning(f"Failed to initialize workflow client: {e}")
                job_client = None

        # Get reference path
        reference_path = REFERENCE_PATHS.get(reference_genome)
        if not reference_path:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unsupported reference genome {reference_genome!r}; "
                    f"known references: {sorted(REFERENCE_PATHS)}"
                ),
            )
        if not os.path.exists(reference_path):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"CRAM decoding requires the {reference_genome} reference FASTA, "
                    f"which is not present at {reference_path}. Fetch the reference "
                    "before converting; a CRAM cannot be read without it."
                ),
            )

        # Save uploaded file. basename() alone kept a crafted name inside the work dir
        # but left every shell and glob metacharacter in it, which safe_upload_name()'s
        # own docstring calls out as insufficient -- and output_bam below is derived
        # from this name, so it carried them onto the *shared* volume as well.
        local_job_id = str(uuid.uuid4())
        work_dir = tempfile.mkdtemp(dir=TEMP_DIR)
        filename = safe_upload_name(file.filename or "input.cram", local_job_id)
        input_path = os.path.join(work_dir, filename)
        # Output goes on the shared volume, not beside the input -- see
        # conversion_output_dir().
        output_dir = conversion_output_dir(local_job_id, job_id, patient_id)
        # stored_stem(), not os.path.splitext()[0]: safe_upload_name preserves
        # whichever allowlisted suffix arrived, including the two-part ones, so a
        # misdirected `.vcf.gz` post here would otherwise yield `<name>.vcf.bam`.
        output_bam = os.path.join(output_dir, f"{stored_stem(filename)}.bam")

        # Copied in chunks: a whole-genome CRAM does not fit in this container's heap,
        # and now that the conversion is real, whole-genome CRAMs actually arrive here.
        with open(input_path, "wb") as f:
            while chunk := await file.read(UPLOAD_CHUNK_BYTES):
                f.write(chunk)

        logger.info(f"Job {local_job_id}: Saved CRAM file to {input_path}")

        # The caller picked the reference; check the file agrees before decoding it.
        await asyncio.to_thread(
            verify_reference_matches, local_job_id, input_path, reference_genome
        )

        # Update workflow with file information
        if job_client:
            file_size = os.path.getsize(input_path)
            await job_client.log_progress(f"File uploaded: {file_size} bytes", {
                "file_size_bytes": file_size,
                "input_path": input_path
            })

        # to_thread, not a bare call: converting a whole-genome CRAM takes minutes to
        # hours, and a blocking subprocess here would stall the event loop -- taking
        # /health, and therefore the container's healthcheck, down with it. Gated by
        # _to_thread_semaphore: without it, the executor could hand out enough
        # concurrent sorts to exceed this container's memory limit -- see
        # TO_THREAD_CONCURRENCY_LIMIT above.
        async with _to_thread_semaphore:
            bam_size, index_path, sorted_here, records = await asyncio.to_thread(
                convert_to_indexed_bam,
                local_job_id,
                input_path,
                output_bam,
                work_dir,
                reference_path,
            )

        # Update workflow with completion
        if job_client:
            await job_client.log_progress("CRAM to BAM conversion completed", {
                "output_bam": output_bam,
                "bam_size_bytes": bam_size,
                "bam_index": index_path,
                "sorted": sorted_here,
                "records": records,
                "reference_genome": reference_genome
            })
            await job_client.complete_step("CRAM to BAM conversion completed successfully")

        return {
            "success": True,
            "job_id": local_job_id,
            "bam_path": output_bam,
            "bam": output_bam,  # Alternative field name
            "bam_index": index_path,
            "bam_size_bytes": bam_size,
            "sorted": sorted_here,
            "records": records,
            "message": f"Converted {filename} to a coordinate-sorted, indexed BAM"
        }

    except HTTPException as e:
        # Do not relabel a deliberate 4xx as a 500 on the way out.
        _cleanup_dir(output_dir)
        logger.error(f"CRAM to BAM conversion failed: {e.detail}")
        await _fail_step(job_client, f"CRAM to BAM conversion failed: {e.detail}")
        raise
    except Exception as e:
        _cleanup_dir(output_dir)
        logger.exception(f"Error in CRAM to BAM conversion: {e}")
        await _fail_step(job_client, f"CRAM to BAM conversion failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"CRAM to BAM conversion failed: {str(e)}")
    finally:
        # The input copy is never needed again, and it is now full size. Nothing has
        # ever deleted these, so a WGS run used to leave the input plus its output on
        # this container's writable layer indefinitely.
        _cleanup_dir(work_dir)


@app.post("/sam-to-bam")
async def sam_to_bam(
    file: UploadFile = File(...),
    reference_genome: str = Form("hg38"),
    patient_id: Optional[str] = Form(None),
    report_id: Optional[str] = Form(None),
    job_id: Optional[str] = Form(None),
    step_name: Optional[str] = Form("gatk_sam_to_bam")
):
    """
    Convert SAM to a coordinate-sorted, indexed BAM.

    `samtools view -b` when the SAM header already says `SO:coordinate`, otherwise
    `samtools sort` -- which matters more here than for CRAM, since a SAM is quite
    often queryname-sorted or unsorted. The result is always indexed.

    No reference FASTA is involved: SAM carries its own @SQ header, so unlike CRAM
    this conversion is self-contained. `reference_genome` is accepted only because
    callers post the same form to every conversion route. A SAM with no header is
    rejected by samtools and surfaces here as a 500, not as an empty BAM.
    """
    job_client = None
    work_dir = None
    output_dir = None
    try:
        # Initialize job client if Zaro Job PK is provided
        if job_id:
            try:
                job_client = JobClient(job_id=job_id, step_name=step_name)
                await job_client.start_step(f"Starting SAM to BAM conversion for {file.filename}")
                await job_client.log_progress(f"Converting {file.filename} to BAM", {
                    "filename": file.filename,
                    "reference_genome": reference_genome
                })
            except Exception as e:
                logger.warning(f"Failed to initialize workflow client: {e}")
                job_client = None

        # Sanitised for the same reason as the CRAM route: basename() alone leaves
        # every shell and glob metacharacter in a name that reaches the shared volume.
        local_job_id = str(uuid.uuid4())
        work_dir = tempfile.mkdtemp(dir=TEMP_DIR)
        filename = safe_upload_name(file.filename or "input.sam", local_job_id)
        input_path = os.path.join(work_dir, filename)
        # Shared volume, for the same reason as the CRAM route.
        output_dir = conversion_output_dir(local_job_id, job_id, patient_id)
        # stored_stem(), for the same reason as the CRAM route.
        output_bam = os.path.join(output_dir, f"{stored_stem(filename)}.bam")

        # Chunked for the same reason as the CRAM route: never buffer the whole upload.
        with open(input_path, "wb") as f:
            while chunk := await file.read(UPLOAD_CHUNK_BYTES):
                f.write(chunk)

        logger.info(f"Job {local_job_id}: Saved SAM file to {input_path}")

        # Update workflow with file information
        if job_client:
            file_size = os.path.getsize(input_path)
            await job_client.log_progress(f"File uploaded: {file_size} bytes", {
                "file_size_bytes": file_size,
                "input_path": input_path
            })

        # Offloaded for the same reason as the CRAM route: do not block the loop.
        # No reference is passed: SAM carries its own @SQ header. Gated by
        # _to_thread_semaphore for the same reason as the CRAM route.
        async with _to_thread_semaphore:
            bam_size, index_path, sorted_here, records = await asyncio.to_thread(
                convert_to_indexed_bam,
                local_job_id,
                input_path,
                output_bam,
                work_dir,
                None,
            )

        # Update workflow with completion
        if job_client:
            await job_client.log_progress("SAM to BAM conversion completed", {
                "output_bam": output_bam,
                "bam_size_bytes": bam_size,
                "bam_index": index_path,
                "sorted": sorted_here,
                "records": records
            })
            await job_client.complete_step("SAM to BAM conversion completed successfully")

        return {
            "success": True,
            "job_id": local_job_id,
            "bam_path": output_bam,
            "bam": output_bam,  # Alternative field name
            "bam_index": index_path,
            "bam_size_bytes": bam_size,
            "sorted": sorted_here,
            "records": records,
            "message": f"Converted {filename} to a coordinate-sorted, indexed BAM"
        }

    except HTTPException as e:
        # Do not relabel a deliberate 4xx as a 500 on the way out.
        _cleanup_dir(output_dir)
        logger.error(f"SAM to BAM conversion failed: {e.detail}")
        await _fail_step(job_client, f"SAM to BAM conversion failed: {e.detail}")
        raise
    except Exception as e:
        _cleanup_dir(output_dir)
        logger.exception(f"Error in SAM to BAM conversion: {e}")
        await _fail_step(job_client, f"SAM to BAM conversion failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"SAM to BAM conversion failed: {str(e)}")
    finally:
        _cleanup_dir(work_dir)


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
        
        # Method 1: Check our stored process registry
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
            
            # Remove from registry
            del running_processes[job_id]
        
        # Use existing jobs dictionary to find and cancel jobs. Matched on
        # zaro_job_id alone -- this used to also match any job whose input_file
        # or output_file *contained* patient_id as a substring, which is a
        # patient-wide selector, not a job-scoped one: two concurrent uploads
        # for the same patient share one patient_id, so cancelling one could
        # sweep in and rmtree the other's still-running BAM. It also compared
        # against a "workflow_id" key that variant_call() never sets, so that
        # half of the check was always False and the patient-substring half was
        # the only thing that ever matched. zaro_job_id is recorded on every
        # entry variant_call() creates, so an exact match is now available.
        jobs_cancelled = 0
        for gatk_job_id, job in jobs.items():
            if job.get("zaro_job_id") == job_id:

                # Mark job as cancelled
                job["status"] = "cancelled"
                job["message"] = "Job cancelled by user"
                jobs_cancelled += 1
                logger.info(f"Cancelled job {gatk_job_id}")
                
                # Clean up job-specific files
                cleanup_paths = []
                if job.get("input_file"):
                    cleanup_paths.append(job["input_file"])
                if job.get("output_file"):
                    cleanup_paths.append(job["output_file"])
                
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
        
        if jobs_cancelled == 0:
            logger.warning(f"No jobs found for job {job_id} and patient {patient_id}")
        
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
    # Make sure GATK is installed and verify reference genomes
    try:
        result = subprocess.run(["gatk", "--version"], capture_output=True, text=True)
        logger.info(f"GATK version: {result.stdout.strip()}")

        # Ensure reference dictionaries exist
        ensure_reference_dictionaries()

        # Check if samtools is installed
        try:
            subprocess.run(["samtools", "--version"], capture_output=True, check=True)
            logger.info("Verified samtools is installed")
        except Exception as e:
            logger.error(f"Samtools check failed: {str(e)}")
    except Exception as e:
        logger.error(f"GATK not found or not executable: {str(e)}")

    # Start the FastAPI server
    uvicorn.run(app, host='0.0.0.0', port=5000, log_level="info") 