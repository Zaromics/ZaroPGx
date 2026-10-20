"""GET /services-status checks the database on the app's own engine.

It used to call create_engine(DATABASE_URL) on every request and never dispose of
the result, so each call left a new connection pool (and its open connection)
behind. It now runs its SELECT 1 on app.api.db's engine, the one every request
already shares.
"""

import httpx
import pytest
import sqlalchemy

import app.main as main
from app.api import db as app_db


@pytest.fixture
def no_backends(monkeypatch):
    """Only the app and the database to check: every HTTP backend is off or down."""
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


def test_repeated_status_checks_create_no_engines(
    client, engine, no_backends, monkeypatch
):
    if engine is None:
        pytest.skip("no PostgreSQL to test against")
    created = []
    real_create_engine = sqlalchemy.create_engine

    def counting_create_engine(*args, **kwargs):
        created.append(args)
        return real_create_engine(*args, **kwargs)

    # Wherever the handler could look it up: the package, and app.main's globals.
    monkeypatch.setattr(sqlalchemy, "create_engine", counting_create_engine)
    monkeypatch.setattr(main, "create_engine", counting_create_engine, raising=False)

    responses = [client.get("/services-status") for _ in range(3)]

    assert created == []
    for response in responses:
        assert response.status_code == 200
        # The suite's PostgreSQL answered: the check ran on the app's engine.
        assert "database" not in response.json().get("unhealthy_services", {})
    assert app_db.engine is engine
    # Every connection the checks took went back to the pool.
    assert engine.pool.checkedout() == 0


def test_status_check_reports_a_database_the_app_cannot_reach(
    client, engine, no_backends, monkeypatch
):
    """The check follows the app's engine, not a URL of its own: when that engine
    cannot connect, the database is reported unhealthy, even with DATABASE_URL
    naming a server that would answer."""
    if engine is None:
        pytest.skip("no PostgreSQL to test against")
    monkeypatch.setenv("DATABASE_URL", engine.url.render_as_string(hide_password=False))
    unreachable = sqlalchemy.create_engine(
        "postgresql+psycopg://zaropgx_user:x@127.0.0.1:1/x",
        connect_args={"connect_timeout": 2},
    )
    monkeypatch.setattr(app_db, "engine", unreachable)
    try:
        response = client.get("/services-status")
    finally:
        unreachable.dispose()
    assert response.status_code == 200
    assert "database" in response.json()["unhealthy_services"]
