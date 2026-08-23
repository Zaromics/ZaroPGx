"""Apply pending SQL migrations at startup.

Postgres runs ``db/init/*.sql`` only on a *fresh* data directory, so a schema change
never reaches an already-created volume on its own. That is exactly how a deployed
``zaropgx_pgdata`` drifted to a pre-``jobs`` schema and made every upload fail with
``relation "jobs" does not exist`` until the volume was reset by hand.

The migrations in ``db/init/migrations/`` are historical transforms that turn a
pre-``jobs`` schema into the current one, which ``00_complete_database_schema.sql``
now embeds directly. They are NOT all idempotent (04 renames a column, which fails
once the column already has its new name), so the applier baselines: the first time it
runs against a database that is already current (the ``jobs`` table exists), it records
the existing migrations as applied without running them; a genuinely old volume (no
``jobs`` table) runs them to migrate. Applied files are recorded in ``schema_migrations``
so each runs once, under an advisory lock so concurrent app workers don't race.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import List

from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# Arbitrary, stable key so only one worker applies migrations at a time.
_ADVISORY_LOCK_KEY = 8230823

# app/api/db_migrations.py -> app/api -> app -> repo root; migrations live under db/init.
_DEFAULT_MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[2] / "db" / "init" / "migrations"
)

_MIGRATION_NAME = re.compile(r"^\d+_.*\.sql$")


def _split_statements(sql: str) -> List[str]:
    """Split a simple migration file into individual statements.

    These migrations contain no functions or dollar-quoted bodies, so a plain split on
    ``;`` is safe - but strip ``--`` line comments FIRST, because a comment can itself
    contain a semicolon and would otherwise be split into a bogus statement.
    """
    no_comments = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    return [stmt.strip() for stmt in no_comments.split(";") if stmt.strip()]


def apply_pending_migrations(
    engine: Engine, migrations_dir: Path | None = None
) -> List[str]:
    """Apply any not-yet-recorded migrations in order. Returns the files applied."""
    directory = Path(migrations_dir) if migrations_dir else _DEFAULT_MIGRATIONS_DIR
    if not directory.is_dir():
        logger.warning("Migrations directory %s not found; skipping", directory)
        return []

    files = sorted(p for p in directory.glob("*.sql") if _MIGRATION_NAME.match(p.name))
    if not files:
        return []

    applied: List[str] = []
    with engine.begin() as conn:
        # Serialize across workers for the whole apply; the lock is released on commit.
        conn.execute(
            text("SELECT pg_advisory_xact_lock(:k)"), {"k": _ADVISORY_LOCK_KEY}
        )
        table_existed = (
            conn.execute(
                text("SELECT to_regclass('public.schema_migrations')")
            ).scalar()
            is not None
        )
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "filename TEXT PRIMARY KEY, "
                "applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
        )
        already = {
            row[0]
            for row in conn.execute(text("SELECT filename FROM schema_migrations"))
        }

        # Baseline: the very first time this runs against a database that is ALREADY at
        # the current schema (00_complete_database_schema.sql, which embeds every
        # migration's end state), record the existing migrations as applied without
        # running them. They are historical transforms and not all idempotent - e.g.
        # 04's `RENAME COLUMN workflow_id TO job_id` fails once the column is already
        # `job_id`. A genuinely old volume (no `jobs` table) skips the baseline and
        # runs them to migrate. After this first run, new migrations apply normally.
        if not table_existed and not already:
            jobs_exists = (
                conn.execute(text("SELECT to_regclass('public.jobs')")).scalar()
                is not None
            )
            if jobs_exists:
                for path in files:
                    conn.execute(
                        text("INSERT INTO schema_migrations (filename) VALUES (:f)"),
                        {"f": path.name},
                    )
                logger.info(
                    "Baselined %d existing migration(s) on an already-current schema",
                    len(files),
                )
                return []

        for path in files:
            if path.name in already:
                continue
            logger.info("Applying migration %s", path.name)
            for statement in _split_statements(path.read_text(encoding="utf-8")):
                conn.execute(text(statement))
            conn.execute(
                text("INSERT INTO schema_migrations (filename) VALUES (:f)"),
                {"f": path.name},
            )
            applied.append(path.name)

    if applied:
        logger.info("Applied %d migration(s): %s", len(applied), ", ".join(applied))
    else:
        logger.debug("No pending migrations")
    return applied
