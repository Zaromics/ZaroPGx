"""PharmCAT's own messages about a call or a drug, as the report shows them.

PharmCAT 3.4 attaches messages to each gene (``genes.<GENE>.messages``) and to
each drug (``drugs.<source>.<drug>.messages``, and each annotation's), and its own
HTML report shows them: a gene's in its Section III gene table, a drug's under the
drug in Section II (org/pharmgkb/pharmcat/reporter/report.hbs). This report showed
none of them. Its kinds (``exception_type``), from the rules in the 3.4.0 jar's
reporter/messages.json and the ones PharmCAT adds as it runs:

- ambiguity (38 rules): "The assigned genotype is *1/*15; however, *5/*37 cannot
  be ruled out without phased data.";
- note (14): G6PD hemizygous samples, UGT1A1*80 without *28 or *37, the phenytoin
  HLA-B warning, the warfarin flowchart and VKORC1 notes, SLCO1B1 drug text, ...;
- footnote (6): the reverse-strand footnotes (CYP3A5, DPYD, IFNL3, TPMT, VKORC1)
  and UGT1A1's repeat warning;
- non-match: the RYR1 malignant hyperthermia warning;
- gene-specific warnings: CYP2C19 *17 missing;
- untyped rules: pcat-outside-call, pcat-dpyd-hapB3-exonic-only,
  pcat-dpyd-hapB3-intronic-mismatch-exonic;
- report-as-genotype (cpic-warfarin-rs12777823): no text; PharmCAT reports the
  variant's genotype with the drug instead (REPORT_AS_GENOTYPE);
- added at run time: reference-allele and missing-variants; and in code,
  extra-position-notes and combo-partial.

Three are left out (LEFT_OUT), because the report already says the same in its
own words. Everything else is shown in PharmCAT's text: its markup removed, the
words unchanged.
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping
from typing import Any, Dict, Iterable, List, Optional

# Rules whose content the report states itself, and where.
LEFT_OUT = {
    # "The CYP2B6 *1 allele assignment is characterized by the absence of variants
    # at the positions ..." -- the Wild Type labels and their legends, and the
    # positions notes ("... positions had no genotype in the file and were read as
    # reference").
    "reference-allele": "the Wild Type labels, their legends and the positions notes",
    # "Some position data used to define CYP2C19 alleles is missing which may
    # change the matched genotype." -- Alerts and Warnings' "Positions not in the
    # VCF" / "Positions not read" lists each gene's missing positions and the
    # alleles they leave unassessable.
    "missing-variants": "Alerts and Warnings' lists of positions not read",
    # "This comes from an outside data source which does not supply position-level
    # detail." -- the Call column and Source Legend name the tool that made the
    # call, and the PyPGx evidence panel gives its position-level detail.
    "pcat-outside-call": "the Call column, the Source Legend and the PyPGx evidence",
}

# The kinds PharmCAT 3.4 emits, and the 2.x "warning"/"error" the API used to
# select. Anything else PharmCAT adds is shown too; this is for the API's filter.
KINDS = frozenset(
    {
        "ambiguity",
        "note",
        "footnote",
        "non-match",
        "gene-specific warnings",
        "report-as-genotype",
        "extra-position-notes",
        "combo-partial",
        "",
        "warning",
        "error",
    }
)

# report-as-genotype rules (messages.json): the drug, and the gene and variant
# whose genotype PharmCAT reports with it. Its message is empty.
REPORT_AS_GENOTYPE = {"warfarin": (("CYP2C9", "rs12777823"),)}

_BREAK = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


def shown(message: Mapping[str, Any]) -> bool:
    """Whether the report shows this message (it has text, and is not LEFT_OUT)."""
    rule = str(message.get("rule_name") or "").strip()
    return rule not in LEFT_OUT and bool(str(message.get("message") or "").strip())


def paragraphs(text: Any) -> List[str]:
    """PharmCAT's message as plain paragraphs: its <br> breaks kept as paragraph
    breaks, every other tag removed (a link keeps its words), entities decoded."""
    parts = []
    for chunk in _BREAK.split(str(text or "")):
        words = re.sub(r"\s+", " ", html.unescape(_TAG.sub("", chunk))).strip()
        if words:
            parts.append(words)
    return parts


def _entry(message: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "kind": str(message.get("exception_type") or ""),
        "rule": str(message.get("rule_name") or ""),
        "paragraphs": paragraphs(message.get("message")),
    }


def _add(found: List[Dict[str, Any]], messages: Any) -> None:
    for message in messages or []:
        if not isinstance(message, Mapping) or not shown(message):
            continue
        entry = _entry(message)
        if entry["paragraphs"] and all(
            (e["rule"], e["paragraphs"]) != (entry["rule"], entry["paragraphs"])
            for e in found
        ):
            found.append(entry)


def _gene_payloads(report: Mapping[str, Any]) -> Iterable[tuple]:
    """(GENE, payload) for 3.x's genes.<GENE> and 2.x's genes.<source>.<GENE>."""
    genes = report.get("genes") if isinstance(report, Mapping) else None
    if not isinstance(genes, Mapping):
        return
    for key, value in genes.items():
        if not isinstance(value, Mapping):
            continue
        if "geneSymbol" in value or "sourceDiplotypes" in value:
            yield str(value.get("geneSymbol") or key).upper(), value
        else:
            for gene, payload in value.items():
                if isinstance(payload, Mapping):
                    yield str(payload.get("geneSymbol") or gene).upper(), payload


def gene_messages(report: Any) -> Dict[str, List[Dict[str, Any]]]:
    """Each gene's messages the report shows, by gene symbol, in PharmCAT's order."""
    found: Dict[str, List[Dict[str, Any]]] = {}
    for gene, payload in _gene_payloads(report or {}):
        _add(found.setdefault(gene, []), payload.get("messages"))
    return {gene: entries for gene, entries in found.items() if entries}


def _variant_genotypes(report: Mapping[str, Any]) -> Dict[tuple, str]:
    """(GENE, rsid) -> call, from each gene's variants and variants of interest."""
    calls: Dict[tuple, str] = {}
    for gene, payload in _gene_payloads(report):
        for key in ("variants", "variantsOfInterest"):
            for variant in payload.get(key) or []:
                if isinstance(variant, Mapping) and variant.get("call"):
                    rsid = str(variant.get("dbSnpId") or "").strip()
                    if rsid:
                        calls.setdefault((gene, rsid), str(variant["call"]))
    return calls


def drug_messages(report: Any) -> Dict[str, List[Dict[str, Any]]]:
    """Each drug's messages the report shows, by lower-cased drug name: the drug's
    and its annotations', over every guideline source, each once; and the
    genotype PharmCAT reports with the drug for a report-as-genotype rule."""
    report = report or {}
    found: Dict[str, List[Dict[str, Any]]] = {}
    drugs = report.get("drugs") if isinstance(report, Mapping) else None
    for source in (drugs or {}).values() if isinstance(drugs, Mapping) else []:
        if not isinstance(source, Mapping):
            continue
        for name, drug in source.items():
            if not isinstance(drug, Mapping):
                continue
            entries = found.setdefault(str(drug.get("name") or name).lower(), [])
            _add(entries, drug.get("messages"))
            for guideline in drug.get("guidelines") or []:
                for annotation in (guideline or {}).get("annotations") or []:
                    if isinstance(annotation, Mapping):
                        _add(entries, annotation.get("messages"))
    calls = _variant_genotypes(report) if found else {}
    for drug, variants in REPORT_AS_GENOTYPE.items():
        if drug not in found:
            continue
        for gene, rsid in variants:
            call = calls.get((gene, rsid))
            if call:
                found[drug].append(
                    {
                        "kind": "report-as-genotype",
                        "rule": f"{gene} {rsid}",
                        "paragraphs": [f"{gene} {rsid}: {call}"],
                    }
                )
    return {drug: entries for drug, entries in found.items() if entries}


def api_messages(messages: Iterable[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    """The API's warning_messages: the messages the report shows, of PharmCAT 3.4's
    kinds and 2.x's warning/error."""
    return [
        m
        for m in messages or []
        if str(m.get("exception_type") or "") in KINDS and shown(m)
    ]


def gene_message_lines(entries: Optional[List[Mapping[str, Any]]]) -> List[str]:
    """Each message as one line, its paragraphs joined: for the interactive
    report's gene panel."""
    return [" ".join(e.get("paragraphs") or []) for e in entries or []]
