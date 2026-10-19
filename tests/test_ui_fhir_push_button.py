"""The "Push to FHIR server" button on a finished job's results card.

The button sends the job's FHIR bundle to the stack's own FHIR server through
POST /fhir/push/workflow/{job_id}. These tests run the page's real inline
script in Node (tests/js/fhir_push_button.js, on the DOM shim the workflow
panel test uses) and feed it what the app really answers: /services-config
from its own handler, and the push endpoint's responses -- status, JSON and
headers -- from the FastAPI app with only the FHIR server itself faked.
"""

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.models import JobStatus
from app.services import fhir_export_service
from app.services.fhir import push as fhir_push

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = REPO_ROOT / "app" / "templates" / "index.html"
DRIVER = Path(__file__).resolve().parent / "js" / "fhir_push_button.js"

JOB_ID = "11111111-2222-3333-4444-555555555555"
RESULTS = {
    "job_id": JOB_ID,
    "patient_id": "patient-1",
    "pdf_report_url": f"/reports/patient-1/{JOB_ID}/{JOB_ID}_pgx_report.pdf",
}

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


def _services_config(monkeypatch, hapi_fhir: bool, fhir_export: bool) -> dict:
    """What GET /services-config answers with these two settings."""
    from app import main

    monkeypatch.setattr(main, "HAPI_FHIR_ENABLED", hapi_fhir)
    monkeypatch.setenv("FHIR_EXPORT_ENABLED", "true" if fhir_export else "false")
    return asyncio.run(main.services_config())


def _run(tmp_path: Path, services_config, push=None) -> dict:
    scenario = {"services_config": services_config, "results": RESULTS, "push": push}
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(scenario), encoding="utf-8")
    proc = subprocess.run(
        [shutil.which("node"), str(DRIVER), str(TEMPLATE), str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    out = json.loads(proc.stdout)
    assert out["ok"], out["errors"]
    return out


@pytest.fixture
def push_answer(client, monkeypatch, tmp_path):
    """How the real endpoint answers a push of JOB_ID, as the browser gets it.

    The job and its report directory are stand-ins; ``push_bundle`` -- the
    part that talks to the FHIR server -- is the given function.
    """
    from app.api.routes import fhir_export_router as router_module

    reports = tmp_path / "reports"
    monkeypatch.setattr(fhir_export_service, "REPORT_DIR", reports)
    job = SimpleNamespace(
        id=JOB_ID,
        status=JobStatus.COMPLETED,
        job_metadata={"patient_id": "patient-1"},
    )
    monkeypatch.setattr(
        router_module.JobService,
        "get_job",
        lambda self, job_id: job if job_id == JOB_ID else None,
    )

    def answer(push_bundle, with_bundle=True):
        if with_bundle:
            job_dir = reports / "patient-1" / JOB_ID
            job_dir.mkdir(parents=True, exist_ok=True)
            (job_dir / f"{JOB_ID}_pgx_fhir_report.json").write_text(
                json.dumps({"resourceType": "Bundle", "type": "collection"})
            )
        monkeypatch.setattr(router_module.fhir_push, "push_bundle", push_bundle)
        response = client.post(f"/fhir/push/workflow/{JOB_ID}")
        return {
            "status": response.status_code,
            "body": response.json(),
            "headers": dict(response.headers),
        }

    return answer


def _offered(out: dict) -> bool:
    section = out["section"]
    return 'id="fhirPushBtn"' in section["html"] and "d-none" not in section[
        "classes"
    ].split(" ")


@pytest.mark.parametrize(
    "hapi_fhir, fhir_export, offered",
    [
        (True, True, True),
        (True, False, False),
        (False, True, False),
        (False, False, False),
    ],
)
def test_the_button_is_offered_only_with_the_server_and_the_export_on(
    monkeypatch, tmp_path, hapi_fhir, fhir_export, offered
):
    config = _services_config(monkeypatch, hapi_fhir, fhir_export)
    out = _run(tmp_path, config)
    assert _offered(out) is offered
    if not offered:
        assert "fhirPushBtn" not in out["section"]["html"]
        assert "d-none" in out["section"]["classes"].split(" ")
    # Nothing is pushed until the button is pressed.
    assert all(f["url"] == "/services-config" for f in out["fetches"])


def test_no_button_when_the_configuration_cannot_be_read(tmp_path):
    out = _run(tmp_path, None)
    assert not _offered(out)


def test_a_click_pushes_the_job_and_shows_what_the_server_did(
    monkeypatch, tmp_path, push_answer
):
    def pushed(bundle, job_id):
        return {
            "server_url": "http://fhir-server:8080/fhir",
            "entries": 36,
            "resources": {
                "DiagnosticReport": {"created": 0, "existing": 1, "deleted": 0},
                "Patient": {"created": 0, "existing": 1, "deleted": 0},
                "Observation": {"created": 2, "existing": 28, "deleted": 0},
                "Task": {"created": 0, "existing": 1, "deleted": 2},
            },
            "diagnostic_report": "DiagnosticReport/1001/_history/2",
            "patient": "Patient/1000/_history/1",
        }

    answer = push_answer(pushed)
    assert answer["status"] == 200
    out = _run(tmp_path, _services_config(monkeypatch, True, True), answer)

    assert out["fetches"][-1] == {
        "url": f"/fhir/push/workflow/{JOB_ID}",
        "method": "POST",
    }
    click = out["click"]
    assert click["during"]["disabled"] is True
    assert "Pushing" in click["during"]["label"]
    assert click["after"]["disabled"] is False
    assert "Push to FHIR server" in click["after"]["label"]

    html = click["result_html"]
    assert "Pushed to http://fhir-server:8080/fhir" in html
    assert "<strong>DiagnosticReport</strong>: 1 updated" in html
    assert "<strong>Patient</strong>: 1 already there, reused" in html
    assert "<strong>Observation</strong>: 2 created; 28 updated" in html
    assert "<strong>Task</strong>: 1 updated; 2 deleted" in html
    assert "DiagnosticReport/1001/_history/2" in html
    assert "Patient/1000/_history/1" in html
    assert "text-success-emphasis" in html


@pytest.mark.parametrize(
    "reachable, message, heading",
    [
        (
            False,
            "The FHIR server at http://fhir-server:8080/fhir could not be reached "
            "(ConnectError('Connection refused')). Check that it is running and "
            "that FHIR_SERVER_URL points at it.",
            "Could not reach the FHIR server",
        ),
        (
            True,
            "The FHIR server at http://fhir-server:8080/fhir refused the bundle "
            "(HTTP 400): HAPI-0450: <b>bad</b> bundle",
            "The FHIR server did not accept the bundle",
        ),
    ],
)
def test_an_unreachable_or_refusing_server_is_named_with_its_reason(
    monkeypatch, tmp_path, push_answer, reachable, message, heading
):
    def fails(bundle, job_id):
        raise fhir_push.FHIRPushError(message, reachable=reachable)

    answer = push_answer(fails)
    assert answer["status"] == 502
    assert answer["headers"]["x-fhir-server-reachable"] == str(reachable).lower()
    out = _run(tmp_path, _services_config(monkeypatch, True, True), answer)

    html = out["click"]["result_html"]
    assert heading in html
    assert "text-danger-emphasis" in html
    # The server's own words, as text: markup in them is shown, not run.
    escaped = message.replace("<", "&lt;").replace(">", "&gt;").replace("'", "&#39;")
    assert escaped in html
    assert "<b>" not in html
    assert out["click"]["after"]["disabled"] is False


def test_a_job_without_a_bundle_says_so(monkeypatch, tmp_path, push_answer):
    def never(bundle, job_id):  # pragma: no cover - the bundle is missing
        raise AssertionError("pushed without a bundle")

    answer = push_answer(never, with_bundle=False)
    assert answer["status"] == 404
    out = _run(tmp_path, _services_config(monkeypatch, True, True), answer)
    html = out["click"]["result_html"]
    assert "Not pushed" in html
    assert f"Job {JOB_ID} has no FHIR bundle" in html


def test_an_app_that_does_not_answer_is_not_a_push(monkeypatch, tmp_path):
    out = _run(
        tmp_path,
        _services_config(monkeypatch, True, True),
        {"network_error": "Failed to fetch"},
    )
    html = out["click"]["result_html"]
    assert "Not pushed" in html
    assert "ZaroPGx did not answer: Failed to fetch" in html
    assert out["click"]["after"]["disabled"] is False
