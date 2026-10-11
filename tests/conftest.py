"""Shared pytest fixtures for the ZaroPGx test suite."""

import os
import sys

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

import contextlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import db as app_db
from app.api.db import get_db
from app.services.job_service import JobService
from app.services.websocket_manager import ConnectionManager
from tests import postgres as test_postgres
from tests.e2e.harness import apply_e2e_env, e2e_requested, vacuous_e2e_failure

# Why there is no PostgreSQL to test against, when there is none (outside CI).
_NO_POSTGRES: list[str] = []


def _skip_no_postgres():
    pytest.skip(f"no PostgreSQL to test against: {_NO_POSTGRES[0]}")


def _refuse_no_postgres(*args, **kwargs):
    raise RuntimeError(f"no PostgreSQL to test against: {_NO_POSTGRES[0]}")


def pytest_addoption(parser):
    # Must live in the top-level tests/conftest.py — nested conftests cannot
    # register CLI options.
    parser.addoption(
        "--zaropgx-e2e",
        action="store_true",
        default=False,
        help=(
            "Enable full-stack e2e (equivalent to ZAROPGX_E2E=1). Prefer this over "
            "relying on shell export alone — Git Bash often does not pass env to "
            "Win32 python.exe."
        ),
    )


def pytest_configure(config):
    apply_e2e_env(
        os.environ,
        cli_flag=bool(config.getoption("--zaropgx-e2e")),
    )


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    """Fail vacuous green runs when e2e was explicitly requested."""
    requested = e2e_requested(
        os.environ,
        cli_flag=bool(session.config.getoption("--zaropgx-e2e", default=False)),
    )
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    passed = len(reporter.stats.get("passed", [])) if reporter else 0
    if vacuous_e2e_failure(requested=requested, passed=passed, exitstatus=exitstatus):
        sys.stderr.write(
            "ERROR: e2e was requested (ZAROPGX_E2E=1 or --zaropgx-e2e) but 0 tests "
            "passed - likely all skipped because the enable flag never reached "
            "pytest. Re-run with: pytest -m e2e --zaropgx-e2e\n"
        )
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def _running_e2e(request: pytest.FixtureRequest) -> bool:
    if e2e_requested(os.environ):
        return True
    if request.node.get_closest_marker("e2e") is not None:
        return True
    node_path = str(getattr(request.node, "path", getattr(request.node, "fspath", "")))
    return "tests/e2e" in node_path.replace("\\", "/")


@pytest.fixture(scope="session")
def engine():
    """The suite's PostgreSQL (tests/postgres.py), with the app bound to it.

    None under e2e, which runs against the live stack's database, and when there is
    no PostgreSQL to start outside CI: the tests that need one then skip.
    """
    if e2e_requested(os.environ):
        yield None
        return
    stack = contextlib.ExitStack()
    try:
        url = stack.enter_context(test_postgres.server())
    except test_postgres.PostgresUnavailable as exc:
        if os.environ.get("CI"):
            pytest.fail(f"PostgreSQL is required in CI: {exc}")
        _NO_POSTGRES.append(str(exc))
        # A test that reaches the database through the app's own SessionLocal, not
        # a fixture, cannot skip from inside a request (Starlette's middleware turns
        # the skip into "No response returned"). It fails saying why, instead of
        # with a refused connection to the placeholder DATABASE_URL.
        previous = app_db.engine
        no_postgres = create_engine(
            "postgresql+psycopg://", creator=_refuse_no_postgres
        )
        app_db.SessionLocal.configure(bind=no_postgres)
        app_db.engine = no_postgres
        try:
            yield None
        finally:
            app_db.SessionLocal.configure(bind=previous)
            app_db.engine = previous
        return
    with stack:
        eng = create_engine(url, future=True, pool_pre_ping=True)
        test_postgres.ensure_schema(eng)
        # App code that opens its own sessions (SessionLocal()) lands here too,
        # rather than on the placeholder DATABASE_URL set above.
        previous = app_db.engine
        app_db.SessionLocal.configure(bind=eng)
        app_db.engine = eng
        try:
            yield eng
        finally:
            app_db.SessionLocal.configure(bind=previous)
            app_db.engine = previous
            eng.dispose()


@pytest.fixture(scope="session")
def session_factory(engine):
    if engine is None:
        return None
    return sessionmaker(
        autocommit=False, autoflush=False, bind=engine, expire_on_commit=False
    )


@pytest.fixture(scope="session")
def _db_reset(engine):
    return None if engine is None else test_postgres.Reset(engine)


@pytest.fixture(autouse=True)
def database(request, _db_reset):
    """After each test, empty the tables it wrote to (tests/postgres.py: Reset)."""
    if _db_reset is None or _running_e2e(request):
        yield
        return
    yield
    seeds_written = _db_reset.after_test()
    if seeds_written:
        pytest.fail(
            f"wrote to tables db/init seeds, which are not reset between tests: "
            f"{sorted(seeds_written)}",
            pytrace=False,
        )


@pytest.fixture
def db_session(request, database, session_factory):
    if _running_e2e(request):
        yield None
        return
    if session_factory is None:
        _skip_no_postgres()
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture(autouse=True)
def override_db_dependency(request, engine):
    """Point FastAPI's get_db at the test's own session.

    Applied per test and unwound afterwards, so it cannot leak into other test
    modules the way the old module-level assignment did.
    """
    if _running_e2e(request) or engine is None:
        yield
        return
    db_session = request.getfixturevalue("db_session")

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
def client(override_db_dependency, db_session):
    from app.main import app

    # Startup/shutdown hooks reach for Postgres and sibling containers.
    app.router.on_startup.clear()
    app.router.on_shutdown.clear()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def job_service(db_session):
    return JobService(db_session)


@pytest.fixture
def connection_manager():
    return ConnectionManager()
