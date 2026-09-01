"""
Upload Router - Nextflow-Only Processing

This module handles genomic data uploads and processes them exclusively through Nextflow.
"""

import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import requests
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
)
from sqlalchemy.orm import Session

from app.api.db import (
    SessionLocal,
    create_patient,
    get_db,
    register_genetic_data,
    save_genomic_header,
)
from app.api.models import FileAnalysis as PydanticFileAnalysis
from app.api.models import (
    FileType,
    JobCreate,
    JobLogCreate,
    JobStatus,
    JobStepUpdate,
    JobUpdate,
    LogLevel,
    StepStatus,
    UploadResponse,
    VCFHeaderInfo,
    WorkflowInfo,
    WorkflowOptions,
)
from app.api.utils.file_processor import FileProcessor, safe_upload_basename
from app.api.utils.header_inspector import (
    extract_raw_header_text,
    filter_header_to_canonical_contigs,
    inspect_header,
)
from app.pharmcat.report_json import PharmCATSchemaError, iter_gene_blocks
from app.reports.generator import create_interactive_html_report
from app.reports.pdf_generators import generate_pdf_report_dual_lane
from app.services.job_service import JobService, schedule_coroutine
from app.services.workflow_progress_calculator import WorkflowProgressCalculator
from app.utils.env import env_flag
from app.utils.pharmcat_assume_ref import resolve_assume_ref_flags
from app.visualizations.workflow_diagram import (
    render_kroki_mermaid_svg,
    render_simple_png_from_workflow,
    render_with_graphviz,
    render_workflow,
)

from ..utils.security import get_optional_user

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize router
router = APIRouter(prefix="/upload", tags=["upload"])

# Constants
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/data/uploads")
REPORTS_DIR = os.environ.get("REPORT_DIR", "/data/reports")

# Same idiom/size as the sidecars' UPLOAD_CHUNK_BYTES (docker/pypgx/pypgx_wrapper.py,
# docker/zarohla/app.py, docker/gatk-api/gatk_api.py, docker/pharmcat/pharmcat.py):
# stream an upload in bounded chunks instead of buffering it whole in memory.
UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024

# Not created at import time — the default is an absolute container path, so importing this
# module off-host would create it on the host. app.main's startup_event creates it instead.

# Initialize file processor
file_processor = FileProcessor(temp_dir=UPLOAD_DIR)

# Wall-clock ceiling for the Nextflow poll loop, in seconds. Generous on purpose: a WGS
# BAM run walks ZaroHLA -> GATK -> PyPGx -> PharmCAT and can legitimately take most of a
# day, so this is a stuck-job backstop, not a service-level objective.
DEFAULT_NEXTFLOW_MAX_WAIT_SECONDS = 86400  # 24 hours

# Statuses that mean somebody else already finished this job. The Nextflow poll is not the
# only thing that can complete a run (containers report in over JOB_API_BASE, and app.main
# completes a job once its reports exist), so a deadline must never overwrite one of these.
TERMINAL_JOB_STATUSES = frozenset(
    {
        JobStatus.COMPLETED.value,
        JobStatus.FAILED.value,
        JobStatus.CANCELLED.value,
    }
)


def _nextflow_max_wait_seconds() -> float:
    """How long to wait for a Nextflow job before failing it.

    Override with ``NEXTFLOW_MAX_WAIT_SECONDS`` (seconds). Anything unusable — blank,
    unparseable, zero or negative — falls back to the default. Falling back is the safe
    direction in both senses: the cap is never disabled, and a plausible-looking typo
    (``0``, which conventionally reads as "no limit") can never fail every job instantly.
    """
    raw = (os.getenv("NEXTFLOW_MAX_WAIT_SECONDS") or "").strip()
    if not raw:
        return float(DEFAULT_NEXTFLOW_MAX_WAIT_SECONDS)
    try:
        seconds = float(raw)
    except ValueError:
        seconds = 0.0
    if seconds <= 0:
        logger.warning(
            "Unusable NEXTFLOW_MAX_WAIT_SECONDS=%r; using %ss",
            raw,
            DEFAULT_NEXTFLOW_MAX_WAIT_SECONDS,
        )
        return float(DEFAULT_NEXTFLOW_MAX_WAIT_SECONDS)
    return seconds


# Input types pipelines/pgx/main.nf can carry from upload to report. Anything else
# reaches its `error "Unsupported input type: ${params.input_type}"` and the run dies at
# workflow definition, so submitting one can only ever produce a failed job.
#
# `fastq` is deliberately absent even though main.nf *has* a fastq branch: that branch's
# first step POSTs to gatk-api's /align-fastq, which answers HTTP 501 because the image
# ships no aligner, and main.nf's curls use --fail-with-body, so the 501 kills the run.
# A branch existing is not the same as the branch working.
#
# `bcf` is present, and it must still never be ALIASED onto `vcf`. That alias was tried
# and reverted, and the reason it was wrong has not gone away: it made main.nf take the
# vcf branch, which converts nothing - it stages the upload verbatim, so the file
# reached docker/pharmcat still named `.bcf`, /genotype gated on that name and answered
# 400, and the PharmCAT curl's trailing `|| true` swallowed it. The run then "completed"
# with no PharmCAT output: a silent wrong answer replacing a loud one. Renaming an input
# type is only honest when a lane exists that can finish it.
#
# `bcf` is here because that lane now exists: main.nf has a `bcf` branch whose first
# step is the BcfToVCF process, which POSTs the file to gatk-api's /bcf-to-vcf
# (`bcftools view -O z` plus a tabix index) and refuses to return anything it cannot
# vouch for as a non-empty BGZF VCF. The file that reaches the filename-gating sidecars
# is a genuinely different, genuinely converted one - which is what the alias never was.
#
# `gvcf` is here on the same terms and for a sharper version of the same reason: aliasing
# a gVCF onto `vcf` would not merely fail to convert it, it would hand PharmCAT a file
# PharmCAT itself detects and refuses ("... is a gVCF file, which is not currently
# supported"), behind the same swallowing `|| true`. main.nf's `gvcf` branch runs
# GVCFToVCF first, which POSTs to gatk-api's /gvcf-to-vcf: two GATK GenotypeGVCFs passes
# joined with `bcftools concat -a`, published under a name that is deliberately not
# `*.g.vcf*` because PharmCAT condemns that filename before reading a byte. Not every
# gVCF is accepted -- a non-GATK (`<*>`) flavour and a GRCh37 one are refused at
# FileProcessor, each for a reason the conversion cannot get past.
NEXTFLOW_INPUT_TYPES = frozenset({"vcf", "bcf", "gvcf", "bam", "cram", "sam"})


def _unanalysable_upload_reason(workflow: Dict[str, Any]) -> Optional[str]:
    """Why this upload cannot be analysed at all, or ``None`` to let it through.

    ``unsupported`` on its own is not a refusal. It means "flagged", and
    ``is_provisional`` — this codebase's own flag for "we did analyse it,
    provisionally" — is what would exempt a flagged input from refusal.

    As of 2026-08-31 **nothing sets it**. Both inputs that once did are now decided
    the other way: a GRCh37/hg19 VCF is genuinely supported (lifted over to GRCh38
    via GATK Picard LiftoverVcf) and reaches this gate unflagged, and a VCF on a
    third build — T2T-CHM13, the only one the header inspector can name — is now
    refused outright rather than analysed on coordinates that are not GRCh38's.
    The exemption is kept anyway, because the ordering below is the actual guard:
    the flag is written by hand next to a reason string, so it records intent as
    readily as behaviour, and consulting the pipeline's repertoire first is what
    stops a mis-set one from waving an input past.

    What genuinely cannot work is an input that is flagged unsupported and that the
    pipeline cannot carry: FASTQ, 23andMe, AncestryDNA, FASTA, BED and unrecognised
    formats. Those used to be accepted, queued, and then failed minutes later with a
    Nextflow or gatk-api error the user could do nothing with.

    BCF and gVCF used to sit in that list and no longer do, each for its own reason.

    A BCF onto the *vcf* branch would not run and would not say so, because that branch
    stages the upload verbatim and docker/pharmcat's /genotype gates on the ``.bcf``
    filename behind a ``|| true``. What it needed was a real conversion step, and it has
    one — main.nf's own ``bcf`` branch converts the file with bcftools (gatk-api's
    /bcf-to-vcf) before anything downstream sees it.

    A gVCF's old refusal was justified with a sentence that was simply false: that
    PharmCAT "has never been validated against a gVCF's ``<NON_REF>`` reference blocks,
    so the output would be star alleles nobody has checked". PharmCAT 3.4.0 emits no
    star alleles from a gVCF at all — it DETECTS one (filename, ``##GVCFBlock`` header,
    or a reference-block data row) and refuses it outright. The risk was a loud failure
    minutes into a run, not a quiet wrong answer. gVCFs now have a real lane too:
    main.nf's ``gvcf`` branch genotypes the file with GATK GenotypeGVCFs (gatk-api's
    /gvcf-to-vcf) over PharmCAT's own position list, which makes the converted file
    *better* than an ordinary VCF rather than merely acceptable — its reference
    genotypes at pharmacogene positions are called data instead of PharmCAT's
    ``--absent-to-ref`` fabrication.

    Two gVCF shapes still reach this gate, and both are refused on facts the conversion
    cannot get past: one whose reference blocks are ``<*>`` rather than GATK's
    ``<NON_REF>`` (GenotypeGVCFs stops on it), and a GRCh37/hg19 one (PharmCAT's
    position list exists in GRCh38 coordinates only, so the pass that makes the lane
    worthwhile has nothing to run against). See FileProcessor's ``_refuse_non_gatk_gvcf``
    and ``_refuse_grch37_gvcf``.

    A gVCF that happens to be *written* as a BCF is routed to the gVCF lane, not the BCF
    one; FileProcessor reads ``##GVCFBlock`` out of the binary header to catch it, and
    the gVCF conversion's staging step is bcftools, so it reads a BCF for free.

    This gate is still not a complete guard, and cannot be one: it only ever sees inputs
    FileProcessor chose to flag.

    ``is_provisional`` only exempts a *runnable* input type, and that ordering is the
    point rather than belt-and-braces. The flag was written aspirationally once — for
    23andMe, meaning "we intend to convert this one day" rather than "we analysed
    this" — and only the pipeline's own repertoire says whether a provisional
    analysis is a thing that can happen at all, so it is consulted first.
    """
    if not workflow.get("unsupported"):
        return None
    file_type = str(workflow.get("file_type") or "unknown").lower()
    if file_type in NEXTFLOW_INPUT_TYPES and workflow.get("is_provisional"):
        return None
    return workflow.get("unsupported_reason") or (
        f"Files of type '{file_type}' cannot be analysed."
    )


def _has_readable_gene_blocks(pharmcat_data: Any) -> bool:
    """True when the report formatters would find at least one gene to render.

    Asks ``iter_gene_blocks`` -- the same walker the report lane and the database
    loader use -- rather than testing ``pharmcat_data["genes"]`` for truthiness.
    The TSV fallback seeds ``{"genes": {"CPIC": {}}}`` before it knows whether any
    row carries a gene symbol, and that empty bucket is truthy while yielding
    nothing to render, so a truthiness test would call an empty report usable.
    """
    if not isinstance(pharmcat_data, Mapping):
        return False
    genes_section = pharmcat_data.get("genes")
    if not isinstance(genes_section, Mapping):
        return False
    return any(True for _ in iter_gene_blocks(genes_section))


def _discard_refused_upload(file_paths: Optional[List[str]]) -> None:
    """Delete the bytes of an upload we are about to refuse. Never raises.

    A refusal is not a failure to clean up after: the file is unreferenced the moment
    the 400 is raised, no patient or job row names it, and app/services/cleanup_service
    only sweeps ``/data/uploads/{patient_id}``. Best effort by design — a file that
    cannot be removed must not turn a clean 400 into a 500.
    """
    for path in file_paths or []:
        try:
            os.unlink(path)
            logger.info("Removed refused upload: %s", path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("Could not remove refused upload %s: %s", path, exc)


# --- Boundary validation for values that reach the Nextflow pipeline's shell ---
#
# main.nf assembles curl argv inside a bash `shell:` block. Nextflow escapes `path`
# inputs before interpolation but NOT `val`/`params` strings, so any user string that
# reaches an `!{...}` interpolation is spliced verbatim into the shell: a `"` breaks out
# of the surrounding quoting and the remainder runs as code - inside the nextflow
# container, which bind-mounts the Docker socket (root on the host). Two user-facing
# fields travel that path: sample_identifier and reference_genome (-> params.reference).
# We constrain both to a strict allowlist of characters that mean nothing to the shell,
# so the interpolation is safe by construction rather than by hoping an escape holds.
# sample_identifier is ALSO handed to the pipeline through the environment
# (SAMPLE_IDENTIFIER, see runner.py/main.nf), which is the structural fix; this
# allowlist is defence in depth and turns a hostile value into a clear 400.
#
# The alphabet is deliberately generous enough for real identifiers - alphanumerics
# plus dot, underscore and hyphen, 1-64 chars, first char alphanumeric - so ordinary
# sample names (NA12878, Sample_01, HG002.GRCh38, patient-123) and reference labels
# (hg38, GRCh38, hg19, GRCh37, b37, T2T-CHM13) all pass, while every shell metacharacter
# (quote, semicolon, $, backtick, parenthesis, whitespace, newline, ...) is rejected.
_PIPELINE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def validate_pipeline_token(value: Optional[str], field_name: str) -> str:
    """Return the stripped value if it is a safe pipeline token, else raise HTTP 400.

    Empty/whitespace-only input is rejected; callers treating the field as optional
    must guard for emptiness before calling.
    """
    stripped = (value or "").strip()
    if not _PIPELINE_TOKEN_RE.match(stripped):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid {field_name}: only letters, digits, dot, underscore and "
                "hyphen are allowed (1-64 characters, and it must start with a letter "
                f"or digit). Received: {value!r}"
            ),
        )
    return stripped


def sanitize_optional_pipeline_token(value: Optional[str]) -> Optional[str]:
    """Filter a derived, non-user-facing token (e.g. a VCF-header sample name).

    Returns the stripped value if it passes the allowlist, else None. Used for the
    header-derived sample identifier: it must never fail an otherwise-valid job, but it
    also must not carry shell metacharacters into the pipeline - so an unusual header
    name is dropped rather than trusted.
    """
    stripped = (value or "").strip()
    if stripped and _PIPELINE_TOKEN_RE.match(stripped):
        return stripped
    return None


def analysis_reference(workflow: Dict[str, Any]) -> str:
    """The build the pipeline analyses against -> Nextflow's params.reference.

    A GRCh37/hg19 VCF is lifted to GRCh38 (gatk-api /liftover-vcf) BEFORE PyPGx
    and PharmCAT ever see it, so the build the analysis runs against is hg38 no
    matter which reference the uploader picked for the *original* file. Reading
    the reference_genome form field here would let a GRCh37 upload whose selector
    was (quite reasonably) left on "hg19" run PyPGx's GRCh37 assembly against the
    already-lifted GRCh38 coordinates -- silently wrong star alleles, the worst
    failure mode in the pipeline, with nothing in the output saying so. main.nf
    documents params.reference as the TARGET build for exactly this reason.

    Non-liftover inputs are unchanged: their coordinates are whatever the
    reference_genome field says, so that value flows straight through.
    """
    if workflow.get("needs_liftover"):
        return "hg38"
    return workflow.get("reference", "hg38")


# Report generation flags
INCLUDE_PHARMCAT_HTML = env_flag("INCLUDE_PHARMCAT_HTML", True)
INCLUDE_PHARMCAT_JSON = env_flag("INCLUDE_PHARMCAT_JSON", False)
INCLUDE_PHARMCAT_TSV = env_flag("INCLUDE_PHARMCAT_TSV", False)

# Always use Nextflow for processing
USE_NEXTFLOW = True

# Log the configuration for debugging
logger.info(
    f"PharmCAT Report Configuration - HTML: {INCLUDE_PHARMCAT_HTML}, JSON: {INCLUDE_PHARMCAT_JSON}, TSV: {INCLUDE_PHARMCAT_TSV}"
)

# Progress calculation is now handled by WorkflowProgressCalculator


async def delayed_cleanup_on_cancellation(job_id: str, job_metadata: dict):
    """
    Perform delayed cleanup when app container detects cancellation.

    This function waits a short period to ensure any in-progress file operations
    complete, then cleans up the reports directory and other files.

    Args:
        job_id: The workflow ID that was cancelled
        job_metadata: Workflow metadata containing file paths
    """

    try:
        # Wait a short period to ensure any in-progress operations complete
        await asyncio.sleep(2.0)

        patient_id = job_metadata.get("patient_id")
        if not patient_id:
            logger.warning(
                f"No patient_id found in workflow metadata for delayed cleanup of job {job_id}"
            )
            return

        # Define cleanup paths (nested job outdir + legacy flat patient dir)
        cleanup_paths = [
            f"/data/reports/{patient_id}/{job_id}",  # Nested job outdir
            f"/data/reports/{patient_id}",  # Legacy flat patient dir
            f"/data/temp/{patient_id}",  # Temporary files
            f"/data/uploads/{patient_id}",  # Uploaded files
            f"/data/results/{patient_id}",  # Results directory
        ]

        # Add any additional paths from metadata
        if "output_directory" in job_metadata:
            cleanup_paths.append(job_metadata["output_directory"])
        if "temp_directory" in job_metadata:
            cleanup_paths.append(job_metadata["temp_directory"])

        # Clean up each path
        for path_str in cleanup_paths:
            try:
                path = Path(path_str)
                if path.exists():
                    logger.info(f"Delayed cleanup: Removing directory {path}")
                    shutil.rmtree(path, ignore_errors=True)
                    logger.info(f"Delayed cleanup: Successfully removed {path}")
                else:
                    logger.debug(
                        f"Delayed cleanup: Path does not exist, skipping {path}"
                    )
            except Exception as e:
                logger.warning(f"Delayed cleanup: Failed to remove {path_str}: {e}")

        logger.info(f"Delayed cleanup completed for cancelled job {job_id}")

    except Exception as e:
        logger.error(f"Error during delayed cleanup of job {job_id}: {e}")


async def handle_final_stages_progression(
    job_service: JobService, job_id: str, outdir: str
):
    """
    Handle the final stages of workflow progression after Nextflow completion.

    Runs the sync report/diagram work in a worker thread so Kroki ``requests``
    calls cannot block the uvicorn event loop (health + GET /jobs stay responsive).
    Uses a fresh DB session in the worker (SQLAlchemy sessions are not thread-safe).
    """
    # job_service is unused here — kept for call-site compatibility
    await asyncio.to_thread(_handle_final_stages_progression_sync, job_id, outdir)


def _handle_final_stages_progression_sync(job_id: str, outdir: str):
    """
    Sync implementation of final-stage report generation (may block on Kroki/IO).

    Opens its own SessionLocal so work is safe under asyncio.to_thread.
    """
    from app.api.db import SessionLocal

    db = SessionLocal()
    job_service = JobService(db)
    try:
        # Check for cancellation before starting
        job = job_service.get_job(job_id)
        if job and job.status == "cancelled":
            logger.info(f"Job {job_id} was cancelled before report generation")
            # Thread-safe schedule onto the main event loop
            schedule_coroutine(
                delayed_cleanup_on_cancellation(job_id, job.job_metadata)
            )
            return

        # Send initial progress update
        step_update = JobStepUpdate(
            status=StepStatus.RUNNING, output_data={"progress_percent": 0}
        )
        job_service.update_job_step(job_id, "report_generation", step_update)

        log_data = JobLogCreate(
            step_name="report_generation",
            log_level=LogLevel.INFO,
            message="Generating final reports from Nextflow output",
        )
        job_service.log_job_event(job_id, log_data)

        # Get workflow metadata to extract patient and data information
        job = job_service.get_job(job_id)
        if not job:
            raise RuntimeError(f"Job {job_id} not found")

        metadata = job.job_metadata or {}
        patient_id = metadata.get("patient_id")
        data_id = metadata.get("data_id")
        workflow_config = metadata.get("workflow", {})
        file_analysis = metadata.get("file_analysis", {})

        if not patient_id or not data_id:
            raise RuntimeError(f"Missing patient_id or data_id in workflow metadata")

        # Extract sample identifier from workflow metadata
        sample_identifier = None
        if "sample_identifier" in metadata:
            sample_identifier = metadata["sample_identifier"]
        header_sample_identifier = metadata.get("header_sample_identifier")

        # outdir is /data/reports/{patient_id}/{job_id}
        patient_dir = Path(outdir)
        patient_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Using patient directory: {patient_dir}")

        # Set up all output paths in the nested job directory (filenames use job_id)
        pdf_report_path = patient_dir / f"{job_id}_pgx_report.pdf"
        interactive_html_path = patient_dir / f"{job_id}_pgx_report_interactive.html"
        pharmcat_html_path = patient_dir / f"{job_id}_pgx_pharmcat.html"
        pharmcat_json_path = patient_dir / f"{job_id}_pgx_pharmcat.json"
        pharmcat_tsv_path = patient_dir / f"{job_id}_pgx_pharmcat.tsv"

        # Check for existing PharmCAT outputs in the patient directory
        logger.info(f"Looking for PharmCAT files in: {patient_dir}")

        pharmcat_html_exists = pharmcat_html_path.exists()
        pharmcat_json_exists = pharmcat_json_path.exists()
        pharmcat_tsv_exists = pharmcat_tsv_path.exists()

        if patient_dir.exists():
            logger.info(
                f"Patient directory exists, contents: {list(patient_dir.glob('*'))}"
            )
            logger.info(
                f"PharmCAT files exist - HTML: {pharmcat_html_exists}, JSON: {pharmcat_json_exists}, TSV: {pharmcat_tsv_exists}"
            )

            # Log the actual files found for debugging
            pharmcat_pattern = f"{job_id}_pgx_pharmcat.*"
            pharmcat_files = list(patient_dir.glob(pharmcat_pattern))
            logger.info(f"Found PharmCAT files: {pharmcat_files}")

        # Attempt reconciliation if some PharmCAT outputs are missing
        if not (pharmcat_html_exists and pharmcat_json_exists and pharmcat_tsv_exists):
            try:
                reports_root = patient_dir.parent
                # Try to derive sample base from any pharmcat pipeline log in this dir
                sample_base = None
                logs = list(patient_dir.glob("*_pharmcat_pipeline.log"))
                if logs:
                    sample_base = logs[0].name.replace("_pharmcat_pipeline.log", "")
                    logger.info(f"Derived sample_base from pipeline log: {sample_base}")
                # Candidate alternate directory names to search
                alt_dir_names = []
                if sample_identifier and str(sample_identifier).strip():
                    alt_dir_names.append(str(sample_identifier).strip())
                if header_sample_identifier and str(header_sample_identifier).strip():
                    alt_dir_names.append(str(header_sample_identifier).strip())
                if sample_base:
                    alt_dir_names.append(sample_base)
                # Deduplicate while preserving order
                seen = set()
                alt_dir_names = [
                    x for x in alt_dir_names if not (x in seen or seen.add(x))
                ]
                for alt_name in alt_dir_names:
                    alt_dir = reports_root / alt_name
                    if not alt_dir.exists():
                        continue
                    # Candidate source files (by job_id, patient_id, and by alt_name)
                    src_candidates = [
                        (
                            alt_dir / f"{job_id}_pgx_pharmcat.html",
                            pharmcat_html_path,
                        ),
                        (
                            alt_dir / f"{job_id}_pgx_pharmcat.json",
                            pharmcat_json_path,
                        ),
                        (alt_dir / f"{job_id}_pgx_pharmcat.tsv", pharmcat_tsv_path),
                        (
                            alt_dir / f"{patient_id}_pgx_pharmcat.html",
                            pharmcat_html_path,
                        ),
                        (
                            alt_dir / f"{patient_id}_pgx_pharmcat.json",
                            pharmcat_json_path,
                        ),
                        (alt_dir / f"{patient_id}_pgx_pharmcat.tsv", pharmcat_tsv_path),
                        (alt_dir / f"{alt_name}_pgx_pharmcat.html", pharmcat_html_path),
                        (alt_dir / f"{alt_name}_pgx_pharmcat.json", pharmcat_json_path),
                        (alt_dir / f"{alt_name}_pgx_pharmcat.tsv", pharmcat_tsv_path),
                    ]
                    for src, dest in src_candidates:
                        try:
                            if src.exists() and not dest.exists():
                                shutil.copy2(src, dest)
                                logger.info(
                                    f"Reconciled PharmCAT output: {src} -> {dest}"
                                )
                        except Exception as e:
                            logger.warning(f"Failed to reconcile {src} -> {dest}: {e}")
                    # Refresh state
                    pharmcat_html_exists = pharmcat_html_path.exists()
                    pharmcat_json_exists = pharmcat_json_path.exists()
                    pharmcat_tsv_exists = pharmcat_tsv_path.exists()
                    if pharmcat_html_exists and pharmcat_json_exists:
                        break
            except Exception as e:
                logger.warning(
                    f"PharmCAT output reconciliation encountered an error: {e}"
                )

        # Try to load PharmCAT results from the Nextflow output
        pharmcat_data = {"genes": [], "drugRecommendations": []}

        # Look for PharmCAT JSON results
        pharmcat_json_file = patient_dir / f"{job_id}_pgx_pharmcat.json"
        pharmcat_run_id = None
        # 265: set when validate_report() refused the payload. The report lane
        # reads it twice -- once to keep the rejected payload out of
        # pharmcat_data, and once after the TSV fallback to decide whether this
        # job can honestly produce a report at all.
        pharmcat_schema_error: Optional[PharmCATSchemaError] = None

        if pharmcat_json_file.exists():
            try:
                with open(pharmcat_json_file, "r", encoding="utf-8") as f:
                    pharmcat_results = json.load(f)
                    if isinstance(pharmcat_results, dict):
                        # Load PharmCAT results into database
                        from app.pharmcat.pharmcat_parser import load_pharmcat_file

                        try:
                            # Pass the job_service's database session to ensure consistency
                            pharmcat_run_id = load_pharmcat_file(
                                pharmcat_json_file, job_service.db
                            )

                            if pharmcat_run_id:
                                # Link PharmCAT run to workflow
                                job_service.link_pharmcat_run(job_id, pharmcat_run_id)
                                logger.info(
                                    f"Successfully linked PharmCAT run {pharmcat_run_id} to job {job_id}"
                                )
                            else:
                                logger.warning(
                                    "load_pharmcat_file returned None - database insert may have failed"
                                )
                        except PharmCATSchemaError as schema_error:
                            # Not a database failure, and reporting it as one sends
                            # the operator to the wrong system. validate_report()
                            # found the payload's structure has moved away from what
                            # the walkers read, so nothing downstream can read it
                            # honestly -- not the DB loader, and not the report
                            # formatters, which share those walkers.
                            pharmcat_schema_error = schema_error
                            pharmcat_run_id = None
                            logger.error(
                                f"PharmCAT report.json rejected by the schema gate "
                                f"for job {job_id}: {schema_error}"
                            )
                        except Exception as db_error:
                            # Check if it's a database constraint error
                            error_str = str(db_error).lower()
                            if "value too long" in error_str or "varchar" in error_str:
                                logger.error(f"Database constraint error: {db_error}")
                                logger.error(
                                    "This usually means the database schema needs to be updated."
                                )
                                logger.error(
                                    "Run migration: db/init/migrations/01_fix_pharmcat_variant_allele_length.sql"
                                )
                            else:
                                logger.error(
                                    f"Failed to load PharmCAT data into database: {db_error}"
                                )
                            # Continue with file-based data fallback
                            pharmcat_run_id = None

                        if pharmcat_schema_error is None:
                            # PharmCAT JSON has genes directly, not in a "data" object
                            pharmcat_data = pharmcat_results
                            logger.info(
                                f"Loaded PharmCAT results from {pharmcat_json_file}"
                            )
                        else:
                            # Deliberately leave pharmcat_data at its empty seed.
                            # Assigning the rejected payload here is what made the
                            # gate ornamental: `genes` is non-empty in every
                            # rejection mode, so the TSV fallback below could never
                            # fire and the report was built from the very payload
                            # the gate had just declared unreadable.
                            logger.error(
                                "Not building a report from the rejected payload; "
                                "trying the PharmCAT TSV, which is a separate "
                                "artifact of the same run and is read by a "
                                "different parser"
                            )
                    else:
                        logger.warning(
                            f"PharmCAT JSON file has unexpected structure: {pharmcat_results}"
                        )
            except Exception as e:
                logger.error(f"Failed to load PharmCAT JSON results: {e}")

        # If JSON is missing or empty, try TSV fallback for simpler extraction
        if not pharmcat_data.get("genes"):
            try:
                pharmcat_tsv_file = patient_dir / f"{job_id}_pgx_pharmcat.tsv"
                if pharmcat_tsv_file.exists():
                    from app.reports.pharmcat_tsv_parser import (
                        parse_pharmcat_tsv,
                        tsv_entry_to_source_diplotype,
                    )

                    tsv_diplotypes, tsv_recs = parse_pharmcat_tsv(
                        str(pharmcat_tsv_file)
                    )
                    if tsv_diplotypes:
                        # Build minimal pharmcat_data structure compatible with downstream formatting
                        pharmcat_data = {
                            "genes": {"CPIC": {}},
                            "drugRecommendations": [],
                        }
                        for entry in tsv_diplotypes:
                            gene = entry.get("gene")
                            if not gene:
                                continue
                            gene_block = pharmcat_data["genes"]["CPIC"].setdefault(
                                gene, {}
                            )
                            # Carry the TSV's Outside Call through as PharmCAT's
                            # own vocabulary, so the rest of the pipeline needs
                            # no TSV-specific provenance code (BACKLOG 28 + 216).
                            outside = (entry.get("outside_call") or "").strip().lower()
                            if outside == "yes":
                                gene_block["callSource"] = "OUTSIDE"
                            elif outside == "no":
                                gene_block["callSource"] = "MATCHER"
                            dip_obj = tsv_entry_to_source_diplotype(entry)
                            gene_block.setdefault("sourceDiplotypes", [])
                            gene_block["sourceDiplotypes"].append(dip_obj)
                            # Optional mirror for any leftover sniffers:
                            gene_block.setdefault("recommendationDiplotypes", [])
                            gene_block["recommendationDiplotypes"].append(dip_obj)
                        # Map recommendations if any
                        if tsv_recs:
                            for rec in tsv_recs:
                                pharmcat_data.setdefault(
                                    "drugRecommendations", []
                                ).append(
                                    {
                                        "drug": rec.get("drug"),
                                        "genes": rec.get("gene"),
                                        "recommendation": rec.get("recommendation"),
                                        "classification": rec.get("classification")
                                        or "Unknown",
                                    }
                                )
                        logger.info(
                            f"Loaded PharmCAT data via TSV fallback with {len(tsv_diplotypes)} diplotypes and {len(tsv_recs)} recommendations"
                        )
            except Exception as e:
                logger.warning(f"Failed TSV fallback for PharmCAT parsing: {e}")

        # 265: the gate has teeth on this lane too.
        #
        # A payload the gate rejected must not become a clinical report. The TSV
        # above is the one honest way out: it is PharmCAT's own tab-delimited
        # output for the same run, a different file read by a different parser
        # (app/reports/pharmcat_tsv_parser.py), so believing it is not believing
        # the payload that was refused. If it yielded gene blocks, the report is
        # built from that and the run continues.
        #
        # If it did not, there is nothing left to report from. Generating anyway
        # would emit a report with no genes, and an empty report is
        # indistinguishable from a clean one -- exactly the silent degradation
        # the gate exists to catch. So the job fails, loudly, naming the reason.
        #
        # Deliberately ahead of the `needs_report` gate below rather than inside
        # it. A rejected payload also failed to load into the database -- the
        # gate raised before the first write, so no PharmCAT run is linked --
        # which means a needs_report=False job would otherwise complete having
        # produced no usable PharmCAT data at all and said nothing about it.
        # That is the same silent degradation wearing a different hat. Opting out
        # of reports is not opting out of knowing the analysis failed.
        if pharmcat_schema_error is not None and not _has_readable_gene_blocks(
            pharmcat_data
        ):
            message = (
                f"PharmCAT report.json failed the structure gate and no usable "
                f"PharmCAT TSV was available, so no report was generated: "
                f"{pharmcat_schema_error}"
            )
            logger.error(f"Job {job_id}: {message}")
            job_service.update_job_step(
                job_id,
                "report_generation",
                JobStepUpdate(
                    status=StepStatus.FAILED,
                    error_details={
                        "error": message,
                        "reason": "pharmcat_schema_gate",
                    },
                ),
            )
            raise RuntimeError(message) from pharmcat_schema_error

        if pharmcat_schema_error is not None:
            logger.warning(
                f"Job {job_id}: report built from the PharmCAT TSV because the "
                f"report.json was rejected by the structure gate"
            )
            # The log is for the operator. The clinician holding the report needs
            # to know too -- a rescued report otherwise looks exactly like a
            # clean one -- so the same fact goes onto the Alerts and Warnings
            # page through workflow_warnings, the channel 265's unverified
            # version banner already established.
            #
            # Deliberately here rather than anywhere below: generate_report reads
            # workflow.warnings off the Job row with a plain query and no
            # populate_existing(), so it only sees this write because
            # `db_session = SessionLocal()` further down opens a session *after*
            # the commit, with an empty identity map. Move this past that line
            # and the banner silently stops appearing, with no error anywhere.
            # tests/test_generator_job_metadata_read.py holds the argument.
            try:
                from app.reports.generator import pharmcat_tsv_rescue_alert

                job_service.append_workflow_warning(
                    job_id, pharmcat_tsv_rescue_alert(str(pharmcat_schema_error))
                )
            except Exception as warn_error:
                # A report the reader can use, minus its banner, beats no report.
                logger.error(
                    f"Job {job_id}: failed to record the TSV-rescue alert on the "
                    f"report: {warn_error}"
                )

        # Update progress: Diagram generation (35% of report generation)
        step_update = JobStepUpdate(
            status=StepStatus.RUNNING, output_data={"progress_percent": 35}
        )
        job_service.update_job_step(job_id, "report_generation", step_update)

        # Generate workflow diagrams for this sample
        logger.info("=== WORKFLOW DIAGRAM GENERATION START ===")
        try:

            # Determine workflow configuration based on the data
            workflow_config_diagram = {
                "file_type": workflow_config.get("file_type")
                or file_analysis.get("file_type", "vcf"),
                "used_gatk": workflow_config.get("needs_gatk", False),
                "used_hla": workflow_config.get("needs_hla", False),
                "used_pypgx": workflow_config.get("needs_pypgx", False),
                "used_pypgx_bam2vcf": workflow_config.get("needs_pypgx_bam2vcf", False),
                "used_pharmcat": True,
                "used_mtdna": workflow_config.get("needs_mtdna", False),
                "exported_to_fhir": False,
            }

            logger.info(f"Workflow configuration: {workflow_config_diagram}")

            # Generate SVG workflow diagram (true Graphviz renderer for PDF-safe text)
            try:
                svg_bytes = render_with_graphviz(workflow_config_diagram, fmt="svg")
                if svg_bytes:
                    svg_path = patient_dir / f"{job_id}_workflow.svg"
                    with open(svg_path, "wb") as f_out:
                        f_out.write(svg_bytes)
                    logger.info(
                        f"✓ Graphviz Workflow SVG generated successfully: {svg_path} ({len(svg_bytes)} bytes)"
                    )
                else:
                    logger.warning(
                        "⚠ Graphviz Workflow SVG generation returned empty result"
                    )
            except Exception as e:
                logger.error(
                    f"✗ Graphviz Workflow SVG generation failed: {str(e)}",
                    exc_info=True,
                )

            # Generate Kroki Mermaid SVG workflow diagram for comparison
            try:
                kroki_svg_bytes = render_kroki_mermaid_svg(
                    workflow=workflow_config_diagram
                )
                if kroki_svg_bytes:
                    kroki_svg_path = (
                        patient_dir / f"{job_id}_workflow_kroki_mermaid.svg"
                    )
                    with open(kroki_svg_path, "wb") as f_out:
                        f_out.write(kroki_svg_bytes)
                    logger.info(
                        f"✓ Kroki Mermaid Workflow SVG generated successfully: {kroki_svg_path} ({len(kroki_svg_bytes)} bytes)"
                    )
                else:
                    logger.warning(
                        "⚠ Kroki Mermaid Workflow SVG generation returned empty result"
                    )
            except Exception as e:
                logger.error(
                    f"✗ Kroki Mermaid Workflow SVG generation failed: {str(e)}",
                    exc_info=True,
                )

            # Generate PNG workflow diagram
            try:
                png_bytes = render_workflow(fmt="png", workflow=workflow_config_diagram)
                if not png_bytes:
                    # Force pure-Python PNG fallback so a file is always present
                    logger.info("PNG generation failed, trying Python fallback...")
                    png_bytes = render_simple_png_from_workflow(workflow_config_diagram)
                if png_bytes:
                    png_path = patient_dir / f"{job_id}_workflow.png"
                    with open(png_path, "wb") as f_out:
                        f_out.write(png_bytes)
                    logger.info(
                        f"✓ Workflow PNG generated successfully: {png_path} ({len(png_bytes)} bytes)"
                    )
                else:
                    logger.warning(
                        "⚠ Workflow PNG generation still failed after fallback"
                    )
            except Exception as e:
                logger.error(
                    f"✗ Workflow PNG generation failed: {str(e)}", exc_info=True
                )

            logger.info(f"=== WORKFLOW DIAGRAM GENERATION END ===")
        except Exception as e:
            logger.error(
                f"✗ Workflow diagram generation failed: {str(e)}", exc_info=True
            )
            logger.info("Continuing without workflow diagrams...")

        # Determine effective Sample Identifier for reports
        effective_sample_identifier_reports = (
            (
                str(sample_identifier).strip()
                if (sample_identifier and str(sample_identifier).strip())
                else None
            )
            or (
                str(header_sample_identifier).strip()
                if (header_sample_identifier and str(header_sample_identifier).strip())
                else None
            )
            or patient_id
        )

        # Honour the report toggle. needs_report=False already drops the
        # report_generation step template (workflow_registry), and this gate is the only
        # thing that suppresses report artifacts: the skip_report the upload puts in the
        # Nextflow payload now survives the trip -- NextflowRunRequest declares it and
        # build_nextflow_command emits it on the argv (406) -- but no process in
        # pipelines/pgx/main.nf reads params.skip_report, so it only reaches the run's
        # resolved params. Without this, opting out of reports did nothing at all.
        # Absent means on: only an explicit opt-out disables it.
        needs_report = bool(workflow_config.get("needs_report", True))
        response_data: Dict[str, Any] = {}

        if needs_report:
            # Generate reports using the main report generation function with database integration
            logger.info(f"Generating reports using main report generation function")

            # A second, *fresh* session -- deliberately not the long-lived `db` this
            # function opened at the top. generate_report's Job read carries no
            # populate_existing(), so it only observes the upload's metadata on a
            # session with an empty identity map; tests/test_generator_job_metadata_read.py
            # pins both shapes.
            #
            # SessionLocal() + try/finally, never `next(get_db())`: get_db is a
            # generator whose `finally: db.close()` only runs when the generator is
            # exhausted or closed, and advancing it once never gets there -- that
            # session was leaked, its connection never returned to the pool.
            db_session = SessionLocal()

            # Use the main report generation function with database integration
            from app.reports.generator import generate_report

            try:
                response_data = generate_report(
                    pharmcat_results={"data": pharmcat_data},
                    output_dir=str(patient_dir),  # already nested outdir
                    patient_info={
                        "id": patient_id,
                        "data_id": data_id,
                        "sample_identifier": effective_sample_identifier_reports,
                    },
                    job_id=job_id,
                    db_session=db_session,
                )
            finally:
                db_session.close()

            # Update progress: Reports generated (100% of report generation)
            step_update = JobStepUpdate(
                status=StepStatus.RUNNING, output_data={"progress_percent": 100}
            )
            job_service.update_job_step(job_id, "report_generation", step_update)

            # Log report generation completion
            logger.info(f"Report generation completed for job {job_id}")
            logger.info(
                f"Generated reports: {[k for k, v in response_data.items() if v]}"
            )
        else:
            logger.info(
                f"Report generation disabled for job {job_id} (needs_report=False); "
                "skipping report artifacts"
            )
            log_data = JobLogCreate(
                step_name="report_generation",
                log_level=LogLevel.INFO,
                message="Report generation skipped: reports were disabled for this job",
            )
            job_service.log_job_event(job_id, log_data)

        # Add provisional flag if the workflow was marked as provisional
        is_provisional = workflow_config.get("is_provisional", False)

        # Add additional metadata to response_data from generate_report
        response_data["is_provisional"] = is_provisional
        response_data["job_directory"] = str(patient_dir)

        # Add PharmCAT run_id if available
        if pharmcat_run_id:
            response_data["pharmcat_run_id"] = pharmcat_run_id

        # Update workflow metadata with reports.
        #
        # Built from a re-read, not from the `metadata` snapshot taken near the
        # top of this function. update_job replaces job_metadata wholesale --
        # `job.job_metadata = update_data.metadata`, no merge
        # (job_service.py:371-372) -- so anything written to the row since that
        # snapshot is erased by a copy of it. And things are written to it:
        # link_pharmcat_run adds pharmcat_run_id/pharmcat_linked_at, the TSV
        # rescue above adds its reader-facing alert, and both assign a *new*
        # dict, which the stale local cannot see. get_job's populate_existing()
        # is what makes this a genuine re-read rather than the identity map's
        # copy of the same stale row.
        current_job = job_service.get_job(job_id)
        current_metadata = (
            (current_job.job_metadata or {}) if current_job is not None else metadata
        )
        updated_metadata = dict(current_metadata)
        updated_metadata["reports"] = response_data

        # Update the workflow with the new metadata
        workflow_update = JobUpdate(metadata=updated_metadata)
        job_service.update_job(job_id, workflow_update)

        # Complete the report generation step
        step_update = JobStepUpdate(
            status=StepStatus.COMPLETED,
            output_data={"reports": response_data, "progress_percent": 100},
        )
        job_service.update_job_step(job_id, "report_generation", step_update)

        # Complete the workflow
        workflow_update = JobUpdate(status=JobStatus.COMPLETED)
        job_service.update_job(job_id, workflow_update)

        # Broadcast workflow completion with report URLs
        try:
            schedule_coroutine(
                job_service._broadcast_job_update(
                    str(job_id),
                    {
                        "job_id": str(job_id),
                        "status": "completed",
                        "progress_percentage": 100,
                        "current_step": "completed",
                        "message": "Processing complete! - All processing finished",
                        "pdf_report_url": response_data.get("pdf_path"),
                        "html_report_url": response_data.get("html_path"),
                        "interactive_html_report_url": response_data.get(
                            "interactive_html_path"
                        ),
                        "pharmcat_html_report_url": response_data.get(
                            "pharmcat_html_path"
                        ),
                        "pharmcat_json_report_url": response_data.get(
                            "pharmcat_json_path"
                        ),
                        "pharmcat_tsv_report_url": response_data.get(
                            "pharmcat_tsv_path"
                        ),
                        "mtdna_report_url": response_data.get("mtdna_report_path"),
                        "mtdna_haplogroups_url": response_data.get(
                            "mtdna_haplogroups_path"
                        ),
                        "mtdna_vcf_url": response_data.get("mtdna_vcf_path"),
                    },
                )
            )
        except Exception as e:
            logger.error(f"Failed to broadcast job completion with reports: {e}")

        log_data = JobLogCreate(
            step_name="workflow_completion",
            log_level=LogLevel.INFO,
            message="Workflow completed successfully with reports generated",
        )
        job_service.log_job_event(job_id, log_data)

        logger.info(f"Job {job_id} completed successfully with reports generated")
        logger.info(f"Generated reports: {list(response_data.keys())}")

    except Exception as e:
        logger.error(f"Error in final stages progression for job {job_id}: {e}")
        workflow_update = JobUpdate(status=JobStatus.FAILED)
        job_service.update_job(job_id, workflow_update)

        log_data = JobLogCreate(
            step_name=None,
            log_level=LogLevel.ERROR,
            message=f"Error in final stages: {str(e)}",
        )
        job_service.log_job_event(job_id, log_data)
    finally:
        db.close()


async def wait_for_nextflow_completion(
    job_service: JobService,
    job_id: str,
    nextflow_url: str,
    job_key: str,
    outdir: str,
):
    """
    Wait for Nextflow job completion and coordinate with WorkflowProgressCalculator.

    This function monitors Nextflow execution and lets individual containers report
    their progress via WorkflowClient. The WorkflowProgressCalculator will handle
    progress calculation based on step status updates from the containers.

    The wait is bounded by ``NEXTFLOW_MAX_WAIT_SECONDS`` (see
    :func:`_nextflow_max_wait_seconds`). A job that never reaches a terminal Nextflow
    state is marked FAILED with a JobLog naming the limit, rather than polling forever.

    Args:
        job_service: Workflow service instance
        job_id: The workflow ID
        nextflow_url: Nextflow runner URL
        job_key: Nextflow job key
        outdir: Output directory path
    """
    try:
        logger.info(f"Waiting for Nextflow completion for job {job_id}")

        max_wait_seconds = _nextflow_max_wait_seconds()
        deadline = time.monotonic() + max_wait_seconds

        # Log that Nextflow execution has started
        log_data = JobLogCreate(
            step_name="nextflow_executor",
            log_level=LogLevel.INFO,
            message="Nextflow pipeline started - individual containers will report progress",
        )
        job_service.log_job_event(job_id, log_data)

        while True:
            try:
                # Check if workflow has been cancelled. This read is blocking SQLAlchemy,
                # so it runs in a worker thread like the status call below; the await
                # serialises access, so this session is never used by two threads at
                # once. (The writes further down stay on the loop: they are one-shot on
                # the way out, not per-iteration.)
                job = await asyncio.to_thread(job_service.get_job, job_id)
                if job and job.status == "cancelled":
                    logger.info(
                        f"Job {job_id} was cancelled, stopping Nextflow monitoring"
                    )
                    break

                # Give up rather than poll a wedged job forever (BACKLOG 359). Checked
                # after the read above so the deadline can never overwrite a job that
                # some other path already finished.
                if time.monotonic() >= deadline:
                    current_status = getattr(job, "status", None)
                    if current_status in TERMINAL_JOB_STATUSES:
                        logger.warning(
                            f"Nextflow wait for job {job_id} hit its "
                            f"{max_wait_seconds:g}s deadline, but the job is already "
                            f"{current_status}; leaving that status alone"
                        )
                        break

                    logger.error(
                        f"Nextflow job {job_key} for job {job_id} exceeded the "
                        f"{max_wait_seconds:g}s wait deadline; marking it failed"
                    )
                    job_service.update_job(job_id, JobUpdate(status=JobStatus.FAILED))
                    log_data = JobLogCreate(
                        step_name="nextflow_executor",
                        log_level=LogLevel.ERROR,
                        message=(
                            "Nextflow wait deadline exceeded: the pipeline did not "
                            f"reach a terminal state within {max_wait_seconds:g} "
                            "seconds "
                            f"(NEXTFLOW_MAX_WAIT_SECONDS={max_wait_seconds:g}). "
                            "Marking the job failed. Raise NEXTFLOW_MAX_WAIT_SECONDS "
                            "if this input legitimately needs longer."
                        ),
                    )
                    job_service.log_job_event(job_id, log_data)
                    break

                # Check Nextflow job status (off the event loop)
                response = await asyncio.to_thread(
                    requests.get, f"{nextflow_url}/status/{job_key}", timeout=30
                )
                if response.status_code == 200:
                    status_data = response.json()

                    # Log Nextflow status for monitoring purposes
                    status = status_data.get("status", "unknown")
                    message = status_data.get("message", "Processing...")

                    # Only log significant status changes to avoid spam
                    # Only log when status changes or when it's a final status
                    if status in ["completed", "failed", "cancelled"]:
                        log_data = JobLogCreate(
                            step_name="nextflow_executor",
                            log_level=LogLevel.INFO,
                            message=f"Nextflow executor: {message}",
                        )
                        job_service.log_job_event(job_id, log_data)

                    # Check if completed
                    if status_data.get("status") == "completed":
                        logger.info(f"Nextflow job {job_key} completed successfully")

                        # Log that Nextflow execution completed
                        log_data = JobLogCreate(
                            step_name="nextflow_executor",
                            log_level=LogLevel.INFO,
                            message="Nextflow pipeline completed - proceeding to report generation",
                        )
                        job_service.log_job_event(job_id, log_data)

                        # Handle final stages (report generation)
                        await handle_final_stages_progression(
                            job_service, job_id, outdir
                        )
                        break
                    elif status_data.get("status") == "failed":
                        error_msg = status_data.get("error", "Nextflow job failed")
                        logger.error(f"Nextflow job {job_key} failed: {error_msg}")

                        # Update workflow status to failed
                        workflow_update = JobUpdate(status=JobStatus.FAILED)
                        job_service.update_job(job_id, workflow_update)

                        log_data = JobLogCreate(
                            step_name=None,
                            log_level=LogLevel.ERROR,
                            message=f"Nextflow job failed: {error_msg}",
                        )
                        job_service.log_job_event(job_id, log_data)
                        break
                    elif status_data.get("status") == "cancelled":
                        logger.info(f"Nextflow job {job_key} was cancelled")

                        # Breaking out without a write left the job at 'running'
                        # forever: this poll is the only thing watching the run. Guard
                        # the write the same way the deadline does (bff00ad) —
                        # update_job has no terminal-state guard of its own, and a
                        # container callback or app.main may already have finished this
                        # job between the read above and this poll.
                        current_status = getattr(job, "status", None)
                        if current_status in TERMINAL_JOB_STATUSES:
                            logger.warning(
                                f"Nextflow job {job_key} reported cancelled, but job "
                                f"{job_id} is already {current_status}; leaving that "
                                "status alone"
                            )
                            break

                        job_service.update_job(
                            job_id, JobUpdate(status=JobStatus.CANCELLED)
                        )
                        log_data = JobLogCreate(
                            step_name="nextflow_executor",
                            log_level=LogLevel.WARN,
                            message=(
                                "Nextflow reported the pipeline cancelled; marking the "
                                "job cancelled."
                            ),
                        )
                        job_service.log_job_event(job_id, log_data)
                        break

                # Wait before next check, but never sleep past the deadline
                await asyncio.sleep(min(5, max(0.0, deadline - time.monotonic())))

            except requests.RequestException as e:
                logger.warning(f"Error checking Nextflow status: {e}")
                await asyncio.sleep(min(15, max(0.0, deadline - time.monotonic())))

    except Exception as e:
        logger.error(f"Error waiting for Nextflow completion: {e}")
        workflow_update = JobUpdate(status=JobStatus.FAILED)
        job_service.update_job(job_id, workflow_update)

        log_data = JobLogCreate(
            step_name=None,
            log_level=LogLevel.ERROR,
            message=f"Error waiting for completion: {str(e)}",
        )
        job_service.log_job_event(job_id, log_data)


async def process_file_nextflow_background_with_db(
    file_path: str,
    patient_id: str,
    data_id: str,
    workflow: dict,
    sample_identifier: Optional[str] = None,
    job_id: Optional[str] = None,
):
    """
    WRAPPER FUNCTION: Creates database session and delegates to core implementation.

    This is the function that should be called from background tasks. It handles
    database session lifecycle management and delegates to the core implementation below.

    Args:
        file_path: Path to the uploaded file
        patient_id: Patient identifier
        data_id: Genetic data record ID
        workflow: Workflow configuration dictionary
        sample_identifier: Optional sample identifier
        job_id: Optional workflow ID for tracking
    """
    db = SessionLocal()
    try:
        await process_file_nextflow_background(
            file_path, patient_id, data_id, workflow, db, sample_identifier, job_id
        )
    finally:
        db.close()


async def process_file_nextflow_background(
    file_path: str,
    patient_id: str,
    data_id: str,
    workflow: dict,
    db: Session,
    sample_identifier: Optional[str] = None,
    job_id: Optional[str] = None,
):
    """
    CORE IMPLEMENTATION: Execute the PGx pipeline via the Nextflow runner service.

    This function contains the actual workflow logic and requires a database session
    to be passed in. It should NOT be called directly from background tasks - use
    process_file_nextflow_background_with_db() instead.

    Args:
        file_path: Path to the uploaded file
        patient_id: Patient identifier
        data_id: Genetic data record ID
        workflow: Workflow configuration dictionary
        db: Database session (must be provided)
        sample_identifier: Optional sample identifier
        job_id: Optional workflow ID for tracking
    """
    job_service = JobService(db)

    try:
        # Get the workflow if job_id is provided
        if job_id:
            job_obj = job_service.get_job(job_id)
            if not job_obj:
                logger.error(f"Job {job_id} not found")
                return

            # Check for cancellation before starting
            if job_obj.status == "cancelled":
                logger.info(f"Job {job_id} was cancelled before processing started")
                # Schedule delayed cleanup to ensure any partial files are removed
                task = asyncio.create_task(
                    delayed_cleanup_on_cancellation(job_id, job_obj.job_metadata)
                )
                # Add a name for easier debugging
                task.set_name(f"delayed_cleanup_{job_id}")
                return
        else:
            logger.error("No job_id provided for background processing")
            return

        # Update header analysis step
        step_update = JobStepUpdate(status=StepStatus.RUNNING)
        job_service.update_job_step(job_id, "header_analysis", step_update)

        # Inspect file header
        try:
            header_json = inspect_header(file_path)
            header_record_id = save_genomic_header(
                db,
                file_path,
                (workflow.get("file_type") or "UNKNOWN").upper(),
                header_json,
            )

            # Persist filtered header text (canonical contigs only) into patient reports dir
            try:
                raw_header = extract_raw_header_text(file_path)
                if raw_header is not None:
                    filtered_header = filter_header_to_canonical_contigs(raw_header)
                    patient_dir = (
                        Path(os.getenv("REPORT_DIR", "/data/reports"))
                        / str(patient_id)
                        / str(job_id)
                    )
                    patient_dir.mkdir(parents=True, exist_ok=True)
                    header_txt_path = patient_dir / f"{data_id}.header.txt"
                    with open(header_txt_path, "w", encoding="utf-8") as hf:
                        hf.write(filtered_header)
            except Exception as _header_txt_err:
                logger.debug(
                    f"Header text write skipped due to error: {_header_txt_err}"
                )

            # Derive Sample ID from header if available. The VCF header is
            # attacker-controlled, so this string reaches the pipeline the same way the
            # user-entered sample_identifier does - filter it through the same allowlist.
            # An unusual header name is dropped (job proceeds without it) rather than
            # trusted into the pipeline's shell.
            header_sample_identifier = None
            try:
                if isinstance(header_json, dict):
                    samples_list = header_json.get("samples") or []
                    if isinstance(samples_list, list) and samples_list:
                        first_sample = samples_list[0]
                        if isinstance(first_sample, str) and first_sample.strip():
                            header_sample_identifier = sanitize_optional_pipeline_token(
                                first_sample
                            )
            except Exception:
                header_sample_identifier = None

            # Complete header analysis step
            step_update = JobStepUpdate(
                status=StepStatus.COMPLETED,
                output_data={"header_record_id": header_record_id},
            )
            job_service.update_job_step(job_id, "header_analysis", step_update)

            log_data = JobLogCreate(
                step_name="header_analysis",
                log_level=LogLevel.INFO,
                message="Header analysis completed successfully",
            )
            job_service.log_job_event(job_id, log_data)

        except Exception as e:
            logger.error(f"Header analysis failed: {e}")
            step_update = JobStepUpdate(
                status=StepStatus.FAILED, error_details={"error": str(e)}
            )
            job_service.update_job_step(job_id, "header_analysis", step_update)

            workflow_update = JobUpdate(status=JobStatus.FAILED)
            job_service.update_job(job_id, workflow_update)

            log_data = JobLogCreate(
                step_name="header_analysis",
                log_level=LogLevel.ERROR,
                message=f"Header analysis failed: {str(e)}",
            )
            job_service.log_job_event(job_id, log_data)
            return

        # Submit to Nextflow
        nextflow_url = os.getenv("NEXTFLOW_RUNNER_URL", "http://nextflow:5055")

        try:
            # Determine input type and reference from workflow. Passed through as
            # detected, never renamed: main.nf stages the file verbatim, so a payload
            # that disagrees with the file on disk buys a swallowed sidecar error
            # instead of a refusal (see NEXTFLOW_INPUT_TYPES above).
            input_type = workflow.get("file_type", "vcf")

            # The build the pipeline analyses against. For a lifted GRCh37/hg19 VCF
            # this is forced to hg38 (the file is GRCh38 by the time PyPGx/PharmCAT
            # run) regardless of the uploader's reference_genome selection -- see
            # analysis_reference().
            reference = analysis_reference(workflow)

            # The build the file itself is expressed against, DETECTED from its
            # header by FileProcessor (which sets source_build alongside
            # needs_liftover for a GRCh37/hg19 VCF). Deliberately not the
            # reference_genome form field: that defaults to hg38 no matter what the
            # file is, and keying the lift off it would mean never lifting anything.
            # Sent only when a lift is actually planned; blank means "no liftover"
            # to both the runner and main.nf. Sanitised as defence in depth - the
            # value is header-derived, but it ends up interpolated into main.nf's
            # LiftoverVCF shell block, the same trip reference makes.
            source_build = ""
            if workflow.get("needs_liftover"):
                source_build = (
                    sanitize_optional_pipeline_token(
                        str(workflow.get("source_build") or "")
                    )
                    or ""
                )

            # Determine skip flags based on workflow needs (after user overrides)
            skip_hla = "true" if not workflow.get("needs_hla", False) else "false"
            skip_pypgx = "true" if not workflow.get("needs_pypgx", False) else "false"
            skip_gatk = "true" if not workflow.get("needs_gatk", False) else "false"
            skip_report = "true" if not workflow.get("needs_report", True) else "false"
            skip_mtdna = "true" if not workflow.get("needs_mtdna", False) else "false"

            # Debug logging for service states
            logger.info(
                f"User toggle states: optitype={workflow.get('optitype_enabled')}, "
                f"gatk={workflow.get('gatk_enabled')}, pypgx={workflow.get('pypgx_enabled')}, "
                f"report={workflow.get('report_enabled')}, mtdna={workflow.get('mtdna_enabled')}"
            )
            logger.info(
                f"Workflow needs (after user overrides): needs_hla={workflow.get('needs_hla')}, "
                f"needs_gatk={workflow.get('needs_gatk')}, needs_pypgx={workflow.get('needs_pypgx')}, "
                f"needs_report={workflow.get('needs_report')}, needs_mtdna={workflow.get('needs_mtdna')}"
            )
            logger.info(
                f"Skip flags: skip_hla={skip_hla}, skip_pypgx={skip_pypgx}, "
                f"skip_gatk={skip_gatk}, skip_report={skip_report}, skip_mtdna={skip_mtdna}"
            )

            # Prepare Nextflow payload matching NextflowRunRequest
            # Compute effective sample identifier precedence:
            # 1) User-entered sample_identifier  2) Header-derived sample  3) None
            effective_sample_identifier = (
                str(sample_identifier).strip()
                if (sample_identifier and str(sample_identifier).strip())
                else None
            ) or header_sample_identifier

            # Display / path identity: report_id = job_id (137c)

            # Persist sample IDs so final-stage report generation can read them
            # without relying on locals() across coroutine boundaries.
            eff_absent = False
            eff_unspec = False
            if job_id:
                try:
                    job_obj = job_service.get_job(job_id)
                    if job_obj:
                        meta = dict(job_obj.job_metadata or {})
                        if header_sample_identifier:
                            meta["header_sample_identifier"] = header_sample_identifier
                        if effective_sample_identifier:
                            meta["sample_identifier"] = effective_sample_identifier
                        eff_absent = bool(meta.get("pharmcat_absent_to_ref", False))
                        eff_unspec = bool(
                            meta.get("pharmcat_unspecified_to_ref", False)
                        )
                        job_service.update_job(job_id, JobUpdate(metadata=meta))
                except Exception as meta_err:
                    logger.debug(
                        "Could not persist sample identifiers on job %s: %s",
                        job_id,
                        meta_err,
                        exc_info=True,
                    )

            payload = {
                "input": file_path,
                "input_type": input_type,
                "patient_id": patient_id,
                "report_id": str(job_id),  # display report_id = job_id
                "reference": reference,
                "outdir": f"/data/reports/{patient_id}/{job_id}",
                "job_id": str(job_id),  # NOT patient_id
                "skip_hla": skip_hla,
                "skip_pypgx": skip_pypgx,
                "skip_gatk": skip_gatk,
                "skip_report": skip_report,
                "skip_mtdna": skip_mtdna,
                "sample_identifier": effective_sample_identifier,
                "pharmcat_absent_to_ref": "true" if eff_absent else "false",
                "pharmcat_unspecified_to_ref": "true" if eff_unspec else "false",
                "source_build": source_build,
            }

            # Submit job to Nextflow
            response = requests.post(f"{nextflow_url}/run", json=payload, timeout=30)
            if response.status_code != 200:
                raise RuntimeError(f"Nextflow submission failed: {response.text}")

            job_data = response.json()
            job_key = job_data.get("job_key")

            if not job_key:
                raise RuntimeError("No job key returned from Nextflow")

            logger.info(f"Submitted Nextflow job {job_key} for job {job_id}")

            # Wait for completion
            await wait_for_nextflow_completion(
                job_service,
                job_id,
                nextflow_url,
                job_key,
                job_data.get("outdir", f"/data/reports/{patient_id}/{job_id}"),
            )

        except Exception as e:
            logger.error(f"Nextflow execution failed: {e}")
            workflow_update = JobUpdate(status=JobStatus.FAILED)
            job_service.update_job(job_id, workflow_update)

            log_data = JobLogCreate(
                step_name=None,
                log_level=LogLevel.ERROR,
                message=f"Nextflow execution failed: {str(e)}",
            )
            job_service.log_job_event(job_id, log_data)
            return

    except Exception as e:
        logger.error(f"Error in Nextflow background processing: {e}")
        workflow_update = JobUpdate(status=JobStatus.FAILED)
        job_service.update_job(job_id, workflow_update)

        log_data = JobLogCreate(
            step_name=None,
            log_level=LogLevel.ERROR,
            message=f"Background processing error: {str(e)}",
        )
        job_service.log_job_event(job_id, log_data)


@router.post("/genomic-data", response_model=UploadResponse)
async def upload_genomic_data(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    sample_identifier: Optional[str] = Form(None),
    reference_genome: Optional[str] = Form("hg38"),
    optitype_enabled: Optional[str] = Form(None),
    gatk_enabled: Optional[str] = Form(None),
    pypgx_enabled: Optional[str] = Form(None),
    report_enabled: Optional[str] = Form(None),
    mtdna_enabled: Optional[str] = Form(None),
    pharmcat_absent_to_ref: Optional[str] = Form(None),
    pharmcat_unspecified_to_ref: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """
    Upload genomic data files for pharmacogenomic analysis.

    This endpoint handles the upload of genomic data files (VCF, BAM, CRAM, SAM)
    and initiates the Nextflow-based processing pipeline.

    Supported file types:
    - VCF: Direct processing through PyPGx and PharmCAT. A GRCh37/hg19 VCF (detected
      from the file's own header) is lifted over to GRCh38 first — GATK Picard
      LiftoverVcf in the gatk-api container, UCSC hg19-to-hg38 chain. Variants that
      cannot be lifted are dropped, and the run fails rather than continuing if an
      implausibly large share of the file fails to lift.
    - BAM/CRAM/SAM: BAM is processed by ZaroHLA then PyPGx, then PharmCAT. CRAM/SAM processed through GATK first for conversion to BAM.
    - FASTQ: rejected with 400. ZaroPGx ships no aligner, so raw reads cannot reach a BAM
      (gatk-api's /align-fastq answers 501); align them yourself and upload the BAM/CRAM/SAM.
    - FASTA/BED/unrecognised formats: rejected with 400. The pipeline has no working
      branch for them, so accepting one could only ever produce a failed job.
    - 23andMe/AncestryDNA: rejected with 400, by decision rather than for want of a
      converter. A chip carries under a third of PharmCAT's positions and cannot show
      a gene duplication, and PyPGx reads the positions it lacks as homozygous
      reference — so the report would be confidently wrong rather than incomplete.
    - GVCF: genotyped, then processed exactly like a VCF. Detected from the name
      (.gvcf, .g.vcf, either gzipped) and, whatever the name, from a ##GVCFBlock record
      or the reference-confidence ALT allele in the content — the stored name is
      sanitised, so it can reach disk with no usable extension at all, and a DeepVariant
      gVCF carries no ##GVCFBlock whatever it is called. The pipeline's `gvcf` branch
      POSTs the file to gatk-api's /gvcf-to-vcf, which runs GATK GenotypeGVCFs twice —
      once with --include-non-variant-sites over PharmCAT's own position list, once
      with -XL of the same file — and joins them with `bcftools concat -a`. The first
      pass is why this beats converting it yourself: the homozygous-reference genotypes
      at pharmacogene positions come from the file's own reference-confidence blocks, so
      they are called data rather than PharmCAT's --absent-to-ref fabrication. The
      output is never named *.g.vcf*, because PharmCAT condemns that filename before
      reading a byte. Two shapes are still rejected with 400: reference blocks that are
      <*> rather than <NON_REF> (GenotypeGVCFs stops on them), and a GRCh37/hg19 gVCF
      (PharmCAT's position list is GRCh38-only, so the pass that makes the lane
      worthwhile has nothing to run against — genotype it yourself and upload the
      GRCh37 VCF, which IS lifted).
    - BCF: converted, then processed exactly like a VCF. The pipeline's `bcf` branch
      POSTs the file to gatk-api's /bcf-to-vcf (`bcftools view -O z`, then a tabix
      index) and only continues once that endpoint has vouched for a non-empty BGZF
      result — a real re-encode, not a rename, because the PharmCAT sidecar gates on
      the filename and swallows its own 400 behind a `|| true`. BCF and VCF hold the
      same records, so the conversion costs no accuracy; every VCF caveat then applies,
      liftover of a GRCh37/hg19 BCF included. A gVCF written as a BCF takes the gVCF
      lane instead (##GVCFBlock is read out of the binary header); that lane's staging
      step is bcftools, so it reads a BCF for free.

    Only the first uploaded data file is analysed; any further data file is reported as
    ignored in the workflow warnings. Index files (.bai, .crai, .csi, .tbi, .idx) may be
    uploaded alongside it. Currently only hg38/GRCh38 reference genome is fully supported.
    """
    try:
        # Reject shell-metacharacter payloads at the boundary, before any value can be
        # persisted or reach the Nextflow pipeline's shell. reference_genome is always
        # present (defaults to hg38); sample_identifier is optional - only validate it
        # when the user actually supplied one, otherwise it falls through to a UUID.
        reference_genome = validate_pipeline_token(
            reference_genome or "hg38", "reference_genome"
        )
        if sample_identifier and sample_identifier.strip():
            sample_identifier = validate_pipeline_token(
                sample_identifier, "sample_identifier"
            )

        # When the caller doesn't send the mtdna_enabled form field at all, fall
        # back to the operator's own MTDNA_ENABLED setting rather than silently
        # defaulting to "on". Without this, an operator who disables the mtdna
        # service (MTDNA_ENABLED=false, e.g. because it isn't deployed) still
        # gets jobs that try to call it -- MTDNA_ENABLED previously only fed the
        # services-config payload and the health check, never needs_mtdna. An
        # explicit form field (present, either value) still wins unchanged;
        # deferred import avoids a circular import with app.main.
        from app.main import MTDNA_ENABLED

        effective_mtdna_enabled = (
            mtdna_enabled
            if mtdna_enabled is not None
            else ("true" if MTDNA_ENABLED else "false")
        )

        # Process uploaded files
        result = await file_processor.process_files(
            files,
            reference_genome,
            optitype_enabled=optitype_enabled,
            gatk_enabled=gatk_enabled,
            pypgx_enabled=pypgx_enabled,
            report_enabled=report_enabled,
            mtdna_enabled=effective_mtdna_enabled,
        )

        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])

        # Act on the unsupported verdict instead of only reporting it. Refuse before any
        # patient/job row exists, so an input the pipeline cannot run costs the user an
        # immediate, actionable message rather than a queued job that dies later.
        unanalysable = _unanalysable_upload_reason(result["workflow"])
        if unanalysable:
            logger.warning(
                "Refusing unanalysable upload (file_type=%s): %s",
                result["workflow"].get("file_type"),
                unanalysable,
            )
            # process_files() saves before it analyses, so the bytes are already on disk.
            # Nothing will ever collect them: the cleanup service sweeps
            # /data/uploads/{patient_id}, and a refusal mints no patient. Left alone, a
            # refused 200GB FASTQ sits in the upload directory forever.
            _discard_refused_upload(result.get("file_paths"))
            raise HTTPException(status_code=400, detail=unanalysable)

        eff_absent, eff_unspec = resolve_assume_ref_flags(
            form_absent=pharmcat_absent_to_ref,
            form_unspecified=pharmcat_unspecified_to_ref,
            env_absent=os.environ.get("PHARMCAT_ABSENT_TO_REF"),
            env_unspecified=os.environ.get("PHARMCAT_UNSPECIFIED_TO_REF"),
        )

        # Create patient record (DB assigns actual_patient_id; no client-side ID pre-mint)
        patient_identifier = (
            sample_identifier if sample_identifier else str(uuid.uuid4())
        )
        actual_patient_id = create_patient(db, patient_identifier)

        # Register genetic data
        primary_file_path = result["file_paths"][0]
        file_analysis = result["file_analysis"]
        data_id = register_genetic_data(
            db,
            actual_patient_id,  # Use the actual patient ID returned from create_patient
            file_analysis.file_type.value,  # file_type
            primary_file_path,  # file_path
            False,  # is_supplementary (boolean)
        )

        # Create workflow (steps minted from recipe registry via create_job)
        wf = result["workflow"]
        options = WorkflowOptions(
            needs_gatk=wf.get("needs_gatk", False),
            needs_alignment=wf.get("needs_alignment", False),
            needs_pypgx=wf.get("needs_pypgx", False),
            needs_pypgx_bam2vcf=wf.get("needs_pypgx_bam2vcf", False),
            needs_hla=wf.get("needs_hla", False),
            needs_mtdna=wf.get("needs_mtdna", False),
            needs_report=wf.get("needs_report", True),
            needs_conversion=wf.get("needs_conversion", False),
            needs_gvcf_genotyping=wf.get("needs_gvcf_genotyping", False),
            needs_liftover=wf.get("needs_liftover", False),
            is_provisional=wf.get("is_provisional", False),
            unsupported=wf.get("unsupported", False),
            unsupported_reason=wf.get("unsupported_reason"),
            requested_reference=(
                wf.get("requested_reference")
                or wf.get("reference")
                or wf.get("reference_genome")
            ),
            recommendations=list(wf.get("recommendations") or []),
            warnings=list(wf.get("warnings") or []),
        )

        job_service = JobService(db)
        job = job_service.create_job(
            JobCreate(
                name=f"Genomic Analysis - {sample_identifier or 'Unknown Sample'}",
                description=f"Pharmacogenomic analysis workflow for {file_analysis.file_type.value} file",
                workflow_type="genomic_analysis",
                options=options,
                metadata={
                    "patient_id": actual_patient_id,
                    "data_id": data_id,
                    "file_paths": result["file_paths"],
                    "pharmcat_absent_to_ref": eff_absent,
                    "pharmcat_unspecified_to_ref": eff_unspec,
                    "file_analysis": {
                        "file_type": file_analysis.file_type.value,
                        "is_compressed": file_analysis.is_compressed,
                        "has_index": file_analysis.has_index,
                        "file_size": file_analysis.file_size,
                        "error": file_analysis.error,
                        "is_valid": file_analysis.is_valid,
                        "validation_errors": file_analysis.validation_errors,
                        "vcf_info": (
                            file_analysis.vcf_info.__dict__
                            if file_analysis.vcf_info
                            else None
                        ),
                    },
                    # workflow_type / workflow dual-write happens inside create_job
                },
            )
        )

        # Start the workflow
        job_service.update_job(job.id, JobUpdate(status=JobStatus.RUNNING))

        # Schedule background processing: Always use Nextflow
        background_tasks.add_task(
            process_file_nextflow_background_with_db,
            primary_file_path,
            str(actual_patient_id),
            str(data_id),
            result["workflow"],
            (
                str(sample_identifier).strip()
                if (sample_identifier and sample_identifier.strip())
                else None
            ),
            str(job.id),  # Pass workflow ID
        )

        # Convert dataclass to Pydantic model
        file_analysis = result["file_analysis"]
        vcf_info = None
        if file_analysis.vcf_info:
            vcf_info = VCFHeaderInfo(
                reference_genome=file_analysis.vcf_info.reference_genome,
                sequencing_platform=file_analysis.vcf_info.sequencing_platform,
                sequencing_profile=file_analysis.vcf_info.sequencing_profile,
                has_index=file_analysis.vcf_info.has_index,
                is_bgzipped=file_analysis.vcf_info.is_bgzipped,
                contigs=file_analysis.vcf_info.contigs,
                sample_count=file_analysis.vcf_info.sample_count,
                variant_count=file_analysis.vcf_info.variant_count,
                reference_genome_ambiguous=file_analysis.vcf_info.reference_genome_ambiguous,
                reference_genome_candidates=file_analysis.vcf_info.reference_genome_candidates,
            )

        # Create workflow info (nested options for UI / clients)
        workflow_info = WorkflowInfo(
            workflow_type="genomic_analysis",
            options=options,
        )

        # Create response
        response = UploadResponse(
            data_id=str(data_id),
            job_id=str(job.id),
            file_type=result["workflow"]["file_type"],
            status="processing",
            message="Files uploaded successfully. Processing started.",
            analysis_info=PydanticFileAnalysis(
                file_type=FileType(result["workflow"]["file_type"]),
                is_compressed=file_analysis.is_compressed,
                has_index=file_analysis.has_index,
                file_size=file_analysis.file_size,
                vcf_info=vcf_info,
                is_valid=file_analysis.is_valid,
                validation_errors=file_analysis.validation_errors,
            ),
            workflow=workflow_info,
        )

        logger.info(f"Upload successful for patient {actual_patient_id}, job {job.id}")
        return response

    except HTTPException:
        # A deliberate status (the 400 above, or one raised by a dependency) is the
        # answer, not an error to re-wrap: HTTPException is an Exception, so without
        # this every 4xx left here as a 500.
        raise
    except Exception as e:
        logger.error(f"Upload failed: {e}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


async def get_upload_status_by_data_id(data_id: str, db: Session):
    """
    Look up the most recent job whose job_metadata["data_id"] matches, then
    return the same status assembly as get_upload_status(job_id).
    """
    from app.api.db import Job

    jobs = db.query(Job).order_by(Job.created_at.desc()).all()
    matched_job_id = None
    for job in jobs:
        meta = job.job_metadata or {}
        if str(meta.get("data_id", "")) == str(data_id):
            matched_job_id = str(job.id)
            break

    if not matched_job_id:
        raise HTTPException(status_code=404, detail="No job found for data_id")

    return await get_upload_status(matched_job_id, db)


@router.get("/status/{job_id}")
async def get_upload_status(job_id: str, db: Session = Depends(get_db)):
    """
    Get the processing status of a job using the new monitoring system.
    Canonical status path is by job_id; /status/{data_id} on main looks up via
    get_upload_status_by_data_id.
    """
    try:
        job_service = JobService(db)

        # Try to get workflow by ID
        job = job_service.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Workflow not found")

        # Get workflow steps
        steps = job_service.get_job_steps(job_id)

        # Convert steps to dictionary format for progress calculator
        steps_dict = [
            {
                "step_name": step.step_name,
                "status": step.status,  # status is already a string from database
                "step_order": step.step_order,
                "container_name": step.container_name,
                "output_data": step.output_data,  # Include output_data for container progress
                "metadata": step.metadata,  # Include metadata for container progress
            }
            for step in steps
        ]

        # Get workflow metadata for configuration
        metadata = job.job_metadata or {}
        workflow_config = metadata.get("workflow", {})

        # Calculate progress using centralized calculator
        progress_calculator = WorkflowProgressCalculator()
        progress_info = progress_calculator.calculate_progress_from_steps(
            steps_dict, workflow_config, job_id
        )

        progress = progress_info.progress_percentage
        current_stage = progress_info.stage.value

        # Get workflow logs
        logs = job_service.get_job_logs(job_id)
        latest_message = progress_info.message

        # Extract report URLs from metadata for completed workflows
        report_urls = {}
        if job.status == "completed" and metadata.get("reports"):
            reports = metadata["reports"]
            logger.info(
                f"Found report data in workflow metadata: {list(reports.keys())}"
            )

            # Extract all report URLs to top level for frontend compatibility
            if "pdf_report_url" in reports:
                report_urls["pdf_report_url"] = reports["pdf_report_url"]
            if "html_report_url" in reports:
                report_urls["html_report_url"] = reports["html_report_url"]
            if "interactive_html_report_url" in reports:
                report_urls["interactive_html_report_url"] = reports[
                    "interactive_html_report_url"
                ]
            if "pharmcat_html_report_url" in reports:
                report_urls["pharmcat_html_report_url"] = reports[
                    "pharmcat_html_report_url"
                ]
            if "pharmcat_json_report_url" in reports:
                report_urls["pharmcat_json_report_url"] = reports[
                    "pharmcat_json_report_url"
                ]
            if "pharmcat_tsv_report_url" in reports:
                report_urls["pharmcat_tsv_report_url"] = reports[
                    "pharmcat_tsv_report_url"
                ]
            if "mtdna_report_url" in reports:
                report_urls["mtdna_report_url"] = reports["mtdna_report_url"]
            if "mtdna_haplogroups_url" in reports:
                report_urls["mtdna_haplogroups_url"] = reports["mtdna_haplogroups_url"]
            if "mtdna_vcf_url" in reports:
                report_urls["mtdna_vcf_url"] = reports["mtdna_vcf_url"]

        # Create response
        response = {
            "job_id": job_id,
            "status": job.status,  # status is already a string from database
            "progress": progress,
            "message": latest_message,
            "current_stage": current_stage,
            "data": {
                "job_id": job.id,
                "patient_id": metadata.get("patient_id"),
                "data_id": metadata.get("data_id"),
                "steps": [
                    {
                        "name": step.step_name,
                        "status": step.status,  # status is already a string from database
                        "order": step.step_order,
                        "container": step.container_name,
                    }
                    for step in steps
                ],
            },
            **report_urls,  # Include report URLs at top level
        }

        logger.info(f"Status response for job {job_id}: {response}")
        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting status for job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting status: {str(e)}")


@router.post("/inspect-header")
async def inspect_file_header(
    file: UploadFile = File(...), db: Session = Depends(get_db)
):
    """
    Inspect the header of a genomic file without processing the full analysis.
    This endpoint allows users to preview file header information before
    committing to the full upload and analysis process.
    """
    try:
        # Save uploaded file temporarily. The suffix carries the uploaded name because
        # inspect_header branches on the extension - but `file.filename` is a
        # client-controlled string on its way into a filesystem path, so it goes through
        # the same sanitiser the upload lane uses for the stored upload
        # (file_processor.safe_upload_basename). Without it a name like `../../x.vcf` or
        # `*.vcf` shapes the path NamedTemporaryFile creates. The response below still
        # echoes the raw name: that is display, not a path.
        temp_file = tempfile.NamedTemporaryFile(
            delete=False, suffix=f"_{safe_upload_basename(file.filename)}"
        )
        try:
            # Stream to disk in bounded chunks rather than buffering the whole
            # upload in memory -- the same UPLOAD_CHUNK_BYTES idiom the sidecars
            # use (see tests/test_sidecar_chunked_io.py). Header inspection still
            # needs the complete file on disk (inspect_header and
            # process_upload below both read the saved copy, not a prefix), so
            # this bounds memory, not I/O volume.
            file_size = 0
            while chunk := await file.read(UPLOAD_CHUNK_BYTES):
                temp_file.write(chunk)
                file_size += len(chunk)
            temp_file.close()

            # Inspect header
            header_info = inspect_header(temp_file.name)

            # Derive workflow analysis using the same backend logic (no Nextflow)
            # `refused` is the same verdict /upload/genomic-data would reach, so the
            # preview cannot promise a workflow the upload would then refuse. It is
            # deliberately not `unsupported`: that flag means "flagged", and only the
            # gate knows whether a flagged input is still run (see
            # _unanalysable_upload_reason), so the gate is asked rather than guessed at.
            compat_workflow = {
                "recommendations": [],
                "warnings": [],
                "unsupported": False,
                "unsupported_reason": None,
                "needs_conversion": False,
                "needs_gvcf_genotyping": False,
                "needs_liftover": False,
                "needs_gatk": False,
                "needs_alignment": False,
                "needs_hla": False,
                "needs_pypgx": False,
                "needs_pypgx_bam2vcf": False,
                "needs_mtdna": False,
                "refused": False,
                "refusal_reason": None,
            }
            try:
                workflow_result = await file_processor.process_upload(
                    str(temp_file.name)
                )
                if workflow_result.get("status") == "success":
                    wf = workflow_result.get("workflow", {})
                    refusal = _unanalysable_upload_reason(wf)
                    compat_workflow = {
                        "recommendations": wf.get("recommendations", []),
                        "warnings": wf.get("warnings", []),
                        "unsupported": wf.get("unsupported", False),
                        "unsupported_reason": wf.get("unsupported_reason"),
                        # Lets the preview panel draw the conversion step for a BCF
                        # or a gVCF, the same way needs_liftover below does for a
                        # GRCh37 file. Without it the preview drew a plan one step
                        # shorter than the plan that runs. The second flag is what
                        # decides which of the two conversions is named, matching
                        # buildPlannedWorkflowHTML and workflow_registry.
                        "needs_conversion": wf.get("needs_conversion", False),
                        "needs_gvcf_genotyping": wf.get("needs_gvcf_genotyping", False),
                        # The rest of the needs_* flags the planned-workflow panel
                        # reads. They are forwarded because this endpoint has already
                        # run the real determine_workflow -- the same call the upload
                        # makes -- so withholding its answer only forced the panel to
                        # re-derive it in JavaScript from the file extension.
                        #
                        # That guessing is what this fixes. The panel's fallbacks
                        # ("bam/cram/sam implies HLA", and so on) are a second copy of
                        # a decision that lives in FileProcessor.determine_workflow,
                        # and the two drifted: CRAM and SAM did not set needs_hla for
                        # a long time while the panel drew the HLA step for them
                        # regardless. needs_mtdna was worse -- it has no fallback at
                        # all, so the pre-upload plan silently omitted a step that runs
                        # on every VCF, BAM, CRAM and SAM job, and the plan the user
                        # approved was one step shorter than the plan that ran.
                        "needs_gatk": wf.get("needs_gatk", False),
                        "needs_alignment": wf.get("needs_alignment", False),
                        "needs_hla": wf.get("needs_hla", False),
                        "needs_pypgx": wf.get("needs_pypgx", False),
                        "needs_pypgx_bam2vcf": wf.get("needs_pypgx_bam2vcf", False),
                        "needs_mtdna": wf.get("needs_mtdna", False),
                        # Lets the preview panel draw the "Liftover to GRCh38"
                        # step for a GRCh37/hg19 VCF, the same way the
                        # post-upload panel does off WorkflowOptions.
                        "needs_liftover": wf.get("needs_liftover", False),
                        "refused": refusal is not None,
                        "refusal_reason": refusal,
                    }
            except Exception as e:
                logger.debug(f"Header inspect workflow derivation failed: {e}")

            return {
                "status": "success",
                "success": True,
                "filename": file.filename,
                "file_size": file_size,
                "header_info": header_info,
                "compat": {"workflow": compat_workflow},
            }

        finally:
            # Clean up temp file
            if os.path.exists(temp_file.name):
                os.unlink(temp_file.name)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Header inspection failed: {e}")
        raise HTTPException(
            status_code=500, detail=f"Header inspection failed: {str(e)}"
        )


@router.get("/reports/job/{job_id}")
async def get_report_urls(job_id: str, db: Session = Depends(get_db)):
    """
    Get the report URLs for a completed job.
    """
    try:
        job_service = JobService(db)

        # First try to get workflow by ID (in case job_id is actually a job_id)
        job = job_service.get_job(job_id)

        # If not found by ID, try to find by name pattern
        if not job:
            # Look for job with name containing the job_id
            from sqlalchemy import and_

            from app.api.db import Job

            job = (
                db.query(Job)
                .filter(
                    and_(
                        Job.name.contains(job_id),
                        Job.status == JobStatus.COMPLETED,
                    )
                )
                .first()
            )

        if not job:
            raise HTTPException(status_code=404, detail="Workflow not found")

        if job.status != JobStatus.COMPLETED:
            raise HTTPException(status_code=400, detail="Workflow not completed")

        # Get report URLs from metadata
        metadata = job.job_metadata or {}
        reports = metadata.get("reports", {})

        # If no reports in metadata, try to construct URLs from patient_id + job_id
        if not reports:
            patient_id = metadata.get("patient_id")
            if patient_id:
                # Construct basic report URLs based on nested naming convention
                reports = {
                    "pdf_report_url": f"/reports/{patient_id}/{job_id}/{job_id}_pgx_report.pdf",
                    "html_report_url": f"/reports/{patient_id}/{job_id}/{job_id}_pgx_report_interactive.html",
                }

                # Check if PharmCAT reports exist and add them
                patient_dir = Path(REPORTS_DIR) / patient_id / str(job.id)
                if patient_dir.exists():
                    pharmcat_html = patient_dir / f"{job.id}_pgx_pharmcat.html"
                    pharmcat_json = patient_dir / f"{job.id}_pgx_pharmcat.json"
                    pharmcat_tsv = patient_dir / f"{job.id}_pgx_pharmcat.tsv"

                    if pharmcat_html.exists():
                        reports["pharmcat_html_report_url"] = (
                            f"/reports/{patient_id}/{job.id}/{pharmcat_html.name}"
                        )
                    if pharmcat_json.exists():
                        reports["pharmcat_json_report_url"] = (
                            f"/reports/{patient_id}/{job.id}/{pharmcat_json.name}"
                        )
                    if pharmcat_tsv.exists():
                        reports["pharmcat_tsv_report_url"] = (
                            f"/reports/{patient_id}/{job.id}/{pharmcat_tsv.name}"
                        )

                    # mtDNA-Server 2 artifacts, published under fixed names (no
                    # job.id prefix) -- see app/reports/generator.py.
                    mtdna_report = patient_dir / "mtdna_report.html"
                    mtdna_haplogroups = patient_dir / "mtdna_result.json"
                    mtdna_vcf = patient_dir / "mtdna_variants.vcf.gz"
                    if mtdna_report.exists():
                        reports["mtdna_report_url"] = (
                            f"/reports/{patient_id}/{job.id}/{mtdna_report.name}"
                        )
                    if mtdna_haplogroups.exists():
                        reports["mtdna_haplogroups_url"] = (
                            f"/reports/{patient_id}/{job.id}/{mtdna_haplogroups.name}"
                        )
                    if mtdna_vcf.exists():
                        reports["mtdna_vcf_url"] = (
                            f"/reports/{patient_id}/{job.id}/{mtdna_vcf.name}"
                        )

        return {
            # The resolved job's canonical UUID, not the raw path parameter: the URL may
            # spell the same id in any form uuid.UUID accepts, and it is job.id that the
            # report paths under /data/reports/{patient_id}/{job.id}/ are named with.
            "job_id": str(job.id),
            "status": "completed",
            "reports": reports,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting report URLs for job {job_id}: {e}")
        raise HTTPException(
            status_code=500, detail=f"Error getting report URLs: {str(e)}"
        )


@router.get("/reports/download/{patient_id}")
async def download_all_reports(
    patient_id: str, current_user: str = Depends(get_optional_user)
):
    """
    Download all reports for a patient as a ZIP file.
    """
    try:
        # Use the same path jail as individual file serving
        from app.api.utils.path_jail import resolve_under
        from app.main import REPORTS_DIR

        try:
            reports_dir = resolve_under(REPORTS_DIR, patient_id)
        except PermissionError:
            raise HTTPException(status_code=403, detail="Access denied")

        # Check if directory exists
        if not reports_dir.exists() or not reports_dir.is_dir():
            raise HTTPException(status_code=404, detail="Reports directory not found")

        # Create ZIP file
        zip_buffer = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")

        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            files_found = list(reports_dir.rglob("*"))
            logger.info(
                f"ZIP Download - Found {len(files_found)} files/directories in {reports_dir}"
            )

            for file_path in files_found:
                if file_path.is_file():
                    # Add file to ZIP with relative path
                    arcname = file_path.relative_to(reports_dir)
                    logger.info(f"ZIP Download - Adding file: {file_path.name}")
                    zip_file.write(file_path, arcname)

        zip_buffer.close()

        # Read ZIP file content
        with open(zip_buffer.name, "rb") as f:
            zip_content = f.read()

        # Clean up
        os.unlink(zip_buffer.name)

        # Return ZIP file
        return Response(
            content=zip_content,
            media_type="application/zip",
            headers={
                "Content-Disposition": f"attachment; filename=reports_{patient_id}.zip"
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating ZIP file for patient {patient_id}: {e}")
        raise HTTPException(
            status_code=500, detail=f"Error creating ZIP file: {str(e)}"
        )
