"""The project documentation is at /docs; FastAPI's API explorer is under /api.

FastAPI claims /docs for Swagger UI by default, which is why the Sphinx site used to
be mounted at /documentation. The API explorer moved instead, and the old address
redirects.
"""

import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("SECRET_KEY", "pytest-secret-key-not-for-production")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://pytest:pytest@localhost:5432/pytest",
)


@pytest.fixture()
def client():
    import app.main as main

    main.app.router.on_startup.clear()
    main.app.router.on_shutdown.clear()
    return TestClient(main.app)


def test_the_api_explorer_is_under_api(client):
    assert client.app.docs_url == "/api/docs"
    assert client.app.redoc_url == "/api/redoc"
    resp = client.get("/api/docs")
    assert resp.status_code == 200
    assert "swagger" in resp.text.lower()
    assert client.get("/api").json()["docs"] == "/api/docs"


def test_the_api_reference_page_embeds_the_moved_explorer(client):
    assert 'src="/api/docs"' in client.get("/api-reference").text


@pytest.mark.parametrize(
    "old, new",
    [
        ("/documentation", "/docs/"),
        ("/documentation/", "/docs/"),
        ("/documentation/user/usage.html", "/docs/user/usage.html"),
    ],
)
def test_old_documentation_links_redirect(client, old, new):
    resp = client.get(old, follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers["location"] == new


def test_the_home_page_links_the_documentation_at_docs():
    from pathlib import Path

    html = Path("app/templates/index.html").read_text(encoding="utf-8")
    assert 'href="/docs/"' in html
    assert "/documentation" not in html


@pytest.mark.parametrize(
    "path",
    [
        "/docs",
        "/docs/index.html",
        "/documentation",
        "/documentation/user/usage.html",
        "/api/docs",
        "/api/docs/oauth2-redirect",
        "/api/redoc",
        "/openapi.json",
    ],
)
def test_the_docs_stay_reachable_in_password_mode(path):
    from app.api.middleware.auth_gate import is_allowlisted

    assert is_allowlisted(path)


def test_moving_the_explorer_opened_nothing_else_under_api():
    from app.api.middleware.auth_gate import is_allowlisted

    assert not is_allowlisted("/api/reports")
    assert not is_allowlisted("/api/docsx")
