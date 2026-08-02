from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

_MAP_NAME = "allele_map_pypgx_to_pharmcat.json"

SYNONYM_GENES = frozenset({"ABCG2", "IFNL3", "VKORC1"})

_SPACE_RE = re.compile(r"\s+")
_PLUS_RE = re.compile(r"\s*\+\s*")
_COMMA_RE = re.compile(r"\s*,\s*")


def _clean_token(token: str) -> str:
    t = token.strip()
    t = _SPACE_RE.sub(" ", t)
    t = _PLUS_RE.sub(" + ", t)
    t = _COMMA_RE.sub(", ", t)
    return t


def _repo_lexicon_candidate(module_file: Path | None = None) -> Path | None:
    """Return <repo>/lexicon/<map> when this module lives at app/utils/.

    PharmCAT image copies this file to /lexicon-lib/allele_translate.py — that
    path has no app/utils parents, so never index parents[2] at import time.
    """
    here = (module_file or Path(__file__)).resolve()
    if here.parent.name != "utils" or here.parent.parent.name != "app":
        return None
    if len(here.parents) < 3:
        return None
    return here.parents[2] / "lexicon" / _MAP_NAME


def resolve_map_path() -> Path:
    env = os.environ.get("ALLELE_MAP_JSON", "").strip()
    if env:
        return Path(env)
    candidates: List[Path] = []
    repo_map = _repo_lexicon_candidate()
    if repo_map is not None:
        candidates.append(repo_map)
    candidates.append(Path("/lexicon") / _MAP_NAME)
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


def load_map(path: Path | None = None) -> Dict[str, Dict[str, str]]:
    map_path = path or resolve_map_path()
    if not map_path.exists():
        raise FileNotFoundError(
            f"Allele map not found at {map_path}. "
            "Run build_allele_mapping.py or set ALLELE_MAP_JSON."
        )
    with map_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def translate_haplotype(
    gene: str, token: str, mapping: Dict[str, Dict[str, str]]
) -> Tuple[str, bool]:
    g = gene.strip()
    t = _clean_token(token)
    gene_map = mapping.get(g, {})
    if t in gene_map:
        return gene_map[t], True
    return t, False


def pypgx_to_pharmcat(
    gene: str, pypgx_token: str, mapping: Dict[str, Dict[str, str]] | None = None
) -> str:
    if mapping is None:
        mapping = load_map()
    out, _found = translate_haplotype(gene, pypgx_token, mapping)
    return out


def translate_diplotype(
    gene: str, diplotype: str, mapping: Dict[str, Dict[str, str]]
) -> Tuple[str, List[str]]:
    raw = diplotype.strip()
    if not raw:
        return raw, []
    parts = raw.split("/")
    out_parts: List[str] = []
    unmapped: List[str] = []
    for part in parts:
        translated, found = translate_haplotype(gene, part, mapping)
        if not found and _clean_token(part):
            unmapped.append(_clean_token(part))
        out_parts.append(translated)
    return "/".join(out_parts), unmapped


def translate_outside_tsv_text(
    text: str, mapping: Dict[str, Dict[str, str]] | None = None
) -> str:
    if mapping is None:
        mapping = load_map()
    lines_out: List[str] = []
    for line in text.splitlines(keepends=True):
        raw = line.rstrip("\r\n")
        ending = line[len(raw) :]
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            lines_out.append(line if line.endswith(("\n", "\r")) else raw + ending)
            continue
        parts = raw.split("\t")
        gene = parts[0].strip() if parts else ""
        if gene.lower() == "gene":
            lines_out.append(raw + ending)
            continue
        if gene in SYNONYM_GENES and len(parts) >= 2 and parts[1].strip():
            new_dip, unmapped = translate_diplotype(gene, parts[1], mapping)
            for tok in unmapped:
                logger.warning(
                    "Unmapped outside-call haplotype for %s: %r (pass-through)",
                    gene,
                    tok,
                )
            parts[1] = new_dip
            lines_out.append("\t".join(parts) + ending)
        else:
            lines_out.append(raw + ending)
    return "".join(lines_out)


def translate_outside_tsv_file(
    path: str | Path, mapping: Dict[str, Dict[str, str]] | None = None
) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    p.write_text(translate_outside_tsv_text(text, mapping), encoding="utf-8")
