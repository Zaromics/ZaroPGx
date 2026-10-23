"""Deletions in CYP2C19 and SLCO1B1, from the read depth PyPGx measures.

PyPGx calls structural alleles only for the genes its gene table marks SV, and
CYP2C19 and SLCO1B1 are not among them, so their deletions went uncalled: CYP2C19
*36 (the whole gene) and *37 (a partial deletion that takes exon 1), SLCO1B1 *48
(the whole gene) and *49 (part of it), all no function in CPIC's tables. With one
copy deleted the other reads as homozygous, and a *1/*37 sample was called *1/*1,
Normal Metabolizer, where it is Intermediate.

PyPGx's own steps still apply to them: depth over the gene's region
(create_regions_bed, without its SV-genes filter), normalised against the control
gene the sidecar already measured (VDR, compute_control_statistics), as copy number
(compute_copy_number). This takes the median copy number of each 5 kb of the region
and calls a run of at least two bins well under two copies a deletion, of one copy
or of both, named by the exons it takes.

On 1000 Genomes 30x genomes: HG00268 reads ~0.9 copies from the region's start to
94,799.5 kb, CYP2C19 *37 (exons 1-5; Krebs et al., npj Genom Med 2026;11:10, whose
breakpoints are chr10:94,737,567-94,799,352); HG01771 reads ~1.0 over exons 2-5 with
exon 1 kept, which is not *37 (the same paper proposes *42 for an exons 2-5 deletion;
CPIC and PharmCAT have no function for it); normals read 1.5-2.3 in every bin.

    python3 deletion_check.py ALIGNMENT CONTROL_STATISTICS OUT.json [--assembly GRCh38]

The calling below takes plain numbers, so it is tested without PyPGx.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

GENES = ("CYP2C19", "SLCO1B1")
BIN = 5000
# A bin under ONE_COPY reads as one copy lost, under BOTH_COPIES as both. Normal bins
# read 1.5-2.3 on 30x genomes and a one-copy deletion 0.8-1.1 (see the module note);
# a run must be MIN_BINS long, so one noisy bin calls nothing.
ONE_COPY = 1.35
BOTH_COPIES = 0.4
MIN_BINS = 2
# The bins outside any run must read as two copies for a call to be made: a profile
# whose rest is off (a low-quality or unusually normalised alignment) says nothing.
NORMAL_RANGE = (1.6, 2.4)
VERSION = 1

# What each deletion is, in PharmVar's names (CPIC: no function, limited evidence).
# CYP2C19 *37 is "a partial deletion including at least the whole of exon 1"; a
# deletion that keeps exon 1 has no star allele CPIC or PharmCAT assigns a function.
WHOLE_GENE = {"CYP2C19": "*36", "SLCO1B1": "*48"}
PARTIAL = {"SLCO1B1": "*49"}


def bins_of(
    positions: Sequence[int], copy_numbers: Sequence[float], start: int, end: int
) -> List[Tuple[int, int, float]]:
    """(start, end, median copy number) for each BIN bp of [start, end)."""
    out = []
    pairs = sorted(zip(positions, copy_numbers))
    i = 0
    for lo in range(start, end, BIN):
        hi = min(lo + BIN, end)
        values = []
        while i < len(pairs) and pairs[i][0] < hi:
            if pairs[i][0] >= lo:
                values.append(pairs[i][1])
            i += 1
        if values:
            out.append((lo, hi, round(statistics.median(values), 3)))
    return out


def runs_under(bins: Sequence[Tuple[int, int, float]]) -> List[List[Tuple[int, int, float]]]:
    """Runs of at least MIN_BINS consecutive bins under ONE_COPY."""
    runs, current = [], []
    for b in bins:
        if b[2] < ONE_COPY and (not current or current[-1][1] == b[0]):
            current.append(b)
            continue
        if len(current) >= MIN_BINS:
            runs.append(current)
        current = [b] if b[2] < ONE_COPY else []
    if len(current) >= MIN_BINS:
        runs.append(current)
    return runs


def exons_in(
    lo: int, hi: int, exons: Sequence[Tuple[int, int]], strand: str = "+"
) -> List[int]:
    """The numbers (1-based, in the gene's direction) of the exons [lo, hi) overlaps."""
    order = list(exons) if strand == "+" else list(exons)[::-1]
    return [n for n, (s, e) in enumerate(order, 1) if s < hi and e > lo]


def name_deletion(gene: str, exons: List[int], total: int) -> Optional[str]:
    """The star allele a deletion of these exons is, or None when it is none."""
    if not exons:
        return None
    if len(exons) == total:
        return WHOLE_GENE.get(gene)
    if gene == "CYP2C19":
        return "*37" if 1 in exons else None
    return PARTIAL.get(gene)


def _exon_span(exons: List[int]) -> str:
    if not exons:
        return "no exon"
    if len(exons) == 1:
        return f"exon {exons[0]}"
    return f"exons {exons[0]}-{exons[-1]}"


def describe(gene: str, deletion: Dict[str, Any], total: int) -> str:
    """The deletion in words, for the report."""
    copies = "both copies" if deletion["copies_lost"] == 2 else "one copy"
    whole = len(deletion["exons"]) == total
    what = f"{copies} of the whole gene" if whole else (
        f"{copies} of {_exon_span(deletion['exons'])}"
    )
    allele = deletion.get("allele")
    if allele:
        text = f"{gene} {allele}: {what} deleted"
    else:
        text = f"a deletion of {what}, which is no star allele CPIC assigns a function"
        if gene == "CYP2C19" and deletion["exons"] and 1 not in deletion["exons"]:
            text += (
                " (it keeps exon 1, so it is not *37; Krebs et al. 2026 proposed *42 "
                "for a deletion of exons 2-5)"
            )
    if gene == "SLCO1B1" and whole:
        text += "; a deletion this size may extend into SLCO1B3"
    return text


def call_gene(
    gene: str,
    bins: Sequence[Tuple[int, int, float]],
    exons: Sequence[Tuple[int, int]],
    strand: str = "+",
) -> Dict[str, Any]:
    """The gene's deletion call from its binned copy number."""
    record: Dict[str, Any] = {
        "status": "measured",
        "bins": [list(b) for b in bins],
        "deletions": [],
    }
    if len(bins) < MIN_BINS:
        return dict(record, status="no_depth")
    runs = runs_under(bins)
    inside = {(b[0], b[1]) for run in runs for b in run}
    rest = [b[2] for b in bins if (b[0], b[1]) not in inside]
    if rest and not NORMAL_RANGE[0] <= statistics.median(rest) <= NORMAL_RANGE[1]:
        return dict(record, status="uncertain", rest_median=round(statistics.median(rest), 3))
    for run in runs:
        lo, hi = run[0][0], run[-1][1]
        median = statistics.median(b[2] for b in run)
        numbers = exons_in(lo, hi, exons, strand)
        deletion = {
            "start": lo,
            "end": hi,
            "median_copy_number": round(median, 3),
            "copies_lost": 2 if median < BOTH_COPIES else 1,
            "exons": numbers,
            "allele": name_deletion(gene, numbers, len(exons)),
        }
        if not numbers:
            continue  # outside every exon: no allele, nothing to report
        deletion["description"] = describe(gene, deletion, len(exons))
        record["deletions"].append(deletion)
    return record


def genotype(snv_diplotype: Optional[str], record: Dict[str, Any]) -> Dict[str, Any]:
    """The gene's call with its deletion: {"diplotype", "copy_number", "conflict"}.

    With one copy deleted, the small-variant call of the other reads homozygous
    (X/X), and the call is X/<deleted allele>. A heterozygous call with a deletion
    under it is a contradiction this does not resolve: the call is left as it was
    and marked. A deletion that is no star allele leaves the call as it was too.
    """
    deletions = record.get("deletions") or []
    if record.get("status") != "measured":
        return {"diplotype": snv_diplotype, "copy_number": None, "conflict": False}
    if not deletions:
        return {"diplotype": snv_diplotype, "copy_number": "Normal", "conflict": False}
    deletion = max(deletions, key=lambda d: (d["copies_lost"], len(d["exons"])))
    whole = deletion["allele"] in WHOLE_GENE.values()
    code = ("WholeDel1" if whole else "PartialDel1") + (
        "Hom" if deletion["copies_lost"] == 2 else ""
    )
    allele = deletion.get("allele")
    if not allele or len(deletions) > 1:
        return {"diplotype": snv_diplotype, "copy_number": code, "conflict": False}
    if deletion["copies_lost"] == 2:
        return {"diplotype": f"{allele}/{allele}", "copy_number": code, "conflict": False}
    halves = [h.strip() for h in str(snv_diplotype or "").split("/")]
    if len(halves) == 2 and halves[0] == halves[1] and halves[0]:
        return {"diplotype": f"{halves[0]}/{allele}", "copy_number": code, "conflict": False}
    return {"diplotype": snv_diplotype, "copy_number": code, "conflict": True}


def measure(alignment: str, control_statistics: str, assembly: str) -> Dict[str, Any]:
    """Each gene's binned copy number and deletion call, from PyPGx's own steps."""
    from fuc import pycov
    from pypgx import sdk
    from pypgx.api import core, utils

    regions = utils.create_regions_bed(
        merge=True, sv_genes=False, assembly=assembly, genes=list(GENES)
    ).to_regions()
    cf = pycov.CovFrame.from_bam([alignment], regions=regions, zero=True)
    depth = sdk.Archive(
        {"Assembly": assembly, "SemanticType": "CovFrame[DepthOfCoverage]", "Platform": "WGS"},
        cf,
    )
    control = sdk.Archive.from_file(control_statistics)
    table = core.load_gene_table()
    genes: Dict[str, Any] = {}
    for gene in GENES:
        cn = utils.compute_copy_number(utils.import_read_depth(gene, depth), control)
        df = cn.data.df
        row = table[table.Gene == gene].iloc[0]
        starts = [int(x) for x in str(row[f"{assembly}ExonStarts"]).strip(",").split(",")]
        ends = [int(x) for x in str(row[f"{assembly}ExonEnds"]).strip(",").split(",")]
        region = str(row[f"{assembly}Region"]).split(":")[1]
        lo, hi = (int(x) for x in region.split("-"))
        sample = df.columns[2]
        bins = bins_of(df.Position.tolist(), df[sample].astype(float).tolist(), lo, hi)
        genes[gene] = call_gene(gene, bins, list(zip(starts, ends)), row.Strand)
    return {
        "version": VERSION,
        "bin_size": BIN,
        "thresholds": {"one_copy": ONE_COPY, "both_copies": BOTH_COPIES, "min_bins": MIN_BINS},
        "genes": genes,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("alignment")
    ap.add_argument("control_statistics")
    ap.add_argument("out")
    ap.add_argument("--assembly", default="GRCh38", choices=("GRCh38",))
    args = ap.parse_args(argv)
    result = measure(args.alignment, args.control_statistics, args.assembly)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh)
    return 0


if __name__ == "__main__":
    sys.exit(main())
