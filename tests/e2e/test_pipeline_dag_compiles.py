"""Nextflow compiles the pipeline the app will ask it to run, for every input type.

Nothing in the suite had ever compiled ``pipelines/pgx/main.nf``. The unit tests
read it as text -- which process names exist, which conditions mention which
parameter -- and the end-to-end tests run two or three input types through the
real stack, which is slow enough that it can only cover a few. Between those two
sits the question neither asks: given the arguments the app would actually pass
for this input type, does Nextflow build a DAG at all?

It is not hypothetical. The consumer-array lane was written, reviewed and called
finished before a hand-run compile showed it wiring PyPGx into a run that had no
reads for it; the guard that came out of that (an array requires --skip_pypgx) is
pinned below, because a guard nothing exercises is a comment. The gVCF lanes'
branch conditions were then resolved by hand through a squash-merge conflict,
where a mistake would have produced exactly this class of failure: a pipeline
that parses, passes every text assertion, and dies on launch.

``nextflow run -preview`` builds the DAG and stops before the first task, so this
costs a JVM start per input type and no computation. It needs the nextflow image
rather than the stack, so it runs in the e2e job, where that image exists.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.e2e.harness import e2e_requested

pytestmark = pytest.mark.e2e

REPO_ROOT = Path(__file__).resolve().parents[2]
MAIN_NF = "/workspace/pipelines/pgx/main.nf"
# Any readable file: -preview resolves the input channel without reading the file.
INPUT = "/workspace/test_data/pharmcat.example.vcf"

# The skip flags upload_router.py derives from FileProcessor.determine_workflow
# ("true" when the step is not needed). Each row is that verdict for one input
# type -- arrays carry no reads, so HLA, PyPGx and mtDNA are all off and only the
# conversion to VCF runs; an alignment needs all of them; a VCF needs everything
# but alignment. A row that drifts from the app fails here as a compile error,
# which is the point: the two have to agree about what runs.
SKIPS = {
    "vcf": {"hla": True, "pypgx": False, "gatk": True, "mtdna": False},
    "bcf": {"hla": True, "pypgx": False, "gatk": False, "mtdna": False},
    "gvcf": {"hla": True, "pypgx": False, "gatk": False, "mtdna": False},
    "bam": {"hla": False, "pypgx": False, "gatk": True, "mtdna": False},
    "cram": {"hla": False, "pypgx": False, "gatk": False, "mtdna": False},
    "sam": {"hla": False, "pypgx": False, "gatk": False, "mtdna": False},
    "fastq": {"hla": False, "pypgx": False, "gatk": False, "mtdna": False},
    "23andme": {"hla": True, "pypgx": True, "gatk": False, "mtdna": True},
    "ancestry": {"hla": True, "pypgx": True, "gatk": False, "mtdna": True},
}


def _image() -> str:
    """The nextflow image the e2e stack is running, resolved the way
    scripts/e2e-up.sh resolves it: the environment, then .env.e2e, then
    compose.yml's own default."""
    tag = os.environ.get("ZAROPGX_TAG", "").strip()
    if not tag:
        env_e2e = REPO_ROOT / ".env.e2e"
        if env_e2e.exists():
            found = re.search(
                r"^ZAROPGX_TAG=(.+)$", env_e2e.read_text(encoding="utf-8"), re.M
            )
            tag = found.group(1).strip() if found else ""
    if not tag:
        compose = (REPO_ROOT / "compose.yml").read_text(encoding="utf-8")
        found = re.search(
            r"image:\s*zaromicsresearch/zaropgx-nextflow:\$\{ZAROPGX_TAG:-([^}]+)\}",
            compose,
        )
        assert found, "compose.yml no longer names the nextflow image this way"
        tag = found.group(1).strip()
    return f"zaromicsresearch/zaropgx-nextflow:{tag}"


@pytest.fixture(scope="session")
def nextflow_image(pytestconfig, tmp_path_factory) -> str:
    if not e2e_requested(
        os.environ, cli_flag=bool(pytestconfig.getoption("--zaropgx-e2e"))
    ):
        pytest.skip("Set ZAROPGX_E2E=1 or pass --zaropgx-e2e")
    if shutil.which("docker") is None:
        pytest.skip("docker is not on PATH")
    image = _image()
    probe = subprocess.run(
        ["docker", "image", "inspect", image], capture_output=True, text=True
    )
    if probe.returncode != 0:
        pytest.skip(f"{image} is not present; bring the stack up first")
    return image


def _compile(
    image: str, work: Path, input_type: str, **overrides
) -> subprocess.CompletedProcess:
    """Build the DAG for one input type. The repository goes in read-only: a
    preview that writes into the checkout would be a finding of its own."""
    skips = {
        **SKIPS.get(input_type, dict.fromkeys(("hla", "pypgx", "gatk", "mtdna"), True)),
        **overrides,
    }
    work.mkdir(parents=True, exist_ok=True)
    work.chmod(0o777)
    argv = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{REPO_ROOT}:/workspace:ro",
        "-v",
        f"{work}:/scratch",
        "-e",
        "NXF_HOME=/scratch/.nextflow",
        "-w",
        "/scratch",
        "--entrypoint",
        "nextflow",
        image,
        "run",
        MAIN_NF,
        "-preview",
        "-profile",
        "standard",
        "-ansi-log",
        "false",
        "--input",
        INPUT,
        "--input_type",
        input_type,
        "--patient_id",
        "p",
        "--report_id",
        "r",
        "--reference",
        "hg38",
        "--outdir",
        "/scratch/out",
        "--pharmcat_absent_to_ref",
        "false",
        "--pharmcat_unspecified_to_ref",
        "false",
    ]
    for step, skipped in skips.items():
        argv += [f"--skip_{step}", "true" if skipped else "false"]
    return subprocess.run(argv, capture_output=True, text=True, timeout=600)


@pytest.mark.parametrize("input_type", sorted(SKIPS))
def test_the_dag_compiles_for_every_input_type_the_upload_accepts(
    input_type, nextflow_image, tmp_path
):
    done = _compile(nextflow_image, tmp_path / "nf", input_type)

    assert done.returncode == 0, (
        f"nextflow could not build the DAG for {input_type}:\n"
        f"{done.stdout[-2000:]}\n{done.stderr[-2000:]}"
    )


def test_the_types_it_accepts_are_the_types_the_upload_sends(nextflow_image, tmp_path):
    """main.nf names its supported types in the refusal it raises for anything
    else. That list and the upload's frozenset are two statements of one fact."""
    from app.api.routes.upload_router import NEXTFLOW_INPUT_TYPES

    done = _compile(nextflow_image, tmp_path / "nf", "not_a_real_type")
    message = f"{done.stdout}\n{done.stderr}"

    assert done.returncode != 0, message
    named = re.search(r"Supported:\s*([^\n.]+)", message)
    assert named, f"main.nf no longer lists what it supports:\n{message[-2000:]}"
    assert {t.strip() for t in named.group(1).split(",")} == set(NEXTFLOW_INPUT_TYPES)
    assert set(SKIPS) == set(NEXTFLOW_INPUT_TYPES), "this file's table is out of date"


@pytest.mark.parametrize("input_type", ["23andme", "ancestry"])
def test_an_array_may_not_be_handed_to_pypgx(input_type, nextflow_image, tmp_path):
    """PyPGx reads a position an array does not carry as homozygous reference,
    and its outside calls override PharmCAT's no-calls -- a confidently wrong
    report rather than an incomplete one. main.nf refuses the combination at
    launch; the app never sends it (tests/test_consumer_array.py), and this is
    the other half of that: the guard itself still fires."""
    done = _compile(nextflow_image, tmp_path / "nf", input_type, pypgx=False)

    assert done.returncode != 0, "an array with PyPGx enabled compiled"
    assert "requires --skip_pypgx" in f"{done.stdout}\n{done.stderr}"
