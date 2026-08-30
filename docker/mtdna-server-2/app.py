import os
import asyncio
import json
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

MUTSERVE_JAR = "/opt/mutserve/mutserve.jar"
HAPLOGREP_JAR = "/opt/haplogrep/haplogrep3.jar"
HAPLOCHECK_JAR = "/opt/haplocheck/haplocheck.jar"
RCRS_FASTA = "/opt/mtdna-files/rcrs_mutserve.fasta"
RCRS_ANNOTATION = "/opt/mtdna-files/rCRS_annotation.txt"
PHYLOTREE = "phylotree-fu-rcrs@1.2"

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
