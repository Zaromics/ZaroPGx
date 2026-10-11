"""main.nf hands alignments to services by path when they are on the shared volume.

pipelines/pgx/bin/input-args picks the curl arguments: ``shared_input=<real path>``
for a file under SHARED_INPUT_ROOTS (default /data/uploads and /data/results, the
services' default too: app/utils/shared_input.py), else the file as an upload. The
CRAM and SAM conversions link the converted BAM instead of copying it and no longer
publish it into the report folder; the FASTQ lane still publishes its aligned BAM.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.utils.shared_input import DEFAULT_ROOTS

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "pipelines" / "pgx" / "bin" / "input-args"
MAIN_NF = (REPO / "pipelines" / "pgx" / "main.nf").read_text(encoding="utf-8")


def _args(file, roots):
    proc = subprocess.run(
        [shutil.which("bash"), str(SCRIPT), "file", "shared_input", str(file)],
        env={
            "SHARED_INPUT_ROOTS": ":".join(str(r) for r in roots),
            "PATH": "/usr/bin:/bin",
        },
        capture_output=True,
        check=True,
    )
    return proc.stdout.decode().split("\0")[:-1]


@pytest.fixture
def volume(tmp_path):
    uploads, results, work = (tmp_path / d for d in ("uploads", "results", "work"))
    for d in (uploads, results, work):
        d.mkdir()
    return uploads, results, work


def test_a_staged_upload_goes_by_its_real_path(volume):
    uploads, results, work = volume
    bam = uploads / "upload_ab12 NA12878;type=x.bam"  # spaces and ; stay literal
    bam.write_bytes(b"BAM")
    staged = work / "in.bam"
    staged.symlink_to(bam)
    assert _args(staged, (uploads, results)) == ["--form-string", f"shared_input={bam}"]


def test_a_work_directory_file_is_uploaded(volume):
    uploads, results, work = volume
    local = work / "made_here.bam"
    local.write_bytes(b"BAM")
    assert _args(local, (uploads, results)) == ["-F", f"file=@{local}"]


def test_a_sibling_directory_is_not_mistaken_for_a_root(volume, tmp_path):
    uploads, results, _ = volume
    evil = tmp_path / "uploads-not-really"
    evil.mkdir()
    f = evil / "x.bam"
    f.write_bytes(b"BAM")
    assert _args(f, (uploads, results))[0] == "-F"


def test_the_default_roots_are_the_services_defaults():
    default = re.search(r"SHARED_INPUT_ROOTS:-([^}]+)\}", SCRIPT.read_text()).group(1)
    assert default.split(":") == list(DEFAULT_ROOTS)


def test_the_script_is_executable_in_git():
    """Nextflow runs bin/ scripts by name; a clone keeps the mode git records."""
    mode = subprocess.run(
        ["git", "ls-files", "-s", "pipelines/pgx/bin/input-args"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()[0]
    assert mode == "100755"


def _process(name):
    start = MAIN_NF.index(f"process {name} {{")
    return MAIN_NF[start : MAIN_NF.index("\nprocess ", start + 1)]


@pytest.mark.parametrize(
    "process, var",
    [
        ("FastqToBAM", "fastq"),
        ("CramToBAM", "cram"),
        ("SamToBAM", "sam"),
        ("OptiTypeHLAFromBAM", "bam"),
        ("PyPGxBam2Vcf", "bam"),
        ("MtdnaCall", "variants_file"),
    ],
)
def test_alignments_go_through_input_args(process, var):
    body = _process(process)
    assert f'input-args file shared_input "!{{{var}}}"' in body
    assert f"-F file=@!{{{var}}}" not in body


def test_cram_and_sam_link_their_bam_and_do_not_publish_it():
    for process in ("CramToBAM", "SamToBAM"):
        body = _process(process)
        head = body[: body.index("input:")]
        assert "publishDir" not in head, process
        assert 'ln -s "$BAM_PATH" .' in body and 'cp "$BAM_PATH"' not in body


def test_the_fastq_lane_still_publishes_its_aligned_bam():
    body = _process("FastqToBAM")
    assert "publishDir { outdir }, mode: 'copy'" in body[: body.index("input:")]
    assert 'ln -s "$BAM_PATH" .' in body


def test_mtdna_sends_a_vcf_as_an_upload():
    """Only an alignment is worth reading in place; a VCF is small."""
    body = _process("MtdnaCall")
    assert "bam|cram|sam) input-args" in body
    assert '-F "file=@!{variants_file}"' in body


def test_every_reader_mounts_the_same_data_volume_as_nextflow():
    """A path under /data names the same file only while all of them mount ./data."""
    import yaml

    services = yaml.safe_load((REPO / "compose.yml").read_text(encoding="utf-8"))[
        "services"
    ]
    for name in ("nextflow", "gatk-api", "pypgx", "zarohla", "mtdna"):
        assert "./data:/data" in services[name]["volumes"], name
