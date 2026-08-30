import os
import asyncio
import json
import logging
import re
import subprocess
import time
import uuid
import csv
import psutil
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, Any, List, Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from pydantic import BaseModel
import sys

sys.path.append("/job-client")
from job_client import JobClient

# Read (and created) before logging is configured because the progress-log handler
# below writes into DATA_DIR. Same ordering as gatk_api.py.
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
TEMP_DIR = DATA_DIR / "temp"
os.makedirs(TEMP_DIR, exist_ok=True)

# Uploads are streamed to disk in chunks of this size rather than read into memory --
# see Dockerfile's note on this service's single-worker choice: a whole-genome BAM
# ingested via a bare `await file.read()` blocks the one event loop for as long as
# the read takes, taking /health and the broadcast /cancel down with it. Same value
# as the gatk-api sidecar.
UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024

# --------------------------------------------------------------------------
# Bounded logging (BACKLOG 252). Same block, same values, same failure
# behaviour as every other ZaroPGx sidecar: docker/gatk-api/gatk_api.py,
# docker/nextflow/runner.py, docker/pharmcat/pharmcat.py,
# docker/pypgx/pypgx_wrapper.py and docker/zarohla/app.py.
# tests/test_log_rotation_252.py pins those five against one rule; this
# module follows the same rule.
#
# It is duplicated rather than imported: these are six separate images, and
# the logging block runs before sys.path is extended with the shared
# /job-client directory. See gatk_api.py for the full argument.
#
# This service's `running_processes` registry (below) is module-level state,
# same as zarohla's -- see this Dockerfile's CMD comment for why that forces
# --workers 1. That single-worker constraint is also what lets this write a
# single shared /data/mtdna_progress.log rather than a per-pid one: a
# RotatingFileHandler is not multi-process safe -- when one worker rolls the
# file over it renames the file out from under any other worker, whose lines
# then migrate down the .1/.2/... chain and are silently deleted past
# backupCount.
PROGRESS_LOG_PATH = os.getenv(
    "MTDNA_PROGRESS_LOG", str(DATA_DIR / "mtdna_progress.log")
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
logger = logging.getLogger("mtdna")
_warn_about_unopened_logs(logger)

sys.path.append("/mtdna-lib")
from mtdna.mt_rnr1 import MT_RNR1_ALLELES  # noqa: E402  (path set above)

# MitoBuild is imported here (not only in the alignment branch) because Task 8's
# _call_from_alignment compares against MitoBuild.HG19 and MitoBuild.GRCH38.
from mtdna.builds import (  # noqa: E402
    MitoBuild,
    classify_build,
    classify_from_mito_contig,
    plan_for,
)
from mtdna.mt_rnr1 import (  # noqa: E402
    MT_RNR1_SPAN,
    REFERENCE,
    VcfRecord,
    match_alleles,
    select_call,
)

MUTSERVE_JAR = "/opt/mutserve/mutserve.jar"
HAPLOGREP_JAR = "/opt/haplogrep/haplogrep3.jar"
HAPLOCHECK_JAR = "/opt/haplocheck/haplocheck.jar"
RCRS_FASTA = "/opt/mtdna-files/rcrs_mutserve.fasta"
PHYLOTREE = "phylotree-fu-rcrs@1.2"
CHAIN_PATH = "/reference/chain/hg19ToHg38.over.chain.gz"

# The pipeline release this image and the vendored files/ + report.Rmd all come
# from. They bump as one unit: a report.Rmd that disagrees with the tool output
# it reads fails silently, rendering a page with empty panels.
PIPELINE_VERSION = "v2.1.16"

running_processes: Dict[str, Dict[str, Any]] = {}


def _tool_versions() -> Dict[str, str]:
    """Versions the base image bakes in as env vars (see upstream Dockerfile)."""
    return {
        "mtdna-server-2": PIPELINE_VERSION,
        "mutserve": os.getenv("MUTSERVE_VERSION", "unknown"),
        "haplogrep3": os.getenv("HAPLOGREP_VERSION", "unknown"),
        "haplocheck": os.getenv("HAPLOCHECK_VERSION", "unknown"),
    }


def _publish_version_manifest() -> None:
    """Record versions where version_manager can find them.

    Same contract as every other sidecar: one JSON per tool under
    /data/versions, {"name": ..., "version": ...}, read by
    VersionManager._load_version_manifests. BACKLOG 375 asked for a version in
    the mtDNA citation; before this service existed there was nothing to
    resolve one from, and the old code published a hardcoded "2.1.16" for
    software that was not installed. Now it is, and this is where it comes from.

    Never fatal: a missing manifest costs the report a version string, not a run.
    """
    try:
        versions_dir = DATA_DIR / "versions"
        versions_dir.mkdir(parents=True, exist_ok=True)
        (versions_dir / "mtdna-server-2.json").write_text(
            json.dumps(
                {
                    "name": "mtDNA-server-2",
                    "version": PIPELINE_VERSION,
                    "components": _tool_versions(),
                }
            ),
            encoding="utf-8",
        )
        logger.info(f"Published mtDNA-server-2 version manifest: {PIPELINE_VERSION}")
    except Exception as exc:
        logger.warning(f"Could not publish the mtDNA version manifest: {exc}")


_publish_version_manifest()

app = FastAPI(title="ZaroPGx mtDNA-Server 2 API", version="1.0.0")


@app.get("/health")
async def health() -> Dict[str, Any]:
    missing = [
        path
        for path in (MUTSERVE_JAR, HAPLOGREP_JAR, HAPLOCHECK_JAR, RCRS_FASTA)
        if not os.path.exists(path)
    ]
    if missing:
        # Unhealthy rather than "healthy with a warning": every one of these is
        # required by some path, and a service that answers 200 without them
        # fails later, inside a run, where it costs more to diagnose.
        raise HTTPException(status_code=503, detail={"missing": missing})
    return {
        "status": "healthy",
        "service": "mtdna-server-2",
        "versions": _tool_versions(),
        "alleles_known": len(MT_RNR1_ALLELES),
    }


async def _run(argv: List[str], cwd: str, job_key: str) -> None:
    """Run argv, register it for /cancel, raise with stderr on failure.

    argv-only, never shell=True: the same rule gatk_api.py follows, so a
    patient_id or filename can never be read as shell.
    """
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    running_processes.setdefault(job_key, {})[argv[0]] = proc
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"{os.path.basename(argv[0])} failed: {stderr.decode()[:2000]}",
        )


# The mito contig may be spelled chrM, MT or M depending on the build; matched
# case-insensitively against the ##contig line's ID= field.
_MITO_CONTIG_NAMES = {"chrm", "mt", "m"}


def _read_mito_contig_header(vcf_path: str):
    """(name, length) of the VCF's mitochondrial ##contig line, or (None, None).

    Read with `bcftools view -h`. The mito contig may be spelled chrM, MT or M;
    take the first that appears. Length is parsed from the same line's
    `length=` field. Returns (None, None) when the header carries no mito
    contig, which is a real case -- some VCFs omit ##contig entirely -- and the
    caller then falls back to the build label.
    """
    result = subprocess.run(
        ["bcftools", "view", "-h", vcf_path],
        capture_output=True,
        text=True,
        check=True,
    )
    for line in result.stdout.splitlines():
        if not line.startswith("##contig="):
            continue
        id_match = re.search(r"ID=([^,>]+)", line)
        if not id_match or id_match.group(1).strip().lower() not in _MITO_CONTIG_NAMES:
            continue
        length_match = re.search(r"length=(\d+)", line)
        length = int(length_match.group(1)) if length_match else None
        return id_match.group(1), length
    return None, None


def _read_chrm_records(vcf_path: str) -> List[VcfRecord]:
    """Normalised chrM records inside MT-RNR1, from a bcftools query TSV."""
    low, high = MT_RNR1_SPAN
    records = []
    with open(vcf_path, "r", encoding="utf-8") as handle:
        for line in handle:
            pos, ref, alt = line.rstrip("\n").split("\t")[:3]
            if alt in (".", "") or not pos.isdigit():
                continue
            position = int(pos)
            # A deletion's anchor sits one base left of the deleted base, so
            # widen the window by the record length rather than testing pos
            # alone -- otherwise a deletion of 648 is dropped for starting at 647.
            if position + len(ref) < low or position > high:
                continue
            records.append(VcfRecord(pos=position, ref=ref, alt=alt))
    return records


async def _call_from_vcf(
    vcf_path: str, work: str, build_name: str, absent_to_ref: bool, job_key: str
) -> Dict[str, Any]:
    """Haplogroup + MT-RNR1 from an uploaded VCF.

    Deliberately runs neither the alignment-only variant caller (it needs a
    BAM, and there is none here) nor upstream's coverage/statistics/
    contamination report -- both consume data that only exists once reads have
    been aligned, which this path never has. Rendering that report anyway
    would produce an mtDNA-Server 2-branded page with half its panels blank;
    see `_call_from_alignment` for the branch that actually has the inputs.
    """
    # Classify from the VCF's OWN ##contig header, not from build_name.
    # file_processor._normalize_reference_genome maps "hg19" -> "GRCh37"
    # (file_processor.py:611-612) -- deliberately, because the two builds are
    # identical across the autosomes and differ only in contig naming. chrM is
    # the one contig where that is false, so by the time a build label reaches
    # this service the hg19/b37 distinction is already gone. Trusting it would
    # hand a genuine hg19 file the rename-only plan and never lift its chrM.
    # The contig length is ground truth: 16571 = NC_001807 (hg19), 16569 = rCRS
    # (b37's MT and GRCh38's chrM alike).
    contig_name, contig_length = _read_mito_contig_header(vcf_path)
    build = classify_from_mito_contig(contig_name, contig_length)
    if build == MitoBuild.UNSUPPORTED:
        # No usable mito contig header -- fall back to the label, which is all
        # the information there is for such a file.
        build = classify_build(build_name)
    plan = plan_for(build)
    if not plan.supported:
        raise HTTPException(status_code=422, detail=plan.reason)

    current = os.path.join(work, "input.vcf.gz")
    await _run(["bcftools", "view", "-Oz", "-o", current, vcf_path], work, job_key)

    if plan.rename_mt_to_chrm:
        # Rename ONLY. b37's MT is already rCRS -- the same sequence GRCh38
        # uses -- so its coordinates are already correct and a liftover would
        # shift them by 2 inside MT-RNR1.
        rename_map = os.path.join(work, "mt_rename.txt")
        with open(rename_map, "w", encoding="utf-8") as handle:
            handle.write("MT\tchrM\nM\tchrM\n")
        renamed = os.path.join(work, "renamed.vcf.gz")
        await _run(
            ["bcftools", "annotate", "--rename-chrs", rename_map, "-Oz", "-o", renamed, current],
            work, job_key,
        )
        current = renamed

    if plan.needs_liftover:
        # hg19's chrM IS a different sequence (NC_001807, 16571 bp), so this one
        # is a real coordinate conversion. MT-RNR1 sits inside a single ungapped
        # 2796 bp block of the chain, so every position in the vocabulary lifts
        # exactly. Done here rather than via gatk-api: the base image ships
        # gatk4, the chain is on the read-only /reference mount, and the target
        # for a chrM-only lift is the rCRS FASTA already vendored in.
        if not os.path.exists(CHAIN_PATH):
            raise HTTPException(
                status_code=503,
                detail=f"hg19 input needs the liftover chain at {CHAIN_PATH}, which is not staged.",
            )
        lifted = os.path.join(work, "lifted.vcf.gz")
        await _run(
            ["gatk", "LiftoverVcf", "-I", current, "-O", lifted,
             "-C", CHAIN_PATH, "-R", RCRS_FASTA,
             "--REJECT", os.path.join(work, "rejected.vcf"),
             "--WARN_ON_MISSING_CONTIG", "true"],
            work, job_key,
        )
        current = lifted

    subset = os.path.join(work, "chrM.vcf.gz")
    # -t/--targets, not -r/--regions: -r needs a .tbi/.csi index, which `current`
    # does not necessarily have here (bcftools annotate --rename-chrs above does
    # not write one, and the plain-copy branch above it never builds one either).
    # -t streams instead and needs no index, at the cost of a linear scan --
    # fine for a single chrM lookup.
    await _run(["bcftools", "view", "-t", "chrM", "-Oz", "-o", subset, current], work, job_key)
    normed = os.path.join(work, "chrM.norm.vcf.gz")
    await _run(
        ["bcftools", "norm", "-m-any", "-f", RCRS_FASTA, "-Oz", "-o", normed, subset],
        work, job_key,
    )
    await _run(["bcftools", "index", "-t", normed], work, job_key)

    query = os.path.join(work, "chrM.tsv")
    with open(query, "w", encoding="utf-8") as handle:
        proc = await asyncio.create_subprocess_exec(
            "bcftools", "query", "-f", "%POS\t%REF\t%ALT\n", normed,
            stdout=handle, stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

    records = _read_chrm_records(query)
    matched = match_alleles(records)
    call = select_call(matched)
    if call is None and absent_to_ref:
        # Only on the user's explicit say-so: in a plain VCF an absent record is
        # ambiguous between "reference here" and "never covered here", and the
        # project already exposes that judgment as pharmcat_absent_to_ref.
        call = REFERENCE

    haplogroup, quality = await _classify_haplogroup(normed, work, job_key)
    return {
        "haplogroup": haplogroup,
        "haplogroup_quality": quality,
        "mt_rnr1": call,
        "mt_rnr1_all_matches": matched,
        "variants": [r._asdict() for r in records],
        "report_html": None,
        "report_unavailable_reason": (
            "mtDNA-Server 2's report needs coverage and contamination metrics, "
            "which are only produced from an alignment (BAM/CRAM/FASTQ)."
        ),
    }


async def _classify_haplogroup(vcf_gz: str, work: str, job_key: str):
    """haplogrep3 classify -> (haplogroup, quality). Takes a VCF, not a BAM."""
    out = os.path.join(work, "haplogroups.txt")
    await _run(
        ["java", "-jar", HAPLOGREP_JAR, "classify", "--tree", PHYLOTREE,
         "--in", vcf_gz, "--out", out, "--extend-report"],
        work, job_key,
    )
    with open(out, "r", encoding="utf-8") as handle:
        rows = [line.rstrip("\n").split("\t") for line in handle if line.strip()]
    if len(rows) < 2:
        return None, None
    header, first = rows[0], rows[1]
    index = {name.strip('"').lower(): i for i, name in enumerate(header)}

    def field(key):
        i = index.get(key)
        return first[i].strip('"') if i is not None and i < len(first) else None

    return field("haplogroup"), field("quality")


async def _call_from_alignment(
    bam_path: str, work: str, build_name: str, job_key: str
) -> Dict[str, Any]:
    """Full mode: mutserve -> haplogrep3 + haplocheck -> upstream's report.html.

    Not implemented yet -- this is Task 8. Task 7 wires only the VCF path
    (haplogrep3 accepts a VCF directly, which is what makes that path possible
    without an alignment at all). Refusing loudly here rather than attempting
    something partial: a BAM/CRAM/SAM upload is a real, expected input shape
    for this endpoint (see call_mtdna's input_type branch below), so it must
    fail with a clear "not yet" rather than silently doing the wrong thing.
    """
    raise HTTPException(
        status_code=501,
        detail=(
            "Mitochondrial calling from an alignment (BAM/CRAM/SAM) is not yet "
            "implemented. Upload a VCF instead."
        ),
    )


@app.post("/call-mtdna")
async def call_mtdna(
    file: UploadFile = File(...),
    patient_id: str = Form(...),
    report_id: str = Form(...),
    input_type: str = Form("vcf"),
    reference_genome: str = Form("GRCh38"),
    absent_to_ref: str = Form("false"),
    job_id: Optional[str] = Form(None),
    step_name: Optional[str] = Form(None),
) -> Dict[str, Any]:
    job_key = job_id or f"{patient_id}_{uuid.uuid4().hex[:8]}"

    # Same construction as docker/zarohla/app.py's call_hla: JobClient's own
    # __init__ takes (base_url, job_id, step_name) as keywords, and tolerates a
    # missing step_name (it falls back to NXF_PROCESS_NAME with a warning) but
    # raises if job_id is empty -- which the `if job_id` guard already prevents.
    client = None
    if job_id:
        try:
            client = JobClient(job_id=job_id, step_name=step_name)
            await client.start_step(f"Starting mtDNA calling ({input_type})")
        except Exception as exc:
            logger.warning(f"Failed to initialize JobClient: {exc}")
            client = None

    work = str(TEMP_DIR / "mtdna" / job_key)
    os.makedirs(work, exist_ok=True)
    upload_path = os.path.join(work, os.path.basename(file.filename or "input"))
    # Streamed, not `await file.read()`: with one worker, reading a whole-genome
    # BAM into memory blocks the single event loop for the duration, taking
    # /health and the broadcast /cancel down with it. Same value and same
    # reasoning as zarohla and gatk-api.
    with open(upload_path, "wb") as handle:
        while chunk := await file.read(UPLOAD_CHUNK_BYTES):
            handle.write(chunk)

    wants_ref = str(absent_to_ref).strip().lower() in {"1", "true", "yes", "on"}
    try:
        if input_type.lower() in {"bam", "cram", "sam"}:
            result = await _call_from_alignment(
                upload_path, work, reference_genome, job_key
            )
        else:
            result = await _call_from_vcf(
                upload_path, work, reference_genome, wants_ref, job_key
            )
    except HTTPException as exc:
        if client:
            await client.fail_step(f"mtDNA calling failed: {exc.detail}")
        raise
    finally:
        running_processes.pop(job_key, None)

    if result.get("mt_rnr1"):
        with open(os.path.join(work, "pharmcat.mtdna.tsv"), "w", encoding="utf-8") as fh:
            # Haploid: one name, never an "A/B" diplotype form.
            fh.write(f"MT-RNR1\t{result['mt_rnr1']}\n")

    if client:
        await client.complete_step(f"mtDNA calling complete ({input_type})")

    return {"success": True, "patient_id": patient_id, **result}


@app.post("/cancel/{job_key}")
async def cancel(job_key: str) -> Dict[str, Any]:
    """Kill this job's child processes.

    running_processes is module-level state, which is why the Dockerfile starts
    exactly one worker: with two, this lookup lands on the worker that does not
    hold the job about half the time and cheerfully answers success while
    mutserve keeps running.
    """
    procs = running_processes.pop(job_key, {})
    for proc in procs.values():
        try:
            proc.kill()
        except ProcessLookupError:
            pass
    return {"status": "cancelled", "job_key": job_key, "killed": len(procs)}
