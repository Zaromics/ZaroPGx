"""The FHIR lane's PharmCAT run-id lookup, which never once ran.

``generate_report``'s FHIR export block resolved the run id with

    from app.api.db import Workflow
    db_session.query(Workflow).filter(Workflow.id == workflow_uuid).first()
    ... workflow_obj.workflow_metadata.get("pharmcat_run_id")

``app.api.db`` has no ``Workflow``. ``db/init/migrations/04_rename_workflows_to_jobs.sql``
renamed the table to ``jobs`` and the column to ``job_metadata``, and the model
became ``Job``. So the import raised ``ImportError`` on *every* call, the
surrounding ``except Exception`` swallowed it at WARNING level, and
``pharmcat_run_id`` was unconditionally ``None``.

The value is not decorative. ``FHIRExportService.export_pgx_report`` uses it for
the exported filename (``pgx_report_<stub>.json``) and ``_build_fhir_bundle``
uses it as the Bundle's identifier, so both quietly degraded to the job id or to
nothing. And the writer is live: ``upload_router`` calls
``JobService.link_pharmcat_run(job_id, pharmcat_run_id)`` the moment PharmCAT
output is parsed, which stores the id under ``job_metadata["pharmcat_run_id"]``.

These tests drive ``lookup_pharmcat_run_id`` against a real session and the real
writer, so re-introducing a name that does not exist fails here instead of
disappearing into a log line.
"""

from __future__ import annotations

import uuid

import pytest

from app.reports.generator import lookup_pharmcat_run_id

PHARMCAT_RUN_ID = "3f2a9c14-77bd-4a1b-9f0e-5c6d8e1b2a30"


def _make_job(session, name="run-id-lookup-probe"):
    from app.api.models import JobCreate
    from app.services.job_service import JobService

    job = JobService(session).create_job(
        JobCreate(workflow_type="genomic_analysis", name=name)
    )
    session.commit()
    return job.id


def test_the_linked_run_id_reaches_the_fhir_lane(db_session):
    """The whole point: what upload writes, report generation must read."""
    from app.services.job_service import JobService

    job_id = _make_job(db_session)
    assert JobService(db_session).link_pharmcat_run(job_id, PHARMCAT_RUN_ID) is True

    assert lookup_pharmcat_run_id(db_session, job_id) == PHARMCAT_RUN_ID


def test_the_lookup_survives_the_model_rename(db_session):
    """Names the actual defect: the read must use ``Job``/``job_metadata``.

    Not a source assertion -- it writes through the *current* model and reads
    through the function. A read that reaches for a retired name cannot pass.
    """
    from app.api.db import Job

    job_id = _make_job(db_session, name="rename-probe")
    row = db_session.query(Job).filter(Job.id == uuid.UUID(str(job_id))).first()
    row.job_metadata = {"pharmcat_run_id": PHARMCAT_RUN_ID, "patient_id": "NA12878"}
    db_session.commit()

    assert lookup_pharmcat_run_id(db_session, job_id) == PHARMCAT_RUN_ID


def test_an_unlinked_job_yields_no_run_id(db_session):
    """create_job seeds job_metadata, so this is the populated-but-unlinked case."""
    job_id = _make_job(db_session, name="unlinked-probe")
    assert lookup_pharmcat_run_id(db_session, job_id) is None


@pytest.mark.parametrize(
    "job_id",
    [None, "", "not-a-uuid", "8b1f0d4e-0000-4000-8000-000000000000"],
    ids=["no-job-id", "empty-job-id", "malformed-uuid", "unknown-job"],
)
def test_missing_or_unusable_job_ids_return_none_without_raising(db_session, job_id):
    """The caller treats None as "fall back to the job id"; it must never throw.

    Report generation is not allowed to fail because a FHIR identifier could not
    be resolved -- that was the one good property of the dead block.
    """
    assert lookup_pharmcat_run_id(db_session, job_id) is None


def test_no_session_returns_none_without_touching_the_database():
    """``app/main.py``'s reprocessing path calls generate_report with no session."""
    assert lookup_pharmcat_run_id(None, str(uuid.uuid4())) is None
