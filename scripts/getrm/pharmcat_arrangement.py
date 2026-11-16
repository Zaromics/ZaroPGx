"""Does what a PharmCAT-printed call asserts about arrangement match the fragments?

    python scripts/getrm/pharmcat_arrangement.py RESULTS_DIR CUTS_DIR [--out DIR]

``arrangement.py`` asks this of PyPGx's calls, which is where the report asks it
(app/reports/read_phase.py marks only a PyPGx-printed call, because PyPGx's
arrangement is not PharmCAT's). This asks it of PharmCAT's own calls, to find out
whether that check is worth extending -- the answer being a measurement, not a guess.

A diplotype resting on two heterozygous defining variants asserts that the two sit on
one chromosome or on two. Which one, this reads out of PharmCAT's allele definitions:
the alt at each position belongs to whichever of the printed alleles is defined with
it, so two alts on the same allele are a cis assertion and one each a trans assertion.
The fragments are counted by the production counter (docker/pypgx/read_phase.py),
unchanged, so a disagreement here would be a disagreement there.

Writes pairs.tsv (every pair the reads reached), assertions.tsv (every pair the reads
decided, against what the call asserts) and by_gene.tsv (how far the check can see,
per gene). The last is the one that matters most: see scripts/getrm/phasing.md.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Optional, Tuple

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
#: Written by docker/pharmcat/definitions.py at container start, from the pinned jar.
DEFINITIONS = REPO / "data" / "pharmcat" / "allele-definitions.json.gz"


def _counter():
    """docker/pypgx/read_phase.py, imported by path -- docker/ is not a package."""
    path = REPO / "docker" / "pypgx" / "read_phase.py"
    spec = importlib.util.spec_from_file_location("pypgx_read_phase", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["pypgx_read_phase"] = module
    spec.loader.exec_module(module)
    return module


rp = _counter()


def het_variants(block, skipped: Counter):
    """The panel positions this sample is heterozygous at, as the counter's Variants.

    PharmCAT writes the genotype as it read it ("C/T"). A call it spells in a way this
    cannot turn into one reference and one alternate base is counted, not guessed at.
    """
    out = []
    for v in block.get("variants") or []:
        call, ref = (v.get("call") or "").strip(), (
            v.get("referenceAllele") or ""
        ).strip()
        if not call or "/" not in call or not ref:
            continue
        left, right = call.split("/", 1)
        if left == right:
            continue
        alts = [a for a in (left, right) if a != ref]
        if len(alts) != 1 or not alts[0] or not set(alts[0]) <= set("ACGT"):
            skipped["position_not_two_plain_alleles"] += 1
            continue
        out.append(rp.Variant(v["chromosome"], int(v["position"]), ref, alts[0]))
    return out


def printed(block) -> Optional[Tuple[str, str]]:
    """The one diplotype the report shows, or None where it shows several.

    PharmCAT enumerates every diplotype consistent with the data and discloses all of
    them; where it did, nothing is being asserted about arrangement and there is
    nothing to check.
    """
    source = block.get("sourceDiplotypes") or []
    if len(source) != 1:
        return None
    first, second = source[0].get("allele1") or {}, source[0].get("allele2") or {}
    if not first.get("name") or not second.get("name"):
        return None
    return first["name"], second["name"]


class Definitions:
    """PharmCAT's allele definitions, as definitions.py compacts them out of the jar."""

    def __init__(self, path: Path = DEFINITIONS):
        with gzip.open(path) as fh:
            loaded = json.load(fh)
        self.version = loaded.get("pharmcat_version")
        self.genes = loaded["genes"]

    def carries(
        self, gene: str, allele: str, chrom: str, pos: int, alt: str
    ) -> Optional[bool]:
        """Whether this allele is defined with `alt` here, or None where its
        definition does not speak to the position at all."""
        entry = self.genes.get(gene)
        if not entry:
            return None
        index = next(
            (
                i
                for i, locus in enumerate(entry["loci"])
                if locus["c"] == chrom and locus["p"] == pos
            ),
            None,
        )
        if index is None:
            return None
        allele_entry = next((a for a in entry["alleles"] if a["n"] == allele), None)
        if allele_entry is None:
            return None
        base = allele_entry["w"].get(str(index))
        return None if base is None else base == alt

    def side(
        self, gene: str, diplotype, chrom: str, pos: int, alt: str
    ) -> Optional[int]:
        """Which of the two printed alleles carries this alt: 1, 2, or None for
        neither or both -- in which case the call asserts nothing about it."""
        first, second = diplotype
        on_first = self.carries(gene, first, chrom, pos, alt)
        on_second = self.carries(gene, second, chrom, pos, alt)
        if on_first and not on_second:
            return 1
        if on_second and not on_first:
            return 2
        return None


def _parse(name: str):
    chrom, pos, _ref, alt = name.split("-", 3)
    return (chrom if chrom.startswith("chr") else "chr" + chrom), int(pos), alt


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "results", type=Path, help="one directory per sample, as run.py writes"
    )
    ap.add_argument("cuts", type=Path, help="<sample>.pgx.cram beside its index")
    ap.add_argument("--out", type=Path)
    ap.add_argument(
        "--definitions",
        type=Path,
        default=DEFINITIONS,
        help="allele-definitions.json.gz; the pharmcat container writes one into "
        "./data/pharmcat on first start, and a checkout that has never run the stack "
        "has none",
    )
    args = ap.parse_args()
    out_dir = args.out or args.results
    if not args.definitions.exists():
        ap.error(
            f"no allele definitions at {args.definitions} -- start the pharmcat "
            "container once, or pass --definitions"
        )
    defs = Definitions(args.definitions)

    totals, per_gene, pair_rows, assertion_rows = Counter(), Counter(), [], []
    for sample_dir in sorted(p for p in args.results.iterdir() if p.is_dir()):
        sample = sample_dir.name
        cram, report = args.cuts / f"{sample}.pgx.cram", sample_dir / "pharmcat.json"
        if not cram.exists() or not report.exists():
            totals["sample_without_reads_or_report"] += 1
            continue
        totals["samples"] += 1
        genes = json.load(open(report))["genes"]
        hets = {}
        for gene, block in genes.items():
            if block.get("callSource") != "MATCHER":
                continue
            found = het_variants(block, totals)
            if len(found) < 2:
                totals["calls_asserting_nothing"] += 1
                continue
            totals["calls_asserting_an_arrangement"] += 1
            per_gene[(gene, "asserts")] += 1
            hets[gene] = found
        wanted = rp.pairs(hets)
        if not wanted:
            continue
        for row in rp.count(cram, wanted):
            totals["pairs_within_reach"] += 1
            per_gene[(row["gene"], "pairs_within_reach")] += 1
            pair_rows.append(
                (
                    sample,
                    row["gene"],
                    row["a"],
                    row["b"],
                    row["distance"],
                    row["cis"],
                    row["trans"],
                    row["shows"] or "",
                )
            )
            if not row["shows"]:
                totals["pairs_the_reads_do_not_decide"] += 1
                continue
            totals["pairs_decided"] += 1
            per_gene[(row["gene"], "pairs_decided")] += 1
            diplotype = printed(genes[row["gene"]])
            if not diplotype:
                totals["call_shows_several_diplotypes"] += 1
                continue
            (ca, pa, aa), (cb, pb, ab) = _parse(row["a"]), _parse(row["b"])
            left = defs.side(row["gene"], diplotype, ca, pa, aa)
            right = defs.side(row["gene"], diplotype, cb, pb, ab)
            if left is None or right is None:
                totals["definition_silent_about_a_position"] += 1
                continue
            asserted = "cis" if left == right else "trans"
            verdict = "agrees" if asserted == row["shows"] else "CONTRADICTS"
            totals[verdict] += 1
            per_gene[(row["gene"], verdict)] += 1
            assertion_rows.append(
                (
                    sample,
                    row["gene"],
                    "/".join(diplotype),
                    row["a"],
                    row["b"],
                    row["distance"],
                    row["cis"],
                    row["trans"],
                    row["shows"],
                    asserted,
                    verdict,
                )
            )

    out_dir.mkdir(parents=True, exist_ok=True)

    def write(name, header, rows):
        with open(out_dir / name, "w", newline="") as fh:
            w = csv.writer(fh, delimiter="\t", lineterminator="\n")
            w.writerow(header)
            w.writerows(rows)

    write(
        "pairs.tsv",
        ("sample", "gene", "a", "b", "distance", "cis", "trans", "shows"),
        pair_rows,
    )
    write(
        "assertions.tsv",
        (
            "sample",
            "gene",
            "printed",
            "a",
            "b",
            "distance",
            "cis",
            "trans",
            "reads",
            "asserted",
            "verdict",
        ),
        sorted(assertion_rows, key=lambda r: (r[10] != "CONTRADICTS", r[0], r[1])),
    )
    measures = (
        "asserts",
        "pairs_within_reach",
        "pairs_decided",
        "agrees",
        "CONTRADICTS",
    )
    write(
        "by_gene.tsv",
        ("gene", *measures),
        [
            (gene, *(per_gene[(gene, m)] for m in measures))
            for gene in sorted({g for g, _ in per_gene})
        ],
    )

    print(f"definitions: PharmCAT {defs.version}")
    for key in sorted(totals):
        print(f"  {key:38s} {totals[key]}")


if __name__ == "__main__":
    main()
