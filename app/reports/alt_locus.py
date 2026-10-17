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

Since 2026-10-16 the sidecar also aligns the reads of the CYP2D6 and UGT2B17/UGT2B15
loci again, to main contigs only, when the upload has reads on their ALT contigs
(docker/pypgx's realign_alt_loci, recorded as ``alt_realignment``). Then the counts
above are of the re-aligned reads, and the report says what was done instead of
flagging. When it could not be done, a locus with reads on its ALT contigs is flagged
whatever its MAPQ 0 share: NA12878's reads aligned without ALT-awareness left CYP2D6
with 48% at MAPQ 0, under the threshold, and 272 usable reads of 1,479.

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


def alt_realignment(
    bam2vcf_output: Optional[Mapping[str, Any]],
) -> Optional[Mapping[str, Any]]:
    """What the sidecar did about reads on the ALT contigs, or None: no locus had
    any, or the record predates the re-alignment (2026-10-16)."""
    info = (bam2vcf_output or {}).get("alt_realignment")
    if isinstance(info, Mapping) and info.get("loci"):
        return info
    return None


def _realigned(info: Optional[Mapping[str, Any]]) -> bool:
    return bool(info) and info.get("status") == "realigned"


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _left_on_alt_contigs(
    bam2vcf_output: Optional[Mapping[str, Any]],
) -> Dict[str, int]:
    """{gene: reads on its locus's ALT contigs} for FLAGGED_GENES in loci found and
    not re-aligned."""
    info = alt_realignment(bam2vcf_output)
    if not info or _realigned(info):
        return {}
    left: Dict[str, int] = {}
    for locus in info.get("loci") or []:
        if not isinstance(locus, Mapping):
            continue
        reads = _as_int(locus.get("alt_reads"))
        if reads < MIN_READS:
            continue
        for gene in locus.get("genes") or []:
            if gene in FLAGGED_GENES:
                left[str(gene)] = reads
    return left


def alt_locus_flags(
    bam2vcf_output: Optional[Mapping[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """The genes to flag: those of FLAGGED_GENES above the threshold, and those whose
    locus kept reads on its ALT contigs (``alt_reads``) for want of re-alignment."""
    flags = {
        gene: found
        for gene, found in mostly_mapq0(bam2vcf_output).items()
        if gene in FLAGGED_GENES
    }
    counts = (bam2vcf_output or {}).get("alt_locus_mapq0") or {}
    for gene, alt_reads in _left_on_alt_contigs(bam2vcf_output).items():
        flag = flags.get(gene)
        if flag is None:
            measured = counts.get(gene) if isinstance(counts, Mapping) else None
            reads = (
                _as_int((measured or {}).get("reads"))
                if isinstance(measured, Mapping)
                else 0
            )
            mapq0 = (
                _as_int((measured or {}).get("mapq0"))
                if isinstance(measured, Mapping)
                else 0
            )
            flag = {
                "reads": reads,
                "mapq0": mapq0,
                "percent": round(100 * mapq0 / reads) if reads else 0,
            }
        flags[gene] = dict(flag, alt_reads=alt_reads)
    return {gene: flags[gene] for gene in FLAGGED_GENES if gene in flags}


def _mostly_mapq0(flag: Mapping[str, Any]) -> bool:
    reads = _as_int(flag.get("reads"))
    return (
        reads >= MIN_READS and _as_int(flag.get("mapq0")) / reads > MAPQ0_SHARE_FLAGGED
    )


def _measured(gene: str, flag: Mapping[str, Any]) -> str:
    if not flag.get("alt_reads"):
        return f"{html.escape(gene)} ({flag['percent']}% of {flag['reads']:,} reads)"
    parts = []
    if _mostly_mapq0(flag):
        parts.append(
            f"{flag['percent']}% of {flag['reads']:,} reads at mapping quality 0"
        )
    parts.append(f"{flag['alt_reads']:,} reads on its ALT contigs")
    return f"{html.escape(gene)} ({'; '.join(parts)})"


def _gene_list(genes: List[str]) -> str:
    named = [html.escape(str(g)) for g in genes]
    return " and ".join([", ".join(named[:-1]), named[-1]] if len(named) > 1 else named)


def _realignment_entry(
    bam2vcf_output: Optional[Mapping[str, Any]], info: Mapping[str, Any]
) -> str:
    """The Alerts entry saying which loci were aligned again, and what changed."""
    loci = [locus for locus in info.get("loci") or [] if isinstance(locus, Mapping)]
    on_alts = ", ".join(
        f"{_as_int(locus.get('alt_reads')):,} for "
        f"{_gene_list(list(locus.get('genes') or []))}"
        for locus in loci
    )
    reads = info.get("reads") if isinstance(info.get("reads"), Mapping) else {}
    pairs = _as_int(reads.get("pairs"))
    single = _as_int(reads.get("single"))
    counted = []
    if pairs:
        counted.append(f"{pairs:,} read pairs")
    if single:
        counted.append(f"{single:,} single reads")
    before = (
        info.get("mapq0_before")
        if isinstance(info.get("mapq0_before"), Mapping)
        else {}
    )
    after = (bam2vcf_output or {}).get("alt_locus_mapq0") or {}
    changes = []
    for locus in loci:
        for gene in locus.get("genes") or []:
            if gene not in FLAGGED_GENES:
                continue
            b, a = before.get(gene), (
                after.get(gene) if isinstance(after, Mapping) else None
            )
            if not isinstance(b, Mapping) or not isinstance(a, Mapping):
                continue
            b_reads, a_reads = _as_int(b.get("reads")), _as_int(a.get("reads"))
            if not b_reads or not a_reads:
                continue
            changes.append(
                f"{html.escape(str(gene))} {b_reads:,} before "
                f"({round(100 * _as_int(b.get('mapq0')) / b_reads)}% at mapping "
                "quality 0), "
                f"{a_reads:,} after ({round(100 * _as_int(a.get('mapq0')) / a_reads)}%)"
            )
    genes = _gene_list([g for locus in loci for g in (locus.get("genes") or [])])
    text = (
        # No emoji: the PDF's fonts draw ℹ️ as a bare "i" (seen in the rendered
        # report), and every entry here shares one amber box anyway.
        f"<p><strong>Re-aligned before genotyping:</strong> {genes}. The uploaded "
        "alignment had reads from these loci on GRCh38's alternate copies of them "
        "(ALT contigs"
        + (f": {on_alts}" if on_alts else "")
        + "), where PyPGx does not read. ZaroPGx aligned the reads of these loci"
        + (f", {' and '.join(counted)}," if counted else "")
        + " again to GRCh38's main chromosomes with BWA-MEM (GATK BwaSpark), as PyPGx "
        "requires, and marked duplicates. These genes are called from those reads; "
        "every other gene from the reads as uploaded."
    )
    if changes:
        text += " Reads over the gene: " + "; ".join(changes) + "."
    orphans = _as_int(reads.get("orphans"))
    if orphans:
        text += (
            f" {orphans:,} reads whose mate could not be found were kept as uploaded."
        )
    return text + "</p>"


def alt_locus_alert(bam2vcf_output: Optional[Mapping[str, Any]]) -> Optional[str]:
    """The Alerts and Warnings entry naming the flagged genes, or saying which loci
    were re-aligned, or None."""
    info = alt_realignment(bam2vcf_output)
    flags = alt_locus_flags(bam2vcf_output)
    alert = _realignment_entry(bam2vcf_output, info) if _realigned(info) else ""
    if not flags and not alert:
        return None
    if flags:
        measured = "; ".join(_measured(gene, f) for gene, f in flags.items())
        heading = (
            "Reads on ALT contigs or with mapping quality 0"
            if any(f.get("alt_reads") for f in flags.values())
            else "Reads with mapping quality 0"
        )
        alert += (
            f"<p>⚠️ {heading}: {measured}. "
            "GRCh38 "
            "includes alternate copies of these genes (ALT contigs). An alignment made "
            "against a reference that has them puts some of their reads on the "
            "alternate copy, where PyPGx does not read, and without ALT-aware mapping "
            "gives the reads that match both copies mapping quality 0. PyPGx calls "
            "these genes' variants from reads with mapping quality 1 or more, and "
            "their copy number from the depth on the primary copy, so these calls "
            "rest on a fraction of the gene's reads and may be wrong: a copy number "
            "can read as a deletion."
        )
        if info and not _realigned(info):
            reason = html.escape(str(info.get("reason") or "no reason was recorded"))
            alert += (
                " ZaroPGx aligns these loci's reads again itself when it can; this run "
                f"could not ({reason})."
            )
        alert += (
            " For reliable calls, realign the reads to the GRCh38 no-alt analysis set, "
            "or with BWA in ALT-aware mode (the reference's <code>.alt</code> file "
            "beside its index).</p>"
        )
    hla = [gene for gene in mostly_mapq0(bam2vcf_output) if gene in _HLA]
    if hla:
        alert += (
            f"<p>{_gene_list(hla)} also had mostly mapping quality 0. Their typing "
            "uses "
            "every read in the MHC and on its ALT contigs, whatever its mapping "
            "quality: OptiType maps them itself.</p>"
        )
    return alert


def alt_locus_note(flag: Mapping[str, Any]) -> str:
    """The note on a flagged gene in the Notes to the Executive Summary."""
    if _mostly_mapq0(flag):
        return (
            f"{flag['percent']}% of the reads over this gene had mapping quality 0 "
            "(the alignment was not ALT-aware), so its call may be wrong. See Alerts "
            "and Warnings."
        )
    return (
        f"{flag['alt_reads']:,} reads from this gene's locus were on GRCh38's ALT "
        "contigs, where PyPGx does not read them, so its call may be wrong. See Alerts "
        "and Warnings."
    )


def alt_locus_call_note(flag: Mapping[str, Any]) -> str:
    """The note under a flagged gene's call, in a narrow table column."""
    if _mostly_mapq0(flag):
        return (
            f"{flag['percent']}% of reads had mapping quality 0: this call may be "
            "wrong (see Alerts)"
        )
    return (
        f"{flag['alt_reads']:,} of its locus's reads were on ALT contigs: this call "
        "may be wrong (see Alerts)"
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
    if any(flag.get("alt_reads") for flag in flags.values()):
        return "reads on ALT contigs, not re-aligned: " + ", ".join(flags)
    return "mostly MAPQ 0, not ALT-aware: " + ", ".join(flags)


def alt_realignment_diagram_line(
    bam2vcf_output: Optional[Mapping[str, Any]],
) -> Optional[str]:
    """The workflow diagram's line for loci aligned again, or None."""
    info = alt_realignment(bam2vcf_output)
    if not _realigned(info):
        return None
    genes = [
        g
        for locus in info.get("loci") or []
        if isinstance(locus, Mapping)
        for g in locus.get("genes") or []
    ]
    return "re-aligned without ALT contigs: " + ", ".join(genes)


def realigned_alt_loci(bam2vcf_output: Optional[Mapping[str, Any]]) -> bool:
    """Whether this run aligned any reads again (credits BWA-MEM and GATK)."""
    return _realigned(alt_realignment(bam2vcf_output))
