"""What a job's FHIR bundle is built from, kept beside its report.

The report generator builds the bundle from the report's own gene rows: PharmCAT's
calls, and what ZaroPGx adds to them -- PyPGx's genes (CYP1A2 and the rest PharmCAT
does not call), calls corrected for copy number, the variants no allele definition
lists. An export made later through the API (/fhir/export/..., /fhir/save/...) had
only PharmCAT's rows in the database to rebuild it from, so it left all that out. The
generator now writes what it built the bundle from, ``<job>_pgx_fhir_source.json`` in
the job's report directory, whether or not FHIR export is on, and the export reads it
back (FHIRExportService); a job reported before has none, and is exported from the
database as it was.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from app.api.utils.path_jail import resolve_under

logger = logging.getLogger(__name__)

VERSION = 1


def filename(job_id: str) -> str:
    return f"{job_id}_pgx_fhir_source.json"


def write(
    report_dir: str,
    job_id: str,
    data: Dict[str, Any],
    run_id: Optional[str] = None,
    patient_id: Optional[str] = None,
    patient_info: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Write what the job's bundle is built from; its path, or None when it could not
    be written (the report goes on without it, and a later export reads the database).

    ``data`` is the report's data as the FHIR export is given it, ``run_id`` the PharmCAT
    run the job is linked to, ``patient_id`` the patient whose directory holds the
    report, and ``patient_info`` the Patient details the report's own export used.
    """
    record = {
        "version": VERSION,
        "workflow_id": str(job_id),
        "run_id": str(run_id) if run_id else None,
        "patient_id": str(patient_id) if patient_id else None,
        "patient_info": patient_info,
        "data": data,
    }
    path = Path(report_dir) / filename(str(job_id))
    try:
        path.write_text(json.dumps(record, default=str), encoding="utf-8")
    except (OSError, TypeError, ValueError) as e:
        logger.warning(f"Could not write {path.name}: {e}")
        return None
    return str(path)


def read(
    report_root: Path,
    job_id: str,
    patient_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """What the job's report saved for its bundle, or None: none was written (a job
    reported before this), it cannot be read, or it is another job's or run's.

    Looked for where the report is: ``<patient>/<job>/``, or ``<job>/`` for a job with
    no patient, then any patient's directory. Only a job id that is a UUID is looked
    for, so the id from a URL names one directory, never a pattern.
    """
    try:
        job_id = str(uuid.UUID(str(job_id)))
    except ValueError:
        return None
    name = filename(job_id)
    candidates = []
    for parts in ((patient_id, job_id, name), (job_id, name)):
        if not all(parts):
            continue
        try:
            candidates.append(resolve_under(report_root, *parts))
        except PermissionError:
            continue
    candidates += sorted(report_root.glob(f"*/{job_id}/{name}"))
    for path in candidates:
        if not path.is_file():
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.warning(f"Could not read {path}: {e}")
            continue
        if (
            not isinstance(record, dict)
            or record.get("version") != VERSION
            or record.get("workflow_id") != job_id
            or not isinstance(record.get("data"), dict)
        ):
            continue
        if run_id and record.get("run_id") and record["run_id"] != str(run_id):
            continue
        return record
    return None


def bundle_patient_id(record: Dict[str, Any]) -> Optional[str]:
    """The patient the report's own bundle named: its directory's, unless that is the
    job's own directory (save_fhir_export's rule)."""
    patient_id = record.get("patient_id")
    return patient_id if patient_id and patient_id != record.get("workflow_id") else None
