"""A sample with no HLA reads is an empty result, not a failed job.

A targeted PGx panel that does not capture the HLA region converts to genuinely
empty FASTQs, and OptiType does not tolerate that -- it dies inside pandas with
``Length mismatch: Expected axis has 0 elements``. That is a non-zero exit, so
zarohla raised 500, ``curl --fail-with-body`` in main.nf exited 1, and the whole
run died at step 2/6 over a property of the sample rather than an error.

**Why the check has to run before OptiType, not after.** Downgrading OptiType's
exit code cannot tell "nothing to type" from "OptiType is broken", and treating
both as an empty result resurrects the bug ``pipelines/pgx/main.nf`` still carries
a comment about: a failing service reporting no HLA calls, indistinguishable from
a legitimate none. That is not cosmetic -- HLA-B*57:01 (abacavir) and HLA-A*31:01
(carbamazepine) are core-23 genes, so "no calls" reads to a clinician as "nothing
to worry about". Zero bytes out of samtools is an observed fact about the input;
a crash is an inference about a cause. Only the first is safe to act on.

So both directions are pinned here: empty input completes, and a non-empty input
that OptiType then fails on still fails.

Nothing downstream needed changing -- ``main.nf`` already resolves an absent
``*.hla_calls.tsv`` through ``hla_ch.ifEmpty(empty_file_ch)`` -- and the last test
pins that, because the fix silently depends on it.

The module is imported out-of-container the way tests/test_liftover_endpoint.py
does it (stubbed psutil/job_client, temp data tree), and samtools/OptiType are
recorded fakes: neither exists in the unit-test environment.
"""

from __future__ import annotations

import importlib.util
import logging
import re
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
ZAROHLA_SOURCE = REPO_ROOT / "docker" / "zarohla" / "app.py"
MAIN_NF = REPO_ROOT / "pipelines" / "pgx" / "main.nf"

BAM_BYTES = b"BAM\x01fake-panel-bam-with-no-hla-capture"


def _fake_psutil():
    module = types.ModuleType("psutil")
    module.virtual_memory = lambda: types.SimpleNamespace(total=16 * 1024**3)
    module.Process = lambda *a, **k: types.SimpleNamespace()
    return module


def _fake_job_client():
    module = types.ModuleType("job_client")

    class JobClient:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("no job server in tests")

    module.JobClient = JobClient
    module.create_job_client = lambda *a, **k: JobClient()
    return module


@pytest.fixture(scope="module")
def zarohla(tmp_path_factory):
    root = tmp_path_factory.mktemp("zarohla_home")
    before_handlers = list(logging.root.handlers)

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATA_DIR", str(root / "data"))
        mp.setitem(sys.modules, "psutil", _fake_psutil())
        mp.setitem(sys.modules, "job_client", _fake_job_client())

        spec = importlib.util.spec_from_file_location(
            "zaropgx_zarohla_under_test", ZAROHLA_SOURCE
        )
        module = importlib.util.module_from_spec(spec)
        mp.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
        yield module

    for handler in list(logging.root.handlers):
        if handler not in before_handlers:
            logging.root.removeHandler(handler)


@pytest.fixture()
def client(zarohla):
    return TestClient(zarohla.app)


@pytest.fixture()
def fake_tools(zarohla, monkeypatch):
    """Replace samtools/OptiType with recorded fakes.

    ``reads`` controls what ``samtools fastq`` writes: b"" reproduces the panel
    with no HLA capture, anything else a sample that has reads to type.
    """
    calls: list[list[str]] = []
    # mhc_reads: how many reads the MHC probe finds. None makes the probe fail, which
    # is the "cannot tell" case and must fall back rather than assume either way.
    state = {"reads": b"", "optitype_returncode": 0, "mhc_reads": 1}

    class FakeProc:
        def __init__(self, returncode, stdout=b""):
            self.returncode = returncode
            self.pid = 4242
            self._stdout = stdout

        async def communicate(self):
            return (self._stdout, b"OptiType blew up" if self.returncode else b"")

    async def fake_exec(*cmd, **kwargs):
        cmd = [str(c) for c in cmd]
        calls.append(cmd)
        tool = Path(cmd[0]).name

        if tool == "samtools" and cmd[1] == "index":
            return FakeProc(0 if state["mhc_reads"] is not None else 1)
        if tool == "samtools" and cmd[1] == "view" and "-H" in cmd:
            if state["mhc_reads"] is None:
                return FakeProc(1)
            return FakeProc(0, stdout=b"@SQ\tSN:chr6\tLN:170805979\n")
        if tool == "samtools" and cmd[1] == "view" and "-c" in cmd:
            if state["mhc_reads"] is None:
                return FakeProc(1)
            return FakeProc(0, stdout=str(state["mhc_reads"]).encode() + b"\n")
        if tool == "samtools" and cmd[1] == "collate":
            Path(cmd[cmd.index("-o") + 1]).write_bytes(BAM_BYTES)
            return FakeProc(0)
        if tool == "samtools" and cmd[1] == "fastq":
            for flag in ("-1", "-2"):
                Path(cmd[cmd.index(flag) + 1]).write_bytes(state["reads"])
            return FakeProc(0)
        if tool == "optitype":
            outdir = Path(cmd[cmd.index("-o") + 1])
            run = outdir / "2026_08_29"
            run.mkdir(parents=True, exist_ok=True)
            (run / "2026_08_29_result.tsv").write_text(
                "\tA1\tA2\tB1\tB2\tC1\tC2\n"
                "0\tA*01:01\tA*02:01\tB*07:02\tB*57:01\tC*07:01\tC*07:02\n",
                encoding="utf-8",
            )
            return FakeProc(state["optitype_returncode"])
        raise AssertionError(f"unexpected subprocess: {cmd}")

    monkeypatch.setattr(zarohla.asyncio, "create_subprocess_exec", fake_exec)
    return types.SimpleNamespace(calls=calls, state=state)


def _tools_run(fake_tools) -> set[str]:
    return {Path(c[0]).name for c in fake_tools.calls}


def _post_bam(client):
    return client.post(
        "/call-hla",
        files={"file": ("panel.bam", BAM_BYTES, "application/octet-stream")},
    )


# --------------------------------------------------------------------------
# No HLA reads: a result, not a failure
# --------------------------------------------------------------------------


def test_empty_fastqs_return_success_with_no_calls(client, fake_tools):
    fake_tools.state["reads"] = b""
    response = _post_bam(client)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "success"
    assert body["results"] == {}


def test_optitype_is_never_invoked_on_an_empty_input(client, fake_tools):
    """The whole point: the case is caught before the crash, not after it."""
    fake_tools.state["reads"] = b""
    _post_bam(client)

    assert "samtools" in _tools_run(fake_tools), "conversion should still have run"
    assert "optitype" not in _tools_run(fake_tools)


def test_the_reason_is_stated_rather_than_left_as_a_silent_empty(client, fake_tools):
    fake_tools.state["reads"] = b""
    warning = _post_bam(client).json().get("warning", "")

    assert "no hla reads" in warning.lower()
    # It must read as a property of the sample, not as something going wrong.
    assert "panel" in warning.lower()


# --------------------------------------------------------------------------
# A genuine OptiType failure must stay fatal
# --------------------------------------------------------------------------


def test_optitype_failure_with_reads_present_is_still_a_500(client, fake_tools):
    """The negative control this fix must not break.

    If this ever returns 200 with no calls, a broken zarohla is silently
    indistinguishable from a sample that has no HLA -- the exact regression
    main.nf's comment was written about.
    """
    fake_tools.state["reads"] = b"@r1\nACGT\n+\nIIII\n"
    fake_tools.state["optitype_returncode"] = 1

    response = _post_bam(client)

    assert response.status_code == 500
    assert "optitype" in _tools_run(fake_tools), "OptiType should have been attempted"


def test_reads_present_and_optitype_healthy_still_calls_hla(client, fake_tools):
    """The ordinary path is untouched."""
    fake_tools.state["reads"] = b"@r1\nACGT\n+\nIIII\n"
    fake_tools.state["optitype_returncode"] = 0

    body = _post_bam(client).json()

    assert body["status"] == "success"
    assert body["results"]["HLA-A"] == "A*01:01,A*02:01"
    assert body["results"]["HLA-B"] == "B*07:02,B*57:01"


# --------------------------------------------------------------------------
# The downstream assumption this fix rests on
# --------------------------------------------------------------------------


def test_main_nf_still_tolerates_an_absent_hla_calls_file():
    """`ifEmpty(empty_file_ch)` is why no pipeline change was needed.

    Returning an empty result writes no ``*.hla_calls.tsv``. That output is
    declared ``optional: true``, so without this fallback the PharmCAT step would
    wait on a channel that never emits and the run would hang instead of dying --
    a worse failure than the one being fixed.
    """
    source = MAIN_NF.read_text(encoding="utf-8")

    assert re.search(r"path\s+\"\*\.hla_calls\.tsv\",\s*optional:\s*true", source), (
        "hla_calls.tsv is no longer an optional output; a sample with no HLA "
        "reads would now fail the process instead of completing empty"
    )
    assert "hla_ch.ifEmpty(empty_file_ch)" in source, (
        "PharmCAT no longer falls back to an empty HLA file; a run with no HLA "
        "calls will hang on a channel that never emits"
    )


# --------------------------------------------------------------------------
# Reads present, but none of them in the MHC
# --------------------------------------------------------------------------
#
# The 0-byte check above is necessary but was not sufficient, and a live run on a
# chrM-only CRAM proved it: `samtools fastq` over a WHOLE alignment writes every
# read in the file, so an input with reads but none in the HLA region produced a
# thoroughly non-empty FASTQ, sailed past the guard, and died inside OptiType's
# pandas with the very "Expected axis has 0 elements" this module exists to
# prevent. The guard was reading emptiness off the wrong artefact: the FASTQ is
# only evidence about HLA content when it was built from the HLA region.
#
# The premise is fixed rather than the symptom. "No HLA reads" is still an
# observed fact about the input -- now counted directly in the MHC interval via
# the index -- never an inference from OptiType's exit code. The negative control
# above (reads present + OptiType fails => 500) still holds unchanged.


def _post_named(client, name: str):
    return client.post(
        "/call-hla",
        files={"file": (name, BAM_BYTES, "application/octet-stream")},
    )


def test_reads_present_but_none_in_the_mhc_is_an_empty_result(client, fake_tools):
    """The case the 0-byte check could not see."""
    fake_tools.state["reads"] = b"@r1\nACGT\n+\nIIII\n"  # non-empty FASTQ
    fake_tools.state["mhc_reads"] = 0

    response = _post_bam(client)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "success"
    assert body["results"] == {}


def test_optitype_is_not_invoked_when_the_mhc_is_empty(client, fake_tools):
    """Same rule as before: caught before the crash, not after it."""
    fake_tools.state["reads"] = b"@r1\nACGT\n+\nIIII\n"
    fake_tools.state["mhc_reads"] = 0

    _post_bam(client)

    assert "optitype" not in _tools_run(fake_tools)


def test_the_mhc_is_counted_on_the_input_not_guessed(client, fake_tools):
    """An observed fact about the alignment, which is the whole safety argument."""
    fake_tools.state["reads"] = b"@r1\nACGT\n+\nIIII\n"
    fake_tools.state["mhc_reads"] = 0

    _post_bam(client)

    counted = [c for c in fake_tools.calls if c[:3] == ["samtools", "view", "-c"]]
    assert counted, "the MHC interval should be counted with samtools view -c"
    region = counted[0][-1]
    assert re.match(r"^(chr)?6:\d+-\d+$", region), f"unexpected MHC region: {region}"


def test_mhc_reads_present_still_runs_optitype(client, fake_tools):
    """The ordinary path stays ordinary."""
    fake_tools.state["reads"] = b"@r1\nACGT\n+\nIIII\n"
    fake_tools.state["mhc_reads"] = 1234

    body = _post_bam(client).json()

    assert "optitype" in _tools_run(fake_tools)
    assert body["results"]["HLA-A"] == "A*01:01,A*02:01"


def test_an_unprobeable_input_falls_back_instead_of_refusing(client, fake_tools):
    """No index, no header, no answer -- so do what we did before, not worse.

    Guessing "no HLA reads" from a failed probe would be exactly the inference the
    module forbids, and refusing outright would break inputs that work today.
    """
    fake_tools.state["reads"] = b"@r1\nACGT\n+\nIIII\n"
    fake_tools.state["mhc_reads"] = None  # probe fails

    body = _post_bam(client).json()

    assert "optitype" in _tools_run(fake_tools), "must still attempt the typing"
    assert body["results"]["HLA-A"] == "A*01:01,A*02:01"
