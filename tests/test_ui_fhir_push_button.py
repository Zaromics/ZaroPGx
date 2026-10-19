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


def _run(tmp_path: Path, services_config, push=None, replaced_by=None) -> dict:
    scenario = {
        "services_config": services_config,
        "results": RESULTS,
        "push": push,
        "replaced_by": replaced_by,
    }
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
    assert section is not None, "no #fhirPushSection in the page"
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


def test_the_push_is_offered_inside_the_jobs_results_card(monkeypatch, tmp_path):
    out = _run(tmp_path, _services_config(monkeypatch, True, True))
    assert _offered(out)
    # The page finds the section only where showResults put it: in the card,
    # not in a stand-in the test harness made up.
    assert out["section"]["inside"][0] == "analysisResults"


def test_a_card_replaced_while_the_configuration_loads_offers_only_its_own_push(
    monkeypatch, tmp_path
):
    # A second job finishes while the first job's card is still asking
    # /services-config whether to offer the push: its card replaces the first.
    other_job = "66666666-7777-8888-9999-000000000000"
    out = _run(
        tmp_path,
        _services_config(monkeypatch, True, True),
        push={"network_error": "Failed to fetch"},
        replaced_by={"job_id": other_job, "patient_id": "patient-2"},
    )
    # The first card's section is out of the page and was left alone.
    assert out["first_section"] == {"html": "", "connected": False}
    assert _offered(out)
    assert out["section"]["inside"][0] == "analysisResults"
    # The one button there pushes the job whose card it is, once.
    pushes = [f for f in out["fetches"] if f["url"].startswith("/fhir/push/")]
    assert pushes == [{"url": f"/fhir/push/workflow/{other_job}", "method": "POST"}]


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
    "error, outcome, heading, tone",
    [
        (
            dict(
                message="The FHIR server at http://fhir-server:8080/fhir could not "
                "be reached (ConnectError('Connection refused')). Check that it is "
                "running and that FHIR_SERVER_URL points at it.",
                reachable=False,
            ),
            "unreachable",
            "Could not reach the FHIR server",
            "danger",
        ),
        (
            dict(
                message="The FHIR server at http://fhir-server:8080/fhir refused the "
                "bundle (HTTP 400): HAPI-0450: <b>bad</b> bundle",
                reachable=True,
            ),
            "refused",
            "The FHIR server did not accept the bundle",
            "danger",
        ),
        (
            # The bundle went out and the answer did not come back: the server
            # may have stored it, and the page must not say it was not pushed.
            dict(
                message="The FHIR server at http://fhir-server:8080/fhir was sent "
                "the bundle but gave no answer (ReadTimeout('timed out')). It may "
                "have stored it, or may still be storing it.",
                reachable=True,
                outcome_unknown=True,
            ),
            "unknown",
            "No answer from the FHIR server: it may have stored the bundle",
            "warning",
        ),
    ],
    ids=["unreachable", "refused", "unknown"],
)
def test_a_failed_push_says_what_it_means_for_the_server(
    monkeypatch, tmp_path, push_answer, error, outcome, heading, tone
):
    message = error["message"]

    def fails(bundle, job_id):
        raise fhir_push.FHIRPushError(**error)

    answer = push_answer(fails)
    assert answer["status"] == 502
    assert answer["headers"]["x-fhir-push-outcome"] == outcome
    out = _run(tmp_path, _services_config(monkeypatch, True, True), answer)

    html = out["click"]["result_html"]
    assert heading in html
    assert f"text-{tone}-emphasis" in html
    assert "Not pushed" not in html
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


def test_no_answer_from_the_app_does_not_claim_nothing_was_pushed(
    monkeypatch, tmp_path
):
    out = _run(
        tmp_path,
        _services_config(monkeypatch, True, True),
        {"network_error": "Failed to fetch"},
    )
    html = out["click"]["result_html"]
    # The request may have reached ZaroPGx and the push gone ahead: the page
    # cannot know, so it does not say "Not pushed".
    assert "No answer from ZaroPGx" in html
    assert "Failed to fetch" in html
    assert "Not pushed" not in html
    assert out["click"]["after"]["disabled"] is False
