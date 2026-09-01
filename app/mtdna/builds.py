"""Which mitochondrial sequence a build actually carries.

| Build         | Contig | Sequence             | Length |
|---------------|--------|----------------------|--------|
| GRCh38/hg38   | chrM   | NC_012920 (rCRS)     | 16569  |
| GRCh37/b37    | MT     | NC_012920 (rCRS)     | 16569  |
| hg19          | chrM   | NC_001807 (Yoruba)   | 16571  |
| T2T-CHM13v2   | chrM   | rCRS **rotated 576** | 16569  |

Note the row that breaks the pattern: b37 spells it the "old" way but carries
the *new* sequence. gatk_api.py's PLAIN_TO_CHR_CONTIGS maps MT -> chrM along
with 1 -> chr1 and the rest, and for the 26 other entries that rename is purely
cosmetic -- same sequence, same coordinates -- so rename-then-lift is right.
MT is the one contig where the spelling difference is also a sequence
difference, and it was added by pattern-completion with its neighbours.

And note the row that breaks the *length*: T2T-CHM13v2 spells it chrM, carries
rCRS, and is 16569 bp -- indistinguishable from GRCh38 by both of the signals
this module reads -- yet its origin is rotated 576 bp (hs1 chrM:0 == GRCh38
chrM:576; verified against the UCSC sequence API and against hs1ToHg38.over.
chain.gz, which carries chrM as two blocks with a ~241 bp unmapped seam).
Treating it as GRCh38's would report MT-RNR1's m.1555A>G at the wrong position
entirely, so the build label is consulted for that one case -- see
classify_from_mito_contig.

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
    # A ##contig=<ID=chrM> line with no length= field. Distinct from
    # UNSUPPORTED (which means "no evidence at all", and is safe to hand to
    # classify_build as a last resort): here there IS a contig line, but
    # "chrM" alone is worn by both hg19 (16571 bp) and GRCh38 (16569 bp), and
    # guessing between them risks a shifted position inside MT-RNR1, not just
    # a missed call. See classify_from_mito_contig.
    AMBIGUOUS_CHRM = "ambiguous_chrm"
    # T2T-CHM13v2. Distinct from UNSUPPORTED ("no evidence at all", which the
    # callers answer by falling back to classify_build) for two reasons: this
    # one IS evidence and must not be overridden by a fallback, and the generic
    # UNSUPPORTED reason -- "could not be matched to rCRS or NC_001807" -- would
    # be false here. CHM13's chrM *is* rCRS; it is rotated.
    CHM13 = "chm13"


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


# Tokens that name T2T-CHM13 in a build label. Matched against a short label
# (header_inspector reports the canonical "T2T-CHM13v2"), not against a path, so
# the three-character "t2t" is a token here rather than a substring -- the same
# distinction file_processor.T2T_BUILD_TOKENS draws.
_CHM13_LABEL_TOKENS = ("chm13", "t2t")


def _label_names_chm13(build_label: Optional[str]) -> bool:
    return any(token in (build_label or "").lower() for token in _CHM13_LABEL_TOKENS)


def classify_from_mito_contig(
    name: Optional[str], length: Optional[int], build_label: Optional[str] = None
) -> MitoBuild:
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

    `build_label` is consulted ONLY for the one case where the length itself
    is missing and the name alone cannot resolve it (see below) -- GATK- and
    bcftools-written headers normally carry length=, but hand-built and
    third-party VCFs often don't, and falling through to classify_build(name)
    unconditionally in that case would silently reintroduce the exact bug
    this function exists to prevent: a real hg19 file, labelled "GRCh37" by
    file_processor's collapse, would take the rename-only (never lifted)
    plan.

    The one exception to "the length is ground truth" is the CHM13 guard below,
    and it is an exception because for CHM13 the length is not ground truth: its
    chrM is rCRS at 16569 bp, the same name and the same length GRCh38's carries,
    but rotated 576 bp (see the module docstring). Both signals this function
    reads therefore say GRCh38 about a file that is not GRCh38, and the label is
    the only evidence that disagrees -- so here, uniquely, the label wins.
    """
    if _label_names_chm13(build_label):
        return MitoBuild.CHM13
    if length == 16571:
        return MitoBuild.HG19
    if length == 16569:
        contig = (name or "").strip().lower()
        if contig in ("mt", "m"):
            return MitoBuild.B37
        if contig == "chrm":
            return MitoBuild.GRCH38
        return MitoBuild.UNSUPPORTED
    if length is None:
        contig = (name or "").strip().lower()
        if contig in ("mt", "m"):
            # Unambiguous even without a length: hg19 never spells it MT/M,
            # only chrM. b37's MT is already rCRS, so rename-only is correct
            # regardless of what the (missing) length would have said.
            return MitoBuild.B37
        if contig == "chrm":
            # Genuinely ambiguous: hg19's chrM is 16571 bp, GRCh38's is
            # 16569 bp, and "chrM" alone is spelled identically by both.
            # The label is safe to consult here, but only in the direction
            # that can't be wrong: nothing collapses INTO "38" -- the lossy
            # collapse file_processor performs is hg19 -> GRCh37, never the
            # reverse -- so a label that unambiguously says 38 really does
            # mean GRCh38. A label that says GRCh37/hg19/b37 (or nothing
            # usable) must NOT be guessed at: silently assuming GRCh38 there
            # would report m.1555A>G at 1553, and silently assuming hg19
            # would lift coordinates that were never hg19's to begin with.
            # Refusing (AMBIGUOUS_CHRM) is the honest outcome; the caller
            # turns that into a 422 telling the reader to re-header the file.
            if classify_build(build_label) == MitoBuild.GRCH38:
                return MitoBuild.GRCH38
            return MitoBuild.AMBIGUOUS_CHRM
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
    MitoBuild.AMBIGUOUS_CHRM: BuildPlan(
        False,
        False,
        False,
        "This VCF's chrM contig header carries no length (##contig=<ID=chrM> "
        "with no length= field), and hg19's chrM (16571 bp, NC_001807) cannot "
        "be told apart from rCRS (16569 bp, the sequence GRCh38's chrM and "
        "b37's MT both carry) without it. Calling anyway risks reporting "
        "variants at positions shifted by up to 2 bp inside MT-RNR1. "
        "Re-header the VCF with contig lengths "
        "(##contig=<ID=chrM,length=...>) and try again.",
    ),
    MitoBuild.CHM13: BuildPlan(
        False,
        False,
        False,
        "T2T-CHM13 files are not supported for mitochondrial calling. CHM13's "
        "chrM is rCRS rotated by 576 bp, so it is 16569 bp and named chrM like "
        "GRCh38's and is nonetheless in different coordinates: calling against "
        "it would report MT-RNR1's m.1555A>G at the wrong position rather than "
        "failing. This stack has no CHM13 chain, and the T2T chrM chain is a "
        "two-block alignment with a ~241 bp unmapped seam. Realign to "
        "GRCh38/hg38, or call your variants against GRCh38/hg38, and upload "
        "that instead.",
    ),
}


def plan_for(build: MitoBuild) -> BuildPlan:
    return _PLANS[build]
