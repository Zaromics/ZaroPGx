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


def test_resolve_under_refuses_a_symlink_loop(tmp_path):
    """Python 3.12's Path.resolve() raises RuntimeError on a loop, not OSError."""
    from app.api.utils.path_jail import resolve_under

    reports = tmp_path / "reports"
    reports.mkdir()
    try:
        (reports / "a").symlink_to(reports / "b")
        (reports / "b").symlink_to(reports / "a")
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    with pytest.raises(PermissionError):
        resolve_under(reports, "a", "x")


def test_resolve_under_refuses_a_nul(tmp_path):
    """Path.resolve() raises ValueError for an embedded NUL, not OSError."""
    from app.api.utils.path_jail import resolve_under

    with pytest.raises(PermissionError):
        resolve_under(tmp_path, "p", "x\x00y")


def test_serve_report_file_with_a_nul_is_refused_not_a_500(client):
    test_client, reports, _ = client
    (reports / "pat1").mkdir()

    resp = test_client.get("/reports/pat1/x%00y.txt")
    assert resp.status_code == 403


def test_serve_report_file_through_a_symlink_loop_is_refused_not_a_500(client):
    test_client, reports, _ = client
    patient = reports / "pat1"
    patient.mkdir()
    try:
        (patient / "a").symlink_to(patient / "b")
        (patient / "b").symlink_to(patient / "a")
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    resp = test_client.get("/reports/pat1/a/x.txt")
    assert resp.status_code == 403
