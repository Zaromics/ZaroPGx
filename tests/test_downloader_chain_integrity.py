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


def test_the_loop_checks_a_pinned_file_before_it_trusts_it():
    """download_genomes() skips the download when the file exists; the check has
    to come first, and the download has to carry the pin."""
    source = SOURCE.read_text(encoding="utf-8")
    loop = source[source.index("def download_genomes") :]
    check = loop.index("discard_unverified(genome)")
    exists = loop.index('if not os.path.exists(genome["gz_path"]):')
    assert check < exists
    assert 'sha256=genome.get("sha256")' in loop[exists : exists + 300]
