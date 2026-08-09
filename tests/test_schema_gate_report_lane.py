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
    def __init__(self, db=None, metadata=None):
        self.db = db
        self.job = _FakeJob(metadata or {})
        self.updates = []
        self.step_updates = []
        self.logs = []
        self.linked = []

    def get_job(self, job_id):
        return self.job

    def update_job(self, job_id, update):
        self.updates.append(update)
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

    monkeypatch.setattr(api_db, "SessionLocal", _FakeSession)
    monkeypatch.setattr(ur, "schedule_coroutine", lambda *a, **k: None)

    monkeypatch.setattr(ur, "render_with_graphviz", lambda *a, **k: None)
    monkeypatch.setattr(ur, "render_kroki_mermaid_svg", lambda *a, **k: None)
    monkeypatch.setattr(ur, "render_workflow", lambda *a, **k: None)
    monkeypatch.setattr(ur, "render_simple_png_from_workflow", lambda *a, **k: None)

    report_calls = []
    monkeypatch.setattr(
        generator,
        "generate_report",
        lambda **kwargs: report_calls.append(kwargs) or {"pdf_path": "/x.pdf"},
    )

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
            }
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
