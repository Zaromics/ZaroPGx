"""Per-gene provenance for the report's Call and Guide columns.

The product rule: report what the run recorded. Every value here traces to
something a tool actually emitted -- PharmCAT's ``callSource``, the TSV
``Outside Call`` column, or a tool marker written by a ZaroPGx merge path that
ran. Nothing is inferred from the gene name. When the run recorded nothing, say
so with ``?`` rather than guessing.

Replaces ``determine_called_by`` / ``determine_report_data_from`` /
``determine_tool_source`` / ``determine_guideline_source`` (generator.py) and
``_determine_called_by_letter`` (pharmcat_data_service.py) -- BACKLOG 28 + 216.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

CALLED_BY_PHARMCAT = "C"
CALLED_BY_PYPGX = "P"
CALLED_BY_OPTITYPE = "O"
CALLED_BY_MTDNA = "M"
CALLED_BY_OUTSIDE = "X"
CALLED_BY_NO_CALL = "–"  # en dash
CALLED_BY_UNKNOWN = "?"

_TOOL_MARKERS = {
    "PHARMCAT": CALLED_BY_PHARMCAT,
    "C": CALLED_BY_PHARMCAT,
    "PYPGX": CALLED_BY_PYPGX,
    "P": CALLED_BY_PYPGX,
    "OPTITYPE": CALLED_BY_OPTITYPE,
    "O": CALLED_BY_OPTITYPE,
    "MTDNA-SERVER-2": CALLED_BY_MTDNA,
    "MTDNA-SERVER": CALLED_BY_MTDNA,
    "M": CALLED_BY_MTDNA,
}

_TOOL_LABELS = {
    CALLED_BY_PHARMCAT: "Called by PharmCAT",
    CALLED_BY_PYPGX: "Called by PyPGx",
    CALLED_BY_OPTITYPE: "Called by OptiType",
    CALLED_BY_MTDNA: "Called by mtDNA-server-2",
}

_LABEL_OUTSIDE = "Outside call - producing tool not recorded by this run"
_LABEL_NO_CALL = "No call made for this gene"
_LABEL_UNKNOWN = "Calling tool not recorded by this run"

_GUIDELINE_LETTERS = {
    "FDA": "F",
    "F": "F",
    "DPWG": "D",
    "D": "D",
    "CPIC": "C",
    "C": "C",
    "PHARMGKB": "P",
}


@dataclass(frozen=True)
class CallProvenance:
    """What the run recorded about who called one gene."""

    letter: str
    label: str
    recorded: bool


def _tool_letter(gene_row: Mapping[str, Any]) -> Optional[str]:
    """Letter for an explicit ZaroPGx tool marker, or None if none names a tool."""
    raw = gene_row.get("tool_source") or gene_row.get("source") or ""
    return _TOOL_MARKERS.get(str(raw).strip().upper())


def resolve_called_by(gene_row: Mapping[str, Any]) -> CallProvenance:
    """Report which tool made this call, as recorded by the run.

    Never consults the gene name. Returns ``?`` when nothing was recorded and
    an en dash when the run recorded that no call was made.
    """
    call_source = str(gene_row.get("call_source") or "").strip().upper()
    tool = _tool_letter(gene_row)

    if call_source == "MATCHER":
        # PharmCAT's own matcher made the call. A PyPGx enrichment marker on
        # the same row is not a calling claim -- do not let it win.
        return CallProvenance(
            CALLED_BY_PHARMCAT, _TOOL_LABELS[CALLED_BY_PHARMCAT], True
        )

    if call_source == "OUTSIDE":
        if tool:
            return CallProvenance(tool, _TOOL_LABELS[tool], True)
        return CallProvenance(CALLED_BY_OUTSIDE, _LABEL_OUTSIDE, True)

    if call_source == "NONE":
        return CallProvenance(CALLED_BY_NO_CALL, _LABEL_NO_CALL, True)

    outside_call = str(gene_row.get("outside_call") or "").strip().lower()
    if outside_call == "no":
        return CallProvenance(
            CALLED_BY_PHARMCAT, _TOOL_LABELS[CALLED_BY_PHARMCAT], True
        )
    if outside_call == "yes":
        if tool:
            return CallProvenance(tool, _TOOL_LABELS[tool], True)
        return CallProvenance(CALLED_BY_OUTSIDE, _LABEL_OUTSIDE, True)

    if tool:
        # No PharmCAT record at all for this gene, so the tool marker is the
        # only record there is -- a gene PharmCAT never saw.
        return CallProvenance(tool, _TOOL_LABELS[tool], True)

    if not str(gene_row.get("diplotype") or "").strip():
        return CallProvenance(CALLED_BY_NO_CALL, _LABEL_NO_CALL, True)

    return CallProvenance(CALLED_BY_UNKNOWN, _LABEL_UNKNOWN, False)


def resolve_guideline_source(gene_row: Mapping[str, Any]) -> str:
    """Letter for the guideline source the run recorded, or '' when it recorded none."""
    raw = gene_row.get("guideline_source") or gene_row.get("phenotype_source") or ""
    return _GUIDELINE_LETTERS.get(str(raw).strip().upper(), "")
