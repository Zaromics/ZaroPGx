"""Sessions opened outside FastAPI's dependency machinery must be handed back.

``app.api.db.get_db`` is a *generator* dependency: the ``db.close()`` that hands
the connection back to the pool lives in its ``finally:``. ``db = next(get_db())``
advances that generator exactly once, which is not how a generator reaches its
``finally`` -- so the caller ends up owning a session nothing will ever close.

What actually happens in CPython is subtler than "the session is never closed",
and worth writing down because it is why a naive test passes either way: the
generator object is a temporary, so it is finalised the instant ``next()``
returns, and *that* runs the ``finally``. The session therefore arrives already
closed, and every subsequent use of it checks a fresh connection out of the pool
that nothing ever checks back in. The connection then depends on garbage
collection -- SQLAlchemy's non-checked-in-connection weakref path -- instead of
on the code.

So the property under test is: **when the code that borrowed a session is done,
the last thing that happened to that session is a close, not a transaction
start.** A session left mid-transaction is a session still holding a pooled
connection. These tests assert nothing about source text: they install a Session
subclass that logs every transaction start (``after_begin``, which fires exactly
when a connection is taken out of the pool) and every ``close()``, keep a strong
reference to each session so a leak cannot be swept out of view by the garbage
collector, and then drive the real code paths.

Pool checkout/checkin counting was tried first and rejected: the test engine uses
``StaticPool``, whose single connection record does not emit balanced
checkout/checkin pairs when two sessions overlap, so the count drifts for reasons
that have nothing to do with the code under test.

Background/non-request code in this repo gets a session with ``SessionLocal()``
inside a ``try/finally``; ``app/main.py::get_reports_by_job_id`` is the reference
shape.
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session as SASession
from sqlalchemy.orm import sessionmaker

from app.api.db import Job


@pytest.fixture
def session_ledger(engine, monkeypatch):
    """Record what happens to every session ``SessionLocal`` hands out."""
    log: dict[int, list[str]] = {}
    opened: list[SASession] = []

    class _TrackedSession(SASession):
        def close(self, *args, **kwargs):
            log.setdefault(id(self), []).append("close")
            return super().close(*args, **kwargs)

    @event.listens_for(_TrackedSession, "after_begin")
    def _after_begin(session, transaction, connection):
        # Fires exactly when the session takes a connection out of the pool.
        log.setdefault(id(session), []).append("begin")

    factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine,
        expire_on_commit=False,
        class_=_TrackedSession,
    )

    def _open() -> SASession:
        session = factory()
        # The strong reference is load-bearing: without it CPython finalises a
        # leaked session at function exit and the pool quietly recovers, hiding
        # exactly the bug this file exists to catch.
        opened.append(session)
        log.setdefault(id(session), [])
        return session

    import app.api.db as db_module
    import app.api.routes.upload_router as upload_router_module

    # main.py and upload_router import SessionLocal at call time, so the module
    # attribute on app.api.db is the one that matters; upload_router also holds a
    # module-level alias used by the Nextflow background wrapper. Patching both
    # also covers get_db(), which builds its session from the same name -- which
    # is what makes a reverted fix visible here rather than invisible.
    monkeypatch.setattr(db_module, "SessionLocal", _open)
    monkeypatch.setattr(upload_router_module, "SessionLocal", _open)

    return SimpleNamespace(opened=opened, log=log)


def assert_sessions_were_returned(ledger, expected_at_least: int = 1):
    assert len(ledger.opened) >= expected_at_least, (
        f"expected at least {expected_at_least} session(s) on this path, saw "
        f"{len(ledger.opened)} -- this test no longer exercises the code it pins"
    )

    still_borrowed = [
        entries
        for session in ledger.opened
        for entries in [ledger.log.get(id(session), [])]
        if entries and entries[-1] != "close"
    ]
    assert not still_borrowed, (
        f"{len(still_borrowed)} of {len(ledger.opened)} session(s) were last seen "
        f"starting a transaction, not being closed: {still_borrowed} -- the caller "
        "walked away while the session still owned a pooled connection"
    )

    open_transactions = [s for s in ledger.opened if s.in_transaction()]
    assert not open_transactions, (
        f"{len(open_transactions)} session(s) still hold an open transaction after "
        "the code that borrowed them returned"
    )


@pytest.fixture
def stored_job(db_session):
    """A committed Job for the /check-reports and /trigger-completion paths."""
    from app.api.models import JobCreate
    from app.services.job_service import JobService

    job = JobService(db_session).create_job(
        JobCreate(workflow_type="genomic_analysis", name="session-leak-probe")
    )
    job.job_metadata = {"patient_id": "patient-leak-probe"}
    db_session.commit()
    return str(job.id)


def test_check_reports_returns_both_of_its_sessions(client, session_ledger, stored_job):
    """GET /check-reports/{job_id} borrows two sessions; both must come back."""
    response = client.get(f"/check-reports/{stored_job}")

    assert response.status_code == 200
    assert_sessions_were_returned(session_ledger, expected_at_least=2)


def test_trigger_completion_returns_its_lookup_session(
    client, session_ledger, stored_job
):
    """GET /trigger-completion/{job_id} with no report files on disk."""
    response = client.get(f"/trigger-completion/{stored_job}")

    assert response.status_code == 200
    assert_sessions_were_returned(session_ledger, expected_at_least=1)


def test_trigger_completion_returns_its_status_update_session(
    client, session_ledger, stored_job, monkeypatch
):
    """Its second session only opens when report files exist, so make them exist."""
    real_exists = os.path.exists

    def _exists(path):
        name = str(path)
        if name.endswith("_pgx_report.pdf") or name.endswith("_pgx_report.html"):
            return True
        return real_exists(path)

    monkeypatch.setattr(os.path, "exists", _exists)
    response = client.get(f"/trigger-completion/{stored_job}")

    assert response.status_code == 200
    assert_sessions_were_returned(session_ledger, expected_at_least=2)


def test_reprocess_report_returns_its_lookup_session(
    client, session_ledger, stored_job
):
    """POST /reprocess-report/{report_id} resolves patient_id before anything else."""
    response = client.post(f"/reprocess-report/{stored_job}")

    assert response.status_code == 200
    assert_sessions_were_returned(session_ledger, expected_at_least=1)


def test_final_stages_progression_returns_its_report_session(
    db_session, session_ledger, tmp_path, monkeypatch
):
    """The report-generation session in ``_handle_final_stages_progression_sync``.

    That call site needs a session with an empty identity map (generate_report's
    Job read carries no ``populate_existing()`` -- see
    tests/test_generator_job_metadata_read.py), which is why it opens a second
    one rather than reusing the long-lived session the function already holds.
    "Its own" still has to be given back.
    """
    import app.api.routes.upload_router as upload_router_module
    import app.reports.generator as generator_module
    from app.api.models import JobCreate
    from app.services.job_service import JobService

    job = JobService(db_session).create_job(
        JobCreate(workflow_type="genomic_analysis", name="final-stages-probe")
    )
    job.job_metadata = {
        "patient_id": "patient-final-stages",
        "data_id": str(uuid.uuid4()),
        "workflow": {"file_type": "vcf"},
    }
    db_session.commit()
    job_id = str(job.id)

    # Diagram renderers reach for Graphviz/Kroki; neither exists in unit tests.
    for name in (
        "render_with_graphviz",
        "render_kroki_mermaid_svg",
        "render_workflow",
        "render_simple_png_from_workflow",
    ):
        monkeypatch.setattr(upload_router_module, name, lambda *a, **k: b"")

    captured = {}

    def _fake_generate_report(*args, **kwargs):
        session = kwargs.get("db_session")
        captured["db_session"] = session
        # The real generate_report reads the Job off this session; do the same so
        # the session is actually used, which is what makes a leak observable.
        session.query(Job).filter(Job.id == uuid.UUID(job_id)).first()
        return {"pdf_path": None, "html_path": None}

    monkeypatch.setattr(generator_module, "generate_report", _fake_generate_report)
    upload_router_module._handle_final_stages_progression_sync(job_id, str(tmp_path))

    assert captured, "generate_report was never reached; this test proves nothing"
    # Two sessions: the function's own long-lived one and the fresh report session.
    assert_sessions_were_returned(session_ledger, expected_at_least=2)

    refreshed = (
        db_session.query(Job)
        .filter(Job.id == uuid.UUID(job_id))
        .populate_existing()
        .first()
    )
    assert refreshed.status != "failed"
