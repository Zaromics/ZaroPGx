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

# Uploads are streamed to disk in chunks of this size rather than read into memory --
# see Dockerfile's note on this service's single-worker choice: a whole-genome BAM
# ingested via a bare `await file.read()` blocks the one event loop for as long as
# the read takes, taking /health and the broadcast /cancel down with it. Same value
# as the gatk-api sidecar.
UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024

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
# This block briefly carried the pid in the filename, because zarohla was the
# only one of the five running `gunicorn --workers 2` and RotatingFileHandler is
# not multi-process safe: when worker A rolls over it renames the file out from
# under worker B, which goes on appending to the renamed inode, and B's lines
# migrate down the .1/.2/... chain until they are silently deleted past
# backupCount. The Dockerfile now starts one worker (see the comment on its CMD:
# the `running_processes` registry below is per-process state, so a second worker
# broke /cancel about half the time), which removes the hazard at the source and
# lets this go back to the same single shared path as the other four.
PROGRESS_LOG_PATH = os.getenv(
    'ZAROHLA_PROGRESS_LOG', str(DATA_DIR / 'zarohla_progress.log')
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
logger = logging.getLogger("zarohla")
_warn_about_unopened_logs(logger)

# --------------------------------------------------------------------------
# Upload filename sanitising
# --------------------------------------------------------------------------
# `file.filename` arrives in the multipart body and nothing upstream of this
# service constrains it. The Nextflow HLA processes post `-F file=@<staged
# name>` (pipelines/pgx/main.nf), and that staged name derives from the
# patient's own upload, so joining it onto the job directory made it a
# path-write primitive: `../` walks out of TEMP_DIR, and `*`, `?`, `[...]` and
# `{...}` survive into paths that later reach glob-expanding code -- an upload
# filename doing exactly that in the Nextflow lane produced a verified
# cross-patient disclosure. This service runs no `shell=True`, so it was never
# command injection here; the write primitive is reason enough on its own.
#
# DOCUMENTED DUPLICATE of docker/pypgx/pypgx_wrapper.py's ALLOWED_UPLOAD_SUFFIXES
# and safe_upload_name(): same tuple in the same order, same fallback
# semantics, same uuid4-hex stem. It is copied rather than imported because
# these are separate images and this module is imported before sys.path is
# extended with the shared /job-client directory -- the same argument the
# bounded-logging block above makes. The copy is held honest by
# tests/test_command_injection_hardening.py, which execs both implementations
# out of their sources and asserts they choose the same suffix for the same
# name; an undocumented divergence is what that test exists to prevent.
#
# Longest suffixes first: `.vcf.gz` must win over `.vcf`, and `.fastq.gz` over
# `.fastq` -- OptiType is a FASTQ consumer, so the gzipped FASTQ suffixes are
# the ones that matter most here.
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
    `default_suffix`, which the caller picks from the endpoint's contract --
    `.fastq` here, because a file this endpoint does not recognise as an
    alignment is handed to OptiType as reads.

    Nothing downstream correlates on the original name: /call-hla returns only
    {"status", "results"}, the results are read from OptiType's own timestamped
    `*_result.tsv` rather than from anything named after the input, the job
    directory is removed in the `finally` block, and the one place the stored
    name is still consulted -- the BAM/SAM/CRAM branch below -- only classifies
    the suffix, which this function preserves.

    The per-call uuid also removes a collision this endpoint could already hit:
    `file1` and `file2` land in the same job directory, so two uploads sharing
    a filename used to overwrite each other and hand OptiType the same file
    twice.
    """
    suffix = default_suffix
    # Strip any directory component under both separators before matching, so a
    # name like `..\evil/x.bam` cannot smuggle one through.
    name = (original or "").strip().replace("\\", "/").rsplit("/", 1)[-1].lower()
    for candidate in ALLOWED_UPLOAD_SUFFIXES:
        if name.endswith(candidate):
            suffix = candidate
            break
    return f"{uuid.uuid4().hex}{suffix}"


app = FastAPI(title="ZaroHLA API", version="1.0.0")

class CancelRequest(BaseModel):
    job_id: str
    patient_id: str
    action: str

# Per-process, and that is only correct because docker/zarohla/Dockerfile starts
# `gunicorn --workers 1`. /cancel below can only kill a pid it finds in here, so
# under two workers a cancel accepted by the worker that did not start the job
# returned {"status": "success"} while OptiType went on running. If this service
# ever needs more than one worker, this dict has to become genuinely shared state
# first -- a module global is not that, and neither is `--preload`, which forks
# after import and then lets each worker mutate its own copy.
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
            # Never `job_dir / file1.filename` -- see safe_upload_name() above.
            f1_path = job_dir / safe_upload_name(file1.filename, ".fastq")
            f2_path = job_dir / safe_upload_name(file2.filename, ".fastq")
            logger.info(
                f"Job {local_job_id}: storing paired uploads "
                f"{file1.filename!r}/{file2.filename!r} as "
                f"{f1_path.name!r}/{f2_path.name!r}"
            )
            with open(f1_path, "wb") as f:
                while chunk := await file1.read(UPLOAD_CHUNK_BYTES):
                    f.write(chunk)
            with open(f2_path, "wb") as f:
                while chunk := await file2.read(UPLOAD_CHUNK_BYTES):
                    f.write(chunk)
        elif file:
            input_path = job_dir / safe_upload_name(file.filename, ".fastq")
            logger.info(
                f"Job {local_job_id}: storing upload {file.filename!r} as "
                f"{input_path.name!r}"
            )
            with open(input_path, "wb") as f:
                while chunk := await file.read(UPLOAD_CHUNK_BYTES):
                    f.write(chunk)
            
            if input_path.name.lower().endswith(".bam") or input_path.name.lower().endswith(".sam") or input_path.name.lower().endswith(".cram"):
                if job_client:
                    await job_client.log_progress(f"Converting BAM to FASTQ using samtools")

                f1_path = job_dir / "read1.fq"
                f2_path = job_dir / "read2.fq"
                # `samtools fastq -1/-2` only routes properly-mate-adjacent reads to the
                # paired outputs; a real BAM is coordinate-sorted with mates far apart,
                # so it must be name-collated first or most pairs fall through to the
                # discarded singleton stream and OptiType gets few/no reads. Collate to
                # a temp BAM first (no shell: this sidecar is command-injection hardened).
                collated_path = job_dir / "collated.bam"
                collate_cmd = ["samtools", "collate", "-u", "-o", str(collated_path), str(input_path)]
                logger.info(f"Running samtools: {' '.join(collate_cmd)}")
                collate_proc = await asyncio.create_subprocess_exec(*collate_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                if job_id:
                    running_processes[job_id] = {"pid": collate_proc.pid, "job_dir": str(job_dir)}
                _, collate_err = await collate_proc.communicate()
                if job_id and job_id not in running_processes:
                    raise Exception("Process cancelled by user")
                if collate_proc.returncode != 0:
                    raise Exception(f"samtools collate failed: {collate_err.decode()}")

                cmd = ["samtools", "fastq", "-1", str(f1_path), "-2", str(f2_path), "-0", "/dev/null", "-s", "/dev/null", str(collated_path)]

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
