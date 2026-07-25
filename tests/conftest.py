"""Shared pytest fixtures for the ZaroPGx test suite."""

import os

# Environment has to be set before any `app.*` module is imported: app.main and
# app.api.db read configuration at import time.
os.environ.setdefault("ZAROPGX_DEV_MODE", "true")
os.environ.setdefault("FHIR_EXPORT_ENABLED", "true")
os.environ.setdefault("SECRET_KEY", "pytest-secret-key-not-for-production")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://pytest:pytest@localhost:5432/pytest",
)
os.environ.setdefault("DB_PASSWORD", "pytest-db-password")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.db import Base, get_db
from app.services.websocket_manager import ConnectionManager
from app.services.workflow_service import WorkflowService

# Postgres schemas the ORM models are qualified with. SQLite has no CREATE
# SCHEMA, but an ATTACHed database occupies the same namespace, so attaching an
# in-memory database under each schema name lets Base.metadata.create_all()
# build the whole metadata unchanged -- no per-table allowlist to keep in sync.
_PG_SCHEMAS = ("user_data", "job_monitoring")


def _running_e2e(request: pytest.FixtureRequest) -> bool:
    if os.environ.get("ZAROPGX_E2E") == "1":
        return True
    if request.node.get_closest_marker("e2e") is not None:
        return True
    node_path = str(getattr(request.node, "path", getattr(request.node, "fspath", "")))
    return "tests/e2e" in node_path.replace("\\", "/")


@pytest.fixture(scope="session")
def engine():
    # Session-scoped: detect via env set by e2e scripts / CI before pytest
    if os.environ.get("ZAROPGX_E2E") == "1":
        yield None
        return

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )

    @event.listens_for(eng, "connect")
    def _attach_pg_schemas(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        for schema in _PG_SCHEMAS:
            cursor.execute(f"ATTACH DATABASE ':memory:' AS {schema}")
        cursor.close()

    # Force the pooled connection (and therefore the ATTACHes) into existence
    # before any DDL runs.
    with eng.connect():
        pass
    yield eng


@pytest.fixture(scope="session")
def session_factory(engine):
    if engine is None:
        return None
    return sessionmaker(
        autocommit=False, autoflush=False, bind=engine, expire_on_commit=False
    )


@pytest.fixture(autouse=True)
def database(request, engine):
    """Build every table before each test and tear them down afterwards."""
    if _running_e2e(request):
        yield
        return
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session(request, database, session_factory):
    if _running_e2e(request):
        yield None
        return
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture(autouse=True)
def override_db_dependency(request, db_session):
    """Point FastAPI's get_db at the SQLite test database.

    Applied per test and unwound afterwards, so it cannot leak into other test
    modules the way the old module-level assignment did.
    """
    if _running_e2e(request):
        yield
        return

    from app.main import app

    def _get_test_db():
        # Hand out the test's own session; the db_session fixture owns closing it.
        yield db_session

    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _get_test_db
    yield
    if previous is None:
        app.dependency_overrides.pop(get_db, None)
    else:
        app.dependency_overrides[get_db] = previous


@pytest.fixture
def client(override_db_dependency):
    from app.main import app

    # Startup/shutdown hooks reach for Postgres and sibling containers.
    app.router.on_startup.clear()
    app.router.on_shutdown.clear()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def workflow_service(db_session):
    return WorkflowService(db_session)


@pytest.fixture
def connection_manager():
    return ConnectionManager()
