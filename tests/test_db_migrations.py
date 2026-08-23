"""Unit tests for the startup migration applier's parsing and discovery.

The DDL path is postgres-specific (advisory locks, TIMESTAMPTZ) and is exercised live
against the real database on app startup; here we pin the pure logic that decides what
to run and how each file is split into statements.
"""

from pathlib import Path

from app.api.db_migrations import (
    _DEFAULT_MIGRATIONS_DIR,
    _MIGRATION_NAME,
    _split_statements,
    apply_pending_migrations,
)


def test_split_statements_drops_comments_and_blanks():
    sql = (
        "-- a comment\n"
        "ALTER TABLE x ALTER COLUMN y TYPE VARCHAR(100);\n"
        "\n"
        "-- another comment\n"
        "DROP SCHEMA IF EXISTS z CASCADE;\n"
    )
    assert _split_statements(sql) == [
        "ALTER TABLE x ALTER COLUMN y TYPE VARCHAR(100)",
        "DROP SCHEMA IF EXISTS z CASCADE",
    ]


def test_split_statements_keeps_multiline_statement_together():
    sql = "ALTER TABLE t\nALTER COLUMN c TYPE VARCHAR(30);"
    assert _split_statements(sql) == ["ALTER TABLE t\nALTER COLUMN c TYPE VARCHAR(30)"]


def test_split_statements_ignores_comment_only_tail():
    sql = "SELECT 1;\n-- trailing note only\n"
    assert _split_statements(sql) == ["SELECT 1"]


def test_migration_name_matches_numbered_sql_only():
    # Numbered .sql files match; the applier only scans db/init/migrations/, which does
    # not contain the 00 baseline, so the pattern matching a leading-number name is fine.
    assert _MIGRATION_NAME.match("04_rename_workflows_to_jobs.sql")
    assert _MIGRATION_NAME.match("05_job_workflow_type_snapshot.sql")
    assert not _MIGRATION_NAME.match("README.md")
    assert not _MIGRATION_NAME.match("notes.sql")


def test_repo_migrations_are_discoverable_and_guarded():
    """The real migration files are discoverable and free of unguarded CREATE TABLE.

    Full idempotency is NOT required - 04 renames a column, which is not repeatable -
    which is exactly why the applier baselines an already-current schema rather than
    re-running these against it.
    """
    assert _DEFAULT_MIGRATIONS_DIR.is_dir()
    files = sorted(_DEFAULT_MIGRATIONS_DIR.glob("*.sql"))
    assert files, "expected migration files under db/init/migrations"
    for path in files:
        assert _MIGRATION_NAME.match(path.name), path.name
        body = path.read_text(encoding="utf-8").upper()
        assert "CREATE TABLE " not in body or "IF NOT EXISTS" in body, path.name


def test_apply_missing_dir_is_a_noop(tmp_path: Path):
    # No engine touched when the directory is absent.
    assert apply_pending_migrations(engine=None, migrations_dir=tmp_path / "nope") == []
