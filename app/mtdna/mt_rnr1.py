"""MT-RNR1's allele vocabulary, as PharmCAT defines it.

PharmCAT cannot call MT-RNR1 itself: pharmcat_positions.vcf carries no chrM
position at all, which is why MT-RNR1 is one of the four genes listed under
config/genes.json categories.pharmcat_outside_callers. Something else has to
supply the call, and it has to be spelled PharmCAT's way.

The names here are copied from org/pharmgkb/pharmcat/phenotype/MT_RNR1.json
inside pharmcat.jar. tests/test_mt_rnr1_vocabulary.py pins them, so a PharmCAT
bump that renames an allele fails there rather than silently emitting a name
PharmCAT will reject.

MT-RNR1 is haploid. Every diplotypekey in that file is a single allele with
count 1, so the outside call is one name -- never a "A/B" diplotype form.
"""

from __future__ import annotations

from typing import Dict, List, NamedTuple, Optional

RISK_INCREASED = "increased"
RISK_UNCERTAIN = "uncertain"
RISK_NORMAL = "normal"

# Higher wins. A sample carrying both m.663A>G and m.1555A>G is reported as the
# one that changes prescribing, not the one that happens to come first.
_TIER_ORDER = {RISK_INCREASED: 3, RISK_UNCERTAIN: 2, RISK_NORMAL: 1}

# MT-RNR1 (12S rRNA) in rCRS coordinates. Every variant below falls inside it,
# so one region subset covers the whole vocabulary.
MT_RNR1_SPAN = (648, 1601)

REFERENCE = "Reference"


class VcfRecord(NamedTuple):
    """One normalised VCF record: bcftools norm -m-any -f <rCRS> output."""

    pos: int
    ref: str
    alt: str


class Allele(NamedTuple):
    pos: int
    ref: str
    alt: str
    risk: str
    #: True for the three names PharmCAT spells with ">del", which a VCF may
    #: represent several ways depending on left-alignment.
    is_deletion: bool = False


def _snv(pos: int, ref: str, alt: str, risk: str) -> Allele:
    return Allele(pos=pos, ref=ref, alt=alt, risk=risk)


MT_RNR1_ALLELES: Dict[str, Allele] = {
    REFERENCE: Allele(pos=0, ref="", alt="", risk=RISK_NORMAL),
    "m.663A>G": _snv(663, "A", "G", RISK_UNCERTAIN),
    "m.669T>C": _snv(669, "T", "C", RISK_UNCERTAIN),
    "m.747A>G": _snv(747, "A", "G", RISK_UNCERTAIN),
    "m.786G>A": _snv(786, "G", "A", RISK_UNCERTAIN),
    "m.807A>C": _snv(807, "A", "C", RISK_UNCERTAIN),
    "m.807A>G": _snv(807, "A", "G", RISK_UNCERTAIN),
    "m.827A>G": _snv(827, "A", "G", RISK_NORMAL),
    "m.839A>G": _snv(839, "A", "G", RISK_UNCERTAIN),
    "m.896A>G": _snv(896, "A", "G", RISK_UNCERTAIN),
    "m.930G>A": _snv(930, "G", "A", RISK_UNCERTAIN),
    "m.951G>A": _snv(951, "G", "A", RISK_UNCERTAIN),
    "m.960C>del": Allele(960, "C", "", RISK_UNCERTAIN, is_deletion=True),
    "m.961T>G": _snv(961, "T", "G", RISK_UNCERTAIN),
    "m.961T>del": Allele(961, "T", "", RISK_UNCERTAIN, is_deletion=True),
    "m.961T>del+Cn": Allele(961, "T", "C", RISK_UNCERTAIN, is_deletion=True),
    "m.988G>A": _snv(988, "G", "A", RISK_UNCERTAIN),
    "m.1095T>C": _snv(1095, "T", "C", RISK_INCREASED),
    "m.1189T>C": _snv(1189, "T", "C", RISK_UNCERTAIN),
    "m.1243T>C": _snv(1243, "T", "C", RISK_UNCERTAIN),
    "m.1494C>T": _snv(1494, "C", "T", RISK_INCREASED),
    "m.1520T>C": _snv(1520, "T", "C", RISK_UNCERTAIN),
    "m.1537C>T": _snv(1537, "C", "T", RISK_UNCERTAIN),
    "m.1555A>G": _snv(1555, "A", "G", RISK_INCREASED),
    "m.1556C>T": _snv(1556, "C", "T", RISK_UNCERTAIN),
}


def _is_single_base_deletion(record: VcfRecord) -> bool:
    return len(record.ref) == len(record.alt) + 1 and record.ref.startswith(record.alt)


def _deleted_span(record: VcfRecord) -> range:
    """Exactly the positions this deletion removes.

    bcftools norm left-aligns, so a deletion inside the 960-965 C homopolymer
    is emitted with an anchor base at the tract's left edge regardless of which
    C was actually lost. Accept any position the deletion spans rather than
    demanding an exact anchor, which would depend on the caller.

    No off-by-one slack on the end: widening this by one makes a single-base
    deletion span two positions, so a deletion at 960 would also match
    m.961T>del and m.961T>del+Cn -- three matches for one variant, and
    select_call could then name the wrong allele.
    """
    first_deleted = record.pos + len(record.alt)
    return range(first_deleted, first_deleted + (len(record.ref) - len(record.alt)))


def match_alleles(records: List[VcfRecord]) -> List[str]:
    """Every PharmCAT MT-RNR1 name these records support, lowest position first."""
    matched = []
    for name, allele in MT_RNR1_ALLELES.items():
        if name == REFERENCE:
            continue
        for record in records:
            if allele.is_deletion:
                if _is_single_base_deletion(record) and allele.pos in _deleted_span(
                    record
                ):
                    matched.append(name)
                    break
            elif (record.pos, record.ref, record.alt) == (
                allele.pos,
                allele.ref,
                allele.alt,
            ):
                matched.append(name)
                break
    return sorted(matched, key=lambda n: MT_RNR1_ALLELES[n].pos)


def select_call(names: List[str]) -> Optional[str]:
    """The single name to hand PharmCAT, or None when nothing matched.

    None is not "Reference": claiming normal risk of aminoglycoside-induced
    hearing loss on no evidence is exactly the dishonesty this feature exists
    to remove. Whether an empty match becomes Reference is a coverage question,
    decided by the caller (see call_mtdna in the sidecar).
    """
    if not names:
        return None
    return max(
        names,
        key=lambda n: (_TIER_ORDER[MT_RNR1_ALLELES[n].risk], -MT_RNR1_ALLELES[n].pos),
    )
