"""The index beside a BAM upload, made once for every step that reads the upload.

Each step reads a BAM upload in place and is handed the index beside it when there is
a current one (app/utils/shared_input.py). A BAM uploaded alone had none, so ZaroHLA
and PyPGx each indexed their own link to it, one after the other, and the read-phase
check, which builds no index because it reads a few hundred positions of a file tens of
gigabytes long, answered "not_run" for every BAM upload: no report made from a BAM said
anything about phase (measured 2026-11-04 on a cut of NA12878's BAM).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def index_bam(path: str | Path) -> Path:
    """Write ``<path>.bai`` and return its path; CalledProcessError when it cannot be.

    In a child process, as app/genome/contig_names.py indexes its copies: a 30x genome
    takes minutes, and in the app's process that would hold its GIL throughout. A BAM
    not sorted by coordinate cannot be indexed, and the partial index htslib leaves is
    removed, because a file beside the upload newer than it would be handed to every
    step as current.
    """
    bam = Path(path)
    index = bam.with_name(bam.name + ".bai")
    try:
        subprocess.run(
            [
                sys.executable,
                "-c",
                "import pysam, sys; pysam.index('-@', '4', sys.argv[1], sys.argv[2])",
                str(bam),
                str(index),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=True,
        )
    except subprocess.CalledProcessError:
        index.unlink(missing_ok=True)
        raise
    return index
