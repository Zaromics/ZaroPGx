"""Pushing a job's FHIR bundle to the FHIR server (app.services.fhir.push).

No FHIR server here: httpx.MockTransport stands in for it.
tests/test_fhir_push_hapi.py runs the same push against a real HAPI server when
one is given.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import HTTPException

from app.api.models import JobStatus
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
        if request.method == "GET":
            # The listing of the job's resources: none on this server.
            return httpx.Response(
                200, json={"resourceType": "Bundle", "type": "searchset"}
            )
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
        "DiagnosticReport": {"created": 1, "existing": 0, "deleted": 0},
        "Patient": {"created": 1, "existing": 0, "deleted": 0},
        "Observation": {"created": 3, "existing": 0, "deleted": 0},
        "Task": {"created": 2, "existing": 0, "deleted": 0},
        "Procedure": {"created": 1, "existing": 0, "deleted": 0},
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


def _transaction_fails(error):
    """The job's listing answers; the transaction's request raises ``error``."""

    def handler(request):
        if request.method == "GET":
            return httpx.Response(
                200, json={"resourceType": "Bundle", "type": "searchset"}
            )
        raise error(request)

    return handler


@pytest.mark.parametrize(
    "error",
    [
        lambda request: httpx.ReadTimeout("timed out", request=request),
        lambda request: httpx.RemoteProtocolError(
            "Server disconnected without sending a response.", request=request
        ),
        lambda request: httpx.ReadError("Connection reset by peer", request=request),
    ],
    ids=["read-timeout", "disconnected", "reset"],
)
def test_a_transaction_sent_without_an_answer_may_have_been_stored(error):
    # The server got the transaction and the answer never came: HAPI commits it
    # all the same (tests/test_fhir_push_hapi.py), so "nothing changed" would
    # be wrong.
    with pytest.raises(push.FHIRPushError) as caught:
        push.push_bundle(
            _bundle(),
            "job-1",
            server_url="http://hapi.test/fhir",
            client=_client(_transaction_fails(error)),
        )
    assert caught.value.reachable is True
    assert caught.value.outcome_unknown is True
    message = str(caught.value)
    assert "http://hapi.test/fhir was sent the bundle but gave no answer" in message
    assert "It may have stored it, or may still be storing it." in message
    assert "could not be reached" not in message


@pytest.mark.parametrize(
    "error",
    [
        lambda request: httpx.ConnectError("Connection refused", request=request),
        lambda request: httpx.ConnectTimeout("timed out", request=request),
    ],
    ids=["refused", "connect-timeout"],
)
def test_a_transaction_that_could_not_connect_was_not_sent(error):
    # The listing worked, then the server went away: the transaction never
    # left, so nothing was stored.
    with pytest.raises(push.FHIRPushError) as caught:
        push.push_bundle(
            _bundle(),
            "job-1",
            server_url="http://hapi.test/fhir",
            client=_client(_transaction_fails(error)),
        )
    assert caught.value.reachable is False
    assert caught.value.outcome_unknown is False
    assert "http://hapi.test/fhir could not be reached" in str(caught.value)


def test_a_listing_that_times_out_pushes_nothing():
    def handler(request):
        if request.method == "GET":
            raise httpx.ReadTimeout("timed out", request=request)
        raise AssertionError("nothing may be posted")

    with pytest.raises(push.FHIRPushError) as caught:
        push.push_bundle(
            _bundle(),
            "job-1",
            server_url="http://hapi.test/fhir",
            client=_client(handler),
        )
    assert caught.value.reachable is False
    assert caught.value.outcome_unknown is False
    assert str(caught.value) == (
        "The FHIR server at http://hapi.test/fhir did not answer in time "
        "(ReadTimeout('timed out')); nothing was pushed."
    )


# ---------------------------------------------------------------------------
# A re-push removes what the job's bundle no longer has
# ---------------------------------------------------------------------------

JOB_TAG = {"system": "urn:zaropgx:job-id", "code": "job-1"}


def test_every_resource_but_the_patient_is_tagged_with_the_job():
    bundle = _bundle()
    bundle["entry"][2]["resource"]["meta"]["tag"] = [
        {"system": "http://example.org/other", "code": "kept"},
        {"system": "urn:zaropgx:job-id", "code": "an-older-job"},
    ]
    transaction = push.to_transaction(bundle, "job-1")
    for entry in transaction["entry"]:
        tags = entry["resource"].get("meta", {}).get("tag", [])
        if entry["resource"]["resourceType"] == "Patient":
            assert tags == []
        else:
            assert tags[-1] == JOB_TAG
            assert [t for t in tags if t["system"] == "urn:zaropgx:job-id"] == [JOB_TAG]
    assert transaction["entry"][2]["resource"]["meta"]["tag"][0]["code"] == "kept"
    untagged = push.to_transaction(_bundle())
    assert all("tag" not in e["resource"].get("meta", {}) for e in untagged["entry"])


def _server(holding, answer_status="200 OK"):
    """A FHIR server holding some of job-1's resources: a two-page listing per
    type, and a transaction-response to whatever is posted."""

    def handler(request):
        handler.requests.append(request)
        if request.method == "GET":
            handler.listing_headers = request.headers
            rtype = request.url.path.rsplit("/", 1)[-1]
            if rtype == "fhir":  # a next-page link
                rtype = request.url.params["type"]
                page = 2
            else:
                assert request.url.params["_tag"] == "urn:zaropgx:job-id|job-1"
                page = 1
            mine = [r for r in holding if r[0] == rtype]
            half = mine[: len(mine) // 2] if page == 1 else mine[len(mine) // 2 :]
            body = {
                "resourceType": "Bundle",
                "type": "searchset",
                "entry": [
                    {
                        "resource": {
                            "resourceType": t,
                            "id": i,
                            "identifier": [{"system": sys, "value": val}],
                        }
                    }
                    for t, i, sys, val in half
                ],
            }
            if page == 1:
                body["link"] = [
                    {
                        "relation": "next",
                        "url": f"http://hapi.test/fhir?_getpages=x&type={rtype}",
                    }
                ]
            return httpx.Response(200, json=body)
        sent = json.loads(request.content)
        handler.sent = sent
        return httpx.Response(
            200,
            json={
                "resourceType": "Bundle",
                "type": "transaction-response",
                "entry": [
                    {"response": {"status": answer_status}} for _ in sent["entry"]
                ],
            },
        )

    handler.requests = []
    return handler


def test_a_smaller_bundle_deletes_what_it_no_longer_has():
    bundle = _bundle()
    by_type = {}
    for entry in bundle["entry"]:
        by_type.setdefault(entry["resource"]["resourceType"], []).append(entry)
    kept_task = by_type["Task"][0]["resource"]["identifier"][0]
    holding = [
        # Still in the bundle: updated, not deleted.
        ("Task", "11", kept_task["system"], kept_task["value"]),
        # Gone from the bundle: a recommendation and an observation.
        ("Task", "12", "urn:ietf:rfc:3986", "urn:uuid:gone-task"),
        ("Observation", "13", "urn:ietf:rfc:3986", "urn:uuid:gone-obs"),
        ("Observation", "14", "urn:ietf:rfc:3986", "urn:uuid:gone-obs-2"),
        # The same identifier as a kept Task, but another type: deleted.
        ("Procedure", "15", kept_task["system"], kept_task["value"]),
    ]
    handler = _server(holding)
    summary = push.push_bundle(
        bundle, "job-1", server_url="http://hapi.test/fhir", client=_client(handler)
    )

    deletes = [
        e["request"]["url"]
        for e in handler.sent["entry"]
        if e["request"]["method"] == "DELETE"
    ]
    assert sorted(deletes) == [
        "Observation/13",
        "Observation/14",
        "Procedure/15",
        "Task/12",
    ]
    assert len(handler.sent["entry"]) == len(bundle["entry"]) + 4
    assert summary["resources"]["Task"]["deleted"] == 1
    assert summary["resources"]["Observation"]["deleted"] == 2
    assert summary["resources"]["Procedure"]["deleted"] == 1
    assert summary["resources"]["Patient"]["deleted"] == 0
    listed = [r.url.path.rsplit("/", 1)[-1] for r in handler.requests[:-1]]
    assert set(listed) >= set(push.JOB_RESOURCE_TYPES)
    # HAPI would otherwise answer from a search it cached a minute ago.
    assert handler.listing_headers["cache-control"] == "no-cache"


def test_without_a_job_nothing_is_listed_or_deleted():
    handler = _server([("Task", "12", "urn:ietf:rfc:3986", "urn:uuid:gone")])
    push.push_bundle(
        _bundle(), server_url="http://hapi.test/fhir", client=_client(handler)
    )
    assert [r.method for r in handler.requests] == ["POST"]
    assert all(e["request"]["method"] != "DELETE" for e in handler.sent["entry"])


def test_a_server_that_will_not_list_the_job_stops_the_push():
    def handler(request):
        if request.method == "GET":
            return httpx.Response(400, json={"resourceType": "OperationOutcome"})
        raise AssertionError("nothing may be posted")

    with pytest.raises(push.FHIRPushError) as caught:
        push.push_bundle(
            _bundle(),
            "job-1",
            server_url="http://hapi.test/fhir",
            client=_client(handler),
        )
    assert "would not list the job's DiagnosticReport resources" in str(caught.value)


# ---------------------------------------------------------------------------
# Only a transaction-response for every entry is a push
# ---------------------------------------------------------------------------


def _answer(body, status=200):
    def handler(request):
        return httpx.Response(status, json=body(json.loads(request.content)))

    return handler


def _ok_entries(sent):
    return [{"response": {"status": "201 Created"}} for _ in sent["entry"]]


@pytest.mark.parametrize(
    "body, reason",
    [
        (
            lambda sent: {
                "resourceType": "OperationOutcome",
                "issue": [{"severity": "information", "diagnostics": "ok?"}],
            },
            "OperationOutcome, not a transaction-response",
        ),
        (
            lambda sent: {
                "resourceType": "Bundle",
                "type": "batch-response",
                "entry": _ok_entries(sent),
            },
            "'batch-response' Bundle",
        ),
        (
            lambda sent: {
                "resourceType": "Bundle",
                "type": "transaction-response",
                "entry": _ok_entries(sent)[:-1],
            },
            "7 entries for the 8 sent",
        ),
        (
            lambda sent: {
                "resourceType": "Bundle",
                "type": "transaction-response",
                "entry": _ok_entries(sent)[:-1]
                + [{"response": {"status": "400 Bad Request"}}],
            },
            "1 of the 8 entries were not stored (entry 7: 400 Bad Request)",
        ),
        (lambda sent: [], "list"),
    ],
)
def test_anything_but_a_full_transaction_response_is_a_failure(body, reason):
    with pytest.raises(push.FHIRPushError) as caught:
        push.push_bundle(
            _bundle(), server_url="http://hapi.test/fhir", client=_client(_answer(body))
        )
    assert caught.value.reachable is True
    assert reason in str(caught.value)


# ---------------------------------------------------------------------------
# Credentials in FHIR_SERVER_URL are sent, never shown
# ---------------------------------------------------------------------------

SECRET_URL = "http://ann:s3cr%40t@hapi.test:8080/fhir"


def test_split_server_url_keeps_credentials_apart():
    assert push.split_server_url(SECRET_URL) == (
        "http://hapi.test:8080/fhir",
        ("ann", "s3cr@t"),
    )
    assert push.split_server_url("http://hapi.test/fhir") == (
        "http://hapi.test/fhir",
        None,
    )


def test_credentials_go_as_basic_auth_and_not_into_the_answer():
    handler = _respond_all("201 Created")
    summary = push.push_bundle(
        _bundle(), server_url=SECRET_URL, client=_client(handler)
    )
    assert str(handler.request.url) == "http://hapi.test:8080/fhir"
    assert (
        handler.request.headers["authorization"]
        == "Basic " + base64.b64encode(b"ann:s3cr@t").decode()
    )
    assert summary["server_url"] == "http://hapi.test:8080/fhir"
    assert "s3cr" not in json.dumps(summary)


@pytest.mark.parametrize(
    "handler",
    [
        lambda request: (_ for _ in ()).throw(
            httpx.ConnectError(f"refused {SECRET_URL}", request=request)
        ),
        lambda request: httpx.Response(
            400,
            json={
                "resourceType": "OperationOutcome",
                "issue": [{"severity": "error", "diagnostics": f"bad at {SECRET_URL}"}],
            },
        ),
        lambda request: httpx.Response(200, json={"resourceType": "Bundle"}),
        _transaction_fails(
            lambda request: httpx.ReadTimeout(
                f"timed out on {SECRET_URL}", request=request
            )
        ),
    ],
)
def test_errors_do_not_show_the_credentials(handler):
    with pytest.raises(push.FHIRPushError) as caught:
        push.push_bundle(_bundle(), server_url=SECRET_URL, client=_client(handler))
    assert "hapi.test:8080/fhir" in str(caught.value)
    assert "s3cr" not in str(caught.value) and "ann:" not in str(caught.value)


def test_status_and_logs_do_not_show_the_credentials(
    endpoint, tmp_path, monkeypatch, caplog
):
    monkeypatch.setenv("FHIR_SERVER_URL", SECRET_URL)
    status = asyncio.run(endpoint.module.fhir_export_status(current_user="tester"))
    assert status["server_url"] == "http://hapi.test:8080/fhir"

    _write_bundle(tmp_path)
    real_push = push.push_bundle
    handlers = iter(
        [
            _respond_all("201 Created"),
            lambda request: httpx.Response(503, text=f"down: {SECRET_URL}"),
        ]
    )
    monkeypatch.setattr(
        endpoint.module.fhir_push,
        "push_bundle",
        lambda bundle, job_id: real_push(
            bundle, job_id, client=_client(next(handlers))
        ),
    )
    caplog.set_level("DEBUG")
    response = endpoint()
    assert response.server_url == "http://hapi.test:8080/fhir"
    with pytest.raises(HTTPException) as caught:
        endpoint()
    assert caught.value.status_code == 502
    assert "s3cr" not in caught.value.detail
    assert "hapi.test:8080" in caplog.text
    assert "s3cr" not in caplog.text and "ann:" not in caplog.text


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
    db = MagicMock()

    def call(job_id=JOB_ID):
        return asyncio.run(
            router_module.push_workflow_to_fhir_server(
                workflow_id=job_id, db=db, current_user="tester"
            )
        )

    call.db = db
    call.job = job
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

    def fake_push(bundle, job_id=None, server_url=None, client=None):
        pushed["bundle"] = bundle
        pushed["job_id"] = job_id
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
    assert pushed["job_id"] == JOB_ID


def test_endpoint_unknown_job_is_404(endpoint):
    with pytest.raises(HTTPException) as caught:
        endpoint("not-a-job")
    assert caught.value.status_code == 404


@pytest.mark.parametrize(
    "status",
    [JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.RUNNING, JobStatus.PENDING],
)
def test_endpoint_pushes_only_a_completed_job(endpoint, tmp_path, monkeypatch, status):
    # The bundle an earlier run of the job left in its directory.
    _write_bundle(tmp_path)
    endpoint.job.status = status

    def never(bundle, job_id=None, server_url=None, client=None):
        raise AssertionError("pushed the bundle of a job that is not completed")

    monkeypatch.setattr(endpoint.module.fhir_push, "push_bundle", never)
    with pytest.raises(HTTPException) as caught:
        endpoint()
    assert caught.value.status_code == 409
    assert caught.value.detail == (
        f"Job {JOB_ID} is {status.value}, not completed. Only a completed job's "
        "FHIR bundle is pushed."
    )


def test_endpoint_job_without_bundle_is_404(endpoint):
    with pytest.raises(HTTPException) as caught:
        endpoint()
    assert caught.value.status_code == 404
    # The export is on: the endpoint answers 503 otherwise. The setting is not
    # why there is no bundle.
    assert "FHIR_EXPORT_ENABLED" not in caught.value.detail
    assert caught.value.detail == (
        f"Job {JOB_ID} has no FHIR bundle. The bundle is written with the job's "
        "custom report, and this job's report did not write one: FHIR export was "
        "off when it ran, or the export failed."
    )


def test_endpoint_job_with_its_report_turned_off_says_so(endpoint):
    endpoint.job.job_metadata = {
        "patient_id": "patient-1",
        "workflow": {"needs_report": False},
    }
    with pytest.raises(HTTPException) as caught:
        endpoint()
    assert caught.value.status_code == 404
    assert caught.value.detail == (
        f"Job {JOB_ID} has no FHIR bundle. The bundle is written with the custom "
        "report, which was turned off for this job."
    )


def test_endpoint_server_down_is_502_and_leaves_the_bundle_alone(
    endpoint, tmp_path, monkeypatch
):
    bundle_path = _write_bundle(tmp_path)
    saved = bundle_path.read_text()

    def down(bundle, job_id=None, server_url=None, client=None):
        raise push.FHIRPushError("The FHIR server at X could not be reached", False)

    monkeypatch.setattr(endpoint.module.fhir_push, "push_bundle", down)
    with pytest.raises(HTTPException) as caught:
        endpoint()
    assert caught.value.status_code == 502
    assert "could not be reached" in caught.value.detail
    # The web page tells "down" from "refused" by this, not by the wording.
    assert caught.value.headers == {"X-FHIR-Push-Outcome": "unreachable"}
    assert bundle_path.read_text() == saved


@pytest.mark.parametrize(
    "error, outcome",
    [
        (
            push.FHIRPushError("The FHIR server at X refused the bundle", True),
            "refused",
        ),
        (
            push.FHIRPushError(
                "The FHIR server at X was sent the bundle but gave no answer",
                reachable=True,
                outcome_unknown=True,
            ),
            "unknown",
        ),
    ],
)
def test_endpoint_failed_push_is_502_marked_with_its_outcome(
    endpoint, tmp_path, monkeypatch, error, outcome
):
    _write_bundle(tmp_path)

    def fails(bundle, job_id=None, server_url=None, client=None):
        raise error

    monkeypatch.setattr(endpoint.module.fhir_push, "push_bundle", fails)
    with pytest.raises(HTTPException) as caught:
        endpoint()
    assert caught.value.status_code == 502
    # "unknown": the server got the bundle and did not answer, so it may have
    # stored it; the page must not say it was refused.
    assert caught.value.headers == {"X-FHIR-Push-Outcome": outcome}


def test_endpoint_is_off_with_the_export(endpoint, monkeypatch):
    monkeypatch.setenv("FHIR_EXPORT_ENABLED", "false")
    with pytest.raises(HTTPException) as caught:
        endpoint()
    assert caught.value.status_code == 503
