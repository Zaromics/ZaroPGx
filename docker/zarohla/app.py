import os
import asyncio
import json
import logging
import re
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

sys.path.append("/job-client")
from job_client import JobClient

# Read (and created) before logging is configured because the progress-log handler
# below writes into DATA_DIR. Same ordering as gatk_api.py.
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
# Where the FASTQ conversion lands. Separable from DATA_DIR because it is by far
# the largest thing this service writes and the shortest-lived: converting a whole
# WGS alignment produces tens of gigabytes of FASTQ that exist only until OptiType
# has read them. On a host whose /data lives on a small disk that is the difference
# between a run and a full filesystem, so HLA_TEMP_DIR can point it at roomier
# storage (compose wires it to the ZAROPGX_SCRATCH bind mount). Defaults to the
# old location, so an unconfigured deployment behaves exactly as before.
TEMP_DIR = Path(os.getenv("HLA_TEMP_DIR") or (DATA_DIR / "temp"))


# Sidecar working directories live under the job they belong to,
# TEMP_DIR/zarohla/<job id>/<random>, and the app removes
# TEMP_DIR/zarohla/<job id> when the job ends (app/services/cleanup_service.py:
# cleanup_job_files). That covers what this service cannot remove itself: outputs
# Nextflow copies after the response, and whatever a killed worker left behind.
# They used to sit at TEMP_DIR/<random>, which nothing could tie to a job, so they
# stayed for good. A job id that is not a plain token never becomes a path part.
_JOB_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$")


def job_work_dir(job_id: Optional[str], local_job_id: str) -> Path:
    key = job_id if job_id and _JOB_KEY.match(job_id) else "no-job"
    return TEMP_DIR / "zarohla" / key / local_job_id
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
    "ZAROHLA_PROGRESS_LOG", str(DATA_DIR / "zarohla_progress.log")
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
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=_log_handlers,
)
logger = logging.getLogger("zarohla")
_warn_about_unopened_logs(logger)


def _publish_version_manifest() -> None:
    """Record OptiType's version where the report generator can find it.

    Every other sidecar publishes its own manifest into /data/versions -- see
    docker/pharmcat/start.sh and docker/pypgx/setup_pypgx.sh. zarohla published
    none, so nothing in the stack knew the version of the tool that actually does
    the HLA typing: the report's Software Platform table could only see the
    container wrapper ("Zaropgx Zarohla 0.3.0", the ZaroPGx release number, not
    OptiType's), and the citation fell back to a version hardcoded in
    generator.py that nothing verified against the image.

    Read from the installed distribution rather than a constant, so it cannot
    drift from the pin in this service's Dockerfile.

    data/versions/about.md asks for exactly this: a container's manifest naming
    the versions of its constituent packages.
    """
    try:
        from importlib.metadata import version as _distribution_version

        optitype_version = _distribution_version("optitype")
    except Exception as exc:  # pragma: no cover - depends on the image
        logger.warning(f"Could not read the OptiType version: {exc}")
        return

    try:
        versions_dir = DATA_DIR / "versions"
        versions_dir.mkdir(parents=True, exist_ok=True)
        (versions_dir / "optitype.json").write_text(
            json.dumps({"name": "OptiType", "version": optitype_version}),
            encoding="utf-8",
        )
        logger.info(f"Published OptiType version manifest: {optitype_version}")
    except Exception as exc:
        # Never fatal: a missing manifest costs the report a version string, not
        # a run.
        logger.warning(f"Could not publish the OptiType version manifest: {exc}")


_publish_version_manifest()

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
# The HLA class I genes OptiType types -- HLA-A, -B and -C -- as GRCh38 gene loci
# (Ensembl), each padded by 1 kb. The probe asks about THESE, not the whole 5 Mb MHC it
# used to: a panel FASTQ re-aligned against the no-ALT reference put two MAPQ-0 strays
# into the MHC and zero reads on any HLA gene, "reads in the MHC" waved it through, and
# OptiType crashed. Coverage of the MHC is not coverage of what OptiType reads.
#
# GRCh38 only, deliberately. The pipeline analyses hg38 whatever the upload was, and a
# GRCh37 entry would be coordinates this module has never checked; a build with no
# entry makes the probe answer "cannot tell", which falls back rather than guesses.
HLA_CLASS_I_LOCI = {
    "GRCh38": (
        (29940260, 29950572),  # HLA-A  chr6:29,941,260-29,949,572
        (31352872, 31368067),  # HLA-B  chr6:31,353,872-31,367,067
        (31267749, 31273130),  # HLA-C  chr6:31,268,749-31,272,130
    ),
}
# A read at MAPQ 0 aligned equally well somewhere else, so it says nothing about
# whether these genes were sequenced. Measured on a real single-end sample: MAPQ >= 1
# keeps 167 / 265 / 53 of its reads on A / B / C (97-100%), and drops both of the
# panel's strays.
PROBE_MIN_MAPQ = 1

_BUILD_ALIASES = {
    "hg38": "GRCh38",
    "grch38": "GRCh38",
    "hg19": "GRCh37",
    "grch37": "GRCh37",
    "b37": "GRCh37",
}

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


NO_HLA_READS_MESSAGE = (
    "No HLA reads in the input, so nothing to type. Normal for a targeted "
    "panel without the HLA region."
)


async def _no_hla_reads_result(job_client) -> Dict[str, Any]:
    """The one shape "this sample has no HLA" is allowed to take.

    Two checks now reach this: the HLA-A/-B/-C locus probe (before conversion) and the
    0-byte FASTQ check (after it). They answer the same question about different
    evidence, so they must not be allowed to answer it differently -- a caller
    that had to tell them apart would be back to guessing.
    """
    if job_client:
        # complete_step, not fail_step: the run continues with no HLA calls.
        # main.nf already handles an absent hla_calls.tsv through
        # `hla_ch.ifEmpty(empty_file_ch)`, so nothing downstream changes.
        await job_client.complete_step(
            NO_HLA_READS_MESSAGE,
            output_data={"results": {}, "reason": "no_hla_reads"},
        )
    return {"status": "success", "results": {}, "warning": NO_HLA_READS_MESSAGE}


async def _samtools(*args: str) -> tuple[int, bytes]:
    """Run samtools, returning (returncode, stdout). No shell, as everywhere here."""
    proc = await asyncio.create_subprocess_exec(
        "samtools",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    return proc.returncode, stdout


async def _probe_hla_read_count(
    input_path: Path, reference_genome: Optional[str]
) -> Optional[int]:
    """Confidently placed reads on HLA-A/-B/-C, or None when that cannot be established.

    It used to count reads anywhere in the 5 Mb MHC; see HLA_CLASS_I_LOCI for why that
    was the wrong question. The history below is otherwise unchanged.

    This exists because the 0-byte FASTQ check further down was reading emptiness
    off the wrong artefact. `samtools fastq` over a whole alignment writes every
    read in the file, so "no HLA reads" only shows up as an empty FASTQ when the
    alignment had no reads AT ALL. An input with reads but none in the MHC -- a
    targeted PGx panel without HLA capture, or a mitochondrial-only file -- yielded
    a perfectly non-empty FASTQ, walked past the guard, and died inside OptiType's
    pandas with the same "Length mismatch: Expected axis has 0 elements" the guard
    was added to prevent. Observed on a live run against a chrM-only CRAM.

    Counting the interval directly keeps the safety property the guard was built
    on: "no HLA reads" stays an OBSERVED FACT about the input, established before
    OptiType runs, never an inference from OptiType's exit code. That distinction
    is the whole argument -- see the block comment at the FASTQ check below, and
    tests/test_zarohla_no_hla_reads.py, which pins both directions.

    Returns None -- meaning "cannot tell" -- for anything unprobeable: a SAM (not
    indexable without sorting it first), an unindexable or unsorted alignment, a
    header with no recognisable chromosome 6. A None must never be read as zero;
    the caller falls back to converting the whole file exactly as before, because
    guessing "no HLA" from a failed probe would be precisely the inference this
    module refuses to make.
    """
    suffix = input_path.name.lower()
    if not (suffix.endswith(".bam") or suffix.endswith(".cram")):
        return None

    build = _BUILD_ALIASES.get((reference_genome or "hg38").strip().lower())
    loci = HLA_CLASS_I_LOCI.get(build or "GRCh38")
    if not loci:
        return None

    # A region query needs an index, and main.nf posts the alignment without one.
    if (await _samtools("index", str(input_path)))[0] != 0:
        return None

    # Contig naming is not knowable in advance: `chr6` and `6` are both in the wild
    # and this sidecar sees whatever the caller aligned against.
    rc, header = await _samtools("view", "-H", str(input_path))
    if rc != 0:
        return None
    names = set(re.findall(rb"\sSN:(\S+)", header))
    contig = next((c for c in (b"chr6", b"6") if c in names), None)
    if contig is None:
        return None

    regions = [f"{contig.decode()}:{start}-{end}" for start, end in loci]
    rc, counted = await _samtools(
        "view", "-c", "-q", str(PROBE_MIN_MAPQ), str(input_path), *regions
    )
    if rc != 0:
        return None
    try:
        return int(counted.decode().strip())
    except ValueError:
        return None


@app.get("/health")
def health():
    return {"status": "healthy", "service": "zarohla"}


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
    step_name: Optional[str] = Form("zarohla"),
) -> Dict[str, Any]:

    job_client = None
    if job_id:
        try:
            job_client = JobClient(job_id=job_id, step_name=step_name)
            if await job_client.is_job_cancelled():
                logger.info(
                    f"Workflow {job_id} is cancelled, aborting ZaroHLA processing"
                )
                return {"success": False, "error": "Workflow has been cancelled"}

            await job_client.start_step("Starting HLA typing")
        except Exception as e:
            logger.warning(f"Failed to initialize JobClient: {e}")

    local_job_id = str(uuid.uuid4())
    job_dir = job_work_dir(job_id, local_job_id)
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

            if (
                input_path.name.lower().endswith(".bam")
                or input_path.name.lower().endswith(".sam")
                or input_path.name.lower().endswith(".cram")
            ):
                # Ask the alignment itself whether it carries any HLA reads before
                # spending a whole-genome FASTQ conversion finding out. A zero here
                # is an observed fact; None means the probe could not run, and is
                # deliberately NOT treated as zero. See _probe_hla_read_count.
                hla_reads = await _probe_hla_read_count(input_path, reference_genome)
                if hla_reads == 0:
                    logger.info(
                        f"Job {local_job_id}: no MAPQ>={PROBE_MIN_MAPQ} reads on "
                        "HLA-A/-B/-C; nothing to type"
                    )
                    return await _no_hla_reads_result(job_client)
                logger.info(f"Job {local_job_id}: {hla_reads} HLA class I reads")

                if job_client:
                    await job_client.log_progress(
                        f"Converting BAM to FASTQ using samtools"
                    )

                f1_path = job_dir / "read1.fq"
                f2_path = job_dir / "read2.fq"
                # Single-end reads. A read in a single-end alignment carries neither
                # READ1 nor READ2, and samtools routes exactly those reads to -0. This
                # used to be /dev/null, so every read of a single-end BAM was thrown
                # away, R1/R2 came out empty, and the 0-byte guard below then reported
                # "no HLA reads" -- for a sample with 96,189 reads in the MHC (measured).
                # See the choice of OptiType inputs after the conversion.
                f0_path = job_dir / "read0.fq"
                # `samtools fastq -1/-2` only routes properly-mate-adjacent reads to the
                # paired outputs; a real BAM is coordinate-sorted with mates far apart,
                # so it must be name-collated first or most pairs fall through to the
                # discarded singleton stream and OptiType gets few/no reads. Collate to
                # a temp BAM first (no shell: this sidecar is command-injection hardened).
                collated_path = job_dir / "collated.bam"
                collate_cmd = [
                    "samtools",
                    "collate",
                    "-u",
                    "-o",
                    str(collated_path),
                    str(input_path),
                ]
                logger.info(f"Running samtools: {' '.join(collate_cmd)}")
                collate_proc = await asyncio.create_subprocess_exec(
                    *collate_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                if job_id:
                    running_processes[job_id] = {
                        "pid": collate_proc.pid,
                        "job_dir": str(job_dir),
                    }
                _, collate_err = await collate_proc.communicate()
                if job_id and job_id not in running_processes:
                    raise Exception("Process cancelled by user")
                if collate_proc.returncode != 0:
                    raise Exception(f"samtools collate failed: {collate_err.decode()}")

                cmd = [
                    "samtools",
                    "fastq",
                    "-1",
                    str(f1_path),
                    "-2",
                    str(f2_path),
                    "-0",
                    str(f0_path),
                    "-s",
                    "/dev/null",
                    str(collated_path),
                ]

                logger.info(f"Running samtools: {' '.join(cmd)}")
                process = await asyncio.create_subprocess_exec(
                    *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                if job_id:
                    running_processes[job_id] = {
                        "pid": process.pid,
                        "job_dir": str(job_dir),
                    }

                stdout, stderr = await process.communicate()

                if job_id and job_id not in running_processes:
                    raise Exception("Process cancelled by user")

                if process.returncode != 0:
                    raise Exception(f"samtools failed: {stderr.decode()}")

                # Pick OptiType's inputs from where the reads actually went. A paired
                # alignment fills R1/R2 and is typed paired, exactly as before. A
                # single-end one leaves R1/R2 empty and fills -0, so it is typed
                # single-end from that. Deciding this AFTER the conversion, from file
                # sizes, is what keeps the 0-byte guard honest: it now only ever sees
                # an empty file when the reads were genuinely absent, not when a flag
                # sent them somewhere nobody looked. (A mixed alignment keeps the
                # paired reads and drops the single-end ones, as it always did.)
                def _has_reads(path):
                    return os.path.exists(path) and os.path.getsize(path) > 0

                if not _has_reads(f1_path) and not _has_reads(f2_path):
                    if _has_reads(f0_path):
                        logger.info(
                            f"Job {local_job_id}: single-end alignment; typing from "
                            f"{f0_path.name}"
                        )
                        f1_path, f2_path = f0_path, None
            else:
                f1_path = input_path
        else:
            raise HTTPException(
                status_code=400,
                detail="Must provide either 'file' or 'file1' and 'file2'",
            )

        # A targeted PGx panel with no HLA capture converts to genuinely empty
        # FASTQs, and OptiType does not tolerate that: it dies inside pandas with
        # "Length mismatch: Expected axis has 0 elements", a non-zero exit that
        # took the whole job down at step 2/6. Having no HLA reads is a property
        # of the sample, not a failure, so it must not be one.
        #
        # The check is here, BEFORE OptiType runs, and that placement is the whole
        # point. Downgrading OptiType's exit code afterwards cannot distinguish
        # "nothing to type" from "OptiType is broken", and would resurrect the bug
        # pipelines/pgx/main.nf still carries a comment about: a failing service
        # reporting no HLA calls, indistinguishable from a legitimate none. That
        # matters clinically -- HLA-B*57:01 (abacavir) and HLA-A*31:01
        # (carbamazepine) are core-23 genes, so "no calls" reads as "nothing to
        # worry about". Zero bytes out of samtools is an observed fact about the
        # input; a crash is an inference. Only the first is safe to act on, so a
        # non-empty FASTQ that OptiType then fails on stays fatal, as before.
        #
        # Size, deliberately, not a record parse: samtools writes a 0-byte file
        # when there were no reads, whereas a file with content but no valid
        # records is a different and real problem that must keep failing.
        candidates = [p for p in (f1_path, f2_path) if p and os.path.exists(p)]
        if candidates and all(os.path.getsize(p) == 0 for p in candidates):
            logger.info(f"Job {local_job_id}: {NO_HLA_READS_MESSAGE}")
            return await _no_hla_reads_result(job_client)

        if job_client:
            await job_client.log_progress(f"Running OptiType on {f1_path.name}")

        cmd = ["optitype", "run", "-i", str(f1_path)]
        if f2_path and os.path.exists(f2_path) and os.path.getsize(f2_path) > 0:
            # OptiType v1.5 CLI takes each paired-end file as its own -i (not a bare positional)
            cmd.extend(["-i", str(f2_path)])

        cmd.extend([f"--{seq_type}", "--mapper", mapper, "-o", str(outdir)])

        logger.info(f"Running command: {' '.join(cmd)}")
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
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
                await job_client.fail_step(
                    "OptiType execution failed", {"error": stderr.decode()}
                )
            raise HTTPException(
                status_code=500, detail=f"OptiType failed: {stderr.decode()}"
            )

        if job_client:
            await job_client.log_progress("Parsing OptiType results")

        results = {}
        # OptiType v1.5 writes into a timestamped subdir (outdir/<ts>/<ts>_result.tsv)
        result_files = list(outdir.rglob("*_result.tsv"))
        if not result_files:
            raise Exception("OptiType did not produce a _result.tsv file")

        tsv_file = result_files[0]
        with open(tsv_file, "r") as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row in reader:
                results["HLA-A"] = f"{row.get('A1', '')},{row.get('A2', '')}".strip(",")
                results["HLA-B"] = f"{row.get('B1', '')},{row.get('B2', '')}".strip(",")
                results["HLA-C"] = f"{row.get('C1', '')},{row.get('C2', '')}".strip(",")
                break

        results = {k: v for k, v in results.items() if v}

        if job_client:
            await job_client.complete_step(
                "HLA typing completed successfully", {"results": results}
            )

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
