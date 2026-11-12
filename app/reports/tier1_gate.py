# app/reports/tier1_gate.py
"""Withhold a gene's verdict when the run could not assess the alleles AMP/CAP say a
clinical test must assess. The standard and the measurements are in
``app/pharmcat/amp_tier1.py``; this is the half that edits the report.

WHAT THIS CHANGES, and what it does not. PharmCAT already declines a gene it has no
position for: on the consumer-array file measured on 2026-11-12 it reported
``Unknown/Unknown`` for TPMT, NUDT15, DPYD, CYP3A4, CYP3A5, CYP4F2, SLCO1B1, CYP2D6 and
the rest. What it does NOT decline is a gene it saw a little of, and there it reported

    CYP2C9   *1/*1, Normal Metabolizer, Activity Score 2.0   from 2 of 88 positions
    CYP2C19  *2/*2, Poor Metabolizer                         from 2 of 35 positions

So this gate acts on a narrow and specific set: genes PharmCAT DID call while unable to
assess a Tier 1 allele. Everything it would "withhold" that PharmCAT already left
uncalled is a no-op, by construction -- ``withhold_inadequate_calls`` only touches a row
that currently holds a call.

WHY THE ROW IS EMPTIED RATHER THAN FLAGGED. The report already has one way of saying a
gene has no verdict: an empty diplotype, which puts the gene under "Genes examined
without a call" with its ``no_call_note`` as the reason, and which
``app/reports/implications.py`` turns into "Not called: phenotype unknown, so no
genotype-based guidance". Routing an inadequate gene down that existing path means the
Executive Summary, the gene table, the implications column and the PDF all agree without
any of them learning about Tier 1. A second way of being not-called would be a second
way to drift.

THE DRUG TABLE. A withheld verdict must not leave guidance standing that was computed
from it. On the measured array run no such guidance existed -- PharmCAT's own reporter
had already issued none for CYP2C9 -- but a denser input (a panel VCF covering enough of
a gene to be called, not enough to assess its Tier 1 alleles) can produce it, so
``mark_recommendations_without_a_call`` annotates any recommendation whose genes were all
withheld. It annotates rather than deletes: a drug row that vanishes is a question the
reader cannot answer, and the reason is the thing they need.
"""

from __future__ import annotations

from html import escape
from typing import Any, Dict, List, Mapping, Optional, Sequence

from app.pharmcat.amp_tier1 import adequacy, statement
from app.pharmcat.report_json import iter_gene_blocks
from app.reports.marks import CAUTION_SVG

#: Keys that carry the call itself, cleared together. A row that kept its phenotype
#: while losing its diplotype would read as a phenotype with no genotype behind it,
#: which is the opposite of what this gate is for.
_CALL_KEYS = (
    "diplotype",
    "diplotype_display",
    "phenotype",
    "activity_score",
    "rec_lookup_diplotype",
)


def _uncalled_by_gene(report: Optional[Mapping[str, Any]]) -> Dict[str, List[str]]:
    """gene -> the alleles PharmCAT could not assess, from either report shape.

    Through ``iter_gene_blocks`` rather than by indexing ``genes``: a PharmCAT 3.x
    report nests its genes under the source (CPIC/DPWG/FDA) and a 2.x one does not, and
    that walker is the only thing in this repo that reads both correctly.
    """
    genes = (report or {}).get("genes")
    if not isinstance(genes, Mapping):
        return {}
    found: Dict[str, List[str]] = {}
    for block in iter_gene_blocks(genes):
        symbol = str(block.gene_symbol or "").strip().upper()
        if not symbol:
            continue
        data = block.gene_data if isinstance(block.gene_data, Mapping) else {}
        uncalled = data.get("uncalledHaplotypes")
        if not isinstance(uncalled, (list, tuple, set)):
            continue
        # First source wins, and the sources agree: uncalledHaplotypes is a property of
        # the matcher's work on the gene, not of the guideline source reporting it.
        found.setdefault(symbol, [str(name) for name in uncalled])
    return found


def _holds_a_call(row: Mapping[str, Any]) -> bool:
    """Whether this row currently shows a verdict there is something to withhold.

    Deliberately not ``gene_was_called``: that answers "did a caller produce this", and
    the question here is "does the reader see a diplotype". PharmCAT's own
    ``Unknown/Unknown`` is not a call and must not be re-withheld -- it already has its
    own reason, and overwriting it would replace a specific one with this general one.
    """
    for key in ("diplotype_display", "diplotype"):
        value = str(row.get(key) or "").strip()
        if not value:
            continue
        lowered = value.lower()
        if "unknown" in lowered or lowered in {"n/a", "na", "-", "."}:
            continue
        return True
    return False


def withhold_inadequate_calls(
    rows: Optional[Sequence[Any]], report: Optional[Mapping[str, Any]]
) -> List[str]:
    """Empty the verdict of every gene that fell short of its AMP/CAP minimum set.

    In place, on the report's own gene rows. Returns the gene symbols withheld, in the
    order the rows carry them, so the caller can raise one alert naming them.

    A row is touched only when ALL of these hold, which is what keeps the gate narrow:

    * AMP publishes a minimum allele set for the gene (``adequacy().assessed``) -- a
      gene with none is never withheld, and never counted as adequate either;
    * at least one Tier 1 allele is in PharmCAT's ``uncalledHaplotypes`` for it;
    * the row currently shows a call. PharmCAT's ``Unknown/Unknown`` keeps its own
      reason.
    """
    uncalled = _uncalled_by_gene(report)
    if not uncalled:
        return []
    withheld: List[str] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("gene") or "").strip().upper()
        if not symbol or symbol not in uncalled:
            continue
        verdict = adequacy(symbol, uncalled.get(symbol))
        if not verdict.inadequate or not _holds_a_call(row):
            continue
        note = statement(verdict)
        for key in _CALL_KEYS:
            if key in row:
                row[key] = ""
        if note:
            row["no_call_note"] = note
        # Kept for the alert and for anything that wants to say more than the note:
        # which alleles, and under whose recommendation.
        row["tier1_withheld"] = {
            "gene": symbol,
            "missing": list(verdict.missing),
            "citation": verdict.citation,
        }
        withheld.append(symbol)
    return withheld


def withheld_alert(rows: Optional[Sequence[Any]], max_listed: int = 8) -> Optional[str]:
    """One alert naming the genes whose verdict was withheld, and which alleles for.

    Built from the rows rather than from the returned gene list, so it can only ever
    describe edits that were actually made. None when nothing was withheld.

    The caution icon and the one-paragraph shape match the report's other input alerts
    (``unread_variants_alert``, ``partial_coverage_alert``) because this is the same kind
    of statement: something about the data, stated once, above the table it affects.
    """
    listed = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        withheld = row.get("tier1_withheld")
        if not isinstance(withheld, Mapping):
            continue
        gene = escape(str(withheld.get("gene") or ""))
        missing = [escape(str(name)) for name in withheld.get("missing") or []]
        if not gene:
            continue
        listed.append(f"{gene} ({'; '.join(missing)})" if missing else gene)
    if not listed:
        return None
    shown = ", ".join(listed[:max_listed])
    if len(listed) > max_listed:
        shown += f" and {len(listed) - max_listed} more"
    return (
        f"<p>{CAUTION_SVG} <strong>Genes not called: the data could not assess the "
        f"alleles a clinical test must assess.</strong> The AMP/CAP pharmacogenomics "
        f"working group publishes, per gene, a minimum set of alleles a clinical "
        f"genotyping assay should include. For these genes this run could not assess "
        f"one or more of them, so no genotype is reported: {shown}. A reference result "
        f"for such a gene would rest on positions the data does not cover, which reads "
        f"as a normal result rather than as an untested one.</p>"
    )


def mark_recommendations_without_a_call(
    recommendations: Optional[Sequence[Any]], withheld: Optional[Sequence[str]]
) -> int:
    """Annotate guidance whose every gene had its verdict withheld; how many.

    In place. A recommendation resting on several genes is left alone while any of them
    still has a verdict: CPIC's warfarin guidance reads CYP2C9 and VKORC1 together, and
    losing CYP2C9 does not make the VKORC1 half untrue.

    Annotated, not removed, and the text is not rewritten: PharmCAT's words stay
    PharmCAT's. ``tier1_no_call`` says the guidance no longer has a genotype under it
    and names the genes, for the template to render beside the row.
    """
    names = {str(gene or "").strip().upper() for gene in (withheld or []) if gene}
    if not names:
        return 0
    marked = 0
    for rec in recommendations or []:
        if not isinstance(rec, dict):
            continue
        genes = rec.get("recommendation_genes") or rec.get("genes") or rec.get("gene")
        if isinstance(genes, str):
            genes = [genes]
        if not isinstance(genes, (list, tuple)):
            continue
        symbols = {str(g or "").strip().upper() for g in genes if str(g or "").strip()}
        if not symbols or not symbols.issubset(names):
            continue
        rec["tier1_no_call"] = sorted(symbols)
        marked += 1
    return marked
