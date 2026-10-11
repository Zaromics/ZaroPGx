"""A service's 4xx refusal fails the run once; it is not retried.

nextflow.config retries every failed task once. For a refusal that re-sent the whole
input to be refused again: NA12878's 15 GB CRAM, compressed against a different FASTA,
took two uploads and two indexings (7 minutes) to fail where one (3 minutes) says the
same thing. Each guarded service call now exits 65 on a 4xx, which the errorStrategy
terminates on; a 5xx or no response still exits 1 and is retried.

These run each call site's own shell against a fake curl that answers with a chosen
HTTP status, the way --fail-with-body plus -w '%{http_code}' would.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

PIPELINE = Path(__file__).resolve().parents[1] / "pipelines" / "pgx"
MAIN_NF = PIPELINE / "main.nf"
CALL = re.compile(r"^[ ]*if ! HTTP_CODE=\$\(curl .*?^[ ]*fi$", re.M | re.S)

FAKE_CURL = """#!/usr/bin/env bash
# Writes an error body to the -o file and prints the status, as curl -w would.
out=""
while [ $# -gt 0 ]; do
  if [ "$1" = "-o" ]; then out="$2"; shift; fi
  shift
done
[ -n "$out" ] && echo '{"detail": "from the fake service"}' > "$out"
printf '%s' "$FAKE_STATUS"
case "$FAKE_STATUS" in
  000) exit 7 ;;
  2??) exit 0 ;;
  *) exit 22 ;;
esac
"""


def _call_sites():
    return CALL.findall(MAIN_NF.read_text(encoding="utf-8"))


def _run(block: str, status: str, tmp_path: Path) -> subprocess.CompletedProcess:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    curl = bin_dir / "curl"
    curl.write_text(FAKE_CURL)
    curl.chmod(0o755)
    script = (
        f"set -euo pipefail\nCURL_ARGS=( -F x=y )\n{block}\necho reached-the-result\n"
    )
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_STATUS": status,
    }
    return subprocess.run(
        ["bash", "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True
    )


def test_every_guarded_call_site_is_covered():
    """All nine --fail-with-body sites; none left on the old exit-1-for-all form."""
    text = MAIN_NF.read_text(encoding="utf-8")
    assert len(_call_sites()) == 9
    assert "if ! curl -sS --fail-with-body" not in text


@pytest.mark.parametrize(
    "block", _call_sites(), ids=lambda b: b.split("http://")[1].split(")")[0]
)
@pytest.mark.parametrize(
    "status, exit_code",
    [("400", 65), ("413", 65), ("422", 65), ("500", 1), ("503", 1), ("000", 1)],
)
def test_a_refusal_exits_65_and_anything_else_exits_1(
    block, status, exit_code, tmp_path
):
    result = _run(block, status, tmp_path)
    assert result.returncode == exit_code
    # The server's message and the status still reach .command.err.
    assert f"(HTTP {status})" in result.stderr
    assert "from the fake service" in result.stderr


@pytest.mark.parametrize("block", _call_sites()[:1])
def test_a_success_carries_on(block, tmp_path):
    result = _run(block, "200", tmp_path)
    assert result.returncode == 0
    assert "reached-the-result" in result.stdout


def test_the_error_strategy_terminates_on_65_and_retries_the_rest():
    config = (PIPELINE / "nextflow.config").read_text(encoding="utf-8")
    assert "errorStrategy = { task.exitStatus == 65 ? 'terminate' : 'retry' }" in config
    assert "maxRetries = 1" in config
