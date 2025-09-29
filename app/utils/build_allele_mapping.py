"""
Generate a PyPGx → PharmCAT allele/diplotype translation map.

Inputs:
  - lexicon/pharmcat_phenotypes.tsv (tab-separated)

Outputs (not written unless you run this module):
  - lexicon/allele_map_pypgx_to_pharmcat.csv
  - lexicon/allele_map_pypgx_to_pharmcat.json

Notes
  - Only genes present in pharmcat_phenotypes.tsv are considered
  - HLA-A and HLA-B are skipped as requested
  - For most loci, PharmCAT and PyPGx use identical allele tokens; we provide
    normalization rules and identity mapping by default so the file is a
    deterministic, auditable bridge between tools.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Tuple


REPO_ROOT = Path(__file__).resolve().parents[2]
PHARMCAT_TSV = REPO_ROOT / "lexicon" / "pharmcat_phenotypes.tsv"
OUT_CSV = REPO_ROOT / "lexicon" / "allele_map_pypgx_to_pharmcat.csv"
OUT_JSON = REPO_ROOT / "lexicon" / "allele_map_pypgx_to_pharmcat.json"

# Genes to exclude per requirements
EXCLUDED_GENES = {"HLA-A", "HLA-B"}

# Extra synonyms observed in PyPGx outputs that PharmCAT expects in a different
# canonical form. These help avoid warnings like:
# [pharmcat-pipeline] WARNING: Undocumented <GENE> named variant in outside call: <TOKEN>
GENE_SPECIFIC_SYNONYMS: dict[str, dict[str, str]] = {
    # ABCG2 expects rs2231142 reference/variant forms
    "ABCG2": {
        "Reference": "rs2231142 reference (G)",
        "rs2231142": "rs2231142 variant (T)",
    },
    # IFNL3 expects rs12979860 reference/variant forms
    "IFNL3": {
        "Reference": "rs12979860 reference (C)",
        "rs12979860": "rs12979860 variant (T)",
    },
    # VKORC1 expects rs9923231 reference/variant forms
    "VKORC1": {
        "Reference": "rs9923231 reference (C)",
        "rs9923231": "rs9923231 variant (T)",
    },
}


@dataclass(frozen=True)
class AlleleMapping:
    gene: str
    pypgx: str
    pharmcat: str
    notes: str = ""


def _clean_token(token: str) -> str:
    """Trim, collapse whitespace, and standardize separators within a token."""
    t = token.strip()
    # Collapse multiple spaces
    t = re.sub(r"\s+", " ", t)
    # Normalize plus separators in hybrid alleles (e.g., "*36 + *10")
    t = re.sub(r"\s*\+\s*", " + ", t)
    # Normalize commas inside multi-variant tokens (keep comma+space)
    t = re.sub(r"\s*,\s*", ", ", t)
    return t


def normalize_for_pypgx(token: str) -> str:
    """
    Convert a PharmCAT allele/diplotype token to the form PyPGx typically uses.

    Today, most tokens are identical across tools. This function is a safe
    place to add adjustments if we encounter known divergences.
    """
    t = _clean_token(token)
    # Common harmless normalizations (kept identity by default):
    # - Keep unicode '≥' as-is because PyPGx enumerations use '*1x≥3'
    # - Ensure CNV syntax like 'x2' remains intact (no spaces)
    # - Ensure plus spacing around hybrids '*36 + *10'
    # Nothing to change for now; return cleaned token.
    return t


def _split_named_alleles(field: str) -> List[str]:
    """
    Named Alleles column contains semicolon-separated entries.
    Return a list of cleaned tokens, skipping empties and placeholder text.
    """
    if not field:
        return []
    parts = [p.strip() for p in field.split(";")]
    tokens: List[str] = []
    for p in parts:
        if not p:
            continue
        tokens.append(_clean_token(p))
    return tokens


def read_pharmcat_named_alleles(tsv_path: Path) -> List[Tuple[str, str]]:
    """Yield (gene, allele_token) pairs from the PharmCAT TSV, skipping HLA genes."""
    rows: List[Tuple[str, str]] = []
    with tsv_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        # Expect a column named 'Gene' and 'Named Alleles'
        for rec in reader:
            gene = (rec.get("Gene") or "").strip()
            if not gene or gene in EXCLUDED_GENES:
                continue
            named = rec.get("Named Alleles") or ""
            for token in _split_named_alleles(named):
                rows.append((gene, token))
    return rows


def build_mapping(pairs: Iterable[Tuple[str, str]]) -> List[AlleleMapping]:
    """Create one-to-one mappings for each PyPGx token to PharmCAT token.

    We use PharmCAT's Named Alleles as the canonical list and assume PyPGx
    uses identical or trivially normalized tokens for these genes. Thus we
    treat the normalized token as the PyPGx key, and the original token as
    the PharmCAT value.
    """
    results: List[AlleleMapping] = []
    seen = set()
    for gene, pharmcat_tok in pairs:
        pypgx_tok = normalize_for_pypgx(pharmcat_tok)
        key = (gene, pypgx_tok, pharmcat_tok)
        if key in seen:
            continue
        seen.add(key)
        results.append(AlleleMapping(gene=gene, pypgx=pypgx_tok, pharmcat=pharmcat_tok, notes=""))
    # Add gene-specific synonyms
    for gene, syns in GENE_SPECIFIC_SYNONYMS.items():
        for pypgx_tok_raw, pharmcat_tok_raw in syns.items():
            pypgx_tok = normalize_for_pypgx(pypgx_tok_raw)
            pharmcat_tok = _clean_token(pharmcat_tok_raw)
            key = (gene, pypgx_tok, pharmcat_tok)
            if key in seen:
                continue
            seen.add(key)
            results.append(AlleleMapping(gene=gene, pypgx=pypgx_tok, pharmcat=pharmcat_tok, notes="synonym"))

    # Stable sort by gene, then PyPGx token
    results.sort(key=lambda m: (m.gene, m.pypgx.lower()))
    return results


def write_outputs(mappings: List[AlleleMapping]) -> None:
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    # CSV
    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Gene", "PyPGx", "PharmCAT", "Notes"])
        for m in mappings:
            writer.writerow([m.gene, m.pypgx, m.pharmcat, m.notes])
    # JSON
    # Structure: { gene: { pypgx_token: pharmcat_token, ... }, ... }
    out: dict[str, dict[str, str]] = {}
    for m in mappings:
        out.setdefault(m.gene, {})[m.pypgx] = m.pharmcat
    with OUT_JSON.open("w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)


def main() -> None:
    if not PHARMCAT_TSV.exists():
        raise FileNotFoundError(f"Missing input: {PHARMCAT_TSV}")
    pairs = read_pharmcat_named_alleles(PHARMCAT_TSV)
    mappings = build_mapping(pairs)
    write_outputs(mappings)
    print(f"Wrote {OUT_CSV.relative_to(REPO_ROOT)} and {OUT_JSON.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()


