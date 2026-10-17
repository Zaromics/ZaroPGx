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
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, unquote, urlsplit, urlunsplit

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
    it), without a trailing slash. It may hold credentials (user:password@):
    show it only through :func:`split_server_url`."""
    return (
        os.getenv("FHIR_SERVER_URL", "http://fhir-server:8080/fhir").strip().rstrip("/")
    )


def split_server_url(url: str) -> Tuple[str, Optional[Tuple[str, str]]]:
    """The URL without its user:password@ part, and those credentials.

    Everything shown or logged -- /fhir/status, the push's answer, its errors --
    uses the first; requests send the second as HTTP Basic auth.
    """
    parts = urlsplit(url)
    if parts.username is None and parts.password is None:
        return url, None
    host = parts.hostname or ""
    if ":" in host:  # an IPv6 literal
        host = f"[{host}]"
    netloc = f"{host}:{parts.port}" if parts.port else host
    plain = urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    return plain, (unquote(parts.username or ""), unquote(parts.password or ""))


_USERINFO = re.compile(r"(//)[^/@\s]+@")


def _scrub(text: str) -> str:
    """Any user:password@ in a URL inside the text, removed."""
    return _USERINFO.sub(r"\1", text)


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


def check_response(
    transaction: Dict[str, Any], body: Any, base: str, status_code: int
) -> None:
    """Raise FHIRPushError unless the server answered what a transaction gets:
    a transaction-response Bundle with one 2xx entry per entry sent."""
    sent = len(transaction["entry"])
    where = f"The FHIR server at {base} answered HTTP {status_code}"
    if not isinstance(body, dict) or body.get("resourceType") != "Bundle":
        what = body.get("resourceType") if isinstance(body, dict) else type(body)
        raise FHIRPushError(
            f"{where} with {what}, not a transaction-response Bundle; "
            "the push did not happen.",
            reachable=True,
        )
    if body.get("type") != "transaction-response":
        raise FHIRPushError(
            f"{where} with a {body.get('type')!r} Bundle, not a "
            "transaction-response; the push did not happen.",
            reachable=True,
        )
    entries = body.get("entry") or []
    if len(entries) != sent:
        raise FHIRPushError(
            f"{where} with {len(entries)} entries for the {sent} sent.",
            reachable=True,
        )
    failed = [
        (n, str((entry.get("response") or {}).get("status", "")))
        for n, entry in enumerate(entries)
        if not str((entry.get("response") or {}).get("status", "")).startswith("2")
    ]
    if failed:
        n, status = failed[0]
        raise FHIRPushError(
            f"{where} but {len(failed)} of the {sent} entries were not stored "
            f"(entry {n}: {status or 'no status'}).",
            reachable=True,
        )


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
    if the server cannot be reached, rejects it, or answers anything but a
    transaction-response for every entry."""
    base, auth = split_server_url((server_url or fhir_server_url()).rstrip("/"))
    if not base:
        raise FHIRPushError("FHIR_SERVER_URL is empty.", reachable=False)
    transaction = to_transaction(bundle)
    own_client = client is None
    http = client or httpx.Client(timeout=PUSH_TIMEOUT)
    try:
        response = _send(
            http,
            "POST",
            base,
            base,
            auth,
            content=json.dumps(transaction),
            headers={"Content-Type": FHIR_JSON, "Accept": FHIR_JSON},
        )
    finally:
        if own_client:
            http.close()
    if response.status_code >= 400:
        raise FHIRPushError(
            f"The FHIR server at {base} refused the bundle "
            f"(HTTP {response.status_code}): {_scrub(_outcome_text(response))}",
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
    check_response(transaction, response_bundle, base, response.status_code)
    summary = summarize_response(transaction, response_bundle)
    summary["server_url"] = base
    summary["entries"] = len(transaction["entry"])
    return summary


def _send(http: httpx.Client, method: str, url: str, base: str, auth, **kwargs):
    """One request to the server; a transport failure becomes FHIRPushError
    naming the server without its credentials."""
    if auth:
        kwargs["auth"] = auth
    try:
        return http.request(method, url, **kwargs)
    except httpx.TimeoutException as exc:
        raise FHIRPushError(
            f"The FHIR server at {base} did not answer in time ({_scrub(repr(exc))}).",
            reachable=False,
        ) from exc
    except httpx.HTTPError as exc:
        raise FHIRPushError(
            f"The FHIR server at {base} could not be reached ({_scrub(repr(exc))}). "
            "Check that it is running and that FHIR_SERVER_URL points at it.",
            reachable=False,
        ) from exc
