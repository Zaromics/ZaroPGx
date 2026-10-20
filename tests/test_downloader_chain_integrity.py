"""genome-downloader checks the liftover chain against a pinned SHA-256, not TLS.

hgdownload.soe.ucsc.edu's certificate chains to emSign's G3 roots, which Mozilla
removed from NSS on 2026-10-20 after eMudhra revoked G3 intermediates for CA
compromise; certifi 2026.10.19 dropped them too, so the https download of
hg19ToHg38.over.chain.gz failed on every fresh build. The chain now comes over
http and is checked against the SHA-256 of the file UCSC has served unchanged since
2014. A download that does not match is discarded, with the reason in /status,
and a wrong file already on disk is removed and fetched again: no chain is better
than a wrong one.

`docker/genome-downloader/downloader_api.py` runs in its own image and imports
tqdm, which the dev venv does not ship, so the fixture stubs it.
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
# hg19ToHg38.over.chain.gz: 227,698 bytes, MD5 35887f73fe5e2231656504d1f6430900
# (UCSC's goldenPath/hg19/liftOver/md5sum.txt).
CHAIN_SHA256 = "5c0598e500ceb5a78c73086929e8ef993aec309bcafb595139b53d440b125a1d"


@pytest.fixture()
def downloader(tmp_path, monkeypatch):
    fake_tqdm = types.ModuleType("tqdm")
    fake_tqdm.tqdm = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "tqdm", fake_tqdm)
    spec = importlib.util.spec_from_file_location("zaropgx_downloader_chain", SOURCE)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    module.STATUS_FILE = str(tmp_path / "download_status.json")
    return module


class _Response:
    def __init__(self, body: bytes):
        self.body = body
        self.headers = {"content-length": str(len(body))}

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        for i in range(0, len(self.body), chunk_size):
            yield self.body[i : i + chunk_size]


def _serve(downloader, monkeypatch, body: bytes):
    monkeypatch.setattr(downloader.requests, "head", lambda *a, **k: _Response(body))
    monkeypatch.setattr(downloader.requests, "get", lambda *a, **k: _Response(body))


def _chain(downloader):
    (chain,) = [g for g in downloader.GENOMES if g["name"] == "hg19_to_hg38_chain"]
    return chain


def test_the_chain_is_pinned_and_not_trusted_to_tls(downloader):
    chain = _chain(downloader)
    assert chain["sha256"] == CHAIN_SHA256
    # The pin is the integrity check; https to UCSC would need a distrusted root.
    assert chain["url"] == (
        "http://hgdownload.soe.ucsc.edu/goldenPath/hg19/liftOver/"
        "hg19ToHg38.over.chain.gz"
    )


def test_only_the_chain_is_pinned(downloader):
    """The other entries are verified by TLS (certifi) or are not fixed content."""
    pinned = [g["name"] for g in downloader.GENOMES if g.get("sha256")]
    assert pinned == ["hg19_to_hg38_chain"]


def test_a_download_that_matches_its_hash_is_kept(downloader, tmp_path, monkeypatch):
    body = b"chain 1000 chr1 10 + 0 10 chr1 10 + 0 10 1\n10\n"
    _serve(downloader, monkeypatch, body)
    dest = tmp_path / "hg19ToHg38.over.chain.gz"

    ok = downloader.download_file(
        "http://x/c.gz",
        str(dest),
        "hg19_to_hg38_chain",
        sha256=hashlib.sha256(body).hexdigest(),
    )

    assert ok is True
    assert dest.read_bytes() == body
    assert not Path(f"{dest}.part").exists()


def test_a_download_that_does_not_match_is_discarded_and_says_why(
    downloader, tmp_path, monkeypatch
):
    _serve(downloader, monkeypatch, b"<html>a captive portal or a tampered file</html>")
    dest = tmp_path / "hg19ToHg38.over.chain.gz"

    ok = downloader.download_file(
        "http://x/c.gz", str(dest), "hg19_to_hg38_chain", sha256=CHAIN_SHA256
    )

    assert ok is False
    assert not dest.exists(), "no chain is better than a wrong one"
    assert not Path(f"{dest}.part").exists()
    entry = downloader.download_status["genomes"]["hg19_to_hg38_chain"]
    assert entry["status"] == "error"
    assert "SHA-256 mismatch" in entry["error"]
    assert CHAIN_SHA256 in entry["error"]
    on_disk = json.loads(Path(downloader.STATUS_FILE).read_text(encoding="utf-8"))
    assert "SHA-256 mismatch" in on_disk["genomes"]["hg19_to_hg38_chain"]["error"]


def test_a_wrong_file_already_on_disk_is_removed(downloader, tmp_path):
    path = tmp_path / "hg19ToHg38.over.chain.gz"
    path.write_bytes(b"truncated or tampered")
    genome = {**_chain(downloader), "gz_path": str(path), "fasta_path": str(path)}

    downloader.discard_unverified(genome)

    assert not path.exists()


def test_a_matching_file_already_on_disk_is_kept(downloader, tmp_path):
    body = b"a chain"
    path = tmp_path / "hg19ToHg38.over.chain.gz"
    path.write_bytes(body)
    genome = {
        "name": "hg19_to_hg38_chain",
        "gz_path": str(path),
        "fasta_path": str(path),
        "sha256": hashlib.sha256(body).hexdigest(),
    }

    downloader.discard_unverified(genome)

    assert path.read_bytes() == body


def _chain_only(downloader, tmp_path, monkeypatch):
    """GENOMES cut down to the chain, everything under tmp_path."""
    chain = {
        **_chain(downloader),
        "gz_path": str(tmp_path / "chain" / "hg19ToHg38.over.chain.gz"),
        "fasta_path": str(tmp_path / "chain" / "hg19ToHg38.over.chain.gz"),
    }
    monkeypatch.setattr(downloader, "GENOMES", [chain])
    monkeypatch.setattr(downloader, "REFERENCE_DIR", str(tmp_path))
    monkeypatch.setattr(
        downloader, "DOWNLOAD_COMPLETE_FLAG", str(tmp_path / ".download_complete")
    )
    monkeypatch.setattr(downloader, "REF_CACHE_ENABLED", False)
    return chain


def _pin_to(downloader, monkeypatch, body: bytes):
    """Pin the cut-down chain entry to `body`, a stand-in for the real file."""
    (chain,) = downloader.GENOMES
    monkeypatch.setitem(chain, "sha256", hashlib.sha256(body).hexdigest())


def test_a_wrong_chain_fails_the_run_and_says_why(downloader, tmp_path, monkeypatch):
    chain = _chain_only(downloader, tmp_path, monkeypatch)
    _serve(downloader, monkeypatch, b"<html>captive portal</html>")

    downloader.download_genomes()

    entry = downloader.download_status["genomes"]["hg19_to_hg38_chain"]
    assert entry["status"] == "error"
    assert "SHA-256 mismatch" in entry["error"]
    assert "the liftover chain was not installed" in entry["error"]
    assert "proxy or captive portal" in entry["error"]
    assert "sha256_verified" not in entry
    assert not Path(chain["gz_path"]).exists()
    assert not (tmp_path / ".download_complete").exists()
    assert downloader.download_status["completed"] is False
    assert not list((tmp_path / "chain").glob("*.part"))


def test_a_wrong_chain_on_disk_is_replaced_by_the_run(
    downloader, tmp_path, monkeypatch
):
    chain = _chain_only(downloader, tmp_path, monkeypatch)
    good = b"chain 1 chr1 10 + 0 10 chr1 10 + 0 10 1\n10\n"
    _pin_to(downloader, monkeypatch, good)
    Path(chain["gz_path"]).parent.mkdir(parents=True)
    Path(chain["gz_path"]).write_bytes(b"left by an earlier run")
    _serve(downloader, monkeypatch, good)

    downloader.download_genomes()

    assert Path(chain["gz_path"]).read_bytes() == good
    assert (tmp_path / ".download_complete").exists()
    entry = downloader.download_status["genomes"]["hg19_to_hg38_chain"]
    assert entry["status"] == "ready"
    assert entry["sha256_verified"] == hashlib.sha256(good).hexdigest()


def test_a_run_starts_without_the_last_runs_verified_hash(
    downloader, tmp_path, monkeypatch
):
    _chain_only(downloader, tmp_path, monkeypatch)
    entry = downloader.download_status["genomes"]["hg19_to_hg38_chain"]
    entry.update(status="ready", sha256_verified=CHAIN_SHA256)
    _serve(downloader, monkeypatch, b"wrong")

    downloader.download_genomes()

    assert entry["status"] == "error"
    assert "sha256_verified" not in entry, "/status must not say error and verified"


def test_one_run_at_a_time(downloader, tmp_path, monkeypatch):
    _chain_only(downloader, tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(
        downloader.requests, "get", lambda *a, **k: calls.append(a) or _Response(b"")
    )
    downloader._download_lock.acquire()
    try:
        downloader.download_genomes()
    finally:
        downloader._download_lock.release()

    assert calls == [], "a second run started beside the first"
    assert downloader.download_status["in_progress"] is False


def test_downloads_use_a_unique_part_file_and_stay_readable(
    downloader, tmp_path, monkeypatch
):
    body = b"x" * 3000
    seen = []
    real_mkstemp = downloader.tempfile.mkstemp

    def mkstemp(**kwargs):
        fd, path = real_mkstemp(**kwargs)
        seen.append(path)
        return fd, path

    monkeypatch.setattr(downloader.tempfile, "mkstemp", mkstemp)
    _serve(downloader, monkeypatch, body)
    dest = tmp_path / "hg19ToHg38.over.chain.gz"
    Path(f"{dest}.part").write_bytes(b"stale")  # the old fixed name, left by 0.4.0

    ok = downloader.download_file(
        "http://x/c.gz",
        str(dest),
        "hg19_to_hg38_chain",
        sha256=hashlib.sha256(body).hexdigest(),
    )

    assert ok is True
    (part,) = seen
    assert part != f"{dest}.part" and part.endswith(".part")
    assert (dest.stat().st_mode & 0o777) == 0o644, "the other services read it"
    assert not Path(f"{dest}.part").exists()
    assert not list(tmp_path.glob("*.part"))


def test_requests_carry_timeouts(downloader, tmp_path, monkeypatch):
    seen = []

    def call(*a, **k):
        seen.append(k.get("timeout"))
        return _Response(b"x")

    monkeypatch.setattr(downloader.requests, "head", call)
    monkeypatch.setattr(downloader.requests, "get", call)
    downloader.download_file(
        "http://x/c.gz", str(tmp_path / "c.gz"), "hg19_to_hg38_chain"
    )

    assert seen == [(30, 300), (30, 300)]


def test_startup_rechecks_the_chain_on_a_finished_install(
    downloader, tmp_path, monkeypatch
):
    chain = _chain_only(downloader, tmp_path, monkeypatch)
    Path(chain["gz_path"]).parent.mkdir(parents=True)
    Path(chain["gz_path"]).write_bytes(b"swapped after it was verified")
    (tmp_path / ".download_complete").write_text("Completed")
    downloader.download_status["completed"] = True
    downloader.download_status["genomes"]["hg19_to_hg38_chain"].update(
        status="ready", sha256_verified=CHAIN_SHA256
    )

    assert downloader.verify_staged_pins() is True

    assert not Path(chain["gz_path"]).exists()
    assert not (tmp_path / ".download_complete").exists(), "so a run fetches it again"
    assert downloader.download_status["completed"] is False
    entry = downloader.download_status["genomes"]["hg19_to_hg38_chain"]
    assert entry["status"] == "pending" and "sha256_verified" not in entry


def test_startup_keeps_a_good_chain_and_the_flag(downloader, tmp_path, monkeypatch):
    chain = _chain_only(downloader, tmp_path, monkeypatch)
    good = b"a good chain"
    _pin_to(downloader, monkeypatch, good)
    Path(chain["gz_path"]).parent.mkdir(parents=True)
    Path(chain["gz_path"]).write_bytes(good)
    (tmp_path / ".download_complete").write_text("Completed")

    assert downloader.verify_staged_pins() is False

    assert Path(chain["gz_path"]).exists()
    assert (tmp_path / ".download_complete").exists()


def test_startup_runs_the_check_and_schedules_a_run_when_it_removes_one():
    source = SOURCE.read_text(encoding="utf-8")
    main = source[source.index('if __name__ == "__main__":') :]
    assert main.index("verify_staged_pins()") < main.index("schedule_download(")
    assert "if repinned or " in main
