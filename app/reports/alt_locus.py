"""Genes whose reads mostly have mapping quality 0: the alignment was not ALT-aware.

GRCh38 carries alternate copies of CYP2D6 and UGT2B15 (and of HLA-A, -B and -C) on
ALT contigs. Aligned against a reference that includes them, without ALT-aware
mapping, a read from one of these genes matches two places equally: it gets mapping
quality 0 and lands on either copy. PyPGx calls CYP2D6's and UGT2B15's variants from
reads at MAPQ >= 1 (bcftools, through fuc's ``min_mq=1``) and their copy number from
the depth on the primary copy, so the call rests on a fraction of the gene's reads,
and the depth falls with the reads moved to the alternate copy: a copy number that
reads as a deletion.

HLA-A, -B and -C are measured but never flagged. zarohla hands OptiType every read in
the primary MHC, on every HLA-* contig and on every chr6 ALT or MHC contig, plus the
unplaced unmapped reads, whatever their mapping quality, and OptiType maps them itself
against the IPD-IMGT/HLA alleles. Its MAPQ >= 1 probe only decides whether typing runs
at all, and says so when it does not ("HLA reads not located"). PharmCAT gets HLA only
as OptiType's outside call.

The PyPGx sidecar counts the reads (docker/pypgx's alt_locus_mapq0, on the
pypgx_bam2vcf step's output_data, with the measurements behind the threshold); this
module decides which genes to flag and says so in the report. Calls are kept, and
flagged: the reader decides, told what was measured.

Found 2026-10-13 on a genome aligned with plain ``bwa mem`` to NCBI's full GRCh38
(GCF_000001405.26), which has the ALT contigs and no ``.alt`` file beside the index.
"""

from __future__ import annotations

import html
from typing import Any, Dict, List, Mapping, Optional

# More than half the reads over the gene at MAPQ 0. NA12878's reads, realigned: at
# most 16.4% with ALT-aware BWA (CYP2D6; 5.3% with no ALT contigs at all), at least
# 73.3% without ALT-awareness (CYP2D6; 82.4% for UGT2B15).
MAPQ0_SHARE_FLAGGED = 0.5
# Below this many reads a share says little, and the gene's call has little to stand on
# for reasons the partial-coverage alerts already give.
MIN_READS = 20
# The genes whose calls use only reads at MAPQ >= 1. Not HLA: see the docstring.
FLAGGED_GENES = ("CYP2D6", "UGT2B15")
_HLA = ("HLA-A", "HLA-B", "HLA-C")


def mostly_mapq0(
    bam2vcf_output: Optional[Mapping[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """{gene: {"reads", "mapq0", "percent"}} for each measured gene above the
    threshold, flagged or not, in the sidecar's order. Empty when nothing was measured
    (a VCF input, an older sidecar) or nothing crossed it."""
    counts = (bam2vcf_output or {}).get("alt_locus_mapq0") or {}
    found: Dict[str, Dict[str, Any]] = {}
    if not isinstance(counts, Mapping):
        return found
    for gene, measured in counts.items():
        try:
            reads = int(measured.get("reads") or 0)
            mapq0 = int(measured.get("mapq0") or 0)
        except (AttributeError, TypeError, ValueError):
            continue
        if reads >= MIN_READS and mapq0 / reads > MAPQ0_SHARE_FLAGGED:
            found[str(gene)] = {
                "reads": reads,
                "mapq0": mapq0,
                "percent": round(100 * mapq0 / reads),
            }
    return found


def alt_locus_flags(
    bam2vcf_output: Optional[Mapping[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """The genes to flag: those of FLAGGED_GENES above the threshold."""
    return {
        gene: found
        for gene, found in mostly_mapq0(bam2vcf_output).items()
        if gene in FLAGGED_GENES
    }


def alt_locus_alert(bam2vcf_output: Optional[Mapping[str, Any]]) -> Optional[str]:
    """The Alerts and Warnings entry naming the flagged genes, or None."""
    flags = alt_locus_flags(bam2vcf_output)
    if not flags:
        return None
    measured = "; ".join(
        f"{html.escape(gene)} ({f['percent']}% of {f['reads']:,} reads)"
        for gene, f in flags.items()
    )
    alert = (
        f"<p>⚠️ Reads with mapping quality 0: {measured}. GRCh38 includes alternate "
        "copies of these genes (ALT contigs). An alignment made against a reference "
        "that has them, without ALT-aware mapping, gives a read that matches both "
        "copies mapping quality 0 and places many on the alternate copy. PyPGx calls "
        "these genes' variants from reads with mapping quality 1 or more, and their "
        "copy number from the depth on the primary copy, so these calls rest on a "
        "fraction of the gene's reads and may be wrong: a copy number can read as a "
        "deletion. For reliable calls, realign the reads to the GRCh38 no-alt "
        "analysis set, or with BWA in ALT-aware mode (the reference's "
        "<code>.alt</code> file beside its index).</p>"
    )
    hla = [gene for gene in mostly_mapq0(bam2vcf_output) if gene in _HLA]
    if hla:
        named = " and ".join([", ".join(hla[:-1]), hla[-1]] if len(hla) > 1 else hla)
        alert += (
            f"<p>{named} also had mostly mapping quality 0. Their typing uses every "
            "read in the MHC and on its ALT contigs, whatever its mapping quality: "
            "OptiType maps them itself.</p>"
        )
    return alert


def alt_locus_note(flag: Mapping[str, Any]) -> str:
    """The note on a flagged gene in the Notes to the Executive Summary."""
    return (
        f"{flag['percent']}% of the reads over this gene had mapping quality 0 (the "
        "alignment was not ALT-aware), so its call may be wrong. See Alerts and "
        "Warnings."
    )


def alt_locus_call_note(flag: Mapping[str, Any]) -> str:
    """The note under a flagged gene's call, in a narrow table column."""
    return (
        f"{flag['percent']}% of reads had mapping quality 0: this call may be wrong "
        "(see Alerts)"
    )


def annotate_alt_locus(
    genes: List[Dict[str, Any]], flags: Mapping[str, Mapping[str, Any]]
) -> None:
    """Put each flagged gene's measurement and notes on its report row, in place."""
    if not flags:
        return
    for row in genes or []:
        if not isinstance(row, dict):
            continue
        gene = str(row.get("gene") or "")
        flag = flags.get(gene) or flags.get(gene.upper())
        if flag:
            row["alt_locus"] = dict(
                flag, note=alt_locus_note(flag), call_note=alt_locus_call_note(flag)
            )


def alt_locus_diagram_line(flags: Mapping[str, Mapping[str, Any]]) -> Optional[str]:
    """The workflow diagram's line for the step that measured it, or None."""
    if not flags:
        return None
    return "mostly MAPQ 0, not ALT-aware: " + ", ".join(flags)
