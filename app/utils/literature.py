# app/utils/literature.py
"""Turning PharmCAT's citation objects into something a person can read.

Lives here, not in ``app/reports/generator.py``, because both ends of the
pipeline need it and generator.py is downstream of the data service: formatting
at render time alone was not enough, since
``app/services/pharmcat_data_service.py`` had already collapsed each citation to
``str(c)`` before the template ever saw it. That is where the Python repr in the
report came from, and the model contract
(``ReportRecommendation.literature_references: List[str]``) is why it looked
deliberate.
"""

from __future__ import annotations

from typing import Any, List


def format_literature_reference(reference: Any) -> str:
    """One readable citation from PharmCAT's reference object.

    These were rendered with ``|join(", ")`` over a list of *dicts*, so Jinja fell
    back to Python's repr and every citation in the report read

        {'pmid': '21412232', 'year': 2011, 'title': '...', '_sameAs':
        'https://...', 'journal': 'Clinical pharmacology and therapeutics'}

    across roughly thirty-five pages -- braces, quoted keys, and ``_sameAs``, a
    JSON-LD internal that means nothing to a reader.

    Sentinels are dropped rather than printed. FDA Table entries arrive as
    ``pmid: None, year: -1, journal: None``, which the repr rendered verbatim: a
    citation claiming the year -1.
    """
    if not isinstance(reference, dict):
        return str(reference).strip()

    parts: List[str] = []

    title = str(reference.get("title") or "").strip().rstrip(".")
    if title:
        parts.append(title)

    journal = str(reference.get("journal") or "").strip()
    if journal and journal.lower() not in {"none", "null"}:
        parts.append(journal)

    try:
        year = int(reference.get("year"))
    except (TypeError, ValueError):
        year = 0
    if year > 0:
        parts.append(str(year))

    citation = ". ".join(parts)

    pmid = str(reference.get("pmid") or "").strip()
    if pmid and pmid.lower() not in {"none", "null"}:
        citation = f"{citation}. PMID {pmid}" if citation else f"PMID {pmid}"

    if citation and not citation.endswith("."):
        citation += "."
    return citation


def format_literature_references(references: Any) -> str:
    """The whole reference list as one readable run of citations."""
    if not references:
        return ""
    if isinstance(references, (str, bytes)):
        return str(references).strip()
    formatted = [
        text for text in (format_literature_reference(r) for r in references) if text
    ]
    return " ".join(formatted)
