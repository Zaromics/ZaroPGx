#!/usr/bin/env python3
import os
import subprocess
import uuid
import time
from pathlib import Path
from typing import Optional, Dict, Any

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import logging
import sys
import os

# Import shared workflow client for integration
import sys
sys.path.append('/workflow-client')
from workflow_client import WorkflowClient, create_workflow_client

DATA_DIR = Path(os.getenv('DATA_DIR', '/data'))
TEMP_DIR = DATA_DIR / 'temp'
RESULTS_DIR = DATA_DIR / 'results'
os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(RESULTS_DIR, exist_ok=True)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),  # Console output
        logging.FileHandler('/data/optitype_progress.log')  # Progress log accessible to main app
    ]
)
logger = logging.getLogger("optitype")

app = FastAPI(title="OptiType Wrapper API", version="1.0.0")
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
        "service": "optitype-wrapper",
        "timestamp": time.time(),
    }

@app.post("/type")
async def hlaclass1_type(
    file1: UploadFile = File(...),
    file2: Optional[UploadFile] = File(None),
    mode: str = Form("dna"),  # "dna" or "rna"
    out_prefix: Optional[str] = Form(None),
    workflow_id: Optional[str] = Form(None),
    step_name: Optional[str] = Form("optitype_typing"),
) -> Dict[str, Any]:
    if mode not in ("dna", "rna"):
        raise HTTPException(status_code=400, detail="mode must be 'dna' or 'rna'")

    # Initialize WorkflowClient if workflow_id is provided
    workflow_client = None
    if workflow_id:
        try:
            workflow_client = WorkflowClient(workflow_id, step_name)
            await workflow_client.start_step()
        except Exception as e:
            logger.warning(f"Failed to initialize WorkflowClient: {e}")

    job_id = str(uuid.uuid4())
    job_dir = TEMP_DIR / job_id
    os.makedirs(job_dir, exist_ok=True)

    try:
        # Log file upload progress
        if workflow_client:
            await workflow_client.log_progress("Uploading input files", {
                "file1": file1.filename,
                "file2": file2.filename if file2 else None,
                "mode": mode
            })

        # Save uploads
        f1_path = job_dir / file1.filename
        with open(f1_path, "wb") as f:
            f.write(await file1.read())
        f2_path = None
        if file2 is not None:
            f2_path = job_dir / file2.filename
            with open(f2_path, "wb") as f:
                f.write(await file2.read())

        # Prepare output dir
        run_out_dir = job_dir / "optitype_out"
        os.makedirs(run_out_dir, exist_ok=True)

        # Build command
        mode_flag = "--dna" if mode == "dna" else "--rna"
        inputs = f"{f1_path} {f2_path}" if f2_path else f"{f1_path}"
        cmd = (
            f"conda run -n optitype-py27 python /opt/OptiType/OptiTypePipeline.py "
            f"-i {inputs} {mode_flag} -o {run_out_dir}"
        )
        if out_prefix:
            cmd += f" -p {out_prefix}"

        logger.info(f"Running OptiType command: {cmd}")
        logger.info(f"Working directory: {job_dir}")
        logger.info(f"Output directory: {run_out_dir}")
        
        # Log execution start
        if workflow_client:
            await workflow_client.log_progress("Starting OptiType HLA typing", {
                "command": cmd,
                "mode": mode,
                "output_prefix": out_prefix
            })
        
        proc = subprocess.run(cmd, shell=True, text=True, capture_output=True)
        logger.info(f"Command return code: {proc.returncode}")
        logger.info(f"Command stdout: {proc.stdout}")
        logger.info(f"Command stderr: {proc.stderr}")
        
        if proc.returncode != 0:
            logger.error(f"OptiType failed with return code {proc.returncode}")
            if workflow_client:
                await workflow_client.fail_step(f"OptiType failed: {proc.stderr}", {
                    "return_code": proc.returncode,
                    "stderr": proc.stderr
                })
            raise HTTPException(status_code=500, detail=f"OptiType failed: {proc.stderr}")

        # Log results processing
        if workflow_client:
            await workflow_client.log_progress("Processing OptiType results", {
                "output_directory": str(run_out_dir)
            })

        # Move results to shared results dir
        final_dir = RESULTS_DIR / f"optitype_{job_id}"
        os.makedirs(final_dir, exist_ok=True)
        logger.info(f"Moving results from {run_out_dir} to {final_dir}")
        for child in run_out_dir.iterdir():
            dest = final_dir / child.name
            child.replace(dest)
            logger.info(f"Moved {child.name} to results directory")

        # Log completion
        if workflow_client:
            await workflow_client.complete_step("OptiType HLA typing completed successfully", {
                "job_id": job_id,
                "results_dir": str(final_dir),
                "mode": mode,
                "files_processed": [file1.filename] + ([file2.filename] if file2 else [])
            })

        return {
            "success": True,
            "job_id": job_id,
            "results_dir": str(final_dir),
        }
    except HTTPException:
        if workflow_client:
            await workflow_client.fail_step("OptiType processing failed", {
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


