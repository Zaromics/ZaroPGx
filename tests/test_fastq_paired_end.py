"""A paired-end FASTQ reaches the aligner as a pair, through every hop.

Paired-end used to be refused, because the ingest carried one data file end to end and
a mate pair would have been aligned from R1 alone -- half the evidence, in a report that
looked complete. The aligner was never the obstacle (GATK's FastqToSam takes -F2 and
BwaSpark aligns pairs); the hops in between were. This module pins each of them, because
the failure mode of dropping the second mate anywhere along the way is not an error --
it is a single-end run that reports as if it were whole:

    upload_router  payload["input2"]      <- FileProcessor._accept_fastq_mate
    runner         NextflowRunRequest.input2 -> --input2
    main.nf        params.input2 -> mate2_ch -> FastqToBAM `path fastq2` -> -F file2=@
    gatk-api       /align-fastq file2 -> FastqToSam -F2, BwaSpark paired

The upload and gatk-api ends are exercised against their real handlers in
tests/test_upload_story_coherence.py and tests/test_gatk_api_no_mock_bam.py.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = REPO_ROOT / "docker" / "nextflow" / "runner.py"
MAIN_NF = REPO_ROOT / "pipelines" / "pgx" / "main.nf"
UPLOAD_ROUTER = REPO_ROOT / "app" / "api" / "routes" / "upload_router.py"


def _load_runner():
    """Import docker/nextflow/runner.py by path -- docker/ is not a package."""
    name = "zaropgx_nextflow_runner"
    if name in sys.modules:
        return sys.modules[name]
    os.environ.setdefault(
        "NEXTFLOW_PROGRESS_LOG",
        str(Path(tempfile.gettempdir()) / "zaropgx_nextflow_progress_test.log"),
    )
    spec = importlib.util.spec_from_file_location(name, RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


runner = _load_runner()


def _argv(**overrides):
    kwargs = dict(
        input_path="/data/uploads/upload_r1.fastq.gz",
        input_type="fastq",
        patient_id="p",
        report_id="r",
        reference="hg38",
        outdir="/data/reports/p",
    )
    kwargs.update(overrides)
    return runner.build_nextflow_command(**kwargs)


# --------------------------------------------------------------------------
# runner
# --------------------------------------------------------------------------


def test_the_runner_emits_input2_for_a_pair():
    cmd = _argv(input2="/data/uploads/upload_mate2_r2.fastq.gz")
    assert cmd[cmd.index("--input2") + 1] == "/data/uploads/upload_mate2_r2.fastq.gz"


def test_the_runner_emits_no_input2_for_single_end():
    """Blank means single-end to main.nf; a blank argv element buys nothing."""
    assert "--input2" not in _argv(input2="")
    assert "--input2" not in _argv()


def test_the_request_model_declares_input2():
    """Pydantic silently drops an undeclared field, which would quietly align R1
    alone -- the exact failure this chain exists to prevent."""
    req = runner.NextflowRunRequest(
        input="/data/a.fastq",
        input_type="fastq",
        patient_id="p",
        input2="/data/b.fastq",
    )
    assert req.input2 == "/data/b.fastq"


@pytest.mark.parametrize("bad", ["relative/b.fastq", "/data/b.fastq\n--skip_gatk true"])
def test_the_request_model_refuses_a_malformed_input2(bad):
    with pytest.raises(Exception):
        runner.NextflowRunRequest(
            input="/data/a.fastq", input_type="fastq", patient_id="p", input2=bad
        )


# --------------------------------------------------------------------------
# main.nf
# --------------------------------------------------------------------------


def _fastq_to_bam() -> str:
    src = MAIN_NF.read_text(encoding="utf-8")
    start = src.index("process FastqToBAM")
    return src[start : src.index("\n}\n", start)]


def test_main_nf_declares_input2_and_a_mate_channel():
    src = MAIN_NF.read_text(encoding="utf-8")
    assert "params.input2" in src
    # checkIfExists: a mistyped --input2 is an error at launch, not a quietly
    # single-end run.
    assert "file(params.input2, checkIfExists: true)" in src
    assert "FastqToBAM(input_ch, mate2_ch," in src


def test_fastq_to_bam_takes_and_sends_the_second_mate():
    block = _fastq_to_bam()
    assert "path fastq2" in block
    # Sent only when real: the single-end placeholder is the 0-byte assets/empty.tsv.
    assert '[ -s "!{fastq2}" ]' in block
    assert "file2=@!{fastq2}" in block


def test_the_single_end_placeholder_is_empty():
    """The `[ -s ]` test above only tells a mate from the placeholder if the
    placeholder really is 0 bytes."""
    placeholder = REPO_ROOT / "pipelines" / "pgx" / "assets" / "empty.tsv"
    assert placeholder.exists()
    assert placeholder.stat().st_size == 0


# --------------------------------------------------------------------------
# upload_router
# --------------------------------------------------------------------------


def test_the_upload_router_sends_input2_to_the_runner():
    src = UPLOAD_ROUTER.read_text(encoding="utf-8")
    payload = src[src.index("payload = {") : src.index("}", src.index("payload = {"))]
    assert '"input2": workflow.get("input2")' in payload
