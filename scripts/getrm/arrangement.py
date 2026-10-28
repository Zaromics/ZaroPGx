#!/usr/bin/env python3
"""Does the arrangement a PyPGx call asserts match the arrangement the reads show?

PyPGx phases with Beagle: statistical, from a reference panel, with no read evidence
behind it. A diplotype resting on two heterozygous variants asserts something the
reads can check without any phaser -- that the two sit on one chromosome or on two --
by counting the fragments that carry both positions.

This asks that of every call in a run's ``pypgx_result.json`` files, against the cuts
``cut.sh`` made, and prints the ones where the reads contradict the call.

Not only the calls PyPGx flags. ``alternative_phase`` names the readings PyPGx could
not rule out, and keying the check to it finds nothing: of the 34 flagged calls on the
61 GeT-RM genomes the reads decide 3, and all three confirm what is already printed. The
flag marks the calls PyPGx knows are uncertain, which are not the calls that are
wrong -- HG00589's CYP2D6 call is wrong and carries ``alternative_phase: null``.

Usage:
  scripts/getrm/arrangement.py ~/getrm/results ~/getrm/cuts
  scripts/getrm/arrangement.py ~/getrm/results ~/getrm/cuts --all   # every pair

What it found, 2026-10-27, on the 61 GeT-RM genomes (2x150, median fragment
415-431 bp): 719 pairs within 1 kb, 413 the reads decide, 411 of them agreeing with
the call.
The two that disagree are CYP2D6 ``*2/*21`` in HG00589, where 7 fragments to 0 put the
``*2`` core on the ``*21`` chromosome and so make the call GeT-RM's ``*1/*21``, and
CYP2F1 ``*1/*2`` in NA19026, a gene with no dosing guideline.

Two traps, both of which produce confident false findings:

  Indels count. A substitutions-only pass misses HG00589 entirely, because ``*21`` is
  an insertion.

  Two variants at one position are not a phasing question. UGT1A1 ``*28`` and ``*36``
  are an insertion and a deletion of the same TA repeat; treating "an indel is here"
  as evidence for both invents contradictions. The indel's own sequence is compared,
  and a pair no fragment could carry together is skipped.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import subprocess
import sys
from typing import Dict, List, Tuple

Variant = Tuple[str, int, str, str]

REACH = 1000  # bp apart: beyond this a 2x150 fragment rarely carries both
HET = (0.15, 0.85)  # allele fraction: outside this the site is not heterozygous
MIN_FRAGMENTS = 3
NOISE = 0.2  # the minority arrangement above this share means "no answer"


def parse_variant(text: str) -> Variant | None:
    """PyPGx writes a variant as chrom-position-ref-alt, with no chr prefix."""
    parts = str(text).split("-")
    if len(parts) != 4:
        return None
    chrom = parts[0] if parts[0].startswith("chr") else f"chr{parts[0]}"
    return (chrom, int(parts[1]), parts[2], parts[3])


def pairs_for(record: dict) -> List[dict]:
    """Every pair of heterozygous variants this call places, and where it puts them."""
    details = (record or {}).get("details") or {}
    hap1 = details.get("haplotype1") or []
    hap2 = details.get("haplotype2") or []
    defines: Dict[str, set] = {}
    fraction: Dict[Variant, float] = {}
    for supporting in details.get("supporting_variants") or []:
        found = set()
        for item in supporting.get("variants") or []:
            key = parse_variant(item.get("variant"))
            if key:
                found.add(key)
                fraction[key] = item.get("allele_fraction")
        defines[supporting["allele"]] = found

    side: Dict[Variant, set] = {}
    for number, haplotype in ((1, hap1), (2, hap2)):
        for allele in haplotype:
            for variant in defines.get(allele, ()):
                side.setdefault(variant, set()).add(number)

    het = [
        v for v in side if fraction.get(v) is not None and HET[0] < fraction[v] < HET[1]
    ]
    out = []
    for i in range(len(het)):
        for j in range(i + 1, len(het)):
            a, b = het[i], het[j]
            if a[0] != b[0] or not 0 < abs(a[1] - b[1]) <= REACH:
                continue
            if len(side[a]) != 1 or len(side[b]) != 1:
                continue  # on both haplotypes: the call asserts nothing about it
            out.append(
                {
                    "a": a,
                    "b": b,
                    "implied": "cis" if side[a] == side[b] else "trans",
                    "call": "/".join(["+".join(hap1), "+".join(hap2)]),
                }
            )
    return out


def pileup(
    cram: str, positions, docker: str | None, image: str | None, reference: str | None
) -> dict:
    """{(chrom, position): {read name: base or signed indel sequence}}.

    No -f: the cuts are encoded against GRCh38_full_analysis_set_plus_decoy, which
    REF_PATH decodes, and mpileup without a reference prints base letters rather than
    . and , -- which is what the comparison wants anyway.
    """
    bed = "\n".join(f"{c}\t{p - 1}\t{p}" for c, p in sorted(positions))
    shell = (
        f"printf '%s\\n' \"$BED\" > /tmp/arrangement.bed && "
        f"samtools mpileup -l /tmp/arrangement.bed --output-QNAME -q 20 -Q 13 "
        f"--no-output-ends '{cram}' 2>/dev/null"
    )
    if docker:
        command = ["docker", "exec", "-e", f"BED={bed}", docker, "bash", "-c", shell]
    elif image:
        # The cuts are CRAM against the 1000 Genomes analysis set, which samtools
        # decodes from the reference cache the genome downloader stages.
        command = [
            "docker",
            "run",
            "--rm",
            "-e",
            f"BED={bed}",
            "-v",
            f"{pathlib.Path(cram).parent}:{pathlib.Path(cram).parent}:ro",
        ]
        if reference:
            command += [
                "-v",
                f"{reference}:{reference}:ro",
                "-e",
                f"REF_PATH={reference}/ref_cache/%2s/%2s/%s",
            ]
        command += ["--entrypoint", "bash", image, "-c", shell]
    else:
        command = ["bash", "-c", shell]
    text = subprocess.run(
        command,
        capture_output=True,
        text=True,
        env={"BED": bed, "PATH": "/usr/bin:/bin"} if not (docker or image) else None,
        timeout=900,
    ).stdout

    seen = {}
    for line in text.splitlines():
        fields = line.split("\t")
        if len(fields) < 7:
            continue
        chrom, position, bases, names = (
            fields[0],
            int(fields[1]),
            fields[4],
            fields[6].split(","),
        )
        per_read, i = [], 0
        while i < len(bases):
            ch = bases[i]
            if ch in "ACGTNacgtn*#":
                i += 1
                m = re.match(r"([+-])(\d+)", bases[i:])
                if m:  # the read carries an indel at this base
                    length = int(m.group(2))
                    start = i + len(m.group(0))
                    per_read.append(m.group(1) + bases[start : start + length].upper())
                    i = start + length
                else:
                    per_read.append(ch.upper())
            elif ch == "^":
                i += 2  # mapping quality follows the marker
            else:
                i += 1
        if len(per_read) == len(names):
            seen[(chrom, position)] = dict(zip(names, per_read))
    return seen


def allele_of(call: str, variant: Variant) -> str | None:
    """Whether this read carries the variant, the reference, or something else.

    An indel must match the sequence the variant names: UGT1A1 *28 and *36 are an
    insertion and a deletion of the same repeat, and a read carrying one is not
    evidence for the other.
    """
    ref, alt = variant[2].upper(), variant[3].upper()
    if len(ref) == 1 and len(alt) == 1:
        return "alt" if call == alt else "ref" if call == ref else None
    if alt.startswith(ref):
        want = "+" + alt[len(ref) :]
    elif ref.startswith(alt):
        want = "-" + ref[len(alt) :]
    else:
        return None
    return "alt" if call == want else "ref" if call == ref[0] else None


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("results", help="the run's results, a directory per sample")
    parser.add_argument("cuts", help="the cuts cut.sh made")
    parser.add_argument(
        "--all",
        action="store_true",
        help="print every pair, not only the contradictions",
    )
    parser.add_argument("--docker", help="run samtools in this running container")
    parser.add_argument("--image", help="run samtools in a throwaway container")
    parser.add_argument(
        "--reference", help="the staged reference, for REF_PATH with --image"
    )
    parser.add_argument("--json", help="write every pair here")
    args = parser.parse_args()

    results = pathlib.Path(args.results).expanduser()
    cuts = pathlib.Path(args.cuts).expanduser()
    rows = []
    for sample_dir in sorted(d for d in results.iterdir() if d.is_dir()):
        record_file = sample_dir / "pypgx_result.json"
        cram = cuts / f"{sample_dir.name}.pgx.cram"
        if not record_file.is_file() or not cram.is_file():
            continue
        data = json.loads(record_file.read_text())
        cases = []
        for gene, record in (data.get("results") or {}).items():
            for case in pairs_for(record):
                cases.append({**case, "gene": gene})
        if not cases:
            continue
        positions = {(c["a"][0], c["a"][1]) for c in cases} | {
            (c["b"][0], c["b"][1]) for c in cases
        }
        seen = pileup(
            str(cram),
            positions,
            args.docker,
            args.image,
            args.reference and str(pathlib.Path(args.reference).expanduser().resolve()),
        )
        for case in cases:
            a, b = case["a"], case["b"]
            at_a = seen.get((a[0], a[1]), {})
            at_b = seen.get((b[0], b[1]), {})
            table = collections.Counter()
            for name in set(at_a) & set(at_b):
                x, y = allele_of(at_a[name], a), allele_of(at_b[name], b)
                if x and y:
                    table[(x, y)] += 1
            cis = table[("alt", "alt")] + table[("ref", "ref")]
            trans = table[("alt", "ref")] + table[("ref", "alt")]
            total = cis + trans
            if total < MIN_FRAGMENTS or min(cis, trans) / total > NOISE:
                shows = None
            else:
                shows = "cis" if cis > trans else "trans"
            rows.append(
                {
                    "sample": sample_dir.name,
                    "gene": case["gene"],
                    "call": case["call"],
                    "a": f"{a[0]}:{a[1]}",
                    "b": f"{b[0]}:{b[1]}",
                    "distance": abs(b[1] - a[1]),
                    "implied": case["implied"],
                    "cis": cis,
                    "trans": trans,
                    "shows": shows,
                }
            )

    decided = [r for r in rows if r["shows"]]
    against = [r for r in decided if r["shows"] != r["implied"]]
    for row in sorted(
        args.all and rows or against, key=lambda r: (r["gene"], r["sample"])
    ):
        print(
            f"{row['sample']:9} {row['gene']:9} {row['call']:22} "
            f"{row['distance']:>5} bp  call says {row['implied']:5} "
            f"reads say {str(row['shows']):5} cis={row['cis']} trans={row['trans']}"
        )
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(rows, indent=1))
    print(
        f"\n{len(rows)} pairs within {REACH} bp, {len(decided)} the reads decide, "
        f"{len(against)} the reads contradict",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
