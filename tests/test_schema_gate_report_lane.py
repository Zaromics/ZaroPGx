"""The PharmCAT schema gate must have teeth on the report lane, not just the DB.

``validate_report`` landed as a gate in front of ``PharmCATParser.parse_and_load``,
so a payload whose structure has moved away from what the walkers read can no
longer be written to the database. The report lane, which is the lane a clinician
actually reads, ignored it completely:

* ``_handle_final_stages_progression_sync`` caught ``PharmCATSchemaError`` in a
  blanket ``except Exception as db_error`` and logged it as a *database* failure,
  pointing the operator at a migration script for a problem that has nothing to do
  with the database;
* it then did ``pharmcat_data = pharmcat_results`` unconditionally and handed the
  rejected payload straight to ``generate_report``;
* the TSV fallback below could never rescue it, because it is guarded on
  ``not pharmcat_data.get("genes")`` and ``genes`` is non-empty in every rejection
  mode -- a payload is refused for the *shape* of its gene blocks, not for having
  none.

So a payload the gate had just declared unreadable still became a clinical report.

The property pinned here is the blunt one: **a payload the gate rejects does not
become a report.** Every test below drives the real
``_handle_final_stages_progression_sync`` over a real ``report.json`` on disk and
through the real ``validate_report``; nothing about the gate is stubbed.

Harness note, inherited from tests/test_upload_router_skip_report.py: the function
wraps its body in a blanket ``except Exception`` that marks the job FAILED, so an
``AssertionError`` raised from inside a stub is swallowed and reported as an
ordinary failure. Observations are therefore recorded on fakes and asserted after
the call returns.
"""

import json

import pytest

import app.api.routes.upload_router as ur
from app.api.models import JobStatus, StepStatus

JOB_ID = "job-schema-gate"
PATIENT_ID = "patient-schema-gate"


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------

# Passes the gate: PharmCAT 3.4 flat layout with a readable gene block.
GOOD_REPORT = {
    "pharmcatVersion": "3.4.0",
    "genes": {
        "CYP2C19": {
            "geneSymbol": "CYP2C19",
            "sourceDiplotypes": [{"label": "*1/*2"}],
        }
    },
    "drugs": {},
}

# Refused: every gene block has lost ``sourceDiplotypes``, so every diplotype,
# phenotype and activity score would be dropped and every gene would render
# Unknown/Unknown. ``genes`` is still non-empty -- which is exactly why the old
# ``not pharmcat_data.get("genes")`` TSV guard never fired on it.
BROKEN_REPORT = {
    "pharmcatVersion": "3.4.0",
    "genes": {
        "CYP2C19": {"geneSymbol": "CYP2C19"},
        "CYP2D6": {"geneSymbol": "CYP2D6"},
    },
    "drugs": {},
}

# Refused a different way: ``sourceDiplotypes`` is a mapping, which the parser
# iterates and then calls ``.get`` on each element of.
MISTYPED_REPORT = {
    "pharmcatVersion": "3.4.0",
    "genes": {
        "CYP2C19": {
            "geneSymbol": "CYP2C19",
            "sourceDiplotypes": {"label": "*1/*2"},
        }
    },
    "drugs": {},
}

PHARMCAT_TSV = (
    "Gene\tSource Diplotype\tPhenotype\tActivity Score\tOutside Call\n"
    "CYP2C19\t*1/*2\tIntermediate Metabolizer\t\tno\n"
)


def test_the_broken_payloads_really_are_refused_by_the_gate():
    """Guard the fixtures themselves: a payload that quietly starts passing the
    gate would turn every assertion below into a tautology."""
    from app.pharmcat.report_json import validate_report

    assert validate_report(GOOD_REPORT).ok
    assert not validate_report(BROKEN_REPORT).ok
    assert not validate_report(MISTYPED_REPORT).ok


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class _FakeSession:
    def close(self):
        pass


class _FakeJob:
    def __init__(self, metadata):
        self.id = JOB_ID
        self.status = "running"
        self.job_metadata = metadata


class _FakeJobService:
    def __init__(self, db=None, metadata=None, events=None):
        self.db = db
        self.job = _FakeJob(metadata or {})
        self.updates = []
        self.step_updates = []
        self.logs = []
        self.linked = []
        self.appended_warnings = []
        # Shared ordering tape: every event the rescue-warning constraint is
        # about lands here in the order it happened.
        self.events = events if events is not None else []

    def get_job(self, job_id):
        return self.job

    def append_workflow_warning(self, job_id, warning):
        self.appended_warnings.append(warning)
        self.events.append("warning-committed")
        # Mirror the real method: read-modify-write onto a *new* dict, which is
        # what makes the router's earlier `metadata` local go stale.
        metadata = dict(self.job.job_metadata or {})
        workflow = dict(metadata.get("workflow") or {})
        workflow["warnings"] = list(workflow.get("warnings") or []) + [warning]
        metadata["workflow"] = workflow
        self.job.job_metadata = metadata
        return True

    def update_job(self, job_id, update):
        self.updates.append(update)
        # The real one replaces job_metadata wholesale, with no merge
        # (job_service.py:371-372). A fake that only records the call cannot
        # show a mid-function write being erased by a stale snapshot, which is
        # exactly the bug test_the_rescue_warning_survives_the_final_metadata_update
        # exists for.
        if getattr(update, "metadata", None) is not None:
            self.job.job_metadata = update.metadata
        return self.job

    def update_job_step(self, job_id, step_name, update):
        self.step_updates.append((step_name, update))
        return None

    def log_job_event(self, job_id, log_data):
        self.logs.append(log_data)

    def link_pharmcat_run(self, job_id, run_id):
        self.linked.append(run_id)

    def _broadcast_job_update(self, job_id, payload):
        return None

    def statuses(self):
        return [getattr(u, "status", None) for u in self.updates]

    def messages(self):
        return " | ".join(getattr(log, "message", "") or "" for log in self.logs)

    def failed_steps(self):
        return [
            (name, update)
            for name, update in self.step_updates
            if getattr(update, "status", None) == StepStatus.FAILED
        ]


@pytest.fixture
def final_stage(monkeypatch, tmp_path, caplog):
    """Drive the real final-stage worker with only the network stubbed out.

    ``load_pharmcat_file`` is deliberately *not* stubbed. It reaches
    ``PharmCATParser.parse_and_load``, which calls ``validate_report`` before it
    touches the session, so the real gate runs and raises before any database
    access -- the fake session below is never used on the rejection path.
    """
    import app.api.db as api_db
    import app.reports.generator as generator

    monkeypatch.setattr(ur, "schedule_coroutine", lambda *a, **k: None)

    monkeypatch.setattr(ur, "render_with_graphviz", lambda *a, **k: None)
    monkeypatch.setattr(ur, "render_kroki_mermaid_svg", lambda *a, **k: None)
    monkeypatch.setattr(ur, "render_workflow", lambda *a, **k: None)
    monkeypatch.setattr(ur, "render_simple_png_from_workflow", lambda *a, **k: None)

    # The ordering tape the TSV-rescue warning's constraint is pinned against:
    # the warning has to be committed before generate_report's own session is
    # even opened, because that session's empty identity map is the only reason
    # generator.py's plain Job read sees it (tests/test_generator_job_metadata_read.py).
    events = []

    report_calls = []

    def _fake_generate_report(**kwargs):
        events.append("generate_report-called")
        report_calls.append(kwargs)
        return {"pdf_path": "/x.pdf"}

    monkeypatch.setattr(generator, "generate_report", _fake_generate_report)

    class _RecordingSessionLocal(_FakeSession):
        def __init__(self):
            super().__init__()
            events.append("session-opened")

    # Both bindings, because the report lane's `db_session = SessionLocal()`
    # resolves to the function-local `from app.api.db import SessionLocal` at
    # the top of _handle_final_stages_progression_sync, not to the module
    # global -- and the day that local import goes away, the global is what
    # runs. Patching one only would silently stop recording.
    monkeypatch.setattr(api_db, "SessionLocal", _RecordingSessionLocal)
    monkeypatch.setattr(ur, "SessionLocal", _RecordingSessionLocal)

    def _run(report_payload=None, tsv=None, needs_report=True):
        outdir = tmp_path / PATIENT_ID / JOB_ID
        outdir.mkdir(parents=True, exist_ok=True)
        if report_payload is not None:
            (outdir / f"{JOB_ID}_pgx_pharmcat.json").write_text(
                json.dumps(report_payload), encoding="utf-8"
            )
        if tsv is not None:
            (outdir / f"{JOB_ID}_pgx_pharmcat.tsv").write_text(tsv, encoding="utf-8")

        service = _FakeJobService(
            metadata={
                "patient_id": PATIENT_ID,
                "data_id": "data-1",
                "workflow": {"needs_report": needs_report},
            },
            events=events,
        )
        monkeypatch.setattr(ur, "JobService", lambda db: service)
        caplog.clear()
        ur._handle_final_stages_progression_sync(JOB_ID, str(outdir))
        return service, report_calls

    return _run


# ---------------------------------------------------------------------------
# The property: a rejected payload does not become a report
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload", [BROKEN_REPORT, MISTYPED_REPORT], ids=["no-diplotypes", "mistyped"]
)
def test_a_rejected_payload_never_reaches_the_report_generator(final_stage, payload):
    """The one property. No report is built from a payload the gate refused."""
    service, report_calls = final_stage(report_payload=payload)

    assert not report_calls, (
        "a payload the schema gate rejected was handed to generate_report: "
        f"{report_calls}"
    )


@pytest.mark.parametrize(
    "payload", [BROKEN_REPORT, MISTYPED_REPORT], ids=["no-diplotypes", "mistyped"]
)
def test_a_rejected_payload_with_no_tsv_fails_the_job(final_stage, payload):
    """Failing is the honest outcome: there is nothing left to report from.

    Completing the job with an empty report would be worse than failing, because
    an empty report is indistinguishable from a clean one.
    """
    service, _ = final_stage(report_payload=payload)

    assert JobStatus.FAILED in service.statuses(), (
        "the job completed despite having no readable PharmCAT results: "
        f"{service.statuses()}"
    )
    assert JobStatus.COMPLETED not in service.statuses()


def test_the_failure_names_the_schema_gate_not_the_database(final_stage, caplog):
    """The operator must be sent to PharmCAT, not to a database migration.

    The old code logged a schema rejection through the ``value too long`` /
    ``varchar`` branch's sibling as "Failed to load PharmCAT data into database",
    which names the wrong system entirely.
    """
    service, _ = final_stage(report_payload=BROKEN_REPORT)

    reason = service.messages()
    assert "PharmCAT" in reason, reason
    assert (
        "structure gate" in reason or "schema gate" in reason
    ), f"the failure does not say the payload was refused by the gate: {reason}"
    assert "database" not in reason.lower(), (
        "a schema rejection is still being reported as a database failure: " f"{reason}"
    )
    # And the gate's own diagnosis reaches the log, so the operator can see which
    # part of the payload moved.
    assert "sourceDiplotypes" in caplog.text or "source_diplotypes" in caplog.text


def test_the_report_generation_step_is_marked_failed(final_stage):
    """The UI reads step status; a job that dies here must not leave the step
    sitting at RUNNING/100%."""
    service, _ = final_stage(report_payload=BROKEN_REPORT)

    failed = service.failed_steps()
    assert failed, f"no step was marked FAILED: {service.step_updates}"
    names = [name for name, _ in failed]
    assert "report_generation" in names, names
    details = [
        getattr(update, "error_details", None) or {}
        for name, update in failed
        if name == "report_generation"
    ]
    assert any(d.get("reason") == "pharmcat_schema_gate" for d in details), details


# ---------------------------------------------------------------------------
# The one honest way out: PharmCAT's own TSV
# ---------------------------------------------------------------------------


def test_a_rejected_payload_falls_back_to_the_pharmcat_tsv(final_stage):
    """The TSV is a *different file* read by a *different parser*, so believing it
    is not believing the payload that was refused."""
    service, report_calls = final_stage(report_payload=BROKEN_REPORT, tsv=PHARMCAT_TSV)

    assert len(report_calls) == 1, "the TSV fallback did not rescue the run"
    assert JobStatus.COMPLETED in service.statuses(), service.statuses()

    # And what it was handed is the TSV's data, not the rejected payload.
    data = report_calls[-1]["pharmcat_results"]["data"]
    assert data is not BROKEN_REPORT
    genes = data["genes"]["CPIC"]
    assert "CYP2C19" in genes
    assert genes["CYP2C19"]["sourceDiplotypes"], genes["CYP2C19"]


def test_an_empty_tsv_is_not_a_rescue(final_stage):
    """A TSV with a header and no rows yields no gene blocks. That is still
    nothing to report from, so the job must still fail."""
    service, report_calls = final_stage(
        report_payload=BROKEN_REPORT,
        tsv="Gene\tSource Diplotype\tPhenotype\tActivity Score\tOutside Call\n",
    )

    assert not report_calls
    assert JobStatus.FAILED in service.statuses()


# ---------------------------------------------------------------------------
# The gate must not become a new way to break working runs
# ---------------------------------------------------------------------------


def test_a_payload_that_passes_the_gate_still_becomes_a_report(final_stage):
    service, report_calls = final_stage(report_payload=GOOD_REPORT)

    assert len(report_calls) == 1
    assert JobStatus.COMPLETED in service.statuses()
    assert report_calls[-1]["pharmcat_results"]["data"] == GOOD_REPORT


def test_a_rejected_payload_fails_even_a_job_that_wanted_no_report(final_stage):
    """Opting out of reports is not opting out of knowing the analysis failed.

    The same rejection also kept the run out of the database -- the gate raises
    before the first write -- so a needs_report=False job would otherwise
    complete having produced no usable PharmCAT data anywhere, silently. The
    check therefore sits ahead of the needs_report gate, not inside it.
    """
    service, report_calls = final_stage(
        report_payload=BROKEN_REPORT, needs_report=False
    )

    assert not report_calls
    assert JobStatus.FAILED in service.statuses(), service.statuses()


def test_turning_reports_off_is_otherwise_still_honoured(final_stage):
    """The new failure must not become a second way to break the skip path."""
    service, report_calls = final_stage(report_payload=GOOD_REPORT, needs_report=False)

    assert not report_calls, "reports were generated for a skip_report job"
    assert JobStatus.COMPLETED in service.statuses(), service.statuses()


def test_a_missing_report_json_is_unchanged(final_stage):
    """No report.json at all is not a gate rejection, and the pre-existing
    behaviour (generate what there is) must survive this change."""
    service, report_calls = final_stage(report_payload=None)

    assert len(report_calls) == 1
    assert JobStatus.COMPLETED in service.statuses()


def test_a_missing_report_json_with_a_tsv_still_uses_the_tsv(final_stage):
    service, report_calls = final_stage(report_payload=None, tsv=PHARMCAT_TSV)

    assert len(report_calls) == 1
    data = report_calls[-1]["pharmcat_results"]["data"]
    assert "CYP2C19" in data["genes"]["CPIC"]
    assert JobStatus.COMPLETED in service.statuses()


# ---------------------------------------------------------------------------
# _has_readable_gene_blocks: the usability test the failure decision rests on
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# The rescue is not silent: the clinician is told the TSV built this report
# ---------------------------------------------------------------------------
#
# Before this, a rescued run reached the reader looking exactly like a clean
# one. The only trace was a logger.warning, which no clinician reads. The
# unverified-version banner (265) already solved the same surfacing problem via
# `workflow_warnings`, which both report templates render in "Alerts and
# Warnings"; this follows that channel, from the other end -- the job row's
# metadata, which generator.py reads at :1880.


def test_a_tsv_rescued_run_tells_the_reader_it_was_rescued(final_stage):
    """Exactly one warning, appended to the channel the templates read."""
    service, report_calls = final_stage(report_payload=BROKEN_REPORT, tsv=PHARMCAT_TSV)

    assert len(report_calls) == 1, "the TSV fallback did not rescue the run"
    assert len(service.appended_warnings) == 1, (
        "a TSV-rescued run did not put exactly one warning in front of the "
        f"reader: {service.appended_warnings}"
    )


def test_the_rescue_warning_is_committed_before_the_report_session_opens(final_stage):
    """The load-bearing ordering constraint, pinned as a tape.

    ``generate_report`` reads ``workflow.warnings`` off the Job row with a plain
    query, no ``populate_existing()``. That read only observes another session's
    write because ``upload_router`` hands it a session opened *after* every such
    write and never used before (tests/test_generator_job_metadata_read.py). So
    the warning must be committed before ``db_session = SessionLocal()`` runs --
    not merely before ``generate_report`` is called.

    The tape's first "session-opened" is the worker's own long-lived ``db``; the
    second is the dedicated report session. The warning has to land between them.
    """
    service, _ = final_stage(report_payload=BROKEN_REPORT, tsv=PHARMCAT_TSV)

    assert service.events == [
        "session-opened",  # the worker's own db
        "warning-committed",
        "session-opened",  # generate_report's dedicated, empty-identity-map session
        "generate_report-called",
    ], service.events


def test_the_rescue_warning_says_what_happened_and_why(final_stage):
    """Honest copy: name the gate's reason, name the TSV, don't overclaim."""
    service, _ = final_stage(report_payload=BROKEN_REPORT, tsv=PHARMCAT_TSV)

    warning = service.appended_warnings[0]

    # It names the file that was refused and the file the report came from.
    assert "report.json" in warning, warning
    assert "TSV" in warning, warning
    # It carries the gate's own diagnosis, not just "validation failed".
    assert "sourceDiplotypes" in warning, warning
    # And it does not claim the rejected file is out of the picture entirely:
    # probe_matcher_metadata globs *_pgx_pharmcat.json, so genome build and
    # matcher version are still read from it.
    assert "genome build" in warning.lower(), warning
    assert "matcher" in warning.lower(), warning


def test_a_clean_run_gets_no_rescue_warning(final_stage):
    """The banner must not become noise on every report."""
    service, report_calls = final_stage(report_payload=GOOD_REPORT)

    assert len(report_calls) == 1
    assert service.appended_warnings == [], service.appended_warnings
    assert "warning-committed" not in service.events, service.events
    assert service.events[-1] == "generate_report-called", service.events


def test_a_run_with_no_report_json_at_all_gets_no_rescue_warning(final_stage):
    """A missing report.json is not a gate rejection, so nothing was rescued."""
    service, report_calls = final_stage(report_payload=None, tsv=PHARMCAT_TSV)

    assert len(report_calls) == 1
    assert service.appended_warnings == [], service.appended_warnings


@pytest.mark.parametrize(
    "tsv", [None, "Gene\tSource Diplotype\tPhenotype\tActivity Score\tOutside Call\n"]
)
def test_the_fail_path_is_unchanged_and_writes_no_warning(final_stage, tsv):
    """Nothing was rescued, so there is no rescued report to qualify. The job
    still fails naming the gate, exactly as before."""
    service, report_calls = final_stage(report_payload=BROKEN_REPORT, tsv=tsv)

    assert not report_calls
    assert JobStatus.FAILED in service.statuses(), service.statuses()
    assert service.appended_warnings == [], service.appended_warnings


def test_a_failed_warning_write_does_not_sink_the_rescued_report(
    final_stage, monkeypatch
):
    """Losing the banner is bad; losing the report the reader needs is worse."""

    def _boom(*a, **k):
        raise RuntimeError("job metadata write failed")

    monkeypatch.setattr(_FakeJobService, "append_workflow_warning", _boom)
    service, report_calls = final_stage(report_payload=BROKEN_REPORT, tsv=PHARMCAT_TSV)

    assert len(report_calls) == 1, "a warning-write failure killed the rescued report"
    assert JobStatus.COMPLETED in service.statuses(), service.statuses()


# ---------------------------------------------------------------------------
# The copy itself
# ---------------------------------------------------------------------------


def test_the_alert_escapes_the_gate_reason():
    """The templates render each warning with ``|safe``; the gate's summary is
    built from payload-derived key names, so it is not trusted markup."""
    from app.reports.generator import pharmcat_tsv_rescue_alert

    alert = pharmcat_tsv_rescue_alert("<script>alert(1)</script> & co")

    assert "<script>" not in alert
    assert "&lt;script&gt;" in alert
    assert "&amp; co" in alert


def test_the_alert_survives_an_empty_reason():
    """A rejection with no printable summary must still produce a coherent
    banner rather than a dangling sentence."""
    from app.reports.generator import pharmcat_tsv_rescue_alert

    for reason in ("", "   ", None):
        alert = pharmcat_tsv_rescue_alert(reason)
        assert "None" not in alert, alert
        assert "TSV" in alert
        assert alert.startswith("<p>")


# ---------------------------------------------------------------------------
# The persistence the ordering tape stands in for: a real row, a real commit
# ---------------------------------------------------------------------------


def test_append_workflow_warning_is_visible_to_a_freshly_opened_session(
    session_factory,
):
    """End to end on real sessions: the worker's long-lived session writes the
    warning, and the shape of read ``generate_report`` performs picks it up."""
    import uuid as _uuid

    from app.api.db import Job
    from app.api.models import JobCreate
    from app.services.job_service import JobService

    worker = session_factory()
    fresh = session_factory()
    try:
        service = JobService(worker)
        job = service.create_job(
            JobCreate(workflow_type="genomic_analysis", name="tsv-rescue-probe")
        )
        job_id = job.id
        # The upload path's warnings are already there; the rescue appends.
        row = worker.query(Job).filter(Job.id == job_id).first()
        row.job_metadata = {
            "patient_id": PATIENT_ID,
            "workflow": {"needs_report": True, "warnings": ["<p>upload alert</p>"]},
        }
        worker.commit()

        assert service.append_workflow_warning(job_id, "<p>rescued</p>") is True

        # generator.py:1880's read, on a session opened afterwards.
        job_uuid = _uuid.UUID(str(job_id))
        read_row = fresh.query(Job).filter(Job.id == job_uuid).first()
        warnings = (read_row.job_metadata or {})["workflow"]["warnings"]
        assert warnings == ["<p>upload alert</p>", "<p>rescued</p>"], warnings

        # And the rest of the workflow config survived the read-modify-write.
        assert read_row.job_metadata["workflow"]["needs_report"] is True
        assert read_row.job_metadata["patient_id"] == PATIENT_ID
    finally:
        for session in (worker, fresh):
            session.rollback()
            session.close()


def test_append_workflow_warning_does_not_duplicate_on_a_re_run(session_factory):
    """Final-stage progression can be driven twice for the same job; the reader
    should not get the same banner twice."""
    from app.api.db import Job
    from app.api.models import JobCreate
    from app.services.job_service import JobService

    worker = session_factory()
    try:
        service = JobService(worker)
        job = service.create_job(
            JobCreate(workflow_type="genomic_analysis", name="tsv-rescue-dupe")
        )
        service.append_workflow_warning(job.id, "<p>rescued</p>")
        service.append_workflow_warning(job.id, "<p>rescued</p>")

        row = worker.query(Job).filter(Job.id == job.id).populate_existing().first()
        assert row.job_metadata["workflow"]["warnings"] == ["<p>rescued</p>"]
    finally:
        worker.rollback()
        worker.close()


@pytest.fixture
def real_final_stage(monkeypatch, tmp_path, session_factory):
    """Drive the whole final stage against a *real* JobService and a real row.

    The fake-service harness above cannot see what this fixture exists for: its
    ``update_job`` only appends to a list, so the final
    ``update_job(JobUpdate(metadata=...))`` -- which replaces ``job_metadata``
    wholesale (job_service.py:371-372, no merge) -- never actually overwrites
    anything. Only a real row can show whether a warning written mid-function is
    still there at the end.
    """
    import app.api.db as api_db
    import app.reports.generator as generator
    from app.api.models import JobCreate
    from app.services.job_service import JobService

    monkeypatch.setattr(ur, "schedule_coroutine", lambda *a, **k: None)
    monkeypatch.setattr(ur, "render_with_graphviz", lambda *a, **k: None)
    monkeypatch.setattr(ur, "render_kroki_mermaid_svg", lambda *a, **k: None)
    monkeypatch.setattr(ur, "render_workflow", lambda *a, **k: None)
    monkeypatch.setattr(ur, "render_simple_png_from_workflow", lambda *a, **k: None)
    monkeypatch.setattr(
        generator, "generate_report", lambda **kwargs: {"pdf_path": "/x.pdf"}
    )
    # Real service, but no event loop here to await the broadcast coroutine on.
    monkeypatch.setattr(
        JobService, "_broadcast_job_update", lambda self, *a, **k: None, raising=False
    )

    # The worker opens its own sessions; hand it real ones on the test database.
    monkeypatch.setattr(api_db, "SessionLocal", session_factory)
    monkeypatch.setattr(ur, "SessionLocal", session_factory)

    opened = []

    def _open():
        session = session_factory()
        opened.append(session)
        return session

    setup = _open()
    created = JobService(setup).create_job(
        JobCreate(workflow_type="genomic_analysis", name="tsv-rescue-persistence")
    )
    job_id = str(created.id)

    outdir = tmp_path / PATIENT_ID / job_id
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / f"{job_id}_pgx_pharmcat.json").write_text(
        json.dumps(BROKEN_REPORT), encoding="utf-8"
    )
    (outdir / f"{job_id}_pgx_pharmcat.tsv").write_text(PHARMCAT_TSV, encoding="utf-8")

    def _stamp(metadata):
        from app.api.db import Job

        row = setup.query(Job).filter(Job.id == created.id).populate_existing().first()
        row.job_metadata = metadata
        setup.commit()

    _stamp(
        {
            "patient_id": PATIENT_ID,
            "data_id": "data-1",
            "workflow": {"needs_report": True, "warnings": ["<p>upload alert</p>"]},
        }
    )

    def _drive():
        ur._handle_final_stages_progression_sync(job_id, str(outdir))

    def _read_row():
        """Read the row as a brand-new session would -- the report's own view."""
        from app.api.db import Job

        verifier = _open()
        return (
            verifier.query(Job)
            .filter(Job.id == created.id)
            .populate_existing()
            .first()
            .job_metadata
        )

    yield _drive, _read_row

    for session in opened:
        session.rollback()
        session.close()


def test_the_rescue_warning_survives_the_final_metadata_update(real_final_stage):
    """The warning has to still be on the row when the function returns.

    It was not. ``metadata = job.job_metadata or {}`` is captured near the top of
    ``_handle_final_stages_progression_sync``; ``append_workflow_warning``
    assigns a *new* dict to ``job.job_metadata``, so that local still points at
    the pre-warning object; and the closing ``updated_metadata =
    metadata.copy()`` + ``update_job(JobUpdate(metadata=...))`` then replaced the
    row with the stale snapshot. The banner reached the report that was rendered
    in-process and was erased from the record, so any *regenerated* report came
    out TSV-rescued with nothing saying so -- the silent degradation 265 exists
    to prevent.
    """
    drive, read_row = real_final_stage
    drive()

    metadata = read_row()
    warnings = metadata["workflow"]["warnings"]

    assert "<p>upload alert</p>" in warnings, warnings
    rescue = [w for w in warnings if "TSV" in w]
    assert len(rescue) == 1, f"the rescue warning did not survive the run: {warnings}"
    # And the final update genuinely happened -- otherwise this passes for the
    # wrong reason.
    assert metadata.get("reports"), metadata


def test_a_second_drive_does_not_duplicate_the_surviving_warning(real_final_stage):
    """Dedup is only real if the row it reads still holds the first warning."""
    drive, read_row = real_final_stage
    drive()
    drive()

    warnings = read_row()["workflow"]["warnings"]
    assert len([w for w in warnings if "TSV" in w]) == 1, warnings
    assert warnings.count("<p>upload alert</p>") == 1, warnings


def test_append_workflow_warning_reports_a_missing_job(session_factory):
    import uuid as _uuid

    from app.services.job_service import JobService

    worker = session_factory()
    try:
        assert (
            JobService(worker).append_workflow_warning(_uuid.uuid4(), "<p>x</p>")
            is False
        )
    finally:
        worker.rollback()
        worker.close()


@pytest.mark.parametrize(
    "data,expected",
    [
        ({"genes": {"CPIC": {}}}, False),  # the TSV fallback's own empty seed
        ({"genes": {}}, False),
        ({"genes": []}, False),
        ({"genes": None}, False),
        ({}, False),
        (None, False),
        ({"genes": {"CYP2C19": {"geneSymbol": "CYP2C19"}}}, True),
        ({"genes": {"CPIC": {"CYP2C19": {"geneSymbol": "CYP2C19"}}}}, True),
    ],
)
def test_has_readable_gene_blocks(data, expected):
    """``{"genes": {"CPIC": {}}}`` is truthy and yields nothing to render -- which
    is why the decision cannot be a truthiness test on ``genes``."""
    assert ur._has_readable_gene_blocks(data) is expected
