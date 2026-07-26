"""Tests for report path jail and retired /reports stubs."""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("ZAROPGX_DEV_MODE", "true")
os.environ.setdefault("FHIR_EXPORT_ENABLED", "true")
os.environ.setdefault("SECRET_KEY", "pytest-secret-key-not-for-production")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://pytest:pytest@localhost:5432/pytest",
)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.main as main
    from app.api.utils.path_jail import resolve_under

    reports = tmp_path / "reports"
    reports.mkdir()
    monkeypatch.setattr(main, "REPORTS_DIR", reports)

    main.app.router.on_startup.clear()
    main.app.router.on_shutdown.clear()
    return TestClient(main.app), reports, resolve_under


def test_resolve_under_rejects_sibling_prefix(tmp_path):
    from app.api.utils.path_jail import resolve_under

    reports = tmp_path / "reports"
    sibling = tmp_path / "reports-old"
    reports.mkdir()
    sibling.mkdir()
    (sibling / "secret.txt").write_text("nope", encoding="utf-8")

    with pytest.raises(PermissionError):
        resolve_under(reports, "..", "reports-old", "secret.txt")


def test_serve_report_file_allows_in_jail(client):
    test_client, reports, _ = client
    patient = reports / "pat1"
    patient.mkdir()
    target = patient / "note.txt"
    target.write_text("ok", encoding="utf-8")

    resp = test_client.get("/reports/pat1/note.txt")
    assert resp.status_code == 200
    assert resp.text == "ok"


def test_serve_report_file_rejects_symlink_escape(client):
    test_client, reports, _ = client
    sibling = reports.parent / "reports-old"
    sibling.mkdir()
    (sibling / "leak.txt").write_text("secret", encoding="utf-8")
    link = reports / "evil"
    try:
        link.symlink_to(sibling, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    resp = test_client.get("/reports/evil/leak.txt")
    assert resp.status_code == 403


def test_download_all_reports_rejects_symlink_escape(client):
    test_client, reports, _ = client
    sibling = reports.parent / "reports-old"
    sibling.mkdir()
    (sibling / "leak.txt").write_text("secret", encoding="utf-8")
    link = reports / "evil"
    try:
        link.symlink_to(sibling, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    resp = test_client.get("/upload/reports/download/evil")
    assert resp.status_code == 403


def test_serve_report_file_rejects_encoded_parent_segment(client):
    from urllib.parse import quote

    test_client, reports, _ = client
    sibling = reports.parent / "reports-old"
    sibling.mkdir()
    (sibling / "leak.txt").write_text("secret", encoding="utf-8")

    # Encode so Starlette does not normalize away the .. segment before routing.
    patient = quote("../reports-old", safe="")
    resp = test_client.get(f"/reports/{patient}/leak.txt")
    assert resp.status_code == 403


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/reports/generate"),
        ("get", "/reports/abc/status"),
        ("get", "/reports/abc/download"),
        ("get", "/reports/recommendations/abc"),
        ("post", "/reports/abc/export-to-fhir"),
    ],
)
def test_retired_report_stubs_return_501(client, method, path):
    test_client, _, _ = client
    resp = getattr(test_client, method)(path)
    assert resp.status_code == 501
