"""The positions behind each call PharmCAT made itself.

PharmCAT's report JSON carries, for every gene its matcher called, the whole panel of
positions it reads there: chromosome, position, rsID, the genotype it read, which
star alleles the position helps define, whether it was phased, and any warning it
raised. No surface of this report listed any of it. A reader of the 23 genes
PharmCAT calls on a real run saw a diplotype with nothing behind it, while the genes
PyPGx calls carried their defining variants, each read's allele fraction and, since
app/reports/rarity.py, how common each variant is.

All of it is far too much. Over the NA12878 demo run PharmCAT genotypes 1,066
positions across those 23 genes: 319 in RYR1 alone, 171 in G6PD, 93 in CFTR. Of the
1,066, seventeen were read as anything other than homozygous reference.

Those seventeen are the evidence. A diplotype is what the genotypes at the panel's
positions add up to, and a position read as homozygous reference is what makes the
reference allele the answer there: it adds nothing a reader can weigh. So each gene
lists the positions that differ, with the frequency of the allele found, and says how
many it read in all and how many it could not read -- the last being the ones that
could have changed the call and did not get the chance.

Read at report time from the report JSON PharmCAT already hands the generator, so an
older run re-rendered gains the table, as with rarity.py. Nothing is written by the
pipeline for it.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from app.pharmcat.report_json import iter_gene_blocks
from app.reports import rarity

logger = logging.getLogger(__name__)

#: Past this many alleles a position's list is a column of its own rather than a
#: cell: NAT2's rs1801280 defines 29 of them. The rest are counted instead.
MAX_ALLELES = 4


def _alts(call: Any, reference: Any) -> List[str]:
    """The bases in a genotype that are not the reference one.

    PharmCAT writes a genotype as its bases, ``C/T`` unphased or ``C|T`` phased, and
    an indel as the whole sequence either side (``CAT/CATAT``), which is how gnomAD
    writes it too. ``./.`` and an empty call are no genotype at all.
    """
    if not isinstance(call, str) or not isinstance(reference, str) or not reference:
        return []
    bases = [b.strip() for b in call.replace("|", "/").split("/")]
    return sorted({b for b in bases if b and b != "." and b != reference})


def _zygosity(call: Any, reference: Any) -> str:
    """How the genotype read: the words unlisted.py's table uses."""
    if not isinstance(call, str):
        return ""
    bases = [b.strip() for b in call.replace("|", "/").split("/")]
    if len(bases) != 2 or any(b in ("", ".") for b in bases):
        return ""
    if bases[0] != bases[1]:
        return "heterozygous"
    return "homozygous" if bases[0] != reference else ""


def _alleles(names: Sequence[Any]) -> str:
    """Which star alleles the position helps define, in as many as the cell holds."""
    items = [str(n) for n in names or [] if n]
    if len(items) <= MAX_ALLELES:
        return ", ".join(items)
    rest = len(items) - MAX_ALLELES
    return ", ".join(items[:MAX_ALLELES]) + f" and {rest} more"


def reference_of(variant: Mapping[str, Any]) -> Any:
    """The base the panel calls the reference at this position.

    PharmCAT 3.x writes ``referenceAllele``; 2.x wrote ``wildtypeAllele``. Reading only
    the first left every genotype in an older report with no reference to compare
    against, so nothing differed from it and the table was empty rather than absent --
    the quieter half of the shape problem the walker above fixes.
    """
    return variant.get("referenceAllele") or variant.get("wildtypeAllele")


def variant_key(variant: Mapping[str, Any], alt: str) -> str:
    """The variant as the bundle keys it: ``chr10-94781859-G-A``."""
    return (
        f"{variant.get('chromosome')}-{variant.get('position')}"
        f"-{reference_of(variant)}-{alt}"
    )


def _rows(gene: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """The positions this gene was read at that are not homozygous reference."""
    rows = []
    for variant in gene.get("variants") or []:
        if not isinstance(variant, Mapping):
            continue
        reference = reference_of(variant)
        alts = _alts(variant.get("call"), reference)
        if not alts:
            continue
        rows.append(
            {
                "position": f"{variant.get('chromosome')}:{variant.get('position')}",
                "rsid": variant.get("dbSnpId") or "",
                "call": str(variant.get("call") or ""),
                "reference": str(reference or ""),
                "zygosity": _zygosity(variant.get("call"), reference),
                "defines": _alleles(variant.get("alleles") or []),
                "phased": bool(variant.get("phased")),
                "keys": [variant_key(variant, alt) for alt in alts],
                "warnings": [str(w) for w in (variant.get("warnings") or []) if w],
                "undocumented": bool(variant.get("hasUndocumentedVariations")),
            }
        )
    return rows


def _unread(gene: Mapping[str, Any]) -> int:
    """How many of the gene's positions carry no genotype at all."""
    return sum(
        1
        for variant in gene.get("variants") or []
        if isinstance(variant, Mapping) and not variant.get("call")
    )


def collect(
    report: Optional[Mapping[str, Any]], bundle: Optional[Path] = None
) -> Dict[str, Any]:
    """What PharmCAT read, gene by gene, with a frequency against each allele found.

    Only the genes its own matcher called: an outside call (CYP2D6 from PyPGx, HLA
    from OptiType) carries no positions here, and PyPGx's evidence panel already
    shows the variants behind the ones that do.
    """
    genes = (report or {}).get("genes")
    if not isinstance(genes, Mapping):
        return {"status": "none"}
    found, genotyped, unread = [], 0, 0
    seen = set()
    # Through the walker that knows both shapes PharmCAT has emitted: 3.x writes
    # genes -> symbol, 2.x wrote genes -> {CPIC|DPWG|FDA} -> symbol
    # (app/pharmcat/report_json.py). Reading the symbols straight off ``genes`` found
    # three guideline sources and no variants in the older shape, so the whole table
    # disappeared without a word -- which is what test_data's own 2.15 report does.
    # A gene in that shape appears under each source that annotates it, with the same
    # payload, so the first of them answers for it.
    for block in sorted(iter_gene_blocks(genes), key=lambda b: b.gene_symbol):
        symbol, gene = block.gene_symbol, block.gene_data
        if symbol in seen:
            continue
        seen.add(symbol)
        panel = [v for v in gene.get("variants") or [] if isinstance(v, Mapping)]
        if not panel:
            continue
        genotyped += len(panel)
        unread += _unread(gene)
        rows = _rows(gene)
        if rows:
            found.append(
                {
                    "gene": str(symbol),
                    "genotyped": len(panel),
                    "unread": _unread(gene),
                    "rows": rows,
                }
            )
    if not genotyped:
        return {"status": "none"}

    keys = [key for gene in found for row in gene["rows"] for key in row["keys"]]
    prices = rarity.resolve(keys, bundle)
    for gene in found:
        for row in gene["rows"]:
            texts = [prices[k]["text"] for k in row["keys"] if k in prices]
            row["frequency"] = "; ".join(texts)
    return {
        "status": "done",
        "genes": found,
        "genotyped": genotyped,
        "differing": sum(len(gene["rows"]) for gene in found),
        "unread": unread,
        "release": rarity.release(bundle) if prices else None,
        "no_data": rarity.NO_DATA,
        "priced": bool(prices),
    }
