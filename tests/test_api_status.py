"""GET /api-status probes only endpoints that exist, and does not leak tracebacks.

It used to probe GATK's /test-job, which gatk_api.py has never had, so every call
reported a failed "test endpoint"; it repeated the version as a literal of its own;
and on an error it returned the full Python traceback to the caller.
"""

import os
import types

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("SECRET_KEY", "pytest-secret-key-not-for-production")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://pytest:pytest@localhost:5432/pytest",
)


class _Response:
    status = 200

    async def json(self):
        return {"status": "healthy"}

    async def text(self):
        return ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _fake_aiohttp(requested):
    class Session:
        def get(self, url, timeout=None):
            requested.append(url)
            return _Response()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    return types.SimpleNamespace(ClientSession=Session)


@pytest.fixture()
def main(monkeypatch):
    import app.main as main

    main.app.router.on_startup.clear()
    main.app.router.on_shutdown.clear()
    monkeypatch.setenv("GATK_API_URL", "http://gatk-api:5000")
    return main


def test_only_gatk_health_is_probed(main, monkeypatch):
    requested = []
    monkeypatch.setattr(main, "aiohttp", _fake_aiohttp(requested))

    body = TestClient(main.app).get("/api-status").json()

    assert requested == ["http://gatk-api:5000/health"]
    assert body["gatk_api"]["available"] is True
    assert "test_job_endpoint" not in body
    assert body["version"] == main.app.version


def test_an_error_is_a_500_without_a_traceback(main, monkeypatch):
    requested = []
    monkeypatch.setattr(main, "aiohttp", _fake_aiohttp(requested))

    def boom():
        raise RuntimeError("clock unavailable")

    monkeypatch.setattr(main, "time", types.SimpleNamespace(time=boom))

    response = TestClient(main.app).get("/api-status")

    assert response.status_code == 500
    assert response.json() == {"error": "clock unavailable"}
