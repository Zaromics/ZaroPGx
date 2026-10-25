"""Variants in the pharmacogenes that no allele definition lists.

PharmCAT and PyPGx call a gene's genotype from the variants their allele definitions
name, and nothing else: a rare variant that stops the protein, if no definition lists
it, leaves the gene reported as normal function. This finds every variant the run's
VCF carries in the coding exons of the genes checked (annotation_bundle.GENES, docker/
genome-downloader), sets aside those a definition lists, and describes the rest: what
each does to the protein (bcftools csq on the gene's MANE Select transcript), how
common it is (gnomAD v4.1), AlphaMissense's score for a missense change, and ClinVar's
classification. Those predicted to stop the protein, or to damage it, are flagged.

A flag never changes a call or a phenotype; the report says what was found and that
the phenotype assumes the variant does nothing. The check reads only the VCF and the
annotation bundle the genome downloader builds (/reference/annotation): nothing about
the sample leaves the machine. Without the bundle it reports that it did not run.

"Listed" is an exact match, after splitting and left-aligning both sides (bcftools
norm) and writing both in capitals, with a position and allele in PharmCAT's
positions VCF (every allele its definitions use, and its positions of interest) or
in PyPGx's variant table (core and tag variants, and their synonyms). A different
allele at a listed position is not listed, and is marked: PharmCAT reads such a
variant as the reference for DPYD, TPMT, NUDT15, G6PD, RYR1 and CACNA1S, and leaves
the gene uncalled for the rest.

Tiers, for a variant that passes QC, is not listed, and is rare (under 1% in every
gnomAD genetic ancestry group, or absent):
  A    loss of function: stop gained, frameshift, splice donor or acceptor (the two
       intronic bases), start lost; ClinVar pathogenic or likely pathogenic with a
       review star for the gene's drug-response condition (CLINVAR_CONDITIONS; for
       CACNA1S, RYR1, F2 and F5 that condition comes of a gain of function)
  A-low  the same, but in the last 5% of the coding sequence (LOFTEE's cut-off: the
       protein may escape decay and keep its function), in a gene with a single
       coding exon, or in a segmental duplication or low-mappability region (GIAB)
  B    missense with AlphaMissense above 0.564 (its "likely pathogenic")
  B-low  missense with AlphaMissense 0.152 to 0.564; 0.152 is APF2's threshold for
       pharmacogenes (Zhou, Pirmann and Lauschke 2024), set for sensitivity
  C    effect uncertain: in-frame insertion or deletion, stop lost, splice region,
       a missense AlphaMissense has no score for
  none no predicted effect on the protein (synonymous, intronic) or AlphaMissense
       below 0.152
A gene with a variant of any of these tiers is marked beside its call (MARK_TIERS):
the report words the mark by tier, and for a gene with a near-identical neighbour
says the variant may be that gene's. Common variants, those with no predicted effect
and calls that fail QC are listed in the result but mark nothing.
"""

from __future__ import annotations

import csv
import gzip
import json
import logging
import os
import re
import statistics
import subprocess
from bisect import bisect_right
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

REFERENCE_DIR = Path(os.getenv("REFERENCE_DIR", "/reference"))
ANNOTATION_DIR = Path(os.getenv("ANNOTATION_DIR", str(REFERENCE_DIR / "annotation")))
PHARMCAT_POSITIONS_VCF = REFERENCE_DIR / "pharmcat" / "pharmcat_positions.vcf"

RARE = 0.01
AM_LIKELY_PATHOGENIC = 0.564
AM_PHARMACOGENE = 0.152
LAST_CODING_SHARE = 0.95
MIN_GQ = 20
MIN_DEPTH = 10
HET_BALANCE = (0.25, 0.75)
HOM_BALANCE = 0.9
# Depth this far over the run's median at these exons reads as two copies collapsed
# onto one (a duplication or a paralog): the call is not trusted.
MAX_DEPTH_OVER_MEDIAN = 2.5
LOSS_OF_FUNCTION = ("stop_gained", "frameshift", "splice_donor", "splice_acceptor", "start_lost")
UNCERTAIN = ("inframe_insertion", "inframe_deletion", "inframe_altering", "stop_lost", "splice_region")
# The tiers that mark a gene's call: every one with a predicted or possible effect on
# the protein, in every gene.
MARK_TIERS = ("A", "A-low", "B", "B-low", "C")
# The condition ClinVar names when its classification is about the gene's drug
# response: for these genes, a pathogenic variant is a loss of function here, but
# for those in CLINVAR_GAIN, whose condition comes of a gain of function.
CLINVAR_CONDITIONS = {
    "DPYD": ("dihydropyrimidine dehydrogenase deficiency", "fluorouracil", "capecitabine", "dpyd"),
    "G6PD": ("glucose-6-phosphate dehydrogenase deficiency", "g6pd deficiency", "anemia, nonspherocytic hemolytic"),
    "TPMT": ("thiopurine", "tpmt"),
    "NUDT15": ("thiopurine", "nudt15"),
    "BCHE": ("butyrylcholinesterase", "cholinesterase", "succinylcholine"),
    "UGT1A1": ("gilbert", "crigler", "hyperbilirubinemia"),
    "CFTR": ("cystic fibrosis", "cftr"),
    "RYR1": ("malignant hyperthermia",),
    "CACNA1S": ("malignant hyperthermia",),
    "F2": ("thrombophilia",),
    "F5": ("thrombophilia",),
}
# Its condition in the label, for a gene whose pathogenic variants gain a function:
# "loss of function" would say the opposite of what they do.
CLINVAR_GAIN = {
    "RYR1": "malignant hyperthermia susceptibility",
    "CACNA1S": "malignant hyperthermia susceptibility",
    "F2": "thrombophilia",
    "F5": "thrombophilia",
}
REVIEW_STARS = {
    "practice_guideline": 4,
    "reviewed_by_expert_panel": 3,
    "criteria_provided,_multiple_submitters,_no_conflicts": 2,
    "criteria_provided,_single_submitter": 1,
    "criteria_provided,_conflicting_classifications": 1,
}
TIER_LABELS = {
    "A": "Predicted loss of function",
    "A-low": "Possible loss of function, low confidence",
    "B": "Predicted damaging",
    "B-low": "Possibly damaging",
    "C": "Effect uncertain",
    "none": "No predicted effect on the protein",
    "common": "Common",
    "low_quality": "Call not reliable enough to assess",
}
AMINO = {
    "A": "Ala", "R": "Arg", "N": "Asn", "D": "Asp", "C": "Cys", "Q": "Gln", "E": "Glu",
    "G": "Gly", "H": "His", "I": "Ile", "L": "Leu", "K": "Lys", "M": "Met", "F": "Phe",
    "P": "Pro", "S": "Ser", "T": "Thr", "W": "Trp", "Y": "Tyr", "V": "Val", "*": "Ter",
}
COMPLEMENT = str.maketrans("ACGTN", "TGCAN")

Key = Tuple[str, int, str, str]


class NotRun(Exception):
    """The check could not run on this input; the message says why, for the report."""


# --- the "listed" alleles ----------------------------------------------------------


def _run(cmd: Sequence[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), check=True, capture_output=True, text=True, **kwargs)


# A REF or ALT of bases alone, in either case: the alleles uppercase_alleles writes in
# capitals. A symbolic allele (<DEL>) or a breakend (G]chr1:123]) is left as it is.
PLAIN_ALLELES = re.compile(r"[ACGTNacgtn*.]+(,[ACGTNacgtn*.]+)*")


def uppercase_alleles(line: str) -> str:
    """A VCF line with its REF and ALT in capitals. bcftools against a soft-masked
    FASTA (PyPGx's create-input-vcf, on pypgx_grch38.fasta) writes an indel in a
    masked stretch in lower case, chr15 74747271 at>a (CYP1A2 -2467delT), and every
    table here (PharmCAT's and PyPGx's alleles, gnomAD, ClinVar, AlphaMissense)
    writes capitals: compared as written, a listed indel came out unlisted, and its
    lookups found nothing."""
    if line.startswith("#"):
        return line
    col = line.split("\t")
    if len(col) < 5:
        return line
    for i in (3, 4):
        if PLAIN_ALLELES.fullmatch(col[i]):
            col[i] = col[i].upper()
    return "\t".join(col)


def _norm_keys(vcf_text: str, fasta: Path, work: Path, name: str) -> Set[Key]:
    """The (chrom, pos, ref, alt) of every allele of a VCF, split and left-aligned."""
    src = work / f"{name}.vcf"
    src.write_text(vcf_text)
    out = _run(
        ["bcftools", "norm", "-m", "-any", "-c", "w", "-f", str(fasta), str(src)]
    ).stdout
    keys = set()
    for line in out.splitlines():
        if line.startswith("#"):
            continue
        col = line.split("\t")
        keys.add((col[0], int(col[1]), col[3].upper(), col[4].upper()))
    return keys


def _fasta_contigs(fasta: Path) -> List[Tuple[str, int]]:
    fai = Path(str(fasta) + ".fai")
    contigs = []
    for line in fai.read_text().splitlines():
        col = line.split("\t")
        if re.fullmatch(r"chr([0-9]+|X|Y|M)", col[0]):
            contigs.append((col[0], int(col[1])))
    return contigs


def _vcf_text(records: Iterable[Tuple[str, int, str, str]], contigs) -> str:
    head = ["##fileformat=VCFv4.2"] + [f"##contig=<ID={c},length={n}>" for c, n in contigs]
    head.append("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO")
    order = {c: i for i, (c, _) in enumerate(contigs)}
    rows = sorted({r for r in records if r[0] in order}, key=lambda r: (order[r[0]], r[1]))
    return "\n".join(head + [f"{c}\t{p}\t.\t{r}\t{a}\t.\t.\t." for c, p, r, a in rows]) + "\n"


def pypgx_variant_names(data_dir: Path) -> List[str]:
    """Every GRCh38 variant name PyPGx's tables use: its variant table's names and
    synonyms, and its allele table's core and tag variants."""
    names = []
    with open(data_dir / "variant-table.csv") as f:
        for row in csv.DictReader(f):
            names.append(row["GRCh38Name"])
            names += [s for s in (row.get("GRCh38Synonym") or "").split(",")]
    with open(data_dir / "allele-table.csv") as f:
        for row in csv.DictReader(f):
            for column in ("GRCh38Core", "GRCh38Tag"):
                names += (row.get(column) or "").split(",")
    return [n.strip() for n in names if re.fullmatch(r"\s*[0-9XY]+-\d+-[ACGTN]+-[ACGTN]+\s*", n or "")]


def listed_alleles(fasta: Path, work: Path, pharmcat_vcf: Path, pypgx_data: Path) -> Tuple[Set[Key], Set[Tuple[str, int]]]:
    """(the alleles PharmCAT's and PyPGx's definitions use, the positions they read)."""
    contigs = _fasta_contigs(fasta)
    records = []
    for line in pharmcat_vcf.read_text().splitlines():
        if line.startswith("#"):
            continue
        col = line.split("\t")
        for alt in col[4].split(","):
            if alt not in (".", "*"):
                records.append((col[0], int(col[1]), col[3].upper(), alt.upper()))
    for name in pypgx_variant_names(pypgx_data):
        chrom, pos, ref, alt = name.split("-")
        records.append(("chr" + chrom, int(pos), ref.upper(), alt.upper()))
    keys = _norm_keys(_vcf_text(records, contigs), fasta, work, "listed")
    positions = {(c, p) for c, p, _, _ in records} | {(c, p) for c, p, _, _ in keys}
    return keys, positions


def pypgx_data_dir() -> Path:
    import pypgx  # in the pypgx image only

    return Path(pypgx.__file__).resolve().parent / "api" / "data"


# --- the run's variants ------------------------------------------------------------


def _contig_style(vcf: Path) -> str:
    """"chr" or "" : how the VCF names its chromosomes, from its first record or its
    header's contigs."""
    header = _run(["bcftools", "view", "-h", str(vcf)]).stdout
    names = re.findall(r"##contig=<ID=([^,>]+)", header)
    if any(n.startswith("chr") for n in names):
        return "chr"
    if any(re.fullmatch(r"[0-9]+|X|Y|MT?", n) for n in names):
        return ""
    first = subprocess.run(
        f"bcftools view -H {shlex_quote(str(vcf))} | head -1", shell=True,
        capture_output=True, text=True,
    ).stdout
    return "chr" if first.startswith("chr") or not first else ""


def shlex_quote(text: str) -> str:
    import shlex

    return shlex.quote(text)


def annotated_records(vcf: Path, fasta: Path, bundle: Path, work: Path) -> Tuple[str, Path]:
    """The run's variants in the regions, its first sample's, split, left-aligned and
    with their consequence on each MANE transcript (BCSQ). Returns (sample, path)."""
    style = _contig_style(vcf)
    regions = bundle / "regions.bed"
    if style == "":
        regions = work / "regions.nochr.bed"
        regions.write_text(
            "".join(line.removeprefix("chr") + "\n" for line in (bundle / "regions.bed").read_text().splitlines())
        )
    samples = _run(["bcftools", "query", "-l", str(vcf)]).stdout.split()
    if not samples:
        raise NotRun("The VCF has no sample, so no genotypes to check.")
    # Overlap, not position: a deletion that starts upstream of an exon's flank and
    # takes its splice site is read too. Then the alleles in capitals, before norm
    # and csq compare them with the reference (uppercase_alleles).
    selected = _run(
        ["bcftools", "view", "-T", str(regions), "--targets-overlap", "1", "-s", samples[0], str(vcf)]
    ).stdout
    capitals = work / "selected.vcf"
    capitals.write_text("".join(uppercase_alleles(line) + "\n" for line in selected.splitlines()))
    steps = [["bcftools", "view", "-Ou", str(capitals)]]
    if style == "":
        rename = work / "chr.map"
        rename.write_text("".join(f"{c}\tchr{c}\n" for c in [*map(str, range(1, 23)), "X", "Y"]) + "MT\tchrM\n")
        steps.append(["bcftools", "annotate", "--rename-chrs", str(rename), "-Ou", "-"])
    steps += [
        ["bcftools", "norm", "-m", "-any", "-c", "w", "-f", str(fasta), "-Ou", "-"],
        ["bcftools", "view", "-i", 'GT="alt"', "-Ou", "-"],
        [
            "bcftools", "csq", "-f", str(fasta), "-g", str(bundle / "transcripts.gff3.gz"),
            "--local-csq", "-p", "a", "--force", "-Ov", "-o", str(work / "annotated.vcf"), "-",
        ],
    ]
    procs = []
    stdin = None
    for i, cmd in enumerate(steps):
        p = subprocess.Popen(
            cmd, stdin=stdin, stdout=subprocess.PIPE if i < len(steps) - 1 else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        if stdin is not None:
            stdin.close()
        stdin = p.stdout
        procs.append(p)
    errors = []
    for cmd, p in zip(steps, procs):
        _, err = p.communicate()
        if p.returncode != 0:
            errors.append(f"{cmd[1]}: {err.decode(errors='replace').strip()[-400:]}")
    if errors:
        raise RuntimeError("; ".join(errors))
    return samples[0], work / "annotated.vcf"


def parse_vcf(path: Path):
    for line in path.read_text().splitlines():
        if line.startswith("#"):
            continue
        col = uppercase_alleles(line).split("\t")
        info = {}
        for item in col[7].split(";"):
            k, _, v = item.partition("=")
            info[k] = v
        fmt = dict(zip(col[8].split(":"), col[9].split(":"))) if len(col) > 9 else {}
        yield {
            "chrom": col[0], "pos": int(col[1]), "ref": col[3], "alt": col[4],
            "qual": col[5], "filter": col[6], "info": info, "format": fmt,
        }


def _ints(text: Optional[str]) -> List[int]:
    out = []
    for x in (text or "").split(","):
        try:
            out.append(int(x))
        except ValueError:
            return []
    return out


def genotype(fmt: Dict[str, str]) -> Tuple[str, int, int]:
    """(GT, copies of the alt allele, alleles called) for one split record."""
    gt = fmt.get("GT", ".")
    alleles = re.split(r"[/|]", gt)
    return gt, sum(1 for a in alleles if a == "1"), sum(1 for a in alleles if a != ".")


def depth(rec: dict) -> Optional[int]:
    fmt = rec["format"]
    if (fmt.get("DP") or ".") != ".":
        return _ints(fmt["DP"])[0] if _ints(fmt["DP"]) else None
    ad = _ints(fmt.get("AD"))
    if ad:
        return sum(ad)
    return _ints(rec["info"].get("DP"))[0] if _ints(rec["info"].get("DP")) else None


def genotype_quality(fmt: Dict[str, str]) -> Optional[int]:
    gq = _ints(fmt.get("GQ"))
    if gq:
        return gq[0]
    pl = sorted(_ints(fmt.get("PL")))
    return pl[1] - pl[0] if len(pl) >= 2 else None


def quality_problems(rec: dict, median_depth: Optional[float]) -> List[str]:
    """Why a call is not reliable enough to flag; empty when it is. A field the VCF
    does not carry is not held against it."""
    problems = []
    if rec["filter"] not in ("PASS", "."):
        problems.append(f"filtered ({rec['filter']})")
    gq = genotype_quality(rec["format"])
    if gq is not None and gq < MIN_GQ:
        problems.append(f"genotype quality {gq}")
    dp = depth(rec)
    if dp is not None and dp < MIN_DEPTH:
        problems.append(f"{dp} reads")
    if dp is not None and median_depth and dp > MAX_DEPTH_OVER_MEDIAN * median_depth:
        problems.append(f"{dp} reads, over {MAX_DEPTH_OVER_MEDIAN:g} times the median")
    ad = _ints(rec["format"].get("AD"))
    _, copies, called = genotype(rec["format"])
    if len(ad) >= 2 and sum(ad) > 0:
        share = ad[1] / sum(ad)
        if called == 2 and copies == 1 and not HET_BALANCE[0] <= share <= HET_BALANCE[1]:
            problems.append(f"{round(100 * share)}% of reads carry it")
        if copies == called == 2 and share < HOM_BALANCE:
            problems.append(f"{round(100 * share)}% of reads carry it")
    return problems


# --- what is known about each variant --------------------------------------------


def _table(path: Path, wanted: Set[Key]) -> Dict[Key, dict]:
    """The rows of a bundle table for the keys asked about."""
    found = {}
    if not path.exists() or not wanted:
        return found
    with gzip.open(path, "rt") as f:
        head = f.readline().rstrip("\n").split("\t")
        for line in f:
            col = line.rstrip("\n").split("\t")
            key = (col[0], int(col[1]), col[2].upper(), col[3].upper())
            if key in wanted:
                found[key] = dict(zip(head, col))
    return found


class Intervals:
    def __init__(self, path: Path):
        self.by_chrom: Dict[str, List[Tuple[int, int, str]]] = {}
        if path.exists():
            with gzip.open(path, "rt") as f:
                for line in f:
                    col = line.rstrip("\n").split("\t")
                    self.by_chrom.setdefault(col[0], []).append((int(col[1]), int(col[2]), col[3]))
        for items in self.by_chrom.values():
            items.sort()

    def label(self, chrom: str, start: int, end: int) -> Optional[str]:
        """The first label of an interval overlapping [start, end] (1-based)."""
        hits = [lab for s, e, lab in self.by_chrom.get(chrom, []) if s < end and e >= start]
        if not hits:
            return None
        return "segmental duplication" if "segmental duplication" in hits else hits[0]


def _float(text: Optional[str]) -> Optional[float]:
    try:
        return float(text) if text not in (None, "", ".") else None
    except ValueError:
        return None


def clinvar_entry(row: Optional[dict]) -> Optional[dict]:
    if not row:
        return None
    review = row.get("clnrevstat", "")
    return {
        "id": row.get("variation_id"),
        "significance": row.get("clnsig", "").replace("_", " "),
        "review": review.replace("_", " "),
        "stars": REVIEW_STARS.get(review, 0),
        "conditions": [c.replace("_", " ") for c in re.split(r"[|,]", row.get("clndn", "")) if c and c != "not_provided"],
    }


def clinvar_says_loss(gene: str, clinvar: Optional[dict]) -> bool:
    if not clinvar or clinvar["stars"] < 1 or gene not in CLINVAR_CONDITIONS:
        return False
    sig = clinvar["significance"].lower()
    if "conflicting" in sig or not ("pathogenic" in sig and "benign" not in sig):
        return False
    words = " ".join(clinvar["conditions"]).lower()
    return any(term in words for term in CLINVAR_CONDITIONS[gene])


# --- consequence and HGVS ----------------------------------------------------------


def consequences(rec: dict) -> List[dict]:
    """bcftools csq's consequence on each MANE transcript the variant touches."""
    out = []
    for entry in (rec["info"].get("BCSQ") or "").split(","):
        parts = entry.split("|")
        # A splice-site change has no amino-acid field: four parts, not seven.
        if len(parts) < 3 or parts[0].startswith("@"):
            continue
        out.append({
            "consequence": parts[0].lstrip("*"),
            "gene": parts[1],
            "transcript": parts[2],
            "protein": parts[5] if len(parts) > 5 else "",
        })
    return out


def protein_change(text: str, frameshift: bool = False) -> Tuple[Optional[str], Optional[int]]:
    """bcftools csq's amino-acid change ("949E>949V", "12Q>12*", a frameshift's
    "918IPTIK...>918ILPSRM*") in HGVS p. form, and the first changed residue's
    position."""
    m = re.fullmatch(r"(\d+)([A-Z*]+)(?:>(\d+)([A-Z*]+))?", text or "")
    if not m:
        return None, None
    pos = int(m.group(1))
    before, after = m.group(2), m.group(4)
    if frameshift and after:
        i = next((k for k, (x, y) in enumerate(zip(before, after)) if x != y), None)
        if i is None:
            return None, pos
        ter = f"Ter{after.index('*') - i + 1}" if "*" in after[i:] else ""
        return f"p.{AMINO.get(before[i], before[i])}{pos + i}{AMINO.get(after[i], after[i])}fs{ter}", pos + i
    if after is None and len(before) == 1:  # synonymous: csq names the residue once
        return f"p.{AMINO.get(before, before)}{pos}=", pos
    if after is None or len(before) != 1 or len(after) != 1:
        return None, pos
    if before == after:
        return f"p.{AMINO.get(before, before)}{pos}=", pos
    return f"p.{AMINO.get(before, before)}{pos}{AMINO.get(after, after)}", pos


class Transcript:
    """A MANE transcript's exons and coding span, from the bundle's transcripts.json."""

    def __init__(self, model: dict):
        self.gene = model["gene"]
        self.id = model["transcript"]
        self.refseq = model.get("refseq")
        self.chrom = model["chrom"]
        self.strand = model["strand"]
        self.exons = sorted(tuple(e) for e in model["exons"])
        cds = sorted(tuple(c) for c in model["cds"])
        self.cds_exons = len(cds)
        self.cds_start, self.cds_end = (cds[0][0], cds[-1][1]) if cds else (None, None)
        self.coding_length = sum(e - s + 1 for s, e in cds)
        # Exons in transcript order, each with the transcript position of its first base.
        order = self.exons if self.strand == "+" else list(reversed(self.exons))
        self.ordered, offset = [], 0
        for s, e in order:
            self.ordered.append((s, e, offset))
            offset += e - s + 1
        self.c1 = self._tx(self.cds_start if self.strand == "+" else self.cds_end) if cds else None

    def _tx(self, pos: int) -> Optional[int]:
        """1-based transcript position of an exonic base."""
        for s, e, offset in self.ordered:
            if s <= pos <= e:
                return offset + (pos - s if self.strand == "+" else e - pos) + 1
        return None

    def _c(self, tx: int) -> str:
        c = tx - self.c1 + 1
        if c < 1:
            return f"-{1 - c}"
        if c > self.coding_length:
            return f"*{c - self.coding_length}"
        return str(c)

    def c_position(self, pos: int) -> Optional[str]:
        if self.c1 is None:
            return None
        tx = self._tx(pos)
        if tx is not None:
            return self._c(tx)
        # Before the transcript's first base or after its last, the numbering runs on
        # (CYP2C19*17, 781 bases before NM_000769.4 starts at c.-25: c.-806).
        first, last = self.exons[0][0], self.exons[-1][1]
        if pos < first or pos > last:
            beyond = first - pos if pos < first else pos - last
            upstream = (pos < first) == (self.strand == "+")
            end_tx = 1 if upstream else self.ordered[-1][2] + self.ordered[-1][1] - self.ordered[-1][0] + 1
            return self._c(end_tx - beyond if upstream else end_tx + beyond)
        # Intronic: from the nearer exon edge, + after an exon, - before one.
        best = None
        for s, e, offset in self.ordered:
            for edge, edge_tx in ((s, offset + (1 if self.strand == "+" else e - s + 1)),
                                  (e, offset + (e - s + 1 if self.strand == "+" else 1))):
                dist = abs(pos - edge)
                if best is None or dist < best[0]:
                    best = (dist, edge, edge_tx)
        dist, edge, edge_tx = best
        after_exon = (pos > edge) == (self.strand == "+")
        return f"{self._c(edge_tx)}{'+' if after_exon else '-'}{dist}"

    def hgvs_c(self, chrom: str, pos: int, ref: str, alt: str) -> Optional[str]:
        if chrom != self.chrom or len(ref) != 1 or len(alt) != 1:
            return None
        where = self.c_position(pos)
        if where is None:
            return None
        if self.strand == "-":
            ref, alt = ref.translate(COMPLEMENT), alt.translate(COMPLEMENT)
        return f"c.{where}{ref}>{alt}"


# --- the check ---------------------------------------------------------------------


def tier_for(gene: str, consequence: str, protein_pos: Optional[int], transcript: Optional[Transcript],
             am: Optional[float], clinvar: Optional[dict], low_confidence: Optional[str]) -> str:
    kinds = set(consequence.split("&"))
    if clinvar_says_loss(gene, clinvar):
        return "A"
    if kinds & set(LOSS_OF_FUNCTION):
        late = (
            transcript is not None and protein_pos is not None and transcript.coding_length
            and protein_pos * 3 > LAST_CODING_SHARE * transcript.coding_length
            and kinds & {"stop_gained", "frameshift"}
        )
        single_exon = transcript is not None and transcript.cds_exons == 1
        return "A-low" if (late or single_exon or low_confidence) else "A"
    if "missense" in kinds and am is not None:
        if am > AM_LIKELY_PATHOGENIC:
            return "B-low" if low_confidence else "B"
        if am > AM_PHARMACOGENE:
            return "B-low"
        return "none"
    if kinds & set(UNCERTAIN) or "missense" in kinds:
        return "C"
    return "none"


def label_for(tier: str, am: Optional[float], clinvar_only: bool, gene: str = "") -> str:
    """The tier in words; a tier A ClinVar alone establishes says so."""
    if tier == "A" and clinvar_only:
        if gene in CLINVAR_GAIN:
            return f"Pathogenic for {CLINVAR_GAIN[gene]}, by ClinVar"
        return "Loss of function, by ClinVar"
    text = TIER_LABELS[tier]
    if tier in ("B", "B-low") and am is not None:
        text += f" (AlphaMissense {am:.2f})"
    return text


def check(vcf: Path, fasta: Path, work: Path, bundle: Path = ANNOTATION_DIR,
          pharmcat_vcf: Path = PHARMCAT_POSITIONS_VCF, pypgx_data: Optional[Path] = None) -> Dict[str, Any]:
    """The check's result for the run's VCF, as unlisted_variants.json carries it."""
    manifest_path = bundle / "manifest.json"
    if not manifest_path.exists():
        return {
            "status": "not_run",
            "reason": "The annotation data this check reads is not installed; the genome downloader builds it.",
        }
    manifest = json.loads(manifest_path.read_text())
    work.mkdir(parents=True, exist_ok=True)
    try:
        sample, annotated = annotated_records(vcf, fasta, bundle, work)
    except NotRun as e:
        return {"status": "not_run", "reason": str(e)}
    listed, listed_positions = listed_alleles(fasta, work, pharmcat_vcf, pypgx_data or pypgx_data_dir())
    transcripts = {
        m["gene"]: Transcript(m) for m in json.loads((bundle / "transcripts.json").read_text())
    }
    records = [r for r in parse_vcf(annotated) if consequences(r)]
    depths = [d for d in (depth(r) for r in records) if d]
    median_depth = statistics.median(depths) if len(depths) >= 20 else None

    unlisted, listed_count = [], 0
    for rec in records:
        key = (rec["chrom"], rec["pos"], rec["ref"], rec["alt"])
        if key in listed:
            listed_count += 1
            continue
        unlisted.append((key, rec))
    keys = {k for k, _ in unlisted}
    am_rows = _table(bundle / "alphamissense.tsv.gz", keys)
    gnomad_rows = _table(bundle / "gnomad.tsv.gz", keys)
    clinvar_rows = _table(bundle / "clinvar.tsv.gz", keys)
    lowconf = Intervals(bundle / "lowconf.bed.gz")

    variants = []
    for key, rec in unlisted:
        chrom, pos, ref, alt = key
        csq = consequences(rec)
        genes = list(dict.fromkeys(c["gene"] for c in csq))
        main = csq[0]
        transcript = transcripts.get(main["gene"])
        p_hgvs, p_pos = protein_change(main["protein"], "frameshift" in main["consequence"])
        gt, copies, called = genotype(rec["format"])
        ad = _ints(rec["format"].get("AD"))
        am_row, gn_row = am_rows.get(key), gnomad_rows.get(key)
        am = _float(am_row["am_pathogenicity"]) if am_row else None
        grpmax = _float(gn_row.get("AF_grpmax_joint")) if gn_row else None
        if gn_row and grpmax is None:
            grpmax = _float(gn_row.get("AF_joint"))
        clinvar = clinvar_entry(clinvar_rows.get(key))
        low = lowconf.label(chrom, pos, pos + len(ref) - 1)
        problems = quality_problems(rec, median_depth)
        clinvar_only = (
            clinvar_says_loss(main["gene"], clinvar)
            and not set(main["consequence"].split("&")) & set(LOSS_OF_FUNCTION)
        )
        if problems:
            tier = "low_quality"
        elif grpmax is not None and grpmax >= RARE:
            tier = "common"
        else:
            tier = tier_for(main["gene"], main["consequence"], p_pos, transcript, am, clinvar, low)
        variants.append({
            "chrom": chrom, "pos": pos, "ref": ref, "alt": alt,
            "gene": main["gene"], "genes": genes,
            "transcript": transcript.id if transcript else main["transcript"],
            "refseq": transcript.refseq if transcript else None,
            "consequence": main["consequence"].replace("&", ", ").replace("_", " "),
            "hgvs_c": transcript.hgvs_c(chrom, pos, ref, alt) if transcript else None,
            "hgvs_p": p_hgvs,
            "genotype": gt,
            "zygosity": {(1, 2): "heterozygous", (2, 2): "homozygous", (1, 1): "hemizygous"}.get((copies, called), gt),
            "depth": depth(rec),
            "alt_reads": ad[1] if len(ad) >= 2 else None,
            "quality": problems,
            "defined_position": (chrom, pos) in listed_positions,
            "gnomad": None if not gn_row else {
                "af": _float(gn_row.get("AF_joint")),
                "af_grpmax": grpmax,
                "grpmax": gn_row.get("grpmax_joint") or None,
                "filter": gn_row.get("filter"),
            },
            "alphamissense": None if am is None else {"score": am, "class": am_row.get("am_class", "").replace("_", " ")},
            "clinvar": clinvar,
            "low_confidence": low,
            "tier": tier,
            "label": label_for(tier, am, clinvar_only, main["gene"]),
            # Tier A on ClinVar's word alone, not on what the variant does.
            "clinvar_basis": tier == "A" and clinvar_only,
            # ClinVar classifies it, with a star, for the gene's drug response.
            "clinvar_drug_response": clinvar_says_loss(main["gene"], clinvar),
        })
    variants.sort(key=lambda v: ("A A-low B B-low C none common low_quality".split().index(v["tier"]), v["gene"], v["pos"]))
    flagged: Dict[str, List[int]] = {}
    for i, v in enumerate(variants):
        if v["tier"] in MARK_TIERS:
            for gene in v["genes"]:
                flagged.setdefault(gene, []).append(i)
    return {
        "status": "done",
        "sample": sample,
        "genes_checked": manifest.get("genes", []),
        "genes_without_transcript": manifest.get("genes_without_transcript", []),
        "variants_listed": listed_count,
        "variants": variants,
        "flagged_genes": flagged,
        "annotation": {
            "bundle_version": manifest.get("bundle_version"),
            "built": manifest.get("built"),
            "sources": {k: {"version": s.get("version"), "licence": s.get("licence")} for k, s in manifest.get("sources", {}).items()},
        },
    }


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("vcf", type=Path)
    ap.add_argument("--fasta", type=Path, default=REFERENCE_DIR / "grch38" / "Homo_sapiens_assembly38.fasta")
    ap.add_argument("--bundle", type=Path, default=ANNOTATION_DIR)
    ap.add_argument("--work", type=Path, default=Path("/tmp/unlisted"))
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    result = check(args.vcf, args.fasta, args.work, args.bundle)
    text = json.dumps(result, indent=1)
    if args.out:
        args.out.write_text(text)
    else:
        print(text)


if __name__ == "__main__":
    main()
