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
from typing import NamedTuple, Optional


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
    # Order matters: "hg19" must be tested before "38", because reference
    # filenames like "hg19_to_hg38_lifted.fasta" name both builds and contain
    # "38" as the liftover target -- the detected/uploaded build is hg19, and
    # that evidence must not be discarded in favour of the substring match on
    # the target. It must also be tested before the generic "37" check,
    # because a detector may report "hg19" and "GRCh37" for genuinely
    # different files, and "GRCh37" contains "37" but not "hg19".
    if "hg19" in name:
        return MitoBuild.HG19
    if "38" in name:
        return MitoBuild.GRCH38
    if "grch37" in name or name == "b37" or "37" in name:
        return MitoBuild.B37
    return MitoBuild.UNSUPPORTED


def classify_from_mito_contig(name: Optional[str], length: Optional[int]) -> MitoBuild:
    """Identify the mitochondrial sequence from a VCF's own ##contig header.

    Ground truth, and the reason it is preferred over the build label:
    file_processor._normalize_reference_genome maps "hg19" -> "GRCh37"
    (file_processor.py:611-612). That is right for the autosomal liftover --
    those coordinates are identical and only the naming differs -- and wrong
    for chrM, the single contig where hg19 and b37 carry different sequences.
    By the time a build label reaches us the distinction is already gone, so
    we read the length instead.

      16571 -> NC_001807, hg19's chrM  -> needs a real liftover
      16569 -> rCRS (NC_012920)        -> already in target coordinates;
                                          MT vs chrM then says b37 vs GRCh38
    """
    if length == 16571:
        return MitoBuild.HG19
    if length == 16569:
        contig = (name or "").strip().lower()
        if contig in ("mt", "m"):
            return MitoBuild.B37
        if contig == "chrm":
            return MitoBuild.GRCH38
        return MitoBuild.UNSUPPORTED
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
