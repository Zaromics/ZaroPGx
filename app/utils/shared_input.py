"""Let a service read an input already on the shared /data volume, in place.

Every alignment the pipeline handles sat on the shared ./data volume already: the
upload in /data/uploads, a BAM converted from CRAM, SAM or FASTQ in /data/results.
Nextflow still posted it to each service as a multipart upload, and each stored a
full copy in its own temp tree: on 30x NA12878 the 41.2 GB upload plus a 41.2 GB
copy each in data/temp/mtdna and data/temp/zarohla at the same time, then PyPGx's.
Every service that reads alignments mounts ./data at /data, as the nextflow
container does, so a path under /data names the same file in all of them.

A service given ``shared_input`` links the file into its own working directory under
the name it would have stored an upload as, and works from the link exactly as it
worked from the stored copy: anything it writes beside the input (an index, say)
lands in its working directory, and removing that directory removes the link, never
the file. Nothing is ever written through the link. Only regular files under
SHARED_INPUT_ROOTS are accepted, after following every symlink, so a path cannot
reach the reference tree, another service's temp tree, or anything outside /data.

Copied into each service image beside job_client.py (/job-client/).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Iterable, Optional, Sequence, Tuple

# Where the stack keeps inputs: uploads, and the BAMs gatk-api converts or aligns.
# Overridable for tests and unusual layouts; colon-separated, like PATH.
DEFAULT_ROOTS: Sequence[str] = ("/data/uploads", "/data/results")

# An index beside the source is copied too when it is at least as new as the file,
# so a service need not index a whole-genome alignment again (PyPGx took 8 minutes
# to index NA12878's BAM). Copied, not linked: an index is megabytes, and a service
# that indexes anyway (pysam.index, samtools index) writes <input>.bai, which through
# a link would overwrite the shared file. A stale index would make htslib read the
# wrong offsets, so an older one is left behind and the service indexes as before.
_INDEX_SUFFIXES = {
    ".bam": (".bai", ".csi"),
    ".cram": (".crai",),
    ".vcf.gz": (".tbi", ".csi"),
}


class SharedInputRefused(ValueError):
    """The path does not name a file this service may read in place."""


def shared_input_roots() -> list:
    raw = os.environ.get("SHARED_INPUT_ROOTS")
    roots = raw.split(":") if raw else list(DEFAULT_ROOTS)
    return [Path(r).resolve() for r in roots if r]


def resolve_shared_input(path: str, roots: Optional[Iterable[Path]] = None) -> Path:
    """The real file behind ``path``; SharedInputRefused unless it is a regular file
    under one of the roots once every symlink is followed."""
    if not path or "\x00" in path:
        raise SharedInputRefused("no input path given")
    try:
        real = Path(path).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SharedInputRefused(f"{path} does not exist on the shared volume") from exc
    allowed = list(roots) if roots is not None else shared_input_roots()
    if not any(real == root or root in real.parents for root in allowed):
        raise SharedInputRefused(
            f"{path} is outside the shared input directories "
            f"({', '.join(str(r) for r in allowed)})"
        )
    if not real.is_file():
        raise SharedInputRefused(f"{path} is not a regular file")
    return real


def current_index(real: Path) -> Optional[Tuple[Path, str]]:
    """(index, its suffix) for an index beside ``real`` at least as new as it."""
    name = real.name.lower()
    for suffix, index_suffixes in _INDEX_SUFFIXES.items():
        if not name.endswith(suffix):
            continue
        stem = real.name[: -len(suffix)]
        for idx in index_suffixes:
            # samtools writes both spellings: x.bam.bai and x.bai.
            for candidate in (
                real.with_name(real.name + idx),
                real.with_name(stem + idx),
            ):
                if (
                    candidate.is_file()
                    and candidate.stat().st_mtime >= real.stat().st_mtime
                ):
                    return candidate, idx
    return None


def link_shared_input(
    path: str, dest: os.PathLike, roots: Optional[Iterable[Path]] = None
) -> Path:
    """Link the shared file ``path`` at ``dest``; copy a current index beside it.

    ``dest`` is where the service would have stored an upload; it must not exist.
    """
    real = resolve_shared_input(path, roots)
    dest = Path(dest)
    os.symlink(real, dest)
    index = current_index(real)
    if index is not None:
        shutil.copy2(index[0], Path(f"{dest}{index[1]}"))  # keeps its mtime
    return dest
