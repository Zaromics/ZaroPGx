"""What the reads say about which chromosome each variant sits on.

PyPGx phases with Beagle: statistical, from a reference panel, with no read evidence
behind it. A diplotype resting on two heterozygous variants asserts something the
reads can check without any phaser -- that the two sit on one chromosome (cis) or on
two (trans) -- by counting the fragments that carry both positions.

This module answers only the read question, for every pair of heterozygous variants
close enough for a fragment to span: it does not know what any call asserts, and it
names no allele. The comparison is made where the call is, in the report
(app/reports/read_phase.py), which reads what this writes.

Measured on the 61 GeT-RM genomes: 719 pairs of heterozygous defining variants within
1 kb, 415 of which the reads decide, and 413 of those agreeing with the call PyPGx
printed. Beagle is right 99.5% of the time the reads can check it. The two it got
wrong were CYP2D6 ``*2/*21`` in HG00589, where 7 fragments to 0 put the ``*2`` cores
on the ``*21`` chromosome -- which makes that chromosome ``*21`` and the other
reference, GeT-RM's ``*1/*21`` -- and CYP2F1 ``*1/*2`` in NA19026, a gene with no
dosing guideline.

This module reads the reads directly. scripts/getrm/arrangement.py measured the same
question through ``samtools mpileup`` and found the same two contradictions, 413
decided rather than 415: mpileup drops reads around an indel that this keeps, and the
two extra pairs are both deletions it under-read. Where both decide a pair they never
disagree.

Two things the measurement had to be taught, both of which produce confident false
findings:

- **Indels count.** A substitutions-only pass misses HG00589 entirely, because its
  deciding variant is an insertion. So an indel is read from the read's own sequence,
  not inferred.
- **Two variants at one position are not a phasing question.** UGT1A1 ``*28`` and
  ``*36`` are an insertion and a deletion of the same TA repeat: they are competing
  alleles of one site, no fragment can carry both, and counting "an indel is here" as
  evidence for either invents contradictions. Pairs at one position are skipped, and
  an indel must match the sequence the variant names.

Only the positions PyPGx defines its star alleles at are read (its allele-table.csv
``GRCh38Core`` column, through ``pypgx_gene_positions``), which is what a diplotype's
arrangement rests on, and which keeps this to a few hundred positions rather than
every heterozygous site in a genome.
"""

from __future__ import annotations

import collections
import logging
import os
from pathlib import Path
from typing import Dict, List, Mapping, NamedTuple, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# bp apart: beyond this a 2x150 fragment rarely carries both positions. The GeT-RM
# genomes' fragments have a median of 415-431 bp, and the measurement found no pair
# beyond 1 kb that the reads decided.
REACH = 1000
# Allele fraction outside which the site is not heterozygous, so the call asserts
# nothing about an arrangement. Read from the VCF's AD when it carries one.
HET = (0.15, 0.85)
# Fewer than this many informative fragments is not an answer.
MIN_FRAGMENTS = 3
# The minority arrangement above this share of the fragments means the reads are
# mixed, which is not an answer either.
NOISE = 0.2
# A read placed less confidently than this, or a base called less confidently, is not
# evidence. samtools mpileup's own defaults for a genotyping pileup.
MIN_MAPQ = 20
MIN_BASEQ = 13


class Variant(NamedTuple):
    """A variant as PyPGx's tables name one, with the contig as the alignment has it."""

    chrom: str
    pos: int  # 1-based, as VCF and PyPGx write it
    ref: str
    alt: str

    def name(self) -> str:
        """PyPGx's own spelling, so the report can match this to a call's variants."""
        base = self.chrom[3:] if self.chrom.startswith("chr") else self.chrom
        return f"{base}-{self.pos}-{self.ref}-{self.alt}"


def _canonical(name: str) -> str:
    """A contig without UCSC's prefix: PyPGx's tables have none, alignments vary."""
    return name[3:] if name.startswith("chr") else name


def heterozygous(
    vcf_path: Path, gene_positions: Mapping[str, Sequence[Tuple[str, int, int]]]
) -> Dict[str, List[Variant]]:
    """Gene -> its heterozygous variants in this VCF, at the positions PyPGx defines
    star alleles at.

    A multi-allelic record contributes one variant per alternate allele the genotype
    carries. A record whose genotype is not heterozygous is left out: both chromosomes
    carry the same thing, so no arrangement is being asserted. Those records are not
    lost -- ``homozygous`` collects them, because naming still needs them.
    """
    return _defining(vcf_path, gene_positions, want="het")


def _defining(
    vcf_path: Path,
    gene_positions: Mapping[str, Sequence[Tuple[str, int, int]]],
    want: str,
) -> Dict[str, List[Variant]]:
    """The shared sweep behind ``heterozygous`` and ``homozygous``."""
    import pysam

    wanted: Dict[Tuple[str, int], List[str]] = collections.defaultdict(list)
    for gene, spans in gene_positions.items():
        for contig, start, _end in spans:
            wanted[(_canonical(contig), start + 1)].append(gene)

    found: Dict[str, List[Variant]] = collections.defaultdict(list)
    with pysam.VariantFile(str(vcf_path)) as vcf:
        if not vcf.header.samples:
            return {}
        sample = vcf.header.samples[0]
        for record in vcf:
            genes = wanted.get((_canonical(record.chrom), record.pos))
            if not genes:
                continue
            call = record.samples[sample]
            indices = {a for a in (call.allele_indices or ()) if a is not None}
            alleles = {a for a in indices if a}
            if not alleles:
                continue  # reference, half called at the reference, or a no-call
            if want == "het":
                # A half call (``1/.``) names one chromosome and leaves the other
                # unknown, which is not the same as carrying two different things.
                if len(indices) < 2:
                    continue
            elif len(indices) != 1:
                continue  # heterozygous, or half called: not on both chromosomes
            depths = call.get("AD")
            total = sum(d for d in depths if d is not None) if depths else 0
            for index in sorted(alleles):
                if index >= len(record.alleles):
                    continue
                if (
                    depths
                    and total
                    and index < len(depths)
                    and depths[index] is not None
                ):
                    fraction = depths[index] / total
                    if want == "het":
                        if not HET[0] < fraction < HET[1]:
                            continue
                    elif fraction < HET[1]:
                        # Called on both chromosomes but read on about half of them:
                        # more likely a heterozygote genotyped wrong than a variant
                        # to put on both sides of a name.
                        continue
                variant = Variant(
                    record.chrom,
                    record.pos,
                    record.ref.upper(),
                    str(record.alleles[index]).upper(),
                )
                for gene in genes:
                    found[gene].append(variant)
    least = 1 if want == "het" else 0
    return {gene: sorted(set(v)) for gene, v in found.items() if len(set(v)) > least}


def pairs(
    by_gene: Mapping[str, Sequence[Variant]],
) -> List[Tuple[str, Variant, Variant]]:
    """Every pair of heterozygous variants a fragment could span, by gene.

    Two variants at one position are not a phasing question (see the module
    docstring), so they are not paired.
    """
    out: List[Tuple[str, Variant, Variant]] = []
    for gene, variants in sorted(by_gene.items()):
        ordered = sorted(variants)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1 :]:
                if a.chrom != b.chrom:
                    continue
                if not 0 < abs(b.pos - a.pos) <= REACH:
                    continue
                out.append((gene, a, b))
    return out


def allele_in_read(read, variant: Variant) -> Optional[str]:
    """ "alt", "ref", or None for a read that is no evidence about this variant.

    The read's own sequence decides. A substitution is the base at the position; an
    insertion is the bases the read carries after it, which must be the sequence the
    variant names; a deletion is the reference positions the read skips. A read that
    does not cover the position, carries an indel of a different sequence there, or
    calls the base too poorly to count is not evidence.
    """
    try:
        aligned = read.get_aligned_pairs()
    except (ValueError, AttributeError):  # a read with no cigar
        return None
    sequence = read.query_sequence
    if not sequence:
        return None
    qualities = read.query_qualities

    # Where the variant's anchor base sits in the read, which reference positions
    # follow it (a deleted one is aligned to no query base, so its value is None), and
    # which query bases are inserted immediately after it.
    anchor = variant.pos - 1  # pysam counts reference positions from 0
    last = anchor + max(len(variant.ref), 1)
    at: Dict[int, Optional[int]] = {}
    after: List[int] = []
    seen_anchor = False
    past_anchor = False
    for query_position, reference_position in aligned:
        if reference_position is None:
            # An insertion: its bases come between the anchor and the next aligned
            # reference position, so only those count as following the anchor.
            if seen_anchor and not past_anchor and query_position is not None:
                after.append(query_position)
            continue
        at[reference_position] = query_position
        if reference_position == anchor:
            seen_anchor = True
        elif seen_anchor:
            past_anchor = True
            if reference_position > last:
                break

    if anchor not in at:
        return None

    # The base the variant is anchored on must be called confidently, whatever kind of
    # variant it is: an indel read off a quality-6 anchor is not evidence either. (It
    # is the one filter this had missed, and it cost a fragment against the
    # measurement's count before it was put back.)
    position = at[anchor]
    if position is None:
        return None  # the read deletes the anchor base: no evidence either way
    if qualities is not None and qualities[position] < MIN_BASEQ:
        return None

    ref, alt = variant.ref, variant.alt
    if len(ref) == 1 and len(alt) == 1:  # a substitution
        base = sequence[position].upper()
        return "alt" if base == alt else "ref" if base == ref else None

    if alt.startswith(ref):  # an insertion after the anchor base
        if not past_anchor:
            # Nothing aligned after the anchor, so what follows it is unknown. A read
            # that stops here is not evidence the insertion is absent, and a trailing
            # soft clip is aligned to no reference position exactly as an insertion
            # is -- an aligner clipping a read at an indel breakpoint would otherwise
            # be read as carrying the insertion.
            return None
        inserted = "".join(sequence[p].upper() for p in after)
        return "alt" if inserted == alt[len(ref) :] else "ref" if not inserted else None
    if ref.startswith(alt):  # a deletion of the bases after the anchor
        deleted = range(anchor + len(alt), anchor + len(ref))
        states = [at.get(p, "absent") for p in deleted]
        if any(state == "absent" for state in states):
            return None  # the read ends inside the deletion
        if all(state is None for state in states):
            return "alt"
        return "ref" if all(state is not None for state in states) else None
    return None  # a substitution of several bases at once: not read here


def fragments(alignment, variant: Variant) -> Dict[str, str]:
    """Fragment name -> the allele its reads agree on, for one variant.

    A fragment is a template, not a read: its mates share a name and a chromosome, so
    either mate covering the position is evidence about the same chromosome. Mates
    that disagree are dropped rather than guessed at.
    """
    out: Dict[str, str] = {}
    conflicted = set()
    reads = None
    # The VCF's contig names and the alignment's need not agree on UCSC's prefix: a
    # mismatch would otherwise read no reads and look like silence rather than an
    # error.
    for name in (
        variant.chrom,
        _canonical(variant.chrom),
        f"chr{_canonical(variant.chrom)}",
    ):
        try:
            reads = alignment.fetch(name, max(variant.pos - 1, 0), variant.pos)
            break
        except (ValueError, KeyError):
            continue
    if reads is None:
        return {}
    for read in reads:
        if read.is_unmapped or read.is_duplicate or read.is_qcfail:
            continue
        if read.is_secondary or read.is_supplementary:
            continue
        if (read.mapping_quality or 0) < MIN_MAPQ:
            continue
        allele = allele_in_read(read, variant)
        if allele is None:
            continue
        previous = out.get(read.query_name)
        if previous is not None and previous != allele:
            conflicted.add(read.query_name)
        out[read.query_name] = allele
    for name in conflicted:
        out.pop(name, None)
    return out


class NoIndex(RuntimeError):
    """The alignment cannot be read by position, so nothing can be counted."""


def count(
    alignment_path: Path,
    wanted: Sequence[Tuple[str, Variant, Variant]],
    reference: Optional[Path] = None,
) -> List[dict]:
    """One row per pair: how many fragments carry the two variants together (cis) and
    how many carry one each (trans), and what that decides."""
    import pysam

    rows: List[dict] = []
    options = {"reference_filename": str(reference)} if reference else {}
    with pysam.AlignmentFile(str(alignment_path), **options) as alignment:
        # Without an index every fetch raises and each pair would come back 0/0 --
        # silence that reads exactly like "the reads reach nothing", which is a
        # different and much weaker statement than "they were never looked at".
        if not alignment.has_index():
            # Not the file's name: a shared input is linked under a job-scoped hash,
            # and naming that tells a reader nothing.
            raise NoIndex("the alignment has no index beside it")
        cache: Dict[Variant, Dict[str, str]] = {}
        for gene, a, b in wanted:
            for variant in (a, b):
                if variant not in cache:
                    cache[variant] = fragments(alignment, variant)
            left, right = cache[a], cache[b]
            cis = trans = 0
            for name, first in left.items():
                second = right.get(name)
                if second is None:
                    continue
                # Both sites are heterozygous, so a fragment carrying the reference at
                # both is as much cis evidence as one carrying both variants: the two
                # variants are then together on the chromosome this fragment is not
                # from. Counting only variant-variant fragments throws away a third of
                # the evidence.
                if first == second:
                    cis += 1
                else:
                    trans += 1
            rows.append(
                {
                    "gene": gene,
                    "a": a.name(),
                    "b": b.name(),
                    "distance": abs(b.pos - a.pos),
                    "cis": cis,
                    "trans": trans,
                    "shows": verdict(cis, trans),
                }
            )
    return rows


def verdict(cis: int, trans: int) -> Optional[str]:
    """ "cis", "trans", or None where the reads do not decide.

    Silence is the common case and is not a finding: of the 719 pairs the measurement
    reached, 306 had too few informative fragments to say anything.
    """
    total = cis + trans
    if total < MIN_FRAGMENTS:
        return None
    if min(cis, trans) / total > NOISE:
        return None
    return "cis" if cis > trans else "trans"


def _allele_cores(gene: str, assembly: str) -> Dict[str, frozenset]:
    """Star allele -> the core variants PyPGx defines it by, for one gene.

    PyPGx's allele-table.csv, the same table the defining positions come from. An
    allele with no core variants is the reference one ("N/A"), which every haplotype
    would otherwise match.
    """
    import csv

    import pypgx

    data = Path(pypgx.__file__).parent / "api" / "data" / "allele-table.csv"
    out: Dict[str, frozenset] = {}
    with open(data, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            if row["Gene"].strip().upper() != gene.upper():
                continue
            raw = (row.get(f"{assembly}Core") or "").strip()
            out[row["StarAllele"].strip()] = frozenset(
                v for v in raw.split(",") if v and v != "N/A"
            )
    return out


def name_haplotype(gene: str, variants, assembly: str = "GRCh38") -> Optional[str]:
    """What PyPGx would call one chromosome carrying these variants.

    Every allele whose core variants are all present is a candidate, and PyPGx's own
    ``sort_alleles(by="priority")`` picks between them -- by allele function first,
    then the number of core variants. That is what makes a chromosome carrying both
    ``*2``'s cores and ``*21``'s core ``*21`` rather than ``*2``, and reproducing
    PyPGx's rule is the point: naming by a rule of our own would disagree with the
    caller somewhere it is right.
    """
    try:
        from pypgx.api import core
    except ImportError:  # pragma: no cover - PyPGx is always in this image
        return None
    table = _allele_cores(gene, assembly)
    if not table:
        return None
    have = frozenset(variants)
    fits = [a for a, need in table.items() if need and need <= have]
    if not fits:
        return core.get_default_allele(gene, assembly)
    return core.sort_alleles(fits, by="priority", gene=gene, assembly=assembly)[0]


# More than this many groups of variants to arrange is not worth enumerating: the
# answer would be a list too long to mean anything.
MAX_GROUPS = 6


def arrangements(variants: Sequence[Variant], decided: Sequence[dict]) -> List[tuple]:
    """Every split of these variants into two chromosomes the reads allow.

    A decided pair fixes two variants relative to each other, cis or trans; variants
    no decided pair links are free, so each group of linked variants can sit either
    way round and every combination is returned. Returns (side, side) pairs of
    variant-name sets.
    """
    names = [v.name() for v in variants]
    index = {name: i for i, name in enumerate(names)}
    # Union-find over the pairs the reads decide, carrying each member's side.
    parent = list(range(len(names)))
    flip = [False] * len(names)

    def find(i):
        root, inverted = i, False
        while parent[root] != root:
            inverted ^= flip[root]
            root = parent[root]
        return root, inverted

    for row in decided:
        a, b = index.get(str(row.get("a"))), index.get(str(row.get("b")))
        if a is None or b is None:
            continue
        ra, fa = find(a)
        rb, fb = find(b)
        if ra == rb:
            continue
        parent[rb] = ra
        flip[rb] = fa ^ fb ^ (row.get("shows") == "trans")

    groups: Dict[int, List[tuple]] = {}
    for i, name in enumerate(names):
        root, inverted = find(i)
        groups.setdefault(root, []).append((name, inverted))
    if len(groups) > MAX_GROUPS:
        return []

    out = []
    for mask in range(2 ** (len(groups) - 1) if groups else 0):
        left, right = set(), set()
        for position, members in enumerate(groups.values()):
            turned = bool(mask >> (position - 1) & 1) if position else False
            for name, inverted in members:
                (left if inverted ^ turned else right).add(name)
        out.append((frozenset(left), frozenset(right)))
    return out


# Genes where naming a haplotype from its own variants does NOT reproduce what PyPGx
# printed, measured over every haplotype in the 61 GeT-RM genomes: 2,434 of 2,497
# pairs came back identical, and the 63 that did not are almost all in these. Naming
# is not shown for them -- a name that disagrees with the caller is worse than none.
# CYP2D6 is kept deliberately: 53 of its 54 reproduced, and the one that did not
# (NA19908, named *43 where PyPGx printed *46) is a genome whose CYP2D6 has no pair
# of heterozygous variants within reach of a fragment, so the reads say nothing about
# it and nothing is named.
NAMING_UNFAITHFUL = frozenset(
    {"CYP1A2", "CYP2A6", "CYP2E1", "POR", "SLC22A2", "SLCO1B1"}
)


def reads_name(
    gene: str,
    variants: Sequence[Variant],
    rows: Sequence[dict],
    assembly: str,
    on_both: Sequence[Variant] = (),
) -> List[str]:
    """The diplotypes the reads allow for this gene, named as PyPGx names them.

    One entry when the reads settle the arrangement, several when variants they do
    not reach could sit either way, and none when there is nothing to say. The
    report prints these beside the call it questions, never instead of it.
    """
    decided = [r for r in rows if r.get("shows") in ("cis", "trans")]
    if not decided or gene.upper() in NAMING_UNFAITHFUL:
        return []
    return _named_arrangements(gene, variants, decided, assembly, on_both)


def _named_arrangements(
    gene: str,
    variants: Sequence[Variant],
    decided: Sequence[dict],
    assembly: str,
    on_both: Sequence[Variant],
) -> List[str]:
    """Each arrangement ``decided`` leaves open, named as PyPGx names it."""
    # A variant on both chromosomes belongs to both haplotypes. It is not arranged --
    # there is nothing to arrange -- but an allele whose core variants include it
    # could not fit either side without it, and would fall back to the default.
    both = {v.name() for v in on_both}
    out = set()
    for left, right in arrangements(variants, decided):
        first = name_haplotype(gene, set(left) | both, assembly)
        second = name_haplotype(gene, set(right) | both, assembly)
        if not first or not second:
            return []
        out.add(tuple(sorted((first, second))))
    return sorted("/".join(pair) for pair in out)


# Only the simplest unreachable case is worth naming: two heterozygous variants with
# nothing linking them, so there is exactly one alternative to the printed call.
# Measured over the 61 GeT-RM genomes, 314 of the 590 calls placing two or more
# heterozygous variants are this shape, and 288 of those name a *different* diplotype
# the other way round -- CYP2B6 *1/*6 or *4/*9, CYP2C19 *2/*38 or *35/*38. With three
# variants the list runs to four names and stops meaning anything.
MAX_UNREACHED_NAMES = 2


def unreached_names(
    gene: str,
    variants: Sequence[Variant],
    rows: Sequence[dict],
    assembly: str,
    on_both: Sequence[Variant] = (),
) -> List[str]:
    """The diplotypes left open where the reads reach nothing at all.

    ``reads_name`` speaks only where a pair is decided, which leaves the commonest
    case silent: two defining variants further apart than a fragment is long. Then
    the reads constrain nothing, the printed diplotype rests entirely on the phaser,
    and the other arrangement is a different call 92% of the time. 198 of those over
    the 61 genomes, among them CYP2C19, CYP2B6, DPYD and SLCO1B1.

    Returned only for the two-name case, so a reader is given one alternative rather
    than a list. Nothing is said where a pair *was* decided -- ``reads_name`` has the
    better answer there, built from evidence rather than from its absence.
    """
    if gene.upper() in NAMING_UNFAITHFUL:
        return []
    if any(r.get("shows") in ("cis", "trans") for r in rows):
        return []
    names = _named_arrangements(gene, variants, (), assembly, on_both)
    return names if len(names) == MAX_UNREACHED_NAMES else []


def phenotypes_for(gene: str, names: Sequence[str]) -> List[str]:
    """What PyPGx would call the phenotype of each diplotype the reads allow.

    A wrongly paired diplotype does not always mean a wrongly reported patient: the
    phenotype comes of the two alleles' functions, and HG00589's CYP2D6 is an
    Intermediate Metaboliser whether it is read as *2/*21, *1/*21 or *21/*39. Saying
    which of the two it is lets the report tell a reader whether anything downstream
    of the call would change.
    """
    try:
        from pypgx.api import core
    except ImportError:  # pragma: no cover - PyPGx is always in this image
        return []
    out = set()
    for diplotype in names:
        parts = diplotype.split("/")
        if len(parts) != 2:
            continue
        try:
            phenotype = core.predict_phenotype(gene, parts[0], parts[1])
        except Exception:  # a gene PyPGx has no phenotype rule for
            return []
        if phenotype:
            out.add(str(phenotype))
    return sorted(out)


def settled(names: Sequence[str]) -> List[str]:
    """The allele names every arrangement the reads allow agrees on.

    A variant no fragment reaches can sit either way, and the two readings can name
    the chromosome it sits on differently -- HG00589's CYP2D6 is *1/*21 one way and
    *39/*21 the other. What both agree on is the part the reads actually settle, and
    it is the only part worth putting in front of a reader.
    """
    if not names:
        return []
    common = None
    for diplotype in names:
        pair = collections.Counter(diplotype.split("/"))
        common = pair if common is None else (common & pair)
    return sorted((common or collections.Counter()).elements())


def homozygous(
    vcf_path: Path, gene_positions: Mapping[str, Sequence[Tuple[str, int, int]]]
) -> Dict[str, List[Variant]]:
    """The defining variants this sample carries on both chromosomes, by gene.

    These are not a phasing question -- there is nothing to arrange -- but a name is
    wrong without them: an allele whose core variants include a homozygous one could
    never fit either side, and every such haplotype would silently fall back to the
    gene's default (``*1``).
    """
    return _defining(vcf_path, gene_positions, want="hom")


def check(
    alignment_path: Path,
    vcf_path: Path,
    gene_positions: Mapping[str, Sequence[Tuple[str, int, int]]],
    reference: Optional[Path] = None,
    assembly: str = "GRCh38",
) -> dict:
    """The evidence file: ``{"status": ..., "pairs": [...]}``.

    Answers 200 whatever happens, like the unlisted-variant check: a run must not fail
    because an arrangement could not be read. "status" says whether it ran ("done"),
    could not ("not_run", with the reason) or failed ("failed").
    """
    try:
        if not Path(alignment_path).exists():
            return {
                "status": "not_run",
                "reason": "The alignment is not readable here.",
            }
        by_gene = heterozygous(Path(vcf_path), gene_positions)
        on_both = homozygous(Path(vcf_path), gene_positions)
        wanted = pairs(by_gene)
        if not wanted:
            return {
                "status": "done",
                "pairs": [],
                "reason": "No two heterozygous defining variants lie within "
                f"{REACH} bp of each other.",
            }
        try:
            rows = count(Path(alignment_path), wanted, reference)
        except NoIndex as exc:
            return {
                "status": "not_run",
                "reason": f"The reads could not be read by position: {exc}.",
            }
        decided = sum(1 for row in rows if row["shows"])
        # What the reads' own arrangement would be called, per gene. Several names
        # where variants the reads do not reach could sit either way: HG00589's
        # CYP2D6 is *1/*21 or *39/*21, both of which contradict the printed *2/*21
        # and agree that one chromosome is *21.
        names = {}
        for gene, variants in by_gene.items():
            gene_rows = [r for r in rows if r["gene"] == gene]
            try:
                allowed = reads_name(
                    gene, variants, gene_rows, assembly, on_both.get(gene, ())
                )
            except Exception as exc:  # naming is a bonus, the counts are the point
                logger.warning("Could not name %s from the reads: %s", gene, exc)
                continue
            if allowed:
                names[gene] = {
                    "allows": allowed,
                    "settles": settled(allowed),
                    "phenotypes": phenotypes_for(gene, allowed),
                }
                continue
            # Nothing decided for this gene: the arrangement is beyond a fragment's
            # reach, so name the one alternative the reads leave open instead.
            try:
                open_names = unreached_names(
                    gene, variants, gene_rows, assembly, on_both.get(gene, ())
                )
            except Exception as exc:  # naming is a bonus, the counts are the point
                logger.warning("Could not name %s's alternative: %s", gene, exc)
                continue
            if open_names:
                names[gene] = {
                    "open": open_names,
                    "phenotypes": phenotypes_for(gene, open_names),
                }
        logger.info(
            "Read phase: %d pairs within %d bp, %d the reads decide, %d genes named",
            len(rows),
            REACH,
            decided,
            len(names),
        )
        return {
            "status": "done",
            "reach": REACH,
            "min_fragments": MIN_FRAGMENTS,
            "pairs": rows,
            "names": names,
        }
    except Exception as exc:  # the evidence is worth less than the run
        logger.exception("Read-phase check failed")
        return {"status": "failed", "reason": f"The check failed: {exc}"[:500]}


def write(result: dict, path: Path) -> None:
    """The evidence beside the run's other outputs, for the report to read."""
    import json

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    with open(temporary, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, sort_keys=True)
    os.replace(temporary, path)
