"""Do the report's own readers still find anything in a real run?

    python scripts/getrm/report_sanity.py RESULTS_DIR

The report is built by readers that walk another tool's output: PharmCAT's report JSON,
PyPGx's result, OptiType's table. A reader that stops finding what it is looking for does
not raise -- it returns nothing, the section is left out, and the report looks complete.
That has happened: PharmCAT 2.x nests gene symbols differently from 1.x, the variants
traversal found none, and every report shipped without its evidence table until someone
read one (#232).

Tests pin each reader against a fixture, which is the shape production wrote on the day
the test was written. This asks the other question -- what does the current tool version
actually write -- by running the readers over a directory of real runs and reporting what
they found. It is a sweep, not an assertion: numbers to read, not a pass or fail, because
"3 genes have variants" is only alarming next to the 9 the rest of the corpus shows.

Run it on the GeT-RM results after a release, or on any directory `scripts/getrm/run.py`
has written. The checks are cheap: JSON in, counts out, no reads and no database.

What the corpus showed on 2026-11-14, over 61 GeT-RM genomes, so a later run has
something to be read against -- which is the whole point of counting rather than
asserting:

    genes with a variants table            min    4   median    9   max   13
    positions PharmCAT read                min 1066   median 1066   max 1066
    positions differing from reference     min    5   median   14   max   23
    positions it could not read            min    0   median    2   max    9
    gene rows in the report                min   23   median   23   max   23
    calls withheld (AMP/CAP Tier 1)        min    0   median    0   max    0
    PharmCAT reference labels: 226 in the form the summary matches, 216 spelling out

Two of those are worth knowing. Nothing was withheld in any of the 61: the Tier 1 gate
declines a chip and keeps a complete genome, and this is that measured at corpus scale
rather than on the one sample it was built against. A zero can also mean a gate that
never fires, so it was checked against the other possibility: mark CYP2C19's *2, *3 and
*17 uncalled in one of these same reports and the gate withholds CYP2C19. The zero means
all 61 were adequate. And both forms of PharmCAT's
reference label are live in roughly equal number, so neither branch of the summary's
handling is dead -- a release that normalized that label would move 226 cells to the
other branch and fail nothing.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from app.pharmcat.report_json import (  # noqa: E402
    PHARMCAT_REFERENCE_LABEL,
    extract_display_call,
)
from app.reports import pharmcat_variants, tier1_gate  # noqa: E402


def _spread(name: str, values: List[float], note: str = "") -> str:
    if not values:
        return f"{name:38s} nothing to measure"
    return (
        f"{name:38s} min {min(values):>6g}  median {statistics.median(values):>6g}  "
        f"max {max(values):>6g}" + (f"   {note}" if note else "")
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("results", type=Path, help="one directory per sample")
    args = ap.parse_args()

    samples = sorted(p for p in args.results.iterdir() if p.is_dir())
    genes_listed: List[float] = []
    positions_read: List[float] = []
    positions_differing: List[float] = []
    positions_unread: List[float] = []
    rows_seen: List[float] = []
    withheld_per_run: List[float] = []
    withheld_by_gene: Counter = Counter()
    reference_labels: Counter = Counter()
    silent: Dict[str, List[str]] = {"variants": [], "calls": []}

    for sample in samples:
        report_path = sample / "pharmcat.json"
        if not report_path.exists():
            continue
        report = json.loads(report_path.read_text(encoding="utf-8"))

        collected = pharmcat_variants.collect(report)
        genes = collected.get("genes") or []
        genes_listed.append(len(genes))
        positions_read.append(collected.get("genotyped") or 0)
        positions_differing.append(collected.get("differing") or 0)
        positions_unread.append(collected.get("unread") or 0)
        if not genes:
            silent["variants"].append(sample.name)

        rows = []
        for gene, block in (report.get("genes") or {}).items():
            call = extract_display_call(block) or {}
            label = call.get("diplotype") if isinstance(call, dict) else call
            label = str(label or "").strip()
            rows.append({"gene": gene, "diplotype": label, "phenotype": None})
            # The summary shows PharmCAT's own "Reference/Reference" label where the
            # TSV spells the reference allele out, and tells them apart with a pattern
            # (generator.PHARMCAT_REFERENCE_LABEL). A PharmCAT that renamed that label
            # would fail nothing -- the summary would quietly start printing the
            # spelled-out name instead -- so the forms are counted.
            if "eference" in label:
                key = (
                    "matched"
                    if PHARMCAT_REFERENCE_LABEL.fullmatch(label)
                    else "spelled out"
                )
                reference_labels[key] += 1
        rows_seen.append(len(rows))
        if not any(row["diplotype"] for row in rows):
            silent["calls"].append(sample.name)

        withheld = tier1_gate.withhold_inadequate_calls(rows, report)
        withheld_per_run.append(len(withheld))
        withheld_by_gene.update(withheld)

    if not genes_listed:
        # A sweep that read nothing must not print the all-clear below: "every reader
        # found something in every run" is true and meaningless over no runs, which is
        # the one failure a checker must not have.
        print(f"No run under {args.results} had a pharmcat.json to read.")
        return 1
    print(f"{len(genes_listed)} run(s) under {args.results}\n")
    print(_spread("genes with a variants table", genes_listed))
    print(_spread("positions PharmCAT read", positions_read))
    print(_spread("positions differing from reference", positions_differing))
    print(_spread("positions it could not read", positions_unread))
    print(_spread("gene rows in the report", rows_seen))
    print(_spread("calls withheld (AMP/CAP Tier 1)", withheld_per_run))
    if reference_labels:
        print(
            f"\nPharmCAT reference labels: {reference_labels['matched']} in the form "
            f"the summary matches, {reference_labels['spelled out']} spelling the "
            "allele out"
        )
    if withheld_by_gene:
        print(
            "\nwithheld by gene: "
            + ", ".join(f"{g} {n}" for g, n in withheld_by_gene.most_common())
        )

    problems = 0
    for reader, empty in silent.items():
        if empty:
            problems += len(empty)
            print(
                f"\n{reader}: nothing found in {len(empty)} run(s): {', '.join(empty[:5])}"
            )
    if not problems:
        print("\nEvery reader found something in every run.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
