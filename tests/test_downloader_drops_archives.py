"""The genome downloader removes each archive once its FASTA is extracted and indexed.

The .fa.gz files and PharmCAT's reference tarball sat beside what they unpacked to,
3.7 GB that nothing reads (hg38 984 MB, hg19 949 MB, grch37 892 MB, the tarball
883 MB).
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

SOURCE = (
    Path(__file__).resolve().parent.parent
    / "docker"
    / "genome-downloader"
    / "downloader_api.py"
)


@pytest.fixture()
def downloader(tmp_path, monkeypatch):
    fake_tqdm = types.ModuleType("tqdm")
    fake_tqdm.tqdm = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "tqdm", fake_tqdm)
    spec = importlib.util.spec_from_file_location("zaropgx_downloader_archives", SOURCE)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    module.STATUS_FILE = str(tmp_path / "download_status.json")
    return module


def _genome(tmp_path, **flags):
    return {
        "name": "hg38",
        "gz_path": str(tmp_path / "g.fasta.gz"),
        "fasta_path": str(tmp_path / "g.fasta"),
        **flags,
    }


def test_an_archive_goes_once_its_fasta_is_indexed(downloader, tmp_path):
    g = _genome(tmp_path)
    Path(g["gz_path"]).write_bytes(b"gz")
    Path(g["fasta_path"]).write_text(">chr1\nA\n")
    downloader.discard_archive(g)
    assert Path(g["gz_path"]).exists(), "not indexed yet: keep it"
    Path(g["fasta_path"] + ".fai").write_text("chr1\t1\t6\t1\t2\n")
    downloader.discard_archive(g)
    assert not Path(g["gz_path"]).exists()
    assert Path(g["fasta_path"]).exists()


def test_a_file_downloaded_to_its_final_path_is_never_removed(downloader, tmp_path):
    for flag in ("is_vcf", "is_bed", "is_chain"):
        same = tmp_path / f"{flag}.dat"
        same.write_bytes(b"x")
        Path(f"{same}.fai").write_text("")
        downloader.discard_archive(
            {"name": "x", "gz_path": str(same), "fasta_path": str(same), flag: True}
        )
        assert same.exists(), flag


def test_the_pharmcat_tarball_goes_too(downloader, tmp_path):
    g = _genome(tmp_path, is_tar=True, gz_path=str(tmp_path / "ref.tar"))
    Path(g["gz_path"]).write_bytes(b"tar")
    Path(g["fasta_path"]).write_bytes(b"bgz")
    Path(g["fasta_path"] + ".fai").write_text("")  # shipped in the tarball
    downloader.discard_archive(g)
    assert not Path(g["gz_path"]).exists()


def _staged_tarball(downloader, tmp_path, monkeypatch):
    g = _genome(tmp_path, is_tar=True, gz_path=str(tmp_path / "ref.tar"))
    Path(g["gz_path"]).write_bytes(b"tar")
    Path(g["fasta_path"]).write_bytes(b"bgz")
    Path(g["fasta_path"] + ".fai").write_text("")
    monkeypatch.setattr(downloader, "GENOMES", [g])
    return g


def test_startup_sweeps_a_finished_install(downloader, tmp_path, monkeypatch):
    """An install staged before archives were removed still holds them."""
    g = _staged_tarball(downloader, tmp_path, monkeypatch)
    flag = tmp_path / ".download_complete"
    flag.write_text("Completed")
    downloader.sweep_archives(str(flag))
    assert not Path(g["gz_path"]).exists()


def test_startup_leaves_an_interrupted_extraction_its_archive(
    downloader, tmp_path, monkeypatch
):
    """The tarball unpacks in place, so a restart mid-extraction leaves a .fai that
    looks finished; download_genomes() discards it and extracts again from the
    archive. Swept at startup, the archive was gone first: 883 MB fetched again,
    or nothing at all offline."""
    g = _staged_tarball(downloader, tmp_path, monkeypatch)
    downloader.sweep_archives(str(tmp_path / ".download_complete"))
    assert Path(g["gz_path"]).exists()


def test_startup_runs_the_sweep():
    src = SOURCE.read_text(encoding="utf-8")
    assert "    sweep_archives()\n" in src[src.index('if __name__ == "__main__":') :]
