"""Send a job's FHIR bundle to a FHIR server as one transaction.

The report generator writes each job's bundle to its report directory as a
``collection``: a standalone document. A FHIR server's base endpoint takes
``transaction`` bundles, so :func:`to_transaction` turns the collection into one,
and :func:`push_bundle` posts it to ``FHIR_SERVER_URL`` (the bundled HAPI server
by default).

Pushing a job twice must not leave two copies of its report, so every entry is
conditional on the resource's business identifier:

- the Patient is a conditional create (``ifNoneExist``): an existing Patient with
  the same ZaroPGx patient id is reused as it is, so later reports of one person
  land on one Patient and a push does not overwrite what the server holds;
- the DiagnosticReport and its observations, recommendations and study are
  conditional updates (``PUT Observation?identifier=...``): created the first
  time, replaced in place after. The export gives each of them an identifier
  derived from what it is (app.services.fhir_export_service), so a regenerated
  bundle matches the same resources. A bundle written before those identifiers
  existed gets one from its entry's ``fullUrl``, which is fixed in that file.

Nothing here runs on its own: a push happens when someone asks for it (POST
/fhir/push/workflow/{job_id}). A server that is down or refuses the bundle is an
error for that request only; the job and its files are untouched.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx

from app.api.utils.path_jail import resolve_under
from app.services.fhir import terminology

# HAPI takes a few seconds for a 500-entry transaction the first time (every
# conditional update searches first); a slow server is not a down one.
PUSH_TIMEOUT = httpx.Timeout(300.0, connect=10.0)

FHIR_JSON = "application/fhir+json"


class FHIRPushError(Exception):
    """The push did not happen. ``reachable`` is False when the server could not
    be reached at all (down, wrong URL), True when it answered with an error."""

    def __init__(self, message: str, reachable: bool):
        super().__init__(message)
        self.reachable = reachable


def fhir_server_url() -> str:
    """FHIR_SERVER_URL (by default the bundled HAPI server, as compose.yml names
    it), without a trailing slash."""
    return (
        os.getenv("FHIR_SERVER_URL", "http://fhir-server:8080/fhir").strip().rstrip("/")
    )


def job_bundle_path(
    report_dir: Path, job_id: str, patient_id: Optional[str]
) -> Optional[Path]:
    """The JSON bundle the report generator wrote for the job, if there is one.

    It is ``<patient>/<job>/<job>_pgx_fhir_report.json`` (save_fhir_export); a job
    with no patient keeps its reports in ``<job>/``.
    """
    candidates = []
    if patient_id:
        candidates.append((str(patient_id), job_id, f"{job_id}_pgx_fhir_report.json"))
    candidates.append((job_id, f"{job_id}_pgx_fhir_report.json"))
    candidates.append((job_id, "pgx_fhir_report.json"))
    for parts in candidates:
        try:
            path = resolve_under(report_dir, *parts)
        except PermissionError:
            continue
        if path.is_file():
            return path
    return None


def _identifier_token(identifier: Dict[str, Any]) -> str:
    system = quote(identifier.get("system", ""), safe=":/")
    value = quote(identifier["value"], safe=":/")
    return f"{system}|{value}"


def to_transaction(bundle: Dict[str, Any]) -> Dict[str, Any]:
    """A transaction bundle that creates or updates the collection's resources."""
    if bundle.get("resourceType") != "Bundle":
        raise ValueError("not a FHIR Bundle")
    entries: List[Dict[str, Any]] = []
    for entry in bundle.get("entry", []):
        resource = copy.deepcopy(entry["resource"])
        resource_type = resource["resourceType"]
        full_url = entry.get("fullUrl") or f"urn:uuid:{resource.get('id')}"
        # The server assigns ids; references between entries go by fullUrl.
        resource.pop("id", None)
        identifiers = resource.get("identifier") or []
        if not identifiers:
            identifiers = [{"system": terminology.URI_IDENTIFIER, "value": full_url}]
            resource["identifier"] = identifiers
        token = _identifier_token(identifiers[0])
        if resource_type == "Patient":
            request = {
                "method": "POST",
                "url": "Patient",
                "ifNoneExist": f"identifier={token}",
            }
        else:
            request = {"method": "PUT", "url": f"{resource_type}?identifier={token}"}
        entries.append({"fullUrl": full_url, "resource": resource, "request": request})
    transaction: Dict[str, Any] = {"resourceType": "Bundle", "type": "transaction"}
    if bundle.get("timestamp"):
        transaction["timestamp"] = bundle["timestamp"]
    transaction["entry"] = entries
    return transaction


def _outcome_text(response: httpx.Response) -> str:
    """The server's own words: an OperationOutcome's diagnostics, or the body."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:500]
    if isinstance(body, dict) and body.get("resourceType") == "OperationOutcome":
        texts = [
            issue.get("diagnostics") or issue.get("details", {}).get("text", "")
            for issue in body.get("issue", [])
        ]
        return "; ".join(t for t in texts if t)[:1000]
    return json.dumps(body)[:500]


def summarize_response(
    transaction: Dict[str, Any], response_bundle: Dict[str, Any]
) -> Dict[str, Any]:
    """Per resource type: how many entries the server created and how many it
    found already there (updated, or reused for the Patient). The response's
    entries are in the transaction's order."""
    resources: Dict[str, Dict[str, int]] = {}
    report_location = None
    patient_location = None
    for sent, got in zip(transaction["entry"], response_bundle.get("entry", [])):
        resource_type = sent["resource"]["resourceType"]
        response = got.get("response", {})
        status = str(response.get("status", ""))
        counts = resources.setdefault(resource_type, {"created": 0, "existing": 0})
        counts["created" if status.startswith("201") else "existing"] += 1
        location = response.get("location")
        if resource_type == "DiagnosticReport":
            report_location = location
        elif resource_type == "Patient":
            patient_location = location
    return {
        "resources": resources,
        "diagnostic_report": report_location,
        "patient": patient_location,
    }


def push_bundle(
    bundle: Dict[str, Any],
    server_url: Optional[str] = None,
    client: Optional[httpx.Client] = None,
) -> Dict[str, Any]:
    """POST the bundle to the FHIR server as a transaction; raise FHIRPushError
    if the server cannot be reached or rejects it."""
    base = (server_url or fhir_server_url()).rstrip("/")
    if not base:
        raise FHIRPushError("FHIR_SERVER_URL is empty.", reachable=False)
    transaction = to_transaction(bundle)
    own_client = client is None
    http = client or httpx.Client(timeout=PUSH_TIMEOUT)
    try:
        response = http.post(
            base,
            content=json.dumps(transaction),
            headers={"Content-Type": FHIR_JSON, "Accept": FHIR_JSON},
        )
    except httpx.TimeoutException as exc:
        raise FHIRPushError(
            f"The FHIR server at {base} did not answer in time ({exc!r}).",
            reachable=False,
        ) from exc
    except httpx.HTTPError as exc:
        raise FHIRPushError(
            f"The FHIR server at {base} could not be reached ({exc!r}). "
            "Check that it is running and that FHIR_SERVER_URL points at it.",
            reachable=False,
        ) from exc
    finally:
        if own_client:
            http.close()
    if response.status_code >= 400:
        raise FHIRPushError(
            f"The FHIR server at {base} refused the bundle "
            f"(HTTP {response.status_code}): {_outcome_text(response)}",
            reachable=True,
        )
    try:
        response_bundle = response.json()
    except ValueError as exc:
        raise FHIRPushError(
            f"The FHIR server at {base} answered HTTP {response.status_code} "
            "with something that is not FHIR JSON.",
            reachable=True,
        ) from exc
    summary = summarize_response(transaction, response_bundle)
    summary["server_url"] = base
    summary["entries"] = len(transaction["entry"])
    return summary
