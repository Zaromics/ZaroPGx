"""Regression tests for stale, identity-mapped reads in ``JobService``.

``SessionLocal`` is built with ``expire_on_commit=False`` (``app/api/db.py``), so an
instance a session has already loaded is never expired. A plain
``db.query(Job).filter(...).first()`` therefore fetches the row and then *throws it
away*, handing back the instance already in that session's identity map. A
long-lived background session -- the Nextflow poll loop in ``upload_router`` re-reads
the job every 5 seconds for the whole run -- consequently never observes a write made
by any other session.

Every test here **crosses a session boundary**: it writes through one ``Session`` and
reads through a different one. That is the only shape that can catch this. A
single-session test passes with the bug fully present, which is exactly why it
shipped. ``commit()`` alone is not enough either -- with ``expire_on_commit=False``
committing the reader does not expose the reader's own stale instances.

Two details the tests depend on, and why:

* Each reader keeps a **strong reference** to the instance it loaded. SQLAlchemy's
  identity map holds weak references, so an unreferenced instance is garbage
  collected and the next query reloads it cleanly -- the bug would appear to fix
  itself. The poll loop does hold such a reference (its ``job`` local stays bound
  across the ``await asyncio.sleep(5)`` and across the following ``get_job`` call),
  so holding one here is faithful, not a contrivance.

  For ``JobStep`` this needs care: holding the *Job* is **not** enough. Refreshing
  the Job expires its ``steps`` collection, which drops the last strong reference to
  the step instances, and they are then collected and reloaded fresh -- which hides
  the very staleness the test is trying to catch. Tests that care about step values
  hold ``list(job.steps)`` directly.
* The tests use ``session_factory`` from ``conftest.py`` directly rather than the
  ``db_session`` fixture, because they need two independent sessions.
"""

import uuid

import pytest

from app.api.db import Job, JobStep
from app.api.models import (
    JobCreate,
    JobStatus,
    JobStepUpdate,
    JobUpdate,
    StepStatus,
)
from app.services.job_service import JobService


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


def _make_job(session, name="stale-read-probe"):
    job = JobService(session).create_job(
        JobCreate(workflow_type="genomic_analysis", name=name)
    )
    session.commit()
    return job.id


def _cancel_from_another_session(session_factory_fn, job_id):
    """Cancel the job the way ``POST /jobs/{id}/cancel`` does: a separate session."""
    writer = session_factory_fn()
    job = writer.query(Job).filter(Job.id == job_id).first()
    job.status = JobStatus.CANCELLED
    writer.commit()


# ---------------------------------------------------------------------------
# get_job
# ---------------------------------------------------------------------------


def test_get_job_sees_a_write_made_by_another_session(sessions):
    """The core defect: a second read must return the row, not the cached instance."""
    job_id = _make_job(sessions())

    reader = sessions()
    reader_service = JobService(reader)

    # Load it once, and hold the reference -- this is what puts the instance in
    # the reader's identity map and keeps it there.
    first = reader_service.get_job(job_id)
    assert first.status == "pending"

    _cancel_from_another_session(sessions, job_id)

    second = reader_service.get_job(job_id)
    assert second.status == "cancelled", (
        "get_job returned the stale identity-mapped instance instead of the row it "
        "just selected"
    )
    # Pin the reason the naive test would have passed: the reader never committed
    # or expired anything, and expire_on_commit=False means it never would have.
    assert first.status == "cancelled"


def test_get_job_refreshes_the_same_instance_rather_than_returning_a_new_one(sessions):
    """Callers may hold a Job reference across get_job calls; that must keep working.

    ``populate_existing()`` updates the instance in place, so an earlier reference
    stays valid *and* becomes current. If it ever started returning a fresh object
    instead, code holding the old one would silently go stale again.
    """
    job_id = _make_job(sessions())

    reader = sessions()
    reader_service = JobService(reader)
    first = reader_service.get_job(job_id)

    _cancel_from_another_session(sessions, job_id)

    second = reader_service.get_job(job_id)
    assert second is first


def test_get_job_does_not_discard_a_flushed_local_change(sessions):
    """The re-read must not undo work this session has already flushed.

    ``populate_existing()`` overwrites unflushed attribute values, so anything the
    caller wants to survive has to be in the transaction first. This pins the
    contract the service methods rely on.
    """
    reader = sessions()
    job_id = _make_job(reader)

    job = reader.query(Job).filter(Job.id == job_id).first()
    job.name = "renamed-then-flushed"
    reader.flush()

    assert JobService(reader).get_job(job_id).name == "renamed-then-flushed"


# ---------------------------------------------------------------------------
# The user-facing symptom: a cancelled job must stop the Nextflow poll loop.
# ---------------------------------------------------------------------------


def test_poll_loop_observes_a_mid_run_cancellation_and_skips_report_generation(
    sessions,
):
    """A job cancelled mid-run must be *observed* as cancelled by the monitor.

    This mirrors ``wait_for_nextflow_completion`` in ``app/api/routes/upload_router.py``:
    a background session polls the job, breaks out on ``status == "cancelled"``, and
    otherwise falls through to report generation when Nextflow reports completion.
    With the stale read, the cancellation check never fires and the monitor polls to
    completion and then generates reports for a job the user cancelled.
    """
    job_id = _make_job(sessions())

    # The background session: opened once, lives for the whole run.
    monitor_session = sessions()
    monitor = JobService(monitor_session)

    cancel_on_iteration = 3
    nextflow_completes_on_iteration = 6
    reports_generated = []
    observed = []

    for iteration in range(1, 10):
        # Top of loop: the cancellation check, as the router writes it.
        job = monitor.get_job(job_id)
        observed.append(job.status)
        if job and job.status == "cancelled":
            break

        if iteration == cancel_on_iteration:
            # The user hits cancel. Separate request, separate session.
            _cancel_from_another_session(sessions, job_id)

        if iteration >= nextflow_completes_on_iteration:
            # Nextflow says "completed" -> the router would generate reports here.
            reports_generated.append(job_id)
            break

    assert reports_generated == [], (
        "the poll loop generated reports for a cancelled job -- it never observed "
        "the cancellation"
    )
    assert observed[-1] == "cancelled"
    # It must notice on the very next poll after the cancel, not eventually.
    assert observed.count("cancelled") == 1
    assert len(observed) == cancel_on_iteration + 1


def test_wait_deadline_guard_reads_a_fresh_status(sessions):
    """The deadline guard checks ``getattr(job, "status")`` against terminal states.

    It is one-directional-safe -- a stale value can only make it *skip* the
    leave-it-alone branch and mark a job failed that another session already
    finished. It has to see the fresh status to do the right thing.
    """
    terminal = {"completed", "failed", "cancelled"}
    job_id = _make_job(sessions())

    monitor_session = sessions()
    monitor = JobService(monitor_session)
    job = monitor.get_job(job_id)
    assert job.status not in terminal

    finisher = sessions()
    finished = finisher.query(Job).filter(Job.id == job_id).first()
    finished.status = JobStatus.COMPLETED
    finisher.commit()

    # Deadline hits now: the guard must see COMPLETED and leave the status alone.
    job = monitor.get_job(job_id)
    assert getattr(job, "status", None) in terminal


# ---------------------------------------------------------------------------
# Sibling read methods
# ---------------------------------------------------------------------------


def test_get_job_progress_sees_step_updates_from_another_session(sessions):
    """Step rows are written by the container services, on their own sessions.

    The ``held_steps`` list is the whole test. Keeping the *Job* alive is not enough:
    ``populate_existing()`` expires the ``steps`` collection, which drops the last
    strong reference to the JobStep instances, and the weak identity map then lets
    them be garbage collected so the reload builds fresh objects. That hides the
    defect. Holding the instances -- as anything that walks ``job.steps`` across a
    poll does -- keeps them in the identity map, where a plain lazy reload hands back
    their stale column values. Only ``selectinload`` + ``populate_existing`` refreshes
    them in place.
    """
    job_id = _make_job(sessions())

    reader = sessions()
    reader_service = JobService(reader)

    job = reader_service.get_job(job_id)
    held_steps = list(job.steps)  # strong refs to the ORM instances themselves
    assert [step.status for step in held_steps] == ["pending"] * len(held_steps)
    before = reader_service.get_job_progress(job_id)

    # A container completes its step through its own request-scoped session.
    worker = sessions()
    JobService(worker).update_job_step(
        job_id, "header_analysis", JobStepUpdate(status=StepStatus.COMPLETED)
    )

    after = reader_service.get_job_progress(job_id)
    assert after.progress_percentage > before.progress_percentage, (
        "get_job_progress computed progress from the step statuses this session "
        "loaded the first time"
    )
    assert [s.status for s in held_steps if s.step_name == "header_analysis"] == [
        "completed"
    ], "the held JobStep instances were not refreshed in place"


def test_get_job_steps_sees_step_updates_from_another_session(sessions):
    job_id = _make_job(sessions())

    reader = sessions()
    reader_service = JobService(reader)
    job = reader_service.get_job(job_id)
    held_steps = list(job.steps)  # strong refs, or the reload would build fresh objects
    assert all(step.status == "pending" for step in held_steps)

    worker = sessions()
    JobService(worker).update_job_step(
        job_id, "header_analysis", JobStepUpdate(status=StepStatus.COMPLETED)
    )

    statuses = {
        step.step_name: step.status for step in reader_service.get_job_steps(job_id)
    }
    assert statuses["header_analysis"] == "completed"
    # get_job_steps queries JobStep directly, so populate_existing refreshes the held
    # instances in place -- no eager-load option needed on that path.
    assert [s.status for s in held_steps if s.step_name == "header_analysis"] == [
        "completed"
    ]


def test_get_pharmcat_run_id_sees_a_link_made_by_another_session(sessions):
    job_id = _make_job(sessions())

    reader = sessions()
    reader_service = JobService(reader)
    held = reader_service.get_job(job_id)
    assert reader_service.get_pharmcat_run_id(job_id) is None

    worker = sessions()
    assert JobService(worker).link_pharmcat_run(str(job_id), "pcat-run-cross") is True

    assert reader_service.get_pharmcat_run_id(job_id) == "pcat-run-cross"
    assert held.job_metadata.get("pharmcat_run_id") == "pcat-run-cross"


def test_link_pharmcat_run_does_not_clobber_metadata_written_elsewhere(sessions):
    """``link_pharmcat_run`` replaces the whole metadata dict, so it must re-read it.

    Merging onto a stale copy drops every key another session added since -- including
    the ``cancelled`` flag the cancel endpoint writes.
    """
    job_id = _make_job(sessions())

    linker = sessions()
    linker_service = JobService(linker)
    held = linker_service.get_job(job_id)  # loads the pre-cancellation metadata
    assert "cancelled" not in (held.job_metadata or {})

    # The cancel endpoint stamps its own keys, from its own session.
    canceller = sessions()
    cancel_service = JobService(canceller)
    cancel_metadata = dict(cancel_service.get_job(job_id).job_metadata or {})
    cancel_metadata["cancelled"] = True
    cancel_service.update_job(
        job_id, JobUpdate(status=JobStatus.CANCELLED, metadata=cancel_metadata)
    )

    assert linker_service.link_pharmcat_run(str(job_id), "pcat-run-merge") is True

    verifier = sessions()
    stored = verifier.query(Job).filter(Job.id == job_id).first().job_metadata
    assert stored["pharmcat_run_id"] == "pcat-run-merge"
    assert stored["cancelled"] is True, (
        "link_pharmcat_run merged onto a stale metadata dict and dropped the "
        "cancellation flag"
    )


# ---------------------------------------------------------------------------
# get_job_logs is deliberately left alone -- this pins why.
# ---------------------------------------------------------------------------


def test_get_job_logs_needs_no_re_read_because_log_rows_are_insert_only(sessions):
    """``JobLog`` rows are never UPDATEd, so an identity-mapped copy cannot be wrong.

    A log written by another session is a *new* row with a new identity key, which
    the query loads in full. Adding ``populate_existing()`` there would buy nothing
    and cost a re-populate per row on a path that returns up to 100 of them.
    """
    from app.api.models import JobLogCreate, LogLevel

    job_id = _make_job(sessions())

    reader = sessions()
    reader_service = JobService(reader)
    assert reader_service.get_job_logs(job_id, limit=100) is not None
    baseline = len(reader_service.get_job_logs(job_id, limit=100))

    worker = sessions()
    JobService(worker).log_job_event(
        job_id,
        JobLogCreate(step_name=None, log_level=LogLevel.INFO, message="from elsewhere"),
    )

    messages = [log.message for log in reader_service.get_job_logs(job_id, limit=100)]
    assert len(messages) == baseline + 1
    assert "from elsewhere" in messages


# ---------------------------------------------------------------------------
# The re-read must not eat this session's own pending write.
# ---------------------------------------------------------------------------


def test_completed_steps_written_by_update_job_progress_reaches_the_database(sessions):
    """``_update_job_progress`` mutates ``completed_steps`` then re-reads the row.

    Without the ``flush()`` in between, the re-read overwrites the pending value and
    clears its attribute history, so the commit emits no UPDATE and
    ``jobs.completed_steps`` stays at 0 forever -- this method is its only writer.
    """
    worker = sessions()
    job_id = _make_job(worker)

    service = JobService(worker)
    service.update_job_step(
        job_id, "header_analysis", JobStepUpdate(status=StepStatus.COMPLETED)
    )

    verifier = sessions()
    stored = verifier.query(Job).filter(Job.id == job_id).first()
    assert stored.completed_steps == 1

    service.update_job_step(
        job_id, "pharmcat_analysis", JobStepUpdate(status=StepStatus.COMPLETED)
    )
    verifier2 = sessions()
    assert verifier2.query(Job).filter(Job.id == job_id).first().completed_steps == 2


def test_get_job_progress_survives_a_running_job_with_started_at(sessions):
    """The estimated-completion branch must not blow up on a naive ``started_at``.

    Pre-existing, and not caused by the re-read: on a backend without timezone
    support (SQLite) ``started_at`` comes back naive, and subtracting it from an aware
    ``datetime.now(timezone.utc)`` raises TypeError. ``get_job_progress`` wraps that
    into RuntimeError, which ``_update_job_progress`` does not catch, so it propagates
    and takes the whole ``update_job_step`` call down. Reachable for any RUNNING job
    with a started_at and non-zero progress -- i.e. every job, for most of its life.
    """
    worker = sessions()
    job_id = _make_job(worker)
    service = JobService(worker)

    service.update_job(job_id, JobUpdate(status=JobStatus.RUNNING))
    # This is the call that used to raise, via _update_job_progress.
    service.update_job_step(
        job_id, "header_analysis", JobStepUpdate(status=StepStatus.COMPLETED)
    )

    progress = service.get_job_progress(job_id)
    assert progress is not None
    assert progress.progress_percentage > 0
    assert progress.estimated_completion is not None


def test_get_job_still_returns_none_for_an_unknown_id(sessions):
    reader = sessions()
    assert JobService(reader).get_job(uuid.uuid4()) is None


def test_get_job_still_rejects_a_malformed_id(sessions):
    reader = sessions()
    with pytest.raises(ValueError, match="Invalid job_id format"):
        JobService(reader).get_job("not-a-uuid")


def test_step_rows_are_reachable_after_a_re_read(sessions):
    """``populate_existing()`` expires the loaded collection; it must reload, not empty."""
    job_id = _make_job(sessions())

    reader = sessions()
    service = JobService(reader)
    job = service.get_job(job_id)
    original = {step.step_name for step in job.steps}
    assert original

    refreshed = service.get_job(job_id)
    assert {step.step_name for step in refreshed.steps} == original
    assert reader.query(JobStep).filter(JobStep.job_id == job_id).count() == len(
        original
    )
