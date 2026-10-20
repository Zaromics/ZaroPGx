"""The pinned SHA-256 of UCSC's hg19ToHg38.over.chain.gz, and a cached check of it.

genome-downloader stages the chain on the shared /reference and checks it against
this pin when it downloads it (docker/genome-downloader/downloader_api.py carries
the same value, since its image builds from its own directory; a test keeps the
two equal). gatk-api's /liftover-vcf and the mtDNA service read the chain from that
mount, where anything could have replaced it since, so they check it again before
they use it. Dockerfile.gatk-api and the mtDNA Dockerfile copy this file in.

Hashing the 227 KB file takes about a millisecond; the result is still cached by
the file's mtime and size, so a request does not re-read an unchanged file.
"""

from __future__ import annotations

import hashlib
import os
import threading
from typing import Dict, Optional, Tuple

# 227,698 bytes, unchanged since 2014: see the hg19_to_hg38_chain entry in
# docker/genome-downloader/downloader_api.py for where it comes from.
CHAIN_SHA256 = "5c0598e500ceb5a78c73086929e8ef993aec309bcafb595139b53d440b125a1d"

_verified: Dict[Tuple[str, str], Tuple[int, int]] = {}
_lock = threading.Lock()


def chain_mismatch(path: str, expected: str = CHAIN_SHA256) -> Optional[str]:
    """None when the chain at `path` matches `expected`; otherwise the reason, to
    refuse with. The caller checks that the file exists first."""
    stat = os.stat(path)
    key = (path, expected)
    seen = (stat.st_mtime_ns, stat.st_size)
    with _lock:
        if _verified.get(key) == seen:
            return None
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    actual = digest.hexdigest()
    if actual != expected:
        with _lock:
            _verified.pop(key, None)
        return (
            f"The liftover chain at {path} does not match its pinned SHA-256 "
            f"(expected {expected}, got {actual}); delete it and restart "
            "genome-downloader, whose /status says whether it fetched a good copy."
        )
    with _lock:
        _verified[key] = seen
    return None
