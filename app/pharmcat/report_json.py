"""Unified PharmCAT report.json gene walkers.

PharmCAT has emitted two live ``genes`` shapes:

* **nested** — ``genes → {CPIC|DPWG|FDA} → gene_symbol → gene_data`` (≤2.x)
* **flat** — ``genes → gene_symbol → gene_data`` (3.x / 3.4.0)

``normalize_pharmcat_results`` and ``PharmCATParser._parse_genes`` used to sniff
and walk these independently. All format detection and gene iteration belongs
here so Wave 4 unification has a single contract under test.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterator, Literal, Mapping, Optional

FormatName = Literal["nested", "flat", "empty"]

GUIDELINE_SOURCES = frozenset({"CPIC", "DPWG", "FDA"})
_GENE_SHAPE_KEYS = frozenset(
    {
        "geneSymbol",
        "sourceDiplotypes",
        "recommendationDiplotypes",
        "alleleDefinitionVersion",
    }
)


@dataclass(frozen=True)
class GeneBlock:
    """One gene payload plus the guideline source it was attributed to."""

    gene_symbol: str
    gene_data: Dict[str, Any]
    source: str


def detect_format(genes_section: Optional[Mapping[str, Any]]) -> FormatName:
    """Return ``nested``, ``flat``, or ``empty`` for a PharmCAT ``genes`` object."""
    if not genes_section:
        return "empty"

    first_key = next(iter(genes_section.keys()))
    if first_key in GUIDELINE_SOURCES:
        return "nested"

    first_value = genes_section.get(first_key)
    if isinstance(first_value, dict) and _GENE_SHAPE_KEYS.intersection(first_value):
        return "flat"

    # Ambiguous non-guideline top keys without gene fields → treat as nested
    # source buckets (matches PharmCATParser's historical fallback).
    return "nested"


def iter_gene_blocks(
    genes_section: Optional[Mapping[str, Any]],
) -> Iterator[GeneBlock]:
    """Yield ``(gene_symbol, gene_data, source)`` for every gene in either shape."""
    fmt = detect_format(genes_section)
    if fmt == "empty" or genes_section is None:
        return

    if fmt == "flat":
        for gene_symbol, gene_data in genes_section.items():
            if not isinstance(gene_data, dict):
                continue
            source = gene_data.get("phenotypeSource") or "CPIC"
            yield GeneBlock(gene_symbol, gene_data, source)
        return

    # nested
    for source, genes in genes_section.items():
        if not isinstance(genes, dict):
            continue
        for gene_symbol, gene_data in genes.items():
            if not isinstance(gene_data, dict):
                continue
            yield GeneBlock(gene_symbol, gene_data, source)


def extract_recommendation_call(gene_data: Mapping[str, Any]) -> Dict[str, Any]:
    """Pull the primary recommendation diplotype/phenotype/activity from a gene block.

    Returns the same defaults ``normalize_pharmcat_results`` historically used when
    ``recommendationDiplotypes`` is missing or empty.
    """
    diplotype = "Unknown/Unknown"
    phenotype = "Unknown"
    activity_score = None

    rec_list = gene_data.get("recommendationDiplotypes")
    if not isinstance(rec_list, list) or not rec_list:
        return {
            "diplotype": diplotype,
            "phenotype": phenotype,
            "activity_score": activity_score,
        }

    rec = rec_list[0]
    if not isinstance(rec, dict):
        return {
            "diplotype": diplotype,
            "phenotype": phenotype,
            "activity_score": activity_score,
        }

    if "label" in rec:
        diplotype = rec["label"]

    if "phenotypes" in rec:
        phenotypes = rec["phenotypes"]
        if isinstance(phenotypes, list):
            phenotype = ", ".join(str(p) for p in phenotypes)
        else:
            phenotype = str(phenotypes)

    if "activityScore" in rec:
        activity_score = rec["activityScore"]

    return {
        "diplotype": diplotype,
        "phenotype": phenotype,
        "activity_score": activity_score,
    }
