"""The PostgreSQL the test suite runs against.

Production runs PostgreSQL (compose.yml's ``db`` service), initialised from
``db/init``. The suite used to run on an in-memory SQLite instead, which needed
shims for schemas, JSONB and UUIDs and still behaved differently: it returned naive
timestamps (commit 8bb100e fixed a crash only SQLite could cause) and never enforced
a VARCHAR length. So the tests now get the real thing:

- ``server()`` starts that same image, built from ``db/init`` exactly as production
  is, in a throwaway container with its data on tmpfs, or uses the database at
  ZAROPGX_TEST_POSTGRES_URL (``ensure_schema`` builds ``db/init`` on it if needed).
- ``Reset`` empties, after each test, the tables that test wrote to.
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Iterator, List

import yaml
from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

ROOT = Path(__file__).resolve().parents[1]
INIT_DIR = ROOT / "db" / "init"
URL_ENV = "ZAROPGX_TEST_POSTGRES_URL"
# Every schema db/init creates tables in.
SCHEMAS = ("public", "user_data", "pharmcat", "reports", "cpic", "fhir")
# A Unix socket path may not exceed 107 bytes; "/.s.PGSQL.5432" takes 14.
_SOCKET_DIR_MAX = 107 - len("/.s.PGSQL.5432")


class PostgresUnavailable(RuntimeError):
    """No PostgreSQL to test against: no URL given and no usable Docker."""


def image() -> str:
    """The image compose.yml's db service runs."""
    compose = yaml.safe_load((ROOT / "compose.yml").read_text(encoding="utf-8"))
    return compose["services"]["db"]["image"]


def _socket_dir() -> Path:
    """A directory the container's postgres user can create its socket in.

    Nested in one we own: the image chowns its socket directory to postgres, and in
    a sticky /tmp we could not remove it afterwards.
    """
    base = tempfile.gettempdir()
    if len(base) + len("/zaropgx-pg-xxxxxxxx/s") > _SOCKET_DIR_MAX:
        base = "/tmp"
    socket_dir = Path(tempfile.mkdtemp(prefix="zaropgx-pg-", dir=base)) / "s"
    socket_dir.mkdir()
    socket_dir.chmod(0o777)
    return socket_dir


def _wait_until_ready(name: str, deadline: float) -> None:
    """pg_isready over TCP inside the container. The image runs db/init on a server
    that listens on the socket only, so TCP answers once init has finished and the
    final server is up."""
    probe = ["docker", "exec", name, "pg_isready", "-q", "-h", "127.0.0.1"]
    while subprocess.run(probe, capture_output=True).returncode != 0:
        if time.monotonic() > deadline:
            logs = subprocess.run(
                ["docker", "logs", name], capture_output=True, text=True
            )
            raise PostgresUnavailable(
                f"PostgreSQL did not come up:\n{logs.stderr[-2000:]}"
            )
        time.sleep(0.5)


# Containers this module starts carry these labels: one to find them by, one naming
# the process that started them.
CONTAINER_LABEL = "zaropgx-pytest-pg"
OWNER_LABEL = "zaropgx-pytest-owner"


def stale_containers(listing: str, alive) -> List[str]:
    """The names in a `docker ps` listing whose owning process is gone.

    `server()` stops its container in a finally, which covers a failing test, an
    error and Ctrl-C. It cannot cover being killed: a `timeout` that fires, a
    SIGKILL, an OOM kill, a crash. Then the container stays up -- with a tmpfs and
    a socket directory -- until someone notices, and on a long session they
    accumulate. So each one records the PID that started it, and a later run clears
    out the ones whose PID is no longer there.

    `listing` is `docker ps --format "{{.Names}} {{.Label \"...\"}}"` output and
    `alive` answers whether a PID is running, both passed in so this is testable
    without Docker. A row whose PID is unreadable is left alone: not being able to
    tell is a reason to leave a container running, never to remove one.
    """
    stale = []
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        name, owner = parts
        if not name.startswith(f"{CONTAINER_LABEL}-"):
            continue
        try:
            pid = int(owner)
        except ValueError:
            continue
        if not alive(pid):
            stale.append(name)
    return stale


def _pid_alive(pid: int) -> bool:
    return pathlib.Path(f"/proc/{pid}").exists()


def _reap_stale() -> None:
    """Remove containers left behind by a killed run. Never raises: a failure here
    costs a stale container, which is what we started with, not the test run."""
    try:
        listing = subprocess.run(
            [
                "docker", "ps", "--filter", f"label={CONTAINER_LABEL}",
                "--format", f'{{{{.Names}}}} {{{{.Label "{OWNER_LABEL}"}}}}',
            ],
            capture_output=True, text=True, timeout=30,
        )  # fmt: skip
        if listing.returncode != 0:
            return
        for name in stale_containers(listing.stdout, _pid_alive):
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return


@contextlib.contextmanager
def server() -> Iterator[str]:
    """Yield the URL of a PostgreSQL holding db/init's schema."""
    url = os.environ.get(URL_ENV)
    if url:
        yield url
        return
    if shutil.which("docker") is None:
        raise PostgresUnavailable(f"needs Docker, or {URL_ENV}")
    name = f"zaropgx-pytest-pg-{uuid.uuid4().hex[:8]}"
    # Reached through a Unix socket in a bind-mounted directory, not a published
    # port. A port Docker picks lies in the ephemeral range (32768-60999), and
    # rootless Docker's pasta forwarding (--tcp-ports=auto) skips that range: on the
    # dev host such a port never reached 127.0.0.1, while 18799 did within 2 s.
    socket_dir = _socket_dir()
    _reap_stale()
    started = subprocess.run(
        [
            "docker", "run", "-d", "--rm", "--name", name,
            "--label", f"{CONTAINER_LABEL}=1",
            "--label", f"{OWNER_LABEL}={os.getpid()}",
            "-e", "POSTGRES_USER=zaropgx_user",  # db/init grants to this role
            "-e", "POSTGRES_PASSWORD=pytest",
            "-e", "POSTGRES_DB=zaropgx_db",
            "--tmpfs", "/var/lib/postgresql",
            "-v", f"{INIT_DIR}:/docker-entrypoint-initdb.d:ro",
            "-v", f"{socket_dir}:/var/run/postgresql",
            image(),
        ],
        capture_output=True,
        text=True,
    )  # fmt: skip
    if started.returncode != 0:
        shutil.rmtree(socket_dir.parent, ignore_errors=True)
        raise PostgresUnavailable(
            f"could not start PostgreSQL: {started.stderr.strip()}"
        )
    try:
        _wait_until_ready(name, time.monotonic() + 120)
        yield f"postgresql+psycopg://zaropgx_user:pytest@/zaropgx_db?host={socket_dir}"
    finally:
        # A clean stop removes the socket and lock files; --rm removes the container.
        subprocess.run(["docker", "stop", "-t", "10", name], capture_output=True)
        shutil.rmtree(socket_dir.parent, ignore_errors=True)


def ensure_schema(engine: Engine) -> None:
    """Build db/init on a database given by URL that does not have it yet."""
    with engine.connect() as conn:
        if conn.execute(text("SELECT to_regclass('public.jobs')")).scalar():
            return
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cursor:
            for script in sorted(INIT_DIR.glob("*.sql")):
                cursor.execute(script.read_text(encoding="utf-8"))
        raw.commit()
    finally:
        raw.close()


_WRITE = re.compile(
    r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM|TRUNCATE|MERGE\s+INTO|COPY)\b", re.I
)


class Reset:
    """Empty, after each test, the tables it wrote to.

    Tables db/init seeds (the cpic reference rows, the gene groups) are left alone
    and reported if a test writes to them. The rest are truncated together, without
    CASCADE, so a foreign key from a seeded table into one of them fails loudly
    instead of quietly emptying the seed.
    """

    def __init__(self, engine: Engine):
        self.engine = engine
        with engine.connect() as conn:
            tables = [
                f'"{schema}"."{name}"'
                for schema, name in conn.execute(
                    text(
                        "SELECT n.nspname, c.relname FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE c.relkind IN ('r', 'p') AND n.nspname = ANY(:schemas) "
                        "ORDER BY 1, 2"
                    ),
                    {"schemas": list(SCHEMAS)},
                )
            ]
            seeded = {
                table
                for table in tables
                if conn.execute(text(f"SELECT EXISTS (SELECT 1 FROM {table})")).scalar()
            }
        self.tables = [table for table in tables if table not in seeded]
        # Matched in write statements by qualified name; a public table may also
        # appear bare, since search_path resolves it.
        self._seeds = {}
        for table in seeded:
            schema, name = (re.escape(part.strip('"')) for part in table.split("."))
            prefix = rf"(?:{schema}\.)?" if schema == "public" else rf"{schema}\."
            self._seeds[table] = re.compile(rf"\b{prefix}{name}\b", re.I)
        self.wrote = False
        self.seeds_written: set[str] = set()
        self._resetting = False
        event.listen(engine, "after_cursor_execute", self._watch)

    def _watch(self, conn, cursor, statement, parameters, context, executemany):
        if self._resetting or not _WRITE.search(statement):
            return
        self.wrote = True
        for table, pattern in self._seeds.items():
            if pattern.search(statement):
                self.seeds_written.add(table)

    def after_test(self) -> set[str]:
        """Empty what the test wrote; return any seeded tables it wrote to."""
        seeds_written, self.seeds_written = self.seeds_written, set()
        if not self.wrote:
            return seeds_written
        self.wrote = False
        self._resetting = True
        try:
            self._truncate()
        finally:
            self._resetting = False
        return seeds_written

    def _truncate(self) -> None:
        statement = text(f"TRUNCATE {', '.join(self.tables)} RESTART IDENTITY")
        for attempt in range(2):
            try:
                with self.engine.begin() as conn:
                    conn.execute(text("SET LOCAL lock_timeout = '5s'"))
                    conn.execute(statement)
                return
            except OperationalError:
                if attempt:
                    raise
                # A session the test leaked still holds its transaction open, and
                # with it a lock TRUNCATE has to wait for. The test is over; end it.
                with self.engine.begin() as conn:
                    conn.execute(
                        text(
                            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                            "WHERE datname = current_database() "
                            "AND pid <> pg_backend_pid() "
                            "AND state LIKE 'idle in transaction%'"
                        )
                    )
