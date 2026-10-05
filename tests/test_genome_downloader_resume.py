"""genome-downloader must pick up a download that a restart cut off, and not trust
what the cut-off run left on disk.

download_genomes() saves in_progress=True to /reference/download_status.json before
it starts and clears it only when it finishes. A process killed in between (a reboot,
`docker compose restart`, the OOM killer) leaves that flag behind, and the next process
used to load it back verbatim: schedule_download() then printed "Downloads already in
progress." and returned, and POST /start-download answered "already_running", so the
download never resumed and /status reported it as running forever.

Clearing the flag alone is not safe: the cut-off run wrote its download and its
extraction in place, and the loop skips any stage whose output file exists, so a
truncated .fa.gz or FASTA would be indexed and reported ready. Downloads and
extraction now go through a .part file, gunzip's exit status is checked, and output
from a stage the previous process died in is discarded.

`docker/genome-downloader/downloader_api.py` runs in its own image and imports tqdm,
which the dev venv does not ship, so the fixture stubs it and points STATUS_FILE at a
temp file. Everything else is the real module.
"""

import gzip
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SOURCE = (
    Path(__file__).resolve().parent.parent
    / "docker"
    / "genome-downloader"
    / "downloader_api.py"
)


@pytest.fixture()
def downloader(tmp_path, monkeypatch):
    """A fresh import per test: download_status is module-level state."""
    fake_tqdm = types.ModuleType("tqdm")
    fake_tqdm.tqdm = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "tqdm", fake_tqdm)

    spec = importlib.util.spec_from_file_location(
        "zaropgx_downloader_api_under_test", SOURCE
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    module.STATUS_FILE = str(tmp_path / "download_status.json")

    started = []
    monkeypatch.setattr(module, "download_genomes", lambda: started.append(True))
    module.started = started
    return module


def _save(module, **overrides):
    saved = json.loads(json.dumps(module.download_status))
    saved["genomes"]["hg38"].update(progress=40, status="downloading")
    saved.update(overrides)
    Path(module.STATUS_FILE).write_text(json.dumps(saved), encoding="utf-8")


def test_a_saved_in_progress_flag_does_not_survive_a_restart(downloader):
    _save(downloader, in_progress=True)

    downloader.load_saved_status()

    assert downloader.download_status["in_progress"] is False
    # Only the stale flag is dropped; the per-genome record is kept.
    assert downloader.download_status["genomes"]["hg38"]["progress"] == 40
    on_disk = json.loads(Path(downloader.STATUS_FILE).read_text(encoding="utf-8"))
    assert on_disk["in_progress"] is False, "/status reads the file, so it must agree"


def test_an_interrupted_download_can_be_started_again(downloader):
    _save(downloader, in_progress=True)
    downloader.load_saved_status()
    client = TestClient(downloader.app)

    assert client.get("/status").json()["in_progress"] is False
    assert client.post("/start-download").json() == {"status": "started"}


def test_a_completed_download_stays_completed(downloader):
    _save(downloader, in_progress=False, completed=True)
    downloader.load_saved_status()

    response = TestClient(downloader.app).post("/start-download")

    assert response.json() == {"status": "already_completed"}
    assert downloader.started == []


def test_an_unreadable_status_file_leaves_the_defaults(downloader):
    Path(downloader.STATUS_FILE).write_text("{not json", encoding="utf-8")

    downloader.load_saved_status()

    assert downloader.download_status["in_progress"] is False
    assert downloader.download_status["completed"] is False


def test_no_status_file_is_a_first_run(downloader):
    downloader.load_saved_status()

    assert not Path(downloader.STATUS_FILE).exists()
    assert downloader.download_status["completed"] is False


def test_a_status_file_missing_a_genome_keeps_its_default_entry(downloader):
    saved = json.loads(json.dumps(downloader.download_status))
    del saved["genomes"]["hg19_to_hg38_chain"]  # written before 0.3.1 added it
    saved["genomes"]["hg38"].update(progress=40, status="downloading")
    Path(downloader.STATUS_FILE).write_text(json.dumps(saved), encoding="utf-8")

    downloader.load_saved_status()

    genomes = downloader.download_status["genomes"]
    assert genomes["hg19_to_hg38_chain"]["status"] == "pending"
    assert genomes["hg38"]["status"] == "downloading"


def test_an_unwritable_status_file_does_not_stop_startup(downloader, monkeypatch):
    _save(downloader, in_progress=True)

    def refuse():
        raise PermissionError(13, "Permission denied", downloader.STATUS_FILE)

    monkeypatch.setattr(downloader, "save_status", refuse)

    downloader.load_saved_status()  # must not raise

    assert downloader.download_status["in_progress"] is False


class _Response:
    def __init__(self, chunks, fail_after=None):
        self.headers = {"content-length": str(sum(len(c) for c in chunks))}
        self._chunks, self._fail_after = chunks, fail_after

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        for i, chunk in enumerate(self._chunks):
            if i == self._fail_after:
                raise ConnectionError("connection reset mid-stream")
            yield chunk


def _fake_requests(response):
    return types.SimpleNamespace(
        head=lambda *a, **k: response, get=lambda *a, **k: response
    )


def test_a_download_cut_off_mid_stream_leaves_nothing_in_place(
    downloader, tmp_path, monkeypatch
):
    dest = tmp_path / "hg38.fa.gz"
    response = _Response([b"a" * 10, b"b" * 10], fail_after=1)
    monkeypatch.setattr(downloader, "requests", _fake_requests(response))

    assert downloader.download_file("https://x/hg38.fa.gz", str(dest), "hg38") is False
    assert not dest.exists(), "a partial download must not sit at the final path"
    assert not Path(str(dest) + ".part").exists()


def test_a_finished_download_lands_at_the_destination(
    downloader, tmp_path, monkeypatch
):
    dest = tmp_path / "hg38.fa.gz"
    response = _Response([b"a" * 10, b"b" * 10])
    monkeypatch.setattr(downloader, "requests", _fake_requests(response))

    assert downloader.download_file("https://x/hg38.fa.gz", str(dest), "hg38") is True
    assert dest.read_bytes() == b"a" * 10 + b"b" * 10
    assert not Path(str(dest) + ".part").exists()


def _gz(path, payload):
    path.write_bytes(gzip.compress(payload))
    return path


def test_a_truncated_archive_is_not_extracted(downloader, tmp_path):
    archive = _gz(tmp_path / "hg38.fa.gz", b">chr1\n" + b"ACGT" * 50000 + b"\n")
    archive.write_bytes(archive.read_bytes()[:-200])
    fasta = tmp_path / "hg38.fa"

    assert downloader.extract_file(str(archive), str(fasta), "hg38") is False
    assert not fasta.exists() and not Path(str(fasta) + ".part").exists()
    assert not archive.exists(), "a corrupt archive is removed so it is fetched again"


def test_trailing_garbage_still_extracts(downloader, tmp_path):
    # human_g1k_v37.fasta.gz ends in padding gzip warns about (exit 2) but
    # decompresses in full.
    payload = b">1\n" + b"ACGT" * 1000 + b"\n"
    archive = _gz(tmp_path / "g1k.fa.gz", payload)
    archive.write_bytes(archive.read_bytes() + b"\0" * 512)
    fasta = tmp_path / "g1k.fa"

    assert downloader.extract_file(str(archive), str(fasta), "grch37") is True
    assert fasta.read_bytes() == payload


def _genome(tmp_path, **flags):
    return {
        "name": "hg38",
        "gz_path": str(tmp_path / "g.fa.gz"),
        "fasta_path": str(tmp_path / "g.fa"),
        **flags,
    }


@pytest.mark.parametrize(
    "prior,discarded,kept",
    [
        ("downloading", ["g.fa.gz"], ["g.fa", "g.fa.fai"]),
        ("extracting", ["g.fa"], ["g.fa.gz", "g.fa.fai"]),
        ("indexing", ["g.fa.fai"], ["g.fa.gz", "g.fa"]),
        ("extracted", [], ["g.fa.gz", "g.fa", "g.fa.fai"]),
        ("ready", [], ["g.fa.gz", "g.fa", "g.fa.fai"]),
    ],
)
def test_output_of_an_interrupted_stage_is_discarded(
    downloader, tmp_path, prior, discarded, kept
):
    for name in ("g.fa.gz", "g.fa", "g.fa.fai"):
        (tmp_path / name).write_text("x")

    downloader.discard_interrupted_output(_genome(tmp_path), prior)

    assert [n for n in discarded if (tmp_path / n).exists()] == []
    assert [n for n in kept if not (tmp_path / n).exists()] == []


@pytest.mark.parametrize(
    "prior,discarded", [("downloading", True), ("error", True), ("ready", False)]
)
def test_a_small_file_short_of_ready_is_fetched_again(
    downloader, tmp_path, prior, discarded
):
    vcf = tmp_path / "pharmcat_positions.vcf"
    vcf.write_text("partial")
    genome = {
        "name": "pharmcat_positions",
        "gz_path": str(vcf),
        "fasta_path": str(vcf),
        "is_vcf": True,
    }

    downloader.discard_interrupted_output(genome, prior)

    assert vcf.exists() is not discarded
