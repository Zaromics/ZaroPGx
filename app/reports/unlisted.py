"""The variants in the pharmacogenes that no allele definition lists, in the report.

The check itself runs in the pipeline (docker/pypgx's unlisted_variants.py, Nextflow's
UnlistedVariants) and leaves unlisted_variants.json in the report directory. This
module reads it and says, in the report's words:

- a section listing the variants that may change the protein, and how many others
  were found and why they are not listed there;
- beside each marked gene's call, and in the Executive Summary's notes, that the call
  does not account for the variant, in words that follow its tier: a predicted loss
  of function does not read like an effect that cannot be predicted;
- one alert naming the marked genes.

Every variant the section tables marks its gene, whatever its tier and whatever the
gene: for CYP2D6, CYP2A6, GSTM1 and UGT2B17 the mark adds that the variant may be a
near-identical neighbour's (SIMILAR_GENES). Calls and phenotypes are never changed: a
prediction is not a measurement, and the phase of the variant against the call's
alleles is not known.
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
# The tiers the section's table lists, each of which marks its gene's call; the rest
# are counted below it.
SHOWN_TIERS = ("A", "A-low", "B", "B-low", "C")
FLAG_TIERS = SHOWN_TIERS
# Each tier's words in a note: what the variant may do.
EFFECT = {
    "A": "predicted to cause loss of function",
    "A-low": "possibly causing loss of function (a low-confidence prediction)",
    "B": "predicted to damage the protein",
    "B-low": "possibly damaging the protein",
    "C": "of uncertain effect",
}
# The same, short, for the note under the call in a narrow table column.
EFFECT_SHORT = {
    "A": "predicted loss of function",
    "A-low": "possible loss of function",
    "B": "predicted damaging",
    "B-low": "possibly damaging",
    "C": "effect uncertain",
}
# Genes with a near-identical gene beside them, whose reads can align to them, or
# that are often deleted so that what aligns there is another gene's: a variant
# called in them may not be theirs.
SIMILAR_GENES = {
    "CYP2D6": "Reads of CYP2D7, a near-identical gene beside CYP2D6, can align to it, so the variant may be CYP2D7's.",
    "CYP2A6": "Reads of CYP2A7, a near-identical gene beside CYP2A6, can align to it, so the variant may be CYP2A7's.",
    "GSTM1": "GSTM1 is often deleted, and reads of the similar genes beside it (GSTM2, GSTM4) can align to it, so the variant may be theirs.",
    "UGT2B17": "UGT2B17 is often deleted, and reads of UGT2B15, a near-identical gene, can align to it, so the variant may be UGT2B15's.",
    "GSTT1": "GSTT1 is often deleted, and GRCh38 places it outside the primary assembly, where variant calls are less sure.",
}
# The same, short, for the variant's row in the section's table.
SIMILAR_SHORT = {
    "CYP2D6": "may be CYP2D7's",
    "CYP2A6": "may be CYP2A7's",
    "GSTM1": "may be GSTM2's or GSTM4's",
    "UGT2B17": "may be UGT2B15's",
    "GSTT1": "outside the primary assembly",
}
# PharmCAT's genes whose variants can also bear on a condition, not only on a drug's
# effect, with that condition: the section says so when it tables one of theirs.
DISEASE_GENES = {
    "CFTR": "cystic fibrosis",
    "RYR1": "malignant hyperthermia and some myopathies",
    "CACNA1S": "malignant hyperthermia and some myopathies",
    "F2": "thrombophilia or a bleeding disorder",
    "F5": "thrombophilia or a bleeding disorder",
}
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
    results = (
        pypgx_results.get("results") if isinstance(pypgx_results, Mapping) else None
    )
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
    notes += [
        SIMILAR_SHORT[g.upper()] for g in genes if g and g.upper() in SIMILAR_SHORT
    ]
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
    return (
        bool(male)
        and v.get("chrom") == "chrX"
        and not par
        and v.get("zygosity") == "heterozygous"
    )


def _names(items: Sequence[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def _disease_note(genes: Sequence[str]) -> Optional[str]:
    """That a tabled variant's gene is also a disease gene (DISEASE_GENES), or None."""
    if not genes:
        return None
    if len(genes) == 1:
        return (
            f"{genes[0]} is also a disease gene: a variant in it can bear on "
            f"{DISEASE_GENES[genes[0]]} as well as on a drug's effect."
        )
    conditions = "; ".join(f"{g}, {DISEASE_GENES[g]}" for g in genes)
    return (
        f"{_names(list(genes))} are also disease genes: a variant in them can bear on a "
        f"condition as well as on a drug's effect ({conditions})."
    )


def context(
    result: Optional[Mapping[str, Any]], pypgx_results: Any = None
) -> Dict[str, Any]:
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
        "low_quality": sum(1 for v in variants if v.get("tier") == "low_quality")
        + len(artefacts),
    }
    flagged: Dict[str, List[Dict[str, Any]]] = {}
    for v in shown:
        if v.get("tier") not in FLAG_TIERS:
            continue
        for gene in v.get("genes") or [v.get("gene")]:
            if gene in (result.get("flagged_genes") or {}):
                flagged.setdefault(gene, []).append(v)
    sources = (result.get("annotation") or {}).get("sources") or {}
    tabled = list(
        dict.fromkeys(g for v in shown for g in (v.get("genes") or [v.get("gene")]))
    )
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
        "disease_note": _disease_note([g for g in tabled if g in DISEASE_GENES]),
        "sources": {k: (s or {}).get("version") for k, s in sources.items()},
    }


def _strongest(variants: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    """The variant whose tier says most, ClinVar's word before a prediction's."""
    return min(
        variants,
        key=lambda v: (
            SHOWN_TIERS.index(v.get("tier")) if v.get("tier") in SHOWN_TIERS else 99,
            not v.get("clinvar_basis"),
        ),
    )


def _effect(v: Mapping[str, Any]) -> str:
    """What the variant may do, in its tier's words: "predicted to damage the protein
    (AlphaMissense 0.96)", "classified pathogenic by ClinVar", "of uncertain effect
    (inframe deletion)"."""
    tier = v.get("tier")
    if tier == "A" and v.get("clinvar_basis"):
        label = str(v.get("label") or "")
        if label.startswith("Pathogenic for "):
            return f"classified by ClinVar as pathogenic for {label[len('Pathogenic for '):].split(', by ClinVar')[0]}"
        return "classified by ClinVar as pathogenic, a loss of function"
    text = EFFECT.get(tier, "")
    am = (v.get("alphamissense") or {}).get("score")
    low = v.get("low_confidence")
    if tier == "B" and am is not None:
        text += f" (AlphaMissense {am:.2f})"
    elif tier == "B-low" and am is not None:
        # Low either way: a score under the cut-off, or one over it in a region
        # where the call is less sure.
        why = (
            f"in a {low} region"
            if low and am > 0.564
            else "under its likely-pathogenic cut-off of 0.564"
        )
        text += f" (AlphaMissense {am:.2f}, {why})"
    elif tier == "A-low" and low:
        text = f"possibly causing loss of function (a low-confidence prediction: in a {low} region)"
    elif tier == "C" and v.get("consequence"):
        text += f" ({v['consequence']})"
    return text


def _effect_short(v: Mapping[str, Any]) -> str:
    if v.get("tier") == "A" and v.get("clinvar_basis"):
        return "pathogenic in ClinVar"
    return EFFECT_SHORT.get(v.get("tier"), "")


def _fhir_variant(v: Mapping[str, Any]) -> Dict[str, Any]:
    """What the FHIR export's Variant observation states of it."""
    keys = (
        "chrom",
        "pos",
        "ref",
        "alt",
        "gene",
        "zygosity",
        "hgvs_c",
        "hgvs_p",
        "refseq",
        "depth",
        "alt_reads",
        "tier",
        "label",
    )
    out = {k: v.get(k) for k in keys if v.get(k) not in (None, "")}
    out["effect"] = _effect(v)
    clinvar = v.get("clinvar") or {}
    if clinvar.get("id"):
        out["clinvar_id"] = str(clinvar["id"])
    # ClinVar's own classification, where it is for the gene's drug response; the
    # export codes every other variant as of uncertain significance.
    if v.get("clinvar_drug_response"):
        out["clinvar_significance"] = clinvar.get("significance")
        out["clinvar_conditions"] = clinvar.get("conditions") or []
    return out


def gene_flag(gene: str, variants: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """The note beside a marked gene's call, the Executive Summary's note, the FHIR
    genotype's note, and the variants the FHIR export states (``variants``)."""
    one = len(variants) == 1
    items = "; ".join(f"{_variant_name(v)}, {_effect(v)}" for v in variants)
    strongest = _effect_short(_strongest(variants))
    similar = SIMILAR_GENES.get(gene.upper())
    return {
        # Short, as the copy-number note beside it is: the section has the variants.
        "call_note": (
            f"{'Unlisted variant' if one else f'{len(variants)} unlisted variants'}: "
            f"{strongest if one else 'strongest ' + strongest}"
            f"{', may be another gene' + chr(39) + 's' if similar else ''} (see {SECTION})"
        ),
        "note": (
            f"{items}: no allele definition lists {'it' if one else 'them'}, and the "
            f"phenotype here assumes {'it does' if one else 'they do'} nothing."
            f"{' ' + similar if similar else ''} See {SECTION}."
        ),
        "fhir_note": (
            f"In {gene}, {items}: no allele definition lists {'it' if one else 'them'} "
            f"(computational prediction, not validated for clinical use). This genotype "
            f"and its phenotype do not account for {'it' if one else 'them'}."
            f"{' ' + similar if similar else ''}"
        ),
        "variants": [_fhir_variant(v) for v in variants],
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
        f"{html.escape('; '.join(f'{_variant_name(v)}, {_effect(v)}' for v in variants))}"
        f"{html.escape('. ' + SIMILAR_GENES[gene.upper()]) if gene.upper() in SIMILAR_GENES else ''}</li>"
        for gene, variants in flagged.items()
    )
    return (
        "<p><strong>Variants no allele definition lists, which these calls do not "
        "account for</strong></p>"
        f"<ul>{items}</ul>"
        "<p>PharmCAT and PyPGx read only the variants their definitions name. Each "
        "effect above is a computational prediction, or ClinVar's classification, and "
        "none has been validated for clinical use; a prediction can be wrong in either "
        "direction, and whether a variant lies on the same chromosome copy as an allele "
        f"in the call is not known. {SECTION} has the details.</p>"
    )
