"""Where the reads contradict the arrangement a printed call asserts.

PyPGx phases with Beagle, from a reference panel, with no read evidence behind it. A
diplotype resting on two heterozygous variants asserts that the two sit on one
chromosome or on two, and the reads can check that without a phaser: count the
fragments carrying both positions. The counting is done where the reads are
(docker/pypgx/read_phase.py, which writes ``read_phase.json``); this reads that file,
works out what each printed call asserts, and says so where the two disagree.

It never changes a call. It does say what the reads' own arrangement would be called:
the pypgx lane names each chromosome with PyPGx's own ranking, so HG00589's CYP2D6
reads as ``*1/*21`` or ``*21/*39`` -- GeT-RM's answer is the first of those -- against
the printed ``*2/*21``. There are two because a variant no fragment reaches could sit
either way, and only what every reading allows is shown. The printed diplotype is left
alone: changing it is a decision about what a call says, not about what the reads show.

Measured over the 61 GeT-RM genomes (scripts/getrm/arrangement.py): 719 pairs of
heterozygous defining variants within 1 kb, 413 of which the reads decide, and 411 of
those agreeing with the call. So this writes nothing at all for 59 of 61 genomes, and
the note it does write is rare by construction. The two it found were CYP2D6
``*2/*21`` in HG00589, where 7 fragments to 0 put the ``*2`` cores on the ``*21``
chromosome, making GeT-RM's ``*1/*21`` -- one of three CYP2D6 calls the stack gets
wrong, and the same call Illumina's Cyrius fixes
(scripts/getrm/caller-augmentation.md) -- and CYP2F1 ``*1/*2`` in NA19026, a gene with
no dosing guideline.

**Only a row whose printed call is PyPGx's own is marked** (CYP2D6, and a gene in
``pypgx_preferred_over_pharmcat``). For a gene the report prints PharmCAT's call for,
PyPGx's arrangement is not the printed one, so a note about it would question a call
the reader cannot see; the evidence is kept in the run's file either way.
"""

from __future__ import annotations

import html
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from app.reports.copy_number import pypgx_called

logger = logging.getLogger(__name__)

# What the phasing it questions is called, in every note this module writes.
PHASER = "Beagle"


# What the pypgx wrapper's /read-phase writes and main.nf's PyPGxReadPhase publishes
# into the report directory.
RESULT_FILE = "read_phase.json"


def read_result(report_dir: str) -> dict:
    """read_phase.json, or {} for a run that has none.

    No file is the normal case: a VCF upload has no reads to check, and a run that
    predates this check has no file. Either way it costs the note, not the report.
    """
    path = Path(report_dir or ".") / RESULT_FILE
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.warning("Could not read %s: %s", RESULT_FILE, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _details(pypgx_results: Any, gene: str) -> Mapping[str, Any]:
    """PyPGx's details for one gene in this run, or {}."""
    results = (
        pypgx_results.get("results") if isinstance(pypgx_results, Mapping) else None
    )
    if not isinstance(results, Mapping):
        return {}
    for name, result in results.items():
        if str(name).strip().upper() != gene.upper() or not isinstance(result, Mapping):
            continue
        details = result.get("details")
        if result.get("success") and isinstance(details, Mapping):
            return details
    return {}


def asserted(pypgx_results: Any, gene: str) -> Dict[frozenset, str]:
    """{the two variants}: "cis" or "trans", as this gene's printed call places them.

    A variant that defines an allele on both haplotypes places nothing -- the call
    says it is on both chromosomes, which no pair of positions can contradict -- so it
    is left out.
    """
    details = _details(pypgx_results, gene)
    defines: Dict[str, set] = {}
    for supporting in details.get("supporting_variants") or []:
        if not isinstance(supporting, Mapping):
            continue
        names = {
            str(item.get("variant"))
            for item in supporting.get("variants") or []
            if isinstance(item, Mapping) and item.get("variant")
        }
        defines[str(supporting.get("allele"))] = names

    side: Dict[str, set] = {}
    for number, key in ((1, "haplotype1"), (2, "haplotype2")):
        for allele in details.get(key) or []:
            for variant in defines.get(str(allele), ()):
                side.setdefault(variant, set()).add(number)

    out: Dict[frozenset, str] = {}
    placed = [v for v, numbers in side.items() if len(numbers) == 1]
    for i, a in enumerate(placed):
        for b in placed[i + 1 :]:
            out[frozenset((a, b))] = "cis" if side[a] == side[b] else "trans"
    return out


def contradicted(evidence: Mapping[str, Any], pypgx_results: Any) -> Dict[str, dict]:
    """Gene -> the strongest pair whose arrangement the reads contradict.

    One pair per gene: a note names the gene's call, not each pair, and the pair with
    the most fragments behind it is the one worth quoting.
    """
    if str(evidence.get("status")) != "done":
        return {}
    by_gene: Dict[str, Dict[frozenset, str]] = {}
    found: Dict[str, dict] = {}
    for row in evidence.get("pairs") or []:
        if not isinstance(row, Mapping):
            continue
        shows = row.get("shows")
        gene = str(row.get("gene") or "").strip().upper()
        if shows not in ("cis", "trans") or not gene:
            continue
        if gene not in by_gene:
            by_gene[gene] = asserted(pypgx_results, gene)
        says = by_gene[gene].get(frozenset((str(row.get("a")), str(row.get("b")))))
        if says is None or says == shows:
            continue
        fragments = int(row.get("cis") or 0) + int(row.get("trans") or 0)
        if fragments > int(found.get(gene, {}).get("fragments") or 0):
            found[gene] = {
                "a": str(row.get("a")),
                "b": str(row.get("b")),
                "distance": row.get("distance"),
                "says": says,
                "shows": shows,
                "cis": int(row.get("cis") or 0),
                "trans": int(row.get("trans") or 0),
                "fragments": fragments,
            }
    return found


def _where(shows: str) -> str:
    return "the same chromosome" if shows == "cis" else "opposite chromosomes"


def allowed_names(evidence: Mapping[str, Any], gene: str, printed: Any) -> List[str]:
    """The diplotypes the reads' own arrangement allows, when they rule out the
    printed one.

    The pypgx lane names them with PyPGx's own ranking (docker/pypgx/read_phase.py),
    and there is more than one when a variant no fragment reaches could sit either
    way: HG00589's CYP2D6 is *1/*21 or *21/*39, both of them not the printed *2/*21.
    Nothing is said when the printed call is among them -- then the reads have not
    ruled it out, whatever a single pair showed.
    """
    entry = ((evidence.get("names") or {}).get(gene)) or {}
    allows = [str(name) for name in (entry.get("allows") or [])]
    if not allows:
        return []
    shown = str(printed or "").replace(" ", "")
    if any(_same(name, shown) for name in allows):
        return []
    return allows


def _same(left: str, right: str) -> bool:
    """Two diplotypes naming the same pair of alleles, in either order."""
    return sorted(left.split("/")) == sorted(right.split("/"))


def phenotype_note(
    evidence: Mapping[str, Any], gene: str, printed: Any
) -> Optional[str]:
    """Whether the reads' arrangement would change the phenotype, where it is known.

    A diplotype paired the wrong way is not always a patient reported the wrong way.
    HG00589's CYP2D6 is an Intermediate Metaboliser read as *2/*21, *1/*21 or
    *21/*39, so the printed label is wrong and nothing downstream of it is. Saying so
    is the difference between a note a reader must act on and one they need only
    know about.
    """
    entry = ((evidence.get("names") or {}).get(gene)) or {}
    phenotypes = [str(p) for p in (entry.get("phenotypes") or []) if p]
    if not phenotypes:
        return None
    shown = str(printed or "").strip()
    if not shown:
        return None
    if len(phenotypes) == 1 and phenotypes[0].casefold() == shown.casefold():
        return "the phenotype above is the same either way"
    return "this would make the phenotype " + _names_phrase(phenotypes)


# More readings than this is a list rather than a statement. The service already
# works out what every reading agrees on, and that is said instead: a gene with one
# decided pair and five variants no fragment reaches allows 32 arrangements, which
# belongs in neither the note beside the call nor the one-page Executive Summary.
MAX_NAMES = 3


def arrangement_phrase(found: Mapping[str, Any]) -> str:
    """What the reads' own arrangement would be called, in as few words as it takes.

    The readings themselves where there are few enough to read, and otherwise the
    part of them they all agree on -- HG00589's CYP2D6 is *1/*21 or *21/*39, and
    what both say is that one chromosome is *21.
    """
    names = [str(n) for n in (found.get("allows") or []) if n]
    if 0 < len(names) <= MAX_NAMES:
        return f"their arrangement would be {_names_phrase(names)}"
    settles = [str(a) for a in (found.get("settles") or []) if a]
    if names and settles:
        return (
            "the reads do not reach enough of its variants to name the pair, but "
            f"every arrangement they allow carries {_names_phrase(settles)}"
        )
    return ""


def settled_names(evidence: Mapping[str, Any], gene: str) -> List[str]:
    """The alleles every arrangement the reads allow agrees on, from the service."""
    entry = ((evidence.get("names") or {}).get(gene)) or {}
    return [str(a) for a in (entry.get("settles") or []) if a]


def _names_phrase(names: Sequence[str]) -> str:
    """ "*1/*21", or "*1/*21 or *21/*39" for the readings the reads leave open."""
    names = list(names)
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " or " + names[-1]


def call_note(found: Mapping[str, Any]) -> str:
    """The line beside the call."""
    note = (
        "The reads place two of this call's variants on "
        f"{_where(str(found.get('shows')))}, which this call does not"
    )
    phrase = arrangement_phrase(found)
    if phrase:
        note += f"; {phrase}"
    if found.get("phenotype_note"):
        note += f", and {found['phenotype_note']}"
    return note + " (see Alerts)."


def summary_note(found: Mapping[str, Any]) -> str:
    """The Executive Summary's note, under the call it questions."""
    return (
        "The reads disagree with how this call splits its variants between the two "
        f"chromosomes: {found.get('fragments')} fragments covering both positions put "
        f"them on {_where(str(found.get('shows')))}. Which alleles were found is not "
        "in question, only how they are paired"
        + (f", and {arrangement_phrase(found)}" if arrangement_phrase(found) else "")
        + (
            f". On that arrangement {found['phenotype_note']}"
            if found.get("phenotype_note")
            else ""
        )
        + ". See Alerts and Warnings."
    )


def annotate_genes(
    genes: Sequence[Any], evidence: Mapping[str, Any], pypgx_results: Any
) -> Dict[str, dict]:
    """Mark every row whose printed call is PyPGx's own and whose arrangement the
    reads contradict, in place; returns {gene: what the reads showed} for the alert."""
    contradictions = contradicted(evidence, pypgx_results)
    if not contradictions:
        return {}
    found: Dict[str, dict] = {}
    for row in genes or []:
        if not isinstance(row, dict) or not pypgx_called(row):
            continue
        gene = str(row.get("gene") or "").strip().upper()
        detail = contradictions.get(gene)
        if not detail:
            continue
        detail = dict(detail)
        detail["allows"] = allowed_names(evidence, gene, row.get("diplotype"))
        detail["settles"] = settled_names(evidence, gene) if detail["allows"] else []
        if detail["allows"]:
            detail["phenotype_note"] = phenotype_note(
                evidence, gene, row.get("phenotype")
            )
        row["read_phase_conflict"] = {
            "phaser": PHASER,
            "call_note": call_note(detail),
            "note": summary_note(detail),
            **detail,
        }
        found[str(row.get("gene") or gene)] = detail
    return found


def alert(found: Mapping[str, Mapping[str, Any]]) -> Optional[str]:
    """Alerts and Warnings' entry naming each gene and what the reads showed."""
    if not found:
        return None
    items = ", ".join(
        f"{html.escape(gene)} ({detail.get('fragments')} fragments, "
        f"{html.escape(_where(str(detail.get('shows'))))})"
        for gene, detail in found.items()
    )
    return (
        "<p>⚠️ <strong>The reads contradict how a call is phased:</strong> "
        f"{items}. Which chromosome each variant sits on is decided by "
        f"{PHASER}, from a reference panel rather than from these reads. Where two of "
        "a call's variants are close enough for one fragment to cover both, the reads "
        "settle it directly, and here they disagree with the call. The alleles found "
        "are not in question, only how they are paired into a diplotype, so the "
        "gene's activity may be right while the diplotype is not. This report prints "
        "the call unchanged and says so here. Checked on every call; where the reads "
        "do not reach two of its variants, nothing is said either way.</p>"
    )


def positions(found: Mapping[str, Mapping[str, Any]]) -> List[Tuple[str, str, str]]:
    """(gene, variant, variant) for each contradiction, for a provenance line."""
    return [
        (str(gene), str(detail.get("a")), str(detail.get("b")))
        for gene, detail in found.items()
    ]
