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
- report-as-genotype (cpic-warfarin-rs12777823): no text; PharmCAT writes the
  variant's genotype into the drug's annotations as ``highlightedVariants``
  ("rs12777823:G/A", "rs12777823:Unknown"), which are shown with the drug;
- added at run time: reference-allele and missing-variants; and in code,
  extra-position-notes and combo-partial.

The report leaves out what it already says in its own words (LEFT_OUT, and the
positions rules when its positions alert was produced), and notes PharmCAT gives
every call of a gene whatever the sample (GENERIC). PharmCAT's strand footnotes,
the same words for five genes, are said once (STRAND_FOOTNOTE), and SLCO1B1's drug
text only when SLCO1B1 has no call (WHEN_NOT_CALLED). Everything else is shown in
PharmCAT's text: its markup removed (a link as "words (url)"), the words
unchanged. Rows with no call keep theirs: PharmCAT gives an uncalled RYR1 its
malignant hyperthermia warning.
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping
from typing import Any, Dict, Iterable, List, Optional

# Rules whose content the report states itself, and where. The two positions
# rules only when the report's positions alert (Alerts and Warnings: "Positions not
# in the VCF" / "Positions without a genotype") was produced for this run: it lists each
# gene's missing positions and the alleles they leave unassessed. Without it they
# are shown.
LEFT_OUT = {
    # "The CYP2B6 *1 allele assignment is characterized by the absence of variants
    # at the positions that are included in the underlying allele definitions." --
    # what a reference allele is: its name says it. Its "... either because the
    # sample's VCF has the reference allele or because the position is missing"
    # form is a positions note (POSITIONS_RULES).
    "reference-allele": "it defines the reference allele, which the call names",
    # "This comes from an outside data source which does not supply position-level
    # detail." -- the Call column and Source Legend name the tool that made the
    # call, and the PyPGx evidence panel gives its position-level detail.
    "pcat-outside-call": "the Call column, the Source Legend and the PyPGx evidence",
}
# Notes PharmCAT attaches to every call of the gene, whatever the sample, that change
# nothing about this one: the report leaves them out (the API too, so the two agree).
GENERIC = {
    # "The CYP3A4 alleles are determined based on PharmVar CYP3A4 allele
    # definitions. See PharmCAT disclaimer for further information." -- where the
    # definitions come from, on every CYP3A4 call; the disclaimer it points to is
    # in PharmCAT's own report, not this one.
    "CYP3A4": "where CYP3A4's allele definitions come from, on every CYP3A4 call",
    # "PharmCAT reports based on VCF file input and some VCF files do not specify
    # hemizygous (one X chromosome) and instead represent samples as homozygous.
    # See PharmCAT FAQs" -- on every G6PD call and drug. A hemizygous call written
    # as homozygous has the same phenotype (Normal, Deficient), so it changes no
    # result here.
    "G6PD hemizygous samples": "how some VCF files write chromosome X",
}
# "Some position data used to define CYP2C19 alleles is missing which may change
# the matched genotype.", and reference-allele's "... or because the position is
# missing" form.
POSITIONS_RULES = frozenset({"missing-variants"})
# Notes that matter only when the gene has no call. "The SLCO1B1 genotype (star
# nomenclature) will be displayed if determinable ... In case no genotype can be
# determined, recommendations are based on the rs4149056 genotype alone": with a
# star-allele call it says only that the call was made (generator.
# attach_pharmcat_messages).
WHEN_NOT_CALLED = {"SLCO1B1-drug text": "SLCO1B1"}
# PharmCAT's footnote on each gene on the negative strand (CYP3A5, DPYD, IFNL3,
# TPMT, VKORC1): "The CYP3A5 gene is on the negative chromosomal strand, all
# genotype calls for CYP3A5 in this report refer to the positive chromosomal
# strand. Therefore, genotype calls are complemented from gene bases." The same
# words for each: the report says them once, for all of them.
STRAND_FOOTNOTE = re.compile(r"^(\S+) reverse complement footnote$")

_BREAK = re.compile(r"<br\s*/?>|</?p\b[^>]*>", re.IGNORECASE)
# A tag starts with a letter (or /letter): "AS < 1.5 and AS > 0.5" is text.
_TAG = re.compile(r"</?[A-Za-z][^>]*>")
_LINK = re.compile(
    r"<a\b[^>]*?href\s*=\s*[\"']([^\"']*)[\"'][^>]*>(.*?)</a>", re.IGNORECASE | re.S
)


def _positions_form(message: Mapping[str, Any]) -> bool:
    rule = str(message.get("rule_name") or "").strip()
    text = str(message.get("message") or "")
    return rule in POSITIONS_RULES or (
        rule == "reference-allele" and "missing" in text.lower()
    )


def shown(message: Mapping[str, Any], positions_stated: bool = True) -> bool:
    """Whether the report shows this message: it has text, it bears on this
    sample's call (GENERIC), and the report does not say the same itself
    (LEFT_OUT; the positions rules when ``positions_stated``)."""
    if not str(message.get("message") or "").strip():
        return False
    if _positions_form(message):
        return not positions_stated
    rule = str(message.get("rule_name") or "").strip()
    return rule not in LEFT_OUT and rule not in GENERIC


def _link_text(match: "re.Match[str]") -> str:
    url, words = match.group(1).strip(), _TAG.sub("", match.group(2)).strip()
    # A link within PharmCAT's own report ("#CYP2C19") leads nowhere here.
    if not url or url.startswith("#") or url == words:
        return words
    return f"{words} ({url})"


def paragraphs(text: Any) -> List[str]:
    """PharmCAT's message as plain paragraphs: its <br> and <p> breaks kept as
    paragraph breaks, a link as its words and its URL ("words (url)"), every other
    tag removed, entities decoded."""
    parts = []
    for chunk in _BREAK.split(_LINK.sub(_link_text, str(text or ""))):
        words = re.sub(r"\s+", " ", html.unescape(_TAG.sub("", chunk))).strip()
        if words:
            parts.append(words)
    return parts


def _entry(message: Mapping[str, Any]) -> Dict[str, Any]:
    matches = message.get("matches")
    gene = matches.get("gene") if isinstance(matches, Mapping) else None
    return {
        "kind": str(message.get("exception_type") or ""),
        "rule": str(message.get("rule_name") or ""),
        "gene": str(gene or "").strip(),
        "paragraphs": paragraphs(message.get("message")),
    }


def _add(found: List[Dict[str, Any]], messages: Any, positions_stated: bool) -> None:
    for message in messages or []:
        if not isinstance(message, Mapping) or not shown(message, positions_stated):
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


def gene_messages(
    report: Any, positions_stated: bool = True
) -> Dict[str, List[Dict[str, Any]]]:
    """Each gene's messages the report shows, by gene symbol, in PharmCAT's order.
    The report's top-level messages, which name no gene, are under ""."""
    found: Dict[str, List[Dict[str, Any]]] = {}
    for gene, payload in _gene_payloads(report or {}):
        _add(found.setdefault(gene, []), payload.get("messages"), positions_stated)
    if isinstance(report, Mapping):
        _add(found.setdefault("", []), report.get("messages"), positions_stated)
    return {gene: entries for gene, entries in found.items() if entries}


def drug_messages(
    report: Any, positions_stated: bool = True
) -> Dict[str, List[Dict[str, Any]]]:
    """Each drug's messages the report shows, by lower-cased drug name: the drug's
    and its annotations', over every guideline source, each once; and the
    genotypes PharmCAT highlights with the drug's annotations
    (``highlightedVariants``: report-as-genotype rules, e.g. warfarin's
    "rs12777823:G/A", or "rs12777823:Unknown" where PharmCAT has none)."""
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
            _add(entries, drug.get("messages"), positions_stated)
            for guideline in drug.get("guidelines") or []:
                for annotation in (guideline or {}).get("annotations") or []:
                    if not isinstance(annotation, Mapping):
                        continue
                    _add(entries, annotation.get("messages"), positions_stated)
                    for variant in annotation.get("highlightedVariants") or []:
                        text = str(variant or "").strip().replace("|", "/")
                        entry = {
                            "kind": "report-as-genotype",
                            "rule": "highlightedVariants",
                            "gene": "",
                            "paragraphs": [text],
                        }
                        if text and entry not in entries:
                            entries.append(entry)
    return {drug: entries for drug, entries in found.items() if entries}


def api_messages(messages: Iterable[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    """The API's warning_messages: the messages the report shows."""
    return [m for m in messages or [] if shown(m)]


def gene_message_lines(entries: Optional[List[Mapping[str, Any]]]) -> List[str]:
    """Each message as one string, its paragraphs on lines of their own: for the
    interactive report's gene panel."""
    return ["\n".join(e.get("paragraphs") or []) for e in entries or []]
