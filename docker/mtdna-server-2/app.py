import asyncio
import csv
import json
import logging
import os
import re
import sys
import time
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

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
# MitoBuild is imported here (not only in the alignment branch) because Task 8's
# _call_from_alignment compares against MitoBuild.HG19 and MitoBuild.GRCH38.
from mtdna.builds import (  # noqa: E402
    MitoBuild,
    classify_build,
    classify_from_mito_contig,
    plan_for,
)
from mtdna.mt_rnr1 import MT_RNR1_ALLELES  # noqa: E402  (path set above)
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


async def _read_mito_contig_header(vcf_path: str, work: str, job_key: str):
    """(name, length) of the VCF's mitochondrial ##contig line, or (None, None).

    Read with `bcftools view -h`, run the same non-blocking way as every other
    subprocess in this file (see `_run`): the Dockerfile starts exactly one
    gunicorn worker, so a blocking `subprocess.run()` here would stall
    /health and the broadcast /cancel for as long as bcftools takes on this
    file, and an uncaught CalledProcessError would surface as a bare 500
    instead of this file's consistent HTTPException-with-stderr.

    The mito contig may be spelled chrM, MT or M; take the first that
    appears. Length is parsed from the same line's `length=` field. Returns
    (None, None) when the header carries no mito contig, which is a real
    case -- some VCFs omit ##contig entirely -- and the caller then falls
    back to the build label.
    """
    proc = await asyncio.create_subprocess_exec(
        "bcftools",
        "view",
        "-h",
        vcf_path,
        cwd=work,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    running_processes.setdefault(job_key, {})["bcftools-header"] = proc
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"bcftools failed: {stderr.decode(errors='replace')[:2000]}",
        )
    for line in stdout.decode("utf-8", errors="replace").splitlines():
        if not line.startswith("##contig="):
            continue
        id_match = re.search(r"ID=([^,>]+)", line)
        if not id_match or id_match.group(1).strip().lower() not in _MITO_CONTIG_NAMES:
            continue
        length_match = re.search(r"length=(\d+)", line)
        length = int(length_match.group(1)) if length_match else None
        return id_match.group(1), length
    return None, None


async def _read_mito_sq_header(bam_path: str, work: str, job_key: str):
    """(name, length) of the alignment's mitochondrial @SQ header line, or
    (None, None).

    The BAM/CRAM/SAM equivalent of `_read_mito_contig_header` above: `SN:` is
    the contig name, `LN:` its length -- the same two inputs
    `classify_from_mito_contig` already takes for the VCF path. The alignment
    path needs this too, for the same reason: `reference_genome` alone cannot
    distinguish hg19's chrM (NC_001807, 16571 bp) from b37's MT / GRCh38's
    chrM (both rCRS, 16569 bp) -- file_processor._normalize_reference_genome
    deliberately collapses "hg19" -> "GRCh37" before this service ever sees
    the label (file_processor.py:611-612). Without reading the header, a
    genuinely hg19-aligned BAM labelled "GRCh37" upstream would take the b37
    rename-only plan instead of being refused.
    """
    proc = await asyncio.create_subprocess_exec(
        "samtools",
        "view",
        "-H",
        bam_path,
        cwd=work,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    running_processes.setdefault(job_key, {})["samtools-header"] = proc
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"samtools view -H failed: {stderr.decode(errors='replace')[:2000]}",
        )
    for line in stdout.decode("utf-8", errors="replace").splitlines():
        if not line.startswith("@SQ"):
            continue
        sn_match = re.search(r"SN:(\S+)", line)
        if not sn_match or sn_match.group(1).strip().lower() not in _MITO_CONTIG_NAMES:
            continue
        ln_match = re.search(r"LN:(\d+)", line)
        length = int(ln_match.group(1)) if ln_match else None
        return sn_match.group(1), length
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
    #
    # build_name IS passed in here, but only classify_from_mito_contig decides
    # whether it's safe to use: it's consulted internally, and only for the
    # one case where the contig line exists but carries no length= (a bare
    # "chrM", which hg19 and GRCh38 both spell identically) -- see that
    # function's docstring for why that's the only safe direction to trust
    # the label in.
    contig_name, contig_length = await _read_mito_contig_header(vcf_path, work, job_key)
    build = classify_from_mito_contig(contig_name, contig_length, build_name)
    if build == MitoBuild.UNSUPPORTED:
        # No usable mito contig header at all (name AND length both absent) --
        # fall back to the label, which is all the information there is for
        # such a file. This is NOT the ambiguous-chrM case above: that one
        # resolves (or refuses, as AMBIGUOUS_CHRM) inside
        # classify_from_mito_contig itself and never reaches here.
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
            [
                "bcftools",
                "annotate",
                "--rename-chrs",
                rename_map,
                "-Oz",
                "-o",
                renamed,
                current,
            ],
            work,
            job_key,
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
            [
                "gatk",
                "LiftoverVcf",
                "-I",
                current,
                "-O",
                lifted,
                "-C",
                CHAIN_PATH,
                "-R",
                RCRS_FASTA,
                "--REJECT",
                os.path.join(work, "rejected.vcf"),
                "--WARN_ON_MISSING_CONTIG",
                "true",
            ],
            work,
            job_key,
        )
        current = lifted

    subset = os.path.join(work, "chrM.vcf.gz")
    # -t/--targets, not -r/--regions: -r needs a .tbi/.csi index, which `current`
    # does not necessarily have here (bcftools annotate --rename-chrs above does
    # not write one, and the plain-copy branch above it never builds one either).
    # -t streams instead and needs no index, at the cost of a linear scan --
    # fine for a single chrM lookup.
    await _run(
        ["bcftools", "view", "-t", "chrM", "-Oz", "-o", subset, current], work, job_key
    )
    normed = os.path.join(work, "chrM.norm.vcf.gz")
    await _run(
        ["bcftools", "norm", "-m-any", "-f", RCRS_FASTA, "-Oz", "-o", normed, subset],
        work,
        job_key,
    )
    await _run(["bcftools", "index", "-t", normed], work, job_key)

    query = os.path.join(work, "chrM.tsv")
    with open(query, "w", encoding="utf-8") as handle:
        proc = await asyncio.create_subprocess_exec(
            "bcftools",
            "query",
            "-f",
            "%POS\t%REF\t%ALT\n",
            normed,
            stdout=handle,
            stderr=asyncio.subprocess.PIPE,
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
        # Absolute path under DATA_DIR/TEMP_DIR, which the mtdna volume shares
        # with every other container in the stack (compose.yml mounts ./data
        # at /data everywhere) -- the same route report_html below relies on
        # to be readable from the Nextflow task's own container.
        "chrm_vcf": normed,
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
        [
            "java",
            "-jar",
            HAPLOGREP_JAR,
            "classify",
            "--tree",
            PHYLOTREE,
            "--in",
            vcf_gz,
            "--out",
            out,
            "--extend-report",
        ],
        work,
        job_key,
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


# Upstream's own floor (nextflow.config params.min_mean_coverage). Below it,
# MT-RNR1 stays a no-call rather than being reported as Reference: "normal risk
# of aminoglycoside-induced hearing loss" is a positive claim and needs
# positive evidence.
MIN_MEAN_COVERAGE = float(os.getenv("MTDNA_MIN_MEAN_COVERAGE", "50"))

# CRAM stores no base sequence of its own -- decoding it needs the exact
# reference it was compressed against, and this image bakes in no
# REF_CACHE/REF_PATH to fetch one automatically. These are the two FASTAs the
# `reference` volume (compose.yml) actually stages; a build this service
# calls but has no entry for here (e.g. hg19, already refused earlier) never
# reaches this lookup.
_CRAM_REFERENCE_FASTA = {
    MitoBuild.GRCH38: "/reference/grch38/Homo_sapiens_assembly38.fasta",
    MitoBuild.B37: "/reference/grch37/human_g1k_v37.fasta",
}


async def _call_from_alignment(
    bam_path: str, work: str, build_name: str, job_key: str, input_type: str = "bam"
) -> Dict[str, Any]:
    """Full mode: mutserve -> haplogrep3 + haplocheck -> upstream's report.html."""
    # Ground truth from the alignment's own @SQ header, not just the label --
    # the same rule `_call_from_vcf` already follows via
    # classify_from_mito_contig. Must run before the hg19 refusal below: a
    # BAM genuinely aligned to hg19 but labelled "GRCh37" upstream (the label
    # alone cannot tell them apart -- see _read_mito_sq_header's docstring)
    # has to be caught on the header's LN:16571, not waved through on the
    # label.
    contig_name, contig_length = await _read_mito_sq_header(bam_path, work, job_key)
    build = classify_from_mito_contig(contig_name, contig_length, build_name)
    if build == MitoBuild.UNSUPPORTED:
        # No usable mito @SQ line at all -- fall back to the label, exactly
        # as _call_from_vcf does for the equivalent VCF case.
        build = classify_build(build_name)
    if build == MitoBuild.HG19:
        # hg19's chrM is NC_001807 (16571 bp), not rCRS, so mutserve's positions
        # would be silently wrong against the rCRS reference. The stack has no
        # alignment-level liftover and realigning chrM reads is out of scope, so
        # refuse rather than produce a plausible-looking wrong haplogroup.
        raise HTTPException(
            status_code=422,
            detail=(
                "hg19 alignments are not supported for mitochondrial calling: "
                "hg19's chrM is NC_001807 (16571 bp), not rCRS, and this stack "
                "has no alignment-level liftover. Upload a GRCh38 or GRCh37 "
                "alignment, or a VCF (which can be lifted)."
            ),
        )
    if not plan_for(build).supported:
        raise HTTPException(status_code=422, detail=plan_for(build).reason)

    reference_arg: List[str] = []
    if input_type.lower() == "cram":
        reference_fasta = _CRAM_REFERENCE_FASTA.get(build)
        if reference_fasta is None or not os.path.exists(reference_fasta):
            # Refuse up front with the expected path, rather than letting
            # samtools fail obscurely -- a network fetch attempt against the
            # EBI reference server (this image has no route out for one), or
            # a bare "Failed to populate sequence" error naming nothing.
            raise HTTPException(
                status_code=422,
                detail=(
                    "CRAM input needs the reference it was compressed "
                    "against. Expected it staged at "
                    f"{reference_fasta or '(no known path for this build)'}, "
                    "which is not present on this service's /reference "
                    "mount. Upload a BAM instead, or stage that FASTA."
                ),
            )
        reference_arg = ["--reference", reference_fasta]

    await _run(["samtools", "index", bam_path], work, job_key)
    region = "chrM" if build == MitoBuild.GRCH38 else "MT"
    chrm_bam = os.path.join(work, "chrM.bam")
    # --reference only matters for decoding a CRAM's actual sequence; empty
    # (a no-op) for BAM/SAM, which already carry their own bases.
    await _run(
        ["samtools", "view", "-b"] + reference_arg + ["-o", chrm_bam, bam_path, region],
        work,
        job_key,
    )
    await _run(["samtools", "index", chrm_bam], work, job_key)

    called = os.path.join(work, "chrM.vcf.gz")
    await _run(
        [
            "java",
            "-jar",
            MUTSERVE_JAR,
            "call",
            "--level",
            "0.01",
            "--reference",
            RCRS_FASTA,
            "--mapQ",
            "20",
            "--baseQ",
            "20",
            "--output",
            called,
            "--no-ansi",
            "--strand-bias",
            "1.6",
            chrm_bam,
        ],
        work,
        job_key,
    )
    normed = os.path.join(work, "chrM.norm.vcf.gz")
    await _run(
        ["bcftools", "norm", "-m-any", "-f", RCRS_FASTA, "-Oz", "-o", normed, called],
        work,
        job_key,
    )
    await _run(["bcftools", "index", "-t", normed], work, job_key)

    # The join key every report support file below is keyed on: whatever
    # sample name mutserve wrote into the VCF's own genotype column.
    # haplogrep3 and haplocheck are handed this same VCF below, so they read
    # that same name back out as their own SampleID/Sample column -- matching
    # by construction rather than by guessing mutserve's naming convention.
    sample_key = await _vcf_sample_name(normed, work, job_key)

    haplocheck_txt = os.path.join(work, "haplocheck.txt")
    await _run(
        ["java", "-jar", HAPLOCHECK_JAR, "--out", haplocheck_txt, "--raw", normed],
        work,
        job_key,
    )
    # `region`, not a hardcoded "chrM": samtools view keeps the input's contig
    # name, so a GRCh37 extraction is still called MT. Asking depth for
    # chrM:648-1601 there returns no rows, coverage reads None, and MT-RNR1
    # silently never reaches Reference no matter how deep the sequencing was.
    coverage = await _mean_coverage(chrm_bam, region, work, job_key)

    query = os.path.join(work, "chrM.tsv")
    with open(query, "w", encoding="utf-8") as handle:
        proc = await asyncio.create_subprocess_exec(
            "bcftools",
            "query",
            "-f",
            "%POS\t%REF\t%ALT\n",
            normed,
            stdout=handle,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

    records = _read_chrm_records(query)
    matched = match_alleles(records)
    call = select_call(matched)
    if call is None and coverage is not None and coverage >= MIN_MEAN_COVERAGE:
        # Positive evidence: the gene was sequenced deeply enough that the
        # absence of a listed variant means absence, not silence.
        call = REFERENCE

    haplogroup, quality = await _classify_haplogroup(normed, work, job_key)

    # The four remaining report.Rmd inputs that are this pipeline's own
    # responsibility (not mutserve/haplogrep3/haplocheck output) -- one row
    # each, since this endpoint calls a single BAM at a time.
    stats_row = await _bam_statistics(chrm_bam, region, work, job_key)
    await _write_report_support_files(work, job_key, sample_key, stats_row, normed)

    report_html = await _render_report(work, job_key)
    return {
        "haplogroup": haplogroup,
        "haplogroup_quality": quality,
        "mt_rnr1": call,
        "mt_rnr1_all_matches": matched,
        "mean_coverage": coverage,
        "variants": [r._asdict() for r in records],
        "chrm_vcf": normed,
        "report_html": report_html,
        "report_unavailable_reason": None,
    }


async def _mean_coverage(
    bam: str, contig: str, work: str, job_key: str
) -> Optional[float]:
    """Mean depth across MT-RNR1 (rCRS 648-1601) via samtools depth.

    `contig` is the name in THIS bam's header -- chrM on GRCh38, MT on b37 --
    not a constant. Getting it wrong returns zero rows rather than an error,
    which reads as "no coverage" and quietly withholds a Reference call.
    """
    low, high = MT_RNR1_SPAN
    out = os.path.join(work, "depth.txt")
    with open(out, "w", encoding="utf-8") as handle:
        proc = await asyncio.create_subprocess_exec(
            "samtools",
            "depth",
            "-a",
            "-r",
            f"{contig}:{low}-{high}",
            bam,
            stdout=handle,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
    depths = []
    with open(out, "r", encoding="utf-8") as handle:
        for line in handle:
            parts = line.split("\t")
            if len(parts) >= 3:
                depths.append(int(parts[2]))
    return (sum(depths) / len(depths)) if depths else None


async def _vcf_sample_name(vcf_gz: str, work: str, job_key: str) -> str:
    """The sample name mutserve embedded in the VCF's own genotype column.

    Read back rather than assumed: haplogrep3 and haplocheck are about to be
    handed this same VCF and will report that exact name as their own
    SampleID/Sample column. Using it here -- instead of trying to reproduce
    mutserve's naming convention in Python -- is what makes report.Rmd's
    per-sample `merge()` calls actually match instead of silently dropping
    every row.
    """
    proc = await asyncio.create_subprocess_exec(
        "bcftools",
        "query",
        "-l",
        vcf_gz,
        cwd=work,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    running_processes.setdefault(job_key, {})["bcftools-query-l"] = proc
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"bcftools query -l failed: {stderr.decode(errors='replace')[:2000]}",
        )
    names = [
        n for n in stdout.decode("utf-8", errors="replace").splitlines() if n.strip()
    ]
    if not names:
        raise HTTPException(
            status_code=500,
            detail=(
                "mutserve's VCF carries no sample column; cannot build the "
                "per-sample report tables."
            ),
        )
    return names[0]


async def _bam_statistics(
    bam: str, contig: str, work: str, job_key: str
) -> Dict[str, str]:
    """samtools coverage over `contig` -- upstream's own calculate_statistics.nf
    module reads exactly this table for the report's per-sample statistics
    row: numreads, covbases, coverage%, meandepth, meanbaseq, meanmapq.
    `contig` is the caller's region name (chrM/MT), same rule as
    `_mean_coverage`: never a hardcoded constant.
    """
    proc = await asyncio.create_subprocess_exec(
        "samtools",
        "coverage",
        "-r",
        contig,
        bam,
        cwd=work,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    running_processes.setdefault(job_key, {})["samtools-coverage"] = proc
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"samtools coverage failed: {stderr.decode(errors='replace')[:2000]}",
        )
    lines = [
        line
        for line in stdout.decode("utf-8", errors="replace").splitlines()
        if line.strip()
    ]
    if len(lines) < 2:
        raise HTTPException(
            status_code=500,
            detail=f"samtools coverage produced no row for {contig}.",
        )
    header = lines[0].lstrip("#").split("\t")
    values = lines[1].split("\t")
    return dict(zip(header, values))


async def _write_report_support_files(
    work: str,
    job_key: str,
    sample_key: str,
    stats_row: Dict[str, str],
    normed_vcf: str,
) -> None:
    """The four report.Rmd inputs this pipeline (not mutserve/haplogrep3/
    haplocheck) is responsible for producing -- one row each, since this
    endpoint calls a single sample at a time.

    Keyed throughout on `sample_key`: the exact sample name mutserve wrote
    into the VCF, which is also what haplogrep3's SampleID and haplocheck's
    Sample columns carry (see `_vcf_sample_name`). Get this wrong and
    report.Rmd's `merge()` calls drop every row silently -- report.html still
    renders 200, with every panel blank.
    """
    mapping_path = os.path.join(work, "sample_mappings.txt")
    with open(mapping_path, "w", encoding="utf-8") as handle:
        handle.write("Sample\tFilename\n")
        handle.write(f"{job_key}\t{sample_key}\n")

    stats_path = os.path.join(work, "sample_statistics.txt")
    with open(stats_path, "w", encoding="utf-8") as handle:
        handle.write("Sample\tParameter\tValue\n")
        for parameter, key in (
            ("Contig", "rname"),
            ("NumberofReads", "numreads"),
            ("CoveredBases", "covbases"),
            ("CoveragePercentage", "coverage"),
            ("MeanDepth", "meandepth"),
            ("MeanBaseQuality", "meanbaseq"),
            ("MeanMapQuality", "meanmapq"),
        ):
            handle.write(f"{sample_key}\t{parameter}\t{stats_row.get(key, '')}\n")

    # No coverage-based exclusion happens in this single-BAM-at-a-time
    # endpoint -- a genuinely empty file (0 bytes, no header: report.Rmd
    # reads this one with header=FALSE), not one line that read.delim would
    # misread as a spurious excluded sample.
    open(os.path.join(work, "excluded_samples.txt"), "w", encoding="utf-8").close()

    filters_path = os.path.join(work, "variants_filter.tmp")
    with open(filters_path, "w", encoding="utf-8") as handle:
        proc = await asyncio.create_subprocess_exec(
            "bcftools",
            "query",
            "-f",
            "%FILTER\n",
            normed_vcf,
            stdout=handle,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
    variants_path = os.path.join(work, "variants.txt")
    with (
        open(filters_path, "r", encoding="utf-8") as src,
        open(variants_path, "w", encoding="utf-8") as dst,
    ):
        dst.write("ID\tFilter\n")
        for line in src:
            line = line.strip()
            if line:
                dst.write(f"{sample_key}\t{line}\n")


async def _render_report(work: str, job_key: str) -> Optional[str]:
    """Render upstream's report.Rmd -- the genuine mtDNA-Server 2 report.

    Vendored from the same v2.1.16 tag as the image, because an .Rmd that
    disagrees with the tool output it reads fails by rendering empty panels
    rather than by erroring.
    """
    params = os.path.join(work, "params.txt")
    versions = _tool_versions()
    with open(params, "w", encoding="utf-8") as handle:
        handle.write("Parameter\tValue\n")
        # Shaped like upstream's own modules/local/report.nf, not
        # `_tool_versions()`'s {name: version} dict: report.Rmd's "Variant
        # Caller" valueBox filters pipeline_params for a row literally named
        # "Variant Caller" (`filter(Parameter == "Variant Caller")`), which a
        # {name: version} dict never has -- that box renders 200 and empty.
        # Confirmed against a real render: see task-8-report.md.
        for key, value in (
            ("Version", versions["mtdna-server-2"]),
            ("Job", job_key),
            ("Date", time.strftime("%a %b %d %H:%M:%S UTC %Y", time.gmtime())),
            ("Repository", "https://github.com/genepi/mtdna-server-2"),
            # This sidecar runs mutserve alone, never upstream's optional
            # mutserve+mutect2 "fusion" mode -- "mutserve" is the honest
            # value for what actually produced these calls.
            ("Variant Caller", "mutserve"),
            ("Detection Limit", "0.01"),  # same --level value passed below
            ("Reference", "rcrs"),
            ("Base Quality", "20"),  # same --baseQ value passed below
            ("Map Quality", "20"),  # same --mapQ value passed below
            ("Alignment Quality", "30"),  # mutserve's own default; not overridden
            ("Mutserve", versions["mutserve"]),
            ("Haplocheck", versions["haplocheck"]),
            ("Haplogrep", versions["haplogrep3"]),
        ):
            handle.write(f"{key}\t{value}\n")
    script = (
        "require('rmarkdown'); render('/app/report.Rmd', params = list("
        f"pipeline_parameters = '{params}', variants = '{work}/variants.txt', "
        f"haplogroups = '{work}/haplogroups.txt', haplocheck = '{work}/haplocheck.txt', "
        f"statistics = '{work}/sample_statistics.txt', mapping = '{work}/sample_mappings.txt', "
        f"excluded_samples = '{work}/excluded_samples.txt'"
        f"), knit_root_dir='{work}', output_file='{work}/report.html')"
    )
    try:
        await _run(["Rscript", "-e", script], work, job_key)
    except HTTPException as exc:
        # A failed render costs the download, not the call: the haplogroup and
        # the MT-RNR1 result are already in hand and are what PharmCAT needs.
        logger.warning(f"mtDNA report render failed: {exc.detail}")
        return None
    return (
        os.path.join(work, "report.html")
        if os.path.exists(f"{work}/report.html")
        else None
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
                upload_path, work, reference_genome, job_key, input_type
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
        with open(
            os.path.join(work, "pharmcat.mtdna.tsv"), "w", encoding="utf-8"
        ) as fh:
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
