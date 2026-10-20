"""The database password never reaches the app's log or an API answer.

Found running the 0.4.0 release candidate: the PostgreSQL version lookup handed
psycopg DATABASE_URL in SQLAlchemy's form (postgresql+psycopg://), which psycopg
refuses with an error quoting the whole URL, and the app logged that error on
every start. GET /services-status, which the page polls, logged DATABASE_URL
itself, and the request's headers (cookies included).
"""

import logging

import httpx
import pytest

from app.core.version_manager import VersionManager
from app.utils.redact import redact_secrets

PASSWORD = "s3cret-Passw0rd"


def test_redact_secrets_hides_url_and_environment_passwords(monkeypatch):
    monkeypatch.setenv("DB_PASSWORD", PASSWORD)
    text = (
        f"missing '=' after 'postgresql+psycopg://zaropgx_user:{PASSWORD}@db:5432/x'"
        f" and again {PASSWORD}"
    )
    redacted = redact_secrets(text)
    assert PASSWORD not in redacted
    assert "postgresql+psycopg://zaropgx_user:***@db:5432/x" in redacted
    # Text without a secret is left as it is.
    assert redact_secrets("connection refused") == "connection refused"


def test_a_failed_version_lookup_logs_no_password(monkeypatch, caplog):
    monkeypatch.delenv("POSTGRES_VERSION", raising=False)
    monkeypatch.setenv(
        "DATABASE_URL", f"postgresql+psycopg://zaropgx_user:{PASSWORD}@127.0.0.1:1/x"
    )
    with caplog.at_level(logging.DEBUG, logger="app.core.version_manager"):
        version = VersionManager()._get_postgres_version()
    assert version == "N/A"
    assert "Failed to query PostgreSQL version" in caplog.text
    assert PASSWORD not in caplog.text


def test_the_version_lookup_reads_the_apps_database_url(engine, monkeypatch):
    """DATABASE_URL in SQLAlchemy's form, the suite's Unix socket in its query, as
    the app has it: the lookup now reaches the server instead of failing."""
    if engine is None:
        pytest.skip("no PostgreSQL to test against")
    monkeypatch.delenv("POSTGRES_VERSION", raising=False)
    monkeypatch.setenv("DATABASE_URL", engine.url.render_as_string(hide_password=False))
    version = VersionManager()._get_postgres_version()
    assert version.split(".")[0].isdigit(), version


def test_services_status_keeps_the_password_out_of_log_and_answer(
    client, monkeypatch, caplog
):
    import app.main as main

    for flag in (
        "GATK_ENABLED",
        "PYPGX_ENABLED",
        "OPTITYPE_ENABLED",
        "MTDNA_ENABLED",
        "GENOME_DOWNLOADER_ENABLED",
        "KROKI_ENABLED",
        "HAPI_FHIR_ENABLED",
    ):
        monkeypatch.setattr(main, flag, False, raising=False)

    async def no_backend(self, url, *args, **kwargs):
        raise httpx.ConnectError("no backend in this test")

    monkeypatch.setattr(httpx.AsyncClient, "get", no_backend)
    monkeypatch.setenv(
        "DATABASE_URL", f"postgresql+psycopg://zaropgx_user:{PASSWORD}@127.0.0.1:1/x"
    )
    with caplog.at_level(logging.DEBUG):
        response = client.get(
            "/services-status", headers={"Cookie": "zaropgx_session=abc123secret"}
        )
    assert response.status_code == 200
    assert PASSWORD not in response.text
    assert PASSWORD not in caplog.text
    assert "abc123secret" not in caplog.text
    assert "database" in response.json()["unhealthy_services"]
