"""What ZaroPGx counts as an actionable finding.

The FHIR DiagnosticReport's conclusion ("N genes analyzed, M actionable findings
identified"), ``/api/pharmcat/actionable/{run_id}`` and the summaries' actionable
findings used to count every stored diplotype whose phenotype was not "n/a",
"Normal Metabolizer" or "Uncertain Susceptibility". So Normal Function, G6PD Normal,
a negative HLA screen, Indeterminate and No Result all counted: 9 genes on NA12878
(PharmCAT 3.4), and of those only CFTR is one a guideline changes a prescription for.

``actionable_genes`` holds the definition now, and only it does, so that a change
of definition is made there and every count follows.
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, List, Mapping, Optional, Tuple

from app.reports.evidence import PRESCRIBING_CHANGES

# Most decisive first, as evidence.prescribing_changes lists them.
_CHANGES = tuple(label for _, label in PRESCRIBING_CHANGES)


def _recommendations(
    drug_recommendations: Any,
) -> Iterator[Tuple[Any, Mapping[str, Any]]]:
    """(drug, recommendation) for each recommendation in a report's
    ``drugRecommendations``, read as generator.map_recommendations_for_template
    reads them for the report: grouped by drug (``drug``, ``genes``,
    ``recommendations``), as both report lanes build them, or a legacy flat entry
    that is its own recommendation."""
    for entry in drug_recommendations or []:
        if not isinstance(entry, Mapping):
            continue
        drug = entry.get("drug", "Unknown")
        genes = entry.get("genes", [])
        recommendations = entry.get("recommendations", [])
        if isinstance(genes, list) and isinstance(recommendations, list):
            for recommendation in recommendations:
                if isinstance(recommendation, Mapping):
                    yield drug, recommendation
        else:
            yield drug, entry


def actionable_genes(drug_recommendations: Any) -> Dict[str, Dict[str, List[str]]]:
    """The genes this report counts as actionable findings, each with the drugs and
    the prescribing changes that make it one: gene -> {"drugs", "prescribing_changes"},
    genes in the order the recommendations name them, drugs alphabetical.

    The current definition (chosen 2026-10-19, and open to change here): a gene is an
    actionable finding when a guideline's prescribing change for this sample comes
    from it. A PharmCAT annotation recommends a change where
    evidence.prescribing_changes finds one (a different drug, a dose change or other
    prescribing guidance, unless the annotation rests only on a risk screen's
    all-clear), and the change comes from the genes evidence.change_genes names
    (phenytoin's dose change for a CYP2C9 poor metabolizer comes from CYP2C9, not
    from the negative HLA-B *15:02 screen it was also looked up by).

    Why: a phenotype alone does not say whether anything should change. A CYP3A5
    normal metabolizer starts tacrolimus at 1.5 to 2 times the usual dose; a G6PD
    Normal or a negative HLA screen changes nothing. The guideline's own prescribing
    tags say whether it does. They are taken as ClinPGx sets them, so what they mark
    counts: on NA12878, CPIC's atazanavir counselling for a UGT1A1 normal metabolizer
    (other guidance) and the anaesthetics' guidance for RYR1 and CACNA1S Uncertain
    Susceptibility.

    It is the interactive report's drug-gene network, read off the same
    recommendation entries with the same fallbacks (pgx-report.js,
    actionableNetwork): a gene is an actionable finding exactly when the network
    draws it, so the report and every count of actionable findings agree.
    """
    found: Dict[str, Dict[str, List[str]]] = {}
    for drug, recommendation in _recommendations(drug_recommendations):
        changes = [
            change
            for change in recommendation.get("prescribing_changes") or []
            if change in _CHANGES
        ]
        if not changes or not drug:
            continue
        # As the network takes them: the genes the change comes from, else those the
        # recommendation rests on, else the one it is filed under.
        genes = (
            recommendation.get("change_genes")
            or recommendation.get("recommendation_genes")
            or [recommendation.get("gene", "Unknown")]
        )
        for gene in genes:
            if not gene or gene == "Unknown":
                continue
            entry = found.setdefault(gene, {"drugs": [], "prescribing_changes": []})
            if drug not in entry["drugs"]:
                entry["drugs"].append(drug)
            entry["prescribing_changes"] = [
                change
                for change in _CHANGES
                if change in entry["prescribing_changes"] or change in changes
            ]
    for entry in found.values():
        entry["drugs"].sort(key=lambda drug: str(drug).lower())
    return found


def _score(value: Any) -> Optional[float]:
    """An activity score as a number, or None; 0 stays 0 (a poor metabolizer's)."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def actionable_findings(genes: Any, drug_recommendations: Any) -> List[Dict[str, Any]]:
    """One finding per actionable gene (``actionable_genes``), with the call the
    report states for it: its row in the report's ``genes`` (every HLA-B result; an
    unresolved call's candidates). In the report's gene order, then any gene it has
    no row for. The fields are the API's ActionableFinding."""
    calls: Dict[str, Mapping[str, Any]] = {}
    for row in genes or []:
        if isinstance(row, Mapping) and row.get("gene"):
            calls.setdefault(row["gene"], row)
    actionable = actionable_genes(drug_recommendations)
    order = [gene for gene in calls if gene in actionable]
    order += [gene for gene in actionable if gene not in calls]
    findings = []
    for gene in order:
        call = calls.get(gene, {})
        findings.append(
            {
                "gene_symbol": gene,
                "diplotype_label": call.get("diplotype"),
                "phenotype": call.get("phenotype"),
                "activity_score": _score(call.get("activity_score")),
                "allele1_name": call.get("allele1"),
                "allele2_name": call.get("allele2"),
                "drugs": actionable[gene]["drugs"],
                "prescribing_changes": actionable[gene]["prescribing_changes"],
            }
        )
    return findings
