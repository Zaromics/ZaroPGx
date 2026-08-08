"""Why ``generate_report``'s Job read does *not* need ``populate_existing()``.

``JobService`` took ``populate_existing()`` on six reads because ``SessionLocal``
sets ``expire_on_commit=False``: an instance a session has already loaded is
never expired, so a plain query fetches the row and then discards it in favour of
the identity-mapped copy. A long-lived session therefore never observes another
session's writes.

``app/reports/generator.py`` carries a seventh read of the same shape --

    job_row = db_session.query(Job).filter(Job.id == job_uuid).first()

-- and it feeds ``workflow_warnings`` (including the GRCh37 "results are
provisional" alert) and the assume-reference methodology paragraph into the
report. A stale read there would silently delete clinical copy from the page.

It is nonetheless safe, and the reason is a property of the callers, not of the
statement:

* ``generate_report`` is reached with a ``db_session`` from exactly one place,
  ``app/api/routes/upload_router.py``, which does ``db_session = next(get_db())``
  on the line before the call. ``get_db`` constructs a brand-new
  ``SessionLocal()``, whose identity map is empty, so the query loads the row from
  the database.
* ``app/main.py``'s reprocessing path calls ``generate_report`` with no
  ``db_session`` at all, so the read never executes there.
* The only earlier load of ``Job`` on that session is
  ``PharmCATDataService.get_pharmcat_data_for_workflow``, microseconds earlier on
  the same fresh session.
* Every key the read consumes -- ``workflow.warnings``,
  ``pharmcat_absent_to_ref``, ``pharmcat_unspecified_to_ref`` -- is committed at
  upload time, long before report generation begins.

So there is no window, and adding ``populate_existing()`` would buy nothing today.
The precondition is real though, and it is invisible at the call site, so these
tests pin it: the first shows the production shape works, the second shows the
shape that would break it. If ``generate_report`` ever acquires a caller that
passes a session which already loaded the Job -- a background poll loop's
session, a request-scoped ``Depends(get_db)`` used earlier in the same request --
the second test is the failure that follows, and ``populate_existing()`` becomes
required.
"""

from __future__ import annotations

import uuid

import pytest

from app.api.db import Job

GRCH37_ALERT = (
    "<p>⚠️ This file is aligned to the GRCh37 reference genome. "
    "ZaroPGx supports GRCh38/hg38 VCF files only, so any results for this file "
    "are provisional and should not be relied on.</p>"
)


@pytest.fixture
def sessions(session_factory):
    """Hand out independent sessions and close them all at the end."""
    opened = []

    def _open():
        session = session_factory()
        opened.append(session)
        return session

    yield _open

    for session in opened:
        session.rollback()
        session.close()


def _make_job(session):
    from app.api.models import JobCreate
    from app.services.job_service import JobService

    job = JobService(session).create_job(
        JobCreate(workflow_type="genomic_analysis", name="generator-metadata-probe")
    )
    session.commit()
    return job.id


def _stamp_upload_metadata(session, job_id):
    """Write the keys the upload path writes, from its own session."""
    job = session.query(Job).filter(Job.id == job_id).first()
    job.job_metadata = {
        "workflow": {"warnings": [GRCH37_ALERT]},
        "pharmcat_absent_to_ref": True,
        "pharmcat_unspecified_to_ref": False,
    }
    session.commit()


def _read_like_generate_report(db_session, job_id):
    """The statement ``generate_report`` runs, verbatim in shape."""
    job_uuid = uuid.UUID(str(job_id))
    job_row = db_session.query(Job).filter(Job.id == job_uuid).first()
    meta = (job_row.job_metadata or {}) if job_row is not None else {}
    workflow_config = (
        meta.get("workflow", {}) if isinstance(meta.get("workflow"), dict) else {}
    )
    return workflow_config.get("warnings", []) or []


def test_a_session_opened_at_the_call_site_sees_the_upload_metadata(sessions):
    """Production shape: ``next(get_db())`` on the line before ``generate_report``."""
    job_id = _make_job(sessions())
    _stamp_upload_metadata(sessions(), job_id)

    # upload_router.py: db_session = next(get_db()) -- a fresh SessionLocal, empty
    # identity map, opened after every write this read cares about.
    fresh = sessions()
    assert _read_like_generate_report(fresh, job_id) == [GRCH37_ALERT]


def test_a_session_that_preloaded_the_job_would_go_stale(sessions):
    """The shape that would need ``populate_existing()``; no caller uses it.

    Pinned as the tripwire: if this ever becomes the production shape, the GRCh37
    provisional alert disappears from the report with no error anywhere.
    """
    job_id = _make_job(sessions())

    long_lived = sessions()
    held = long_lived.query(Job).filter(Job.id == uuid.UUID(str(job_id))).first()
    assert held is not None  # strong reference: the identity map holds weak ones
    assert _read_like_generate_report(long_lived, job_id) == []

    _stamp_upload_metadata(sessions(), job_id)

    assert _read_like_generate_report(long_lived, job_id) == [], (
        "a pre-loaded session now sees another session's write on a plain query -- "
        "SQLAlchemy's identity-map behaviour changed, and generator.py's read "
        "needs re-auditing"
    )

    # The second half of the property, and the one the safety argument actually
    # names: expire_on_commit=False. Committing the *reader* does not expire its
    # own instances, so even a session that commits between reads stays stale.
    long_lived.commit()
    assert _read_like_generate_report(long_lived, job_id) == [], (
        "the reader's own commit expired its instances -- expire_on_commit is no "
        "longer False, which is a load-bearing assumption of this whole audit"
    )

    # And the same session with populate_existing() would see it, which is the
    # fix that becomes necessary the day a caller passes such a session.
    refreshed = (
        long_lived.query(Job)
        .filter(Job.id == uuid.UUID(str(job_id)))
        .populate_existing()
        .first()
    )
    assert refreshed.job_metadata["workflow"]["warnings"] == [GRCH37_ALERT]


def test_db_session_stays_optional_with_no_shared_default():
    """A narrow guard, named for what it actually checks.

    It does not enumerate callers -- nothing in-process can. It pins the one
    signature-level way the freshness argument could be voided wholesale: a
    module-level session becoming the parameter's default, which would hand every
    report the *same* long-lived identity map.
    """
    import inspect

    from app.reports.generator import generate_report

    parameters = inspect.signature(generate_report).parameters
    assert "db_session" in parameters
    assert parameters["db_session"].default is None, (
        "db_session gained a default session; a shared default would defeat the "
        "freshness argument entirely"
    )
