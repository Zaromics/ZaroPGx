"""The RxNorm code for a drug PharmCAT names, for the FHIR export.

PharmCAT identifies a drug by its ClinPGx accession (``PA448320``) and its name, and a
receiving EHR matches medications by RxNorm. ``config/rxnorm_drugs.json`` maps every
drug the pinned PharmCAT can report to the RxNorm concept of the same name, looked up
in NLM's RxNav (scripts/build_rxnorm_drugs.py, which says how and what it leaves
uncoded). A drug the table has no code for is sent as text, as every drug was before:
a code is added only where one was found, never guessed.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from app.services.fhir import terminology

logger = logging.getLogger(__name__)

TABLE = Path(__file__).resolve().parents[3] / "config" / "rxnorm_drugs.json"


@lru_cache(maxsize=1)
def _table() -> Tuple[Dict[str, dict], Dict[str, dict]]:
    """(by accession, by lower-cased name). A table that cannot be read leaves every
    drug as text, and says so once."""
    try:
        rows = json.loads(TABLE.read_text(encoding="utf-8"))["drugs"]
    except (OSError, ValueError, KeyError) as exc:
        logger.warning(f"No RxNorm codes in the FHIR export: {TABLE} unreadable: {exc}")
        return {}, {}
    by_name: Dict[str, dict] = {}
    seen = set()
    for row in rows.values():
        key = str(row.get("name") or "").strip().lower()
        if key in seen:
            by_name.pop(key, None)  # a name two accessions share settles nothing
        else:
            seen.add(key)
            by_name[key] = row
    return rows, by_name


def coding(drug_id: Any, name: Any) -> Optional[Dict[str, str]]:
    """The RxNorm Coding for the drug, by accession, else by exact name; None if the
    table has no code for it."""
    by_id, by_name = _table()
    row = by_id.get(str(drug_id or "").strip()) or by_name.get(
        str(name or "").strip().lower()
    )
    if not row or not row.get("rxcui"):
        return None
    return {
        "system": terminology.RXNORM,
        "code": str(row["rxcui"]),
        "display": str(row.get("rxnorm_name") or row.get("name")),
    }


def drug_concept(name: str, drug_id: Any = None) -> Dict[str, Any]:
    """The CodeableConcept for a drug: PharmCAT's name as text, with RxNorm's coding
    where the table has one."""
    concept: Dict[str, Any] = {"text": name}
    found = coding(drug_id, name)
    if found:
        concept["coding"] = [found]
    return concept
