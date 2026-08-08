"""Unified PharmCAT report.json gene walkers.

PharmCAT has emitted two live ``genes`` shapes:

* **nested** — ``genes → {CPIC|DPWG|FDA} → gene_symbol → gene_data`` (≤2.x)
* **flat** — ``genes → gene_symbol → gene_data`` (3.x / 3.4.0)

``normalize_pharmcat_results`` and ``PharmCATParser._parse_genes`` used to sniff
and walk these independently. All format detection, gene iteration, and
``sourceDiplotypes`` extraction belongs here so Wave 4 unification has a
single contract under test.
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
    """One gene payload, the guideline source it was attributed to, and how it
    was called.

    ``source`` is the guideline bucket (nested shape) or ``phenotypeSource``
    (flat shape), and is ``None`` when the run recorded none -- never invented.
    ``call_source`` is PharmCAT's ``callSource``: ``MATCHER`` when its own Named
    Allele Matcher produced the diplotype, ``OUTSIDE`` for a supplied outside
    call, ``NONE`` when no call was made.
    """

    gene_symbol: str
    gene_data: Dict[str, Any]
    source: Optional[str]
    call_source: Optional[str] = None


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
    """Yield a ``GeneBlock`` for every gene in either shape."""
    fmt = detect_format(genes_section)
    if fmt == "empty" or genes_section is None:
        return

    if fmt == "flat":
        for gene_symbol, gene_data in genes_section.items():
            if not isinstance(gene_data, dict):
                continue
            # PharmCAT 3.x emits phenotypeSource=None. Report that as unknown --
            # defaulting to "CPIC" fabricated a guideline attribution and
            # destroyed callSource downstream (BACKLOG 28 + 216).
            yield GeneBlock(
                gene_symbol,
                gene_data,
                gene_data.get("phenotypeSource"),
                gene_data.get("callSource"),
            )
        return

    # nested
    for source, genes in genes_section.items():
        if not isinstance(genes, dict):
            continue
        for gene_symbol, gene_data in genes.items():
            if not isinstance(gene_data, dict):
                continue
            yield GeneBlock(gene_symbol, gene_data, source, gene_data.get("callSource"))


def extract_source_call(gene_data: Mapping[str, Any]) -> Dict[str, Any]:
    """Pull the primary display diplotype/phenotype/activity from a gene block.

    Uses ``sourceDiplotypes`` (PharmCAT's displayed / "real" call). Defaults match
    the historical empty-list behaviour of the old recommendation helper.
    """
    diplotype = "Unknown/Unknown"
    phenotype = "Unknown"
    activity_score = None

    src_list = gene_data.get("sourceDiplotypes")
    if not isinstance(src_list, list) or not src_list:
        return {
            "diplotype": diplotype,
            "phenotype": phenotype,
            "activity_score": activity_score,
        }

    src = src_list[0]
    if not isinstance(src, dict):
        return {
            "diplotype": diplotype,
            "phenotype": phenotype,
            "activity_score": activity_score,
        }

    if "label" in src:
        diplotype = src["label"]

    if "phenotypes" in src:
        phenotypes = src["phenotypes"]
        if isinstance(phenotypes, list):
            phenotype = ", ".join(str(p) for p in phenotypes)
        else:
            phenotype = str(phenotypes)

    if "activityScore" in src:
        activity_score = src["activityScore"]

    return {
        "diplotype": diplotype,
        "phenotype": phenotype,
        "activity_score": activity_score,
    }


def _clean(value: Any) -> Optional[str]:
    """Return a stripped string, or ``None`` for absent/blank values."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def extract_matcher_metadata(
    report: Optional[Mapping[str, Any]],
) -> Dict[str, Optional[str]]:
    """Pull run-derived provenance out of a PharmCAT ``report.json`` payload.

    Three independent facts, each ``None`` when the run did not emit it:

    * ``genome_build`` -- ``matcherMetadata.genomeBuild`` (e.g. ``GRCh38.p14``)
    * ``named_allele_matcher_version`` -- ``matcherMetadata.namedAlleleMatcherVersion``
    * ``data_version`` -- top-level ``dataVersion`` (the guideline data release)

    ``matcherMetadata`` first appears in PharmCAT 3.4.0; v2-shaped reports carry
    ``dataVersion`` only. Callers must render each fact conditionally rather
    than substituting a placeholder.
    """
    if not isinstance(report, Mapping):
        return {
            "genome_build": None,
            "named_allele_matcher_version": None,
            "data_version": None,
        }

    matcher = report.get("matcherMetadata")
    if not isinstance(matcher, Mapping):
        matcher = {}

    return {
        "genome_build": _clean(matcher.get("genomeBuild")),
        "named_allele_matcher_version": _clean(
            matcher.get("namedAlleleMatcherVersion")
        ),
        "data_version": _clean(report.get("dataVersion")),
    }
