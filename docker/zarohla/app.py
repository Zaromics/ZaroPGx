import os
import asyncio
import logging
import time
import uuid
import csv
import psutil
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, Any, Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from pydantic import BaseModel
import sys

sys.path.append('/job-client')
from job_client import JobClient

# Read (and created) before logging is configured because the progress-log handler
# below writes into DATA_DIR. Same ordering as gatk_api.py.
DATA_DIR = Path(os.getenv('DATA_DIR', '/data'))
TEMP_DIR = DATA_DIR / 'temp'
os.makedirs(TEMP_DIR, exist_ok=True)

# --------------------------------------------------------------------------
# Bounded logging (BACKLOG 252). Same block, same values, same failure
# behaviour in every ZaroPGx sidecar: docker/gatk-api/gatk_api.py,
# docker/nextflow/runner.py, docker/pharmcat/pharmcat.py and
# docker/pypgx/pypgx_wrapper.py. tests/test_log_rotation_252.py pins all five
# against one rule, so an edit here that is not made there fails the suite.
#
# It is duplicated rather than imported: these are five separate images, and
# the logging block runs before sys.path is extended with the shared
# /job-client directory. See gatk_api.py for the full argument.
#
# This service had no file handler at all and no format string, so HLA typing
# left nothing on the shared volume for the app to show and nothing on disk to
# read after the container was replaced. /data is the volume shared with the
# main app, so the progress log has to be size bounded from the start - an
# unrotated handler here grows until the shared volume fills. 10 MiB x 5
# backups caps each destination at 60 MiB.
#
# THE ONE THING THIS SERVICE DOES DIFFERENTLY, and why: the filename carries the
# pid. zarohla is the only one of the five that runs more than one process --
# docker/zarohla/Dockerfile:50 is `gunicorn --workers 2` with no `--preload`, so
# each worker imports this module and builds its own handler. RotatingFileHandler
# is not multi-process safe: when worker A rolls over it renames the file out from
# under worker B, which goes on appending to the renamed inode. B's lines then
# migrate down the .1/.2/... chain and are *deleted* once they fall past
# backupCount -- silently, which is the worst possible failure for a diagnostic
# log -- while the 60 MiB ceiling stops holding in the meantime. One file per
# worker is what makes the bound this block advertises actually true.
#
# The cost is file count rather than file size: each is still capped at 60 MiB,
# and gunicorn only mints a new pid when it respawns a dead worker, so a healthy
# container holds two. If that ever becomes a nuisance, the better fix is
# `--workers 1` in the Dockerfile -- which this service arguably wants anyway,
# since `running_processes` below is per-process state that a second worker
# cannot see (a cancel request routed to the wrong worker already finds nothing).
PROGRESS_LOG_PATH = os.getenv(
    'ZAROHLA_PROGRESS_LOG', str(DATA_DIR / f'zarohla_progress.{os.getpid()}.log')
)
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
# Progress log accessible to main app
_progress_handler = _bounded_file_handler(PROGRESS_LOG_PATH)
if _progress_handler is not None:
    _log_handlers.append(_progress_handler)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=_log_handlers
)
logger = logging.getLogger("zarohla")
_warn_about_unopened_logs(logger)

app = FastAPI(title="ZaroHLA API", version="1.0.0")

class CancelRequest(BaseModel):
    job_id: str
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
    job_id = request.job_id
    patient_id = request.patient_id
    
    logger.info(f"Cancelling job {job_id} for patient {patient_id}")
    
    if job_id in running_processes:
        process_info = running_processes[job_id]
        pid = process_info.get("pid")
        
        if pid and psutil.pid_exists(pid):
            try:
                process = psutil.Process(pid)
                for child in process.children(recursive=True):
                    child.kill()
                process.kill()
                logger.info(f"Terminated process {pid} for job {job_id}")
            except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
                logger.warning(f"Could not terminate process {pid}: {e}")
                
        del running_processes[job_id]
        
    return {"status": "success", "message": f"Cancellation processed for {job_id}"}


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
    job_id: Optional[str] = Form(None),
    step_name: Optional[str] = Form("zarohla")
) -> Dict[str, Any]:
    
    job_client = None
    if job_id:
        try:
            job_client = JobClient(job_id=job_id, step_name=step_name)
            if await job_client.is_job_cancelled():
                logger.info(f"Workflow {job_id} is cancelled, aborting ZaroHLA processing")
                return {"success": False, "error": "Workflow has been cancelled"}
                
            await job_client.start_step("Starting HLA typing")
        except Exception as e:
            logger.warning(f"Failed to initialize JobClient: {e}")
            
    local_job_id = str(uuid.uuid4())
    job_dir = TEMP_DIR / local_job_id
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
                if job_client:
                    await job_client.log_progress(f"Converting BAM to FASTQ using samtools")
                
                f1_path = job_dir / "read1.fq"
                f2_path = job_dir / "read2.fq"
                cmd = ["samtools", "fastq", "-1", str(f1_path), "-2", str(f2_path), "-0", "/dev/null", "-s", "/dev/null", str(input_path)]
                
                logger.info(f"Running samtools: {' '.join(cmd)}")
                process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                if job_id:
                    running_processes[job_id] = {"pid": process.pid, "job_dir": str(job_dir)}
                    
                stdout, stderr = await process.communicate()
                
                if job_id and job_id not in running_processes:
                    raise Exception("Process cancelled by user")
                    
                if process.returncode != 0:
                    raise Exception(f"samtools failed: {stderr.decode()}")
            else:
                f1_path = input_path
        else:
            raise HTTPException(status_code=400, detail="Must provide either 'file' or 'file1' and 'file2'")
            
        if job_client:
            await job_client.log_progress(f"Running OptiType on {f1_path.name}")
            
        cmd = ["optitype", "run", "-i", str(f1_path)]
        if f2_path and os.path.exists(f2_path) and os.path.getsize(f2_path) > 0:
            # OptiType v1.5 CLI takes each paired-end file as its own -i (not a bare positional)
            cmd.extend(["-i", str(f2_path)])
            
        cmd.extend([f"--{seq_type}", "--mapper", mapper, "-o", str(outdir)])
        
        logger.info(f"Running command: {' '.join(cmd)}")
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        if job_id:
            running_processes[job_id] = {"pid": process.pid, "job_dir": str(job_dir)}
            
        stdout, stderr = await process.communicate()
        
        if job_id and job_id not in running_processes:
            raise Exception("Process cancelled by user")
            
        if job_id in running_processes:
            del running_processes[job_id]
            
        if process.returncode != 0:
            logger.error(f"OptiType failed: {stderr.decode()}")
            if job_client:
                await job_client.fail_step("OptiType execution failed", {"error": stderr.decode()})
            raise HTTPException(status_code=500, detail=f"OptiType failed: {stderr.decode()}")
            
        if job_client:
            await job_client.log_progress("Parsing OptiType results")
            
        results = {}
        # OptiType v1.5 writes into a timestamped subdir (outdir/<ts>/<ts>_result.tsv)
        result_files = list(outdir.rglob("*_result.tsv"))
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
        
        if job_client:
            await job_client.complete_step("HLA typing completed successfully", {"results": results})
            
        return {"status": "success", "results": results}
        
    except Exception as e:
        logger.error(f"Error in HLA typing: {str(e)}")
        if job_client:
            await job_client.fail_step("HLA typing failed", {"error": str(e)})
            
        if job_id and job_id in running_processes:
            del running_processes[job_id]
            
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            import shutil
            shutil.rmtree(job_dir, ignore_errors=True)
        except Exception:
            pass
