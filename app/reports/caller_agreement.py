"""Where the report's two callers read the same data differently.

On an alignment run PyPGx genotypes PharmCAT's genes too, for their copy number
(``determine_pypgx_gene_set``'s "pypgx" set, docker/pypgx/pypgx_wrapper.py), and
PharmCAT's call is the one the report prints. PyPGx's own call for such a gene was
then dropped: the reader saw one diplotype, with nothing to say that a second
caller, reading the same reads, had named different alleles. This says so --
beside the call, in the Executive Summary's notes and in one alert -- without
changing which call is printed.

What counts as a disagreement is the alleles, not the words. Only a call both
callers write as star alleles is compared, which leaves out every gene whose two
callers describe a genotype in different terms: PharmCAT's "rs2231142 reference
(G)/rs2231142 reference (G)" against PyPGx's "Reference/Reference" (ABCG2, IFNL3,
VKORC1), its variant lists against PyPGx's (DPYD), "B (reference)" against PyPGx's
"MALE" (G6PD), and the RYR1 and CFTR variants PyPGx has no allele for. Within a
compared call the order of the two haplotypes is ignored, as is the spacing of a
tandem ("*68 + *4" against "*68+*4"), and ``x2`` is read as two copies.

Measured over the 61 GeT-RM genomes (scripts/getrm): 671 of the 1,159 gene-sample
pairs where PharmCAT called a gene itself and PyPGx called it too are comparable
this way, and the two callers named different alleles in 21. Seven were NAT2
(below). Of the remaining 14 -- about one note for every four reports -- every one
was a real difference in the alleles called, and in four the printed call was wrong
where PyPGx's was right (CYP4F2 *1/*4 against *2/*3, GeT-RM *2/*3; NUDT15 three
times -- PharmCAT has no *2 in its NUDT15 definitions at all, so handing it one
names the allele at the cost of the phenotype, and these stay as notes).

A gene whose printed call came from PyPGx has no second opinion to show: PharmCAT
takes an outside call over its own matcher's and records only the outside one
(``matcherComponentHaplotypes`` is empty), so its own reading is not in its report
to compare. That is every CYP2D6 call, and NUDT15 on an alignment run.
"""

from __future__ import annotations

import html
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from app.reports.copy_number import pharmcat_called

# The second caller, named in every note this module writes.
CALLER = "PyPGx"

# Genes whose callers name the same haplotype differently, so a difference in the
# text is not a disagreement about the alleles. PharmCAT 3.4 names NAT2 alleles as
# PharmVar does; PyPGx names them by the legacy haplotype groups, so its *12 or *13
# is PharmCAT's *4 and its *5 is PharmCAT's *6. On the 61 GeT-RM genomes this
# accounted for all 7 NAT2 differences, and in every one of them both callers
# agreed with GeT-RM (scripts/getrm/score.py translates the names to score NAT2).
DIFFERENT_NAMES = frozenset({"NAT2"})

# A star allele as both callers write one: *2, *4A, *80, *2.001.
_STAR = re.compile(r"^\*[0-9]+[A-Za-z0-9.]*$")
# A repeated allele: *2x2 is two copies of *2 on one chromosome.
_COPIES = re.compile(r"^(\*[0-9]+[A-Za-z0-9.]*?)x([0-9]+)$")


def _haplotype(text: str) -> Optional[Tuple[str, ...]]:
    """One chromosome's alleles, sorted, or None if it is not written as star
    alleles. A tandem ("*68+*4") is its alleles; "*2x2" is *2 twice."""
    alleles: List[str] = []
    for token in text.split("+"):
        if not token:
            return None
        copies = _COPIES.fullmatch(token)
        if copies:
            alleles += [copies.group(1).upper()] * int(copies.group(2))
        elif _STAR.fullmatch(token):
            alleles.append(token.upper())
        else:
            return None
    return tuple(sorted(alleles)) if alleles else None


def haplotypes(label: Any) -> Optional[Tuple[Tuple[str, ...], ...]]:
    """A diplotype's two chromosomes as sorted allele tuples, in a fixed order so
    "*1/*5" and "*5/*1" compare equal; None if it is not a star-allele diplotype."""
    text = "".join(str(label or "").split())
    if text.count("/") != 1:
        return None
    pair = [_haplotype(half) for half in text.split("/")]
    if any(half is None for half in pair):
        return None
    return tuple(sorted(h for h in pair if h is not None))


def _readings(label: Any) -> List[Tuple[Tuple[str, ...], ...]]:
    """Every star-allele diplotype a call's text offers. A call PharmCAT could not
    resolve to one lists them all, joined with " or " (report_json.unresolved_call,
    which the report shows as "Applies if"); PyPGx joins its own with ";". Any one
    of them matching a reading of the other caller's call is agreement."""
    out = []
    for part in re.split(r"[;|]|\s+or\s+", str(label or "")):
        parsed = haplotypes(part)
        if parsed is not None and parsed not in out:
            out.append(parsed)
    return out


def pypgx_call(pypgx_results: Any, gene: str) -> Optional[str]:
    """PyPGx's own diplotype for the gene in this run, as it wrote it, or None."""
    results = (
        pypgx_results.get("results") if isinstance(pypgx_results, Mapping) else None
    )
    if not isinstance(results, Mapping):
        return None
    for name, result in results.items():
        if str(name).strip().upper() != gene.upper() or not isinstance(result, Mapping):
            continue
        if not result.get("success"):
            return None
        call = str(result.get("diplotype") or "").strip()
        if not call or call in {"Indeterminate", "None"}:
            return None
        return call
    return None


def _without_copies(
    reading: Tuple[Tuple[str, ...], ...],
) -> Tuple[Tuple[str, ...], ...]:
    """The same reading with each chromosome's repeats collapsed: "*1x2/*6" and
    "*1/*6" both become (("*1",), ("*6",))."""
    return tuple(sorted(tuple(sorted(set(side))) for side in reading))


def disagree(printed: Any, other: Any) -> bool:
    """Whether the two calls name different alleles. False unless both are written
    as star alleles and neither reading of one matches a reading of the other.

    A difference only in how many copies of an allele a chromosome carries is not a
    difference in the alleles named: PharmCAT does not assess copy number on an
    alignment, so PyPGx's ``*1x2/*6`` against its ``*1/*6`` is PyPGx measuring
    something PharmCAT did not look at. The row already carries the copy-number note
    that says so, and "which is right is not known" would contradict it.
    """
    left, right = _readings(printed), _readings(other)
    if not left or not right:
        return False
    if not any(reading in right for reading in left):
        bare = {_without_copies(reading) for reading in right}
        return not any(_without_copies(reading) in bare for reading in left)
    return False


def call_note(other: str) -> str:
    """The line beside the call."""
    return (
        f"{CALLER} read the same data as {other}. The two callers disagree, and "
        "this call is PharmCAT's (see Alerts)."
    )


def summary_note(other: str) -> str:
    """The Executive Summary's note, under the call it questions."""
    return (
        f"PharmCAT and {CALLER} disagree: {CALLER} called this gene {other} from "
        "the same data, and the call above is PharmCAT's. Which is right is not "
        "known. See Alerts and Warnings."
    )


def annotate_genes(genes: Sequence[Any], pypgx_results: Any) -> Dict[str, str]:
    """Mark every called row whose call is PharmCAT's own and whose gene PyPGx
    called differently, in place; returns {gene: PyPGx's call} for the alert.

    A row PyPGx supplied the call for (CYP2D6 today, and any gene a preference
    later routes the same way) is PyPGx's own call, so there is nothing to compare
    it with: ``pharmcat_called`` leaves it out.
    """
    found: Dict[str, str] = {}
    for row in genes or []:
        if not isinstance(row, dict) or not pharmcat_called(row):
            continue
        gene = str(row.get("gene") or "").strip().upper()
        if not gene or gene in DIFFERENT_NAMES:
            continue
        other = pypgx_call(pypgx_results, gene)
        if not other or not disagree(row.get("diplotype"), other):
            continue
        row["caller_disagreement"] = {
            "tool": CALLER,
            "call": other,
            "call_note": call_note(other),
            "note": summary_note(other),
            "fhir_note": fhir_note(other),
        }
        found[str(row.get("gene") or gene)] = other
    return found


def fhir_note(other: str) -> str:
    """The same caveat for the exported genotype Observation.

    A bundle a pharmacist's system reads is as much the report as the PDF is, and a
    diplotype exported without the caveat the PDF carries is the one place a reader
    could not tell there were two readings.
    """
    return (
        f"{CALLER} called this gene {other} from the same data, and this genotype "
        "is PharmCAT's. The two callers disagree and which is right is not known."
    )


def alert(found: Mapping[str, str]) -> Optional[str]:
    """Alerts and Warnings' entry naming each gene and both calls, or None."""
    if not found:
        return None
    items = ", ".join(
        f"{html.escape(gene)} ({CALLER} {html.escape(call)})"
        for gene, call in found.items()
    )
    return (
        f"<p>⚠️ <strong>The two callers disagree:</strong> {items}. On an alignment "
        f"ZaroPGx calls PharmCAT's genes with both PharmCAT and {CALLER}, for their "
        f"copy number, and prints PharmCAT's call. Here {CALLER} named different "
        "alleles from the same reads, so each printed call is one of two readings "
        "and this report does not know which is right. The two callers' allele "
        "definitions are not identical "
        "either, so a difference can also be an allele one of them cannot name. A "
        "note beside each call says so.</p>"
    )
