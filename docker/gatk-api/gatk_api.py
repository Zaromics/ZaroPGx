#!/usr/bin/env python3
"""
GATK API wrapper service. This provides an HTTP API that makes appropriate
calls to the GATK container.
"""

import os
import json
import logging
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
from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename
import random
from typing import List, Dict

# Set up more verbose logging with both file and console handlers
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('/var/log/gatk_api.log')
    ]
)

logger = logging.getLogger(__name__)
logger.info("Starting GATK API service with enhanced debugging")

app = Flask(__name__)

# Store startup time
app.config["START_TIME"] = time.time()

# Configuration
GATK_CONTAINER = os.environ.get('GATK_CONTAINER', 'gatk')
DATA_DIR = os.environ.get('DATA_DIR', '/data')
TEMP_DIR = os.environ.get('TMPDIR', '/tmp/gatk_temp')
REFERENCE_DIR = os.environ.get('REFERENCE_DIR', '/reference')
MAX_MEMORY = os.environ.get('MAX_MEMORY', '20g')  # Default to 20g per NIH recommendation

# Create directories
logger.info(f"Creating required directories: DATA_DIR={DATA_DIR}, TEMP_DIR={TEMP_DIR}")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(os.path.join(DATA_DIR, 'uploads'), exist_ok=True)
os.makedirs(os.path.join(DATA_DIR, 'results'), exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

# Map reference genome names to file paths
REFERENCE_PATHS = {
    'hg19': os.path.join(REFERENCE_DIR, 'hg19', 'ucsc.hg19.fasta'),
    'hg38': os.path.join(REFERENCE_DIR, 'hg38', 'Homo_sapiens_assembly38.fasta'),
    'grch37': os.path.join(REFERENCE_DIR, 'grch37', 'human_g1k_v37.fasta'),
    'grch38': os.path.join(REFERENCE_DIR, 'hg38', 'Homo_sapiens_assembly38.fasta')  # symlink
}

# Job queue to track running variant calling jobs
jobs = {}  # job_id -> job_info

# Job status tracking
JOB_STATUS_PENDING = "pending"
JOB_STATUS_INDEXING = "indexing"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_COMPLETED = "completed"
JOB_STATUS_ERROR = "error"

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
            
            # Create the index with default PATH
            cmd = f"samtools index {bam_path}"
            logger.info(f"Job {job_id}: Running command: {cmd}")
            update_job_status(job_id, JOB_STATUS_INDEXING, progress=20, message="Running samtools index")
            
            process = subprocess.run(cmd, shell=True, check=True, capture_output=True)
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

def run_variant_calling(job_id, input_path, output_path, reference_path, regions=None):
    """Run GATK HaplotypeCaller with dynamic memory allocation based on input file size."""
    try:
        # Check file size and customize memory settings
        file_size = os.path.getsize(input_path)
        file_size_gb = file_size / (1024 * 1024 * 1024)
        
        # Get available memory from container
        try:
            import psutil
            total_memory_bytes = psutil.virtual_memory().total
            total_memory_gb = total_memory_bytes / (1024 * 1024 * 1024)
            
            # Log memory information
            logger.info(f"Job {job_id}: System memory - Total: {total_memory_gb:.2f}GB, File size: {file_size_gb:.2f}GB")
            
            # For very large files (> 2GB), use 70% of available memory
            # For smaller files, use default MAX_MEMORY
            if file_size_gb > 2.0:
                # Use 70% of available memory, but cap at MAX_MEMORY if set
                memory_to_use = min(int(total_memory_gb * 0.7), 
                                   int(MAX_MEMORY.replace('g', '')) if MAX_MEMORY.endswith('g') else int(MAX_MEMORY))
                java_options = f"-Xms{memory_to_use}G -Xmx{memory_to_use}G -XX:ParallelGCThreads=2 -XX:+UseG1GC"
                logger.info(f"Job {job_id}: Large file detected, using {memory_to_use}g memory for Java")
            else:
                # Use standard memory settings
                java_options = f"-Xms20G -Xmx20G -XX:ParallelGCThreads=2"
                logger.info(f"Job {job_id}: Using standard memory setting {MAX_MEMORY}")
        except Exception as mem_error:
            # Fall back to default if we can't get memory info
            logger.warning(f"Job {job_id}: Failed to get system memory info: {str(mem_error)}. Using default {MAX_MEMORY}")
            java_options = f"-Xms20G -Xmx20G -XX:ParallelGCThreads=2"
                
        # Update job status
        update_job_status(job_id, JOB_STATUS_RUNNING, progress=30, 
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
                # Build the exclusion arg if we have contigs to exclude
                exclude_arg = ""
                if excluded_contigs:
                    exclude_arg = " ".join([f"-XL {contig}" for contig in excluded_contigs])
                    logger.info(f"Job {job_id}: Excluding contigs: {', '.join(excluded_contigs)}")
                
                # Set up the command
                cmd = f"gatk --java-options '{java_options}' HaplotypeCaller -R {reference_path} -I {input_path} -O {output_path} {regions_arg} {exclude_arg} --verbosity INFO"
                
                # Log the command being run
                logger.info(f"Job {job_id}: Running GATK command (attempt {attempt+1}/{max_retries+1}): {cmd}")
                
                # Prepare the subprocess
                process = subprocess.Popen(
                    cmd,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    universal_newlines=True
                )
                
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
                            logger.info(f"Job {job_id}: Found {len(chromosomes_expected)} chromosomes in reference: {', '.join(chromosomes_expected[:5])}...")
                    else:
                        logger.warning(f"Job {job_id}: Reference index file not found at {fai_path}")
                except Exception as e:
                    logger.warning(f"Job {job_id}: Could not determine chromosome list: {str(e)}")
                
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
                        logger.debug(f"Job {job_id}: Could not get GATK memory usage: {str(e)}")
                        return None
                
                # Process output line by line
                for line in iter(process.stdout.readline, ''):
                    # Log the GATK output
                    logger.debug(f"Job {job_id}: GATK output: {line.strip()}")
                    
                    # Check for contig not present errors
                    contig_not_present_match = re.search(r'Contig\s+(\S+)\s+not\s+present', line)
                    if contig_not_present_match:
                        missing_contig = contig_not_present_match.group(1)
                        contig_error = f"Contig {missing_contig} not present in reference"
                        logger.warning(f"Job {job_id}: {contig_error}")
                        
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
                                logger.info(f"Job {job_id}: GATK processing chromosome: {new_chromosome}")
                                current_chromosome = new_chromosome
                                if new_chromosome not in chromosomes_processed:
                                    chromosomes_processed.append(new_chromosome)
                            
                            # Update progress based on chromosome position
                            progress = update_progress_by_chromosome(current_chromosome)
                            
                            # Get memory usage
                            memory_usage = get_gatk_memory_usage()
                            memory_info = f"({int(memory_usage)}MB used)" if memory_usage else ""
                            
                            # Update job status
                            update_job_status(job_id, JOB_STATUS_RUNNING, progress=progress,
                                            message=f"Processing chromosome {current_chromosome} {memory_info}")
                    
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
                            
                            update_job_status(job_id, JOB_STATUS_RUNNING, progress=int(progress),
                                            message=f"GATK Progress: {gatk_progress:.1f}% {memory_info}")
                    
                    # Check for errors
                    elif 'ERROR' in line:
                        logger.error(f"Job {job_id}: GATK error: {line.strip()}")
                    
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
                            
                            update_job_status(job_id, JOB_STATUS_RUNNING, progress=progress,
                                            message=f"Running GATK HaplotypeCaller ({int(memory_usage)}MB used)")
                
                # Wait for process to complete and get return code
                return_code = process.wait()
                
                # If we had a contig error but the process exited with a non-zero code, retry
                if return_code != 0 and contig_error and attempt < max_retries:
                    logger.warning(f"Job {job_id}: GATK failed with contig error. Will retry excluding: {', '.join(excluded_contigs)}")
                    continue  # Try again with excluded contigs
                
                # If we reach here, either the process succeeded, or it failed without a contig error
                if return_code != 0:
                    raise subprocess.CalledProcessError(return_code, cmd)
                
                # Command completed successfully
                logger.info(f"Job {job_id}: GATK command completed successfully")
                
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
                update_job_status(job_id, JOB_STATUS_COMPLETED, progress=100, 
                                message=f"Variant calling complete{' (excluded: ' + ', '.join(excluded_contigs) + ')' if excluded_contigs else ''}",
                                output_file=output_path,
                                extras=extras_data)
                
                return output_path
                
            except subprocess.CalledProcessError as e:
                # If this was our last attempt, raise the error
                if attempt == max_retries:
                    error_message = f"GATK command failed with exit code {e.returncode}"
                    logger.error(f"Job {job_id}: {error_message}")
                    
                    update_job_status(job_id, JOB_STATUS_ERROR, progress=100, 
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
                                            
                                logger.info(f"Job {job_id}: Proactively excluding viral contig {viral_contig} for retry")
                    
                    logger.warning(f"Job {job_id}: GATK attempt {attempt+1} failed, will retry excluding {len(excluded_contigs)} contigs")
                    continue
        
    except Exception as e:
        error_message = f"Error running GATK HaplotypeCaller: {str(e)}"
        logger.exception(f"Job {job_id}: {error_message}")
        
        update_job_status(job_id, JOB_STATUS_ERROR, progress=100, 
                         message="GATK variant calling failed",
                         error=error_message)
        return None

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint with enhanced information"""
    # Count jobs by status for monitoring
    status_counts = {}
    for job in jobs.values():
        status = job.get("status", "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    
    return jsonify({
        "status": "healthy",
        "timestamp": time.time(),
        "jobs_count": len(jobs),
        "jobs_by_status": status_counts,
        "reference_genomes": list(REFERENCE_PATHS.keys()),
    }), 200

@app.route('/jobs', methods=['GET'])
def list_jobs():
    """List all jobs (with optional filtering)"""
    # Optional filtering
    status_filter = request.args.get('status')
    
    result = []
    for job_id, job_info in jobs.items():
        # Apply status filter if specified
        if status_filter and job_info.get('status') != status_filter:
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
    
    return jsonify(result), 200

def detect_reference(file_path, default_reference='hg38'):
    """
    Detect reference genome from genomic file headers
    
    First tries a fast text-based search, then falls back to samtools for BAM files
    if needed for more accurate detection
    """
    try:
        logger.info(f"Attempting to detect reference genome from file: {file_path}")
        file_ext = os.path.splitext(file_path)[1].lower()
        
        # First try simple text search for all file types (fast)
        try:
            logger.info(f"Trying simple text search for reference genome detection")
            with open(file_path, 'rb') as f:
                # Read first 10KB which should contain any headers
                header = f.read(10240).decode('utf-8', errors='ignore')
                
            # Look for specific reference genome identifiers
            if any(x in header for x in ['GRCh38', 'hg38', 'b38']):
                logger.info(f"Detected hg38/GRCh38 reference via text search")
                return 'hg38'
            elif any(x in header for x in ['GRCh37', 'hg19', 'b37']):
                logger.info(f"Detected hg19/GRCh37 reference via text search") 
                return 'hg19'
            
            logger.info(f"Simple text search did not find reference genome information")
        except Exception as e:
            logger.warning(f"Simple text search failed: {str(e)}")
        
        # For BAM/CRAM/SAM files, try samtools as a fallback if text search failed
        if file_ext in ['.bam', '.cram', '.sam']:
            try:
                logger.info(f"Falling back to samtools for reference detection")
                # Use samtools to get the header
                cmd = f"samtools view -H {file_path}"
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                if result.returncode == 0:
                    header = result.stdout
                    
                    # Check for specific reference genome indicators in the header
                    # First check for @SQ lines with known reference lengths
                    if "SN:chr1\tLN:248956422" in header:
                        logger.info("Detected GRCh38/hg38 reference based on chr1 length")
                        return "hg38"
                    elif "SN:chr1\tLN:249250621" in header:
                        logger.info("Detected GRCh37/hg19 reference based on chr1 length")
                        return "hg19"
                    
                    # Check for reference path in header comments
                    ref_path_match = re.search(r'@PG.*?-R\s+(\S+)', header)
                    if ref_path_match:
                        ref_path = ref_path_match.group(1)
                        logger.info(f"Found reference path in header: {ref_path}")
                        if "hg38" in ref_path or "GRCh38" in ref_path:
                            return "hg38"
                        elif "hg19" in ref_path or "GRCh37" in ref_path:
                            return "hg19"
                    
                    # Check reference dictionary
                    ref_dict_match = re.search(r'@HD.*?VN:(\S+)', header)
                    if ref_dict_match:
                        ref_version = ref_dict_match.group(1)
                        logger.info(f"Found reference version in header: {ref_version}")
                        if "38" in ref_version:
                            return "hg38"
                        elif "19" in ref_version or "37" in ref_version:
                            return "hg19"
            except Exception as e:
                logger.warning(f"Samtools detection failed: {str(e)}")
                
        # For VCF files, check header lines explicitly
        elif file_ext in ['.vcf', '.vcf.gz']:
            try:
                # Open as text directly for VCF files
                with open(file_path, 'r') as f:
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
        
        # If we can't determine, return default
        logger.warning(f"Could not determine reference genome, using default: {default_reference}")
        return default_reference
    except Exception as e:
        logger.error(f"Error detecting reference genome: {str(e)}")
        return default_reference

@app.route('/variant-call', methods=['POST'])
def variant_call():
    """
    Start a variant calling job using GATK HaplotypeCaller

    This endpoint starts an asynchronous GATK job and returns a job ID
    that can be used to check the status of the job.
    """
    try:
        # Check for test mode - for debugging without file upload
        is_test_mode = request.form.get('test_mode', 'false').lower() == 'true'
        if is_test_mode:
            logger.info("Test mode activated - simulating a job without file upload")
            # Generate a job ID
            job_id = str(uuid.uuid4())
            # Create a test job
            jobs[job_id] = {
                "status": JOB_STATUS_PENDING,
                "progress": 0,
                "message": "Test job created",
                "created_at": time.time(),
                "updated_at": time.time(),
                "is_test": True
            }
            # Start a test thread
            threading.Thread(
                target=lambda: simulate_test_job(job_id),
                daemon=True
            ).start()
            return jsonify({
                "job_id": job_id,
                "status": JOB_STATUS_PENDING,
                "message": "Test job started",
                "is_test": True
            }), 202

        # Get or create job_id
        job_id = request.form.get('job_id')
        if job_id:
            logger.info(f"Using provided job_id: {job_id}")
        else:
            job_id = str(uuid.uuid4())
            logger.info(f"Generated new job_id: {job_id}")

        if 'file' not in request.files:
            logger.error("No file provided in request")
            return jsonify({"error": "No file provided"}), 400

        file = request.files['file']
        if file.filename == '':
            logger.error("No filename specified in request")
            return jsonify({"error": "No filename specified"}), 400

        # Get parameters
        reference_genome = request.form.get('reference_genome', 'hg38')
        regions = request.form.get('regions', None)

        logger.info(f"Job {job_id}: Request received - File: {file.filename}, Reference: {reference_genome}, Regions: {regions}")

        # Save uploaded file to a temporary directory
        filename = secure_filename(file.filename)
        input_dir = tempfile.mkdtemp(dir=TEMP_DIR)
        input_path = os.path.join(input_dir, filename)
        output_path = os.path.join(input_dir, f"{os.path.splitext(filename)[0]}.vcf")

        logger.info(f"Job {job_id}: Saving file to {input_path}")
        file.save(input_path)
        logger.info(f"Job {job_id}: Saved uploaded file to {input_path}")
        
        # Check if file exists
        if not os.path.exists(input_path):
            logger.error(f"Job {job_id}: Failed to save uploaded file to {input_path}")
            return jsonify({"error": f"Failed to save uploaded file to {input_path}"}), 500
        
        # Log file details
        file_size = os.path.getsize(input_path)
        logger.info(f"Job {job_id}: File saved: {input_path}, size: {file_size} bytes")
        
        # Auto-detect reference genome for all genomic file types
        file_ext = os.path.splitext(filename)[1].lower()
        if file_ext in ['.bam', '.cram', '.sam', '.vcf', '.vcf.gz']:
            detected_reference = detect_reference(input_path, default_reference=reference_genome)
            if detected_reference != reference_genome:
                logger.warning(f"Job {job_id}: Detected reference ({detected_reference}) differs from specified reference ({reference_genome})")
                logger.warning(f"Job {job_id}: Using detected reference: {detected_reference}")
                reference_genome = detected_reference

        # Validate reference genome
        if reference_genome not in REFERENCE_PATHS:
            logger.error(f"Job {job_id}: Unsupported reference genome: {reference_genome}")
            return jsonify({"error": f"Unsupported reference genome: {reference_genome}"}), 400

        reference_path = REFERENCE_PATHS[reference_genome]
        if not os.path.exists(reference_path):
            logger.error(f"Job {job_id}: Reference genome file not found: {reference_path}")
            return jsonify({"error": f"Reference genome file not found: {reference_path}"}), 500

        # Initialize job info
        jobs[job_id] = {
            "status": JOB_STATUS_PENDING,
            "progress": 0,
            "message": "Job initialized",
            "input_file": input_path,
            "output_file": None,
            "reference_genome": reference_genome,
            "regions": regions,
            "created_at": time.time(),
            "updated_at": time.time()
        }

        # Determine if it's a BAM/CRAM or VCF file
        file_ext = os.path.splitext(filename)[1].lower()

        if file_ext in ['.vcf', '.vcf.gz']:
            # If it's already a VCF, just return the path
            logger.info(f"Job {job_id}: File is already a VCF, returning directly")
            update_job_status(job_id, JOB_STATUS_COMPLETED, progress=100, 
                             message="File already contains variants",
                             output_file=input_path)
            
            return jsonify({
                "job_id": job_id,
                "status": JOB_STATUS_COMPLETED,
                "progress": 100,
                "message": "File already contains variants",
                "output_file": input_path
            }), 200

        elif file_ext in ['.bam', '.cram', '.sam']:
            logger.info(f"Job {job_id}: Starting processing for {file_ext} file")
            
            # For BAM files, create an index first
            if file_ext == '.bam':
                # Update status to indexing first
                update_job_status(job_id, JOB_STATUS_PENDING, progress=5, 
                                 message="Starting BAM file indexing")
                
                # Start the processing in a background thread
                threading.Thread(
                    target=process_bam_file,
                    args=(job_id, input_path, output_path, reference_path, regions),
                    daemon=True
                ).start()
            else:
                # For other formats, start variant calling directly
                update_job_status(job_id, JOB_STATUS_PENDING, progress=5, 
                                 message=f"Starting variant calling for {file_ext} file")
                
                # Start variant calling in a background thread
                threading.Thread(
                    target=run_variant_calling,
                    args=(job_id, input_path, output_path, reference_path, regions),
                    daemon=True
                ).start()

            return jsonify({
                "job_id": job_id,
                "status": JOB_STATUS_PENDING,
                "progress": 5,
                "message": f"Processing started for {file_ext} file"
            }), 202

        else:
            logger.error(f"Job {job_id}: Unsupported file format: {file_ext}")
            return jsonify({"error": f"Unsupported file format: {file_ext}"}), 400

    except Exception as e:
        logger.exception(f"Unexpected error in variant-call endpoint: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/test-job', methods=['GET', 'POST'])
def test_job():
    """Create a test job to verify the API is working correctly"""
    logger.info("Test job endpoint called - creating a simulated job")
    # Generate a job ID
    job_id = request.args.get('job_id', str(uuid.uuid4()))
    
    # Create a test job
    jobs[job_id] = {
        "status": JOB_STATUS_PENDING,
        "progress": 0,
        "message": "Test job created",
        "created_at": time.time(),
        "updated_at": time.time(),
        "is_test": True
    }
    
    # Start a test thread
    threading.Thread(
        target=lambda: simulate_test_job(job_id),
        daemon=True
    ).start()
    
    return jsonify({
        "job_id": job_id,
        "status": JOB_STATUS_PENDING,
        "message": "Test job started",
        "is_test": True
    }), 202

def simulate_test_job(job_id):
    """Simulate a test job for debugging without real processing"""
    try:
        logger.info(f"Starting test job simulation for job {job_id}")
        # Update progress in steps
        for progress in [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]:
            if job_id not in jobs:
                logger.warning(f"Test job {job_id} no longer exists, stopping simulation")
                return
                
            status = JOB_STATUS_RUNNING if progress < 100 else JOB_STATUS_COMPLETED
            message = f"Test job at {progress}% progress" if progress < 100 else "Test job completed"
            
            update_job_status(job_id, status, progress=progress, message=message)
            time.sleep(2)  # Wait 2 seconds between updates
            
        logger.info(f"Test job {job_id} simulation completed")
    
    except Exception as e:
        logger.exception(f"Error in test job simulation for job {job_id}: {str(e)}")
        if job_id in jobs:
            update_job_status(job_id, JOB_STATUS_ERROR, progress=100, 
                            message="Test job failed", error=str(e))

def process_bam_file(job_id, input_path, output_path, reference_path, regions):
    """Process a BAM file: first index it, then call variants"""
    try:
        # First, index the BAM file
        success, message = index_bam_file(job_id, input_path)
        
        if not success:
            logger.error(f"Job {job_id}: BAM indexing failed: {message}")
            update_job_status(job_id, JOB_STATUS_ERROR, progress=100, 
                             message=f"BAM indexing failed: {message}",
                             error=message)
            return
            
        # If indexing succeeded, continue with variant calling
        logger.info(f"Job {job_id}: BAM indexing completed, proceeding to variant calling")
        run_variant_calling(job_id, input_path, output_path, reference_path, regions)
        
    except Exception as e:
        logger.exception(f"Job {job_id}: Error in BAM file processing: {str(e)}")
        update_job_status(job_id, JOB_STATUS_ERROR, progress=100, 
                         message=f"Error in BAM file processing",
                         error=str(e))

@app.route('/job/<job_id>', methods=['GET'])
def job_status(job_id):
    """Get the status of a variant calling job with enhanced details"""
    if job_id not in jobs:
        logger.warning(f"Job status request for unknown job: {job_id}")
        return jsonify({"error": "Job not found"}), 404

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
    return jsonify(response), 200

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

@app.route('/diagnostic', methods=['GET'])
def diagnostic():
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
        
        return jsonify({
            "timestamp": time.time(),
            "status": "running",
            "uptime": time.time() - app.config.get("START_TIME", time.time()),
            "system": system_info,
            "gatk": gatk_info,
            "reference_genomes": reference_info,
            "jobs": jobs_info,
            "routes": [{"endpoint": rule.endpoint, "methods": list(rule.methods), "path": str(rule)} 
                      for rule in app.url_map.iter_rules()]
        }), 200
    except Exception as e:
        logger.exception(f"Error in diagnostic endpoint: {str(e)}")
        return jsonify({
            "status": "error",
            "timestamp": time.time(),
            "error": str(e),
            "traceback": traceback.format_exc() if 'traceback' in sys.modules else "traceback not available"
        }), 500

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

    # Start the Flask server in development mode for better error messages
    app.run(host='0.0.0.0', port=5000, debug=True) 