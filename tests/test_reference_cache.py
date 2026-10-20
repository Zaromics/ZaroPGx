"""The genome downloader fills htslib's MD5-keyed reference cache.

A CRAM stores reads as differences from the exact reference sequences it was
written against, and records each one's MD5. compose.yml points htslib's REF_PATH
at /reference/ref_cache (%2s/%2s/%s), so a CRAM decodes when that cache holds its
sequences. The downloader puts two GRCh38 builds there: the UCSC hg38 it stages
for analysis, and the 1000 Genomes analysis set with decoys and HLA.

Checked outside these tests on real data: populate_ref_cache over the 1000
Genomes FASTA reproduces all 3,366 M5s in 30x NA12878's CRAM header, and
samtools 1.24 with only REF_PATH (no -T, no network) decodes that CRAM over
CYP2D6 to the same 2,397 records, byte for byte, as the BAM.
"""

import hashlib
import importlib.util
import json
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
    spec = importlib.util.spec_from_file_location("zaropgx_downloader_cache", SOURCE)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    module.STATUS_FILE = str(tmp_path / "download_status.json")
    module.DOWNLOAD_COMPLETE_FLAG = str(tmp_path / ".download_complete")
    module.REF_CACHE_DIR = str(tmp_path / "ref_cache")
    return module


def _m5(seq):
    return hashlib.md5(seq.upper().encode()).hexdigest()


def test_each_sequence_is_cached_as_htslib_reads_it(downloader, tmp_path):
    fasta = tmp_path / "ref.fa"
    # Wrapped, soft-masked lowercase and an IUPAC code: the M5 is over the bases
    # uppercased with line breaks removed, IUPAC codes kept (SAM spec, @SQ M5).
    fasta.write_text(">chr1 description\nacgtNNRY\nACGT\n>chrM\nGATTACA\n")
    cache = tmp_path / "ref_cache"

    names = downloader.populate_ref_cache(str(fasta), str(cache))

    assert names == {"chr1": _m5("ACGTNNRYACGT"), "chrM": _m5("GATTACA")}
    md5 = names["chr1"]
    # %2s/%2s/%s: each %2s consumes two characters, %s takes the remaining 28.
    stored = cache / md5[:2] / md5[2:4] / md5[4:]
    assert stored.read_bytes() == b"ACGTNNRYACGT"
    assert downloader.ref_cache_path(str(cache), md5) == str(stored)
    assert not list(cache.rglob("*.part"))


def test_a_sequence_two_builds_share_is_stored_once(downloader, tmp_path):
    (tmp_path / "a.fa").write_text(">chr1\nACGT\n>chr2\nTTTT\n")
    (tmp_path / "b.fa").write_text(">1\nACGT\n>chr2\nTTTA\n")
    cache = tmp_path / "ref_cache"
    downloader.populate_ref_cache(str(tmp_path / "a.fa"), str(cache))
    downloader.populate_ref_cache(str(tmp_path / "b.fa"), str(cache))
    files = sorted(p.read_bytes() for p in cache.rglob("*") if p.is_file())
    assert files == [b"ACGT", b"TTTA", b"TTTT"]


def _sources(downloader, monkeypatch, tmp_path):
    local = tmp_path / "hg38.fa"
    local.write_text(">chr1\nACGTACGT\n")
    remote = tmp_path / "sources" / "analysis_set.fa"
    sources = [
        {"name": "ref_cache_ucsc_hg38", "fasta_path": str(local)},
        {
            "name": "ref_cache_1kg_analysis_set",
            "url": "https://example.invalid/analysis_set.fa",
            "fasta_path": str(remote),
            "remove_after": True,
        },
    ]
    monkeypatch.setattr(downloader, "REF_CACHE_SOURCES", sources)
    return local, remote


def test_the_cache_stage_downloads_caches_and_removes_the_extra_build(
    downloader, monkeypatch, tmp_path
):
    local, remote = _sources(downloader, monkeypatch, tmp_path)
    fetched = []

    def download(url, dest, name, sha256=None, what=None):
        fetched.append(name)
        Path(dest).write_text(">chr1\nACGTACGA\n>HLA-A*01:01:01:01\nGGCC\n")
        return True

    monkeypatch.setattr(downloader, "download_file", download)

    assert downloader.populate_reference_cache() is True
    assert fetched == ["ref_cache_1kg_analysis_set"]
    assert not remote.exists()  # only its sequences are kept
    assert local.exists()  # the analysis FASTA stays
    cache = Path(downloader.REF_CACHE_DIR)
    assert (
        len([p for p in cache.rglob("*") if p.is_file() and not p.name.startswith(".")])
        == 3
    )
    status = downloader.download_status["genomes"]
    assert status["ref_cache_ucsc_hg38"]["status"] == "ready"
    assert status["ref_cache_1kg_analysis_set"]["status"] == "ready"
    assert downloader.ref_cache_pending() == []

    # A second run has nothing to do.
    assert downloader.populate_reference_cache() is True
    assert fetched == ["ref_cache_1kg_analysis_set"]


def test_a_failed_download_leaves_that_build_pending(downloader, monkeypatch, tmp_path):
    _sources(downloader, monkeypatch, tmp_path)
    monkeypatch.setattr(downloader, "download_file", lambda *a, **k: False)

    assert downloader.populate_reference_cache() is False
    assert [s["name"] for s in downloader.ref_cache_pending()] == [
        "ref_cache_1kg_analysis_set"
    ]


def test_an_install_with_its_genomes_already_staged_gets_the_cache(
    downloader, monkeypatch, tmp_path
):
    """.download_complete used to end the service's work for good, so a reference
    tree staged before the cache existed would never get one."""
    Path(downloader.DOWNLOAD_COMPLETE_FLAG).write_text("Completed")
    ran = []
    monkeypatch.setattr(
        downloader, "populate_reference_cache", lambda: ran.append(1) or True
    )
    monkeypatch.setattr(
        downloader, "download_genomes", lambda: pytest.fail("re-downloaded")
    )

    class Inline:
        def __init__(self, target):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(downloader.threading, "Thread", Inline)
    downloader.schedule_download(0)
    assert ran == [1]
    assert downloader.download_status["completed"] is True
    assert downloader.download_status["in_progress"] is False
    assert json.loads(Path(downloader.STATUS_FILE).read_text())["completed"] is True

    # With the cache built too, nothing runs.
    monkeypatch.setattr(downloader, "ref_cache_pending", lambda: [])
    downloader.schedule_download(0)
    assert ran == [1]


def test_a_first_install_is_in_progress_until_the_cache_is_built(
    downloader, monkeypatch, tmp_path
):
    """in_progress=False and completed=True were set before the cache stage, so
    /status called a first install finished through its 3.2 GB download."""
    seen = []

    def cache():
        seen.append(dict(downloader.download_status))
        return True

    monkeypatch.setattr(downloader, "populate_reference_cache", cache)
    downloader.download_status["in_progress"] = True  # as download_genomes sets it
    downloader.finish_download(True)
    assert [(s["in_progress"], s["completed"]) for s in seen] == [(True, False)]
    assert downloader.download_status["in_progress"] is False
    assert downloader.download_status["completed"] is True


def test_the_cache_can_be_switched_off(downloader, monkeypatch, tmp_path):
    """compose.e2e.yml: CI decodes no CRAM, so its runner skips the cache."""
    monkeypatch.setattr(downloader, "REF_CACHE_ENABLED", False)
    monkeypatch.setattr(
        downloader, "populate_reference_cache", lambda: pytest.fail("cached")
    )
    downloader.finish_download(True)
    assert downloader.download_status["completed"] is True
    # An install staged before the cache existed gets none either.
    monkeypatch.setattr(
        downloader.threading, "Thread", lambda target: pytest.fail("started")
    )
    downloader.schedule_download(0)
    assert downloader.download_status["completed"] is True


def test_a_build_left_behind_after_its_marker_is_removed(
    downloader, monkeypatch, tmp_path
):
    """The marker is written before the 3.2 GB FASTA is removed; a stop between
    the two left it for good, because the marker ends every later run early."""
    _, remote = _sources(downloader, monkeypatch, tmp_path)
    remote.parent.mkdir(parents=True, exist_ok=True)
    remote.write_text(">chr1\nACGT\n")
    for src in downloader.REF_CACHE_SOURCES:
        marker = Path(downloader.ref_cache_marker(src))
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("cached\n")
    assert downloader.populate_reference_cache() is True
    assert not remote.exists()
