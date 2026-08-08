"""FHIR_EXPORT_ENABLED must be parsed in exactly one place.

The bug this pins: the flag used to be parsed twice, with different rules and at
different times.

- ``app/main.py`` parsed it with ``_env_flag`` (which ``.strip()``s) *after*
  ``load_dotenv()``, and mounted the ``/fhir/*`` router on the result.
- ``app/services/fhir_export_service.py`` parsed it with a bare
  ``os.getenv(...).lower()`` (no strip) at import time, *before*
  ``load_dotenv()`` ran, and ``fhir_export_router`` imported that value for its
  guard.

So ``FHIR_EXPORT_ENABLED='true '`` -- one trailing space, the single easiest
typo to make in a ``.env`` file -- mounted the router and then tripped its guard:
every ``/fhir/*`` endpoint answered ``503`` while plainly being mounted. Silent,
and the symptom points nowhere near the cause.

Both the mount and the guard now call one resolver,
``fhir_export_service.fhir_export_enabled()``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.services import fhir_export_service as fhir_service_module
from app.services.fhir_export_service import FHIRExportService, fhir_export_enabled

REPO_ROOT = Path(fhir_service_module.__file__).resolve().parents[2]

# Spellings an operator can plausibly put in a .env file, and what each means.
TRUTHY = [
    "true",
    "True",
    "TRUE",
    "1",
    "yes",
    "on",
    "true ",
    " true",
    "  TRUE  ",
    "\ttrue",
]
FALSY = ["false", "False", "0", "no", "off", "", " ", "nope"]


@pytest.mark.parametrize("raw", TRUTHY)
def test_truthy_spellings_enable_export(monkeypatch, raw):
    monkeypatch.setenv("FHIR_EXPORT_ENABLED", raw)
    assert fhir_export_enabled() is True, f"{raw!r} should enable FHIR export"
    assert FHIRExportService(MagicMock()).is_enabled() is True


@pytest.mark.parametrize("raw", FALSY)
def test_falsy_spellings_disable_export(monkeypatch, raw):
    monkeypatch.setenv("FHIR_EXPORT_ENABLED", raw)
    assert fhir_export_enabled() is False, f"{raw!r} should disable FHIR export"
    assert FHIRExportService(MagicMock()).is_enabled() is False


def test_unset_defaults_to_enabled(monkeypatch):
    monkeypatch.delenv("FHIR_EXPORT_ENABLED", raising=False)
    assert fhir_export_enabled() is True


def test_whitespace_padded_true_does_not_503_the_endpoints(client, monkeypatch):
    """The regression, at the HTTP layer: 'true ' must serve, not 503."""
    monkeypatch.setenv("FHIR_EXPORT_ENABLED", "true ")

    status = client.get("/fhir/status")
    assert status.status_code == 200
    assert status.json()["enabled"] is True

    # Guarded endpoints: any status but 503. (404 here just means "no such run".)
    for method, path in (
        ("get", "/fhir/export/run/no-such-run"),
        ("get", "/fhir/export/run/no-such-run/preview"),
        ("get", "/fhir/export/workflow/no-such-workflow"),
    ):
        response = getattr(client, method)(path)
        assert response.status_code != 503, (
            f"{method.upper()} {path} returned 503 for FHIR_EXPORT_ENABLED='true ' "
            "- the mount flag and the guard flag have diverged again"
        )


def test_flag_is_resolved_by_one_shared_function():
    """main.py must not keep its own parse; it must call the service's resolver."""
    import app.main as main_module

    assert main_module.fhir_export_enabled is fhir_export_enabled

    for module in (main_module, fhir_service_module):
        assert not hasattr(module, "FHIR_EXPORT_ENABLED"), (
            f"{module.__name__} still exposes a frozen FHIR_EXPORT_ENABLED constant; "
            "a second reader will drift from the resolver"
        )


def test_flag_name_appears_in_exactly_one_env_lookup():
    """Grep-level guard: only the resolver may name the variable as a lookup key.

    Matches the quoted token ``"FHIR_EXPORT_ENABLED"``, which is how an env
    lookup spells it. Prose mentions in docstrings and in the "Set
    FHIR_EXPORT_ENABLED=true to enable." messages are unquoted and so excluded.
    """
    lookups = [
        (name, line.strip())
        for name in (
            "app/main.py",
            "app/api/routes/fhir_export_router.py",
            "app/services/fhir_export_service.py",
        )
        for line in (REPO_ROOT / name).read_text(encoding="utf-8").splitlines()
        if '"FHIR_EXPORT_ENABLED"' in line
    ]
    assert len(lookups) == 1, f"expected exactly one env lookup, found: {lookups}"
    assert lookups[0][0] == "app/services/fhir_export_service.py"
    assert lookups[0][1] == 'return env_flag("FHIR_EXPORT_ENABLED", True)'


def test_flag_is_not_frozen_at_import_time(monkeypatch):
    """The import-order property.

    Nothing may capture the flag into a module-level constant: that is what made
    the value depend on whether the reader was imported before or after
    ``load_dotenv()``. If flipping os.environ after import flips every reader,
    no pre-dotenv snapshot survives anywhere.
    """
    from app.api.routes import fhir_export_router as router_module

    readers = (
        fhir_export_enabled,
        router_module.fhir_export_enabled,
        lambda: FHIRExportService(MagicMock()).is_enabled(),
    )

    monkeypatch.setenv("FHIR_EXPORT_ENABLED", "false")
    assert [reader() for reader in readers] == [False, False, False]

    monkeypatch.setenv("FHIR_EXPORT_ENABLED", "true ")
    assert [reader() for reader in readers] == [True, True, True]


COLD_BOOT_PROBE = textwrap.dedent("""
    import json, sys
    from fastapi.testclient import TestClient
    from app.api.db import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: iter([None])
    app.router.on_startup.clear()
    app.router.on_shutdown.clear()

    out = {}
    with TestClient(app) as client:
        out["mounted"] = client.get("/fhir/export/formats").status_code
        out["status"] = client.get("/fhir/status").status_code
        out["guarded"] = client.get("/fhir/export/run/abc").status_code
    sys.stdout.write("PROBE=" + json.dumps(out))
    """)


@pytest.mark.parametrize("raw", ["true", "true "])
def test_cold_boot_mounts_and_serves_for_padded_flag(tmp_path, raw):
    """Full cold boot in a fresh interpreter: mount decision and guard must agree.

    In-process tests cannot cover the mount, because app.main was already
    imported by the time they run. This one boots the whole app from scratch.
    """
    probe = tmp_path / "cold_boot_probe.py"
    probe.write_text(COLD_BOOT_PROBE, encoding="utf-8")

    env = dict(os.environ)
    env.update(
        {
            "FHIR_EXPORT_ENABLED": raw,
            "PYTHONPATH": str(REPO_ROOT),
            "ZAROPGX_DEV_MODE": "true",
            "SECRET_KEY": "pytest-secret-key-not-for-production",
            "DATABASE_URL": "postgresql+psycopg://pytest:pytest@localhost:5432/pytest",
            "DB_PASSWORD": "pytest-db-password",
        }
    )

    completed = subprocess.run(
        [sys.executable, str(probe)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr[-3000:]

    payload = next(
        line for line in completed.stdout.splitlines() if line.startswith("PROBE=")
    )
    result = __import__("json").loads(payload[len("PROBE=") :])

    assert result["mounted"] == 200, f"/fhir/* not mounted for {raw!r}: {result}"
    assert result["status"] == 200
    assert (
        result["guarded"] != 503
    ), f"guard tripped for FHIR_EXPORT_ENABLED={raw!r} on a mounted router: {result}"
