"""How common the variants behind a call are.

PyPGx names the variants that define each allele it called, and the report shows them
with the fraction of reads carrying each, but nothing said how common any of them is:
a reader could not tell a variant carried by a tenth of the world from one seen a
handful of times. That is the "Variant rarity context" line in docs/to-do.md's
Reporting section, and it is what this module adds.

A frequency is a property of the variant, not of the sample, so it is read here, at
report time, rather than written into the run's results by the pipeline. Two things
follow: a report re-rendered from an older run gains the context as well, and the
change reaches the live install without rebuilding an image, since ``pgx_app`` reads
this file from the bind-mounted checkout.

The numbers come from the annotation bundle the pipeline already builds for
app/reports/unlisted.py (docker/genome-downloader/annotation_bundle.py): gnomAD
v4.1's joint release, CC0, as ``gnomad.tsv.gz``. ``AF_grpmax_joint`` is the frequency
in the genetic ancestry group that carries the variant most often, which is the number
that answers "is this rare?" -- an overall frequency can hide an allele that is common
in one group and absent from the rest, and several pharmacogene alleles are exactly
that.

The frequency table is cut over the pharmacogenes' coding exons and their splice
sites, where unlisted.py needs it, and -- from bundle version 3 -- over every position
PharmCAT's positions VCF or PyPGx's allele table names as well. Both sets are needed,
because a star allele's core variant is as often outside an exon as in one: CYP2C19
\\*17's -806C>T is in the promoter, CYP3A5 \\*3's 6986A>G in an intron, VKORC1's
-1639G>A in the promoter. Measured over the 61 GeT-RM genomes, the exons alone reached
195 of the 306 distinct variants behind PyPGx's calls; with the called positions it is
306 of 306, and all 111 of the variants PharmCAT reads as something other than
reference as well.

``gnomad_regions.bed`` is the table's own extent, and is read to tell a position the
data does not reach from a variant gnomAD has never seen: none of the 306 is inside
the regions yet missing from gnomAD, so "not in gnomAD" in that case would assert
something the bundle cannot support. A bundle already in place is not rebuilt until
the downloader sees a new version, so a version 2 bundle is still live somewhere: it
has no such file, its table was cut over the exons alone, and ``regions.bed``
describes it exactly. Both are read, the table's own file first, and ``wider`` says
which answered, because what a missing number means depends on it.
"""

import bisect
import gzip
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

Key = Tuple[str, int, str, str]

#: Where ``pgx_app`` sees the bundle: ``./`` is mounted at ``/app``, so the checkout's
#: own ``reference/annotation`` is it. ANNOTATION_DIR overrides, as in the pipeline.
DEFAULT_BUNDLE = Path(
    os.getenv("ANNOTATION_DIR")
    or (Path(os.getenv("REFERENCE_DIR", "reference")) / "annotation")
)

GNOMAD_FILE = "gnomad.tsv.gz"
#: The intervals the frequency table was cut over: the pharmacogenes' coding exons
#: and every position PharmCAT's or PyPGx's own definitions name. Bundle version 3
#: and later write it; before that the table was cut over the exons alone, and
#: regions.bed described both, so an older bundle still answers with that.
GNOMAD_REGIONS_FILE = "gnomad_regions.bed"
REGIONS_FILE = "regions.bed"
MANIFEST_FILE = "manifest.json"

#: What a cell says when the bundle has no row for a variant whose position its
#: regions do not reach. Not "not in gnomAD": see the module docstring.
NO_DATA = "No frequency data"

#: Below this, in every genetic ancestry group, the report calls a variant rare and
#: names it in the Methodology paragraph. 1% is where CPIC's own allele-frequency
#: tables stop reporting group frequencies as actionable, and it keeps the sentence
#: short: over the 61 GeT-RM genomes it names a median of one allele per report, four
#: at most, and none in 28 of them.
RARE_BELOW = 0.01


def minimal(pos: int, ref: str, alt: str) -> Tuple[int, str, str]:
    """The same change written as few bases as it takes, which is how gnomAD writes it.

    One event has several spellings at one position. PharmCAT writes UGT1A1 *28's
    TA insertion as ``CAT -> CATAT`` and gnomAD as ``C -> CAT``; both put two bases
    into the same repeat at the same place, and an exact-key lookup of the first
    finds nothing. Trimming the shared bases off the end, and then off the front,
    gives the minimal representation both agree on (Tan et al. 2015, as bcftools
    norm and gnomAD's own pipeline produce it).

    Three of the 111 variants PharmCAT reads as other than reference over the 61
    GeT-RM genomes need this: UGT1A1 *28 and *37, and a NUDT15 insertion. Without it
    the report says "Not in gnomAD" of an allele a third of the world carries, which
    is worse than saying nothing.
    """
    while len(ref) > 1 and len(alt) > 1 and ref[-1] == alt[-1]:
        ref, alt = ref[:-1], alt[:-1]
    while len(ref) > 1 and len(alt) > 1 and ref[0] == alt[0]:
        ref, alt, pos = ref[1:], alt[1:], pos + 1
    return pos, ref, alt


def variant_key(text: Any) -> Optional[Key]:
    """``chrom-pos-ref-alt`` as the bundle keys it.

    PyPGx writes a variant as ``19-40991369-C-T``: no ``chr`` prefix, and the bundle
    has one. Bases are upper-cased because a VCF called against a soft-masked
    reference can carry them in lower case, which is a real bug we reported upstream
    (dev-notes/upstream/pypgx-lowercase-alleles.md) and which would silently cost
    every indel its frequency here too. The alleles are reduced to the minimal
    representation gnomAD stores, which is a no-op for every variant PyPGx names and
    is not for some of PharmCAT's indels.
    """
    if not isinstance(text, str):
        return None
    parts = text.strip().split("-")
    if len(parts) != 4:
        return None
    chrom, pos, ref, alt = parts
    if not pos.isdigit():
        return None
    chrom = chrom if chrom.lower().startswith("chr") else f"chr{chrom}"
    if not ref or not alt:
        return None
    return (chrom, *minimal(int(pos), ref.upper(), alt.upper()))


def frequency_text(gnomad: Optional[Mapping[str, Any]], covered: bool = True) -> str:
    """gnomAD's highest group frequency in words.

    The one wording for both tables: the variants outside the allele definitions
    (unlisted.py) and the variants behind the calls. ``covered`` is False when the
    bundle's regions do not reach the position, where absence says nothing.
    """
    if not gnomad:
        return "Not in gnomAD" if covered else NO_DATA
    af = gnomad.get("af_grpmax")
    if af is None:
        return "In gnomAD, frequency not given"
    if af == 0:
        return "In gnomAD, not seen in any group"
    pct = 100 * af
    text = f"{pct:.2g}%" if pct >= 0.01 else "under 0.01%"
    group = gnomad.get("grpmax")
    return f"{text}{f' ({group})' if group else ''}"


def _float(text: Any) -> Optional[float]:
    try:
        return float(text) if text not in (None, "", ".") else None
    except (TypeError, ValueError):
        return None


class Regions:
    """The intervals the bundle's tables were built over (BED, 0-based half-open)."""

    def __init__(self, path: Path):
        self.by_chrom: Dict[str, List[Tuple[int, int]]] = {}
        try:
            with open(path) as f:
                for line in f:
                    col = line.split()
                    if len(col) < 3 or not col[1].isdigit():
                        continue
                    self.by_chrom.setdefault(col[0], []).append(
                        (int(col[1]), int(col[2]))
                    )
        except OSError:
            self.by_chrom = {}
        # Merged on load, so one bisect answers exactly. The bundle's own intervals
        # happen not to overlap, but the answer must not rest on that: against a raw
        # sorted list a containing interval can sit any number of entries back, and a
        # fixed look-back would answer "not covered" for a position that is.
        for chrom, items in self.by_chrom.items():
            merged: List[Tuple[int, int]] = []
            for start, end in sorted(items):
                if merged and start <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], end))
                else:
                    merged.append((start, end))
            self.by_chrom[chrom] = merged

    def __bool__(self) -> bool:
        return bool(self.by_chrom)

    def covers(self, chrom: str, pos: int) -> bool:
        """Whether a 1-based position falls in any interval."""
        items = self.by_chrom.get(chrom)
        if not items:
            return False
        i = bisect.bisect_right(items, (pos, float("inf"))) - 1
        return i >= 0 and items[i][0] < pos <= items[i][1]


def _rows(path: Path, wanted: Iterable[Key]) -> Dict[Key, Dict[str, str]]:
    """The gnomAD rows for the keys asked about, in one pass over the table."""
    keys = set(wanted)
    found: Dict[Key, Dict[str, str]] = {}
    if not keys:
        return found
    with gzip.open(path, "rt") as f:
        head = f.readline().rstrip("\n").split("\t")
        for line in f:
            col = line.rstrip("\n").split("\t")
            if len(col) < 4 or not col[1].isdigit():
                continue
            key = (col[0], int(col[1]), col[2].upper(), col[3].upper())
            if key in keys:
                found[key] = dict(zip(head, col))
    return found


def _gnomad(row: Mapping[str, str]) -> Dict[str, Any]:
    """The fields frequency_text reads, named as unlisted.py's rows name them."""
    grpmax = _float(row.get("AF_grpmax_joint"))
    if grpmax is None:
        grpmax = _float(row.get("AF_joint"))
    return {
        "af": _float(row.get("AF_joint")),
        "af_grpmax": grpmax,
        "grpmax": row.get("grpmax_joint") or None,
        "filter": row.get("filter"),
    }


def wider(bundle: Optional[Path] = None) -> bool:
    """Whether this bundle's frequency table was cut wider than the coding exons.

    True when it carries its own region file, which bundle version 3 and later write
    (annotation_bundle.called_regions): then a position the data does not reach is
    neither a coding exon nor one either caller's definitions name, and calling it a
    promoter or intronic core variant would be a guess.
    """
    return (Path(bundle or DEFAULT_BUNDLE) / GNOMAD_REGIONS_FILE).exists()


def release(bundle: Optional[Path] = None) -> Optional[str]:
    """Which gnomAD release the bundle holds, for the report to name."""
    path = Path(bundle or DEFAULT_BUNDLE) / MANIFEST_FILE
    try:
        with open(path) as f:
            manifest = json.load(f)
    except (OSError, ValueError):
        return None
    source = (manifest.get("sources") or {}).get("gnomad") or {}
    return source.get("version") or None


def resolve(
    variants: Sequence[Any], bundle: Optional[Path] = None
) -> Dict[str, Dict[str, Any]]:
    """What is known about each variant named as ``chrom-pos-ref-alt``.

    ``text`` is the words for the table, ``af`` the highest group frequency as a
    number, for comparing against RARE_BELOW, and ``covered`` whether the bundle's
    regions reach the position at all.

    Returns nothing at all when the bundle is not there, so a caller can leave the
    column out rather than fill it with a message repeated down every row.
    """
    directory = Path(bundle or DEFAULT_BUNDLE)
    table = directory / GNOMAD_FILE
    if not table.exists():
        return {}
    keys: Dict[str, Key] = {}
    for text in variants:
        key = variant_key(text)
        if key:
            keys[text] = key
    if not keys:
        return {}
    rows = _rows(table, keys.values())
    regions = Regions(directory / GNOMAD_REGIONS_FILE)
    if not regions:
        regions = Regions(directory / REGIONS_FILE)
    out: Dict[str, Dict[str, Any]] = {}
    for text, key in keys.items():
        row = rows.get(key)
        # Without regions.bed nothing can be said about why a row is missing, so the
        # weaker of the two answers is the honest one.
        covered = bool(row) or (regions.covers(key[0], key[1]) if regions else False)
        gnomad = _gnomad(row) if row else None
        out[text] = {
            "text": frequency_text(gnomad, covered),
            "af": (gnomad or {}).get("af_grpmax"),
            "covered": covered,
        }
    return out


def lookup(variants: Sequence[Any], bundle: Optional[Path] = None) -> Dict[str, str]:
    """A frequency, in words, for each variant. ``resolve`` without the numbers."""
    return {k: v["text"] for k, v in resolve(variants, bundle).items()}


def _variants(genes: Sequence[Any]) -> List[Any]:
    names = []
    for row in genes:
        if not isinstance(row, Mapping):
            continue
        record = row.get("pypgx")
        if not isinstance(record, Mapping):
            continue
        for allele in record.get("supporting_variants") or []:
            if not isinstance(allele, Mapping):
                continue
            for variant in allele.get("variants") or []:
                if isinstance(variant, Mapping) and variant.get("variant"):
                    names.append(variant["variant"])
    return names


def annotate_genes(
    genes: Sequence[Any], bundle: Optional[Path] = None
) -> Dict[str, Any]:
    """Put a ``frequency`` beside each variant behind a call.

    The variant dicts are copied before the key is added: ``generator.pypgx_record``
    passes PyPGx's own lists through by reference, and the parsed results are read
    again elsewhere (read_phase.py among them).
    """
    texts = _variants(genes)
    if not texts:
        return {"status": "none", "shown": 0, "total": 0}
    found = resolve(texts, bundle)
    if not found:
        return {"status": "no_bundle", "shown": 0, "total": len(texts)}
    shown = 0
    rare: List[Dict[str, str]] = []
    seen_rare = set()
    for row in genes:
        if not isinstance(row, Mapping):
            continue
        record = row.get("pypgx")
        if not isinstance(record, Mapping):
            continue
        alleles = record.get("supporting_variants") or []
        fresh = []
        for allele in alleles:
            if not isinstance(allele, Mapping):
                fresh.append(allele)
                continue
            variants = []
            for variant in allele.get("variants") or []:
                if not isinstance(variant, Mapping):
                    variants.append(variant)
                    continue
                copy = dict(variant)
                known = found.get(copy.get("variant"))
                if known:
                    copy["frequency"] = known["text"]
                    if known["text"] != NO_DATA:
                        shown += 1
                    af = known["af"]
                    # Not `allele`: that name holds the mapping this loop rebuilds.
                    allele_name = str(allele.get("allele") or "")
                    gene = str(row.get("gene") or row.get("name") or "")
                    label = (gene, allele_name, copy.get("variant"))
                    if af is not None and af < RARE_BELOW and label not in seen_rare:
                        seen_rare.add(label)
                        rare.append(
                            {
                                "gene": gene,
                                "allele": allele_name,
                                "variant": str(copy.get("variant") or ""),
                                "frequency": known["text"],
                            }
                        )
                variants.append(copy)
            fresh.append({**allele, "variants": variants})
        record["supporting_variants"] = fresh
    return {
        "status": "done",
        "shown": shown,
        "total": len(texts),
        "release": release(bundle),
        # Whether the frequency table reaches past the coding exons: the wording of
        # what a missing number means turns on it, in the note below and in the
        # evidence table's footnote.
        "wider": wider(bundle),
        # The template prints this phrase in its footnote; one spelling, here.
        "no_data": NO_DATA,
        "rare": sorted(rare, key=lambda r: (r["gene"], r["allele"])),
    }


def _named(rare: Sequence[Mapping[str, str]]) -> str:
    """Each as a reader would say it: CYP2D6 *21 (0.41% in eas)."""
    parts = []
    for item in rare:
        name = " ".join(p for p in (item.get("gene"), item.get("allele")) if p)
        frequency = (item.get("frequency") or "").replace("% (", "% in ").rstrip(")")
        parts.append(f"{name} ({frequency})" if frequency else name)
    if len(parts) <= 1:
        return "".join(parts)
    return (
        ", ".join(parts[:-1]) + (" and " if len(parts) == 2 else ", and ") + parts[-1]
    )


def _calls(n: int) -> str:
    return (
        "1 of the alleles PyPGx called"
        if n == 1
        else f"{n} of the alleles PyPGx called"
    )


def alert(summary: Optional[Mapping[str, Any]]) -> Optional[str]:
    """Alerts and Warnings' entry: the calls resting on a variant almost nobody has.

    ``note`` is provenance -- what this run's frequency data reached, true of the run
    as a whole and said of every run. This qualifies particular calls, which is what
    Alerts and Warnings is for ("their implications can qualify and significantly
    alter the interpretation of result data"), and it is rare by construction: over
    the 61 GeT-RM genomes a median of one allele per report, four at most, none in 28
    of them. The same split ``read_phase`` makes between ``alert`` and the phase
    coverage paragraph, for the same reason.
    """
    if not summary or summary.get("status") != "done":
        return None
    rare = list(summary.get("rare") or [])
    if not rare:
        return None
    one = len(rare) == 1
    return (
        f"<p>⚠️ <strong>{'A call rests' if one else 'Calls rest'} on a rare "
        f"variant:</strong> {_calls(len(rare))} {'rests' if one else 'rest'} on a "
        "variant seen in fewer than 1 person in 100 in every gnomAD genetic ancestry "
        f"group: {_named(rare)}. A rare defining variant means the call is uncommon, "
        "not that it is less certain.</p>"
    )


def note(summary: Optional[Mapping[str, Any]]) -> Optional[str]:
    """The Methodology paragraph: how common the variants behind the calls are.

    The per-variant numbers are in the interactive report's evidence table, which the
    PDF does not carry, so what the PDF gets is the part a reader cannot work out for
    themselves: which of its calls rest on a variant almost nobody has. Named rather
    than counted, because a count of rare variants is not something anyone can act on
    while the names are.
    """
    if not summary or summary.get("status") != "done" or not summary.get("total"):
        return None
    total = int(summary["total"])
    shown = int(summary.get("shown") or 0)
    rare = list(summary.get("rare") or [])
    # Scoped to PyPGx on purpose. The evidence table is PyPGx's, and the genes
    # PharmCAT calls itself -- 19 of 23 records on a real run -- list no variants
    # anywhere in the report, so "the alleles called here" would claim a coverage
    # this paragraph does not have.
    subject = (
        "the one variant PyPGx reports behind an allele it called"
        if total == 1
        else f"the {total} variants PyPGx reports behind the alleles it called"
    )
    reached = "all of them" if shown == total else f"{shown or 'none'} of them"
    out = [
        f"<p class='run-provenance'>Of {subject}, this run's frequency data reaches "
        f"{reached}"
    ]
    if shown != total:
        # Why a variant has no number. Said only where it is true: a bundle whose
        # table reaches every position either caller can name (``wider``) leaves out
        # nothing that could be explained by the exons, and over the 61 GeT-RM
        # genomes it leaves out nothing at all.
        if shown:
            out.append(": the rest sit outside the regions it covers")
            if not summary.get("wider"):
                out.append(
                    ", which are the pharmacogenes' coding exons and their splice "
                    "sites, and a core variant is often in a promoter or an intron "
                    "instead"
                )
        else:
            out.append(
                ", whose regions do not reach them"
                if summary.get("wider")
                else ", which cover the pharmacogenes' coding exons and their splice "
                "sites: a core variant is often in a promoter or an intron instead"
            )
    if not shown:
        out.append(".</p>")
        return "".join(out)
    if rare:
        # The naming itself is an Alerts and Warnings entry (``alert`` below): it
        # qualifies particular calls, where the sentences around it describe the run.
        # Said here only as a pointer, so a reader of this page is not left thinking
        # the question was never asked. read_phase splits its alert from its coverage
        # paragraph the same way.
        one = len(rare) == 1
        out.append(
            f". {_calls(len(rare))} {'rests' if one else 'rest'} on a variant almost "
            f"nobody carries; {'it is' if one else 'they are'} named under Alerts and "
            "Warnings."
        )
    else:
        out.append(
            ", and none of the alleles PyPGx called rests on a rare variant: every "
            "one it reaches is seen in at least 1 person in 100 in some gnomAD "
            "genetic ancestry group."
        )
    out.append(
        " The genes PharmCAT called itself are not counted here: the positions"
        " behind those calls, and how common each base found is, are in the"
        " interactive report's PharmCAT evidence table.</p>"
    )
    return "".join(out)
