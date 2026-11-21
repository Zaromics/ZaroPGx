"""FHIR_AUTO_PUSH: a completed job's bundle goes to the FHIR server without the click,
when -- and only when -- an operator turns it on.

Owner, 2026-11-18: "keep automatic push available but set to off by default; env value
has to be set to true or some such." So unset, blank or anything but a truthy
spelling is off, and the push runs after the job is marked completed, so a server that
is down cannot fail a finished analysis.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from app.api.models import JobCreate, JobStatus, JobUpdate
from app.api.routes import upload_router
from app.services import fhir_export_service
from app.services.fhir import push as fhir_push
from app.services.job_service import JobService

REPO = Path(__file__).resolve().parents[1]
BUNDLE = {"resourceType": "Bundle", "type": "collection", "entry": []}


# --- the setting --------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "false", "0", "no", "off", "maybe"])
def test_off_unless_set_true(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("FHIR_AUTO_PUSH", raising=False)
    else:
        monkeypatch.setenv("FHIR_AUTO_PUSH", value)
    assert fhir_push.auto_push_enabled() is False


@pytest.mark.parametrize("value", ["true", "TRUE", " true ", "1", "yes", "on"])
def test_on_when_set_true(monkeypatch, value):
    monkeypatch.setenv("FHIR_AUTO_PUSH", value)
    assert fhir_push.auto_push_enabled() is True


def test_the_example_env_ships_it_off():
    lines = (REPO / ".env.example").read_text(encoding="utf-8").splitlines()
    assert "FHIR_AUTO_PUSH=false" in lines


# --- what a completed job does ------------------------------------------------------


@pytest.fixture
def completed_job(db_session, tmp_path, monkeypatch):
    """A completed job whose report wrote its bundle where save_fhir_export puts it."""
    monkeypatch.setattr(fhir_export_service, "REPORT_DIR", tmp_path)
    service = JobService(db_session)
    job = service.create_job(
        JobCreate(workflow_type="genomic_analysis", name="fhir-auto-push")
    )
    service.update_job(job.id, JobUpdate(status=JobStatus.COMPLETED))
    job_id = str(job.id)
    folder = tmp_path / "patient-1" / job_id
    folder.mkdir(parents=True)
    (folder / f"{job_id}_pgx_fhir_report.json").write_text(
        json.dumps(BUNDLE), encoding="utf-8"
    )
    return service, job_id


@pytest.fixture
def server(monkeypatch):
    """push_bundle, recorded: the transaction it would send is push.py's own, tested
    in test_fhir_push_hapi.py against a real HAPI."""
    sent = []

    def push_bundle(bundle, job_id=None, server_url=None, client=None):
        sent.append((bundle, job_id))
        return {"server_url": "http://fhir-server:8080/fhir", "resources": {"x": 1}}

    monkeypatch.setattr(fhir_push, "push_bundle", push_bundle)
    return sent


def _push_logs(service, job_id):
    return [
        (str(getattr(log.log_level, "value", log.log_level)), log.message)
        for log in service.get_job_logs(job_id)
        if log.step_name == "fhir_push"
    ]


def _run(service, job_id):
    upload_router._auto_push_to_fhir(service, job_id, "patient-1")


def test_off_nothing_is_sent_and_nothing_logged(completed_job, server, monkeypatch):
    monkeypatch.delenv("FHIR_AUTO_PUSH", raising=False)
    service, job_id = completed_job
    _run(service, job_id)
    assert server == []
    assert _push_logs(service, job_id) == []


def test_on_the_jobs_own_bundle_is_sent_and_the_log_says_where(
    completed_job, server, monkeypatch
):
    monkeypatch.setenv("FHIR_AUTO_PUSH", "true")
    monkeypatch.delenv("FHIR_EXPORT_ENABLED", raising=False)
    monkeypatch.delenv("HAPI_FHIR_ENABLED", raising=False)
    service, job_id = completed_job
    _run(service, job_id)
    assert server == [(BUNDLE, job_id)]
    [(level, message)] = _push_logs(service, job_id)
    assert level == "info"
    assert "sent to http://fhir-server:8080/fhir" in message


@pytest.mark.parametrize("setting", ["FHIR_EXPORT_ENABLED", "HAPI_FHIR_ENABLED"])
def test_a_setting_that_stops_it_is_named(completed_job, server, monkeypatch, setting):
    monkeypatch.setenv("FHIR_AUTO_PUSH", "true")
    monkeypatch.setenv(setting, "false")
    service, job_id = completed_job
    _run(service, job_id)
    assert server == []
    [(level, message)] = _push_logs(service, job_id)
    assert level == "warn"
    assert f"{setting} is off" in message


def test_a_server_that_refuses_is_a_warning_and_the_job_stays_completed(
    completed_job, monkeypatch
):
    monkeypatch.setenv("FHIR_AUTO_PUSH", "true")

    def refuse(bundle, job_id=None, server_url=None, client=None):
        raise fhir_push.FHIRPushError(
            "The FHIR server could not be reached.", reachable=False
        )

    monkeypatch.setattr(fhir_push, "push_bundle", refuse)
    service, job_id = completed_job
    _run(service, job_id)
    [(level, message)] = _push_logs(service, job_id)
    assert level == "warn"
    assert "could not be reached" in message
    assert str(service.get_job(job_id).status.value) == "completed"


def test_a_job_with_no_bundle_says_so(completed_job, server, monkeypatch, tmp_path):
    monkeypatch.setenv("FHIR_AUTO_PUSH", "true")
    service, job_id = completed_job
    for path in tmp_path.rglob("*_pgx_fhir_report.json"):
        path.unlink()
    _run(service, job_id)
    assert server == []
    [(level, message)] = _push_logs(service, job_id)
    assert level == "warn" and "has no FHIR bundle" in message


def test_nothing_it_does_can_raise(completed_job, monkeypatch):
    """Even the job log failing: this runs inside the step that completes the job,
    whose except marks the job FAILED."""
    monkeypatch.setenv("FHIR_AUTO_PUSH", "true")
    service, job_id = completed_job

    def broken(*args, **kwargs):
        raise RuntimeError("database gone")

    monkeypatch.setattr(service, "log_job_event", broken)
    monkeypatch.setattr(fhir_push, "push_bundle", broken)
    _run(service, job_id)


# --- where it runs ------------------------------------------------------------------


def test_it_runs_once_after_the_job_is_marked_completed():
    """In the one function that completes a pipeline run, after the COMPLETED update
    and the completion broadcast, never before."""
    source = (REPO / "app" / "api" / "routes" / "upload_router.py").read_text("utf-8")
    tree = ast.parse(source)
    final = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "_handle_final_stages_progression_sync"
    )
    calls = [
        node.lineno
        for node in ast.walk(final)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "_auto_push_to_fhir"
    ]
    completed = [
        node.lineno
        for node in ast.walk(final)
        if isinstance(node, ast.Attribute)
        and node.attr == "COMPLETED"
        and getattr(node.value, "id", None) == "JobStatus"
    ]
    assert len(calls) == 1, calls
    assert completed and calls[0] > max(completed)
    others = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "_auto_push_to_fhir"
    ]
    assert others == calls
