"""The suite's PostgreSQL (tests/postgres.py) is what the tests assume it is.

Every database test leans on these properties without checking them: it runs on the
schema production builds, the app's own sessions reach it, and what one test writes
is gone before the next starts.
"""

from __future__ import annotations

import threading

import pytest
from sqlalchemy import text

from app.api import db as app_db
from app.api.db import Job


@pytest.fixture
def reset(_db_reset):
    if _db_reset is None:
        pytest.skip("no PostgreSQL to test against")
    return _db_reset


def _count(conn, table):
    return conn.execute(text(f"SELECT count(*) FROM {table}")).scalar()


def test_it_is_postgres_with_the_production_schema(engine, db_session):
    assert db_session.execute(text("SHOW server_version")).scalar().startswith("18.")
    for table in (
        "public.jobs",
        "user_data.patients",
        "pharmcat.results",
        "cpic.genes",
    ):
        assert db_session.execute(text(f"SELECT to_regclass('{table}')")).scalar()


def test_app_sessions_reach_it(db_session):
    """Code that opens SessionLocal() itself is on the test database too."""
    session = app_db.SessionLocal()
    try:
        ours = db_session.execute(text("SELECT current_database()")).scalar()
        assert session.execute(text("SELECT current_database()")).scalar() == ours
    finally:
        session.close()


def test_what_a_test_writes_is_emptied_and_seeds_are_kept(engine, db_session, reset):
    db_session.add(Job(name="written by this test", job_metadata={}))
    db_session.commit()
    with engine.connect() as conn:
        seeded = _count(conn, "cpic.genes")
        assert _count(conn, "public.jobs") == 1 and seeded

    assert reset.after_test() == set()

    with engine.connect() as conn:
        assert _count(conn, "public.jobs") == 0
        assert _count(conn, "cpic.genes") == seeded


def test_a_write_to_seed_data_is_reported(engine, reset):
    with engine.begin() as conn:
        # Matches without changing anything, so the seed stays intact.
        conn.execute(text("DELETE FROM cpic.genes WHERE false"))
    assert reset.after_test() == {'"cpic"."genes"'}


def test_a_leaked_transaction_does_not_block_the_reset(engine, reset):
    """test_router_session_leaks.py leaks sessions on purpose; one left holding a
    lock must not hang every test after it."""
    leaked = engine.connect()
    leaked.begin()
    leaked.execute(
        text("INSERT INTO public.jobs (id, name) VALUES (gen_random_uuid(), 'x')")
    )
    done = threading.Event()
    threading.Thread(
        target=lambda: (reset.after_test(), done.set()), daemon=True
    ).start()
    try:
        assert done.wait(30), "the reset waited on the leaked transaction"
        with engine.connect() as conn:
            assert _count(conn, "public.jobs") == 0
    finally:
        leaked.invalidate()
