import os
import asyncio
import logging
import time
import uuid
import csv
import psutil
from pathlib import Path
from typing import Dict, Any, Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from pydantic import BaseModel
import sys

sys.path.append('/workflow-client')
from workflow_client import WorkflowClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("zarohla")

app = FastAPI(title="ZaroHLA API", version="1.0.0")

DATA_DIR = Path(os.getenv('DATA_DIR', '/data'))
TEMP_DIR = DATA_DIR / 'temp'
os.makedirs(TEMP_DIR, exist_ok=True)

class CancelRequest(BaseModel):
    workflow_id: str
    patient_id: str
    action: str

running_processes: Dict[str, Dict[str, Any]] = {}

@app.get("/health")
def health():
    return {
        "status": "healthy",
        "service": "zarohla"
    }

@app.post("/cancel")
async def cancel_workflow_job(request: CancelRequest):
    workflow_id = request.workflow_id
    patient_id = request.patient_id
    
    logger.info(f"Cancelling workflow {workflow_id} for patient {patient_id}")
    
    if workflow_id in running_processes:
        process_info = running_processes[workflow_id]
        pid = process_info.get("pid")
        
        if pid and psutil.pid_exists(pid):
            try:
                process = psutil.Process(pid)
                for child in process.children(recursive=True):
                    child.kill()
                process.kill()
                logger.info(f"Terminated process {pid} for workflow {workflow_id}")
            except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
                logger.warning(f"Could not terminate process {pid}: {e}")
                
        del running_processes[workflow_id]
        
    return {"status": "success", "message": f"Cancellation processed for {workflow_id}"}


@app.post("/call-hla")
async def call_hla(
    file: Optional[UploadFile] = File(None),
    file1: Optional[UploadFile] = File(None),
    file2: Optional[UploadFile] = File(None),
    seq_type: str = Form("dna"),
    mapper: str = Form("yara"),
    reference_genome: Optional[str] = Form("GRCh38"),
    patient_id: Optional[str] = Form("unknown"),
    report_id: Optional[str] = Form("unknown"),
    workflow_id: Optional[str] = Form(None),
    step_name: Optional[str] = Form("hlatyping")
) -> Dict[str, Any]:
    
    workflow_client = None
    if workflow_id:
        try:
            workflow_client = WorkflowClient(workflow_id, step_name)
            if await workflow_client.is_workflow_cancelled():
                logger.info(f"Workflow {workflow_id} is cancelled, aborting hlatyping processing")
                return {"success": False, "error": "Workflow has been cancelled"}
                
            await workflow_client.start_step("Starting HLA typing")
        except Exception as e:
            logger.warning(f"Failed to initialize WorkflowClient: {e}")
            
    job_id = str(uuid.uuid4())
    job_dir = TEMP_DIR / job_id
    os.makedirs(job_dir, exist_ok=True)
    outdir = job_dir / "results"
    os.makedirs(outdir, exist_ok=True)

    try:
        f1_path = None
        f2_path = None
        
        if file1 and file2:
            f1_path = job_dir / file1.filename
            f2_path = job_dir / file2.filename
            with open(f1_path, "wb") as f: f.write(await file1.read())
            with open(f2_path, "wb") as f: f.write(await file2.read())
        elif file:
            input_path = job_dir / file.filename
            with open(input_path, "wb") as f: f.write(await file.read())
            
            if input_path.name.lower().endswith(".bam") or input_path.name.lower().endswith(".sam") or input_path.name.lower().endswith(".cram"):
                if workflow_client:
                    await workflow_client.log_progress(f"Converting BAM to FASTQ using samtools")
                
                f1_path = job_dir / "read1.fq"
                f2_path = job_dir / "read2.fq"
                cmd = ["samtools", "fastq", "-1", str(f1_path), "-2", str(f2_path), "-0", "/dev/null", "-s", "/dev/null", str(input_path)]
                
                logger.info(f"Running samtools: {' '.join(cmd)}")
                process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                if workflow_id:
                    running_processes[workflow_id] = {"pid": process.pid, "job_dir": str(job_dir)}
                    
                stdout, stderr = await process.communicate()
                
                if workflow_id and workflow_id not in running_processes:
                    raise Exception("Process cancelled by user")
                    
                if process.returncode != 0:
                    raise Exception(f"samtools failed: {stderr.decode()}")
            else:
                f1_path = input_path
        else:
            raise HTTPException(status_code=400, detail="Must provide either 'file' or 'file1' and 'file2'")
            
        if workflow_client:
            await workflow_client.log_progress(f"Running OptiType on {f1_path.name}")
            
        cmd = ["optitype", "run", "-i", str(f1_path)]
        if f2_path and os.path.exists(f2_path) and os.path.getsize(f2_path) > 0:
            cmd.append(str(f2_path))
            
        cmd.extend([f"--{seq_type}", "--mapper", mapper, "-o", str(outdir)])
        
        logger.info(f"Running command: {' '.join(cmd)}")
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        if workflow_id:
            running_processes[workflow_id] = {"pid": process.pid, "job_dir": str(job_dir)}
            
        stdout, stderr = await process.communicate()
        
        if workflow_id and workflow_id not in running_processes:
            raise Exception("Process cancelled by user")
            
        if workflow_id in running_processes:
            del running_processes[workflow_id]
            
        if process.returncode != 0:
            logger.error(f"OptiType failed: {stderr.decode()}")
            if workflow_client:
                await workflow_client.fail_step("OptiType execution failed", {"error": stderr.decode()})
            raise HTTPException(status_code=500, detail=f"OptiType failed: {stderr.decode()}")
            
        if workflow_client:
            await workflow_client.log_progress("Parsing OptiType results")
            
        results = {}
        result_files = list(outdir.glob("*_result.tsv"))
        if not result_files:
            raise Exception("OptiType did not produce a _result.tsv file")
            
        tsv_file = result_files[0]
        with open(tsv_file, 'r') as f:
            reader = csv.DictReader(f, delimiter='\t')
            for row in reader:
                results["HLA-A"] = f"{row.get('A1', '')},{row.get('A2', '')}".strip(',')
                results["HLA-B"] = f"{row.get('B1', '')},{row.get('B2', '')}".strip(',')
                results["HLA-C"] = f"{row.get('C1', '')},{row.get('C2', '')}".strip(',')
                break 
                
        results = {k: v for k, v in results.items() if v}
        
        if workflow_client:
            await workflow_client.complete_step("HLA typing completed successfully", {"results": results})
            
        return {"status": "success", "results": results}
        
    except Exception as e:
        logger.error(f"Error in HLA typing: {str(e)}")
        if workflow_client:
            await workflow_client.fail_step("HLA typing failed", {"error": str(e)})
            
        if workflow_id and workflow_id in running_processes:
            del running_processes[workflow_id]
            
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            import shutil
            shutil.rmtree(job_dir, ignore_errors=True)
        except Exception:
            pass
