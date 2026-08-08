"""``PharmCATDataService`` reads and writes ``jobs.job_metadata`` across sessions.

``JobService`` was hardened for this on six reads (tests/test_stale_job_reads.py).
``app/services/pharmcat_data_service.py`` does the same three things to the same
column and was missed, even though it is reached from
``app/api/routes/pharmcat_router.py`` (``/pharmcat/workflow/{id}/summary`` and
``/data``) and from ``app/services/fhir_export_service.py`` (both export paths).

Two distinct defects, one column:

1. **Stale identity-mapped reads.** ``SessionLocal`` is built with
   ``expire_on_commit=False`` (``app/api/db.py``), so a plain
   ``db.query(Job).filter(...).first()`` fetches the row and then discards it in
   favour of the instance already in that session's identity map.
   ``pharmcat_run_id`` is written by whichever session finished the PharmCAT
   stage -- never the session asking -- so the report and FHIR routes read
   ``job_metadata`` as it stood when their session first loaded the job, and
   answer "no PharmCAT data found" for a run that completed minutes ago.

2. **The silent no-op write.** ``link_pharmcat_run_to_workflow`` was a
   read-modify-write of ``job_metadata`` *without* the ``dict()`` copy that
   ``JobService.link_pharmcat_run`` argues at length. ``jobs.job_metadata`` is a
   plain ``Column(JSON)`` with no ``MutableDict``, so mutating the attached dict
   and assigning the same object back leaves the attribute history empty:
   SQLAlchemy emits **no UPDATE**, ``commit()`` succeeds, the method returns
   ``True``, and nothing persists.

**Every test here crosses a session boundary**, and that is the point. With
``expire_on_commit=False`` a ``commit()``-only test passes with both bugs fully
present -- the writer's own session keeps handing back its in-memory instance,
mutation and all. That is exactly how the original instance of this defect
survived into production. Each test therefore writes through one ``Session`` and
verifies through a *different* one.

The readers also hold a strong reference to the instance they loaded, because
SQLAlchemy's identity map holds weak references: an unreferenced instance is
garbage collected and the next query reloads it cleanly, which would make the
bug appear to fix itself. Holding one is faithful to the request handlers, which
keep the ``job`` local alive across the calls in question.
"""

import uuid

import pytest

from app.api.db import Job
from app.api.models import JobCreate
from app.services.job_service import JobService
from app.services.pharmcat_data_service import PharmCATDataService


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


def _make_job(session, **metadata):
    job = JobService(session).create_job(
        JobCreate(
            name="pharmcat-data-service-probe",
            workflow_type="genomic_analysis",
            metadata=metadata or {"patient_id": "PAT-1"},
            created_by="pytest",
        )
    )
    session.commit()
    return job.id


def _link_from_another_session(session_factory_fn, job_id, run_id):
    """Link the run the way the PharmCAT stage does: its own session."""
    writer = session_factory_fn()
    assert (
        PharmCATDataService(writer).link_pharmcat_run_to_workflow(str(job_id), run_id)
        is True
    )
    return writer


# ---------------------------------------------------------------------------
# 2. The write: it has to reach the row, not just the instance
# ---------------------------------------------------------------------------


def test_link_reaches_the_database_and_not_only_the_identity_map(sessions):
    """The no-UPDATE bug, observed the only way it can be: from another session.

    Fails against unfixed code -- without the ``dict()`` copy SQLAlchemy sees no
    attribute history on the bare ``Column(JSON)``, emits no UPDATE, and the
    reader session below finds a jobs row with no ``pharmcat_run_id`` in it even
    though ``link_pharmcat_run_to_workflow`` returned True.
    """
    job_id = _make_job(sessions())

    writer = sessions()
    assert (
        PharmCATDataService(writer).link_pharmcat_run_to_workflow(
            str(job_id), "pcat-run-001"
        )
        is True
    )

    # A different session, therefore a different identity map: this reads the row.
    reader = sessions()
    row = reader.query(Job).filter(Job.id == job_id).first()
    assert row.job_metadata.get("pharmcat_run_id") == "pcat-run-001", (
        "link_pharmcat_run_to_workflow returned True but the UPDATE was never "
        "emitted -- job_metadata was mutated in place and reassigned to itself"
    )
    assert row.job_metadata.get("pharmcat_linked_at")


def test_link_preserves_metadata_written_by_another_session(sessions):
    """The read-modify-write replaces the whole dict, so it must start from the row.

    Without ``populate_existing()`` the writer merges into whatever it cached at
    first load, and every key another session added since -- the cancel
    endpoint's flag among them -- is silently dropped by the write-back.
    """
    job_id = _make_job(sessions(), patient_id="PAT-7")

    writer = sessions()
    service = PharmCATDataService(writer)

    # The writer loads the job once and keeps it: this is what populates -- and
    # pins -- its identity map.
    held = writer.query(Job).filter(Job.id == job_id).first()
    assert "cancelled" not in (held.job_metadata or {})

    # Meanwhile, another request cancels the job and stamps the flag.
    canceller = sessions()
    target = canceller.query(Job).filter(Job.id == job_id).first()
    target.job_metadata = dict(target.job_metadata or {}, cancelled=True)
    canceller.commit()

    assert service.link_pharmcat_run_to_workflow(str(job_id), "pcat-run-002") is True

    reader = sessions()
    metadata = reader.query(Job).filter(Job.id == job_id).first().job_metadata
    assert metadata.get("pharmcat_run_id") == "pcat-run-002"
    assert metadata.get("patient_id") == "PAT-7", "seeded metadata was dropped"
    assert metadata.get("cancelled") is True, (
        "the read-modify-write started from a stale copy and clobbered the "
        "cancellation flag another session had already committed"
    )


def test_link_is_visible_to_job_service_which_reads_the_same_key(sessions):
    """The two services write and read one key; they must agree across sessions."""
    job_id = _make_job(sessions())

    _link_from_another_session(sessions, job_id, "pcat-run-003")

    reader = sessions()
    assert JobService(reader).get_pharmcat_run_id(str(job_id)) == "pcat-run-003"


# ---------------------------------------------------------------------------
# 1. The reads: they must see a link made by another session
# ---------------------------------------------------------------------------


def test_get_workflow_pharmcat_summary_sees_a_link_made_elsewhere(
    sessions, monkeypatch
):
    """``/pharmcat/workflow/{id}/summary`` 404s on a completed run without this.

    The summary body itself comes from ``get_pharmcat_summary`` and needs real
    PharmCAT tables; it is stubbed, because what is under test is whether the
    service gets as far as *finding* the run id at all.
    """
    job_id = _make_job(sessions())

    reader = sessions()
    service = PharmCATDataService(reader)

    seen = []
    monkeypatch.setattr(
        "app.services.pharmcat_data_service.get_pharmcat_summary",
        lambda run_id, db: seen.append(run_id) or {"run_id": run_id},
    )

    # First call: no link yet. This is the call that seeds the reader's identity
    # map with a Job whose job_metadata has no pharmcat_run_id.
    assert service.get_workflow_pharmcat_summary(str(job_id)) is None
    held = reader.query(Job).filter(Job.id == job_id).first()  # strong ref
    assert "pharmcat_run_id" not in (held.job_metadata or {})

    # The PharmCAT stage finishes, on its own session.
    _link_from_another_session(sessions, job_id, "pcat-run-004")

    assert service.get_workflow_pharmcat_summary(str(job_id)) == {
        "run_id": "pcat-run-004"
    }, "the summary route read its cached job_metadata and never saw the link"
    assert seen == ["pcat-run-004"]


def test_get_pharmcat_data_for_workflow_sees_a_link_made_elsewhere(
    sessions, monkeypatch
):
    """Same defect on the ``/data`` route and on both FHIR export paths.

    ``app/services/fhir_export_service.py`` calls this method twice, and a None
    return there is reported to the user as "No PharmCAT data found for
    workflow: <id>" -- for a workflow that has data.
    """
    job_id = _make_job(sessions())

    reader = sessions()
    service = PharmCATDataService(reader)

    monkeypatch.setattr(
        PharmCATDataService,
        "_get_normalized_pharmcat_data",
        lambda self, parser, run_id: {"run_id": run_id},
    )

    class _NullParser:
        def __init__(self, db):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(
        "app.services.pharmcat_data_service.PharmCATParser", _NullParser
    )

    assert service.get_pharmcat_data_for_workflow(str(job_id)) is None
    held = reader.query(Job).filter(Job.id == job_id).first()  # strong ref
    assert "pharmcat_run_id" not in (held.job_metadata or {})

    _link_from_another_session(sessions, job_id, "pcat-run-005")

    assert service.get_pharmcat_data_for_workflow(str(job_id)) == {
        "run_id": "pcat-run-005"
    }, "the data route read its cached job_metadata and never saw the link"


# ---------------------------------------------------------------------------
# Contract fences
# ---------------------------------------------------------------------------


def test_reads_refresh_the_instance_in_place(sessions):
    """``populate_existing()`` updates the held instance rather than replacing it.

    Callers hold ``job`` references across these calls; refreshing in place is
    what makes an earlier reference stay valid *and* become current.
    """
    job_id = _make_job(sessions())

    reader = sessions()
    service = PharmCATDataService(reader)
    held = reader.query(Job).filter(Job.id == job_id).first()

    _link_from_another_session(sessions, job_id, "pcat-run-006")
    service.get_workflow_pharmcat_summary(str(job_id))

    assert held.job_metadata.get("pharmcat_run_id") == "pcat-run-006"


def test_an_unknown_workflow_id_is_not_an_error(sessions):
    """A missing job, and a non-UUID id, both mean "no data" rather than a 500."""
    service = PharmCATDataService(sessions())
    assert service.get_workflow_pharmcat_summary(str(uuid.uuid4())) is None
    assert service.get_pharmcat_data_for_workflow(str(uuid.uuid4())) is None
    assert service.get_workflow_pharmcat_summary("not-a-uuid") is None
    assert service.link_pharmcat_run_to_workflow("not-a-uuid", "run") is False
