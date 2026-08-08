"""Regression: turning reports off did not turn reports off.

``needs_report=False`` drops the ``report_generation`` step template
(``workflow_registry.py``), and that was the whole of its effect.
``_handle_final_stages_progression_sync`` read ``workflow_config`` and never looked at
the flag, so it called ``app.reports.generator.generate_report`` unconditionally the
moment Nextflow finished — the app rebuilt exactly the artifacts the user asked it not
to build.

Nothing downstream saved it either: the ``skip_report`` the upload puts in the Nextflow
payload is dropped on the floor, because ``NextflowRunRequest``
(``docker/nextflow/runner.py``) has no such field and pydantic ignores extras, and
``pipelines/pgx/main.nf`` never reads one. So the gate under test is the only thing in
the system that honours the toggle.

Nothing here raises to signal a violation: the function wraps its body in a blanket
``except Exception`` that marks the job FAILED, so an ``AssertionError`` from a stub
would be swallowed and reported as an ordinary failure. Violations are recorded on the
fake and asserted afterwards, and every case asserts the job was not failed.
"""

import pytest

import app.api.routes.upload_router as ur
from app.api.models import JobStatus

JOB_ID = "job-skip-report"
PATIENT_ID = "patient-skip-report"


class _FakeSession:
    """The worker opens its own session and closes it in a ``finally``."""

    def close(self):
        pass


class _FakeJob:
    def __init__(self, metadata):
        self.id = JOB_ID
        self.status = "running"
        self.job_metadata = metadata


class _FakeJobService:
    """Records the writes the final-stage worker makes."""

    def __init__(self, db=None, metadata=None):
        self.db = db
        self.job = _FakeJob(metadata or {})
        self.updates = []
        self.step_updates = []
        self.logs = []

    def get_job(self, job_id):
        return self.job

    def update_job(self, job_id, update):
        self.updates.append(update)
        return self.job

    def update_job_step(self, job_id, step_name, update):
        self.step_updates.append((step_name, update))
        return None  # a step that was never minted: the real service returns None

    def log_job_event(self, job_id, log_data):
        self.logs.append(log_data)

    def link_pharmcat_run(self, job_id, run_id):  # pragma: no cover - not reached here
        pass

    def _broadcast_job_update(self, job_id, payload):
        return ("broadcast", job_id, payload)

    def statuses(self):
        return [getattr(u, "status", None) for u in self.updates]


@pytest.fixture
def final_stage(monkeypatch, tmp_path):
    """Drive ``_handle_final_stages_progression_sync`` with everything external stubbed."""
    import app.api.db as api_db
    import app.reports.generator as generator

    monkeypatch.setattr(api_db, "SessionLocal", _FakeSession)
    monkeypatch.setattr(api_db, "get_db", lambda: iter([_FakeSession()]))
    monkeypatch.setattr(ur, "schedule_coroutine", lambda *a, **k: None)

    # Diagram rendering reaches Kroki/Graphviz over the network.
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

    def _run(workflow_config):
        service = _FakeJobService(
            metadata={
                "patient_id": PATIENT_ID,
                "data_id": "data-1",
                "workflow": workflow_config,
            }
        )
        monkeypatch.setattr(ur, "JobService", lambda db: service)
        outdir = tmp_path / PATIENT_ID / JOB_ID
        ur._handle_final_stages_progression_sync(JOB_ID, str(outdir))
        assert (
            JobStatus.FAILED not in service.statuses()
        ), f"the worker failed the job instead of finishing it: {service.logs}"
        return service, report_calls

    return _run


def test_report_generation_is_skipped_when_the_user_turned_it_off(final_stage):
    """needs_report=False must reach generate_report, not just Nextflow."""
    service, report_calls = final_stage({"needs_report": False})

    assert not report_calls, "reports were generated for a skip_report job"
    assert (
        JobStatus.COMPLETED in service.statuses()
    ), "skipping the report must still complete the job"


def test_report_generation_still_runs_when_the_flag_is_set(final_stage):
    service, report_calls = final_stage({"needs_report": True})

    assert len(report_calls) == 1, "an opted-in job must still get its reports"
    assert JobStatus.COMPLETED in service.statuses()


def test_report_generation_defaults_to_on_when_the_flag_is_absent(final_stage):
    """Older jobs have no needs_report in their metadata; they must keep reporting."""
    _, report_calls = final_stage({})

    assert len(report_calls) == 1
