"""End-to-end contract tests for ``app/api/routes/pharmcat_router.py``.

These exercise the real router, the real ``PharmCATParser`` and the real
``get_pharmcat_summary`` composition against an in-memory SQLite database, so a
response-model/payload mismatch surfaces as the HTTP 500 a caller would see
rather than as a mocked stand-in for one.

Regression target: the router built ``PharmCATLoadResponse`` /
``PharmCATSummary`` from ``summary["actionable_findings"]`` -- which
``get_pharmcat_summary`` returns as a *list* of findings -- while both models
type ``actionable_findings`` as ``int``.  The integer lives under
``actionable_findings_count``.  ``/workflow/{id}/summary`` additionally omitted
three required ``PharmCATSummary`` fields.  Every affected route answered 500.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.db import Base as AppBase
from app.api.db import Job, get_db
from app.pharmcat.pharmcat_parser import Base as PharmcatBase
from app.pharmcat.pharmcat_parser import PharmCATParser, get_pharmcat_summary

REPORT_JSON = (
    Path(__file__).resolve().parent.parent
    / "test_data"
    / "pharmcat.example.v340.report.json"
)


# The PharmCAT tables are declared with PostgreSQL JSONB columns.  SQLite has no
# JSONB, but it stores JSON as TEXT, so teach the SQLite DDL compiler to emit a
# plain JSON column.  DDL-only and scoped to the sqlite dialect.
@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(type_, compiler, **kw):  # pragma: no cover
    return "JSON"


@pytest.fixture(scope="module")
def pgx_engine():
    """SQLite engine carrying both the app schema and the ``pharmcat`` schema."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _attach_schemas(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        for schema in ("user_data", "pharmcat"):
            cursor.execute(f"ATTACH DATABASE ':memory:' AS {schema}")
        cursor.close()

    with engine.connect():
        pass

    AppBase.metadata.create_all(bind=engine)
    PharmcatBase.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture
def pgx_session(pgx_engine):
    factory = sessionmaker(
        autocommit=False, autoflush=False, bind=pgx_engine, expire_on_commit=False
    )
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        # Leave the schema behind for the next test in the module.
        for table in reversed(PharmcatBase.metadata.sorted_tables):
            session.execute(table.delete())
        session.query(Job).delete()
        session.commit()
        session.close()


@pytest.fixture
def pgx_client(pgx_session, monkeypatch):
    """TestClient whose ``get_db`` hands out the PharmCAT-aware session."""
    from app.main import app
    from app.services.pharmcat_data_service import PharmCATDataService

    # Database-only stand-in for PharmCATDataService.get_workflow_pharmcat_summary.
    # The real method (a) filters ``Job.id == workflow_id`` with the raw string,
    # which PostgreSQL casts to uuid but SQLite's character-based UUID cannot
    # bind, and (b) calls get_pharmcat_summary without a session, so it would
    # open its own engine against DATABASE_URL.  Only those two database
    # concerns change here -- the job/metadata logic, the real summary builder
    # and the router under test are untouched.
    def _workflow_summary(self, workflow_id: str):
        job = self.db.query(Job).filter(Job.id == uuid.UUID(workflow_id)).first()
        if not job:
            return None
        run_id = (job.job_metadata or {}).get("pharmcat_run_id")
        if not run_id:
            return None
        return get_pharmcat_summary(run_id, pgx_session)

    monkeypatch.setattr(
        PharmCATDataService, "get_workflow_pharmcat_summary", _workflow_summary
    )

    def _get_pgx_db():
        yield pgx_session

    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _get_pgx_db
    app.router.on_startup.clear()
    app.router.on_shutdown.clear()
    try:
        with TestClient(app) as client:
            yield client
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous


@pytest.fixture
def report_payload() -> dict:
    return json.loads(REPORT_JSON.read_text(encoding="utf-8"))


@pytest.fixture
def loaded_run(pgx_session, report_payload) -> str:
    parser = PharmCATParser(pgx_session)
    return parser.parse_and_load(report_payload)


# ---------------------------------------------------------------------------
# Parser contract the router depends on
# ---------------------------------------------------------------------------


def test_summary_dict_separates_findings_list_from_count(pgx_session, loaded_run):
    """``actionable_findings`` is the list; the integer is ``*_count``."""
    summary = get_pharmcat_summary(loaded_run, pgx_session)

    assert isinstance(summary["actionable_findings"], list)
    assert isinstance(summary["actionable_findings_count"], int)
    assert summary["actionable_findings_count"] == len(summary["actionable_findings"])
    assert summary["actionable_findings_count"] > 0


# ---------------------------------------------------------------------------
# The three routes reported as always-500
# ---------------------------------------------------------------------------


def test_load_endpoint_returns_counts(pgx_client, pgx_session, report_payload):
    response = pgx_client.post(
        "/api/pharmcat/load",
        files={
            "file": (
                "pharmcat.report.json",
                json.dumps(report_payload).encode("utf-8"),
                "application/json",
            )
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    expected = get_pharmcat_summary(body["run_id"], pgx_session)
    assert body["actionable_findings"] == expected["actionable_findings_count"]
    assert body["actionable_findings"] > 0
    assert body["total_genes"] == expected["total_genes"]
    assert body["total_diplotypes"] == expected["total_diplotypes"]
    assert body["warning_messages"] == len(expected["warning_messages"])


def test_summary_endpoint_returns_full_summary(pgx_client, pgx_session, loaded_run):
    response = pgx_client.get(f"/api/pharmcat/summary/{loaded_run}")

    assert response.status_code == 200, response.text
    body = response.json()
    expected = get_pharmcat_summary(loaded_run, pgx_session)

    assert body["run_id"] == loaded_run
    assert body["pharmcat_version"] == expected["pharmcat_version"]
    assert body["actionable_findings"] == expected["actionable_findings_count"]
    assert (
        len(body["actionable_findings_list"]) == expected["actionable_findings_count"]
    )
    assert body["total_genes"] == expected["total_genes"]
    assert len(body["genes"]) == expected["total_genes"]
    assert body["total_messages"] == expected["total_messages"]
    assert len(body["warning_messages"]) == len(expected["warning_messages"])


def test_workflow_summary_endpoint_returns_full_summary(
    pgx_client, pgx_session, loaded_run
):
    job_id = uuid.uuid4()
    pgx_session.add(
        Job(
            id=job_id,
            name="pharmcat-router-test",
            job_metadata={"pharmcat_run_id": loaded_run},
        )
    )
    pgx_session.commit()

    response = pgx_client.get(f"/api/pharmcat/workflow/{job_id}/summary")

    assert response.status_code == 200, response.text
    body = response.json()
    expected = get_pharmcat_summary(loaded_run, pgx_session)

    assert body["run_id"] == loaded_run
    assert body["pharmcat_version"] == expected["pharmcat_version"]
    assert body["actionable_findings"] == expected["actionable_findings_count"]
    assert (
        len(body["actionable_findings_list"]) == expected["actionable_findings_count"]
    )
    assert len(body["genes"]) == expected["total_genes"]
    assert body["total_messages"] == expected["total_messages"]


def test_workflow_summary_missing_run_is_404(pgx_client, pgx_session):
    """A deliberate 404 must not be swallowed by the blanket ``except Exception``."""
    job_id = uuid.uuid4()
    pgx_session.add(Job(id=job_id, name="no-pharmcat-run", job_metadata={}))
    pgx_session.commit()

    response = pgx_client.get(f"/api/pharmcat/workflow/{job_id}/summary")

    assert response.status_code == 404, response.text


def test_workflow_data_missing_run_is_404(pgx_client, pgx_session, monkeypatch):
    from app.services.pharmcat_data_service import PharmCATDataService

    monkeypatch.setattr(
        PharmCATDataService,
        "get_pharmcat_data_for_workflow",
        lambda self, workflow_id: None,
    )

    response = pgx_client.get(f"/api/pharmcat/workflow/{uuid.uuid4()}/data")

    assert response.status_code == 404, response.text


def test_load_endpoint_rejects_non_json_upload_with_400(pgx_client):
    """The 400 guards must survive the handler's own exception funnel."""
    response = pgx_client.post(
        "/api/pharmcat/load",
        files={"file": ("report.txt", b"not json", "text/plain")},
    )

    assert response.status_code == 400, response.text


def test_load_endpoint_rejects_malformed_json_with_400(pgx_client):
    response = pgx_client.post(
        "/api/pharmcat/load",
        files={"file": ("report.json", b"{not valid json", "application/json")},
    )

    assert response.status_code == 400, response.text


# ---------------------------------------------------------------------------
# Every other route on the router -- same class of mismatch?
# ---------------------------------------------------------------------------


def test_list_routes_agree_with_their_response_models(
    pgx_client, pgx_session, loaded_run
):
    """Each collection route must serialise against its declared model."""
    genes = pgx_client.get(f"/api/pharmcat/genes/{loaded_run}")
    assert genes.status_code == 200, genes.text
    assert {g["gene_symbol"] for g in genes.json()}

    diplotypes = pgx_client.get(f"/api/pharmcat/diplotypes/{loaded_run}")
    assert diplotypes.status_code == 200, diplotypes.text
    assert diplotypes.json()

    gene_symbol = genes.json()[0]["gene_symbol"]
    filtered = pgx_client.get(
        f"/api/pharmcat/diplotypes/{loaded_run}", params={"gene_symbol": gene_symbol}
    )
    assert filtered.status_code == 200, filtered.text
    assert all(d["gene_symbol"] == gene_symbol for d in filtered.json())

    drugs = pgx_client.get(
        f"/api/pharmcat/drugs/{loaded_run}", params={"gene_symbol": gene_symbol}
    )
    assert drugs.status_code == 200, drugs.text

    messages = pgx_client.get(f"/api/pharmcat/messages/{loaded_run}")
    assert messages.status_code == 200, messages.text

    actionable = pgx_client.get(f"/api/pharmcat/actionable/{loaded_run}")
    assert actionable.status_code == 200, actionable.text
    assert len(actionable.json()) == len(
        get_pharmcat_summary(loaded_run, pgx_session)["actionable_findings"]
    )

    runs = pgx_client.get("/api/pharmcat/runs")
    assert runs.status_code == 200, runs.text
    assert any(run["run_id"] == loaded_run for run in runs.json())


def test_workflow_data_and_delete_routes(pgx_client, pgx_session, loaded_run):
    deleted = pgx_client.delete(f"/api/pharmcat/runs/{loaded_run}")
    assert deleted.status_code == 200, deleted.text

    missing = pgx_client.delete(f"/api/pharmcat/runs/{loaded_run}")
    assert missing.status_code == 404, missing.text


def test_health_route(pgx_client):
    response = pgx_client.get("/api/pharmcat/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
