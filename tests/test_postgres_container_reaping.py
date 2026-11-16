"""A test PostgreSQL left running by a killed test run gets cleared away.

`tests/postgres.server()` stops its container in a finally, which covers a failing
test, an error and Ctrl-C. It does not cover being killed -- a `timeout` that fires,
a SIGKILL, an OOM kill -- and then the container stays up with a tmpfs and a socket
directory. Two were found alive on the dev host after a day of running suites, aged
two and four hours, from runs that had been killed by a timeout.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid

import pytest

from tests.postgres import (
    CONTAINER_LABEL,
    OWNER_LABEL,
    _reap_stale,
    stale_containers,
)

NO_SUCH_PID = 4194304  # above the usual pid_max, so never a running process


def test_a_container_whose_owner_is_gone_is_stale():
    listing = f"{CONTAINER_LABEL}-abc123 {NO_SUCH_PID}\n"
    assert stale_containers(listing, lambda pid: False) == [f"{CONTAINER_LABEL}-abc123"]


def test_a_container_whose_owner_is_running_is_left_alone():
    """The common case, and the one that must never be got wrong: suites run in
    parallel, and another run's database is not rubbish to be cleared away."""
    listing = f"{CONTAINER_LABEL}-abc123 4242\n"
    assert stale_containers(listing, lambda pid: True) == []


def test_a_row_it_cannot_read_is_left_alone():
    """Not being able to tell is a reason to leave a container running, never to
    remove one: an unparseable pid, a missing label, a blank line, a name from
    somewhere else entirely."""
    listing = "\n".join(
        [
            f"{CONTAINER_LABEL}-abc123 <no value>",
            f"{CONTAINER_LABEL}-def456",
            "pgx_db 1",
            "some-other-container 999999999",
            "",
        ]
    )
    assert stale_containers(listing, lambda pid: False) == []


def test_only_this_module_s_own_containers_are_ever_named():
    """Defence in depth: the filter is the label, but the name is checked too, so a
    stray container that happens to carry the label is still not touched unless it
    is one of ours."""
    listing = f"pgx_db {NO_SUCH_PID}\nnot-ours {NO_SUCH_PID}\n"
    assert stale_containers(listing, lambda pid: False) == []


@pytest.mark.skipif(shutil.which("docker") is None, reason="needs Docker")
def test_it_removes_a_real_abandoned_container():
    """The whole path against Docker: a container labelled as owned by a pid that
    does not exist is gone after a reap, and one labelled as owned by this process
    is still there."""
    abandoned = f"{CONTAINER_LABEL}-{uuid.uuid4().hex[:8]}"
    mine = f"{CONTAINER_LABEL}-{uuid.uuid4().hex[:8]}"

    def start(name, owner):
        done = subprocess.run(
            [
                "docker", "run", "-d", "--rm", "--name", name,
                "--label", f"{CONTAINER_LABEL}=1",
                "--label", f"{OWNER_LABEL}={owner}",
                "docker.io/library/busybox:1.37.0", "sleep", "300",
            ],
            capture_output=True, text=True,
        )  # fmt: skip
        if done.returncode != 0:
            pytest.skip(f"could not start a container: {done.stderr.strip()[:200]}")

    def exists(name):
        done = subprocess.run(
            ["docker", "ps", "-q", "--filter", f"name=^{name}$"],
            capture_output=True,
            text=True,
        )
        return bool(done.stdout.strip())

    import os

    start(abandoned, NO_SUCH_PID)
    start(mine, os.getpid())
    try:
        _reap_stale()
        assert not exists(abandoned), "a container with no owner was left running"
        assert exists(mine), "a container owned by a live process was removed"
    finally:
        for name in (abandoned, mine):
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
