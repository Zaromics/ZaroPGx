#!/usr/bin/env python3
"""Can short reads phase two variants, or would only a panel decide it?

PyPGx phases with Beagle: statistical, from a reference panel, with no read evidence
behind it. Whether read-backed phasing (WhatsHap) could do better for a gene is a
question about the reads, and this answers it from the cached GeT-RM cuts
(``cut.sh``) before anything is wired into the pipeline.

A fragment links two heterozygous sites when sequenced bases of either mate cover
both. Walk a region's heterozygous sites in order, break the chain wherever fewer
than ``--min-link`` fragments link one site to the next, and the blocks that remain
are what a read-backed phaser has to work with: two variants in one block can be
phased from the reads, two in different blocks cannot.

Usage:
  scripts/getrm/phase-blocks.py CRAM chr19:15879400-15897800 \\
      --reference reference/grch38/Homo_sapiens_assembly38.fasta \\
      --mark 15879621=CYP4F2 *4 --mark 15897578=CYP4F2 *4

  # samtools and bcftools from a container instead of PATH (the stack's gatk-api
  # image has both, with the reference at /reference):
  scripts/getrm/phase-blocks.py /tmp/x.cram chr22:42126000-42131500 \\
      --reference /reference/grch38/Homo_sapiens_assembly38.fasta --docker pgx_gatk_api

What it found, 2026-10-27, on the 1000 Genomes 30x cuts (2x150, median fragment
415-429 bp):

  CYP4F2, whose *4 is chr19:15,879,621 C>T and chr19:15,897,578 A>C on one
  chromosome and whose *2/*3 is the same two on opposite ones -- 17,957 bp apart.
  HG00436 has 55 heterozygous sites between them and NA19908 55, about one every
  330 bp, but both chains break: 6 gaps in HG00436 and 8 in NA19908 have no
  linking fragment at all, the widest 3,485 bp. The two variants land in different
  blocks in both samples, at one linking fragment as at two. No short-read phaser
  can decide CYP4F2 *4 against *2/*3; only a panel or long reads can.

  CYP2D6 in HG00589, called *2/*21 where GeT-RM says *1/*21. All three deciding
  positions are heterozygous: *2's cores at chr22:42,126,611 and chr22:42,127,941,
  and *21's 2580_2581insC at chr22:42,128,211. The last two are 270 bp apart and 8
  fragments carry both, so the reads say directly whether 2850C>T sits on the *21
  chromosome -- the question the call got wrong.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from typing import Dict, List, Tuple

CIGAR = re.compile(r"(\d+)([MIDNSHP=X])")
REF_CONSUMING = "MDN=X"
HET = {"0/1", "1/0", "0/2", "1/2"}


def run(command: List[str], docker: str | None) -> str:
    """Run a tool, in a container when one is named."""
    if docker:
        command = ["docker", "exec", docker] + command
    proc = subprocess.run(command, capture_output=True, text=True)
    if proc.returncode:
        sys.exit(f"{command[0]} failed: {proc.stderr.strip()[:400]}")
    return proc.stdout


def heterozygous_sites(cram: str, region: str, reference: str, docker: str | None):
    """The region's heterozygous sites, called from the alignment itself."""
    text = run(
        [
            "sh",
            "-c",
            f"bcftools mpileup -q 20 -Q 20 -r {region} -f {reference} {cram} 2>/dev/null"
            " | bcftools call -mv -Ov 2>/dev/null",
        ],
        docker,
    )
    sites = []
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        fields = line.split("\t")
        sample = dict(zip(fields[8].split(":"), fields[9].split(":")))
        if sample.get("GT", "").replace("|", "/") in HET:
            sites.append(int(fields[1]))
    return sites


def fragments(cram: str, region: str, docker: str | None) -> Dict[str, List[Tuple[int, int]]]:
    """{read name: the reference span of each of its mates}. Secondary and
    supplementary alignments and mapping quality under 20 are left out, as a phaser
    leaves them out."""
    text = run(
        ["sh", "-c", f"samtools view -q 20 -F 0x900 {cram} {region} 2>/dev/null"],
        docker,
    )
    spans: Dict[str, List[Tuple[int, int]]] = defaultdict(list)
    for line in text.splitlines():
        fields = line.split("\t")
        start = int(fields[3])
        length = sum(int(n) for n, op in CIGAR.findall(fields[5]) if op in REF_CONSUMING)
        if length:
            spans[fields[0]].append((start, start + length - 1))
    return spans


def covers(mates: List[Tuple[int, int]], position: int) -> bool:
    return any(start <= position <= end for start, end in mates)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cram", help="an alignment, indexed")
    parser.add_argument("region", help="chr:start-end")
    parser.add_argument("--reference", required=True, help="the FASTA it was aligned to")
    parser.add_argument("--docker", help="run samtools and bcftools in this container")
    parser.add_argument(
        "--min-link",
        type=int,
        default=1,
        help="fragments needed to link two sites (default 1, as WhatsHap counts)",
    )
    parser.add_argument(
        "--mark",
        action="append",
        default=[],
        metavar="POS=NAME",
        help="a position to report by name, repeatable",
    )
    args = parser.parse_args()
    if not args.docker and not (shutil.which("samtools") and shutil.which("bcftools")):
        sys.exit("samtools and bcftools are not on PATH; pass --docker CONTAINER")

    marks = dict(
        (int(m.split("=", 1)[0]), m.split("=", 1)[1]) for m in args.mark if "=" in m
    )
    sites = heterozygous_sites(args.cram, args.region, args.reference, args.docker)
    if not sites:
        sys.exit(f"no heterozygous site in {args.region}")
    reads = fragments(args.cram, args.region, args.docker)
    lengths = sorted(
        max(e for _, e in m) - min(s for s, _ in m) + 1
        for m in reads.values()
        if len(m) == 2
    )

    blocks: List[List[int]] = [[sites[0]]]
    breaks: List[Tuple[int, int, int]] = []
    for left, right in zip(sites, sites[1:]):
        linking = sum(1 for m in reads.values() if covers(m, left) and covers(m, right))
        if linking >= args.min_link:
            blocks[-1].append(right)
        else:
            blocks.append([right])
            breaks.append((left, right, linking))

    print(f"{args.region}: {len(reads)} fragments, {len(sites)} heterozygous sites")
    if lengths:
        print(
            f"  fragment length: median {lengths[len(lengths) // 2]} bp, "
            f"90th {lengths[int(len(lengths) * 0.9)]} bp, max {lengths[-1]} bp"
        )
    print(f"  {len(blocks)} phase block(s) at {args.min_link}+ linking fragment(s)")
    where = {}
    for number, block in enumerate(blocks, 1):
        named = [marks[p] for p in block if p in marks]
        where.update({p: number for p in block})
        print(
            f"    block {number}: {len(block):>3} sites, {block[0]}-{block[-1]} "
            f"({block[-1] - block[0]:>6} bp)" + (f"  <- {', '.join(named)}" if named else "")
        )
    if breaks:
        print("  breaks (neighbours no fragment links):")
        for left, right, linking in breaks:
            print(f"    {left} -> {right}  {right - left:>6} bp apart, {linking} fragment(s)")
    if len(marks) > 1:
        print("  marked positions:")
        for position, name in sorted(marks.items()):
            block = where.get(position)
            print(
                f"    {position} {name}: "
                + (f"block {block}" if block else "not heterozygous here")
            )
        ordered = sorted(marks)
        for i, left in enumerate(ordered):
            for right in ordered[i + 1 :]:
                linking = sum(
                    1 for m in reads.values() if covers(m, left) and covers(m, right)
                )
                print(
                    f"    {left} <-> {right}: {right - left} bp apart, "
                    f"{linking} fragment(s) carry both"
                )


if __name__ == "__main__":
    main()
