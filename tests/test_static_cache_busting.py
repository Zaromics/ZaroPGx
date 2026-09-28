"""A deploy must reach browsers that cached the previous release's scripts.

index.html loaded /static/js/workflow-monitor.js under a fixed URL, and /static sent
no Cache-Control, so browsers applied heuristic freshness: after the v0.2.5 -> v0.3.2
upgrade of pgx.zaromics.com a returning visitor kept running the cached v0.2.5 script,
which opens the WebSocket at /api/v1/workflows/{id}/ws. That route was renamed to
/api/v1/jobs/{id}/ws in v0.3.0, so every attempt was rejected with 403 and the demo
page looped on "Workflow error: undefined" while the job itself ran fine.
"""

import os
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("SECRET_KEY", "pytest-secret-key-not-for-production")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://pytest:pytest@localhost:5432/pytest",
)

INDEX = Path("app/templates/index.html")


@pytest.fixture()
def client():
    import app.main as main

    main.app.router.on_startup.clear()
    main.app.router.on_shutdown.clear()
    return TestClient(main.app)


def test_every_local_script_is_loaded_through_static_url():
    html = INDEX.read_text(encoding="utf-8")
    fixed = re.findall(r'<script[^>]+src="/static/[^"]+"', html)
    assert fixed == [], f"script URLs that never change between releases: {fixed}"
    assert "static_url('js/workflow-monitor.js')" in html
    assert "static_url('js/GenomeDownloadProgress.js')" in html


def test_static_url_changes_when_the_file_changes(tmp_path, monkeypatch):
    import app.main as main

    (tmp_path / "a.js").write_text("old", encoding="utf-8")
    monkeypatch.setattr(main, "STATIC_DIR", tmp_path)
    main.static_url.cache_clear()
    before = main.static_url("a.js")
    (tmp_path / "a.js").write_text("new", encoding="utf-8")
    main.static_url.cache_clear()
    after = main.static_url("a.js")

    assert before.startswith("/static/a.js?v=")
    assert before != after


def test_the_template_environment_offers_static_url():
    import app.main as main

    assert main.templates.env.globals["static_url"] is main.static_url


def test_static_files_are_revalidated(client):
    resp = client.get("/static/js/workflow-monitor.js")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-cache"
