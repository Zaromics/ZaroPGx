"""The pinned SHA-256 of UCSC's hg19ToHg38.over.chain.gz, and a cached check of it.

genome-downloader stages the chain on the shared /reference and checks it against
this pin when it downloads it (docker/genome-downloader/downloader_api.py carries
the same value, since its image builds from its own directory; a test keeps the
two equal). gatk-api's /liftover-vcf and the mtDNA service read the chain from that
mount, where anything could have replaced it since, so they check it again before
they use it. Dockerfile.gatk-api and the mtDNA Dockerfile copy this file in.

Hashing the 227 KB file takes about a millisecond; the result is still cached by
the file's identity (device, inode, size, mtime, ctime), so a request does not
re-read an unchanged file.
"""

from __future__ import annotations

import hashlib
import os
import threading
from typing import Dict, Optional, Tuple

# 227,698 bytes, unchanged since 2014: see the hg19_to_hg38_chain entry in
# docker/genome-downloader/downloader_api.py for where it comes from.
CHAIN_SHA256 = "5c0598e500ceb5a78c73086929e8ef993aec309bcafb595139b53d440b125a1d"

_verified: Dict[Tuple[str, str], Tuple[int, int, int, int, int]] = {}
_lock = threading.Lock()


def pinned_mismatch(path: str, expected: str, what: str) -> Optional[str]:
    """None when the file at `path` matches `expected`; otherwise the reason, to
    refuse with. Cached per file identity: device, inode, size, mtime and ctime, so
    a copy that keeps the mtime (cp -p, rsync -a) is still checked again."""
    key = (path, expected)
    try:
        with open(path, "rb") as handle:
            stat = os.fstat(handle.fileno())
            seen = (
                stat.st_dev,
                stat.st_ino,
                stat.st_size,
                stat.st_mtime_ns,
                stat.st_ctime_ns,
            )
            with _lock:
                if _verified.get(key) == seen:
                    return None
            digest = hashlib.sha256()
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as e:
        # Unlinked since the caller saw it (genome-downloader's startup check removes
        # one that fails), or unreadable: refused like a mismatch, not a 500.
        with _lock:
            _verified.pop(key, None)
        return (
            f"{what} at {path} could not be read ({e.strerror or e}); restart "
            "genome-downloader, which checks it at startup and fetches a good copy "
            "(its /status says whether it did)."
        )
    actual = digest.hexdigest()
    if actual != expected:
        with _lock:
            _verified.pop(key, None)
        return (
            f"{what} at {path} does not match its pinned SHA-256 (expected "
            f"{expected}, got {actual}). Restarting genome-downloader is enough: its "
            "startup check removes the file and fetches a good copy (its /status "
            "says whether it did)."
        )
    with _lock:
        _verified[key] = seen
    return None


def chain_mismatch(path: str, expected: str = CHAIN_SHA256) -> Optional[str]:
    """pinned_mismatch() for the liftover chain. The caller checks that the file
    exists first."""
    return pinned_mismatch(path, expected, "The liftover chain")
