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


def _is_pure_deletion(record: VcfRecord) -> bool:
    """True when nothing is inserted: ALT is exactly a prefix of REF.

    This is the shape bcftools norm emits for a plain deletion after
    left-alignment: an anchor base kept, the deleted base(s) dropped off the
    end of REF. m.960C>del and m.961T>del (ALT="") are plain deletions and
    can only match a record shaped like this.

    m.961T>del+Cn is not a plain deletion -- it also carries an inserted C,
    so a record that satisfies this check is never that allele. See
    match_alleles.
    """
    return len(record.ref) > len(record.alt) and record.ref.startswith(record.alt)


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
                if allele.alt == "":
                    # m.960C>del / m.961T>del: a plain deletion, nothing
                    # inserted. Only a pure-deletion record can name these --
                    # position alone is not enough, since m.961T>del and
                    # m.961T>del+Cn share a position (see the else branch).
                    if _is_pure_deletion(record) and allele.pos in _deleted_span(
                        record
                    ):
                        matched.append(name)
                        break
                else:
                    # m.961T>del+Cn: the T at 961 deleted *and* an extra C
                    # inserted into the adjoining poly-C tract -- a delins,
                    # not a pure deletion. bcftools norm's left-aligned shape
                    # for a compound variant inside a homopolymer run isn't
                    # something this module has a verified example of, and a
                    # repeat-tract insertion is exactly the kind of variant
                    # that can be represented more than one way depending on
                    # the normaliser. Guessing a matching rule risks naming
                    # this allele for a record that is really something
                    # else, so until a real normalised-VCF shape for this
                    # allele is confirmed, it matches nothing here rather
                    # than the wrong thing. Tracked: review round 1, finding
                    # 1 (2026-08-30).
                    continue
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


def has_unresolved_961_deletion(records: List[VcfRecord]) -> bool:
    """True when some record's deletion/delins footprint touches position 961.

    m.961T>del+Cn is a real, named MT-RNR1 allele that `match_alleles`
    deliberately never matches -- there is no verified normalised-VCF shape
    for that compound delins yet (see match_alleles's comment). So a carrier
    of only that variant produces the same `matched == []` as a carrier with
    nothing at 961 at all: `select_call` returns None either way, and the two
    are indistinguishable from `matched` alone.

    Anything that calls `select_call` and would otherwise promote an empty
    match straight to Reference must check this first. True here means "a
    deletion or delins sits on top of 961 and nothing named it" -- which
    could be the unresolvable m.961T>del+Cn -- so the honest result is a
    no-call, not a guess at Reference. A pure deletion that DOES resolve to
    m.960C>del or m.961T>del is already a match in `matched`, so `call` is
    never None for that case and this function is never consulted for it.
    """
    for record in records:
        if len(record.ref) <= len(record.alt):
            continue  # no deletion component -- cannot be what masks 961
        if 961 in _deleted_span(record):
            return True
    return False


# Reason codes for an MT-RNR1 result that stayed a no-call. Shared between the
# sidecar (docker/mtdna-server-2/app.py, which decides which applies) and the
# report (app/reports/generator.py's mtdna_report_context, which turns the
# code into reader-facing text) so the two never have to independently derive
# the same fact -- see "give every fact one home" in this branch's history.
NO_CALL_NO_CHRM_DATA = "no_chrm_data"
NO_CALL_NOT_CONSENTED = "absent_to_ref_not_set"
NO_CALL_UNRESOLVED_961_DELINS = "unresolved_961_delins"
NO_CALL_COVERAGE_BELOW_FLOOR = "coverage_below_floor"
NO_CALL_COVERAGE_UNKNOWN = "coverage_unknown"

# How a Reference call was established. This travels with the call because a
# measured Reference and an inferred one are different claims, and the reader
# is entitled to know which they are looking at -- the same reasoning that
# gives every gene a provenance letter rather than an unqualified diplotype.
#
# There is deliberately no "declared" basis yet: it belongs to Tier B (gVCF
# reference blocks), which is not built because gVCF uploads are refused
# upstream (file_processor.py:653). Adding the constant before the tier that
# produces it would be a value nothing can ever hold.
BASIS_MEASURED = "measured"
BASIS_INFERRED = "inferred"

# Tier D: the file carried mitochondrial data, but nothing established that
# MT-RNR1 itself was interrogated. Distinct from NO_CALL_NO_CHRM_DATA, which
# means the file carried none at all -- the two need different report copy
# because they suggest different remedies to the reader.
NO_CALL_REGION_NOT_COVERED = "region_not_covered"


class MtRnr1Call(NamedTuple):
    """The resolved call, why it was withheld, and how it was established.

    Exactly one of `call` / `no_call_reason` is set. `basis` is set only
    alongside a REFERENCE promotion -- a named allele needs no basis (the
    variant is the evidence), and a no-call has nothing to describe.
    """

    call: Optional[str]
    no_call_reason: Optional[str]
    basis: Optional[str]


def has_variant_in_gene(records: List[VcfRecord]) -> bool:
    """True when any record falls inside MT-RNR1's rCRS span.

    This is Tier C's first half. It works because rCRS is a phylogenetic
    outlier (an H2a2a1 sequence), so most of humanity differs from it at a
    near-fixed set of positions -- and two of them, 750 and 1438, land inside
    648-1601. Both samples this repo has run, one WGS and one exome, carry
    both. So "a variant inside the gene" is a gate almost every genuinely
    covered sample passes and almost every uncovered one fails.

    A deletion's anchor sits one base left of the first deleted base, so the
    window is widened by the record length rather than testing pos alone --
    the same rule _read_chrm_records uses when subsetting.
    """
    low, high = MT_RNR1_SPAN
    return any(
        record.pos + len(record.ref) >= low and record.pos <= high for record in records
    )


def resolve_mt_rnr1_call(
    matched: List[str],
    records: List[VcfRecord],
    *,
    evidence_reason: Optional[str],
    basis: Optional[str] = None,
) -> MtRnr1Call:
    """The final MT-RNR1 call, and -- when it stays a no-call -- why.

    This is the one place the "empty match -> Reference" promotion actually
    happens. Both `_call_from_vcf` and `_call_from_alignment` in the sidecar
    route their promotion decision through this function instead of each
    hand-rolling it, so their tests can no longer stay green on a substring
    match alone (e.g. "absent_to_ref" appearing anywhere in app.py) -- they
    exercise this function's real behaviour with real VcfRecord inputs
    instead (see test_mt_rnr1_vocabulary.py).

    `matched` is `match_alleles()`'s output. A real match always wins,
    regardless of `evidence_reason`: a caller who forgot to set
    pharmcat_absent_to_ref, or whose coverage was thin, does not get to erase
    a variant that is actually there.

    `evidence_reason` is the caller's own judgment of whether it has positive
    evidence this sample's mitochondrion was genuinely examined -- None means
    "yes"; otherwise it is the NO_CALL_* reason to report when `matched` is
    empty. An empty match is not itself evidence of anything: it is equally
    what "genuinely reference" and "never sequenced" look like from here,
    which is exactly why the caller, not this function, supplies that
    judgment (VCF path: a chrM contig header or a chrM record, gated on the
    user's own pharmcat_absent_to_ref consent; alignment path: mean coverage
    across the gene at or above MIN_MEAN_COVERAGE).

    Even with positive evidence, `has_unresolved_961_deletion` can still
    block the promotion: an empty match caused by m.961T>del+Cn's
    deliberately-unmatched shape must not read the same as a confirmed
    reference call.

    Returns (call, no_call_reason). Exactly one is falsy: `call` is set
    (a real allele name, or REFERENCE) when a match already existed or the
    promotion is allowed; `no_call_reason` is set to the concrete blocking
    reason otherwise.

    `basis` is how the caller established its evidence -- BASIS_MEASURED for
    a depth measurement over the gene, BASIS_INFERRED for Tier C's
    in-gene-variant-plus-clean-haplogroup inference. It is recorded on the
    promotion and dropped on every other path: a named allele carries its own
    evidence, and a no-call has nothing to describe.
    """
    call = select_call(matched)
    if call is not None:
        return MtRnr1Call(call, None, None)
    if evidence_reason is not None:
        return MtRnr1Call(None, evidence_reason, None)
    if has_unresolved_961_deletion(records):
        return MtRnr1Call(None, NO_CALL_UNRESOLVED_961_DELINS, None)
    return MtRnr1Call(REFERENCE, None, basis)
