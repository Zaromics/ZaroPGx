"""app/utils/shared_input.py: a service reads a shared input in place, safely."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.utils.shared_input import (
    SharedInputRefused,
    current_index,
    link_shared_input,
    resolve_shared_input,
)


@pytest.fixture
def data(tmp_path, monkeypatch):
    root = tmp_path / "data"
    for sub in ("uploads", "results/job1", "reference", "temp/zarohla"):
        (root / sub).mkdir(parents=True)
    monkeypatch.setenv("SHARED_INPUT_ROOTS", f"{root / 'uploads'}:{root / 'results'}")
    return root


def test_an_upload_is_linked_where_the_upload_would_have_been_stored(data, tmp_path):
    bam = data / "uploads" / "upload_ab12_NA12878.final.bam"
    bam.write_bytes(b"BAM\x01reads")
    work = tmp_path / "work"
    work.mkdir()

    link = link_shared_input(str(bam), work / "f00d.bam")

    assert link.is_symlink() and link.resolve() == bam
    assert link.read_bytes() == b"BAM\x01reads"
    # The service's cleanup removes the link, never the upload.
    os.remove(link)
    assert bam.exists()


def test_a_converted_bam_in_results_is_accepted(data, tmp_path):
    bam = data / "results" / "job1" / "x.bam"
    bam.write_bytes(b"BAM")
    assert resolve_shared_input(str(bam)) == bam


@pytest.mark.parametrize(
    "where",
    [
        "reference/hg38.fasta",  # the reference tree
        "temp/zarohla/other.bam",  # another service's working files
    ],
)
def test_files_elsewhere_on_the_volume_are_refused(data, where):
    target = data / where
    target.write_bytes(b"x")
    with pytest.raises(SharedInputRefused, match="outside the shared input"):
        resolve_shared_input(str(target))


def test_traversal_and_symlinks_out_of_the_roots_are_refused(data, tmp_path):
    secret = tmp_path / "secret.bam"
    secret.write_bytes(b"x")
    with pytest.raises(SharedInputRefused):
        resolve_shared_input(str(data / "uploads" / ".." / ".." / "secret.bam"))
    escape = data / "uploads" / "looks_fine.bam"
    escape.symlink_to(secret)
    with pytest.raises(SharedInputRefused, match="outside"):
        resolve_shared_input(str(escape))


def test_a_symlink_into_the_roots_is_followed(data, tmp_path):
    """Nextflow hands a process a symlink in its work dir; the real file counts."""
    bam = data / "uploads" / "u.bam"
    bam.write_bytes(b"BAM")
    staged = tmp_path / "work" / "u.bam"
    staged.parent.mkdir()
    staged.symlink_to(bam)
    assert resolve_shared_input(str(staged)) == bam


@pytest.mark.parametrize("path", ["", "/nonexistent/x.bam", "a\x00b"])
def test_nothing_usable_is_refused(data, path):
    with pytest.raises(SharedInputRefused):
        resolve_shared_input(path)


def test_a_directory_is_refused(data):
    with pytest.raises(SharedInputRefused, match="not a regular file"):
        resolve_shared_input(str(data / "results" / "job1"))


def test_a_current_index_is_copied_and_a_stale_one_is_not(data, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    bam = data / "results" / "job1" / "x.bam"
    bam.write_bytes(b"BAM")
    bai = data / "results" / "job1" / "x.bam.bai"
    bai.write_bytes(b"BAI")
    os.utime(bai, (bam.stat().st_mtime + 5, bam.stat().st_mtime + 5))

    link_shared_input(str(bam), work / "in.bam")
    copied = work / "in.bam.bai"
    assert copied.read_bytes() == b"BAI"
    assert copied.stat().st_mtime >= bam.stat().st_mtime  # current, as copied
    # A copy, not a link: a service that indexes anyway (pysam.index) writes
    # in.bam.bai, and through a link that would overwrite the shared index.
    assert not copied.is_symlink()
    copied.write_bytes(b"REWRITTEN")
    assert bai.read_bytes() == b"BAI"

    # Older than the BAM: htslib would read the wrong offsets, so leave it.
    os.utime(bai, (bam.stat().st_mtime - 60, bam.stat().st_mtime - 60))
    assert current_index(bam) is None
    link_shared_input(str(bam), work / "in2.bam")
    assert not (work / "in2.bam.bai").exists()


def test_both_index_spellings_and_cram_indexes_are_found(data):
    bam = data / "uploads" / "s.bam"
    bam.write_bytes(b"BAM")
    (data / "uploads" / "s.bai").write_bytes(b"BAI")
    assert current_index(bam) == (data / "uploads" / "s.bai", ".bai")
    cram = data / "uploads" / "s.cram"
    cram.write_bytes(b"CRAM")
    (data / "uploads" / "s.cram.crai").write_bytes(b"CRAI")
    assert current_index(cram)[1] == ".crai"
    assert current_index(Path(data / "uploads" / "nothing.sam")) is None
