# app/pharmcat/amp_tier1.py
"""Whether a gene was tested well enough for its call to mean anything: the AMP/CAP
minimum allele sets, against the alleles PharmCAT says it could not assess.

THE PROBLEM THIS SOLVES, measured on this stack 2026-11-12. PharmCAT does not refuse a
gene it saw only a little of. Given a 9-position consumer-array VCF, the full
``pharmcat_pipeline`` -- preprocessor included, with NEITHER assume-reference flag, and
no PyPGx anywhere -- reported:

    CYP2C9   Normal Metabolizer, Activity Score 2.0     from 2 of 88 positions
    CYP2C19  Poor Metabolizer                           from 2 of 35 positions

It declined the 13 genes it had NO position for, but given any data at all it issues a
confident phenotype, and CYP2C9's carries 19 drug recommendations. A reference call is
exactly the call that absence can fake: ``*1`` means "no variant at any defining
position", which is only true if the positions were looked at.

THE STANDARD. The AMP Pharmacogenomics Working Group publishes, per gene, the minimum
set of alleles a clinical PGx assay should include. An allele is **Tier 1** when it
i) "has been well characterized and shown to significantly affect the function of the
protein and/or gene leading to an alteration in a drug response phenotype", ii) has "an
appreciable minor allele frequency in a population/ethnicity group", and iii) has
"publicly available reference materials" (CYP2C9 paper, verbatim). **Tier 2** meets at
least one but not all three. So "was this gene adequately tested" has a published,
citable answer, per gene, in the vocabulary clinical laboratories already use -- rather
than a coverage fraction or a confidence score this project would have to invent.

AMP's own motivating example is the bug above, and they name the harm: a patient
heterozygous for rs7900194 (``*8``, "common among African-Americans and Africans")
tested on an assay without that variant "may be assigned a genotype of *1/*1 (normal
function) rather than *1/*8, impacting the predicted phenotype and clinical treatment
strategy and potentially creating a health care disparity." rs7900194 is one of the
variants no consumer array carries (docs/user/file-formats.md).

WHY THIS IS NEARLY FREE. PharmCAT already reports, per gene, the alleles it could not
assess (``uncalledHaplotypes`` in report.json, ``uncallableHaplotypes`` in match.json --
the same list, spelled differently in the two files). So the only thing missing was the
Tier 1 table. ``uncalledHaplotypes`` ALONE is useless as a gate, measured: on PharmCAT's
own complete example VCF, 9 genes are called with some allele uncallable (CYP2C9 7,
CFTR 59, RYR1 6, TPMT 2, NUDT15 3, and others), because every gene has rare alleles that
need structural or copy-number evidence no VCF carries. Intersected with Tier 1 it
becomes exact: on that same complete input NO gene is missing a Tier 1 allele, so a
whole-genome upload keeps every verdict, while the array file loses every star-allele
verdict with the gap named.

NO SCORE. There is no number here on purpose. A "73% confidence" would be fabrication:
nothing in this pipeline is calibrated against outcomes, and a precise-looking figure
invites exactly the false trust this module exists to prevent. AMP's own words --
complete, or incomplete and which alleles are unassessed -- are what can be defended.

THE ALLELE NAMES ARE PHARMCAT'S, NOT AMP'S. Every string below was taken from a real
PharmCAT 3.4.0 ``uncallableHaplotypes`` list on this stack, not transcribed from the
papers: AMP writes DPYD's Tier 1 as ``c.1905+1G>A`` and PharmCAT writes
``c.1905+1G>A (*2A)``, and a table keyed on the paper's spelling would match nothing and
fail open -- every gene adequate, silently. tests/test_amp_tier1.py pins the spellings
against the fixtures.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, Iterable, Optional, Tuple

#: Where a Tier 1 list came from, and how sure we are of it. A list taken from a
#: secondary source is still usable -- it gates the same way -- but it is marked, so a
#: reader can tell which entries have been read in the paper and which have not. That
#: distinction earned itself: an early secondary summary of CYP2D6 omitted *41, and
#: reading the paper (PMC8579245) settled that it belongs in Tier 1.
#:
#: The three still marked SECONDARY -- CYP2C19, TPMT and NUDT15 -- agree across three
#: independent secondary sources each, and their primary texts were unreachable at every
#: route tried (jmdjournal.org and ScienceDirect answer 403; the institutional repository
#: copies answer a bot challenge). They are not guesses, but they have not been read in
#: the paper, and that is what this field says.
PRIMARY = "primary"
SECONDARY = "secondary"


@dataclass(frozen=True)
class Tier1Set:
    """One gene's AMP/CAP Tier 1 alleles, as PharmCAT spells them, plus its source."""

    alleles: FrozenSet[str]
    citation: str
    source: str


#: The AMP/CAP minimum ("Tier 1") allele sets, per gene.
#:
#: A gene ABSENT from this mapping is NOT gated and must not be treated as adequate --
#: see ``adequacy``, which reports ``assessed=False`` for it. AMP has not published a
#: minimum set for every gene PharmCAT calls (SLCO1B1, CFTR, G6PD, RYR1, NAT2, CYP2B6,
#: UGT1A1, F2, F5, ABCG2, IFNL3 and CACNA1S have none here), and silently passing those
#: would be the fail-open this module exists to avoid.
TIER1: Dict[str, Tier1Set] = {
    "CYP2C9": Tier1Set(
        # Read in the paper: "CYP2C9 variant alleles recommended as Tier 1 by the AMP
        # PGx Working Group include CYP2C9 *2, *3, *5, *6, *8, and *11."
        alleles=frozenset({"*2", "*3", "*5", "*6", "*8", "*11"}),
        citation=(
            "Pratt VM et al. Recommendations for Clinical CYP2C9 Genotyping Allele "
            "Selection: A Joint Recommendation of the Association for Molecular "
            "Pathology and College of American Pathologists. J Mol Diagn 2019."
        ),
        source=PRIMARY,
    ),
    "CYP2C19": Tier1Set(
        alleles=frozenset({"*2", "*3", "*17"}),
        citation=(
            "Pratt VM et al. Recommendations for Clinical CYP2C19 Genotyping Allele "
            "Selection: A Report of the Association for Molecular Pathology. "
            "J Mol Diagn 2018;20(3):269-276."
        ),
        source=SECONDARY,
    ),
    "CYP2D6": Tier1Set(
        # Read in the paper (PMC8579245): Tier 1 is "CYP2D6 *2 to *6, *9, *10, *17,
        # *29, and *41"; Tier 2 is "*7, *8, *12, *14, *15, *21, *31, *40, *42, *49,
        # *56, and *59" plus CYP2D6/CYP2D7 hybrid genes. INCLUDES *41 -- an earlier
        # secondary summary omitted it, which is the disagreement the source field
        # exists for, and reading the paper is what settled it.
        #
        # "Interrogation of CYP2D6 gene duplications/multiplications is included in
        # Tier 1" as well, and that CANNOT be checked here: a duplication is not an
        # allele in uncalledHaplotypes. So an adequate CYP2D6 by this table is adequate
        # on its SNV alleles only -- app/reports/copy_number.py is what says whether the
        # input could show copy number, and its caveat stands beside this one.
        alleles=frozenset(
            {"*2", "*3", "*4", "*5", "*6", "*9", "*10", "*17", "*29", "*41"}
        ),
        citation=(
            "Pratt VM et al. Recommendations for Clinical CYP2D6 Genotyping Allele "
            "Selection: A Joint Consensus Recommendation of the Association for "
            "Molecular Pathology, College of American Pathologists, Dutch "
            "Pharmacogenetics Working Group, and European Society for "
            "Pharmacogenomics and Personalized Therapy. J Mol Diagn "
            "2021;23(9):1047-1064. PMID 34118403, PMCID PMC8579245."
        ),
        source=PRIMARY,
    ),
    "CYP3A4": Tier1Set(
        alleles=frozenset({"*22"}),
        citation=(
            "Pratt VM et al. CYP3A4 and CYP3A5 Genotyping Recommendations. "
            "J Mol Diagn 2023;25(9):619-629. PMID 37419245."
        ),
        source=PRIMARY,
    ),
    "CYP3A5": Tier1Set(
        alleles=frozenset({"*3", "*6", "*7"}),
        citation=(
            "Pratt VM et al. CYP3A4 and CYP3A5 Genotyping Recommendations. "
            "J Mol Diagn 2023;25(9):619-629. PMID 37419245."
        ),
        source=PRIMARY,
    ),
    "TPMT": Tier1Set(
        alleles=frozenset({"*2", "*3A", "*3B", "*3C"}),
        citation=(
            "Pratt VM et al. TPMT and NUDT15 Genotyping Recommendations: A Joint "
            "Consensus Recommendation of AMP, CPIC, CAP, DPWG, ESPT and PharmGKB. "
            "J Mol Diagn 2022. NOTE: a 2026 CPIC thiopurine guideline update exists "
            "and may revise this set; re-read before relying on it."
        ),
        source=SECONDARY,
    ),
    "NUDT15": Tier1Set(
        # *3 ALONE. NUDT15*2 is Tier 2, not Tier 1 -- an easy and consequential
        # mistake, since *2 is the better known name.
        alleles=frozenset({"*3"}),
        citation=(
            "Pratt VM et al. TPMT and NUDT15 Genotyping Recommendations: A Joint "
            "Consensus Recommendation of AMP, CPIC, CAP, DPWG, ESPT and PharmGKB. "
            "J Mol Diagn 2022."
        ),
        source=SECONDARY,
    ),
    "DPYD": Tier1Set(
        # Seven variants, in PharmCAT's spelling. AMP writes them as bare c. notation;
        # PharmCAT appends the legacy star name to two of them and writes HapB3 as the
        # pair of variants that define it.
        alleles=frozenset(
            {
                "c.1905+1G>A (*2A)",
                "c.1679T>G (*13)",
                "c.1129-5923C>G, c.1236G>A (HapB3)",
                "c.557A>G",
                "c.868A>G",
                "c.2279C>T",
                "c.2846A>T",
            }
        ),
        citation=(
            "Pratt VM et al. DPYD Genotyping Recommendations: A Joint Consensus "
            "Recommendation of AMP, ACMG, CPIC, CAP, DPWG, ESPT, PharmGKB and "
            "PharmVar. J Mol Diagn 2024."
        ),
        source=PRIMARY,
    ),
    "CYP4F2": Tier1Set(
        # DELIBERATELY EMPTY, and not an oversight: AMP placed CYP4F2*3 in Tier 2
        # ("CYP4F2*3 was categorized as tier 2" -- its function "is not well defined"
        # and varies by ancestry) and nothing in Tier 1. So CYP4F2 has no minimum set
        # to fall short of, and this entry records that it was looked up.
        alleles=frozenset(),
        citation=(
            "Pratt VM et al. Recommendations for Clinical Warfarin Genotyping Allele "
            "Selection: A Report of the Association for Molecular Pathology and the "
            "College of American Pathologists. J Mol Diagn 2020;22(7):847-859. "
            "PMID 32380173."
        ),
        source=PRIMARY,
    ),
}

#: Genes whose whole allele definition is their Tier 1 variant, so they gate themselves:
#: PharmCAT cannot call them at all without it, and a call therefore proves it was read.
#: VKORC1's Tier 1 is the single promoter variant c.-1639G>A (rs9923231), which is also
#: the only position in PharmCAT's definition for the gene. Listed rather than given an
#: entry above because PharmCAT names VKORC1's haplotypes by rsID and genotype
#: ("rs9923231 variant (T)"), not by star allele, and a Tier 1 set written in star
#: notation would match nothing and fail open.
SELF_GATING = frozenset({"VKORC1"})


@dataclass(frozen=True)
class Adequacy:
    """Whether a gene met its AMP/CAP minimum allele set on this run.

    ``assessed`` False means no judgement was possible -- AMP publishes no minimum set
    for this gene -- and is NOT the same as adequate. Callers must branch on it, which
    is why it is a separate field rather than ``complete=True``.
    """

    gene: str
    assessed: bool
    complete: bool
    missing: Tuple[str, ...]
    citation: Optional[str] = None
    source: Optional[str] = None

    @property
    def inadequate(self) -> bool:
        """The one condition that withholds a call: judged, and found short."""
        return self.assessed and not self.complete


def adequacy(gene: Any, uncalled_haplotypes: Optional[Iterable[Any]]) -> Adequacy:
    """Whether ``gene``'s Tier 1 alleles were all assessable on this run.

    ``uncalled_haplotypes`` is PharmCAT's own list for the gene -- report.json's
    ``uncalledHaplotypes`` or match.json's ``uncallableHaplotypes``. None and empty are
    different only in that an empty list means "nothing was unassessable", which is the
    adequate answer; neither is treated as missing evidence, because PharmCAT emits the
    key for every gene it matched.

    Never raises: a gene name that is not a string, or a list holding things that are
    not strings, yields ``assessed=False``. Deciding adequacy must not be the thing that
    costs a report its gene rows.
    """
    symbol = str(gene or "").strip().upper()
    if symbol in SELF_GATING:
        # Its only defining position IS its Tier 1 variant, so a call proves the
        # variant was read. Judged, and complete, with nothing to list.
        return Adequacy(gene=symbol, assessed=True, complete=True, missing=())
    tier1 = TIER1.get(symbol)
    if tier1 is None:
        return Adequacy(gene=symbol, assessed=False, complete=False, missing=())
    names = {
        str(name).strip()
        for name in (uncalled_haplotypes or [])
        if str(name or "").strip()
    }
    missing = tuple(sorted(tier1.alleles & names, key=_allele_sort_key))
    return Adequacy(
        gene=symbol,
        assessed=True,
        complete=not missing,
        missing=missing,
        citation=tier1.citation,
        source=tier1.source,
    )


def _allele_sort_key(name: str) -> Tuple[int, str]:
    """``*2`` before ``*11`` before ``*3A``, and c. notation after star alleles.

    Plain string order puts ``*11`` before ``*2``, which reads as a mistake in a
    clinical sentence listing alleles.
    """
    text = str(name)
    if text.startswith("*"):
        rest = text[1:]
        digits = ""
        for char in rest:
            if not char.isdigit():
                break
            digits += char
        if digits:
            return (0, f"{int(digits):04d}{rest[len(digits):]}")
    return (1, text)


def statement(result: Adequacy) -> Optional[str]:
    """One sentence for the report, or None when there is nothing to say.

    Returns None for an adequate gene: a line on every row saying "nothing is wrong"
    is noise, and the report already states the positions it read. The sentence names
    the alleles rather than counting them, because "4 alleles unassessed" cannot be
    acted on and "*5, *6, *8 and *11 unassessed" can -- a clinician reading an
    African-ancestry patient's result knows immediately what was missed.
    """
    if not result.assessed:
        return None
    if result.complete:
        return None
    names = list(result.missing)
    if len(names) == 1:
        listed = names[0]
    else:
        # Semicolons when any allele's own NAME holds a comma, which DPYD's do:
        # "c.1129-5923C>G, c.1236G>A (HapB3)" is one allele, and comma-separating the
        # list around it reads as two -- so DPYD's seven looked like eight.
        joiner = "; " if any("," in name for name in names) else ", "
        listed = joiner.join(names[:-1]) + f" and {names[-1]}"
    return (
        f"Not called: this run could not assess {listed}, which the AMP/CAP minimum "
        f"allele set for {result.gene} requires. A reference call here would rest on "
        f"positions the data does not cover."
    )
