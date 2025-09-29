from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict


REPO_ROOT = Path(__file__).resolve().parents[2]
MAP_JSON = REPO_ROOT / "lexicon" / "allele_map_pypgx_to_pharmcat.json"


_SPACE_RE = re.compile(r"\s+")
_PLUS_RE = re.compile(r"\s*\+\s*")
_COMMA_RE = re.compile(r"\s*,\s*")


def _clean_token(token: str) -> str:
    t = token.strip()
    t = _SPACE_RE.sub(" ", t)
    t = _PLUS_RE.sub(" + ", t)
    t = _COMMA_RE.sub(", ", t)
    return t


def load_map() -> Dict[str, Dict[str, str]]:
    if not MAP_JSON.exists():
        raise FileNotFoundError("Run build_allele_mapping.py to generate the JSON map first.")
    with MAP_JSON.open("r", encoding="utf-8") as f:
        return json.load(f)


def pypgx_to_pharmcat(gene: str, pypgx_token: str, mapping: Dict[str, Dict[str, str]] | None = None) -> str:
    if mapping is None:
        mapping = load_map()
    g = gene.strip()
    t = _clean_token(pypgx_token)
    return mapping.get(g, {}).get(t, t)


