"""What else the same genotypes could have been called, for a call PharmCAT made.

PharmCAT is handed an unphased VCF. Where a gene has two or more heterozygous positions
among the ones its allele definitions are written over, which chromosome each variant
sits on decides what the diplotype is called, and nothing in the file says. PharmCAT
reports this itself, per gene: ``phased`` is false for every call this pipeline produces,
and ``effectivelyPhased`` is false for the calls where phase could have changed the
answer -- 169 of the 1,159 calls its matcher made over the 61 GeT-RM genomes. It then
prints the diplotype its definitions explain most specifically, which is a choice, not a
reading.

Most of those it says something about. 26 it could not resolve at all and printed as
alternatives, which the report already shows (``app/pharmcat/report_json.py``'s
``unresolved_call`` joins them with "or"), and for others it raises an ``ambiguity``
message -- "The assigned genotype is ``*1/*6``; however, ``*4/*9`` cannot be ruled out" --
which the report prints under the call. **Where PharmCAT has said it, this module says
nothing**: a second copy of one fact is the mistake #226 fixed for the rarity alert.

What is left is the calls PharmCAT prints as one answer and says nothing about. Over the
61 GeT-RM genomes this module can name another reading for 82 of them, and PharmCAT's own
message already names the same one for 68. **So this speaks for 14 -- 0.23 per report**:
NAT2 5, SLCO1B1 2, NUDT15 2, TPMT 2, UGT1A1 2, G6PD 1. Small, and worth having: NA18526's
NUDT15 ``*1/*3`` (Intermediate Metabolizer) reads just as well as ``*3/*6``, which is a
Poor Metabolizer and a different thiopurine dose, and nothing in the report said so.

``app/reports/read_phase.py`` does the same job for the calls PyPGx makes, from the reads;
its module docstring states why it stops where it does -- "for a gene the report prints
PharmCAT's call for, PyPGx's arrangement is not the printed one". This is that gap, and
it is the larger half: CYP2C19, CYP2B6, DPYD, SLCO1B1, TPMT and UGT1A1 are all PharmCAT's
to call.

How a chromosome is named
-------------------------

From PharmCAT's own allele definitions, published out of its jar by
``docker/pharmcat/definitions.py``: every named allele with the base each of the gene's
positions must carry for it to fit, and every diplotype's phenotype. Three rules, each of
which a first attempt got wrong and the GeT-RM runs corrected:

- **A definition can ask for an ambiguity code.** CYP2C19 ``*2`` wants ``R`` -- A or G --
  at chr10:94775507, so comparing bases literally rejects every ``*2`` there is.
- **A position that was not read neither fits nor fails.** NUDT15 ``*5`` is defined by one
  position; in HG00277 that position has no call, so ``*5`` cannot be ruled out -- yet
  PharmCAT printed ``*6``, whose defining variant was actually seen.
- **So an allele's score is its evidence, not its definition**: how many of the positions
  it is specified at were read and match. That is what makes ``*6`` (1) beat ``*5`` (0)
  above, and why PharmCAT's own ``matchScore`` for ``*1/*6`` there is 20 and not 21 --
  ``*1`` is specified at 20 positions and 19 of them were read.

Measured over the 61 GeT-RM genomes with PharmCAT 3.4.0: of the 1,132 two-allele calls its
matcher made, this names the same diplotype PharmCAT printed, as the best-scoring
arrangement, for **1,078 (95.2%)**, and arrives at PharmCAT's own ``matchScore`` for 1,055
of those. Reproducing the printed call is what licenses naming the other one, so the other
54 say nothing at all -- the same rule ``read_phase.unreached_alternative`` applies, for
the same reason: an alternative to a call this module could not reproduce would be an
alternative to the wrong thing.

Of the 82 readings it can name, 42 would be a different phenotype -- UGT1A1 25, SLCO1B1 9,
TPMT 4, NAT2 2, CYP2B6 1, NUDT15 1 -- and 42 rest on two positions within one sequencing
fragment, so reads could settle them. That is the next piece of work and not this one:
``docker/pypgx/read_phase.py`` already counts the fragments spanning a pair for the calls
PyPGx makes, and PharmCAT's positions are in the same alignment.

Which channel
-------------

A note beside the call, and a Methodology paragraph counting them. Not an alert: nothing
here contradicts anything, which is the distinction Alerts and Warnings carries (see
``generator.py``'s channel docstring and ``read_phase.annotate_open``, which also writes
only a call note). The call note names the alternative and its phenotype where that
differs; the paragraph carries the reassurance that which alleles were found is not in
question, only how they are paired.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from app.pharmcat.report_json import iter_gene_blocks
from app.reports.copy_number import pharmcat_called

logger = logging.getLogger(__name__)

#: Where ``docker/pharmcat/definitions.py`` publishes the digest. Both containers mount
#: the same ``./data``, so the app reads the file the pharmcat service wrote.
DIGEST = Path(
    os.getenv("PHARMCAT_DEFINITIONS", "/data/pharmcat/allele-definitions.json.gz")
)

#: The digest shape this module understands; ``definitions.py`` writes the same number.
FORMAT = 1

#: bp apart within which one 2x150 fragment can carry both positions, as
#: ``docker/pypgx/read_phase.py`` counts them. Only used to say whether reads could ever
#: settle an arrangement -- nothing here reads any.
REACH = 1000

#: 2^(n-1) arrangements are enumerated, so this is 32 for a gene with six heterozygous
#: positions. Past it the question is not asked: every such call in the GeT-RM set is
#: DPYD's or RYR1's, which PharmCAT reports as separate haplotypes anyway.
MAX_HETS = 6

#: Up to this many alternatives are named one by one; past it they are counted.
MAX_NAMES = 2

#: IUPAC ambiguity codes, as the definitions use them for a position an allele accepts
#: either base at.
IUPAC = {
    "R": frozenset("AG"),
    "Y": frozenset("CT"),
    "S": frozenset("GC"),
    "W": frozenset("AT"),
    "K": frozenset("GT"),
    "M": frozenset("AC"),
    "B": frozenset("CGT"),
    "D": frozenset("AGT"),
    "H": frozenset("ACT"),
    "V": frozenset("ACG"),
    "N": frozenset("ACGT"),
}

Locus = Tuple[Any, Any]

_cache: Dict[Tuple[str, int, int], Optional[Dict[str, Any]]] = {}


def accepts(requirement: Any, base: Any) -> bool:
    """Whether a definition's requirement is met by the base that was read."""
    if not isinstance(requirement, str) or not isinstance(base, str):
        return False
    if requirement == base:
        return True
    # "AGGAGTC or AGGAGTCGGAGTC": NUDT15 *3 takes the 6 bp repeat in either state.
    if " or " in requirement:
        return base in [part.strip() for part in requirement.split(" or ")]
    if len(requirement) == 1 and len(base) == 1:
        return base in IUPAC.get(requirement, frozenset())
    return False


def digest(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """The published definitions, or None where none have been published.

    Cached on the file's size and modification time, so a rebuilt digest is picked up
    without restarting and a render never pays for the 5 MB of JSON twice.
    """
    target = Path(path or DIGEST)
    try:
        stat = target.stat()
    except OSError:
        return None
    key = (str(target), stat.st_mtime_ns, stat.st_size)
    if key in _cache:
        return _cache[key]
    data: Optional[Dict[str, Any]] = None
    try:
        with gzip.open(target, "rt", encoding="utf-8") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, Mapping) and loaded.get("format") == FORMAT:
            data = dict(loaded)
        else:
            logger.warning("%s is not a digest this release reads", target)
    except (OSError, ValueError) as exc:
        logger.warning("Could not read %s: %s", target, exc)
    _cache.clear()  # one file, one entry: the old mtime's copy is dead weight
    _cache[key] = data
    return data


class Definition:
    """One gene's allele definitions, as the digest carries them."""

    def __init__(self, entry: Mapping[str, Any]) -> None:
        self.version = entry.get("version")
        self.loci: List[Locus] = [
            (locus.get("c"), locus.get("p"))
            for locus in entry.get("loci") or []
            if isinstance(locus, Mapping)
        ]
        self.rsids: Dict[Locus, Optional[str]] = {
            (locus.get("c"), locus.get("p")): locus.get("r")
            for locus in entry.get("loci") or []
            if isinstance(locus, Mapping)
        }
        self.alleles: List[Tuple[str, Dict[int, str]]] = []
        for allele in entry.get("alleles") or []:
            if not isinstance(allele, Mapping) or not allele.get("n"):
                continue
            wants = allele.get("w")
            if not isinstance(wants, Mapping):
                continue
            needs: Dict[int, str] = {}
            for index, base in wants.items():
                try:
                    needs[int(index)] = base
                except (TypeError, ValueError):
                    continue
            self.alleles.append((str(allele["n"]), needs))
        self.phenotypes: Mapping[str, str] = entry.get("phenotypes") or {}
        #: Where each allele sits in the definitions, which is the order PharmCAT
        #: itself prints a diplotype in: *3B before *3C, *1 before *80+*28. Without
        #: it the two names come out in the order the VCF happens to write its
        #: bases, and "*28/*80" beside a printed "*1/*80+*28" reads as a third thing.
        self.order: Dict[str, int] = {
            allele: index for index, (allele, _) in enumerate(self.alleles)
        }

    def name(self, carried: Sequence[Optional[str]]) -> Tuple[Optional[str], int]:
        """The allele this chromosome is called, and the evidence behind that name.

        Ties go to the allele the definitions list first, which is PharmCAT's own
        order: CYP4F2 ``*3`` before ``*17``, both defined by one position.
        """
        best: Optional[str] = None
        best_score = -1
        for allele, needs in self.alleles:
            score = 0
            for index, requirement in needs.items():
                if index >= len(carried):
                    continue
                base = carried[index]
                if base is None:
                    continue  # not read: no evidence either way
                if not accepts(requirement, base):
                    score = -1
                    break
                score += 1
            if score > best_score:
                best, best_score = allele, score
        return best, max(best_score, 0)

    def phenotype(self, left: str, right: str) -> Optional[str]:
        """The phenotype PharmCAT's own table gives this pair of alleles."""
        return self.phenotypes.get("/".join(sorted([left, right])))


def read_bases(gene: Mapping[str, Any]) -> Dict[Locus, List[str]]:
    """(chromosome, position) -> the two bases read there, for the positions with a call.

    A position with no call is left out, which is how it becomes "not read" above: it is
    evidence neither for nor against any allele defined over it.
    """
    out: Dict[Locus, List[str]] = {}
    for variant in gene.get("variants") or []:
        if not isinstance(variant, Mapping):
            continue
        call = variant.get("call")
        if not isinstance(call, str):
            continue
        bases = [base.strip() for base in call.replace("|", "/").split("/")]
        if len(bases) != 2 or any(base in ("", ".") for base in bases):
            continue
        out[(variant.get("chromosome"), variant.get("position"))] = bases
    return out


def _chromosome(
    definition: Definition,
    read: Mapping[Locus, List[str]],
    assignment: Mapping[Locus, str],
) -> List[Optional[str]]:
    """The base one chromosome carries at each of the gene's positions, None if unread.

    A homozygous position is on both chromosomes, which is why it is not an arrangement
    question and why leaving it out would make every allele defined over it unfittable.
    """
    out: List[Optional[str]] = []
    for locus in definition.loci:
        bases = read.get(locus)
        if not bases:
            out.append(None)
        elif bases[0] == bases[1]:
            out.append(bases[0])
        else:
            out.append(assignment.get(locus))
    return out


def arrangements(
    definition: Definition, gene: Mapping[str, Any]
) -> Tuple[List[Dict[str, Any]], List[Locus]]:
    """Every way this gene's heterozygous positions split over two chromosomes, named.

    Returns the arrangements that name both chromosomes -- one that cannot is not a
    diplotype PharmCAT would have considered -- and the heterozygous positions.
    """
    read = read_bases(gene)
    hets = [locus for locus in definition.loci if len(set(read.get(locus) or [])) == 2]
    if not hets or len(hets) > MAX_HETS:
        return [], hets
    first, rest = hets[0], hets[1:]
    found: List[Dict[str, Any]] = []
    for mask in range(1 << len(rest)):
        assignment = {first: read[first][0]}
        for index, locus in enumerate(rest):
            assignment[locus] = read[locus][mask >> index & 1]
        other = {
            locus: next(base for base in read[locus] if base != carried)
            for locus, carried in assignment.items()
        }
        left, left_score = definition.name(_chromosome(definition, read, assignment))
        right, right_score = definition.name(_chromosome(definition, read, other))
        if left is None or right is None:
            continue
        first_name, second_name = sorted(
            (left, right), key=lambda allele: definition.order.get(allele, 0)
        )
        found.append(
            {
                "names": frozenset({left, right}),
                "shown": f"{first_name}/{second_name}",
                "score": left_score + right_score,
                "phenotype": definition.phenotype(left, right),
            }
        )
    return found, hets


#: PharmCAT's own ambiguity message: "The assigned genotype is *1/*6; however, *4/*9
#: cannot be ruled out ...". It raises one per gene, with the drugs it bears on, and the
#: report prints them under the call. Where it has said this, saying it again is a second
#: copy of one fact -- the mistake #226 fixed for the rarity alert.
AMBIGUITY = "cannot be ruled out"


def pharmcat_warned(gene: Mapping[str, Any]) -> bool:
    """Whether PharmCAT itself said this call's arrangement is not the only reading.

    Over the 61 GeT-RM genomes it says so for 68 of the 82 calls this module can name
    an alternative for, naming the same alternative in every one of the 68. The 14 it
    is silent about are what is left to say: NAT2 5, SLCO1B1 2, NUDT15 2, TPMT 2,
    UGT1A1 2, G6PD 1.
    """
    for message in gene.get("messages") or []:
        if not isinstance(message, Mapping):
            continue
        if str(message.get("exception_type") or "").lower() == "ambiguity":
            return True
        if AMBIGUITY in str(message.get("message") or ""):
            return True
    return False


def _printed(gene: Mapping[str, Any]) -> Optional[Tuple[str, str, Optional[str]]]:
    """The one diplotype PharmCAT printed for this gene, with its phenotype.

    None where it printed several (the report already shows those as "or"), or where the
    call is a list of haplotypes rather than a diplotype, which is how PharmCAT reports
    DPYD and RYR1.
    """
    candidates = gene.get("sourceDiplotypes")
    if not isinstance(candidates, Sequence) or len(candidates) != 1:
        return None
    source = candidates[0]
    if not isinstance(source, Mapping):
        return None
    first = (source.get("allele1") or {}).get("name")
    second = (source.get("allele2") or {}).get("name")
    if not first or not second:
        return None
    phenotypes = source.get("phenotypes") or []
    phenotype = str(phenotypes[0]) if phenotypes else None
    return str(first), str(second), phenotype


def unmeasured(
    report: Optional[Mapping[str, Any]], path: Optional[Path] = None
) -> Dict[str, Any]:
    """Every call PharmCAT made that rests on an arrangement nothing measured.

    ``{"status": ..., "genes": {gene: finding}, "counts": {...}}``. "none" where no
    definitions have been published or the report carries no genes of PharmCAT's own.
    """
    genes = (report or {}).get("genes")
    if not isinstance(genes, Mapping):
        return {"status": "none"}
    published = digest(path)
    if not published:
        return {"status": "none", "reason": "no allele definitions are published"}
    definitions = published.get("genes") or {}
    cache: Dict[str, Optional[Definition]] = {}
    found: Dict[str, Any] = {}
    counts = {
        "calls": 0,
        "unphased": 0,
        "pharmcat_warned": 0,
        "named": 0,
        "within_reach": 0,
        "differs": 0,
    }
    # Both report shapes, through the one walker that knows them: PharmCAT 3.x writes
    # genes -> symbol, 2.x wrote genes -> {CPIC|DPWG|FDA} -> symbol, and a gene in the
    # older shape appears under each guideline source that annotates it with the same
    # block, so the first of them answers for it.
    for block in iter_gene_blocks(genes):
        symbol, gene = block.gene_symbol, block.gene_data
        if symbol in found or block.call_source != "MATCHER":
            continue
        printed = _printed(gene)
        if printed is None:
            continue
        counts["calls"] += 1
        if gene.get("phased") or gene.get("effectivelyPhased"):
            continue
        counts["unphased"] += 1
        if pharmcat_warned(gene):
            # PharmCAT has already named the other reading, and the report prints its
            # messages under the call. 68 of the 82 in the GeT-RM set.
            counts["pharmcat_warned"] += 1
            continue
        if symbol not in cache:
            entry = definitions.get(symbol)
            cache[symbol] = Definition(entry) if isinstance(entry, Mapping) else None
        definition = cache[symbol]
        if definition is None:
            continue
        first, second, phenotype = printed
        candidates, hets = arrangements(definition, gene)
        if not candidates or len(hets) < 2:
            continue
        top = max(candidate["score"] for candidate in candidates)
        printed_names = frozenset({first, second})
        # Reproducing PharmCAT's own call, as the best-scoring arrangement, is the
        # licence to name another one. Without it this says nothing.
        if not any(
            candidate["names"] == printed_names and candidate["score"] == top
            for candidate in candidates
        ):
            continue
        # Keyed on the pair of names, not on the text: with three heterozygous
        # positions two splits can name the same two alleles in either order, and
        # "*4/*46" beside "*46/*4" would read as two readings where there is one.
        others: List[Dict[str, Any]] = []
        seen = {printed_names}
        for candidate in candidates:
            if candidate["names"] in seen:
                continue
            seen.add(candidate["names"])
            others.append(
                {"diplotype": candidate["shown"], "phenotype": candidate["phenotype"]}
            )
        if not others:
            continue
        positions = sorted(position for _, position in hets)
        closest = min(
            second_position - first_position
            for first_position, second_position in zip(positions, positions[1:])
        )
        differs = [
            other
            for other in others
            if other["phenotype"] and phenotype and other["phenotype"] != phenotype
        ]
        counts["named"] += 1
        if closest <= REACH:
            counts["within_reach"] += 1
        if differs:
            counts["differs"] += 1
        found[str(symbol)] = {
            "printed": f"{first}/{second}",
            "phenotype": phenotype,
            "alternatives": others,
            "positions": [
                {
                    "chromosome": chromosome,
                    "position": position,
                    "rsid": definition.rsids.get((chromosome, position)),
                }
                for chromosome, position in hets
            ],
            "closest": closest,
            "within_reach": closest <= REACH,
            "definitions": definition.version,
        }
    counts["pharmcat_version"] = published.get("pharmcat_version") or ""
    return {"status": "done", "genes": found, "counts": counts}


def _phrase(alternatives: Sequence[Mapping[str, Any]]) -> str:
    """ "*3B/*3C", or "*1/*2 or *35/*17", or "3 other readings"."""
    names = [str(alternative.get("diplotype")) for alternative in alternatives]
    if len(names) > MAX_NAMES:
        return f"{len(names)} other readings"
    if len(names) == 1:
        return names[0]
    return " or ".join(names)


def call_note(finding: Mapping[str, Any]) -> str:
    """The line beside the call, in one short sentence.

    It prints in the gene table's Diplotype column, 19% of the table's width, which is
    what ``read_phase.open_note`` was cut down to fit (#213). So the phenotype is named
    only where it differs from the printed call's -- that one is in the same row's
    Phenotype cell, two columns over -- and the rest of what there is to say is in the
    Methodology paragraph ``coverage_note`` writes.
    """
    alternatives = finding.get("alternatives") or []
    note = f"Pairing not measured; the other reading is {_phrase(alternatives)}"
    if len(alternatives) == 1:
        phenotype = alternatives[0].get("phenotype")
        if phenotype and phenotype != finding.get("phenotype"):
            note += f", {phenotype}"
    return note + "."


def _same(left: Any, right: Any) -> bool:
    """Two diplotypes naming the same pair of alleles, in either order."""
    return sorted(str(left).replace(" ", "").split("/")) == sorted(
        str(right).replace(" ", "").split("/")
    )


def annotate_genes(
    rows: Sequence[Any],
    report: Optional[Mapping[str, Any]],
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Mark every row whose printed call is PharmCAT's own and rests on an unmeasured
    arrangement, in place; returns what ``coverage_note`` needs.

    Left alone where ``caller_agreement`` already said the printed call is one of two
    readings: two notes making the same shape of point about one call, one from the
    callers disagreeing and one from the definitions, would read as two separate doubts.
    """
    result = unmeasured(report, path)
    found = result.get("genes") or {}
    if not found:
        return result
    marked: Dict[str, Any] = {}
    for row in rows or []:
        if not isinstance(row, dict) or not pharmcat_called(row):
            continue
        if row.get("caller_disagreement"):
            continue
        finding = found.get(str(row.get("gene") or "").strip().upper())
        if not finding:
            continue
        # The note is about the call the row shows. A row showing something else --
        # the recommendation call where the source call was not one diplotype, or a
        # row this report built from another lane -- is a different statement, and a
        # note naming the alternative to a call the reader cannot see is worse than
        # silence. app/reports/read_phase.unreached_alternative refuses on the same
        # ground.
        shown = row.get("diplotype_display") or row.get("diplotype")
        if not shown or not _same(shown, finding["printed"]):
            continue
        row["pharmcat_phase"] = dict(finding, call_note=call_note(finding))
        marked[str(row.get("gene") or "").strip().upper()] = finding
    # The paragraph counts what the report printed, not what could have been printed.
    # A finding reaches no row when another note already speaks for that call, or when
    # the row shows a different diplotype, or when the gene has no row at all -- and a
    # paragraph saying "for 1 more this report names it beside the call" beside no such
    # note is the inconsistency a real NA18526 render turned up.
    counts = result.get("counts") or {}
    counts["could_name"] = int(counts.get("named") or 0)
    counts["named"] = len(marked)
    counts["within_reach"] = sum(
        1 for finding in marked.values() if finding.get("within_reach")
    )
    counts["differs"] = sum(
        1
        for finding in marked.values()
        for alternative in finding.get("alternatives") or []
        if alternative.get("phenotype")
        and finding.get("phenotype")
        and alternative["phenotype"] != finding["phenotype"]
    )
    return result


def coverage_note(result: Optional[Mapping[str, Any]]) -> Optional[str]:
    """The Methodology paragraph: what phase this run had, and what that costs.

    In the run-provenance family (``generator.py``'s channel docstring), beside
    ``read_phase.coverage_note``, which says the same kind of thing about the calls PyPGx
    makes. Silent when PharmCAT made no call whose arrangement was in question, which is
    most of the time for a small gene panel and never for a genome.
    """
    counts = (result or {}).get("counts") or {}
    calls = int(counts.get("calls") or 0)
    unphased_calls = int(counts.get("unphased") or 0)
    if not calls or not unphased_calls:
        return None
    warned = int(counts.get("pharmcat_warned") or 0)
    named = int(counts.get("named") or 0)
    within = int(counts.get("within_reach") or 0)
    version = str(counts.get("pharmcat_version") or "")
    note = (
        "The file PharmCAT read carries no phase, so where a gene has two or more "
        "heterozygous defining positions, which chromosome each variant sits on is not "
        "in the data: PharmCAT reports the diplotype its allele definitions explain "
        f"most specifically. {unphased_calls} of the {calls} calls it made here "
        f"{'rests' if unphased_calls == 1 else 'rest'} on such an arrangement. "
    )
    # Said in this order because it is the order of authority: PharmCAT's own warning
    # first, this report's reading of its definitions second, and silence last.
    if warned:
        note += (
            f"PharmCAT names the other reading itself for "
            f"{'it' if warned == 1 and unphased_calls == 1 else f'{warned} of them'}, "
            "in its notes under the call. "
        )
    if named:
        # "more" only after PharmCAT's own count, or it is more than nothing.
        note += (
            f"For {named} {'more' if warned else 'of them'}, read from the same "
            "definitions, this report names it beside the call. "
        )
    elif warned < unphased_calls and not int(counts.get("could_name") or 0):
        # Only where nothing was found, not merely where nothing was printed: a
        # reading another note already speaks for is not "no other reading".
        note += (
            "For the rest, these definitions allow no other reading of the variants "
            "that were found. "
        )
    note += "Which alleles were found is not in question, only how they are paired"
    if within:
        note += (
            f"; {within} of the readings named here "
            f"{'rests' if within == 1 else 'rest'} on two positions close enough for "
            "one sequencing fragment to carry both, so reads could settle "
            f"{'it' if within == 1 else 'them'}"
        )
    if version:
        note += f". The definitions are PharmCAT {version}'s, from ClinPGx"
    return note + "."
