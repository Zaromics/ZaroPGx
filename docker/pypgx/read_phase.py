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
    carry the same thing, so no arrangement is being asserted.
    """
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
            alleles = {a for a in (call.allele_indices or ()) if a}
            if not alleles or len(set(call.allele_indices or ())) < 2:
                continue  # homozygous, or a no-call: nothing is being arranged
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
                    if not HET[0] < fraction < HET[1]:
                        continue
                variant = Variant(
                    record.chrom,
                    record.pos,
                    record.ref.upper(),
                    str(record.alleles[index]).upper(),
                )
                for gene in genes:
                    found[gene].append(variant)
    return {gene: sorted(set(v)) for gene, v in found.items() if len(set(v)) > 1}


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


def check(
    alignment_path: Path,
    vcf_path: Path,
    gene_positions: Mapping[str, Sequence[Tuple[str, int, int]]],
    reference: Optional[Path] = None,
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
        wanted = pairs(by_gene)
        if not wanted:
            return {
                "status": "done",
                "pairs": [],
                "reason": "No two heterozygous defining variants lie within "
                f"{REACH} bp of each other.",
            }
        rows = count(Path(alignment_path), wanted, reference)
        decided = sum(1 for row in rows if row["shows"])
        logger.info(
            "Read phase: %d pairs within %d bp, %d the reads decide",
            len(rows),
            REACH,
            decided,
        )
        return {
            "status": "done",
            "reach": REACH,
            "min_fragments": MIN_FRAGMENTS,
            "pairs": rows,
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
