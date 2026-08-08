#!/usr/bin/env python3
"""
PyPGx Wrapper Service for ZaroPGx
Provides REST API endpoints for calling PyPGx supported star alleles
"""

import os
import json
import logging
import re
from logging.handlers import RotatingFileHandler
import tempfile
import shlex
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Dict, Any, List, Optional
import zipfile
import io
import csv
import time
import asyncio
import psutil
from concurrent.futures import ThreadPoolExecutor, as_completed

import uvicorn
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Import shared workflow client for integration
import sys
sys.path.append('/job-client')
from job_client import JobClient, create_job_client  # pyright: ignore[reportMissingImports]

# --------------------------------------------------------------------------
# Upload name hardening
# --------------------------------------------------------------------------
# This service has no authentication and is reachable from every container on
# the compose network, so `file.filename` off a multipart request is entirely
# attacker-controlled. It used to be joined straight onto a job directory and
# then interpolated into shell=True command lines, which made a filename like
# `x;touch pwned.bam` a command-injection vector.
#
# The uploaded name is therefore never used on disk. It is replaced by a UUID
# plus one suffix drawn from the list below, which cannot carry shell syntax, a
# path separator, or a `..` traversal segment. werkzeug's secure_filename - the
# sanitiser the app's own /api/variant-call route uses - is deliberately NOT
# used here: werkzeug is not installed in this image (see
# docker/pypgx/Dockerfile.pypgx), and a rename is preferable to hand-rolling a
# second character filter that would have to be kept in step with it.
#
# Longest suffixes first: `.vcf.gz` must win over `.vcf`.
ALLOWED_UPLOAD_SUFFIXES = (
    ".vcf.gz",
    ".vcf.bgz",
    ".vcf",
    ".bcf",
    ".bam",
    ".cram",
    ".sam",
    ".fastq.gz",
    ".fq.gz",
    ".fastq",
    ".fq",
)


def safe_upload_name(original: Optional[str], default_suffix: str) -> str:
    """Return a shell-inert, collision-free on-disk name for an upload.

    Only the *extension* of `original` is honoured, and only if it appears in
    ALLOWED_UPLOAD_SUFFIXES; everything else about the caller's name is
    discarded. An unrecognised or missing extension falls back to
    `default_suffix`, which the caller picks from the endpoint's contract.

    Nothing downstream correlates on the original name: the endpoints return
    the paths they actually wrote (`input_file` / `vcf_path`) and the caller
    uses those, and the one place the original name is still consulted -
    input-type detection in /genotype - only classifies the extension and never
    touches the filesystem.
    """
    suffix = default_suffix
    # Strip any directory component under both separators before matching, so a
    # name like `..\evil/x.vcf` cannot smuggle one through.
    name = (original or "").strip().replace("\\", "/").rsplit("/", 1)[-1].lower()
    for candidate in ALLOWED_UPLOAD_SUFFIXES:
        if name.endswith(candidate):
            suffix = candidate
            break
    return f"{uuid.uuid4().hex}{suffix}"


# --------------------------------------------------------------------------
# Gene name hardening
# --------------------------------------------------------------------------
# A gene name is not only a command argument. run_pypgx() builds a filesystem
# path out of it - `Path(output_dir) / f"{gene}-pipeline"` - and passes it to
# the pypgx CLI as a *positional* argument. Running commands as argv lists
# stops a gene name becoming shell syntax, but it does not stop:
#
#   * path escape - `../../../../TMP/X` walks out of the job directory and
#     makes pypgx write there;
#   * argument injection - a leading `-` is read by pypgx's own option parser
#     as a flag rather than as the gene to call.
#
# Neither needs a shell, so both survive the argv rewrite. They are closed here
# instead, on shape.
#
# The rule accepts every gene name the shipped catalogue can produce
# (97 distinct names across config/genes.json, including the hyphenated HLA-A,
# HLA-B, HLA-C and MT-RNR1) and nothing else. Requiring the first character to
# be alphanumeric rejects a leading `-`; excluding `.` and both slashes makes
# `..` and any path separator unrepresentable rather than merely filtered.
GENE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


def validate_gene_names(genes: List[str]) -> List[str]:
    """Reject any gene name that is not a plain identifier. Returns the list.

    Applied to every requested gene set regardless of where it came from, so a
    branch added later inherits the check instead of having to remember it.
    """
    invalid = [g for g in genes if not GENE_NAME_RE.fullmatch(g)]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid gene name(s): {invalid}. Gene names must start with a "
                "letter or digit and contain only letters, digits, '_' and '-'."
            ),
        )
    return genes


# --------------------------------------------------------------------------
# Command helpers
# --------------------------------------------------------------------------
# Every external command in this module runs as an argv list with the default
# shell=False. That closes the injection class at the sink as well as at the
# source: no metacharacter in any argument can be reinterpreted as syntax,
# whatever a future caller passes in.


def bgzip_to(src: str, dest: str) -> None:
    """bgzip `src` into `dest`.

    The `> dest` redirection is the only reason this ever needed a shell; an
    explicit stdout handle replaces it, so no shell is involved.
    """
    with open(dest, "wb") as out_fh:
        subprocess.run(["bgzip", "-c", str(src)], stdout=out_fh, check=True)


def bgzip_in_place(path: str) -> None:
    """bgzip `path`, replacing it with `path`.gz."""
    subprocess.run(["bgzip", "-f", str(path)], check=True)


def tabix_index(vcf_gz: str, force: bool = False) -> None:
    """Build a tabix index for a bgzipped VCF."""
    argv = ["tabix"]
    if force:
        argv.append("-f")
    argv.extend(["-p", "vcf", str(vcf_gz)])
    subprocess.run(argv, check=True)


# Gene Configuration Management
class GeneConfig:
    """Manages PyPGx supported genes configuration"""

    def __init__(self, config_path: Optional[str] = None):
        if config_path:
            self.config_path = Path(config_path)
        else:
            # Try multiple possible locations for the config file
            possible_paths = [
                Path(__file__).parent.parent / "config" / "genes.json",  # docker/pypgx/../config/
                Path(__file__).parent.parent.parent / "config" / "genes.json",  # docker/pypgx/../../config/
                Path.cwd() / "config" / "genes.json",  # From current working directory
            ]
            self.config_path = None
            for path in possible_paths:
                if path.exists():
                    self.config_path = path
                    break
            # If no path found, use the most likely one (will trigger fallback)
            if self.config_path is None:
                self.config_path = Path.cwd() / "config" / "genes.json"
        self._config = None
        self._supported_genes = None

    def load_config(self) -> Dict[str, Any]:
        """Load gene configuration from JSON file"""
        if self._config is not None:
            return self._config

        try:
            with open(self.config_path, 'r') as f:
                self._config = json.load(f)
            logger.info(f"Loaded gene configuration from {self.config_path}")
            return self._config
        except FileNotFoundError:
            logger.warning(f"Gene configuration file not found at {self.config_path}, using fallback")
            self._config = self._get_fallback_config()
            return self._config
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in gene configuration: {e}")
            self._config = self._get_fallback_config()
            return self._config

    def get_supported_genes(self) -> List[str]:
        """Get list of all supported genes (maintains backward compatibility)"""
        if self._supported_genes is not None:
            return self._supported_genes

        config = self.load_config()
        if "sets" in config and "all" in config["sets"]:
            self._supported_genes = config["sets"]["all"]
        else:
            # Fallback to extracting from genes list
            self._supported_genes = [gene["name"] for gene in config.get("genes", [])]

        return self._supported_genes

    def get_gene_set(self, set_name: str = "all") -> List[str]:
        """Get a specific gene set"""
        config = self.load_config()
        if "sets" in config and set_name in config["sets"]:
            return config["sets"][set_name]
        elif set_name == "all":
            return self.get_supported_genes()
        else:
            logger.warning(f"Gene set '{set_name}' not found, returning all genes")
            return self.get_supported_genes()

    def get_gene_info(self, gene_name: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a specific gene"""
        config = self.load_config()
        for gene in config.get("genes", []):
            if gene["name"].upper() == gene_name.upper():
                return gene
        return None

    def get_categories(self) -> Dict[str, Any]:
        """Get gene categories information"""
        config = self.load_config()
        return config.get("categories", {})

    def _get_fallback_config(self) -> Dict[str, Any]:
        """Fallback configuration if JSON file is not available"""
        logger.warning("Using fallback gene configuration")
        return {
            "metadata": {
                "version": "fallback",
                "description": "Fallback PyPGx supported genes",
                "total_genes": 87
            },
            "genes": [{"name": gene, "category": "unknown", "status": "active"}
                     for gene in self._get_fallback_gene_list()],
            "sets": {
                "all": self._get_fallback_gene_list(),
                "core": ["CYP2D6", "CYP2C9", "CYP2C19", "CYP3A4", "CYP3A5"]
            }
        }

    def _get_fallback_gene_list(self) -> List[str]:
        """Fallback list of supported genes"""
        return [
            "ABCB1", "ABCG2", "ACYP2", "ADRA2A", "ADRB2", "ANKK1", "APOE", "ATM", "BCHE", "BDNF",
            "CACNA1S", "CFTR", "COMT", "CYP1A1", "CYP1A2", "CYP1B1", "CYP2A6", "CYP2A13", "CYP2B6",
            "CYP2C8", "CYP2C9", "CYP2C19", "CYP2D6", "CYP2E1", "CYP2F1", "CYP2J2", "CYP2R1", "CYP2S1",
            "CYP2W1", "CYP3A4", "CYP3A5", "CYP3A7", "CYP3A43", "CYP4A11", "CYP4A22", "CYP4B1", "CYP4F2",
            "CYP17A1", "CYP19A1", "CYP26A1", "DBH", "DPYD", "DRD2", "F2", "F5", "G6PD", "GRIK1", "GRIK4",
            "GRIN2B", "GSTM1", "GSTP1", "GSTT1", "HTR1A", "HTR2A", "IFNL3", "IFNL4", "ITGB3", "ITPA",
            "MTHFR", "NAT1", "NAT2", "NUDT15", "OPRK1", "OPRM1", "POR", "PTGIS", "RARG", "RYR1", "SLC6A4",
            "SLC15A2", "SLC22A2", "SLC28A3", "SLC47A2", "SLCO1B1", "SLCO1B3", "SLCO2B1", "SULT1A1",
            "TBXAS1", "TPMT", "UGT1A1", "UGT1A4", "UGT1A6", "UGT2B7", "UGT2B15", "UGT2B17", "VKORC1", "XPC"
        ]

# Initialize gene configuration
gene_config = GeneConfig()

# Memory and parallel processing configuration
PYPGX_MEMORY_LIMIT = os.getenv('PYPGX_MEMORY_LIMIT', '7G')
PYPGX_MAX_PARALLEL_GENES = int(os.getenv('PYPGX_MAX_PARALLEL_GENES', '8'))
PYPGX_BATCH_SIZE = int(os.getenv('PYPGX_BATCH_SIZE', '4'))

# PyPGx/PharmCAT preference configuration
PYPGX_PHARMCAT_PREFERENCE = os.getenv('PYPGX_PHARMCAT_PREFERENCE', 'auto').lower()

def get_memory_usage() -> Dict[str, float]:
    """Get current memory usage statistics"""
    try:
        memory = psutil.virtual_memory()
        return {
            'total_gb': memory.total / (1024**3),
            'available_gb': memory.available / (1024**3),
            'used_gb': memory.used / (1024**3),
            'percent_used': memory.percent
        }
    except Exception as e:
        logger.warning(f"Failed to get memory usage: {e}")
        return {'total_gb': 0, 'available_gb': 0, 'used_gb': 0, 'percent_used': 0}

def calculate_optimal_batch_size(file_size_gb: float, available_memory_gb: float) -> int:
    """Calculate optimal batch size based on file size and available memory"""
    # Base batch size from environment
    base_batch_size = PYPGX_BATCH_SIZE
    
    # Adjust based on file size and available memory
    if file_size_gb > 1.0:  # Large VCF file
        # For large files, use smaller batches to conserve memory
        memory_factor = min(available_memory_gb / 8.0, 1.0)  # Scale based on available memory
        optimal_size = max(2, int(base_batch_size * memory_factor))
    else:
        # For smaller files, can use larger batches
        optimal_size = min(base_batch_size * 2, PYPGX_MAX_PARALLEL_GENES)
    
    return min(optimal_size, PYPGX_MAX_PARALLEL_GENES)

def chunk_list(lst: List[str], chunk_size: int) -> List[List[str]]:
    """Split a list into chunks of specified size"""
    return [lst[i:i + chunk_size] for i in range(0, len(lst), chunk_size)]

def determine_pypgx_gene_set(preference: str, input_type: str) -> str:
    """
    Determine which gene set PyPGx should use based on preference and input type.
    
    Args:
        preference: Environment variable value ('auto', 'pypgx', 'pharmcat')
        input_type: Input file type from workflow data ('vcf', 'bam', 'fastq', 'unknown')
    
    Returns:
        Gene set name to use
    """
    if preference == 'pypgx':
        # Always prefer PyPGx calls
        return 'pypgx'
    elif preference == 'pharmcat':
        # Always prefer PharmCAT calls for overlapping genes
        return 'pypgx_minus_pharmcat'
    elif preference == 'auto':
        # Auto mode: VCF -> prefer PharmCAT, BAM/FASTQ -> prefer PyPGx
        if input_type == 'vcf':
            return 'pypgx_minus_pharmcat'
        else:  # bam, fastq, unknown
            return 'pypgx'
    else:
        # Invalid preference, default to auto behavior
        logger.warning(f"Invalid PYPGX_PHARMCAT_PREFERENCE value: {preference}, using auto")
        if input_type == 'vcf':
            return 'pypgx_minus_pharmcat'
        else:
            return 'pypgx'

async def process_gene_batch_parallel(
    genes: List[str], 
    vcf_path: str, 
    job_dir: str, 
    reference_genome: str,
    max_workers: int = None,
    job_id: str = None,
    job_client = None
) -> Dict[str, Any]:
    """Process a batch of genes in parallel using ThreadPoolExecutor"""
    if max_workers is None:
        max_workers = min(len(genes), PYPGX_MAX_PARALLEL_GENES)
    
    results = {}
    logger.info(f"Processing {len(genes)} genes in parallel with {max_workers} workers")
    
    # Check for cancellation before starting batch processing
    if job_client:
        try:
            if await job_client.is_job_cancelled():
                logger.info(f"Workflow {job_id} is cancelled, aborting batch processing")
                return {"cancelled": True, "message": "Workflow has been cancelled"}
        except Exception as e:
            logger.warning(f"Failed to check workflow cancellation status: {e}")
    
    # Ensure VCF is compressed and indexed BEFORE parallel processing
    vcf_path = str(vcf_path)
    vcf_gz = vcf_path if vcf_path.endswith('.gz') else f"{vcf_path}.gz"
    tbi_path = f"{vcf_gz}.tbi"
    
    if not os.path.exists(vcf_gz):
        logger.info(f"bgzip compressing VCF for tabix: {vcf_path} -> {vcf_gz}")
        bgzip_to(vcf_path, vcf_gz)

    if not os.path.exists(tbi_path):
        logger.info(f"Indexing VCF with tabix: {vcf_gz}")
        tabix_index(vcf_gz)
    
    # Log memory usage before processing
    memory_before = get_memory_usage()
    logger.info(f"Memory before batch processing: {memory_before['used_gb']:.2f}GB used, {memory_before['available_gb']:.2f}GB available")
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all gene processing tasks
        future_to_gene = {
            executor.submit(run_pypgx, vcf_gz, job_dir, gene, reference_genome, job_id): gene 
            for gene in genes
        }
        
        # Collect results as they complete
        for future in as_completed(future_to_gene):
            # Check for cancellation before processing each result
            if job_client:
                try:
                    if await job_client.is_job_cancelled():
                        logger.info(f"Workflow {job_id} is cancelled, stopping batch processing")
                        # Cancel remaining futures
                        for f in future_to_gene:
                            if not f.done():
                                f.cancel()
                        return {"cancelled": True, "message": "Workflow has been cancelled", "partial_results": results}
                except Exception as e:
                    logger.warning(f"Failed to check workflow cancellation status: {e}")
            
            gene = future_to_gene[future]
            try:
                result = future.result()
                results[gene] = result
                logger.info(f"Completed processing gene {gene}")
            except Exception as e:
                logger.exception(f"Error processing gene {gene}")
                results[gene] = {"success": False, "error": str(e)}
    
    # Log memory usage after processing
    memory_after = get_memory_usage()
    logger.info(f"Memory after batch processing: {memory_after['used_gb']:.2f}GB used, {memory_after['available_gb']:.2f}GB available")
    
    return results

# --------------------------------------------------------------------------
# Bounded logging (BACKLOG 252). Same block, same values, same failure
# behaviour in every ZaroPGx sidecar: docker/gatk-api/gatk_api.py,
# docker/nextflow/runner.py, docker/pharmcat/pharmcat.py and
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
PROGRESS_LOG_PATH = os.environ.get('PYPGX_PROGRESS_LOG', '/data/pypgx_progress.log')
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
            "Could not open log file %s (%s) - logging to console only. Inside the "
            "container this means the shared volume is not mounted, and the main app "
            "will not see this service's progress.",
            path,
            exc,
        )


_log_handlers = [logging.StreamHandler()]  # Console output
# File output for progress tracking, accessible to main app
_progress_handler = _bounded_file_handler(PROGRESS_LOG_PATH)
if _progress_handler is not None:
    _log_handlers.append(_progress_handler)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=_log_handlers
)
logger = logging.getLogger("pypgx_wrapper")
_warn_about_unopened_logs(logger)

# Directory setup
DATA_DIR = Path(os.getenv('DATA_DIR', '/data'))
TEMP_DIR = DATA_DIR / 'temp'
REFERENCE_DIR = Path(os.getenv('REFERENCE_DIR', '/reference'))
REPORT_DIR = Path(os.getenv('REPORT_DIR', '/data/reports'))

# Create necessary directories
os.makedirs(TEMP_DIR, exist_ok=True)

# Load supported genes from configuration (replaces hardcoded SUPPORTED_GENES list)
SUPPORTED_GENES = gene_config.get_supported_genes()

# Store running processes by workflow_id for cancellation
running_processes: Dict[str, Dict[str, Any]] = {}

class CancelRequest(BaseModel):
    job_id: str
    patient_id: str
    action: str

def register_process(process_key: str, pid: int, process_info: Dict[str, Any] = None):
    """Register a running process for a workflow."""
    running_processes[process_key] = {
        "pid": pid,
        "start_time": time.time(),
        **(process_info or {})
    }
    logger.info(f"Registered process {pid} for {process_key}")

def unregister_process(process_key: str):
    """Unregister a process when it completes normally."""
    if process_key in running_processes:
        del running_processes[process_key]
        logger.info(f"Unregistered process for {process_key}")

app = FastAPI(
    title="PyPGx Wrapper API",
    description="REST API for PyPGx supported star allele calling",
    version="0.2.8",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health_check():
    """Health check endpoint with gene configuration info and memory status"""
    config = gene_config.load_config()
    memory_info = get_memory_usage()
    
    return {
        'status': 'healthy',
        'service': 'pypgx-wrapper',
        'config_version': config.get('metadata', {}).get('version', 'unknown'),
        'total_supported_genes': len(SUPPORTED_GENES),
        'supported_genes_sample': SUPPORTED_GENES[:5],  # Show first 5 genes
        'memory_usage': memory_info,
        'parallel_config': {
            'max_parallel_genes': PYPGX_MAX_PARALLEL_GENES,
            'batch_size': PYPGX_BATCH_SIZE,
            'memory_limit': PYPGX_MEMORY_LIMIT
        },
        'preference_config': {
            'pypgx_pharmcat_preference': PYPGX_PHARMCAT_PREFERENCE,
            'available_gene_sets': {
                'pypgx': len(gene_config.get_gene_set('pypgx')),
                'pypgx_minus_pharmcat': len(gene_config.get_gene_set('pypgx_minus_pharmcat')),
                'pharmcat_can_call': len(gene_config.get_gene_set('pharmcat_can_call'))
            }
        },
        'timestamp': time.time()
    }

@app.get("/")
def root():
    """API root endpoint"""
    return {
        "message": "PyPGx Wrapper API",
        "usage": "POST to /genotype with a VCF file to call alleles",
        "version": "0.2.8",
        "endpoints": [
            "GET /health - Health check with gene config info",
            "GET /genes - Get supported genes information",
            "GET /genes/{gene_name} - Get detailed gene information",
            "GET /gene-sets - Get available gene sets",
            "POST /genotype - Run genotyping analysis",
            "POST /create-input-vcf - Create VCF from alignment file"
        ]
    }

@app.get("/genes")
def get_supported_genes():
    """Get list of all supported genes with metadata"""
    config = gene_config.load_config()
    return {
        "total_count": len(SUPPORTED_GENES),
        "genes": SUPPORTED_GENES,
        "metadata": config.get("metadata", {})
    }

@app.get("/genes/{gene_name}")
def get_gene_details(gene_name: str):
    """Get detailed information about a specific gene"""
    gene_info = gene_config.get_gene_info(gene_name.upper())
    if gene_info:
        return gene_info
    else:
        raise HTTPException(status_code=404, detail=f"Gene {gene_name} not found")

@app.get("/gene-sets")
def get_gene_sets():
    """Get available gene sets"""
    config = gene_config.load_config()
    sets_info = {}
    if "sets" in config:
        for set_name, genes in config["sets"].items():
            sets_info[set_name] = {
                "count": len(genes),
                "description": f"{set_name.title()} gene set",
                "genes": genes[:10]  # Show first 10 genes as sample
            }
    return sets_info

@app.post("/create-input-vcf")
async def create_input_vcf(
    file: UploadFile = File(...),
    reference_genome: str = Form("hg38"),
    patient_id: Optional[str] = Form(None),
    report_id: Optional[str] = Form(None),
    job_id: Optional[str] = Form(None),
    step_name: Optional[str] = Form("pypgx_bam2vcf")
):
    """
    Create an input VCF (SNVs/indels) from a BAM/CRAM/SAM using PyPGx's recommended method.

    Returns JSON with the path to the generated VCF (bgzipped) and its index.
    """
    if reference_genome not in ["hg19", "hg38", "GRCh37", "GRCh38"]:
        raise HTTPException(status_code=400, detail=f"Reference genome {reference_genome} is not supported. Use hg19/GRCh37 or hg38/GRCh38.")

    # Initialize workflow client if job_id is provided
    job_client = None
    if job_id:
        try:
            job_client = JobClient(job_id=job_id, step_name=step_name)
            await job_client.start_step(f"Starting BAM to VCF conversion for {file.filename}")
            await job_client.log_progress(f"Converting {file.filename} to VCF", {
                "filename": file.filename,
                "reference_genome": reference_genome
            })
        except Exception as e:
            logger.warning(f"Failed to initialize workflow client: {e}")
            job_client = None

    # Normalize to GRCh37/GRCh38 wording for PyPGx
    pypgx_assembly = "GRCh37" if reference_genome in ("hg19", "GRCh37") else "GRCh38"

    local_job_id = str(uuid.uuid4())
    job_dir = TEMP_DIR / local_job_id
    os.makedirs(job_dir, exist_ok=True)

    try:
        # Save uploaded alignment file under a generated name - the client's
        # filename never reaches the filesystem or a command line.
        safe_name = safe_upload_name(file.filename, ".bam")
        input_path = job_dir / safe_name
        with open(input_path, "wb") as f:
            content = await file.read()
            f.write(content)

        # Determine output VCF path. Derived from the generated stem, not from
        # the client's name; `safe_name` is `<hex><suffix>` and the hex part
        # never contains a dot, so splitting on the first dot yields the stem
        # for single- and double-suffix names alike.
        output_vcf_gz = job_dir / (safe_name.split(".", 1)[0] + ".vcf.gz")

        res = run_pypgx_create_input_vcf(str(input_path), str(output_vcf_gz), pypgx_assembly)
        if not res.get("success"):
            if job_client:
                await job_client.log_progress(f"BAM to VCF conversion failed: {res.get('error', 'Unknown error')}", {"error": res.get("error")})
                await job_client.complete_step(f"BAM to VCF conversion failed: {res.get('error', 'Unknown error')}")
            raise HTTPException(status_code=500, detail=res.get("error", "PyPGx create-input-vcf failed"))

        # Update workflow with success
        if job_client:
            await job_client.log_progress(f"BAM to VCF conversion completed successfully", {
                "vcf_path": str(output_vcf_gz),
                "assembly": pypgx_assembly
            })
            await job_client.complete_step("BAM to VCF conversion completed successfully")

        payload: Dict[str, Any] = {
            "success": True,
            "job_id": local_job_id,
            "input_file": str(input_path),
            "vcf_path": str(output_vcf_gz),
            "tbi_path": str(output_vcf_gz) + ".tbi",
            "assembly": pypgx_assembly,
        }
        if patient_id:
            payload["patient_id"] = patient_id
        if report_id:
            payload["report_id"] = report_id
        return payload

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error creating VCF from alignment with PyPGx")
        if job_client:
            await job_client.log_progress(f"BAM to VCF conversion failed: {str(e)}", {"error": str(e)})
            await job_client.complete_step(f"BAM to VCF conversion failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error creating VCF from alignment with PyPGx: {str(e)}")

def run_pypgx_create_input_vcf(alignment_path: str, output_vcf_gz: str, assembly: str) -> Dict[str, Any]:
    """Run PyPGx create-input-vcf to generate a VCF from an alignment file.

    Tries the documented invocation first; if it fails, tries a fallback form.
    Ensures the output is bgzipped and tabix-indexed.
    """
    try:
        # Primary, recommended form (assumed):
        # pypgx create-input-vcf --assembly GRCh38 --bam <bam> --output <out.vcf.gz>
        cmd_primary = [
            "pypgx", "create-input-vcf",
            "--assembly", str(assembly),
            "--bam", str(alignment_path),
            "--output", str(output_vcf_gz),
        ]
        logger.info(f"Running PyPGx (primary) create-input-vcf: {shlex.join(cmd_primary)}")
        proc = subprocess.run(cmd_primary, text=True, capture_output=True)
        if proc.returncode != 0:
            logger.warning(f"Primary create-input-vcf failed (rc={proc.returncode}). stderr: {proc.stderr}\nTrying fallback invocation form.")
            # Fallback form in case of different CLI signature
            cmd_fallback = [
                "pypgx", "create-input-vcf",
                str(alignment_path), str(output_vcf_gz),
                "--assembly", str(assembly),
            ]
            logger.info(f"Running PyPGx (fallback) create-input-vcf: {shlex.join(cmd_fallback)}")
            proc = subprocess.run(cmd_fallback, text=True, capture_output=True)
            if proc.returncode != 0:
                logger.error(f"PyPGx create-input-vcf failed. stderr: {proc.stderr}")
                return {"success": False, "error": proc.stderr or "create-input-vcf failed"}

        # Ensure bgzip + tabix index present
        if not os.path.exists(output_vcf_gz):
            # Some PyPGx versions may output .vcf (uncompressed) – try to find and compress
            raw_vcf = output_vcf_gz[:-3] if output_vcf_gz.endswith('.gz') else output_vcf_gz
            if os.path.exists(raw_vcf):
                logger.info(f"bgzip compressing raw VCF: {raw_vcf}")
                bgzip_in_place(raw_vcf)
            else:
                return {"success": False, "error": "Expected VCF output not found"}

        tbi_path = output_vcf_gz + ".tbi"
        if not os.path.exists(tbi_path):
            logger.info(f"Indexing VCF with tabix: {output_vcf_gz}")
            tabix_index(output_vcf_gz, force=True)

        return {"success": True, "vcf": output_vcf_gz, "tbi": tbi_path}
    except subprocess.CalledProcessError as cpe:
        logger.exception("Subprocess error running create-input-vcf")
        return {"success": False, "error": str(cpe)}
    except Exception as e:
        logger.exception("Unexpected error running create-input-vcf")
        return {"success": False, "error": str(e)}

@app.post("/genotype")
async def genotype(
    file: UploadFile = File(...),
    gene: str = Form(None),
    genes: str = Form("ALL"),
    gene_set: str = Form(None),
    reference_genome: str = Form("hg19"),
    patient_id: Optional[str] = Form(None),
    report_id: Optional[str] = Form(None),
    job_id: Optional[str] = Form(None),
    step_name: Optional[str] = Form("pypgx_analysis"),
    input_type: Optional[str] = Form(None),
):
    """
    Run PyPGx on a VCF file to determine alleles

    Args:
        file: The VCF file to analyze
        gene: Single gene to analyze (legacy)
        genes: Comma-separated gene list or "ALL" or gene set name
        gene_set: Predefined gene set (core, cyp450, etc.)
        reference_genome: Reference genome (hg19 or hg38)

    Returns:
        Genotyping results
    """
    # Normalize requested genes: support single gene, comma-separated list, gene sets, or ALL
    requested_genes: List[str]

    # Check if genes parameter refers to a predefined gene set
    if genes and genes.strip().upper() != "ALL":
        potential_set = genes.strip().lower()
        available_sets = gene_config.get_categories().keys()
        if potential_set in ["core", "cyp450", "all"] or potential_set in available_sets:
            requested_genes = gene_config.get_gene_set(potential_set)
        else:
            # Parse as comma-separated list. This is raw request text, so it
            # gets the same SUPPORTED_GENES membership check the legacy
            # `gene`/`genes` branch below has always applied - the two paths
            # accept the same kind of input and had no business disagreeing.
            gene_list = [g.strip().upper() for g in genes.split(',') if g.strip()]
            requested_genes = sorted(set(g for g in gene_list))
            unsupported = [g for g in requested_genes if g not in SUPPORTED_GENES]
            if unsupported:
                raise HTTPException(status_code=400, detail=f"Unsupported genes: {unsupported}. Supported genes: {SUPPORTED_GENES}")
    elif gene_set:
        # Use explicit gene set parameter
        requested_genes = gene_config.get_gene_set(gene_set.lower())
    elif genes and genes.strip().upper() == "ALL":
        # For "ALL", apply preference logic based on input file type
        # Use input_type from workflow data if available, otherwise fallback to detection
        detected_input_type = input_type
        if not detected_input_type:
            # Fallback: detect from filename if input_type not provided
            filename = file.filename.lower() if file.filename else ""
            if filename.endswith(('.vcf', '.vcf.gz')):
                detected_input_type = 'vcf'
            elif filename.endswith(('.bam', '.cram', '.sam')):
                detected_input_type = 'bam'
            elif filename.endswith(('.fastq', '.fq', '.fastq.gz', '.fq.gz')):
                detected_input_type = 'fastq'
            else:
                detected_input_type = 'unknown'
        
        logger.info(f"Using input type: {detected_input_type} (from workflow: {input_type is not None})")
        
        # Determine appropriate gene set based on preference
        preferred_gene_set = determine_pypgx_gene_set(PYPGX_PHARMCAT_PREFERENCE, detected_input_type)
        logger.info(f"Using gene set: {preferred_gene_set} (preference: {PYPGX_PHARMCAT_PREFERENCE}, input_type: {detected_input_type})")
        
        requested_genes = gene_config.get_gene_set(preferred_gene_set)
    else:
        # Merge legacy single `gene` with `genes` list if provided
        gene_list = []
        if genes and genes.strip():
            gene_list.extend([g.strip().upper() for g in genes.split(',') if g.strip()])
        if gene:
            gene_list.append(gene.strip().upper())
        # De-duplicate and validate
        requested_genes = sorted(set(g for g in gene_list))
        unsupported = [g for g in requested_genes if g not in SUPPORTED_GENES]
        if unsupported:
            raise HTTPException(status_code=400, detail=f"Unsupported genes: {unsupported}. Supported genes: {SUPPORTED_GENES}")
        if not requested_genes:
            requested_genes = ["CYP2D6"]

    # Single choke point, deliberately outside the branch chain above: whatever
    # a branch produced, or a branch added later produces, is shape-checked
    # before it can reach a path join or a CLI argument.
    #
    # Membership in SUPPORTED_GENES is enforced on the two branches that read
    # raw request text, but NOT here, because SUPPORTED_GENES is not a complete
    # catalogue: config/genes.json's `neuropsychopharmacogenes_panel_missing`
    # set legitimately holds six names (ANK3, CACNA1C, HTR2C, MC4R, SCN1A,
    # SCN2A) that are absent from `sets.all`. Enforcing membership on
    # config-derived sets would reject a valid configuration.
    validate_gene_names(requested_genes)

    if reference_genome not in ["hg19", "hg38", "GRCh37", "GRCh38"]:
        raise HTTPException(status_code=400, detail=f"Reference genome {reference_genome} is not supported. Use hg19/GRCh37 or hg38/GRCh38.")
    
    # Determine assembly string for PyPGx (expects GRCh37/GRCh38 columns like 'GRCh38Region')
    if reference_genome in ("hg19", "GRCh37"):
        pypgx_assembly = "GRCh37"
    else:
        pypgx_assembly = "GRCh38"
    
    # Create a unique job directory
    local_job_id = str(uuid.uuid4())
    job_dir = TEMP_DIR / local_job_id
    os.makedirs(job_dir, exist_ok=True)
    
    
    # Initialize workflow client if job_id is provided
    job_client = None
    if job_id:
        try:
            job_client = JobClient(job_id=job_id, step_name=step_name)
            
            # Check if workflow has been cancelled before starting
            if await job_client.is_job_cancelled():
                logger.info(f"Workflow {job_id} is cancelled, aborting PyPGx processing")
                return {"success": False, "error": "Workflow has been cancelled"}
            
            await job_client.start_step(f"Starting PyPGx analysis for {len(requested_genes)} genes")
            await job_client.log_progress(f"Processing {file.filename} with PyPGx", {
                "genes": requested_genes,
                "reference_genome": reference_genome,
                "file_size_gb": 0  # Will be updated after file is saved
            })
        except Exception as e:
            logger.warning(f"Failed to initialize workflow client: {e}")
            job_client = None
    
    try:
        # Save the uploaded VCF file under a generated name. Input-type
        # detection above still reads file.filename, but only to classify the
        # extension - it never builds a path from it.
        input_filepath = job_dir / safe_upload_name(file.filename, ".vcf")
        with open(input_filepath, "wb") as f:
            content = await file.read()
            f.write(content)
        
        # Get file size for memory optimization
        file_size_gb = os.path.getsize(input_filepath) / (1024**3)
        memory_info = get_memory_usage()
        
        logger.info(f"Processing PyPGx genotyping for {len(requested_genes)} genes")
        logger.info(f"File size: {file_size_gb:.2f}GB, Available memory: {memory_info['available_gb']:.2f}GB")
        
        # Update workflow with file information
        if job_client:
            await job_client.log_progress(f"File uploaded: {file_size_gb:.2f}GB", {
                "file_size_gb": file_size_gb,
                "available_memory_gb": memory_info['available_gb'],
                "total_genes": len(requested_genes)
            })
        
        # Calculate optimal batch size based on file size and available memory
        optimal_batch_size = calculate_optimal_batch_size(file_size_gb, memory_info['available_gb'])
        logger.info(f"Using batch size: {optimal_batch_size} genes per batch")
        
        # Split genes into batches for parallel processing
        gene_batches = chunk_list(requested_genes, optimal_batch_size)
        
        aggregated: Dict[str, Any] = {"success": True, "results": {}, "job_id": local_job_id}
        if patient_id:
            aggregated["patient_id"] = patient_id
        if report_id:
            aggregated["report_id"] = report_id
        
        # Process each batch in parallel
        for batch_idx, gene_batch in enumerate(gene_batches):
            logger.info(f"Processing batch {batch_idx + 1}/{len(gene_batches)} with {len(gene_batch)} genes: {gene_batch}")
            
            # Log batch start for progress tracking
            batch_start_time = time.time()
            total_genes = len(requested_genes)
            logger.info(f"BATCH_START: batch={batch_idx + 1}, total_batches={len(gene_batches)}, genes_in_batch={len(gene_batch)}, total_genes={total_genes}")
            
            # Log batch start for progress tracking
            if job_client:
                await job_client.log_progress(f"Processing batch {batch_idx + 1}/{len(gene_batches)}: {', '.join(gene_batch)}", {
                    "batch_index": batch_idx + 1,
                    "total_batches": len(gene_batches),
                    "genes_in_batch": gene_batch
                })
            
            try:
                # Process the batch in parallel
                batch_results = await process_gene_batch_parallel(
                    gene_batch, 
                    str(input_filepath), 
                    str(job_dir), 
                    pypgx_assembly,
                    max_workers=min(len(gene_batch), PYPGX_MAX_PARALLEL_GENES),
                    job_id=job_id,
                    job_client=job_client
                )
                
                # Check if batch processing was cancelled
                if batch_results.get("cancelled"):
                    logger.info(f"Batch processing cancelled for workflow {job_id}")
                    aggregated["success"] = False
                    aggregated["cancelled"] = True
                    aggregated["message"] = batch_results.get("message", "Workflow cancelled")
                    # Include any partial results
                    if "partial_results" in batch_results:
                        aggregated["results"].update(batch_results["partial_results"])
                    break
                
                # Add batch results to aggregated results
                for gene, result in batch_results.items():
                    aggregated["results"][gene] = result
                    # Only fail overall if it's a systemic issue, not gene-specific
                    if not result.get("success", False) and "No SNV/indel-based star alleles" not in str(result.get("error", "")):
                        aggregated["success"] = False
                
                batch_duration = time.time() - batch_start_time
                genes_completed = (batch_idx + 1) * len(gene_batch)
                progress_percent = int((genes_completed / total_genes) * 100)
                
                logger.info(f"BATCH_COMPLETE: batch={batch_idx + 1}, duration={batch_duration:.2f}s, genes_completed={genes_completed}/{total_genes}, progress={progress_percent}%")
                
                # Update workflow with batch completion
                if job_client:
                    await job_client.log_progress(f"Completed batch {batch_idx + 1}/{len(gene_batches)} in {batch_duration:.2f}s", {
                        "batch_index": batch_idx + 1,
                        "duration_seconds": batch_duration,
                        "genes_completed": genes_completed,
                        "total_genes": total_genes,
                        "progress_percent": progress_percent
                    })
                    
                    # Update the step with progress information for proper mapping
                    await job_client.update_step_status(
                        "running",
                        f"Completed batch {batch_idx + 1}/{len(gene_batches)} in {batch_duration:.2f}s",
                        output_data={"progress_percent": progress_percent}
                    )
                
            except Exception as e:
                logger.exception(f"Error processing batch {batch_idx + 1}")
                # Mark all genes in this batch as failed
                for gene in gene_batch:
                    aggregated["results"][gene] = {"success": False, "error": f"Batch processing error: {str(e)}"}
                    aggregated["success"] = False
                
                # Log error to workflow
                if job_client:
                    await job_client.log_error(f"Error processing batch {batch_idx + 1}: {str(e)}", {
                        "batch_index": batch_idx + 1,
                        "genes_in_batch": gene_batch,
                        "error": str(e)
                    })
        # Move per-gene pipeline folders into per-patient reports dir if patient_id provided
        try:
            if patient_id:
                dest_dir = REPORT_DIR / str(patient_id) / f"pypgx_{local_job_id}"
                dest_dir.mkdir(parents=True, exist_ok=True)
                for item in os.listdir(job_dir):
                    src_path = job_dir / item
                    if src_path.is_dir() and item.endswith("-pipeline"):
                        import shutil
                        shutil.move(str(src_path), str(dest_dir / item))
                aggregated["work_dir"] = str(dest_dir)
        except Exception as mv_e:
            logger.warning(f"Failed to move PyPGx work dirs: {mv_e}")
        # Optionally persist a summary JSON
        # Prefer writing into a per-patient reports directory when patient_id is provided
        try:
            if patient_id:
                dest_dir = REPORT_DIR / str(patient_id)
                dest_dir.mkdir(parents=True, exist_ok=True)
                output_path = dest_dir / f"{local_job_id}_pypgx_results.json"
            else:
                output_path = DATA_DIR / f"{local_job_id}_pypgx_results.json"
        except Exception:
            # Fallback to DATA_DIR on any error creating the reports dir
            output_path = DATA_DIR / f"{local_job_id}_pypgx_results.json"
        output_file = str(output_path)
        try:
            with open(output_file, "w") as f:
                json.dump(aggregated, f, indent=2)
            aggregated["output_file"] = output_file
        except Exception:
            logger.warning("Failed to persist aggregated PyPGx results file")
        # Complete workflow step
        if job_client:
            if aggregated["success"]:
                await job_client.complete_step(f"PyPGx analysis completed successfully for {len(requested_genes)} genes", {
                    "total_genes": len(requested_genes),
                    "successful_genes": len([r for r in aggregated["results"].values() if r.get("success", False)]),
                    "failed_genes": len([r for r in aggregated["results"].values() if not r.get("success", False)]),
                    "output_file": aggregated.get("output_file", "")
                })
            else:
                await job_client.fail_step(f"PyPGx analysis failed: {aggregated.get('error', 'Unknown error')}", {
                    "error": aggregated.get("error", "Unknown error"),
                    "total_genes": len(requested_genes)
                })
        
        return aggregated

    except Exception as e:
        logger.exception("Error processing VCF with PyPGx")
        
        # Log error to workflow
        if job_client:
            await job_client.fail_step(f"PyPGx analysis failed: {str(e)}", {
                "error": str(e),
                "total_genes": len(requested_genes)
            })
        
        # Always return 200 status code - communicate errors through JSON response
        return {
            "success": False,
            "error": f"Error processing VCF with PyPGx: {str(e)}",
            "results": {},
            "job_id": local_job_id
        }

def run_pypgx(vcf_path: str, output_dir: str, gene: str, reference_genome: str = 'hg19', job_id: str = None) -> Dict[str, Any]:
    """Run PyPGx for star allele calling on the input VCF"""
    try:
        # VCF should already be compressed and indexed by the batch processing function
        vcf_path = str(vcf_path)
        vcf_gz = vcf_path if vcf_path.endswith('.gz') else f"{vcf_path}.gz"

        # Determine a pipeline output directory that does NOT pre-exist
        # PyPGx creates the output directory itself; avoid FileExistsError if already present
        #
        # `gene` is attacker-reachable (the comma-separated branch of /genotype
        # takes it straight off the request), and it is used three ways here:
        # to build this path, as a positional pypgx argument, and in the log
        # line below. Keeping it out of a shell is necessary but NOT sufficient
        # - a path separator would escape output_dir and a leading `-` would be
        # parsed by pypgx as an option. validate_gene_names() is what makes
        # those unrepresentable; this join relies on it having run.
        pipeline_dir = Path(output_dir) / f"{gene}-pipeline"
        if pipeline_dir.exists():
            safe_dir = Path(output_dir) / f"{gene}-pipeline-{uuid.uuid4().hex[:6]}"
            pipeline_dir = safe_dir
        
        # Use the appropriate command for NGS pipeline
        # Use the compressed/indexed VCF for PyPGx
        # argv form, no shell - see the note above on why that is only one of
        # the three things `gene` needed protecting against.
        pypgx_cmd = [
            "pypgx", "run-ngs-pipeline",
            str(gene), str(pipeline_dir),
            "--variants", str(vcf_gz),
            "--assembly", str(reference_genome),
        ]

        logger.info(f"Running PyPGx command: {shlex.join(pypgx_cmd)}")

        # Execute PyPGx with process tracking for cancellation
        process = subprocess.Popen(
            pypgx_cmd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        # Register process for cancellation if job_id is provided
        if job_id:
            # Create a unique process key for this gene
            process_key = f"{job_id}_{gene}"
            register_process(process_key, process.pid, {
                "gene": gene,
                "pipeline_dir": str(pipeline_dir),
                "vcf_path": vcf_gz,
                "cleanup_paths": [str(pipeline_dir), vcf_gz]
            })
        
        # Wait for completion and capture output
        # Use a timeout to make the process more responsive to cancellation
        try:
            stdout, stderr = process.communicate(timeout=300)  # 5 minute timeout
            return_code = process.returncode
        except subprocess.TimeoutExpired:
            # If timeout occurs, the process is still running
            # This shouldn't happen in normal operation, but provides a safety net
            logger.warning(f"PyPGx process for gene {gene} timed out after 5 minutes")
            process.kill()
            stdout, stderr = process.communicate()
            return_code = process.returncode
        
        # Unregister process when done
        if job_id:
            process_key = f"{job_id}_{gene}"
            unregister_process(process_key)
        
        # Check if the command was successful
        if return_code != 0:
            stderr_str = stderr or ""
            # Handle case where gene doesn't have SNV/indel star allele definitions
            if "does not have any star alleles defined by SNVs/indels" in stderr_str:
                logger.info(f"Gene {gene} doesn't have SNV/indel-based star alleles - this is expected for some genes")
                return {
                    'success': True,  # Not an error, just no SNV/indel data available
                    'gene': gene,
                    'diplotype': None,
                    'details': {'note': 'No SNV/indel-based star alleles available for this gene'},
                    'job_id': os.path.basename(output_dir),
                    'error': 'No SNV/indel-based star alleles available for this gene'  # For the main loop logic
                }
            logger.error(f"PyPGx failed: {process.stderr}")
            return {
                'success': False,
                'error': f"PyPGx failed: {process.stderr}"
            }
        
        # Extract genotype information from the results
        diplotype, details = parse_pypgx_results(pipeline_dir, gene)
        
        return {
            'success': True,
            'gene': gene,
            'diplotype': diplotype,
            'details': details,
            'job_id': os.path.basename(output_dir)
        }
        
    except Exception as e:
        logger.exception("Error running PyPGx")
        return {
            'success': False,
            'error': f"Error running PyPGx: {str(e)}"
        }

def parse_pypgx_results(pipeline_dir: Path, gene: str) -> tuple:
    """Parse the PyPGx results to extract diplotype and details for a gene.

    Strategy:
    - Prefer genotypes.zip if present; look for TSV/CSV containing rows per gene
    - Fallback to results.zip; scan for TSV/CSV with gene and diplotype fields
    - If nothing parseable is found, return (None, {})
    """
    try:
        gene_upper = gene.upper()

        def parse_zip_for_calls(zip_path: Path) -> Optional[tuple]:
            if not os.path.exists(zip_path):
                return None
            with zipfile.ZipFile(zip_path, 'r') as zf:
                # Iterate members; prefer tsv/csv
                members = zf.namelist()
                for name in members:
                    lower = name.lower()
                    if not (lower.endswith('.tsv') or lower.endswith('.csv')):
                        continue
                    try:
                        with zf.open(name, 'r') as fh:
                            raw = fh.read()
                        text = raw.decode('utf-8', errors='replace')
                        # Detect delimiter
                        delimiter = '\t' if '\t' in text.splitlines()[0] else ','
                        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
                        # Try common column names
                        for row in reader:
                            # Match by gene column if present; else try file-scoped gene
                            row_gene = (row.get('gene') or row.get('Gene') or row.get('GENE') or '').strip().upper()
                            if row_gene and row_gene != gene_upper:
                                continue
                            diplotype = (row.get('diplotype') or row.get('Diplotype') or row.get('DIPLOTYPE') or row.get('genotype') or row.get('Genotype'))
                            phenotype = (row.get('phenotype') or row.get('Phenotype') or row.get('PHENOTYPE'))
                            activity = (row.get('activity_score') or row.get('Activity_Score') or row.get('activityScore') or row.get('ActivityScore'))
                            if diplotype or phenotype or activity:
                                details = {}
                                if phenotype:
                                    details['phenotype'] = str(phenotype).strip()
                                if activity is not None and str(activity).strip() != '':
                                    details['activity_score'] = str(activity).strip()
                                return (str(diplotype).strip() if diplotype else None, details)
                    except Exception:
                        continue
            return None

        # Try genotypes.zip first
        parsed = parse_zip_for_calls(pipeline_dir / 'genotypes.zip')
        if parsed:
            return parsed
        # Fallback to results.zip
        parsed = parse_zip_for_calls(pipeline_dir / 'results.zip')
        if parsed:
            return parsed
        # Nothing found
        return None, {}
    except Exception as e:
        logger.exception(f"Error parsing PyPGx results for {gene}: {str(e)}")
        return None, {}

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
        logger.info(f"Current running processes: {len(running_processes)}")
        logger.info(f"Process keys: {list(running_processes.keys())}")
        
        # Find and terminate processes
        terminated_count = 0
        
        # Check our stored process registry for all processes with this workflow_id
        processes_to_terminate = []
        for process_key, process_info in running_processes.items():
            if process_key.startswith(job_id):
                processes_to_terminate.append((process_key, process_info))
        
        logger.info(f"Found {len(processes_to_terminate)} processes to terminate for job {job_id}")
        
        if processes_to_terminate:
            for process_key, process_info in processes_to_terminate:
                pid = process_info.get("pid")
                logger.info(f"Processing {process_key} with PID {pid}")
                
                if pid and psutil.pid_exists(pid):
                    try:
                        process = psutil.Process(pid)
                        
                        # First try graceful termination
                        process.terminate()
                        logger.info(f"Sent terminate signal to process {pid} for {process_key}")
                        
                        # Wait a short time for graceful shutdown
                        try:
                            process.wait(timeout=5)
                            logger.info(f"Process {pid} terminated gracefully")
                        except psutil.TimeoutExpired:
                            # Force kill if graceful termination fails
                            logger.warning(f"Process {pid} did not terminate gracefully, force killing")
                            process.kill()
                            process.wait(timeout=2)
                            logger.info(f"Force killed process {pid}")
                        
                        terminated_count += 1
                    except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
                        logger.warning(f"Could not terminate process {pid}: {e}")
                    except Exception as e:
                        logger.error(f"Unexpected error terminating process {pid}: {e}")
                else:
                    logger.info(f"Process {pid} for {process_key} no longer exists")
                
                # Clean up specific tracked file paths
                cleanup_paths = process_info.get("cleanup_paths", [])
                logger.info(f"Cleaning up {len(cleanup_paths)} paths for {process_key}")
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
                del running_processes[process_key]
        else:
            logger.warning(f"No running processes found for job {job_id}")
        
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

if __name__ == "__main__":
    uvicorn.run("pypgx_wrapper:app", host="0.0.0.0", port=5000, reload=True) 