#!/usr/bin/env python3
"""For a discordant GeT-RM row, do the reads support our call or theirs?

``score.py`` says two calls differ. It cannot say which is right, and the headline
number mixes disagreements of very different kinds. This asks the reads, per row: it
collects every core variant of every allele either side names, measures each one in
that sample's cut, and reports the variants a side's diplotype leaves **unexplained**
and the ones it **requires but the reads do not show**. Both are unambiguous; the
dosage arithmetic in between is printed rather than judged.

Why this finds anything at all: for several genes the allele a caller prints for *an
empty chromosome* is a named star allele -- ``*3`` for SLC22A2, ``*7`` for CYP2E1,
``*1`` for CYP4F2 (each is its ``GRCh38Default`` in ``lexicon/gene-table.csv``, with
no core variants). GeT-RM's single-assay "nothing detected" is written the same way,
so a row where one side simply found more reads to account for reads as a miscall.

Usage:
  scripts/getrm/discordance_evidence.py CALLS.tsv CUTS_DIR [--gene CYP2E1] [--sample NA12878]

Over score-final's 43 discordances, 2026-11-15:

    OURS=16   our call accounts for the reads and GeT-RM's does not
    THEIRS=2  GeT-RM's does and ours does not
    arrangement=20  both account for every variant measured; only cis/trans differs
    both=5    each leaves something unexplained

So the 43 are mostly not miscalls, and the headline 0.9458 understates the pipeline.

What it found on ``score-final``'s 43 discordances, 2026-11-15 (the rows this
reproduces by hand):

  CYP2E1  HG00436  GeT-RM (*7/*7) vs ours *5/*5   -- their call leaves two HOMOZYGOUS
          variants unexplained (34/34 and 35/35 reads). Ours accounts for both.
  CYP4F2  NA18564  GeT-RM *3/*3 (firm) vs *3/*4   -- theirs leaves a het *2 variant
          unexplained at 26 of 45 reads. ``*4`` is defined as ``*2``+``*3`` in cis.
  SLC22A2 HG00276  GeT-RM *1/(*2) vs ours *2/*3   -- OURS leaves a homozygous *1
          variant unexplained, because ``*3`` means a chromosome with nothing on it.
  UGT1A1  HG00436  GeT-RM *27/(*28+*60) vs PyPGx *1/*80+*28 -- PyPGx's leaves *27
          unexplained at 19 of 35 reads. PharmCAT, which the report prints, has it.

Two things it deliberately does not do:

  **Indels are reported, not measured.** ``*28`` is a TA insertion and ``*36`` a
  deletion of the same repeat; "an indel is here" is evidence for neither on its own
  (the trap ``arrangement.py`` documents). Substitutions are measured; an indel core
  prints as ``indel`` and is left out of every verdict.

  **An arrangement is not a disagreement.** Where both sides name the same variants and
  differ only in how they sit across the two chromosomes -- GeT-RM's ``(*2)/*3`` against
  our ``*1/*4`` -- neither side leaves anything unexplained and the row prints as
  ``arrangement``. No pileup can settle those; ``read_phase.py`` is where that lives.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]

#: A called allele inside a diplotype: "(*2)" -> "*2", "*36x2" -> "*36", "*80+*28" splits.
ALLELE = re.compile(r"\*?[A-Za-z0-9]+")

#: AF below this is absent, above HOM_AF is homozygous, and a confident single copy
#: needs both ONE_COPY_AF and MIN_ALT_READS. Anything else is ``unclear`` and is kept
#: out of every verdict: the first pass of this script called NA06991's CYP2B6 row on
#: **3 alternate reads of 15** (AF 0.20), which is as likely to be noise as a variant,
#: and a tool that decides a validation disagreement on that is worse than no tool.
ABSENT_AF = 0.10
ONE_COPY_AF = 0.25
MIN_ALT_READS = 5
HOM_AF = 0.85

#: A position with fewer reads than this says nothing either way.
MIN_DEPTH = 8


def cores(gene: str) -> Dict[str, List[str]]:
    """{star allele: its GRCh38 core variants} for one gene, from the lexicon."""
    out: Dict[str, List[str]] = {}
    with (REPO / "lexicon" / "allele-table.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["Gene"] != gene:
                continue
            raw = (row.get("GRCh38Core") or "").strip()
            out[row["StarAllele"]] = (
                [] if raw in ("", "N/A") else [v.strip() for v in raw.split(",")]
            )
    return out


def alleles_in(diplotype: str) -> List[str]:
    """The star alleles a diplotype names, one entry per chromosome's worth.

    "*36 + *10/*36 + *10" -> ["*36", "*10", "*36", "*10"]; a copy-number suffix is
    dropped, since this asks which variants are claimed, not how many copies.
    """
    found: List[str] = []
    for part in re.split(r"[/+]", diplotype or ""):
        part = part.strip().strip("()").strip()
        part = re.sub(r"x\d+$", "", part)
        if not part:
            continue
        found.append(part if part.startswith("*") else f"*{part}")
    return found


def call_state(alt: int, depth: int) -> str:
    """What the reads say at one position: absent, one copy, hom, or unclear.

    Its own function so the thresholds can be tested without a CRAM. The `unclear`
    band is the correction this script needed: the first version called NA06991's
    CYP2B6 row on 3 alternate reads of 15 (AF 0.20), as likely noise as a variant.
    """
    if depth <= 0:
        return "unclear"
    fraction = alt / depth
    if fraction < ABSENT_AF:
        return "absent"
    if fraction > HOM_AF:
        return "hom"
    if fraction >= ONE_COPY_AF and alt >= MIN_ALT_READS:
        return "one copy"
    return "unclear"


def measure(cut: Path, variant: str) -> Optional[dict]:
    """Depth and alternate fraction for one ``chrom-pos-ref-alt`` core variant."""
    import pysam

    chrom, pos, ref, alt = variant.split("-", 3)
    if len(ref) != 1 or len(alt) != 1:
        return {"variant": variant, "indel": True}
    contig = f"chr{chrom}" if not chrom.startswith("chr") else chrom
    counts: Counter = Counter()
    with pysam.AlignmentFile(str(cut), "rc") as alignment:
        if contig not in alignment.references:
            return None
        for column in alignment.pileup(
            contig,
            int(pos) - 1,
            int(pos),
            truncate=True,
            min_base_quality=13,
            stepper="samtools",
        ):
            for read in column.pileups:
                if read.is_del or read.is_refskip or read.alignment.is_duplicate:
                    continue
                counts[read.alignment.query_sequence[read.query_position].upper()] += 1
    depth = sum(counts.values())
    if depth < MIN_DEPTH:
        return {"variant": variant, "depth": depth, "thin": True}
    found = counts.get(alt.upper(), 0)
    return {
        "variant": variant,
        "depth": depth,
        "alt": found,
        "af": found / depth,
        "state": call_state(found, depth),
    }


def _carried(side: List[str], variant: str, core: Dict[str, List[str]]) -> int:
    """How many of a side's named alleles require this variant."""
    return sum(1 for name in side if variant in core.get(name, []))


def judge(
    theirs: List[str], ours: List[str], seen: List[dict], core: Dict[str, List[str]]
) -> Tuple[List[str], List[str]]:
    """(what GeT-RM's call leaves unexplained, what ours does).

    A variant the reads show that no allele on a side requires is unexplained by that
    side. A variant a side requires that the reads call absent is a claim the reads do
    not support; it is reported in the same list, marked.
    """
    gaps: Dict[str, List[str]] = {"theirs": [], "ours": []}
    for label, side in (("theirs", theirs), ("ours", ours)):
        for row in seen:
            if row.get("indel") or row.get("thin") or row["state"] == "unclear":
                continue
            needed = _carried(side, row["variant"], core)
            if row["state"] in ("one copy", "hom") and needed == 0:
                gaps[label].append(
                    f"{row['variant']} present ({row['state']}, {row['alt']}/{row['depth']})"
                )
            elif row["state"] == "absent" and needed > 0:
                gaps[label].append(
                    f"{row['variant']} required but absent (0/{row['depth']})"
                )
    return gaps["theirs"], gaps["ours"]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("calls", type=Path, help="score.py's calls.tsv")
    parser.add_argument("cuts", type=Path, help="the directory cut.sh wrote")
    parser.add_argument("--gene", help="only this gene")
    parser.add_argument("--sample", help="only this sample")
    parser.add_argument(
        "--reference",
        default="/home/iliya/ZaroPGx/reference",
        help="the staged reference, for REF_PATH (the cuts are CRAM)",
    )
    args = parser.parse_args()

    os.environ.setdefault("REF_PATH", f"{args.reference}/ref_cache/%2s/%2s/%s")
    os.environ.setdefault("REF_CACHE", f"{args.reference}/ref_cache/%2s/%2s/%s")

    with args.calls.open(encoding="utf-8") as handle:
        rows = [
            row
            for row in csv.DictReader(handle, delimiter="\t")
            if row.get("verdict") == "discordant"
            and (not args.gene or row["gene"] == args.gene)
            and (not args.sample or row["sample"] == args.sample)
        ]
    if not rows:
        print("no discordant rows matched", file=sys.stderr)
        return 1

    tally: Counter = Counter()
    for row in rows:
        cut = args.cuts / f"{row['sample']}.pgx.cram"
        if not cut.is_file():
            print(f"{row['sample']} {row['gene']}: no cut at {cut}")
            continue
        core = cores(row["gene"])
        theirs, ours = alleles_in(row["getrm"]), alleles_in(row["zaropgx"])
        unknown = sorted({n for n in theirs + ours if n not in core})
        wanted: List[str] = []
        for name in theirs + ours:
            for variant in core.get(name, []):
                if variant not in wanted:
                    wanted.append(variant)

        seen = [m for m in (measure(cut, v) for v in wanted) if m]
        their_gaps, our_gaps = judge(theirs, ours, seen, core)

        if their_gaps and not our_gaps:
            verdict, note = "OURS", "their call leaves the reads unexplained"
        elif our_gaps and not their_gaps:
            verdict, note = "THEIRS", "our call leaves the reads unexplained"
        elif not their_gaps and not our_gaps:
            verdict, note = "arrangement", "both account for every variant measured"
        else:
            verdict, note = "both", "each leaves something unexplained"
        tally[verdict] += 1

        print(
            f"\n{row['sample']} {row['gene']} ({row['caller']})"
            f"  GeT-RM {row['getrm']}  vs  ours {row['zaropgx']}"
            f"{'  [tentative]' if row.get('tentative') == 'yes' else ''}"
        )
        for m in seen:
            if m.get("indel"):
                print(f"    {m['variant']:28s} indel -- not measured")
            elif m.get("thin"):
                print(f"    {m['variant']:28s} only {m['depth']} reads -- says nothing")
            else:
                mark = "  (kept out of the verdict)" if m["state"] == "unclear" else ""
                print(
                    f"    {m['variant']:28s} {m['state']:9s} "
                    f"{m['alt']}/{m['depth']} AF={m['af']:.2f}{mark}"
                )
        if unknown:
            print(f"    alleles not in the lexicon: {', '.join(unknown)}")
        for label, gaps in (("GeT-RM", their_gaps), ("ours", our_gaps)):
            for gap in gaps:
                print(f"    {label} does not account for: {gap}")
        print(f"    -> {verdict}: {note}")

    print("\n" + "  ".join(f"{k}={v}" for k, v in sorted(tally.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
