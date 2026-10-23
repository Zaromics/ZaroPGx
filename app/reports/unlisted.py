"""The variants in the pharmacogenes that no allele definition lists, in the report.

The check itself runs in the pipeline (docker/pypgx's unlisted_variants.py, Nextflow's
UnlistedVariants) and leaves unlisted_variants.json in the report directory. This
module reads it and says, in the report's words:

- a section listing the variants predicted to change the protein, and how many others
  were found and why they are not listed there;
- beside each flagged gene's call, and in the Executive Summary's notes, that the call
  does not account for the variant;
- one alert naming the flagged genes.

Calls and phenotypes are never changed: a prediction is not a measurement, and the
phase of the variant against the call's alleles is not known.
"""

from __future__ import annotations

import html
import json
import logging
import os
from typing import Any, Dict, List, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)

RESULT_FILE = "unlisted_variants.json"
SECTION = "Variants Outside the Allele Definitions"
# The tiers the section's table lists; the rest are counted below it.
SHOWN_TIERS = ("A", "A-low", "B", "B-low", "C")
FLAG_TIERS = ("A", "B")
DPYD_CAVEAT = (
    "For DPYD, CPIC and DPWG note that a genotype with no decreased-function allele "
    "does not rule out severe fluoropyrimidine toxicity; measuring DPD activity is one "
    "way to confirm a prediction."
)


def read_result(report_dir: str) -> Optional[Dict[str, Any]]:
    """unlisted_variants.json, or None for a run that predates the check or lost it."""
    path = os.path.join(report_dir, RESULT_FILE)
    try:
        with open(path, "r", encoding="utf-8") as f:
            result = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        logger.warning("Could not read %s: %s", RESULT_FILE, exc)
        return {"status": "failed", "reason": "Its result could not be read."}
    return result if isinstance(result, dict) else None


def sample_is_male(pypgx_results: Any) -> Optional[bool]:
    """What PyPGx's G6PD copy number says of the sample's sex (it reads chrX depth on a
    whole genome), or None where it did not say."""
    results = pypgx_results.get("results") if isinstance(pypgx_results, Mapping) else None
    g6pd = results.get("G6PD") if isinstance(results, Mapping) else None
    details = g6pd.get("details") if isinstance(g6pd, Mapping) else None
    call = str((details or {}).get("copy_number") or "").strip().lower()
    return {"male": True, "female": False}.get(call)


def _variant_name(v: Mapping[str, Any]) -> str:
    """c. and p. where known, else the genomic change."""
    parts = [p for p in (v.get("hgvs_c"), v.get("hgvs_p")) if p]
    if parts:
        return " ".join(parts[:1] + [f"({parts[1]})"] if len(parts) == 2 else parts)
    return _genomic(v)


def _genomic(v: Mapping[str, Any]) -> str:
    """HGVS g. for a substitution; an indel as the VCF has it (HGVS would name the
    deleted or inserted bases, shifted 3', which nothing here computes)."""
    ref, alt = str(v.get("ref") or ""), str(v.get("alt") or "")
    if len(ref) == len(alt) == 1:
        return f"{v.get('chrom')}:g.{v.get('pos')}{ref}>{alt}"
    return f"{v.get('chrom')}:{v.get('pos')} {ref}>{alt}"


def _frequency(v: Mapping[str, Any]) -> str:
    g = v.get("gnomad")
    if not g:
        return "Not in gnomAD"
    af = g.get("af_grpmax")
    if af is None:
        return "In gnomAD, frequency not given"
    if af == 0:
        return "In gnomAD, not seen in any group"
    pct = 100 * af
    text = f"{pct:.2g}%" if pct >= 0.01 else "under 0.01%"
    group = g.get("grpmax")
    return f"{text}{f' ({group})' if group else ''}"


def _clinvar(v: Mapping[str, Any]) -> Dict[str, str]:
    """ClinVar's classification with its review stars, and the conditions it names."""
    c = v.get("clinvar")
    if not c:
        return {"text": "Not in ClinVar", "conditions": ""}
    stars = c.get("stars") or 0
    return {
        "text": f"ClinVar: {c.get('significance') or 'listed'}, {stars} {'star' if stars == 1 else 'stars'}",
        "conditions": ", ".join(c.get("conditions") or []),
    }


def _zygosity(v: Mapping[str, Any], male: Optional[bool]) -> str:
    z = v.get("zygosity") or ""
    if v.get("chrom") == "chrX" and male and z == "homozygous":
        return "hemizygous"
    return z


def _row(v: Mapping[str, Any], male: Optional[bool]) -> Dict[str, Any]:
    notes = []
    if v.get("defined_position"):
        notes.append("a different allele at a position the definitions read")
    if v.get("low_confidence"):
        notes.append(f"in a {v['low_confidence']} region")
    genes = v.get("genes") or [v.get("gene")]
    am = v.get("alphamissense") or {}
    # The tier's words alone; the score goes under them, with the consequence.
    effect = (v.get("label") or "").split(" (AlphaMissense")[0]
    consequence = v.get("consequence") or ""
    if am.get("score") is not None:
        consequence += f", AlphaMissense {am['score']:.2f}"
    return {
        "genes": ", ".join(genes),
        # c. on one line and p. on the next: "c.2788C>T (p.Gln930Ter)" broke inside
        # the p. name in the PDF's column.
        "variant": v.get("hgvs_c") or (None if v.get("hgvs_p") else _genomic(v)),
        "protein": v.get("hgvs_p"),
        "genomic": _genomic(v),
        "transcript": v.get("refseq") or v.get("transcript"),
        "consequence": consequence,
        "zygosity": _zygosity(v, male),
        "effect": effect,
        "tier": v.get("tier"),
        "frequency": _frequency(v),
        "clinvar": _clinvar(v),
        "notes": notes,
    }


def _xy_artefact(v: Mapping[str, Any], male: Optional[bool]) -> bool:
    """A heterozygous call on chrX outside its pseudoautosomal regions in a male: two
    alleles where there is one copy, so a mapping artefact, not a variant."""
    pos = int(v.get("pos") or 0)
    par = pos <= 2781479 or 155701383 <= pos
    return bool(male) and v.get("chrom") == "chrX" and not par and v.get("zygosity") == "heterozygous"


def context(result: Optional[Mapping[str, Any]], pypgx_results: Any = None) -> Dict[str, Any]:
    """What the templates show: the section's status and rows, and the flagged genes."""
    if result is None:
        return {
            "status": "not_run",
            "reason": "It ran before ZaroPGx had the check.",
            "flagged": {},
        }
    status = result.get("status")
    if status != "done":
        return {
            "status": status or "failed",
            "reason": result.get("reason") or "The check did not complete.",
            "flagged": {},
        }
    male = sample_is_male(pypgx_results)
    variants = [v for v in result.get("variants") or [] if isinstance(v, Mapping)]
    artefacts = [v for v in variants if _xy_artefact(v, male)]
    variants = [v for v in variants if not _xy_artefact(v, male)]
    shown = [v for v in variants if v.get("tier") in SHOWN_TIERS]
    others = {
        "none": sum(1 for v in variants if v.get("tier") == "none"),
        "common": sum(1 for v in variants if v.get("tier") == "common"),
        "low_quality": sum(1 for v in variants if v.get("tier") == "low_quality") + len(artefacts),
    }
    flagged: Dict[str, List[Dict[str, Any]]] = {}
    for v in shown:
        if v.get("tier") not in FLAG_TIERS:
            continue
        for gene in v.get("genes") or [v.get("gene")]:
            if gene in (result.get("flagged_genes") or {}):
                flagged.setdefault(gene, []).append(v)
    sources = (result.get("annotation") or {}).get("sources") or {}
    return {
        "status": "done",
        "genes_checked": len(result.get("genes_checked") or []),
        "genes_without_transcript": result.get("genes_without_transcript") or [],
        "found": len(variants) + len(artefacts),
        "listed": result.get("variants_listed") or 0,
        "rows": [_row(v, male) for v in shown],
        "others": others,
        "flagged": flagged,
        "dpyd_caveat": DPYD_CAVEAT if "DPYD" in flagged else None,
        "sources": {k: (s or {}).get("version") for k, s in sources.items()},
    }


def _effect_words(variants: Sequence[Mapping[str, Any]]) -> str:
    """"cause loss of function" or "damage the protein", for the strongest of them."""
    if any(v.get("tier") == "A" for v in variants):
        return "cause loss of function"
    return "damage the protein"


def gene_flag(gene: str, variants: Sequence[Mapping[str, Any]]) -> Dict[str, str]:
    """The note beside a flagged gene's call, the Executive Summary's note, and the
    FHIR genotype's note."""
    names = ", ".join(_variant_name(v) for v in variants)
    effect = _effect_words(variants)
    one = len(variants) == 1
    loss = effect == "cause loss of function"
    return {
        # Short, as the copy-number note beside it is: the section has the variants.
        "call_note": (
            f"{'Unlisted variant' if one else 'Unlisted variants'}: predicted "
            f"{'loss of function' if loss else 'damaging'} (see {SECTION})"
        ),
        "note": (
            f"{names}, which no allele definition lists, {'is' if one else 'are'} predicted "
            f"to {effect}. The phenotype here assumes {'it does' if one else 'they do'} "
            f"nothing. See {SECTION}."
        ),
        "fhir_note": (
            f"{names} in {gene}, which no allele definition lists, {'is' if one else 'are'} "
            f"predicted to {effect} (computational prediction, not validated for clinical "
            f"use). This genotype and its phenotype do not account for {'it' if one else 'them'}."
        ),
    }


def annotate_genes(genes: Sequence[Any], ctx: Mapping[str, Any]) -> None:
    """Put each flagged gene's notes on its rows (``unlisted_flag``)."""
    flagged = ctx.get("flagged") or {}
    for row in genes:
        if isinstance(row, dict) and row.get("gene") in flagged:
            row["unlisted_flag"] = gene_flag(row["gene"], flagged[row["gene"]])


def alert(ctx: Mapping[str, Any]) -> Optional[str]:
    """Alerts and Warnings' entry: the genes flagged, or None."""
    flagged = ctx.get("flagged") or {}
    if not flagged:
        return None
    items = "".join(
        f"<li><strong>{html.escape(gene)}</strong>: "
        f"{html.escape(', '.join(_variant_name(v) for v in variants))}, predicted to "
        f"{_effect_words(variants)}</li>"
        for gene, variants in flagged.items()
    )
    return (
        "<p><strong>Variants no allele definition lists, predicted to change the "
        "protein</strong></p>"
        f"<ul>{items}</ul>"
        "<p>The calls and phenotypes of these genes do not account for them: PharmCAT "
        "and PyPGx read only the variants their definitions name. The predictions are "
        "computational and can be wrong in either direction, and whether a variant lies "
        "on the same chromosome copy as an allele in the call is not known. "
        f"{SECTION} has the details.</p>"
    )
