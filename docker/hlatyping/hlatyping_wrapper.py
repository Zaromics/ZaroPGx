#!/usr/bin/env python3
import csv
import os
import subprocess
import uuid
import time
from pathlib import Path
from typing import Optional, Dict, Any

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import logging
import sys
import os
import psutil
import shutil
import glob

# Import shared workflow client for integration
import sys
sys.path.append('/workflow-client')
from workflow_client import WorkflowClient, create_workflow_client  # pyright: ignore[reportMissingImports]

DATA_DIR = Path(os.getenv('DATA_DIR', '/data'))
TEMP_DIR = DATA_DIR / 'temp'
RESULTS_DIR = DATA_DIR / 'results'
os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(RESULTS_DIR, exist_ok=True)

NEXTFLOW_RUN_VERSION = os.getenv('HLATYPING_PIPELINE_VERSION', '2.1.0')
NEXTFLOW_PROFILE = os.getenv('HLATYPING_PROFILE', 'docker')

# Store running processes by workflow_id for cancellation
running_processes: Dict[str, Dict[str, Any]] = {}

class CancelRequest(BaseModel):
    workflow_id: str
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

def unregister_process(workflow_id: str):
    """Unregister a process when it completes normally."""
    if workflow_id in running_processes:
        del running_processes[workflow_id]
        logger.info(f"Unregistered process for workflow {workflow_id}")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),  # Console output
        logging.FileHandler('/data/hlatyping_progress.log')  # Progress log accessible to main app
    ]
)
logger = logging.getLogger("hlatyping")

app = FastAPI(title="nf-core/hlatyping Wrapper API", version="0.2.2")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "healthy",
        "service": "hlatyping-wrapper",
        "timestamp": time.time(),
    }


@app.post("/type")
async def hla_type(
    file1: Optional[UploadFile] = File(None),
    file2: Optional[UploadFile] = File(None),
    bam: Optional[UploadFile] = File(None),
    seq_type: str = Form("dna"),  # "dna" or "rna"
    sample_name: Optional[str] = Form(None),
    out_prefix: Optional[str] = Form(None),
    reference_genome: str = Form("hg38"),
    workflow_id: Optional[str] = Form(None),
    step_name: Optional[str] = Form("hlatyping"),
) -> Dict[str, Any]:
    if seq_type not in ("dna", "rna"):
        raise HTTPException(status_code=400, detail="seq_type must be 'dna' or 'rna'")

    if not any([file1, bam]):
        raise HTTPException(status_code=400, detail="Provide either FASTQ (file1[/file2]) or a BAM file")

    # Initialize WorkflowClient if workflow_id is provided
    workflow_client = None
    if workflow_id:
        try:
            workflow_client = WorkflowClient(workflow_id, step_name)
            
            # Check if workflow has been cancelled before starting
            if await workflow_client.is_workflow_cancelled():
                logger.info(f"Workflow {workflow_id} is cancelled, aborting hlatyping processing")
                return {"success": False, "error": "Workflow has been cancelled"}
            
            await workflow_client.start_step()
        except Exception as e:
            logger.warning(f"Failed to initialize WorkflowClient: {e}")

    job_id = str(uuid.uuid4())
    job_dir = TEMP_DIR / job_id
    os.makedirs(job_dir, exist_ok=True)

    try:
        saved_f1 = None
        saved_f2 = None
        saved_bam = None

        # Log file upload progress
        if workflow_client:
            await workflow_client.log_progress("Uploading input files", {
                "file1": file1.filename if file1 else None,
                "file2": file2.filename if file2 else None,
                "bam": bam.filename if bam else None,
                "seq_type": seq_type,
                "reference_genome": reference_genome
            })

        # Save uploads
        if file1 is not None:
            saved_f1 = job_dir / file1.filename
            with open(saved_f1, "wb") as f:
                f.write(await file1.read())
        if file2 is not None:
            saved_f2 = job_dir / file2.filename
            with open(saved_f2, "wb") as f:
                f.write(await file2.read())
        if bam is not None:
            saved_bam = job_dir / bam.filename
            with open(saved_bam, "wb") as f:
                f.write(await bam.read())

        run_out_dir = job_dir / "hlatyping_out"
        os.makedirs(run_out_dir, exist_ok=True)

        # Build samplesheet.csv per nf-core/hlatyping docs
        samplesheet_path = job_dir / "samplesheet.csv"
        effective_sample = sample_name or (out_prefix or "sample")

        # Columns: sample,fastq_1,fastq_2,seq_type, optionally bam
        # If BAM is provided, fastq columns must still exist (may be empty)
        with open(samplesheet_path, "w", newline="") as csvfile:
            fieldnames = ["sample", "fastq_1", "fastq_2", "seq_type"]
            include_bam = saved_bam is not None
            if include_bam:
                fieldnames.append("bam")
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            row: Dict[str, Any] = {
                "sample": effective_sample,
                "fastq_1": str(saved_f1) if saved_f1 else "",
                "fastq_2": str(saved_f2) if saved_f2 else "",
                "seq_type": seq_type,
            }
            if include_bam:
                row["bam"] = str(saved_bam)
            writer.writerow(row)

        # Prepare Nextflow command
        # Docs suggest: nextflow run nf-core/hlatyping -profile docker --input samplesheet.csv --outdir <OUTDIR> --genome <GENOME>
        # Map reference genome to nf-core/hlatyping genome parameter
        genome_mapping = {
            'hg38': 'GRCh38',
            'hg37': 'GRCh37', 
            'GRCh38': 'GRCh38',
            'GRCh37': 'GRCh37'
        }
        genome_param = genome_mapping.get(reference_genome, 'GRCh38')
        
        cmd = (
            f"nextflow run nf-core/hlatyping -r {NEXTFLOW_RUN_VERSION} "
            f"-profile {NEXTFLOW_PROFILE} "
            f"--input {samplesheet_path} "
            f"--outdir {run_out_dir} "
            f"--genome {genome_param} "
        )

        # Optional: add workflow reports
        cmd += (
            f"-with-report {run_out_dir}/execution_report.html "
            f"-with-timeline {run_out_dir}/execution_timeline.html "
            f"-with-trace {run_out_dir}/execution_trace.txt "
            f"-with-dag {run_out_dir}/pipeline_dag.svg "
        )

        env = os.environ.copy()
        # Ensure Nextflow caches under data to persist between runs (optional)
        env.setdefault('NXF_HOME', str(DATA_DIR / 'nextflow'))

        logger.info(f"Running hlatyping command: {cmd}")
        logger.info(f"Working directory: {job_dir}")
        logger.info(f"Output directory: {run_out_dir}")
        
        # Log execution start
        if workflow_client:
            await workflow_client.log_progress("Starting nf-core/hlatyping pipeline", {
                "command": cmd,
                "seq_type": seq_type,
                "reference_genome": reference_genome,
                "sample_name": effective_sample
            })
        
        # Register process for cancellation if workflow_id is provided
        if workflow_id:
            # Start process in background to get PID
            process = subprocess.Popen(
                cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                cwd=job_dir
            )
            # Track specific file paths for cleanup
            cleanup_paths = [
                str(job_dir),
                str(run_out_dir),
                str(saved_f1) if saved_f1 else None,
                str(saved_f2) if saved_f2 else None,
                str(saved_bam) if saved_bam else None
            ]
            # Filter out None values
            cleanup_paths = [path for path in cleanup_paths if path is not None]
            
            register_process(workflow_id, process.pid, {
                "job_dir": str(job_dir),
                "sample_name": sample_name,
                "cleanup_paths": cleanup_paths
            })
            
            # Wait for completion
            stdout, stderr = process.communicate()
            return_code = process.returncode
            
            # Unregister process when done
            unregister_process(workflow_id)
        else:
            # Original synchronous approach for non-workflow calls
            proc = subprocess.run(cmd, shell=True, text=True, capture_output=True, env=env, cwd=job_dir)
            stdout = proc.stdout
            stderr = proc.stderr
            return_code = proc.returncode
        logger.info(f"Command return code: {return_code}")
        logger.info(f"Command stdout: {stdout}")
        logger.info(f"Command stderr: {stderr}")
        
        # Check if output directory was created and has content
        if run_out_dir.exists():
            logger.info(f"Output directory contents: {list(run_out_dir.iterdir())}")
            for item in run_out_dir.rglob("*"):
                logger.info(f"  {item.relative_to(run_out_dir)}")
        else:
            logger.warning("Output directory was not created!")
        
        if return_code != 0:
            error_msg = f"hlatyping failed with return code {return_code}\n"
            error_msg += f"STDOUT: {stdout}\n"
            error_msg += f"STDERR: {stderr}"
            if workflow_client:
                await workflow_client.fail_step(f"hlatyping failed: {error_msg}", {
                    "return_code": return_code,
                    "stdout": stdout,
                    "stderr": stderr
                })
            raise HTTPException(status_code=500, detail=error_msg)

        # Log results processing
        if workflow_client:
            await workflow_client.log_progress("Processing hlatyping results", {
                "output_directory": str(run_out_dir)
            })

        # Move results to shared results dir
        final_dir = RESULTS_DIR / f"hlatyping_{job_id}"
        os.makedirs(final_dir, exist_ok=True)
        for child in run_out_dir.iterdir():
            dest = final_dir / child.name
            child.replace(dest)

        # Log completion
        if workflow_client:
            await workflow_client.complete_step("hlatyping completed successfully", {
                "job_id": job_id,
                "results_dir": str(final_dir),
                "seq_type": seq_type,
                "reference_genome": reference_genome,
                "sample_name": effective_sample
            })

        return {
            "success": True,
            "job_id": job_id,
            "results_dir": str(final_dir),
        }
    except HTTPException:
        if workflow_client:
            await workflow_client.fail_step("hlatyping processing failed", {
                "error_type": "http_exception"
            })
        raise
    except Exception as e:
        if workflow_client:
            await workflow_client.fail_step(f"Unexpected error: {str(e)}", {
                "error_type": "unexpected_error",
                "error_message": str(e)
            })
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/call-hla")
async def call_hla(
    file: UploadFile = File(...),
    file2: Optional[UploadFile] = File(None),  # Optional second FASTQ file for paired-end
    reference_genome: str = Form(...),
    patient_id: str = Form(...),
    report_id: str = Form(...),
    seq_type: str = Form("dna"),
    workflow_id: Optional[str] = Form(None),
    step_name: Optional[str] = Form("hlatyping_call"),
) -> Dict[str, Any]:
    """
    HLA calling endpoint for nextflow pipeline integration.
    This endpoint wraps the existing /type endpoint to provide the expected interface.
    Supports both single-end and paired-end FASTQ files, as well as BAM files.
    """
    # Initialize WorkflowClient if workflow_id is provided
    workflow_client = None
    if workflow_id:
        try:
            workflow_client = WorkflowClient(workflow_id, step_name)
            await workflow_client.start_step()
        except Exception as e:
            logger.warning(f"Failed to initialize WorkflowClient: {e}")

    try:
        # Determine file type based on filename
        is_bam = file.filename.lower().endswith('.bam')
        is_fastq = any(file.filename.lower().endswith(ext) for ext in ['.fastq', '.fq', '.fastq.gz', '.fq.gz'])
        
        if not (is_bam or is_fastq):
            raise HTTPException(status_code=400, detail="File must be BAM or FASTQ format")
        
        # Log HLA calling start
        if workflow_client:
            await workflow_client.log_progress("Starting HLA calling", {
                "file_type": "BAM" if is_bam else "FASTQ",
                "patient_id": patient_id,
                "report_id": report_id,
                "seq_type": seq_type,
                "reference_genome": reference_genome
            })

        # Call the existing /type endpoint internally
        if is_bam:
            # For BAM files, call /type with bam parameter
            result = await hla_type(
                file1=None,
                file2=None, 
                bam=file,
                seq_type=seq_type,
                sample_name=patient_id,
                out_prefix=patient_id,
                reference_genome=reference_genome,
                workflow_id=workflow_id,
                step_name=step_name
            )
        else:
            # For FASTQ files, call /type with file1 (and optionally file2 for paired-end)
            result = await hla_type(
                file1=file,
                file2=file2,  # Will be None for single-end, provided for paired-end
                bam=None,
                seq_type=seq_type,
                sample_name=patient_id,
                out_prefix=patient_id,
                reference_genome=reference_genome,
                workflow_id=workflow_id,
                step_name=step_name
            )
        
        if not result.get("success"):
            raise HTTPException(status_code=500, detail="HLA typing failed")
        
        # Parse results from the hlatyping output
        results_dir = Path(result["results_dir"])
        
        # Look for HLA results in the expected format
        # Based on nf-core/hlatyping docs, results are typically in:
        # - results_dir/hlatyping/sample_name/hlatyping/sample_name.hla_calls.tsv
        # - or similar structure
        
        hla_results = {}
        
        # First, try to find any HLA-related files
        logger.info(f"Searching for HLA results in: {results_dir}")
        all_files = list(results_dir.rglob("*"))
        logger.info(f"All files found: {[str(f.relative_to(results_dir)) for f in all_files]}")
        
        # Look for various possible HLA output files
        hla_files = []
        for pattern in ["*.hla_calls.tsv", "*hla*.tsv", "*HLA*.tsv", "*.tsv"]:
            hla_files.extend(list(results_dir.rglob(pattern)))
        
        logger.info(f"HLA files found: {[str(f.relative_to(results_dir)) for f in hla_files]}")
        
        if hla_files:
            # Parse the HLA calls TSV file
            hla_file = hla_files[0]
            logger.info(f"Parsing HLA file: {hla_file}")
            with open(hla_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        parts = line.split('\t')
                        if len(parts) >= 2:
                            gene = parts[0]
                            call = parts[1]
                            hla_results[gene] = call
                            logger.info(f"Found HLA call: {gene} -> {call}")
        else:
            logger.warning("No HLA files found - this may indicate the pipeline failed to produce results")
        
        # Log completion
        if workflow_client:
            await workflow_client.complete_step("HLA calling completed successfully", {
                "patient_id": patient_id,
                "report_id": report_id,
                "hla_results": hla_results,
                "job_id": result.get("job_id"),
                "results_dir": str(results_dir)
            })

        # Return in the format expected by nextflow pipeline
        return {
            "success": True,
            "results": hla_results,
            "job_id": result.get("job_id"),
            "results_dir": str(results_dir)
        }
        
    except Exception as e:
        if workflow_client:
            await workflow_client.fail_step(f"HLA calling failed: {str(e)}", {
                "error_type": "hla_calling_error",
                "error_message": str(e),
                "patient_id": patient_id,
                "report_id": report_id
            })
        raise HTTPException(status_code=500, detail=f"HLA calling failed: {str(e)}")

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
        
        # Check our stored process registry
        if workflow_id in running_processes:
            process_info = running_processes[workflow_id]
            pid = process_info.get("pid")
            
            if pid and psutil.pid_exists(pid):
                try:
                    process = psutil.Process(pid)
                    process.terminate()
                    logger.info(f"Terminated process {pid} for workflow {workflow_id}")
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
            del running_processes[workflow_id]
        else:
            logger.warning(f"No running process found for workflow {workflow_id}")
        
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
