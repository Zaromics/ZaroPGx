"""Pushing a job's FHIR bundle to the FHIR server (app.services.fhir.push).

No FHIR server here: httpx.MockTransport stands in for it.
tests/test_fhir_push_hapi.py runs the same push against a real HAPI server when
one is given.
"""

from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import HTTPException

from app.services import fhir_export_service as fhir_module
from app.services.fhir import push
from app.services.fhir_export_service import FHIRExportService

DATA = {
    "pharmcat_version": "3.4.0",
    "total_genes": 2,
    "actionable_findings": [],
    "genes": [
        {"gene": "CYP2C19", "diplotype": "*1/*2", "phenotype": "Intermediate"},
        {"gene": "CYP2D6", "diplotype": "*1/*1", "phenotype": "Normal Metabolizer"},
    ],
    "drugRecommendations": [
        {
            "drug": "clopidogrel",
            "genes": ["CYP2C19"],
            "recommendations": [
                {"recommendation": "Use an alternative.", "classification": "Strong"},
                {"recommendation": "Consider prasugrel.", "classification": ""},
            ],
        }
    ],
}


def _bundle(report="job-1", patient="patient-1", data=DATA):
    result = FHIRExportService(MagicMock()).export_pgx_report(
        run_id=report,
        patient_info={"id": patient},
        pharmcat_data=data,
        output_format="json",
    )
    assert result["success"], result
    return json.loads(result["content"])


# ---------------------------------------------------------------------------
# Stable resource ids: one report names its resources the same way every time
# ---------------------------------------------------------------------------


def _ids(bundle):
    return [e["fullUrl"] for e in bundle["entry"]]


def test_a_report_is_written_with_the_same_ids_every_time():
    first, second = _bundle(), _bundle()
    assert _ids(first) == _ids(second)
    for entry in first["entry"]:
        assert entry["fullUrl"] == f"urn:uuid:{entry['resource']['id']}"


def test_another_report_gets_other_ids_but_the_same_patient():
    one, other = _bundle(report="job-1"), _bundle(report="job-2")
    shared = set(_ids(one)) & set(_ids(other))
    patient = [
        e["fullUrl"] for e in one["entry"] if e["resource"]["resourceType"] == "Patient"
    ]
    assert shared == set(patient)


def test_a_gene_listed_twice_still_gets_two_resources():
    data = copy.deepcopy(DATA)
    data["genes"].append(dict(data["genes"][0]))
    ids = _ids(_bundle(data=data))
    assert len(ids) == len(set(ids))


def test_every_resource_carries_the_identifier_a_push_matches_on():
    for entry in _bundle()["entry"]:
        resource = entry["resource"]
        (identifier,) = resource["identifier"]
        if resource["resourceType"] == "Patient":
            assert identifier == {
                "system": "urn:zaropgx:patient-id",
                "value": "patient-1",
            }
        elif resource["resourceType"] == "DiagnosticReport":
            assert identifier == {"system": "urn:zaropgx:report-id", "value": "job-1"}
        else:
            assert identifier == {
                "system": "urn:ietf:rfc:3986",
                "value": entry["fullUrl"],
            }


# ---------------------------------------------------------------------------
# The transaction
# ---------------------------------------------------------------------------


def test_transaction_conditionally_creates_the_patient_and_updates_the_rest():
    bundle = _bundle()
    before = copy.deepcopy(bundle)
    transaction = push.to_transaction(bundle)

    assert bundle == before, "the saved bundle must not be changed"
    assert transaction["type"] == "transaction"
    assert len(transaction["entry"]) == len(bundle["entry"])
    for sent, entry in zip(transaction["entry"], bundle["entry"]):
        resource = sent["resource"]
        assert sent["fullUrl"] == entry["fullUrl"]
        assert "id" not in resource
        rtype = resource["resourceType"]
        if rtype == "Patient":
            assert sent["request"] == {
                "method": "POST",
                "url": "Patient",
                "ifNoneExist": "identifier=urn:zaropgx:patient-id|patient-1",
            }
        elif rtype == "DiagnosticReport":
            assert sent["request"] == {
                "method": "PUT",
                "url": "DiagnosticReport?identifier=urn:zaropgx:report-id|job-1",
            }
        else:
            assert sent["request"] == {
                "method": "PUT",
                "url": f"{rtype}?identifier=urn:ietf:rfc:3986|{entry['fullUrl']}",
            }


def test_a_bundle_written_before_identifiers_matches_on_its_full_urls():
    bundle = _bundle()
    for entry in bundle["entry"]:
        if entry["resource"]["resourceType"] not in ("Patient", "DiagnosticReport"):
            del entry["resource"]["identifier"]
    transaction = push.to_transaction(bundle)
    task = next(
        e for e in transaction["entry"] if e["resource"]["resourceType"] == "Task"
    )
    assert task["resource"]["identifier"] == [
        {"system": "urn:ietf:rfc:3986", "value": task["fullUrl"]}
    ]
    assert (
        task["request"]["url"] == f"Task?identifier=urn:ietf:rfc:3986|{task['fullUrl']}"
    )


def test_identifier_values_are_escaped_in_the_url():
    bundle = _bundle(patient="a b&c")
    patient = push.to_transaction(bundle)["entry"][1]
    assert (
        patient["request"]["ifNoneExist"]
        == "identifier=urn:zaropgx:patient-id|a%20b%26c"
    )


# ---------------------------------------------------------------------------
# Talking to the server
# ---------------------------------------------------------------------------


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _respond_all(status):
    def handler(request):
        sent = json.loads(request.content)
        handler.request = request
        handler.sent = sent
        entries = []
        for n, entry in enumerate(sent["entry"], start=1):
            rtype = entry["resource"]["resourceType"]
            entries.append(
                {"response": {"status": status, "location": f"{rtype}/{n}/_history/1"}}
            )
        return httpx.Response(
            200,
            json={
                "resourceType": "Bundle",
                "type": "transaction-response",
                "entry": entries,
            },
        )

    return handler


def test_push_posts_one_transaction_and_counts_what_was_created():
    handler = _respond_all("201 Created")
    summary = push.push_bundle(
        _bundle(), server_url="http://hapi.test/fhir/", client=_client(handler)
    )
    assert str(handler.request.url) == "http://hapi.test/fhir"
    assert handler.request.headers["content-type"] == "application/fhir+json"
    assert handler.sent["type"] == "transaction"
    assert summary["server_url"] == "http://hapi.test/fhir"
    assert summary["entries"] == 8
    assert summary["resources"] == {
        "DiagnosticReport": {"created": 1, "existing": 0},
        "Patient": {"created": 1, "existing": 0},
        "Observation": {"created": 3, "existing": 0},
        "Task": {"created": 2, "existing": 0},
        "Procedure": {"created": 1, "existing": 0},
    }
    assert summary["diagnostic_report"] == "DiagnosticReport/1/_history/1"
    assert summary["patient"] == "Patient/2/_history/1"


def test_a_second_push_reports_the_resources_as_existing():
    summary = push.push_bundle(
        _bundle(),
        server_url="http://hapi.test/fhir",
        client=_client(_respond_all("200 OK")),
    )
    assert all(c["created"] == 0 for c in summary["resources"].values())
    assert sum(c["existing"] for c in summary["resources"].values()) == 8


def test_a_server_that_is_down_is_a_clear_error():
    def handler(request):
        raise httpx.ConnectError("Connection refused", request=request)

    with pytest.raises(push.FHIRPushError) as caught:
        push.push_bundle(
            _bundle(), server_url="http://hapi.test/fhir", client=_client(handler)
        )
    assert caught.value.reachable is False
    assert "http://hapi.test/fhir could not be reached" in str(caught.value)
    assert "FHIR_SERVER_URL" in str(caught.value)


def test_a_server_that_refuses_says_why():
    def handler(request):
        return httpx.Response(
            400,
            json={
                "resourceType": "OperationOutcome",
                "issue": [
                    {"severity": "error", "diagnostics": "HAPI-0450: bad bundle"}
                ],
            },
        )

    with pytest.raises(push.FHIRPushError) as caught:
        push.push_bundle(
            _bundle(), server_url="http://hapi.test/fhir", client=_client(handler)
        )
    assert caught.value.reachable is True
    assert "HTTP 400" in str(caught.value) and "HAPI-0450: bad bundle" in str(
        caught.value
    )


def test_the_server_url_defaults_to_the_bundled_hapi(monkeypatch):
    monkeypatch.delenv("FHIR_SERVER_URL", raising=False)
    assert push.fhir_server_url() == "http://fhir-server:8080/fhir"
    monkeypatch.setenv("FHIR_SERVER_URL", "http://elsewhere:9000/fhir/ ")
    assert push.fhir_server_url() == "http://elsewhere:9000/fhir"


# ---------------------------------------------------------------------------
# Finding the job's bundle
# ---------------------------------------------------------------------------


def test_the_bundle_is_found_in_the_jobs_report_directory(tmp_path):
    job_dir = tmp_path / "patient-1" / "job-1"
    job_dir.mkdir(parents=True)
    (job_dir / "job-1_pgx_fhir_report.json").write_text("{}")
    assert (
        push.job_bundle_path(tmp_path, "job-1", "patient-1")
        == (job_dir / "job-1_pgx_fhir_report.json").resolve()
    )
    assert push.job_bundle_path(tmp_path, "job-2", "patient-1") is None


def test_a_patient_id_cannot_walk_out_of_the_reports_directory(tmp_path):
    outside = tmp_path / "secret" / "job-1"
    outside.mkdir(parents=True)
    (outside / "job-1_pgx_fhir_report.json").write_text("{}")
    reports = tmp_path / "reports"
    reports.mkdir()
    assert push.job_bundle_path(reports, "job-1", "../secret") is None


# ---------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------

JOB_ID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def endpoint(tmp_path, monkeypatch):
    from app.api.routes import fhir_export_router as router_module

    monkeypatch.setattr(fhir_module, "REPORT_DIR", tmp_path)
    monkeypatch.setenv("FHIR_EXPORT_ENABLED", "true")
    job = SimpleNamespace(id=JOB_ID, job_metadata={"patient_id": "patient-1"})
    monkeypatch.setattr(
        router_module.JobService,
        "get_job",
        lambda self, job_id: job if job_id == JOB_ID else None,
    )
    db = MagicMock()

    def call(job_id=JOB_ID):
        return asyncio.run(
            router_module.push_workflow_to_fhir_server(
                workflow_id=job_id, db=db, current_user="tester"
            )
        )

    call.db = db
    call.module = router_module
    return call


def _write_bundle(tmp_path):
    job_dir = tmp_path / "patient-1" / JOB_ID
    job_dir.mkdir(parents=True)
    path = job_dir / f"{JOB_ID}_pgx_fhir_report.json"
    path.write_text(json.dumps(_bundle(report=JOB_ID)))
    return path


def test_endpoint_pushes_the_jobs_bundle(endpoint, tmp_path, monkeypatch):
    _write_bundle(tmp_path)
    pushed = {}

    def fake_push(bundle, server_url=None, client=None):
        pushed["bundle"] = bundle
        return {
            "resources": {"Patient": {"created": 1, "existing": 0}},
            "diagnostic_report": "DiagnosticReport/1/_history/1",
            "patient": "Patient/2/_history/1",
            "server_url": "http://fhir-server:8080/fhir",
            "entries": 8,
        }

    monkeypatch.setattr(endpoint.module.fhir_push, "push_bundle", fake_push)
    response = endpoint()
    assert response.success is True
    assert response.job_id == JOB_ID
    assert response.diagnostic_report == "DiagnosticReport/1/_history/1"
    assert pushed["bundle"]["entry"][0]["resource"]["identifier"][0]["value"] == JOB_ID


def test_endpoint_unknown_job_is_404(endpoint):
    with pytest.raises(HTTPException) as caught:
        endpoint("not-a-job")
    assert caught.value.status_code == 404


def test_endpoint_job_without_bundle_is_404(endpoint):
    with pytest.raises(HTTPException) as caught:
        endpoint()
    assert caught.value.status_code == 404
    assert "FHIR_EXPORT_ENABLED" in caught.value.detail


def test_endpoint_server_down_is_502_and_leaves_the_job_alone(
    endpoint, tmp_path, monkeypatch
):
    bundle_path = _write_bundle(tmp_path)
    saved = bundle_path.read_text()

    def down(bundle, server_url=None, client=None):
        raise push.FHIRPushError("The FHIR server at X could not be reached", False)

    monkeypatch.setattr(endpoint.module.fhir_push, "push_bundle", down)
    with pytest.raises(HTTPException) as caught:
        endpoint()
    assert caught.value.status_code == 502
    assert "could not be reached" in caught.value.detail
    assert not endpoint.db.commit.called
    assert bundle_path.read_text() == saved


def test_endpoint_is_off_with_the_export(endpoint, monkeypatch):
    monkeypatch.setenv("FHIR_EXPORT_ENABLED", "false")
    with pytest.raises(HTTPException) as caught:
        endpoint()
    assert caught.value.status_code == 503
