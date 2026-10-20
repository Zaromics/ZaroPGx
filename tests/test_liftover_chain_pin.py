"""gatk-api and the mtDNA service check the liftover chain against its pin before use.

genome-downloader checks the chain when it downloads it, but only then: a run is
skipped once /reference/.download_complete exists, and the chain sits on a shared
mount. app/utils/liftover_chain.py holds the pin and a check cached by mtime and
size; both services copy it in and refuse a chain that fails it.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from app.utils import liftover_chain

ROOT = Path(__file__).resolve().parent.parent


def test_a_matching_chain_passes(tmp_path):
    chain = tmp_path / "chain.gz"
    chain.write_bytes(b"chain")
    assert (
        liftover_chain.chain_mismatch(str(chain), hashlib.sha256(b"chain").hexdigest())
        is None
    )


def test_a_tampered_chain_is_refused_with_what_to_do(tmp_path):
    chain = tmp_path / "chain.gz"
    chain.write_bytes(b"tampered")
    reason = liftover_chain.chain_mismatch(str(chain))
    assert "does not match its pinned SHA-256" in reason
    assert liftover_chain.CHAIN_SHA256 in reason
    assert "Restarting genome-downloader is enough" in reason


def test_the_check_is_cached_until_the_file_changes(tmp_path, monkeypatch):
    chain = tmp_path / "chain.gz"
    chain.write_bytes(b"chain")
    pin = hashlib.sha256(b"chain").hexdigest()
    reads = []
    real = liftover_chain.hashlib.sha256

    def counting():
        reads.append(1)
        return real()

    monkeypatch.setattr(liftover_chain.hashlib, "sha256", counting)
    assert liftover_chain.chain_mismatch(str(chain), pin) is None
    assert liftover_chain.chain_mismatch(str(chain), pin) is None
    assert len(reads) == 1, "an unchanged chain is not read again"

    chain.write_bytes(b"a different chain")
    os.utime(chain, ns=(1, 1))
    assert liftover_chain.chain_mismatch(str(chain), pin) is not None
    assert len(reads) == 2


def test_a_copy_that_keeps_size_and_mtime_is_checked_again(tmp_path):
    """cp -p / rsync -a keep the mtime; the cache is keyed on the file's identity."""
    chain = tmp_path / "chain.gz"
    chain.write_bytes(b"chain")
    pin = hashlib.sha256(b"chain").hexdigest()
    assert liftover_chain.chain_mismatch(str(chain), pin) is None
    before = chain.stat()
    other = tmp_path / "other.gz"
    other.write_bytes(b"CHAIN")  # same size
    os.utime(other, ns=(before.st_atime_ns, before.st_mtime_ns))
    os.replace(other, chain)
    assert liftover_chain.chain_mismatch(str(chain), pin) is not None


def test_a_chain_removed_under_the_check_is_refused_not_raised(tmp_path):
    """genome-downloader's startup check removes a chain that fails; a request that
    saw it a moment before gets a refusal, not a 500."""
    reason = liftover_chain.chain_mismatch(str(tmp_path / "gone.gz"))
    assert "could not be read" in reason
    assert "restart genome-downloader" in reason


def test_one_pin_for_the_downloader_gatk_api_and_mtdna():
    pin = liftover_chain.CHAIN_SHA256
    downloader = (ROOT / "docker/genome-downloader/downloader_api.py").read_text(
        encoding="utf-8"
    )
    assert f'"{pin}"' in downloader
    for dockerfile, dest in (
        ("docker/gatk-api/Dockerfile.gatk-api", "/app/"),
        ("docker/mtdna-server-2/Dockerfile", "/job-client/"),
    ):
        text = (ROOT / dockerfile).read_text(encoding="utf-8")
        assert f"COPY app/utils/liftover_chain.py {dest}" in text, dockerfile


def test_mtdna_checks_the_chain_before_it_lifts():
    app = (ROOT / "docker/mtdna-server-2/app.py").read_text(encoding="utf-8")
    exists = app.index("if not os.path.exists(CHAIN_PATH):")
    check = app.index("chain_mismatch(CHAIN_PATH, CHAIN_SHA256)")
    lift = app.index('"LiftoverVcf"')
    assert exists < check < lift
    assert "from liftover_chain import CHAIN_SHA256, chain_mismatch" in app
