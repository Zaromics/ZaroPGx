"""Wave 2 Step 7: front-door auth gate.

Default mode is open (no-op). Password mode is enumerated from /openapi.json so
a newly added route fails CI instead of shipping unprotected.
"""

from __future__ import annotations

import os
import pytest
from fastapi.testclient import TestClient

from app.api.middleware import auth_gate
from app.api.middleware.auth_gate import (
    ALLOWLIST_EXACT,
    ALLOWLIST_PREFIXES,
    is_allowlisted,
    mint_session_token,
)
from app.main import app


def _openapi_paths() -> list[tuple[str, str]]:
    client = TestClient(app)
    spec = client.get("/openapi.json").json()
    rows: list[tuple[str, str]] = []
    for path, methods in spec.get("paths", {}).items():
        for method in methods:
            if method.lower() in {"get", "post", "put", "patch", "delete", "head"}:
                rows.append((method.upper(), path))
    return rows


def _concrete_path(path_template: str) -> str:
    """Fill OpenAPI path params with placeholders for probing."""
    out = path_template
    for token in (
        "{file_id}",
        "{job_id}",
        "{patient_id}",
        "{filename}",
        "{workflow_id}",
        "{step_id}",
        "{report_id}",
        "{resource_type}",
        "{id}",
    ):
        out = out.replace(token, "probe")
    # Any remaining {param}
    while "{" in out and "}" in out:
        start = out.index("{")
        end = out.index("}", start)
        out = out[:start] + "probe" + out[end + 1 :]
    return out


def test_default_mode_is_open(monkeypatch):
    monkeypatch.delenv("ZAROPGX_AUTH_MODE", raising=False)
    monkeypatch.setenv("ZAROPGX_DEV_MODE", "true")
    assert auth_gate.resolve_auth_mode() == "open"


def test_dev_mode_false_aliases_to_open_not_password(monkeypatch, caplog):
    monkeypatch.delenv("ZAROPGX_AUTH_MODE", raising=False)
    monkeypatch.setenv("ZAROPGX_DEV_MODE", "false")
    with caplog.at_level("WARNING", logger="app.auth_gate"):
        assert auth_gate.resolve_auth_mode() == "open"
    assert any("ZAROPGX_AUTH_MODE" in r.message for r in caplog.records)


@pytest.mark.parametrize(
    "mode,auth,expected",
    [
        ("open", None, 200),
        ("audit", None, 200),
        ("password", None, 401),
        ("password", "cookie", 200),
        ("password", "bearer", 200),
    ],
)
def test_mode_matrix_on_protected_path(monkeypatch, mode, auth, expected):
    monkeypatch.setenv("ZAROPGX_AUTH_MODE", mode)
    monkeypatch.setenv("ZAROPGX_AUTH_PASSWORD", "gate-secret")
    client = TestClient(app)
    headers = {}
    cookies = {}
    if auth == "cookie":
        cookies[auth_gate.COOKIE_NAME] = mint_session_token()
    elif auth == "bearer":
        headers["Authorization"] = f"Bearer {mint_session_token()}"
    # Probe a cheap HTML route — avoid /services-status (it fans out to backends).
    response = client.get("/", headers=headers, cookies=cookies, follow_redirects=False)
    if expected == 401:
        assert response.status_code in {401, 303}
    else:
        assert response.status_code not in {401, 303}


def test_password_mode_openapi_enumeration(monkeypatch):
    """Every OpenAPI path is allowlisted or returns 401/303 without credentials."""
    monkeypatch.setenv("ZAROPGX_AUTH_MODE", "password")
    monkeypatch.setenv("ZAROPGX_AUTH_PASSWORD", "gate-secret")
    client = TestClient(app)
    offenders: list[str] = []
    for method, path_template in _openapi_paths():
        path = _concrete_path(path_template)
        if is_allowlisted(path):
            continue
        # Skip websocket upgrade paths if any appear
        if "websocket" in path.lower():
            continue
        response = client.request(
            method,
            path,
            headers={"Accept": "application/json"},
            follow_redirects=False,
        )
        # Gate must challenge before the route runs. 422/404/405 here means the
        # gate let an unauthenticated request through.
        if response.status_code not in {401, 303}:
            offenders.append(
                f"{method} {path_template} -> {response.status_code} (concrete {path})"
            )
    assert not offenders, "unprotected routes in password mode:\n  " + "\n  ".join(
        offenders[:40]
    )


def test_allowlist_covers_workflows_and_health():
    assert is_allowlisted("/health")
    assert is_allowlisted("/api/v1/workflows")
    assert is_allowlisted("/api/v1/workflows/abc/steps")
    assert is_allowlisted("/static/favicon.png")
    assert not is_allowlisted("/upload/genomic-data")
    assert not is_allowlisted("/")
    # Sanity: declared constants are used
    assert "/health" in ALLOWLIST_EXACT
    assert any(p.startswith("/api/v1/workflows") for p in ALLOWLIST_PREFIXES)


def test_login_sets_cookie_and_unlocks(monkeypatch):
    monkeypatch.setenv("ZAROPGX_AUTH_MODE", "password")
    monkeypatch.setenv("ZAROPGX_AUTH_PASSWORD", "gate-secret")
    client = TestClient(app)
    denied = client.get("/", headers={"Accept": "application/json"}, follow_redirects=False)
    assert denied.status_code in {401, 303}
    login = client.post(
        "/login",
        data={"password": "gate-secret", "next": "/"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    assert auth_gate.COOKIE_NAME in login.cookies
    client.cookies.set(auth_gate.COOKIE_NAME, login.cookies[auth_gate.COOKIE_NAME])
    unlocked = client.get("/", follow_redirects=False)
    assert unlocked.status_code not in {401, 303}
