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
import sqlalchemy.sql.sqltypes as sqltypes
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.db import Base as AppBase
from app.api.db import Job, get_db
from app.pharmcat import pharmcat_parser
from app.pharmcat.pharmcat_parser import Base as PharmcatBase
from app.pharmcat.pharmcat_parser import (
    PharmCATDiplotype,
    PharmCATParser,
    get_pharmcat_summary,
)

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
def pg_uuid_binds(monkeypatch):
    """Let SQLite bind a str to a UUID column, the way PostgreSQL casts one.

    ``PharmCATDataService`` filters ``Job.id == workflow_id`` with the raw path
    string.  PostgreSQL casts text to uuid; SQLite stores UUIDs as CHAR(32) and
    its bind processor calls ``value.hex``, so a str raises ``AttributeError``.
    Normalising str -> UUID at bind time reproduces the PostgreSQL behaviour and
    lets the production query run unmodified.  Backend emulation only, in the
    same spirit as the JSONB DDL shim above.
    """
    original = sqltypes.Uuid.bind_processor

    def _tolerant_bind_processor(self, dialect):
        processor = original(self, dialect)
        if processor is None:
            return None

        def process(value):
            if isinstance(value, str):
                try:
                    value = uuid.UUID(value)
                except ValueError:
                    return processor(value)
            return processor(value)

        return process

    monkeypatch.setattr(sqltypes.Uuid, "bind_processor", _tolerant_bind_processor)


@pytest.fixture
def sessionless_sessions(pgx_engine, monkeypatch):
    """Capture every session PharmCATParser opens for itself.

    ``PharmCATParser`` falls back to ``app.api.db.SessionLocal`` -- the app's
    canonical factory, the same one ``get_db()`` uses -- whenever it is handed
    no session.  Substituting a factory bound to the test engine keeps that
    path from reaching a real database, so the service method, the parser and
    the summary builder all run unmodified either way.  An empty list is the
    healthy outcome: ``get_workflow_pharmcat_summary`` passes the request
    session, so a recorded session means a second connection leaked outside
    the request transaction.
    """
    opened = []
    _TestSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=pgx_engine, expire_on_commit=False
    )

    def _session_local(*args, **kwargs):
        session = _TestSessionLocal(*args, **kwargs)
        opened.append(session)
        return session

    monkeypatch.setattr(pharmcat_parser, "SessionLocal", _session_local)
    return opened


@pytest.fixture
def pgx_client(pgx_session, pg_uuid_binds, sessionless_sessions):
    """TestClient whose ``get_db`` hands out the PharmCAT-aware session."""
    from app.main import app

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


# Every activityScore in pharmcat.example.v340.report.json is null or
# "No Result" ({None: 10, 'No Result': 2}), so the checked-in fixture cannot
# exercise a real numeric score at all -- least of all 0, the score that
# accompanies a Poor Metabolizer call.  This synthetic report closes that gap
# across the whole ingest -> store -> read -> serialise chain.
SCORE_CASES = {
    "CYP2D6": (0, "Poor Metabolizer", 0.0),
    "CYP2C19": (1.5, "Intermediate Metabolizer", 1.5),
    "DPYD": ("n/a", "Indeterminate", None),
    "TPMT": ("No Result", "Indeterminate", None),
    "NUDT15": (None, "Indeterminate", None),
}


@pytest.fixture
def activity_score_run(pgx_session) -> str:
    payload = {
        "title": "activity-score-run",
        "pharmcatVersion": "3.4.0",
        "genes": {
            gene: {
                "geneSymbol": gene,
                "sourceDiplotypes": [
                    {
                        "label": f"{gene}:synthetic",
                        "activityScore": raw,
                        "phenotypes": [phenotype],
                        "allele1": {"name": "*1", "function": "Normal function"},
                        "allele2": {"name": "*2", "function": "No function"},
                        "matchScore": 7,
                        "inferred": True,
                        "combination": False,
                    }
                ],
            }
            for gene, (raw, phenotype, _) in SCORE_CASES.items()
        },
    }
    return PharmCATParser(pgx_session).parse_and_load(payload)


# ---------------------------------------------------------------------------
# Parser contract the router depends on
# ---------------------------------------------------------------------------


def test_zero_activity_score_survives_the_round_trip(pgx_session, activity_score_run):
    """``Decimal("0.0000")`` is falsy -- it must not be read back as ``None``."""
    stored = {
        row.gene_symbol: row.activity_score
        for row in pgx_session.query(PharmCATDiplotype)
        .filter(PharmCATDiplotype.run_id == activity_score_run)
        .all()
    }
    assert stored["CYP2D6"] is not None and float(stored["CYP2D6"]) == 0.0

    parser = PharmCATParser(pgx_session)
    read_back = {
        d["gene_symbol"]: d["activity_score"]
        for d in parser.get_diplotypes(activity_score_run)
    }
    for gene, (_, _, expected) in SCORE_CASES.items():
        assert read_back[gene] == expected, gene

    findings = {
        f["gene_symbol"]: f["activity_score"]
        for f in parser.get_actionable_findings(activity_score_run)
    }
    assert findings["CYP2D6"] == 0.0
    assert findings["CYP2C19"] == 1.5


def test_non_numeric_activity_score_is_normalised_to_null(
    pgx_session, activity_score_run
):
    """A DECIMAL column must never receive an "n/a"/"No Result" sentinel."""
    stored = {
        row.gene_symbol: row.activity_score
        for row in pgx_session.query(PharmCATDiplotype)
        .filter(PharmCATDiplotype.run_id == activity_score_run)
        .all()
    }
    for gene in ("DPYD", "TPMT", "NUDT15"):
        assert stored[gene] is None, gene


def test_diplotypes_endpoint_exposes_activity_score_and_call_quality(
    pgx_client, activity_score_run
):
    response = pgx_client.get(f"/api/pharmcat/diplotypes/{activity_score_run}")

    assert response.status_code == 200, response.text
    by_gene = {d["gene_symbol"]: d for d in response.json()}

    for gene, (_, _, expected) in SCORE_CASES.items():
        assert by_gene[gene]["activity_score"] == expected, gene

    # match_score / inferred / combination used to be dropped by extra='ignore'
    cyp2d6 = by_gene["CYP2D6"]
    assert cyp2d6["match_score"] == 7
    assert cyp2d6["inferred"] is True
    assert cyp2d6["combination"] is False


def test_actionable_endpoint_keeps_zero_activity_score(pgx_client, activity_score_run):
    response = pgx_client.get(f"/api/pharmcat/actionable/{activity_score_run}")

    assert response.status_code == 200, response.text
    by_gene = {f["gene_symbol"]: f for f in response.json()}

    assert by_gene["CYP2D6"]["phenotype"] == "Poor Metabolizer"
    assert by_gene["CYP2D6"]["activity_score"] == 0.0
    assert by_gene["CYP2C19"]["activity_score"] == 1.5


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
    pgx_client, pgx_session, loaded_run, sessionless_sessions
):
    from app.services.pharmcat_data_service import PharmCATDataService

    # Guard against the route being proved by a test double: the service method
    # must be the production one.
    assert (
        PharmCATDataService.get_workflow_pharmcat_summary.__module__
        == "app.services.pharmcat_data_service"
    )

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
    # The service handed get_pharmcat_summary the request session, so the parser
    # never opened a session of its own -- no second connection outside the
    # request transaction.
    assert not sessionless_sessions, "get_pharmcat_summary opened its own session"
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


def test_workflow_data_missing_run_is_404(pgx_client):
    """Real PharmCATDataService.get_pharmcat_data_for_workflow, unknown job."""
    response = pgx_client.get(f"/api/pharmcat/workflow/{uuid.uuid4()}/data")

    assert response.status_code == 404, response.text


def test_summary_endpoint_unknown_run_is_404(pgx_client):
    """An unknown run must 404, not a 200 full of zeros."""
    response = pgx_client.get("/api/pharmcat/summary/does-not-exist-anywhere")

    assert response.status_code == 404, response.text
    assert "not found" in response.json()["detail"]


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
# The schema gate is a statement about the *request*, not about this server
# ---------------------------------------------------------------------------

# Valid JSON, refused by validate_report(): every gene block has lost
# ``sourceDiplotypes``, so loading it would store a run in which every gene reads
# Unknown/Unknown.  ``genes`` is still populated, so nothing shallower notices.
UNREADABLE_REPORT = {
    "pharmcatVersion": "3.4.0",
    "genes": {
        "CYP2C19": {"geneSymbol": "CYP2C19"},
        "CYP2D6": {"geneSymbol": "CYP2D6"},
    },
    "drugs": {},
}


def _post_report(client, payload):
    return client.post(
        "/api/pharmcat/load",
        files={
            "file": (
                "report.json",
                json.dumps(payload).encode("utf-8"),
                "application/json",
            )
        },
    )


def test_load_endpoint_answers_400_for_a_payload_the_gate_refuses(pgx_client):
    """500 says "this server is broken" and invites a retry; nothing is broken.

    ``PharmCATSchemaError`` is a verdict on the uploaded body -- well-formed JSON
    that is not a report.json this parser can read -- so it belongs in the 4xx
    range.  It used to fall through the handler's blanket ``except Exception``
    and answer 500.

    400 rather than 422: FastAPI already spends 422 on ``RequestValidationError``,
    whose ``detail`` is a *list* of error objects, and the sibling guard tests
    above pin 400-with-a-string for the other two refusals on this route.
    """
    response = _post_report(pgx_client, UNREADABLE_REPORT)

    assert response.status_code == 400, response.text


def test_the_refusal_explains_which_part_of_the_payload_was_refused(pgx_client):
    response = _post_report(pgx_client, UNREADABLE_REPORT)

    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert "PharmCAT" in detail, detail
    assert "sourceDiplotypes" in detail, detail


def test_a_refused_payload_leaves_nothing_in_the_database(pgx_client, pgx_session):
    """The gate runs before the first write and the context manager rolls back,
    so a refusal must not leave a half-loaded run for a later query to find."""
    from app.pharmcat.pharmcat_parser import PharmCATResult

    assert _post_report(pgx_client, UNREADABLE_REPORT).status_code == 400
    assert pgx_session.query(PharmCATResult).count() == 0


def test_a_readable_payload_is_still_accepted(pgx_client, report_payload):
    """The refusal must not become a new way to reject working uploads."""
    assert _post_report(pgx_client, report_payload).status_code == 200


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
