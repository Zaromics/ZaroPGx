# app/api/utils/consumer_array.py
"""A 23andMe or AncestryDNA export, read onto PharmCAT's own GRCh38 positions.

Pure over text, so the app can plan an upload and gatk-api can convert one from the same
code: this module is the canonical copy and the gatk-api image gets it by COPY, the
pattern `fastq_platform` already uses. Nothing here opens a file or runs a tool.

WHY rsID AND NOT COORDINATES. The obvious conversion is `bcftools convert --tsv2vcf`
followed by the liftover the GRCh37 VCF lane already has, and it is WRONG. A chip's
homozygous-reference call becomes `REF=T ALT=.`, and where the reference allele differs
between builds Picard rejects the row `MismatchedRefAllele` -- `RECOVER_SWAPPED_REF_ALT`
needs an ALT to swap with and a reference-only row has none. MEASURED: **rs6025 (Factor V
Leiden) is silently dropped**, because GRCh37 carries T as reference at that position and
GRCh38 carries C. Seven of PharmCAT's 1,226 positions lift by REF/ALT swap and every one
is exposed to it.

A chip reports OBSERVED BASES, which are a property of the DNA and not of a reference, so
keying on the rsID and translating against PharmCAT's own GRCh38 REF/ALT is both simpler
and correct: no chain, no liftover, no b37 reference, and the output is already on the
positions PharmCAT will look at. Verified on all 8 rows of a synthetic chip file, rs6025
included (`TT` -> `1/1`, homozygous Leiden, which the lift-then-convert path lost).
**1,018 of the 1,226 positions carry an rs identifier**, and all five single-position
genes do (ABCG2, F2, F5, IFNL3, VKORC1), as do CYP2C19 35/35, SLCO1B1 35/35 and
CYP4F2 20/20.

WHAT A CHIP CAN ACTUALLY DELIVER, measured end to end through the real pipeline. Five
PharmCAT genes have exactly ONE defining position -- ABCG2, F2, F5, IFNL3, VKORC1 -- and
a chip determines all five completely. But only THREE reach a report: PharmCAT's matcher
calls all five and its calls-only TSV lists all five, while **report.json's `genes`
section carries only ABCG2, IFNL3 and VKORC1**. F2 and F5 have no CPIC/DPWG/FDA guideline
annotation, so the reporter leaves them out of the gene blocks, and this repo reads
report.json by preference (docker/pharmcat/pharmcat.py). So the deliverable is ABCG2
(rosuvastatin, allopurinol), IFNL3 (peginterferon) and VKORC1 (warfarin sensitivity) --
three genes, all three with CPIC guidance -- plus F2/F5 only on the TSV lane, which is
off by default.

A chip cannot determine CYP2C9, CYP2C19, CYP2D6, TPMT, NUDT15 or DPYD, and the AMP/CAP
Tier 1 gate (app/pharmcat/amp_tier1.py) is what declines them rather than printing a
reference call built out of positions nobody read. That gate is a PREREQUISITE of this
lane, not a companion: without it PharmCAT reports `CYP2C9 Normal Metabolizer, Activity
Score 2.0` from two positions. Measured on this converter's own output: PharmCAT called
ABCG2, CYP2C19 *1/*1, IFNL3 and VKORC1; the gate withheld CYP2C19 for being unable to
assess *2 and *3; the report shows ABCG2, IFNL3 and VKORC1.

THE TRAPS, each measured with bcftools 1.24 or on a vendor's own format:

* **AncestryDNA writes the two alleles in two columns.** Fed to `--tsv2vcf` as-is,
  bcftools reads only the first and invents a second: `rs6025 1 169519049 T T` comes out
  `T N 0/1`, turning a Factor V Leiden reference call into a heterozygote against an
  allele that does not exist. The columns are joined here instead, and the vendor's shape
  is read off the row's own field count rather than trusted from a label -- a mislabelled
  AncestryDNA file read as 23andMe's shape does not error, it produces wrong genotypes.
* **The `I`/`D` insertion-deletion coding is not a sequence**, so those rows cannot be
  represented. UGT1A1*28's TA repeat is one of them. Counted, not guessed at.
* **A base matching neither REF nor ALT** is a strand or multi-allelic problem, not a
  genotype: it becomes a no-call and is counted, never forced onto the nearest allele.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

#: Genotypes that mean "the chip tried this position and failed", as the vendors write
#: them. Kept as an explicit no-call rather than dropped: a position the chip could not
#: read is not the same as one it does not carry, and the two produce different report
#: wording downstream (pharmcat_parser.position_coverage counts them apart).
NO_CALL_GENOTYPES = frozenset({"--", "-", "00", "0", "NN", "N", "..", ".", "DD", "II"})
#: The insertion/deletion coding, which names no sequence. `II`/`DD` are also in the set
#: above so a homozygous one reads as a no-call; any genotype CONTAINING I or D is
#: unrepresentable and counted separately.
INDEL_CODES = frozenset({"I", "D"})
#: AncestryDNA's header row carries no `#`, so it is recognized by its first field.
HEADER_FIRST_FIELD = "rsid"
_BASES = frozenset("ACGT")


@dataclass
class ArrayReading:
    """What a chip export turned out to hold. Every count is of ROWS."""

    genotypes: Dict[str, str] = field(default_factory=dict)
    vendor_shape: Optional[str] = None
    total: int = 0
    no_calls: int = 0
    indels: int = 0
    malformed: int = 0
    duplicated: int = 0


def array_genotypes(lines: Iterable[Any]) -> ArrayReading:
    """Read a vendor export into ``{rsid: genotype}``, with everything it dropped.

    The vendor's shape is decided by the first data row's field count -- 4 for 23andMe's
    single genotype column, 5 for AncestryDNA's two allele columns -- and a later row
    that disagrees is counted ``malformed`` and dropped, because a file that contradicts
    itself about its own format is not one to guess about.

    Chromosome and position are read and discarded on purpose: this lane keys on the
    rsID, so a vendor's chromosome coding (23andMe's older numeric 23/24/25/26) cannot
    silently lose the X-linked positions the way a coordinate-keyed conversion would.

    A genotype is upper-cased, and a repeated rsID keeps the FIRST and counts the rest:
    two different genotypes for one position cannot both be true, and picking the later
    one silently would be a choice disguised as parsing.
    """
    reading = ArrayReading()
    expected: Optional[int] = None
    for raw in lines:
        line = str(raw).rstrip("\r\n")
        # `.strip()` for the emptiness test, not `not line`: a whitespace-only line is
        # not a record, and counting it malformed inflates the dropped-row figures the
        # report quotes.
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split("\t")
        if fields[0].strip().lower() == HEADER_FIRST_FIELD:
            continue
        reading.total += 1
        if expected is None:
            if len(fields) == 4:
                expected, reading.vendor_shape = 4, "23andme"
            elif len(fields) == 5:
                expected, reading.vendor_shape = 5, "ancestrydna"
            else:
                reading.malformed += 1
                continue
        if len(fields) != expected:
            reading.malformed += 1
            continue

        rsid = fields[0].strip()
        genotype = (
            fields[3].strip()
            if expected == 4
            else fields[3].strip() + fields[4].strip()
        )
        genotype = genotype.upper()
        if not rsid.lower().startswith("rs"):
            # An internal probe id ("i3002742" on 23andMe) names no dbSNP record, so
            # nothing can be mapped from it. Not malformed -- just not usable here.
            reading.malformed += 1
            continue
        if genotype in NO_CALL_GENOTYPES:
            reading.no_calls += 1
            continue
        if any(code in genotype for code in INDEL_CODES):
            reading.indels += 1
            continue
        if not genotype or any(base not in _BASES for base in genotype):
            reading.malformed += 1
            continue
        if rsid in reading.genotypes:
            reading.duplicated += 1
            continue
        reading.genotypes[rsid] = genotype
    return reading


@dataclass(frozen=True)
class Position:
    """One PharmCAT position, as its own VCF states it."""

    chrom: str
    pos: int
    ref: str
    alts: Tuple[str, ...]
    gene: str


def positions_by_rsid(lines: Iterable[Any]) -> Dict[str, Position]:
    """``pharmcat_positions.vcf`` indexed by rs identifier.

    PharmCAT's list is the authority for where a position is and which allele is the
    reference there, so the chip is read onto it rather than the other way round. A
    record with no usable ID is skipped: it cannot be reached from a chip at all, and
    208 of the 1,226 are in that case.

    The first record for an rsID wins. PharmCAT's list does not repeat one today; if a
    release ever did, taking the first is stable and taking the last would make the
    mapping depend on file order.
    """
    found: Dict[str, Position] = {}
    for raw in lines:
        line = str(raw).rstrip("\r\n")
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) < 8:
            continue
        rsid = fields[2].strip()
        if not rsid.lower().startswith("rs") or rsid in found:
            continue
        gene = ""
        for entry in fields[7].split(";"):
            key, _, value = entry.partition("=")
            if key.strip() == "PX":
                gene = value.strip()
        try:
            position = int(fields[1])
        except (TypeError, ValueError):
            continue
        found[rsid] = Position(
            chrom=fields[0].strip(),
            pos=position,
            ref=fields[3].strip(),
            alts=tuple(a.strip() for a in fields[4].split(",") if a.strip()),
            gene=gene,
        )
    return found


@dataclass
class MappedRecords:
    """The chip's genotypes on PharmCAT's positions, and what did not map."""

    #: (rsid, position, genotype). The rsID is kept so the VCF's ID column carries it:
    #: it is how this lane mapped the row in the first place, and a reader checking a
    #: surprising call needs to find the chip row it came from.
    records: List[Tuple[str, Position, str]] = field(default_factory=list)
    off_panel: int = 0
    unmappable: List[str] = field(default_factory=list)
    genes: Dict[str, int] = field(default_factory=dict)


def map_to_positions(
    genotypes: Dict[str, str], by_rsid: Dict[str, Position]
) -> MappedRecords:
    """Translate each chip genotype into a GT against PharmCAT's own alleles.

    A base equal to the record's REF is allele 0; one equal to an ALT is its 1-based
    index; anything else makes the whole genotype UNMAPPABLE and the position is left
    out, named in ``unmappable``. That last case is the honest home for a strand problem
    or a base PharmCAT's record does not list -- forcing it onto the nearest allele would
    be inventing a genotype, and dropping it silently would hide a systematic strand
    error behind a thin result.

    ``off_panel`` counts chip rows that are not PharmCAT positions at all, which is most
    of a 600,000-row export and is not a problem; it is reported so the numbers add up.
    """
    mapped = MappedRecords()
    for rsid, genotype in genotypes.items():
        position = by_rsid.get(rsid)
        if position is None:
            mapped.off_panel += 1
            continue
        indices: List[int] = []
        for base in genotype:
            if base == position.ref:
                indices.append(0)
            elif base in position.alts:
                indices.append(position.alts.index(base) + 1)
            else:
                indices = []
                break
        if not indices:
            mapped.unmappable.append(rsid)
            continue
        call = "/".join(str(i) for i in sorted(indices))
        mapped.records.append((rsid, position, call))
        if position.gene:
            mapped.genes[position.gene] = mapped.genes.get(position.gene, 0) + 1
    mapped.records.sort(key=lambda r: (r[1].chrom, r[1].pos))
    mapped.unmappable.sort()
    return mapped


def vcf_text(
    mapped: MappedRecords,
    contig_lengths: Dict[str, int],
    sample: str = "SAMPLE",
) -> str:
    """The records as a single-sample GRCh38 VCF, header and all.

    Contig records carry lengths because PharmCAT's preprocessor normalizes against the
    reference and GATK reads the dictionary; only the contigs actually used are declared,
    so the header stays short instead of carrying GRCh38's ~3,400.

    No ``##ZaroPGx_`` coverage lines are written here. Those vouch for absence being
    MEASURED, and a chip measures nothing at a position it does not carry -- claiming
    otherwise is the exact fabrication this lane exists to avoid.
    """
    used: List[str] = []
    for _, position, _call in mapped.records:
        if position.chrom not in used:
            used.append(position.chrom)
    out = [
        "##fileformat=VCFv4.2",
        '##FILTER=<ID=PASS,Description="All filters passed">',
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
        "##source=ZaroPGx consumer array, mapped by rs identifier onto "
        "PharmCAT's positions",
    ]
    for contig in used:
        length = contig_lengths.get(contig)
        out.append(
            f"##contig=<ID={contig},length={length},assembly=GRCh38>"
            if length
            else f"##contig=<ID={contig},assembly=GRCh38>"
        )
    out.append(
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t" + (sample or "SAMPLE")
    )
    for rsid, position, call in mapped.records:
        alt = ",".join(position.alts) if position.alts else "."
        out.append(
            "\t".join(
                (
                    position.chrom,
                    str(position.pos),
                    rsid or ".",
                    position.ref,
                    alt,
                    ".",
                    "PASS",
                    ".",
                    "GT",
                    call,
                )
            )
        )
    return "\n".join(out) + "\n"
