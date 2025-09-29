import csv
import io
import os
from typing import Any, Dict, List, Tuple


def _normalize_header(name: str) -> str:
    return (name or "").strip().lower().replace(" ", "_")


def parse_pharmcat_tsv(tsv_path: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Parse a PharmCAT TSV report and extract diplotypes and drug recommendations.

    This parser is resilient to header variations by matching common column names.

    Returns:
        diplotypes: List of { gene, diplotype, phenotype, activity_score? }
        recommendations: List of { gene, drug, guideline, recommendation, classification }
    """
    if not os.path.exists(tsv_path):
        return [], []

    # Read entire file, skipping empty/comment lines for robust CSV sniffing
    with open(tsv_path, "r", encoding="utf-8", errors="replace") as f:
        raw = f.read()

    # Some generators may include comments; strip them but keep header
    lines = [ln for ln in raw.splitlines() if ln.strip() != "" and not ln.lstrip().startswith("#")]
    if not lines:
        return [], []

    # Identify the real header line (skip preamble like 'PharmCAT 3.0.1')
    header_start_idx = 0
    for idx, ln in enumerate(lines):
        if "\t" in ln:
            parts = [p.strip() for p in ln.split("\t")]
            norm = [_normalize_header(p) for p in parts]
            if any(col in {"gene", "gene_symbol", "locus", "gene_name"} for col in norm):
                header_start_idx = idx
                break
    # Use csv with delimiter='\t' starting at the detected header
    reader = csv.DictReader(io.StringIO("\n".join(lines[header_start_idx:])), delimiter="\t")
    headers = [_normalize_header(h) for h in (reader.fieldnames or [])]

    # Header candidates
    def find_col(candidates: List[str]) -> str | None:
        for cand in candidates:
            lc = _normalize_header(cand)
            if lc in headers:
                return (reader.fieldnames or [])[headers.index(lc)]
        return None

    gene_col = find_col(["gene", "gene_symbol", "locus", "gene_name"]) or "gene"
    dip_col = find_col(["diplotype", "source_diplotype", "call", "star_alleles", "genotype", "result"])  # broad
    pheno_col = find_col(["phenotype", "predicted_phenotype", "phenotype_call"])  # common variants
    as_col = find_col(["activity_score", "activityscore", "as"])  # optional
    # Recommendation Lookup columns for Executive Summary
    rec_dip_col = find_col([
        "recommendation_lookup_diplotype",
        "recommendation\u00a0lookup\u00a0diplotype",
        "recommendation lookup diplotype",
    ])
    rec_pheno_col = find_col([
        "recommendation_lookup_phenotype",
        "recommendation\u00a0lookup\u00a0phenotype",
        "recommendation lookup phenotype",
    ])
    rec_as_col = find_col([
        "recommendation_lookup_activity_score",
        "recommendation\u00a0lookup\u00a0activity\u00a0score",
        "recommendation lookup activity score",
        "rec_lookup_activity_score",
    ])
    drug_col = find_col(["drug", "medication"])  # optional
    guideline_col = find_col(["guideline", "source", "guideline_source"])  # optional
    rec_col = find_col(["recommendation", "action", "recommendation_text"])  # optional
    class_col = find_col(["classification", "level", "strength"])  # optional

    diplotypes: List[Dict[str, Any]] = []
    recommendations: List[Dict[str, Any]] = []

    for row in reader:
        # Normalize value access with graceful fallbacks
        def gv(col: str | None) -> str:
            return (row.get(col) if col else None) or ""

        gene = (gv(gene_col) or "").strip()
        if not gene:
            # Skip rows without gene; these may be section headers or notes
            continue

        diplotype_val = (gv(dip_col) or "").strip() if dip_col else ""
        phenotype_val = (gv(pheno_col) or "").strip() if pheno_col else ""
        activity_score_val = (gv(as_col) or "").strip() if as_col else ""

        # Recommendation lookup vals (may be present even when main cols are empty)
        rec_dip_val = (gv(rec_dip_col) or "").strip() if rec_dip_col else ""
        rec_pheno_val = (gv(rec_pheno_col) or "").strip() if rec_pheno_col else ""
        rec_as_val = (gv(rec_as_col) or "").strip() if rec_as_col else ""

        # If any informative field present (from main or rec-lookup), register an entry
        if diplotype_val or phenotype_val or activity_score_val or rec_dip_val or rec_pheno_val or rec_as_val:
            entry: Dict[str, Any] = {
                "gene": gene,
                "diplotype": diplotype_val or "Unknown",
                "phenotype": phenotype_val or "Unknown",
            }
            if activity_score_val:
                try:
                    entry["activity_score"] = float(activity_score_val)
                except Exception:
                    entry["activity_score"] = activity_score_val
            # Attach Recommendation Lookup fields if present on the row
            if rec_dip_col:
                entry["rec_lookup_diplotype"] = rec_dip_val
            if rec_pheno_col:
                entry["rec_lookup_phenotype"] = rec_pheno_val
            if rec_as_col:
                val = rec_as_val
                if val:
                    try:
                        entry["rec_lookup_activity_score"] = float(val)
                    except Exception:
                        entry["rec_lookup_activity_score"] = val
            diplotypes.append(entry)

        # If drug recommendation columns present on the same row, capture them
        if drug_col and rec_col:
            drug = (gv(drug_col) or "").strip()
            rec = (gv(rec_col) or "").strip()
            if drug or rec:
                recommendations.append({
                    "gene": gene,
                    "drug": drug or "Unknown",
                    "guideline": (gv(guideline_col) or "").strip() if guideline_col else "",
                    "recommendation": rec or "See guideline",
                    "classification": (gv(class_col) or "").strip() if class_col else "Unknown",
                })

    return diplotypes, recommendations


