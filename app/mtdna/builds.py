"""Which mitochondrial sequence a build actually carries.

| Build         | Contig | Sequence           | Length |
|---------------|--------|--------------------|--------|
| GRCh38/hg38   | chrM   | NC_012920 (rCRS)   | 16569  |
| GRCh37/b37    | MT     | NC_012920 (rCRS)   | 16569  |
| hg19          | chrM   | NC_001807 (Yoruba) | 16571  |

Note the row that breaks the pattern: b37 spells it the "old" way but carries
the *new* sequence. gatk_api.py's PLAIN_TO_CHR_CONTIGS maps MT -> chrM along
with 1 -> chr1 and the rest, and for the 26 other entries that rename is purely
cosmetic -- same sequence, same coordinates -- so rename-then-lift is right.
MT is the one contig where the spelling difference is also a sequence
difference, and it was added by pattern-completion with its neighbours.

This module exists so the mtDNA path decides for itself rather than inheriting
that. It reads the DETECTED build (from VCF header inspection), never the
reference_genome form field, which defaults to hg38 regardless of the upload.
"""

from __future__ import annotations

from enum import Enum
from typing import NamedTuple


class MitoBuild(Enum):
    GRCH38 = "grch38"
    B37 = "b37"
    HG19 = "hg19"
    UNSUPPORTED = "unsupported"


class BuildPlan(NamedTuple):
    rename_mt_to_chrm: bool
    needs_liftover: bool
    supported: bool
    reason: str = ""


def classify_build(reference_genome: str) -> MitoBuild:
    name = (reference_genome or "").strip().lower()
    if not name or name == "unknown":
        return MitoBuild.UNSUPPORTED
    if "38" in name:
        return MitoBuild.GRCH38
    # Order matters: "hg19" must be tested before the generic "37", because a
    # detector may report "hg19" and "GRCh37" for genuinely different files.
    if "hg19" in name:
        return MitoBuild.HG19
    if "grch37" in name or name == "b37" or "37" in name:
        return MitoBuild.B37
    return MitoBuild.UNSUPPORTED


_PLANS = {
    MitoBuild.GRCH38: BuildPlan(False, False, True),
    # Rename only. Coordinates are already rCRS; lifting would shift them.
    MitoBuild.B37: BuildPlan(True, False, True),
    MitoBuild.HG19: BuildPlan(False, True, True),
    MitoBuild.UNSUPPORTED: BuildPlan(
        False,
        False,
        False,
        "Mitochondrial calling needs a build whose chrM is rCRS or hg19's "
        "NC_001807. This file's build could not be matched to either.",
    ),
}


def plan_for(build: MitoBuild) -> BuildPlan:
    return _PLANS[build]
