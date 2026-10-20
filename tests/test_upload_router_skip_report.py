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

import json

import pytest

import app.api.routes.upload_router as ur
from app.api.models import JobStatus
from tests.mtdna_outputs import NA12878_RESULT, write_no_chrm_vcf_run

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


def test_reports_off_still_offers_pharmcat_and_mtdna_reports(
    final_stage, monkeypatch, tmp_path
):
    """The Report toggle turns off the custom report, not PharmCAT's own.

    The page says so ("When disabled, only PharmCAT's native report is provided"),
    but the skip branch linked nothing: the job finished offering no report at all,
    though PharmCAT's files sat in its directory (found by the browser smoke test,
    2026-10-18).
    """
    import app.reports.generator as generator

    for flag in ("html", "json", "tsv"):
        monkeypatch.setitem(
            generator.REPORT_CONFIG, f"show_pharmcat_{flag}_report", True
        )
    job_dir = tmp_path / PATIENT_ID / JOB_ID
    job_dir.mkdir(parents=True)
    for name in (
        f"{JOB_ID}_pgx_pharmcat.html",
        f"{JOB_ID}_pgx_pharmcat.json",
        f"{JOB_ID}_pgx_pharmcat.tsv",
    ):
        (job_dir / name).write_text("x")
    (job_dir / "mtdna_result.json").write_text(json.dumps(NA12878_RESULT))
    broadcasts = []
    monkeypatch.setattr(ur, "schedule_coroutine", broadcasts.append)

    service, report_calls = final_stage({"needs_report": False})

    assert not report_calls
    reports = [
        u.metadata["reports"]
        for u in service.updates
        if getattr(u, "metadata", None) and "reports" in u.metadata
    ][-1]
    prefix = f"/reports/{PATIENT_ID}/{JOB_ID}"
    assert reports["pharmcat_html_path"] == f"{prefix}/{JOB_ID}_pgx_pharmcat.html"
    assert reports["pharmcat_json_path"] == f"{prefix}/{JOB_ID}_pgx_pharmcat.json"
    assert reports["pharmcat_tsv_path"] == f"{prefix}/{JOB_ID}_pgx_pharmcat.tsv"
    assert reports["mtdna_haplogroups_path"] == f"{prefix}/mtdna_result.json"
    assert "pdf_path" not in reports and "mtdna_report_path" not in reports
    # The completion message the page renders its links from carries them too.
    final = [
        b[2] for b in broadcasts if isinstance(b, tuple) and "pdf_report_url" in b[2]
    ]
    assert (
        final and final[-1]["pharmcat_html_report_url"] == reports["pharmcat_html_path"]
    )


def test_a_file_without_chrm_data_is_offered_no_mtdna_link(
    final_stage, monkeypatch, tmp_path
):
    """PharmCAT's example VCF: its mtDNA files hold no result, and neither the job's
    stored reports nor the completion message the page draws its links from may
    offer them."""
    write_no_chrm_vcf_run(tmp_path / PATIENT_ID / JOB_ID)
    broadcasts = []
    monkeypatch.setattr(ur, "schedule_coroutine", broadcasts.append)

    service, _ = final_stage({"needs_report": False})

    reports = [
        u.metadata["reports"]
        for u in service.updates
        if getattr(u, "metadata", None) and "reports" in u.metadata
    ][-1]
    assert not [key for key in reports if key.startswith("mtdna_")], reports
    final = [
        b[2]
        for b in broadcasts
        if isinstance(b, tuple) and b[2].get("status") == "completed"
    ]
    assert final
    assert {
        key: final[-1][key]
        for key in ("mtdna_report_url", "mtdna_haplogroups_url", "mtdna_vcf_url")
    } == {
        "mtdna_report_url": None,
        "mtdna_haplogroups_url": None,
        "mtdna_vcf_url": None,
    }
