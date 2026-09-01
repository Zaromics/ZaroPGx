import gzip
import html
import logging
import os
import re
import subprocess
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from werkzeug.utils import secure_filename

# Optional import: pysam for rich header parsing (VCF/BAM/CRAM). Fallbacks are provided.
try:
    import pysam  # type: ignore

    _HAS_PYSAM = True
except Exception:  # optional dependency at runtime
    pysam = None  # type: ignore
    _HAS_PYSAM = False

# Import models from models.py to ensure consistency
from app.api.models import (
    FileInfo,
    FileType,
    FormatSpecificInfo,
    GenomicFileHeader,
    MetadataInfo,
    ProgramInfo,
    SequenceInfo,
    SequencingProfile,
    VCFHeaderInfo,
)
from app.api.utils.file_utils import has_index_file, is_compressed_file
from app.api.utils.header_inspector import inspect_header
from app.utils.env import env_flag

# Configure logging
logger = logging.getLogger(__name__)


def safe_upload_basename(filename: Optional[str]) -> str:
    """Return a filesystem- and glob-safe basename for a client-supplied name.

    Why this matters beyond ordinary hygiene: the saved path becomes
    `--input` to Nextflow, and `pipelines/pgx/main.nf` opens the run with
    `Channel.fromPath(params.input)`. `fromPath` treats its argument as a
    *glob pattern*, so a client that names its upload `*.vcf` produces
    `/data/uploads/upload_*.vcf`, which fans the channel out across every
    other upload sitting in that directory - other patients' files are then
    analysed and published into this job's report. Stripping glob
    metacharacters here is what keeps one upload to one run.

    (Shell metacharacters are a separate question and are *not* the reason for
    this call: Nextflow escapes `path`-typed inputs before interpolating them
    into a task script, so `;` and friends in a filename do not reach a shell
    through that route. They are removed here anyway, because nothing
    downstream should have to rely on that.)

    werkzeug's `secure_filename` is used rather than a bespoke filter so this
    layer matches the app's other upload route (app/main.py). It can return an
    empty string for a wholly pathological name (`..`, `...`), so a generated
    name is substituted in that case.
    """
    cleaned = secure_filename(filename or "")
    return cleaned or f"{uuid.uuid4().hex}.dat"


# Tokens that name T2T-CHM13 inside a DETECTED build string. header_inspector
# reports the canonical `T2T-CHM13v2`; the bare `t2t` spelling is included because
# these are matched against a short build label, not against the free-text
# `##reference=` path header_inspector.ASSEMBLY_NAME_TOKENS reads (where three
# characters would be a substring rather than a token, which is why that table
# deliberately carries neither `t2t` nor bare `hs1`).
T2T_BUILD_TOKENS = ("chm13", "t2t")

# The three spellings a GRCh37-aligned file's build label arrives under. `b37` is
# safe here for the same reason `t2t` is: a build label, not a path.
GRCH37_BUILD_TOKENS = ("grch37", "hg19", "b37")

# The two spellings a gVCF arrives under. GATK writes `sample.g.vcf[.gz]`, whose last
# suffix is `.vcf`; other callers write `sample.gvcf[.gz]`. Only the second pair ever
# reached the extension table in _detect_file_type, so the GATK spelling was typed VCF
# and analysed as an ordinary variant call set - see _detect_file_type for why that is a
# wrong answer rather than a wrong label.
GVCF_NAME_SUFFIXES = (".gvcf", ".gvcf.gz", ".g.vcf", ".g.vcf.gz")

# The header record every gVCF carries and no plain VCF does: one per
# reference-confidence band, e.g. `##GVCFBlock0-1=minGQ=0(inclusive),...`.
GVCF_HEADER_MARKER = "##GVCFBlock"

# Bounds on the header reads below, so a hostile or corrupt file cannot turn a type
# check into an unbounded read.
#
# The line cap was 2000, justified as "far above any real VCF header (a few hundred
# lines)". That is false for the build this pipeline actually runs on:
# Homo_sapiens_assembly38.fasta - the GRCh38 full analysis set that genome-downloader
# stages and that /gvcf-to-vcf genotypes against - has 3,366 contigs, so a VCF called
# against it carries ~3,400 `##contig` records before the first data row. At 2000 the
# scans below ran out of budget inside the contig block, which is not a corner case,
# it is the ordinary GRCh38 file. 20000 clears that with an order of magnitude spare
# and still cannot be turned into an unbounded read.
GVCF_HEADER_SCAN_LINES = 20000
GVCF_HEADER_MAX_LINE_BYTES = 64 * 1024


def _header_declares_gvcf_blocks(file_path: Path) -> bool:
    """True when a VCF-shaped file's header carries a ``##GVCFBlock`` record.

    Bounded to the header: it stops at the first line that is not a ``##`` meta line -
    the ``#CHROM`` row, or a data row in a file with no header at all - so it reads the
    same few kilobytes ``inspect_header`` already reads and never touches the variant
    records.

    Never raises. A file that cannot be read here is simply "not known to be a gVCF" and
    falls through to the ordinary extension logic; deciding a file's type must not be the
    thing that turns an unreadable upload into a 500.

    Both bounds fail *closed* in the same direction - a header longer than
    GVCF_HEADER_SCAN_LINES lines, or a single line past GVCF_HEADER_MAX_LINE_BYTES,
    ends the scan as "not a gVCF" rather than as an error. That is the safe direction
    for a malformed file and the wrong one for a pathological gVCF, so the caps are set
    well clear of a real GRCh38 header (see GVCF_HEADER_SCAN_LINES, which had to be
    raised once already for exactly this reason) and the name rule in _is_gvcf covers
    the ordinary case without reading anything at all.

    In practice this returns almost immediately on a real gVCF: htsjdk sorts
    ``##GVCFBlock`` into the general metadata block, ahead of the INFO/FORMAT/contig
    records, so the marker is within the first few lines. The budget only matters for
    files that are NOT gVCFs, where the whole header is read before answering False.
    """
    try:
        opener = gzip.open if str(file_path).lower().endswith(".gz") else open
        with opener(file_path, "rt", errors="ignore") as handle:
            for _ in range(GVCF_HEADER_SCAN_LINES):
                line = handle.readline(GVCF_HEADER_MAX_LINE_BYTES)
                if not line or not line.startswith("##"):
                    return False
                if line.startswith(GVCF_HEADER_MARKER):
                    return True
    except Exception as e:
        logger.debug(f"Could not read {file_path} for a {GVCF_HEADER_MARKER}: {e}")
    return False


# BCF2's container magic: the (decompressed) stream opens with `BCF` plus a major and
# a minor version byte. htslib has written major version 2 since 2013; BCF1 is not
# produced by anything in this stack's lifetime and is not recognised here.
BCF_MAGIC = b"BCF\x02"

# Cap on the BCF header text read below, for the same reason as
# GVCF_HEADER_MAX_LINE_BYTES: the length field is read out of the file itself, so a
# corrupt or hostile one could otherwise ask for an arbitrary allocation. Far above any
# real header (a few hundred kilobytes even with every ALT contig declared).
BCF_HEADER_MAX_BYTES = 4 * 1024 * 1024


def _open_bcf_stream(file_path: Path):
    """Open a possibly-BGZF file as a binary stream, deciding from the magic bytes.

    A BCF written by htslib is BGZF-framed, which is valid gzip, so Python's gzip
    module reads it; `bcftools view -Ou` writes the same container uncompressed. The
    extension decides nothing here on purpose - the stored upload name is sanitised
    and can reach disk with no usable suffix at all.
    """
    with open(file_path, "rb") as probe:
        magic = probe.read(2)
    return gzip.open(file_path, "rb") if magic == b"\x1f\x8b" else open(file_path, "rb")


def _read_bcf_header_text(file_path: Path) -> Optional[str]:
    """Return a BCF's own header text, or None when the file is not a BCF.

    After the magic above, BCF2 carries a little-endian uint32 giving the length of the
    VCF-style header text that follows. Reading those nine bytes is what lets a
    *binary* file answer the same question `_header_declares_gvcf_blocks` asks of a
    text VCF: that function opens the file as gzip-or-text and scans for `##` lines, so
    it answers False for every BCF no matter what the header actually says.

    Deliberately not pysam and not `bcftools view -h`. Both are optional in this
    process (see the pysam import at the top of this module, and header_inspector's
    bcftools fallback), and this runs inside `_detect_file_type`, which must reach a
    verdict on a host that has neither.

    Never raises: an unreadable, truncated or non-BCF file is simply "not a BCF here",
    and falls through to the ordinary extension logic. Deciding a file's type must not
    be the thing that turns an unreadable upload into a 500.

    The byte cap fails closed in the same direction as `_header_declares_gvcf_blocks`'s
    line caps: a header longer than BCF_HEADER_MAX_BYTES is read only up to it, so a
    `##GVCFBlock` beyond that point is missed rather than raised.
    """
    try:
        with _open_bcf_stream(file_path) as handle:
            if handle.read(len(BCF_MAGIC)) != BCF_MAGIC:
                return None
            handle.read(1)  # minor version byte
            length_bytes = handle.read(4)
            if len(length_bytes) < 4:
                return None
            text_length = int.from_bytes(length_bytes, "little")
            text = handle.read(min(text_length, BCF_HEADER_MAX_BYTES))
        return text.decode("utf-8", errors="ignore")
    except Exception as e:
        logger.debug(f"Could not read {file_path} as a BCF: {e}")
        return None


def _looks_like_bcf(file_path: Path) -> bool:
    """True when the file's first bytes say BCF, whatever it is named.

    Four bytes, bounded, never raises - the same shape as the BAM/CRAM magic reads in
    `_detect_file_type`, which cannot see a BCF because it is BGZF-compressed and they
    read the raw file.
    """
    try:
        with _open_bcf_stream(file_path) as handle:
            return handle.read(len(BCF_MAGIC)) == BCF_MAGIC
    except Exception as e:
        logger.debug(f"Could not read {file_path} for BCF magic bytes: {e}")
        return False


def _bcf_header_declares_gvcf_blocks(file_path: Path) -> bool:
    """The BCF-shaped counterpart of `_header_declares_gvcf_blocks` above."""
    text = _read_bcf_header_text(file_path)
    if text is None:
        return False
    return any(line.startswith(GVCF_HEADER_MARKER) for line in text.splitlines())


# The symbolic ALT allele a gVCF uses for "any other allele", and the only thing that
# decides whether ZaroPGx can genotype it.
#
# GATK writes `<NON_REF>` and declares it as `##ALT=<ID=NON_REF,...>`. DeepVariant,
# bcftools and some Illumina callers write `<*>` instead. That is not cosmetic:
# GenotypeGVCFs -- the entire conversion -- hard-fails on a `<*>` file with
#   "A USER ERROR has occurred: The list of input alleles must contain <NON_REF> as an
#    allele but that is not the case at position ..."
# so accepting one would buy the uploader a job that dies minutes in. It is refused at
# the door instead, which is the same rule the FASTQ and 23andMe branches follow.
GVCF_GATK_ALLELE = "NON_REF"
GVCF_STAR_ALLELE = "*"

# How many DATA rows the flavour read below looks at. Its own budget, NOT shared with
# the header budget: they used to share one counter, and on a GRCh38 file the ~3,400
# `##contig` records consumed the whole allowance before the first data row, so the
# record scan never ran at all. See GVCF_HEADER_SCAN_LINES.
#
# 200 is generous for the question: a gVCF's reference blocks are its bulk, so the very
# first rows are normally blocks.
GVCF_ALLELE_SCAN_RECORDS = 200


def _gvcf_symbolic_allele(file_path: Path) -> Optional[str]:
    """Which reference-confidence allele this gVCF uses: NON_REF, *, or None.

    None means "the evidence does not say", which every caller treats as "not a
    convertible gVCF" -- the fail-closed direction, and the only honest one: the
    conversion needs `<NON_REF>` specifically, so a file we cannot confirm carries it is
    a file we cannot promise to convert.

    TWO signals, and `##ALT=<ID=NON_REF>` is deliberately NOT one of them:

    * ``##GVCFBlock`` in the header. GATK-only, and decisive -- ``GenotypeGVCFs`` strips
      these records from its output (``GenotypeGVCFsEngine.setupVCFWriter`` removes
      exactly the keys starting ``GVCFBlock``), so their presence means the file still
      is a gVCF.
    * The symbolic allele in a DATA row's ALT column. This is what the reference blocks
      are actually made of, and it is the ONLY signal a DeepVariant gVCF gives -- it
      writes no ``##GVCFBlock`` at all.

    The ``##ALT=<ID=NON_REF,...>`` header declaration was read here and it was WRONG,
    severely: ``GenotypeGVCFs`` carries that declaration through into its plain-VCF
    output while stripping the blocks, so the single most ordinary GATK germline
    file -- HaplotypeCaller ``-ERC GVCF`` genotyped into ``sample.vcf.gz`` -- declared
    NON_REF in its header with no such allele in any record. On GRCh38 it was typed
    GVCF and routed to /gvcf-to-vcf, where GenotypeGVCFs died on it with "The list of
    input alleles must contain <NON_REF>"; on GRCh37 it was refused by
    _refuse_grch37_gvcf, whose way out is "run GenotypeGVCFs yourself and upload the
    VCF" -- i.e. the file just refused. ``##ALT=<ID=*,...>`` is not evidence either,
    for the milder version of the same reason: bcftools writes it into ordinary VCFs
    whenever a spanning deletion is present.

    A BCF gets the header arm only. Its records are binary and reading them needs pysam,
    which is optional in this process (see the import at the top of this module) and
    must not be what decides a file's type -- so a `<*>`-flavoured BCF gVCF reads as
    "does not say" here, and is refused for not being confirmably GATK rather than for
    being DeepVariant's. Same verdict, less precise wording; no BCF-writing caller in
    this class is known to exist.

    Bounded and never raising, exactly like `_header_declares_gvcf_blocks`: deciding a
    file's flavour must not be the thing that turns an unreadable upload into a 500.
    The header and record budgets are separate -- see GVCF_ALLELE_SCAN_RECORDS.
    """
    bcf_header = _read_bcf_header_text(file_path)
    if bcf_header is not None:
        blocks = any(
            line.startswith(GVCF_HEADER_MARKER) for line in bcf_header.splitlines()
        )
        return GVCF_GATK_ALLELE if blocks else None

    try:
        opener = gzip.open if str(file_path).lower().endswith(".gz") else open
        with opener(file_path, "rt", errors="ignore") as handle:
            header_lines_seen = 0
            records_seen = 0
            while True:
                line = handle.readline(GVCF_HEADER_MAX_LINE_BYTES)
                if not line:
                    break
                if line.startswith("##"):
                    if line.startswith(GVCF_HEADER_MARKER):
                        return GVCF_GATK_ALLELE
                    header_lines_seen += 1
                    if header_lines_seen >= GVCF_HEADER_SCAN_LINES:
                        break
                    continue
                if line.startswith("#"):
                    continue
                # A data row. ALT is the fifth tab-separated column, and a GATK gVCF's
                # variant rows spell it `A,<NON_REF>` -- so this is a containment test,
                # not an equality one. The angle brackets are required: a bare `*` is
                # VCF 4.2's spanning-deletion allele and says nothing about gVCFs.
                fields = line.split("\t", 5)
                if len(fields) >= 5:
                    alt = fields[4]
                    if f"<{GVCF_GATK_ALLELE}>" in alt:
                        return GVCF_GATK_ALLELE
                    if f"<{GVCF_STAR_ALLELE}>" in alt:
                        return GVCF_STAR_ALLELE
                records_seen += 1
                if records_seen >= GVCF_ALLELE_SCAN_RECORDS:
                    break
    except Exception as e:
        logger.debug(f"Could not read {file_path} for its gVCF symbolic allele: {e}")
    return None


def _text_content_says_gvcf(file_path: Path) -> bool:
    """True when a VCF-shaped file's CONTENT says gVCF.

    One helper rather than a `##GVCFBlock` check repeated at three sites, because that
    record alone cannot see a DeepVariant gVCF -- it writes none -- and a site that
    consults only it types one as an ordinary VCF.

    Delegates entirely to `_gvcf_symbolic_allele`, which reads BOTH signals in one pass
    (the `##GVCFBlock` record and the reference-block ALT allele) and is the same
    function `analyze_file` calls for the flavour. One reader, so "is this a gVCF" and
    "which gVCF is it" can never answer inconsistently -- they are now literally the
    same answer, present or absent.
    """
    return _gvcf_symbolic_allele(file_path) is not None


def _is_gvcf(file_path: Path) -> bool:
    """Decide gVCF from the name, and from the content when the name says only "VCF".

    The BCF arm is keyed on the file's magic bytes rather than on a `.bcf` name, for
    the same reason the VCF arm consults the header at all: the stored name is
    sanitised and can arrive with no usable extension. A gVCF written as a BCF is
    still a gVCF, and routed onto the BCF lane it would be re-encoded and handed to
    PharmCAT still carrying its reference blocks, which PharmCAT refuses.

    The `<*>` arm is why the symbolic-allele read is consulted here and not only in
    determine_workflow: a DeepVariant gVCF carries no `##GVCFBlock` record at all, so
    the header rule cannot see it, and one named `sample.vcf.gz` used to be typed VCF
    and analysed as a plain call set. PharmCAT would then refuse it mid-run (its own
    detector reads the data rows), which is a loud failure in the wrong place --
    minutes into a job, behind main.nf's `|| true`, rather than at the door.
    """
    name = str(file_path).lower()
    if name.endswith(GVCF_NAME_SUFFIXES):
        return True
    if name.endswith((".vcf", ".vcf.gz")):
        return _text_content_says_gvcf(file_path)
    # No `_looks_like_bcf` arm beside this one: for a BCF `_gvcf_symbolic_allele` reads
    # the same `##GVCFBlock` record this line does and nothing more (its records are
    # binary), so a second call could only ever repeat this answer.
    return _bcf_header_declares_gvcf_blocks(file_path)


# --- Consumer genotyping arrays (23andMe / AncestryDNA) ----------------------
#
# Both are refused (see determine_workflow), and telling them apart still matters:
# an AncestryDNA export used to fall through every sniff below and be refused as
# "Unrecognized file format: unknown", which told the user we could not recognise
# a file we can name exactly, and left them nothing to act on.
#
# Two signals, in this order:
#
# 1. The vendor banner on line 1 - "23andMe" or "AncestryDNA". Present on every
#    unedited export from either vendor, and unambiguous.
# 2. The column-header row, for a file whose banner has been stripped (a
#    spreadsheet round-trip does this). This is where the two formats genuinely
#    differ in shape: 23andMe comments its header row (`# rsid<TAB>chromosome...`)
#    and AncestryDNA does not. The column names alone cannot decide it - 23andMe
#    ships BOTH a 4-column `genotype` layout and a 5-column `allele1`/`allele2`
#    one, and the second is spelled exactly like AncestryDNA's - so the `#` is the
#    discriminator rather than the field list.
#
# Bounded and non-raising for the same reasons as _header_declares_gvcf_blocks:
# this runs inside _detect_file_type, and deciding a file's type must not be what
# turns an unreadable upload into a 500.
ARRAY_HEADER_SCAN_LINES = 200
ARRAY_HEADER_MAX_LINE_BYTES = 64 * 1024
_ARRAY_VENDOR_BANNERS = (
    ("23andme", FileType.TWENTYTHREE_AND_ME),
    ("ancestrydna", FileType.ANCESTRY_DNA),
)


def _detect_consumer_array(file_path: Path) -> Optional[FileType]:
    """Which consumer array export this is, or None if it is not one."""
    try:
        with open(file_path, "r", errors="ignore") as handle:
            for _ in range(ARRAY_HEADER_SCAN_LINES):
                raw = handle.readline(ARRAY_HEADER_MAX_LINE_BYTES)
                if not raw:
                    return None
                line = raw.strip()
                if not line:
                    continue
                commented = line.startswith("#")
                lowered = line.lower()
                if commented:
                    for banner, file_type in _ARRAY_VENDOR_BANNERS:
                        if banner in lowered:
                            return file_type
                # The column-header row: first field `rsid`, with `chromosome`
                # and `position` after it. Its comment marker decides the vendor.
                fields = [f.strip() for f in lowered.lstrip("#").strip().split("\t")]
                if fields[:3] == ["rsid", "chromosome", "position"]:
                    return (
                        FileType.TWENTYTHREE_AND_ME
                        if commented
                        else FileType.ANCESTRY_DNA
                    )
                if not commented:
                    # Past the comment block and this is not a header row, so
                    # there is no header row to find. A data row on its own says
                    # nothing: both vendors write `rsID<TAB>chrom<TAB>pos<TAB>...`.
                    return None
    except Exception as e:
        logger.debug(f"Could not read {file_path} as a consumer array export: {e}")
    return None


# Companion index files. The upload form invites one alongside the data file, and
# process_files does not use it yet (see the TODO there) - but it is not a second
# dataset either, so it must not raise the "extra files were not analysed" warning.
INDEX_FILE_SUFFIXES = (".bai", ".crai", ".csi", ".tbi", ".idx")


# What to tell inspect_header when the stored filename has no usable suffix to
# dispatch on. Keys are what _detect_file_type answers; values are the format
# strings inspect_header's dispatch branches on.
#
# GVCF maps to ".vcf" because a gVCF IS VCF-shaped -- the inspector has no gVCF
# branch and needs none, since what we want from it here is the header (build,
# contigs, sample count), which it reads the same way for both. The gVCF/VCF
# distinction is _detect_file_type's job and is already made by the time this
# table is read. BED and the array formats are absent because the inspector has
# no branch for them, so a hint would buy nothing.
_HEADER_FORMAT_HINTS = {
    FileType.VCF: ".vcf",
    FileType.GVCF: ".vcf",
    FileType.BCF: ".bcf",
    FileType.BAM: ".bam",
    FileType.CRAM: ".cram",
    FileType.SAM: ".sam",
    FileType.FASTQ: ".fastq",
    FileType.FASTA: ".fasta",
}


@dataclass
class FileAnalysis:
    file_type: FileType
    is_compressed: bool
    has_index: bool
    read_type: Optional[str] = (
        None  # WGS / WES / Short-read / Long-read / NGS / Sanger / Chip , etc.
    )
    # ONLY for variant-call files: VCF and BCF. (BCF joined it when BCF became a real
    # input type - it is the same header, in the binary encoding, read by the same
    # inspector, so it fills the same fields. Alignment files still do not have one;
    # see reference_genome* below.)
    vcf_info: Optional[VCFHeaderInfo] = None
    file_size: Optional[int] = None
    error: Optional[str] = None
    is_valid: bool = True
    validation_errors: Optional[List[str]] = None
    # Same evidence VCFHeaderInfo.reference_genome_ambiguous/candidates carry for
    # VCF/BCF (Task 6/8), but for BAM/CRAM/SAM: vcf_info stays variant-call-only
    # (see above), so a self-contradicting alignment header's @SQ evidence travels through
    # these fields instead, populated by analyze_file's alignment branch and
    # read by determine_workflow's BAM/CRAM/SAM branches (Task 10 item 5).
    reference_genome_ambiguous: bool = False
    reference_genome_candidates: List[str] = field(default_factory=list)
    # The build the alignment header actually declares (T6's detect_reference_
    # assembly over the @SQ records), for the same BAM/CRAM/SAM reason as the two
    # fields above. Carried because "which build is this file on" decides whether
    # the pipeline can analyse it at all: every downstream step (PyPGx's region
    # tables, PharmCAT) is GRCh38, so a GRCh37-aligned file read as GRCh38 pulls
    # reads from the wrong locus and produces star alleles nobody has checked.
    # None means the header carried no usable evidence -- not GRCh38.
    reference_genome: Optional[str] = None
    # ONLY for FileType.GVCF: which symbolic reference-confidence allele the file uses
    # ("NON_REF", "*", or None for "the evidence does not say"). It decides whether the
    # file can be converted at all -- GATK GenotypeGVCFs, the whole gVCF lane, hard-
    # fails on anything but <NON_REF> -- so determine_workflow needs it, and
    # determine_workflow never sees a path. See _gvcf_symbolic_allele.
    gvcf_symbolic_allele: Optional[str] = None


# PharmCAT's own position list, as shipped: 1,226 records across 22 genes in
# pharmcat_positions.vcf, 157 of them CYP2D6. The denominator for every coverage
# figure quoted to the user below.
PHARMCAT_POSITIONS = 1226
PHARMCAT_CYP2D6_POSITIONS = 157


@dataclass(frozen=True)
class ConsumerArrayCoverage:
    """How much of PharmCAT's position list one vendor's best chip carries.

    Counted on 2026-08-31 against each vendor's published manifest. Those
    manifests are the union of every revision of a chip, so each figure is an
    UPPER BOUND on any one customer's file, and each is the vendor's BEST
    version rather than a typical one -- quoting the best case is what makes the
    refusal argument hold for every case.
    """

    vendor: str
    # Written out rather than derived from the first letter: "23andMe" takes "a"
    # and "AncestryDNA" takes "an", so no rule over the spelling gets both right.
    article: str
    best_chip: str
    positions: int
    positions_pct: str
    cyp2d6_positions: int
    # Star-allele-defining variants absent from that chip, named. None where no
    # per-variant audit of that vendor exists -- the counts above are measured,
    # and naming variants we have not checked for would not be.
    missing_core: Optional[str] = None


CONSUMER_ARRAY_COVERAGE: Dict[FileType, ConsumerArrayCoverage] = {
    FileType.TWENTYTHREE_AND_ME: ConsumerArrayCoverage(
        vendor="23andMe",
        article="A",
        best_chip="v5",
        # 229 records match, but only 222 of them are SNVs (the I/D-coded
        # insertion and deletion rows are not representable), so 18.7% is
        # itself generous by a further ~0.6 points.
        positions=229,
        positions_pct="18.7%",
        cyp2d6_positions=25,
        missing_core=(
            "rs3892097 (*4, around 20% allele frequency in Europeans), "
            "rs1065852 (*10, the most common East Asian allele), and rs16947 "
            "and rs1135840, both core to *2"
        ),
    ),
    FileType.ANCESTRY_DNA: ConsumerArrayCoverage(
        vendor="AncestryDNA",
        article="An",
        best_chip="v2",
        positions=380,
        positions_pct="31.0%",
        cyp2d6_positions=14,
    ),
}


def _ambiguous_reference_genome_warning(candidates: Optional[List[str]]) -> str:
    """Same warning copy/channel for a self-contradicting genome-build header,
    shared by determine_workflow's VCF and BAM/CRAM/SAM branches (Task 8,
    extended to alignment files by Task 10 item 5).

    Does not imply unsupported/is_provisional: the build genuinely could not
    be verified, so the job proceeds against the caller-declared build rather
    than being refused or marked as a known-wrong one.
    """
    candidates = candidates or []
    # detect_reference_assembly's contract (header_inspector.py) only ever
    # sets ambiguous=True with >=2 candidates today, but gate on truthiness
    # rather than a >=2 length check so a future one-candidate ambiguity still
    # gets named instead of silently falling back to the no-candidates phrasing.
    builds_note = f" (candidates: {', '.join(candidates)})" if candidates else ""
    return (
        "<p>⚠️ This file's header contradicts itself about the genome "
        f"build{builds_note}. The build could not be verified, so "
        "analysis proceeds against the caller-declared build.</p>"
    )


def _clear_needs_flags(workflow: Dict) -> None:
    """Unset every ``needs_*`` flag on a workflow that is being REFUSED.

    The rule the refusal helpers below state twice -- a flag that names a step must
    only be set by an input that step can actually finish -- applied to the two
    refusals that reach their verdict AFTER the plan has been written.
    ``_plan_variant_call_workflow`` sets needs_pypgx/needs_mtdna before it inspects
    the build, and the BAM/CRAM/SAM branches set their whole plan before the
    alignment-build check at the end of ``determine_workflow`` runs. Both left a
    refused workflow carrying flags that mint real steps.

    Latent, not live: ``upload_router._unanalysable_upload_reason`` refuses the
    upload before a Job exists, so nothing has ever minted from these. It is fixed
    anyway because "unsupported is set, so the flags do not matter" is exactly the
    assumption the 23andMe branch was making when needs_conversion started minting a
    step nothing posts.

    Generic rather than a named list, so a ``needs_*`` flag added later is covered
    without anyone remembering this function exists. It touches only ``needs_*``:
    ``unsupported``/``is_provisional``/``unsupported_reason`` are the refusal itself,
    and the recommendations and warnings are what the user is owed.
    """
    for key in [k for k in workflow if k.startswith("needs_")]:
        workflow[key] = False


class FileProcessor:
    def __init__(self, temp_dir: str = "/tmp"):
        # Created lazily in process_files(), not here: upload_router instantiates this at
        # module scope, so an eager mkdir would run on import and create the absolute
        # container path on the host.
        self.temp_dir = Path(temp_dir)

    async def analyze_file(self, file_path: str) -> FileAnalysis:
        """
        Analyze a file to determine its type and characteristics.
        """
        try:
            file_path = Path(file_path)
            if not file_path.exists():
                raise FileNotFoundError(f"File not found: {file_path}")

            logger.info(f"Analyzing file: {file_path}")

            # Get basic file info
            file_size = file_path.stat().st_size
            logger.info(f"File size: {file_size} bytes")

            # Detect compression status BEFORE detecting file type (shared util)
            is_compressed = is_compressed_file(file_path)
            logger.info(f"Is compressed: {is_compressed}")

            has_index = has_index_file(file_path)
            logger.info(f"Has index: {has_index}")

            # Determine file type
            file_type = self._detect_file_type(file_path)
            logger.info(f"Detected file type: {file_type.value}")

            # If it's a variant-call file or an alignment, use the independent header
            # inspector. BCF is read here for the same reason VCF is, and by the same
            # code: inspect_header dispatches `.bcf` to _inspect_vcf_bcf, so a BCF
            # yields the same build detection, contig profile and sample count. Without
            # it a BCF reached determine_workflow with vcf_info=None, which reads as
            # "no evidence about the build" - so a GRCh37 BCF would have been analysed
            # on GRCh37 coordinates as if they were GRCh38.
            #
            # GVCF joined them when the gVCF lane landed, and the build matters MORE
            # there than anywhere else: the lane genotypes over PharmCAT's position
            # list, which exists in GRCh38 coordinates only, so a GRCh37 gVCF has to be
            # recognised as one and refused rather than genotyped at GRCh38 loci.
            vcf_info = None
            reference_genome_ambiguous = False
            reference_genome_candidates: List[str] = []
            alignment_reference_genome: Optional[str] = None
            try:
                # Hand the inspector the type we just decided, so a stored name
                # with no usable suffix still gets its header read. See
                # inspect_header's own note: safe_upload_basename drops non-ASCII
                # characters, so a Cyrillic- or CJK-named VCF is stored as
                # `upload_vcf` with nothing after the underscore, the inspector
                # dispatched on the empty suffix and read no header, and every
                # build-keyed decision downstream -- liftover, the T2T refusal,
                # the contradictory-header warning -- silently saw
                # reference_genome="unknown".
                normalized = inspect_header(
                    str(file_path), format_hint=_HEADER_FORMAT_HINTS.get(file_type)
                )
                # Map normalized structure to VCFHeaderInfo when applicable
                if file_type in (
                    FileType.VCF,
                    FileType.BCF,
                    FileType.GVCF,
                ) and isinstance(normalized, dict):
                    metadata = normalized.get("metadata") or {}
                    # Reference genome inference
                    reference_genome = metadata.get("reference_genome") or "unknown"
                    # The evidence trail behind that collapse: a self-
                    # contradicting header and a header with no evidence at
                    # all both land on "unknown" above, but only one of them
                    # is worth warning the user about (see determine_workflow).
                    reference_genome_ambiguous = bool(
                        metadata.get("reference_genome_ambiguous")
                    )
                    reference_genome_candidates = (
                        metadata.get("reference_genome_candidates") or []
                    )
                    # Sequencing profile inference based on contigs count
                    contigs_list = [
                        c.get("name")
                        for c in (normalized.get("sequences") or [])
                        if isinstance(c, dict) and c.get("name")
                    ]
                    seq_profile = SequencingProfile.UNKNOWN
                    if len(contigs_list) > 20:
                        seq_profile = SequencingProfile.WGS
                    elif len(contigs_list) > 0:
                        seq_profile = SequencingProfile.WES
                    samples = normalized.get("samples") or []
                    vcf_info = VCFHeaderInfo(
                        reference_genome=reference_genome,
                        sequencing_platform=metadata.get("created_by") or "unknown",
                        sequencing_profile=seq_profile,
                        has_index=has_index,
                        is_bgzipped=is_compressed or str(file_path).endswith(".gz"),
                        contigs=contigs_list,
                        sample_count=len(samples),
                        variant_count=None,
                        reference_genome_ambiguous=reference_genome_ambiguous,
                        reference_genome_candidates=reference_genome_candidates,
                    )
                elif file_type in (
                    FileType.BAM,
                    FileType.CRAM,
                    FileType.SAM,
                ) and isinstance(normalized, dict):
                    # Alignment headers carry the same ambiguity evidence
                    # (Task 6's @SQ-record detection) but have no VCFHeaderInfo
                    # to live in -- vcf_info stays variant-call-only (see
                    # FileAnalysis's dataclass comment), so it travels on FileAnalysis itself
                    # instead, for determine_workflow's BAM/CRAM/SAM branches
                    # to read (Task 10 item 5).
                    metadata = normalized.get("metadata") or {}
                    reference_genome_ambiguous = bool(
                        metadata.get("reference_genome_ambiguous")
                    )
                    reference_genome_candidates = (
                        metadata.get("reference_genome_candidates") or []
                    )
                    # The detected build itself, not merely whether it was
                    # self-contradictory. Without it the alignment branches
                    # cannot tell a GRCh37-aligned BAM from a GRCh38 one, and
                    # analyse both as GRCh38 -- see FileAnalysis.reference_genome.
                    alignment_reference_genome = metadata.get("reference_genome")
            except Exception as e:
                logger.warning(
                    f"Independent header inspector failed, falling back for type {file_type}: {e}"
                )
                # No fallback exists: inspector failure leaves vcf_info at its
                # None initialization above, regardless of file_type.

            # Read only for a gVCF: it is a bounded extra pass over the file, and for
            # every other type the answer would be meaningless rather than merely
            # absent.
            gvcf_symbolic_allele = (
                _gvcf_symbolic_allele(file_path) if file_type == FileType.GVCF else None
            )

            # Create the file analysis object with all the gathered information
            analysis = FileAnalysis(
                file_type=file_type,
                is_compressed=is_compressed,
                has_index=has_index,
                vcf_info=vcf_info,
                file_size=file_size,
                reference_genome_ambiguous=reference_genome_ambiguous,
                reference_genome_candidates=reference_genome_candidates,
                reference_genome=alignment_reference_genome,
                gvcf_symbolic_allele=gvcf_symbolic_allele,
            )

            logger.info(f"Analysis complete: {analysis}")
            return analysis

        except Exception as e:
            logger.error(f"Error analyzing file {file_path}: {str(e)}")
            return FileAnalysis(
                file_type=FileType.UNKNOWN,
                is_compressed=False,
                has_index=False,
                error=str(e),
            )

    # Compression and index helpers now shared via app.api.utils.file_utils

    def _detect_file_type(self, file_path: Path) -> FileType:
        """
        Detect the type of genomic file based on extension and content.

        Handles common genomic file formats:
        - VCF (.vcf, .vcf.gz)
        - BAM (.bam)
        - CRAM (.cram)
        - SAM (.sam)
        - FASTQ (.fastq, .fq, .fastq.gz, .fq.gz)
        - FASTA (.fasta, .fa, .fna)
        - GVCF (.gvcf, .gvcf.gz, .g.vcf, .g.vcf.gz, or a ##GVCFBlock header)
        - BCF (.bcf, or the BCF magic bytes)
        - BED (.bed)
        - 23andMe and AncestryDNA (.txt, .csv)
        """
        # Debug logging
        logger.info(f"Detecting file type for: {file_path}")
        logger.info(f"File suffixes: {file_path.suffixes}")

        # Check file extension
        ext = file_path.suffix.lower()
        logger.info(f"File extension: {ext}")

        # gVCF is decided ahead of the extension table, because the extension table
        # cannot decide it: `sample.g.vcf[.gz]` ends in `.vcf` and was typed VCF, so a
        # gVCF went down the VCF lane. That is not a wrong label, it is a lost run:
        # PharmCAT 3.4.0 DETECTS a gVCF -- by filename, by ##GVCFBlock header, or by a
        # reference-block data row -- and refuses it, and main.nf's PharmCAT curl ends
        # in `|| true`, so the refusal is swallowed and the job "completes" with no
        # PharmCAT output. determine_workflow now routes a GATK GRCh38 gVCF to the
        # genotyping lane and refuses the two shapes that lane cannot convert; getting
        # the type right is what lets it do either.
        if _is_gvcf(file_path):
            logger.info("Identified as GVCF file")
            return FileType.GVCF

        # Check for double extensions like .vcf.gz
        if ext == ".gz" and len(file_path.suffixes) > 1:
            prev_ext = file_path.suffixes[-2].lower()
            logger.info(f"Previous extension for compressed file: {prev_ext}")

            # Check for VCF format. (No `.gvcf` case here, nor below: every name ending
            # in `.gvcf[.gz]` was answered by the _is_gvcf check above.)
            if prev_ext == ".vcf":
                logger.info("Identified as compressed VCF file")
                return FileType.VCF
            # Check for FASTQ format
            elif prev_ext in [".fastq", ".fq"]:
                logger.info("Identified as compressed FASTQ file")
                return FileType.FASTQ
            # Check for FASTA format
            elif prev_ext in [".fasta", ".fa", ".fna"]:
                logger.info("Identified as compressed FASTA file")
                return FileType.FASTA
            # Handle vcf.gz without dot notation
            elif "vcf" in str(file_path).lower():
                logger.info("Identified as compressed VCF file (from filename)")
                return FileType.VCF
            # Handle gvcf.gz without dot notation
            elif "gvcf" in str(file_path).lower():
                logger.info("Identified as compressed GVCF file (from filename)")
                return FileType.GVCF

        # Single extension check
        if ext in [".vcf"]:
            logger.info("Identified as VCF file")
            return FileType.VCF
        elif ext == ".bam":
            logger.info("Identified as BAM file")
            return FileType.BAM
        elif ext == ".cram":
            logger.info("Identified as CRAM file")
            return FileType.CRAM
        elif ext == ".sam":
            logger.info("Identified as SAM file")
            return FileType.SAM
        elif ext in [".fastq", ".fq"]:
            logger.info("Identified as FASTQ file")
            return FileType.FASTQ
        elif ext in [".fasta", ".fa", ".fna"]:
            logger.info("Identified as FASTA file")
            return FileType.FASTA
        elif ext == ".bcf":
            logger.info("Identified as BCF file")
            return FileType.BCF
        elif ext == ".bed":
            logger.info("Identified as BED file")
            return FileType.BED
        elif ext in [".txt", ".csv"]:
            # Both consumer arrays, not just 23andMe. AncestryDNA was never
            # detected here, so an AncestryDNA export fell through to
            # FileType.UNKNOWN and was refused as "Unrecognized file format" -
            # a file we can name exactly, reported as unrecognisable.
            array_type = _detect_consumer_array(file_path)
            if array_type is not None:
                logger.info(f"Identified as {array_type.value} genotyping array file")
                return array_type

        # If extension doesn't match, try to determine from content first
        try:
            # For possibly compressed files, use gzip to open
            if ext == ".gz":
                with gzip.open(file_path, "rt", errors="ignore") as f:
                    first_line = f.readline().strip()
                    if first_line.startswith("##fileformat=VCF"):
                        # A gVCF's first line says `##fileformat=VCF` too, so this sniff
                        # cannot stop here. It is reached precisely when the name told us
                        # nothing - and the stored name often tells us nothing, because
                        # safe_upload_basename drops non-ASCII: `образец.gvcf` is
                        # stored as `upload_gvcf`, which matches no extension rule.
                        if _text_content_says_gvcf(file_path):
                            logger.info("Identified as gzipped GVCF from content")
                            return FileType.GVCF
                        logger.info("Identified as gzipped VCF from content")
                        return FileType.VCF

                    # If not VCF, check if it might be FASTQ
                    f.seek(0)
                    first_line = f.readline().strip()
                    if first_line.startswith("@"):
                        second_line = f.readline().strip()
                        third_line = f.readline().strip()
                        fourth_line = f.readline().strip()
                        if (
                            third_line.startswith("+")
                            and len(second_line) > 0
                            and len(fourth_line) > 0
                        ):
                            logger.info("Identified as gzipped FASTQ from content")
                            return FileType.FASTQ
            else:
                # Regular file check
                with open(file_path, "rb") as f:
                    try:
                        header = f.read(20).decode("utf-8", errors="ignore")
                        if "##fileformat=VCF" in header:
                            # Same reasoning as the gzipped arm above: line one does not
                            # distinguish a VCF from a gVCF, and this branch is reached
                            # only when the name has stopped being evidence.
                            if _text_content_says_gvcf(file_path):
                                logger.info("Identified as GVCF from content")
                                return FileType.GVCF
                            logger.info("Identified as VCF from content")
                            return FileType.VCF
                        elif header.startswith("@HD") or header.startswith("@SQ"):
                            logger.info("Identified as SAM from content")
                            return FileType.SAM

                        # BAM is binary, check for BAM magic bytes
                        f.seek(0)
                        if f.read(4) == b"BAM\1":
                            logger.info("Identified as BAM from content")
                            return FileType.BAM
                        # CRAM magic bytes
                        f.seek(0)
                        if f.read(4) == b"CRAM":
                            logger.info("Identified as CRAM from content")
                            return FileType.CRAM
                    except UnicodeDecodeError:
                        # If we can't decode as text, it might be binary
                        pass

                # Check for FASTQ format by looking at first few lines
                try:
                    with open(file_path, "r", errors="ignore") as f:
                        first_line = f.readline().strip()
                        if first_line.startswith("@") and len(first_line) > 1:
                            second_line = f.readline().strip()
                            third_line = f.readline().strip()
                            fourth_line = f.readline().strip()
                            if (
                                third_line.startswith("+")
                                and len(second_line) > 0
                                and len(fourth_line) > 0
                            ):
                                logger.info("Identified as FASTQ from content")
                                return FileType.FASTQ
                except Exception:
                    pass

        except Exception as e:
            logger.debug(f"Error detecting file type from content: {str(e)}")

        # BCF, from its magic bytes. Checked here rather than in either arm below
        # because neither can see it: a BCF is BGZF-framed, so the `.gz` arm reads it
        # as text and finds noise, and the plain arm's BAM/CRAM magic reads look at the
        # still-compressed bytes. A mis-named BCF was therefore typed UNKNOWN and
        # refused as "Unrecognized file format", which is not what happened to it.
        if _looks_like_bcf(file_path):
            logger.info("Identified as BCF from content")
            return FileType.BCF

        # If content-based detection failed, try filename patterns as fallback
        if ext == ".gz":
            filename = file_path.name.lower()
            if "vcf" in filename and "gvcf" not in filename:
                logger.info("Identified as gzipped VCF file (from filename pattern)")
                return FileType.VCF
            elif "gvcf" in filename:
                logger.info("Identified as gzipped GVCF file (from filename pattern)")
                return FileType.GVCF
            elif any(pattern in filename for pattern in ["fastq", "fq"]):
                logger.info("Identified as gzipped FASTQ file (from filename pattern)")
                return FileType.FASTQ
            elif any(pattern in filename for pattern in ["fasta", "fa", "fna"]):
                logger.info("Identified as gzipped FASTA file (from filename pattern)")
                return FileType.FASTA

        logger.warning(f"Could not determine file type for {file_path}")
        return FileType.UNKNOWN

    # Removed: VCF header analysis is handled by header_inspector.inspect_header

    def _analyze_alignment_header_with_pysam(
        self, file_path: Path
    ) -> Optional[Dict[str, any]]:
        """
        Extract alignment header information for BAM/CRAM/SAM files using pysam when available.
        Returns a dict with selected fields or None on failure/unavailability.
        """
        if not _HAS_PYSAM:
            return None
        try:
            af = pysam.AlignmentFile(str(file_path), "r")
            header_dict = af.header.to_dict() if hasattr(af.header, "to_dict") else {}
            contigs = []
            if isinstance(header_dict, dict) and "SQ" in header_dict:
                contigs = [
                    sq.get("SN")
                    for sq in header_dict.get("SQ", [])
                    if isinstance(sq, dict) and sq.get("SN")
                ]
            read_groups = (
                header_dict.get("RG", []) if isinstance(header_dict, dict) else []
            )
            platform = None
            for rg in read_groups:
                if isinstance(rg, dict) and rg.get("PL"):
                    platform = rg.get("PL")
                    break
            info = {
                "contigs": contigs,
                "read_group_count": (
                    len(read_groups) if isinstance(read_groups, list) else 0
                ),
                "platform": platform or "unknown",
            }
            logger.info(
                f"Alignment header (pysam): contigs={len(contigs)}, platform={info['platform']}, RGs={info['read_group_count']}"
            )
            return info
        except Exception as e:
            logger.debug(
                f"pysam.AlignmentFile failed to read header for {file_path}: {e}"
            )
            return None

    def _extract_genome_name_from_path(self, reference_path: str) -> str:
        """
        Extract genome name from a reference genome file path.

        Examples:
        - /path/to/hg38.fa -> GRCh38
        - /path/to/GRCh38.p13.fa -> GRCh38
        - /path/to/hg19.fasta.gz -> GRCh37
        """
        if not reference_path or reference_path == "unknown":
            return "unknown"

        try:
            # Split path and get filename
            path_parts = reference_path.split("/")
            filename = path_parts[-1] if path_parts else reference_path

            # Remove file extensions using regex
            base_name = re.sub(
                r"\.(fa|fasta|fna|gz)$", "", filename, flags=re.IGNORECASE
            )

            # Look for embedded genome patterns (most common case)
            grch38_patterns = [r"GRCh38", r"grch38", r"hg38", r"HG38"]
            grch37_patterns = [r"GRCh37", r"grch37", r"hg19", r"HG19"]

            for pattern in grch38_patterns:
                if re.search(pattern, base_name, re.IGNORECASE):
                    return "GRCh38"

            for pattern in grch37_patterns:
                if re.search(pattern, base_name, re.IGNORECASE):
                    return "GRCh37"

            # Handle exact matches
            if base_name.lower() == "hg38":
                return "GRCh38"
            elif base_name.lower() == "hg19":
                return "GRCh37"

            # Handle prefix matches
            if base_name.lower().startswith("grch38"):
                return "GRCh38"
            elif base_name.lower().startswith("grch37"):
                return "GRCh37"

            # If it starts with GRCh, it's likely already properly formatted
            if base_name.startswith("GRCh"):
                return base_name

            # Try to extract GRCh pattern from anywhere in the name
            grch_match = re.search(r"(GRCh\d+)", base_name)
            if grch_match:
                return grch_match.group(1)

            # Last resort: return a cleaned version
            logger.warning(
                f"Could not extract genome name from {base_name}, returning as-is"
            )
            return base_name

        except Exception as e:
            logger.debug(
                f"Error extracting genome name from path {reference_path}: {e}"
            )
            return "unknown"

    def _plan_variant_call_workflow(
        self, workflow: Dict, analysis: FileAnalysis
    ) -> None:
        """Plan the quick pipeline for an already-called variant file, in place.

        Shared verbatim by the VCF, BCF and GVCF branches of determine_workflow,
        because each of the other two is a VCF by the time anything downstream reads
        it: the pipeline re-encodes a BCF with bcftools and genotypes a gVCF with
        GenotypeGVCFs, and everything after that point - the liftover decision, the
        sequencing profile, the HLA/CYP2D6 caveats, the index advice - is the same
        reasoning about the same records. Three copies of clinical build-detection copy
        WILL drift: the branches would be edited months apart, and the one that stopped
        setting needs_liftover would analyse a GRCh37 file on GRCh38 coordinates
        without saying anything.

        Only the format-specific part - that the file is converted first, and what that
        conversion does or does not cost - lives in each branch, so nothing here has to
        be phrased three times or hedged for three formats.

        The liftover arm is unreachable from the GVCF branch, and that is checked
        there rather than hedged here: a GRCh37 gVCF is refused before this runs (see
        _refuse_grch37_gvcf).
        """
        workflow["needs_pypgx"] = True
        # main.nf hands the sidecar the ORIGINAL upload (never a lifted copy --
        # see its comment on mtdna_variants_ch; for a BCF or gVCF, the converted-
        # but-unlifted VCF, which is those lanes' equivalent) and reads whatever
        # chrM/MT records it finds; a file with none simply produces a no-call,
        # the same outcome PharmCAT already reports for MT-RNR1 with no outside
        # call.
        workflow["needs_mtdna"] = True
        workflow["warnings"].append(
            "<p>⚠️ VCF datafiles lack the necessary raw information to perform complete pharmacogenomic analysis.</p>"
        )
        workflow["warnings"].append(
            "<p>The analysis can proceed, however, the results will be incomplete and have degraded accuracy.</p>"
        )
        workflow["warnings"].append(
            # FASTQ was dropped from this list when the format was refused; the
            # refusal itself is stated at the upload gate, so restating it here
            # only padded a sentence that reads fine without it.
            "<p class='preflight'>If you have an upstream, or original, datafile, such as BAM/SAM/CRAM, please consider uploading it instead in order for the PGx analysis to yield complete results with optimal fidelity.</p>"
        )
        workflow["warnings"].append(
            "<p class='preflight'>Although significant computation and processing time is required, if possible, using an upstream datafile(s) is strongly recommended.</p>"
        )
        workflow["warnings"].append("<p>⚠️ HLA typing can not be performed.</p>")
        workflow["warnings"].append(
            "<p>⚠️ CYP2D6 typing will be performed with degraded accuracy.</p>"
        )
        workflow["warnings"].append(
            "<p>⚠️ All genes with phenotypes affected by structural variants and copy-number variants will be evaluated with degraded accuracy.</p>"
        )

        workflow["recommendations"].append("<p>VCF files use the quick pipeline:</p>")
        workflow["recommendations"].append(
            "<p>Step 1: Run PyPGx for star allele calling on all available pharmacogenes.</p>"
        )
        workflow["recommendations"].append(
            "<p>Step 2: Run PharmCAT with outside calls from PyPGx.</p>"
        )
        # Check reference genome compatibility
        if analysis.vcf_info:
            vcf_info = analysis.vcf_info
            reference = vcf_info.reference_genome.lower()

            # Normalize reference genome string for comparison
            is_hg38 = any(ref_id in reference for ref_id in ["hg38", "grch38", "38"])
            if is_hg38:
                workflow["recommendations"].append(
                    f"<p>✓ Compatible GRCh38 reference genome detected: {vcf_info.reference_genome}</p>"
                )
            elif reference != "unknown" and any(
                ref_id in reference for ref_id in ["grch37", "hg19", "37"]
            ):
                # GRCh37/hg19: supported via a real liftover. The pipeline runs
                # GATK Picard LiftoverVcf (gatk-api's /liftover-vcf, UCSC
                # hg19ToHg38 chain) to convert the file to GRCh38 coordinates
                # before PyPGx/PharmCAT see it. This branch keys off the
                # DETECTED build (vcf_info.reference_genome comes from header
                # inspection), never off the reference_genome form field, which
                # defaults to hg38 regardless of what the file actually is.
                #
                # Deliberately NOT unsupported and NOT provisional: the analysis
                # runs on genuine GRCh38 coordinates. The honest caveats are
                # different ones - liftover drops the variants it cannot map,
                # and a converted file is not byte-identical to a natively
                # GRCh38 one - and the copy below says exactly that, no more.
                workflow["needs_liftover"] = True
                # Carried through to the Nextflow run as --source_build so
                # main.nf knows to route the VCF through LiftoverVCF.
                workflow["source_build"] = vcf_info.reference_genome
                workflow["recommendations"].append(
                    f"<p>✓ {vcf_info.reference_genome} detected. ZaroPGx will lift this "
                    "file over to GRCh38/hg38 with GATK LiftoverVcf prior to analysis.</p>"
                )
                workflow["warnings"].append(
                    "<p class='preflight'>⚠️ Liftover will drop and report the number of variants that could not be mapped to GRCh38.</p>"
                )
            elif reference != "unknown":
                # A named build that is neither GRCh38 nor GRCh37 -- today that
                # means T2T-CHM13v2, the only third assembly header_inspector can
                # name. REFUSED, not analysed provisionally.
                #
                # This branch used to set is_provisional=True next to unsupported,
                # and until CHM13 detection existed nothing could reach it, so that
                # was never exercised. It is the wrong verdict now that it can be:
                # is_provisional means "analysed anyway, provisionally" and exempts
                # a vcf from the upload gate (upload_router._unanalysable_upload_
                # reason), which here would mean analysing CHM13 coordinates as
                # GRCh38. The PGx loci sit hundreds of kb from their GRCh38
                # positions, and nothing downstream errors on it: PharmCAT's VCF
                # preprocessor has no assembly check at all -- it runs
                # `bcftools norm -c ws` against GRCh38.p13, and `s` *swaps* a
                # mismatched REF/ALT rather than failing. So the output would be
                # star alleles nobody has checked, the same confident-wrong-answer
                # class the GRCh37-alignment refusal below exists to stop.
                #
                # Refusal rather than a liftover lane is a deliberate decision, not
                # a gap waiting to be filled: the published T2T chains exclude
                # GRCh38's ALT contigs by construction (GSTT1 lives on one), only
                # ~60% of T2T's segmental duplications have a clear GRCh38
                # orthologue -- CYP2D6/2D7/2D8 sits in exactly such a cluster --
                # and no published work characterises CYP2D6 or CYP2C19 in CHM13
                # at all. See the two caveats named in the copy below.
                workflow["unsupported"] = True
                workflow["is_provisional"] = False
                # Nothing is analysed, so nothing is planned. This function set
                # needs_pypgx/needs_mtdna at the top before it knew the build, and a
                # T2T BCF or gVCF arrives here with its caller's conversion flags set
                # too. See _clear_needs_flags.
                _clear_needs_flags(workflow)
                workflow["unsupported_reason"] = (
                    f"ZaroPGx analyses against GRCh38/hg38 only. This file is "
                    f"aligned to {vcf_info.reference_genome}, where the "
                    f"pharmacogenes sit at different coordinates, and nothing "
                    f"downstream would catch that: PharmCAT normalises against "
                    f"GRCh38.p13 without checking which assembly the file is on, "
                    f"so it would rewrite the mismatched reference alleles and "
                    f"report star alleles that are not yours. Automatic liftover "
                    f"covers GRCh37/hg19 only. Call your variants against "
                    f"GRCh38/hg38 and upload that VCF, or realign the reads to "
                    f"GRCh38/hg38 and call from the realigned data."
                )
                workflow["warnings"].append(
                    f"<p>⚠️ This file is aligned to {vcf_info.reference_genome}. "
                    "ZaroPGx analyses GRCh38/hg38 only, so it is refused rather "
                    "than analysed on coordinates that are not GRCh38's.</p>"
                )
                workflow["recommendations"].append(
                    "<p>Call your variants against GRCh38/hg38 and upload the "
                    "resulting VCF, or realign the reads to GRCh38/hg38 first and "
                    "call from the realigned data.</p>"
                )
                if any(token in reference for token in T2T_BUILD_TOKENS):
                    workflow["recommendations"].append(
                        "<p>Lifting a T2T-CHM13 file over to GRCh38 yourself is not "
                        "a substitute, and it is worth knowing why before you try: "
                        "the published T2T chains exclude GRCh38's alternate "
                        "haplotype contigs, so GSTT1 (which sits on "
                        "chr22_KI270879v1_alt in GRCh38) cannot come across at all, "
                        "and CYP2D6's representation in CHM13 is undocumented — no "
                        "published work compares CYP2D6 or CYP2C19 between the two "
                        "assemblies.</p>"
                    )
            elif vcf_info.reference_genome_ambiguous:
                # reference == "unknown" here, but not every "unknown" is
                # the same: this one is a header that contradicts itself
                # (e.g. ##contig records naming two builds), not a header
                # with no evidence at all. The latter stays silent -- see
                # detect_reference_assembly's contract in header_inspector.py.
                workflow["warnings"].append(
                    _ambiguous_reference_genome_warning(
                        vcf_info.reference_genome_candidates
                    )
                )

            # Enhanced sequencing profile recommendations
            if vcf_info.sequencing_profile == SequencingProfile.WGS:
                workflow["recommendations"].append(
                    "<p>✓ Uploaded VCF file is detected as: Whole Genome Sequencing. Full pharmacogene coverage is available (with VCF-related limitations).</p>"
                )
                workflow["recommendations"].append(
                    "<p>Sequencing quality is currently not considered. If the sequencing quality is sufficient, you should have good results.</p>"
                )
            elif vcf_info.sequencing_profile == SequencingProfile.WES:
                workflow["recommendations"].append(
                    "<p>Uploaded VCF file is detected as: Whole Exome Sequencing. Unknown pharmacogene coverage.</p>"
                )
                workflow["recommendations"].append(
                    "<p>Sequencing quality is currently not considered. If the sequencing quality is sufficient, you should have good results (with VCF-related limitations).</p>"
                )
                workflow["warnings"].append(
                    "<p>⚠️ Whole Exome Sequencing can vary in coverage. The analysis may have degraded completeness, accuracy, and precision, compared to Whole Genome Sequencing.</p>"
                )
                workflow["warnings"].append(
                    "<p>⚠️ Genes with complex variants (structural variants, copy-number variants, etc.) may have degraded evaluation.</p>"
                )
            else:
                workflow["recommendations"].append(
                    "<p>Uploaded VCF file is detected as: Unknown, or, Targeted Sequencing. Unknown pharmacogene coverage.</p>"
                )
                workflow["recommendations"].append(
                    "<p>Sequencing quality is currently not considered. If the sequencing quality is sufficient, you should have good results (with VCF-related limitations).</p>"
                )
                workflow["warnings"].append(
                    "<p>⚠️ Depending on the sequencing platform and methodology, the uploaded VCF datafile may have limited pharmacogene coverage.</p>"
                )
                workflow["warnings"].append(
                    "<p>⚠️ Genes with complex variants (structural variants, copy-number variants, etc.) may have degraded evaluation.</p>"
                )

        # Check if index exists
        if analysis.vcf_info and not analysis.vcf_info.has_index:
            workflow["recommendations"].append(
                "<p>Create index for uploaded VCF file to speed up processing.</p>"
            )
            workflow["recommendations"].append(
                "<p>Note: Although an existing index file can be uploaded along with the main VCF file, at the moment, its functionality is not yet supported. (TO DO)</p>"
            )

    def _refuse_consumer_array(
        self, workflow: Dict, coverage: ConsumerArrayCoverage
    ) -> None:
        """Refuse a 23andMe or AncestryDNA upload, in place, with the numbers.

        One helper rather than a branch each, for the reason
        _plan_variant_call_workflow gives: the two refusals make the same
        argument from the same measurements, and two copies of clinical copy
        edited months apart WILL drift into disagreeing about why we say no.
        Everything that genuinely differs between the vendors is in `coverage`.

        No needs_* flag is set, and needs_conversion in particular is NOT set:
        that flag now mints a real bcf_to_vcf step (workflow_registry), so
        setting it here would plan a BCF conversion for a file that is not a BCF
        and is not being analysed at all. It was set by the old 23andMe branch to
        mean "a conversion ought to exist one day", which is exactly the
        aspirational flag this repo forbids.
        """
        workflow["unsupported"] = True
        # Deliberately NOT provisional: nothing is analysed. is_provisional means
        # "analysed anyway, provisionally" and exempts a runnable input type from
        # the upload gate -- setting it here is the original 23andMe bug.
        workflow["is_provisional"] = False
        workflow["unsupported_reason"] = (
            f"ZaroPGx cannot analyse {coverage.vendor} genotyping files, and that "
            f"is a decision rather than a missing converter. {coverage.article} "
            f"{coverage.vendor} {coverage.best_chip} file carries "
            f"{coverage.positions} of the "
            f"{PHARMCAT_POSITIONS:,} positions PharmCAT calls on "
            f"({coverage.positions_pct}), and {coverage.cyp2d6_positions} of the "
            f"{PHARMCAT_CYP2D6_POSITIONS} that define CYP2D6. ZaroPGx runs PyPGx "
            f"alongside PharmCAT, and PyPGx reads a position the file does not "
            f"carry as homozygous reference rather than as a no-call; that call "
            f"then overrides PharmCAT's. A CYP2D6 poor metaboliser would be "
            f"reported as a normal metaboliser, for codeine and tamoxifen, "
            f"rather than as an incomplete result. Upload a sequencing-derived "
            f"GRCh38/hg38 VCF, or a BAM, CRAM or SAM file, instead."
        )
        if coverage.missing_core:
            workflow["warnings"].append(
                "<p>⚠️ The variants that define the common CYP2D6 star alleles are "
                f"absent from {coverage.vendor} {coverage.best_chip} by name: "
                f"{coverage.missing_core}.</p>"
            )
        workflow["warnings"].append(
            "<p>⚠️ rs35742686 (CYP2D6*3) and rs3064744 (the UGT1A1*28 TA repeat) "
            "are absent from every version of every consumer array.</p>"
        )
        workflow["warnings"].append(
            "<p>⚠️ Genotyping chips cannot detect gene duplications or deletions "
            "at all, by any method — and those decide the phenotype for CYP2D6 "
            "and for several other pharmacogenes.</p>"
        )
        workflow["warnings"].append(
            "<p>⚠️ The coverage above is counted from the vendor's published "
            "manifest, which is the union of every revision of the chip, so it is "
            "an upper bound on any individual file rather than a typical one.</p>"
        )
        workflow["recommendations"].append(
            "<p>PharmCAT's own maintainers say the same of consumer-array data: it "
            "has “limited overlap with most of the gene definitions used by "
            "PharmCAT, which will result in very few callable alleles and "
            "therefore not very useful reports”.</p>"
        )
        workflow["recommendations"].append(
            "<p>Upload sequencing data instead. A GRCh38/hg38 VCF is the fastest "
            "input; a BAM, CRAM or SAM additionally carries the read depth PyPGx "
            "needs to call the CYP2D6 copy-number changes no chip can see.</p>"
        )

    def _refuse_non_gatk_gvcf(
        self, workflow: Dict, symbolic_allele: Optional[str]
    ) -> None:
        """Refuse a gVCF whose reference blocks GenotypeGVCFs cannot read, in place.

        Not a policy choice and not a gap: `gatk GenotypeGVCFs` -- the whole conversion
        -- exits with "A USER ERROR has occurred: The list of input alleles must contain
        <NON_REF> as an allele" on a file whose blocks use `<*>` instead, which
        DeepVariant, bcftools and some Illumina callers write. Accepting one would buy
        the uploader a job that dies minutes in, which is the thing this codebase
        refuses to do.

        `symbolic_allele` is None when the file is a gVCF by name or by ##GVCFBlock
        record but nothing in the scanned window confirmed which allele it uses. That is
        refused on the same sentence: the conversion needs <NON_REF> specifically, so a
        file we cannot confirm carries it is a file we cannot promise to convert.

        No needs_* flag is set. In particular needs_conversion is NOT set: it mints a
        real conversion step (workflow_registry), and a flag that names a step must only
        be set by an input that step can finish -- the rule the 23andMe branch broke.
        """
        workflow["unsupported"] = True
        # Deliberately NOT provisional: nothing is analysed.
        workflow["is_provisional"] = False
        # No angle brackets around NON_REF, deliberately. This string is the 400's
        # plain-text `detail` *and* the panel's red alert, and the panel assigns it with
        # innerHTML - a literal "<NON_REF>" would be parsed as a tag and vanish from the
        # sentence. Escaping instead would fix the panel and leave "&lt;NON_REF&gt;" in
        # the API error. The bare name reads correctly in both.
        observed = (
            "an unrecognised reference-confidence allele"
            if symbolic_allele is None
            else f"the {symbolic_allele} reference-confidence allele"
        )
        workflow["unsupported_reason"] = (
            f"ZaroPGx converts a gVCF by running GATK GenotypeGVCFs on it, and "
            f"GenotypeGVCFs reads only GATK's NON_REF reference blocks. This file uses "
            f"{observed}, which DeepVariant, bcftools and some Illumina callers write, "
            f"and GenotypeGVCFs stops on it with 'The list of input alleles must "
            f"contain NON_REF as an allele'. Accepting it would start a job that fails "
            f"partway through instead of producing a report. Genotype it yourself with "
            f"your caller's own tool and upload the resulting plain single-sample "
            f"GRCh38/hg38 VCF, or upload the BAM, CRAM or SAM it was called from and "
            f"let ZaroPGx call the variants."
        )
        workflow["recommendations"].append(
            "<p>Turn the gVCF into a plain VCF with the tool that wrote it, then "
            "upload that:</p>"
        )
        workflow["recommendations"].append(
            "<p>• DeepVariant: the same run writes a plain VCF alongside the gVCF "
            "(<code>--output_vcf</code>) — upload that one.</p>"
        )
        workflow["recommendations"].append(
            "<p>• bcftools: <code>bcftools convert --gvcf2vcf -f &lt;reference.fa&gt; "
            "input.g.vcf.gz</code></p>"
        )
        workflow["recommendations"].append(
            "<p>• Or upload the BAM, CRAM or SAM the gVCF was called from and let "
            "ZaroPGx call the variants.</p>"
        )
        workflow["warnings"].append(
            "<p>⚠️ Do not filter the reference blocks out by hand. "
            "<code>bcftools view -e 'ALT=\"&lt;NON_REF&gt;\"'</code> and its "
            "equivalents delete your real variants too — a gVCF's variant rows carry "
            "the reference allele alongside the alternate one, so the expression "
            "matches them as well.</p>"
        )

    def _refuse_grch37_gvcf(self, workflow: Dict, detected_build: str) -> None:
        """Refuse a GRCh37/hg19 gVCF, in place, and say what is actually missing.

        A GRCh37 VCF is supported (lifted to GRCh38 with Picard LiftoverVcf), so this
        is the one refusal here a user is entitled to find surprising. What differs is
        the interval list: the gVCF lane's whole value is the reference-confidence pass
        emitted over pharmcat_positions.vcf, and that file exists in GRCh38 coordinates
        only, because PharmCAT is a GRCh38-only tool. There is no GRCh37 position list
        to run the pass over.

        The two ways to pretend otherwise are both worse than saying no:

        * Genotype the GRCh37 file at GRCh38 positions. Selects the wrong loci --
          silently, because GATK checks the interval list against the reference
          dictionary, not against biology.
        * Genotype variant sites only, then lift. Correct, but it throws away every
          reference block, so the result is exactly the plain VCF the user could have
          made themselves, reported under copy that says its hom-ref calls are called
          data. That copy would be false for half the lane, and a second honesty story
          in the same feature is a second one to drift.

        So the way out is the one the user can act on now: genotype it themselves and
        upload the GRCh37 VCF, which ZaroPGx does lift.
        """
        workflow["unsupported"] = True
        workflow["is_provisional"] = False
        build = detected_build or "GRCh37"
        workflow["unsupported_reason"] = (
            f"ZaroPGx cannot analyse a {build} gVCF. Converting a gVCF is worth doing "
            f"only because ZaroPGx can emit reference calls over PharmCAT's own list "
            f"of pharmacogene positions, and that list exists in GRCh38 coordinates "
            f"only — PharmCAT is a GRCh38-only tool — so there is nothing to run that "
            f"pass against for a {build} file. Run GATK GenotypeGVCFs on it yourself "
            f"and upload the resulting plain single-sample VCF: a {build} VCF IS "
            f"supported, and ZaroPGx lifts it over to GRCh38 with Picard LiftoverVcf "
            f"before analysis."
        )
        workflow["recommendations"].append(
            f"<p>Genotype the gVCF yourself, then upload the {build} VCF — ZaroPGx "
            f"lifts that over to GRCh38 for you:</p>"
        )
        workflow["recommendations"].append(
            "<p>• <code>gatk GenotypeGVCFs -R &lt;reference.fa&gt; -V input.g.vcf.gz "
            "-O genotyped.vcf.gz</code></p>"
        )
        workflow["recommendations"].append(
            "<p>• Or call your variants against GRCh38/hg38 and upload the gVCF from "
            "that run, which ZaroPGx converts itself.</p>"
        )
        workflow["warnings"].append(
            "<p>⚠️ A gVCF genotyped by hand loses the reference-confidence blocks at "
            "pharmacogene positions, so those positions become no-calls rather than "
            "the called homozygous-reference genotypes a GRCh38 gVCF upload would "
            "get.</p>"
        )

    def determine_workflow(
        self, analysis: FileAnalysis, gatk_enabled: Optional[bool] = None
    ) -> Dict:
        """
        Determine the appropriate workflow based on file analysis.

        This method implements the detailed workflow logic from workflow_logic.md:
        - FASTQ files: refused (no aligner ships with ZaroPGx; see the branch below)
        - CRAM files: conversion to BAM with specific tools and considerations
        - BAM files: OptiType/HLA typing + PyPGx pipeline with detailed recommendations
        - VCF files: direct PyPGx + PharmCAT with outside calls
        - 23andMe / AncestryDNA files: refused (a chip carries too little of
          PharmCAT's position list, and PyPGx reads the gaps as reference)
        - GVCF files: genotyped into a plain VCF (gatk-api's /gvcf-to-vcf, two GATK
          GenotypeGVCFs passes), then the same quick pipeline a VCF gets. A gVCF whose
          reference blocks are not GATK's <NON_REF>, and a GRCh37/hg19 gVCF, are
          refused -- see _refuse_non_gatk_gvcf and _refuse_grch37_gvcf for why each is
          a fact rather than a policy
        - BCF files: converted to a bgzipped VCF (gatk-api's /bcf-to-vcf, bcftools),
          then the same quick pipeline a VCF gets, liftover included
        - SAM files: conversion to BAM using GATK or samtools
        - FASTA files: reference genome files (unsupported for direct analysis)
        - BED files: genomic interval files (unsupported for direct analysis)

        Independently of the format, an input whose DETECTED build is neither
        GRCh38 nor GRCh37 is refused -- T2T-CHM13 is the one such build the
        header inspector can name today -- and a GRCh37/hg19 BAM, CRAM or SAM
        is refused too, because liftover converts called variants and not reads.

        Args:
            analysis: FileAnalysis object containing file type and characteristics
            gatk_enabled: Optional boolean indicating if GATK is enabled

        Returns a dictionary with workflow configuration and recommendations.
        """
        workflow = {
            "needs_gatk": False,
            "needs_indexing": False,
            "needs_alignment": False,
            # Set by the VCF branch when the DETECTED build is GRCh37/hg19: the
            # file is lifted over to GRCh38 (gatk-api's Picard LiftoverVcf) before
            # analysis. Drives the "liftover" step template in
            # app/services/workflow_registry.py and, via source_build below it,
            # the LiftoverVCF process in pipelines/pgx/main.nf.
            # NOTE ON ROUTING. A warning carrying class="preflight" is advice for
            # the person choosing a file -- upload something else, expect this to
            # take a while, here is what liftover is going to do. It belongs on
            # the upload screen and nowhere else: app/reports/generator.py drops
            # those before rendering, because a finished report is not the place
            # to suggest a different input, and because the report states what
            # the liftover actually did, past tense, from the step's own counts.
            # Everything unmarked is a standing caveat that still qualifies the
            # results after the run, so it appears in both places.
            "needs_liftover": False,
            # "this input needs a gatk-api conversion before it can enter the VCF
            # lane". Two inputs set it -- a BCF (/bcf-to-vcf) and a gVCF
            # (/gvcf-to-vcf) -- and needs_gvcf_genotyping below is what says which. It
            # used to mean "a BCF" and nothing else; it was widened rather than joined
            # by a third independent flag so that workflow_registry's
            # gatk_cram_sam_to_bam veto (unless="needs_conversion") keeps being right
            # without learning about every conversion that gets added.
            "needs_conversion": False,
            # gVCF -> plain VCF via GATK GenotypeGVCFs (gatk-api's /gvcf-to-vcf). Drives
            # the "gvcf_to_vcf" step template in app/services/workflow_registry.py and
            # the GVCFToVCF process in pipelines/pgx/main.nf. Always set together with
            # needs_conversion above.
            "needs_gvcf_genotyping": False,
            "needs_hla": False,
            # mtDNA calling via the mtdna sidecar (mutserve + haplogrep3 +
            # haplocheck). Set True on every input type the sidecar can be
            # handed directly or via a BAM the pipeline already produces --
            # vcf, bam, cram, sam. Left False for fastq (refused below, no
            # aligner ships) and every other unsupported/refused file type,
            # matching the other needs_* flags for those branches.
            "needs_mtdna": False,
            "needs_pypgx": False,
            "needs_pypgx_bam2vcf": False,
            "is_provisional": False,
            "recommendations": [],
            "warnings": [],
            "unsupported": False,
            "unsupported_reason": None,
        }

        # Check GATK status from environment if not provided
        if gatk_enabled is None:
            gatk_enabled = env_flag("GATK_ENABLED", False)

        # FASTQ: refused at upload, not analysed.
        #
        # ZaroPGx ships no aligner. Raw reads have to be aligned before any downstream
        # step can touch them, and the only route to a BAM in this stack is gatk-api's
        # /align-fastq, which answers HTTP 501 (docker/gatk-api/gatk_api.py). Since
        # pipelines/pgx/main.nf's curls carry --fail-with-body, that 501 kills the run.
        # So the fastq branch in main.nf exists but cannot complete: accepting a FASTQ
        # could only ever buy the user a job that dies minutes later. None of the
        # needs_* flags are set, because there is no workflow to plan.
        #
        # Also note process_files() analyses files[0] only. Even if alignment existed,
        # a paired-read upload would carry one mate, so "upload both mates" was never
        # true either; the copy below says single- and paired-end alike are refused.
        if analysis.file_type == FileType.FASTQ:
            workflow["unsupported"] = True
            workflow["unsupported_reason"] = (
                "ZaroPGx cannot analyse FASTQ files. It ships no aligner, so raw reads "
                "cannot be turned into the aligned data every later step needs, and a "
                "FASTQ job would fail partway through instead of producing a report. "
                "This applies to paired-end reads too. Align your reads to GRCh38/hg38 "
                "yourself and upload the resulting BAM, CRAM or SAM file, or upload a "
                "GRCh38/hg38 VCF."
            )
            workflow["recommendations"].append(
                "<p>Align the reads to GRCh38/hg38 yourself, then upload the aligned file:</p>"
            )
            workflow["recommendations"].append(
                "<p>• Short reads: bwa-mem2 (please ensure ≥64GB RAM available), or BWA (Burrows-Wheeler Aligner)</p>"
            )
            workflow["recommendations"].append("<p>• Long reads: minimap2</p>")
            workflow["recommendations"].append(
                "<p>• Or run an established end-to-end pipeline such as nf-core/sarek and upload its BAM, CRAM or VCF output.</p>"
            )
            workflow["recommendations"].append(
                "<p>ZaroPGx accepts BAM, CRAM and SAM directly; a GRCh38/hg38 VCF is the fastest input of all.</p>"
            )

        # CRAM -> to be converted to BAM (lossy)
        #
        # Everything below the conversion is the BAM branch's plan, and it is set here
        # because main.nf runs the BAM branch's processes on this lane: once CramToBAM
        # has produced bam_ch, OptiTypeHLAFromBAM and PyPGxBam2Vcf are invoked on it
        # exactly as they are for a BAM upload. Two flags used to be missing, with two
        # different symptoms:
        #
        # * needs_pypgx_bam2vcf. main.nf's PyPGxBam2Vcf posts step_name=pypgx_bam2vcf
        #   on this lane, workflow_registry gates that template on this flag, and a
        #   step name with no row 404s every status update and sits [pending] for the
        #   whole step -- the identical bug gatk_cram_sam_to_bam had here.
        # * needs_hla. It is what upload_router turns into --skip_hla, so leaving it
        #   False sent --skip_hla=true and OptiType never ran: a CRAM or SAM silently
        #   got no HLA typing while a BAM holding the same reads did, with the docs
        #   (docs/user/file-formats.md) and the pipeline both saying otherwise.
        elif analysis.file_type == FileType.CRAM:
            workflow["needs_gatk"] = True
            workflow["needs_pypgx"] = True
            workflow["needs_pypgx_bam2vcf"] = True  # PyPGx create-input-vcf on the BAM
            # OptiType reads the converted BAM (OptiTypeHLAFromBAM), not the raw CRAM.
            workflow["needs_hla"] = True
            # main.nf hands the sidecar the already-converted BAM (bam_ch), not the
            # raw CRAM, so this can run alongside the GATK conversion above.
            workflow["needs_mtdna"] = True
            workflow["recommendations"].append(
                "<p>CRAM files will be converted to BAM using samtools:</p>"
            )
            workflow["recommendations"].append(
                "<p>Command: samtools view -b -T <refgenome.fa> -o <output_file.bam> <input_file.cram></p>"
            )
            workflow["recommendations"].append(
                "<p>Note: CRAM files are smaller but require original reference FASTA for conversion</p>"
            )
            workflow["recommendations"].append(
                "<p>Alternative: Use nf-core/bamtofastq pipeline for CRAM to FASTQ conversion</p>"
            )
            workflow["recommendations"].append(
                "<p>Once converted, the BAM gets the full BAM pipeline: OptiType/HLA "
                "typing, PyPGx create-input-vcf, PyPGx star allele calling, then "
                "PharmCAT with those outside calls.</p>"
            )
            workflow["recommendations"].append(
                "<p>See: https://pharmcat.clinpgx.org/using/Calling-HLA/</p>"
            )

            # Check if index exists
            if not analysis.has_index:
                workflow["recommendations"].append(
                    "<p>Creating index for CRAM file for faster processing</p>"
                )

        # SAM -> to be converted to BAM. Same plan as CRAM above, for the same reason
        # and with the same two flags that were missing; see that branch's comment.
        elif analysis.file_type == FileType.SAM:
            workflow["needs_gatk"] = True
            workflow["needs_pypgx"] = True
            workflow["needs_pypgx_bam2vcf"] = True
            workflow["needs_hla"] = True
            # Same as CRAM above: the sidecar sees the converted BAM, not the raw SAM.
            workflow["needs_mtdna"] = True
            workflow["recommendations"].append(
                "<p>SAM file will be converted to BAM using GATK or samtools:</p>"
            )
            workflow["recommendations"].append(
                "<p>GATK: Picard SortSam and BuildBamIndex for quality control</p>"
            )
            workflow["recommendations"].append(
                "<p>Alternative: samtools view -b -o output.bam input.sam</p>"
            )
            workflow["recommendations"].append(
                "<p>Once converted, the BAM gets the full BAM pipeline: OptiType/HLA "
                "typing, PyPGx create-input-vcf, PyPGx star allele calling, then "
                "PharmCAT with those outside calls.</p>"
            )

            # Check if index exists
            if not analysis.has_index:
                workflow["recommendations"].append(
                    "<p>Creating index for SAM file for faster processing</p>"
                )

        # BAM -> can enter pipeline directly, but OptiType will internally convert to FASTQ
        elif analysis.file_type == FileType.BAM:
            workflow["needs_hla"] = True
            workflow["needs_pypgx"] = True
            workflow["needs_pypgx_bam2vcf"] = True  # Use PyPGx create-input-vcf
            # BAM is exactly what the sidecar wants for its alignment path.
            workflow["needs_mtdna"] = True

            workflow["recommendations"].append(
                "<p>BAM files will be processed with the complete pipeline:</p>"
            )
            workflow["recommendations"].append(
                "<p>Step 1: OptiType/HLA typing - extracts HLA alleles from BAM (~100GB intermediate FASTQ)</p>"
            )
            workflow["recommendations"].append(
                "<p>Step 2: PyPGx create-input-vcf - calls SNVs/indels for all target genes</p>"
            )
            workflow["recommendations"].append(
                "<p>Step 3: PyPGx star allele calling for enhanced pharmacogene analysis</p>"
            )
            workflow["recommendations"].append(
                "<p>Step 4: PharmCAT with outside calls including HLA data</p>"
            )
            workflow["recommendations"].append(
                "<p>Result: Complete 23/23 highest clinical evidence pharmacogenes</p>"
            )
            workflow["recommendations"].append(
                "<p>Reference: https://pharmcat.clinpgx.org/using/Calling-HLA/</p>"
            )
            workflow["recommendations"].append(
                "<p>PyPGx docs: https://pypgx.readthedocs.io/en/latest/cli.html#run-ngs-pipeline</p>"
            )

            # Check if index exists
            if not analysis.has_index:
                workflow["recommendations"].append(
                    "<p>Creating index for BAM file for faster processing</p>"
                )

        # VCF | "quick pipeline" (curated on 2025-09-27)
        elif analysis.file_type == FileType.VCF:
            self._plan_variant_call_workflow(workflow, analysis)

        # 23andMe and AncestryDNA: refused at upload, not analysed.
        #
        # The old copy here refused 23andMe for the wrong reason - "converted to VCF
        # first, and that conversion is not implemented yet" - which promises the
        # feature by describing its absence, and is not what is actually wrong. The
        # coordinates in these files are fine (build 37, plus strand, real positions)
        # and `bcftools convert --tsv2vcf` would happily produce a VCF. What is wrong
        # is downstream: ZaroPGx does not run PharmCAT alone, and PharmCAT alone is
        # the only configuration in which chip data degrades honestly. main.nf
        # concatenates PyPGx's output into combined_outside.tsv and posts it as
        # PharmCAT's outside_tsv, and an outside call OVERRIDES PharmCAT's no-call.
        # PyPGx's own maintainer on array input (pypgx#142): missing loci "will be
        # falsely treated as homozygous reference even though there might be
        # variants". So a 23andMe v5 file with no rs3892097 yields CYP2D6 *1/*1 and
        # the report tells a *4/*4 poor metaboliser they metabolise codeine and
        # tamoxifen normally. That is the confident-wrong-answer class.
        #
        # `is_provisional` used to be set here alongside `unsupported`, which read as
        # "analysed anyway, provisionally" -- the meaning it genuinely carried for a
        # VCF -- and waved 23andMe past the upload refusal gate.
        #
        # AncestryDNA reaches its own branch as of 2026-08-31. Before that
        # FileType.ANCESTRY_DNA existed but was never produced by _detect_file_type,
        # so an AncestryDNA export fell through to the unknown-format branch and was
        # refused as "Unrecognized file format: unknown" -- a file we can name
        # exactly, and measure, reported as unrecognisable.
        elif analysis.file_type in CONSUMER_ARRAY_COVERAGE:
            self._refuse_consumer_array(
                workflow, CONSUMER_ARRAY_COVERAGE[analysis.file_type]
            )

        # FASTA - reference genome files
        elif analysis.file_type == FileType.FASTA:
            workflow["unsupported"] = True
            workflow["unsupported_reason"] = (
                "FASTA files are reference genome files and cannot be analyzed directly."
            )
            workflow["recommendations"].append(
                "<p>FASTA files contain reference genome sequences:</p>"
            )
            workflow["recommendations"].append(
                "<p>• Use FASTA files as reference for alignment (BWA, minimap2, etc.)</p>"
            )
            workflow["recommendations"].append(
                "<p>• Convert FASTQ reads to BAM using this reference</p>"
            )
            workflow["recommendations"].append(
                "<p>• Then use the resulting BAM for pharmacogenomic analysis</p>"
            )

        # GVCF: genotyped into a plain VCF, then analysed as one.
        #
        # What this branch used to say was wrong on both counts, and the copy is worth
        # recording because the wrong version was more plausible than the right one.
        # It claimed PharmCAT "has not been validated against" reference blocks and
        # would therefore "produce star alleles nobody has checked". Measured against
        # PharmCAT 3.4.0: it produces no star alleles at all. It DETECTS a gVCF and
        # refuses it -- pcat/utilities.py:is_gvcf_file fires on a `.g.vcf`-shaped
        # filename, on a ##GVCFBlock header record, or on a data row whose ALT is
        # <NON_REF>/<*>/. spanning more than one base, and the pipeline exits with
        # "... is a gVCF file, which is not currently supported". So the risk was never
        # a confident wrong answer; it was a loud failure at the wrong end of the run.
        #
        # The branch also shipped a way out that DESTROYS the user's data:
        # `bcftools view -e 'ALT="<NON_REF>"'` produced ZERO records when measured,
        # because a GATK gVCF's variant rows carry ALT `A,<NON_REF>` -- the expression
        # deletes the real variants along with the reference blocks, and leaves the
        # ##GVCFBlock headers that would get the result refused anyway. It is gone.
        #
        # A converted gVCF is BETTER than the plain VCF the user would otherwise
        # upload, which is why there is a lane rather than a nicer refusal: its
        # reference-confidence blocks are CALLED data, so genotyping them over
        # PharmCAT's own position list yields real hom-ref genotypes at the PGx
        # positions -- the thing the plain-VCF lane can only fabricate with PharmCAT's
        # --absent-to-ref. gatk-api's /gvcf-to-vcf does exactly that (two GATK
        # GenotypeGVCFs passes joined with `bcftools concat -a`), and main.nf's
        # GVCFToVCF process runs it before anything downstream sees the file.
        #
        # Two shapes are still refused, and both are refusals of fact rather than of
        # policy -- see the helpers below.
        elif analysis.file_type == FileType.GVCF:
            detected_build = (
                (analysis.vcf_info.reference_genome or "") if analysis.vcf_info else ""
            ).lower()
            if analysis.gvcf_symbolic_allele != GVCF_GATK_ALLELE:
                self._refuse_non_gatk_gvcf(workflow, analysis.gvcf_symbolic_allele)
            elif any(token in detected_build for token in GRCH37_BUILD_TOKENS):
                self._refuse_grch37_gvcf(workflow, analysis.vcf_info.reference_genome)
            else:
                # needs_gatk, not just the two conversion flags: GenotypeGVCFs runs in
                # the gatk-api container, and needs_gatk is what upload_router turns
                # into --skip_gatk. Left False, every gVCF job would be submitted with
                # --skip_gatk=true and refused by main.nf's own guard. Same reasoning
                # as the BCF branch below.
                #
                # needs_conversion means "this input needs a gatk-api conversion before
                # it can enter the VCF lane"; needs_gvcf_genotyping says WHICH
                # conversion. Keeping the general flag set is what lets
                # workflow_registry's gatk_cram_sam_to_bam veto (unless=needs_conversion)
                # go on being right without learning about a third flag.
                workflow["needs_conversion"] = True
                workflow["needs_gvcf_genotyping"] = True
                workflow["needs_gatk"] = True
                workflow["recommendations"].append(
                    "<p>gVCF files are genotyped into a plain VCF before analysis:</p>"
                )
                workflow["recommendations"].append(
                    "<p>Step 0: GATK GenotypeGVCFs, run inside ZaroPGx, twice: once "
                    "over PharmCAT's own position list with "
                    "--include-non-variant-sites, and once over everything else. The "
                    "first pass is why uploading the gVCF is better than converting it "
                    "yourself — the reference genotypes at the pharmacogene positions "
                    "come from your file's own reference-confidence blocks, so they "
                    "are called data rather than assumed.</p>"
                )
                # Not "--absent-to-ref is not used on this lane". That was asserted
                # here, in the report paragraph and in the docs, and it is not this
                # branch's to assert: the two assume-reference checkboxes are GLOBAL
                # (index.html), resolved once per upload after this function has
                # returned, and forwarded to PharmCAT with no input-type branch
                # anywhere on the way. What IS true unconditionally is that the lane
                # does not need them -- and which one would bite, which is the
                # counter-intuitive half.
                workflow["recommendations"].append(
                    "<p>ZaroPGx needs neither of PharmCAT's assume-reference flags on "
                    "this lane and adds neither itself. The two checkboxes under "
                    "PharmCAT still apply if you tick them, and on this lane the one "
                    "that changes the answer is <code>--unspecified-to-ref</code>, not "
                    "<code>--absent-to-ref</code>: the reference pass emits a row at "
                    "every position in PharmCAT's list, so a position your file did "
                    "not cover arrives as a present <code>./.</code> row rather than "
                    "as a missing one.</p>"
                )
                workflow["warnings"].append(
                    "<p>⚠️ GenotypeGVCFs re-genotypes each site from the recorded "
                    "likelihoods rather than copying the original caller's genotype. "
                    "ZaroPGx runs it with the calling-confidence threshold set to zero "
                    "so that nothing is dropped for failing a cutoff you did not "
                    "choose, but the emitted genotypes are still not guaranteed "
                    "identical to your caller's.</p>"
                )
                workflow["warnings"].append(
                    "<p>⚠️ Positions your gVCF does not cover are no-calls: a "
                    "reference block that is absent is not a reference call. The run "
                    "reports how many of PharmCAT's positions carried a call. "
                    "Ticking “Assume unspecified sites = reference” reverses that — "
                    "it turns the very positions that count reports as uncovered into "
                    "fabricated <code>0/0</code> calls.</p>"
                )
                workflow["warnings"].append(
                    "<p>⚠️ PharmCAT discards positions whose indel representation does "
                    "not match its own definitions, and those stay no-calls too. That "
                    "is the same outcome a plain VCF gets, not a cost of this "
                    "conversion.</p>"
                )
                workflow["recommendations"].append(
                    "<p>Everything below therefore describes a VCF analysis, and every "
                    "VCF caveat applies: no HLA typing, and degraded accuracy for "
                    "CYP2D6 and for genes whose phenotypes depend on structural or "
                    "copy-number variants.</p>"
                )
                self._plan_variant_call_workflow(workflow, analysis)

        # BCF: converted to a bgzipped VCF, then analysed as one.
        #
        # Renaming a BCF onto the pipeline's vcf branch was tried and reverted, and that
        # is still the wrong answer: the branch stages the upload verbatim, so the file
        # arrives downstream still called `upload_sample.bcf`; docker/pharmcat's
        # /genotype gates on that name (`.vcf`/`.vcf.gz`/`.vcf.bgz`) and answers 400,
        # and main.nf's PharmCAT curl ends in `|| true`, so the 400 is swallowed and the
        # run "completes" with no PharmCAT output at all. What changed is not the
        # naming: main.nf now has a `bcf` branch whose first step POSTs the file to
        # gatk-api's /bcf-to-vcf (`bcftools view -O z`, then a tabix index), which
        # vouches for a non-empty BGZF result before the run continues. The bytes are
        # genuinely different by the time anything gates on a filename.
        #
        # needs_gatk, not just needs_conversion: that conversion runs in the gatk-api
        # container, and needs_gatk is what upload_router turns into --skip_gatk. Left
        # False, every BCF job would be submitted with --skip_gatk=true and refused by
        # main.nf's own guard. It also means unticking GATK in the UI refuses the job
        # loudly instead of starving the lane, which is what that guard is for.
        elif analysis.file_type == FileType.BCF:
            workflow["needs_conversion"] = True
            workflow["needs_gatk"] = True
            workflow["recommendations"].append(
                "<p>BCF files are converted to a bgzipped VCF before analysis:</p>"
            )
            workflow["recommendations"].append(
                "<p>Step 0: bcftools view -O z, run inside ZaroPGx. BCF is the binary "
                "encoding of a VCF and carries exactly the same variant records, so "
                "the conversion itself costs no accuracy - the analysis runs on your "
                "calls unchanged.</p>"
            )
            workflow["recommendations"].append(
                "<p>Everything below therefore describes a VCF analysis, and every "
                "VCF caveat applies: no HLA typing, and degraded accuracy for CYP2D6 "
                "and for genes whose phenotypes depend on structural or copy-number "
                "variants.</p>"
            )
            self._plan_variant_call_workflow(workflow, analysis)

        # BED - genome interval/annotation files
        elif analysis.file_type == FileType.BED:
            workflow["unsupported"] = True
            workflow["unsupported_reason"] = (
                "BED files are typically downstream of sequencing / genotyping, and may contain genomic intervals or other information in an unusual format that cannot be directly analyzed."
            )
            workflow["recommendations"].append(
                "<p>Not typically suitable for direct pharmacogenomic variant analysis.</p>"
            )
            workflow["recommendations"].append(
                "<p>Has this BED file been generated from an existing genomic datafile?</p>"
            )
            workflow["recommendations"].append(
                "<p>If so, please upload the original datafile(s) instead.</p>"
            )
            workflow["recommendations"].append(
                "<p>If the BED file contains data specifically for pharmacogenomic analysis, note that arbitrary BED files are not yet supported.</p>"
            )
            workflow["recommendations"].append(
                "<p>Tool(s) to look into: PyPGx: pypgx create-regions-bed, bedtools</p>"
            )

        # Unknown file type (curated on 2025-09-27)
        else:
            workflow["unsupported"] = True
            workflow["unsupported_reason"] = (
                f"Unrecognized file format: {analysis.file_type.value}."
            )
            workflow["recommendations"].append(
                "<p>The file(s) you have selected could not be recognized.</p>"
            )
            workflow["recommendations"].append(
                "<p>If this is a bug, please report it on GitHub, see bottom of the page. Apologies for the inconvenience.</p>"
            )
            workflow["recommendations"].append("<p>Supported formats:</p>")
            workflow["recommendations"].append(
                "<p>Priority 0 (Supported): VCF, GRCh38/hg38, NGS-derived.</p>"
            )
            workflow["recommendations"].append(
                "<p>Priority 1 (Development): VCF, GRCh37/hg19, NGS-derived.</p>"
            )
            workflow["recommendations"].append(
                "<p>Priority 2 (Development): BAM, CRAM, SAM, BCF, all NGS-derived.</p>"
            )
            workflow["recommendations"].append(
                "<p>Not accepted: FASTQ. ZaroPGx ships no aligner — align the reads to GRCh38/hg38 yourself and upload the resulting BAM, CRAM or SAM.</p>"
            )
            workflow["recommendations"].append(
                "<p>Not accepted: 23andMe and AncestryDNA genotyping exports. That is "
                "a decision rather than pending work — they carry under a third of "
                "the positions PharmCAT calls on, and no chip can show a gene "
                "duplication or deletion.</p>"
            )
            workflow["recommendations"].append(
                "<p>Not accepted: files aligned to T2T-CHM13. ZaroPGx detects them "
                "and refuses them; it analyses against GRCh38/hg38 only.</p>"
            )
            workflow["recommendations"].append(
                "<p>Priority 3 (Research): Other sequencing and genotyping formats.</p>"
            )
            workflow["recommendations"].append(
                "<p>Priority 4 (Research): BED, gVCF, various TXT formats.</p>"
            )
            workflow["recommendations"].append(
                "<p>If you happen to have a supported datafile, please try again and upload that file(s) instead.</p>"
            )

        # Same self-contradicting-header warning as the VCF branch above
        # (Task 8), extended to alignment files (Task 10 item 5): T6's
        # detect_reference_assembly flags a BAM/CRAM/SAM header the same way
        # it flags a VCF one, via analysis.reference_genome_ambiguous/
        # _candidates (populated by analyze_file's alignment branch, since
        # vcf_info stays VCF-only). A single check after the branches above,
        # rather than one copy per BAM/CRAM/SAM branch, keeps the warning from
        # drifting out of sync between them. "No evidence" stays silent here
        # exactly as it does for VCF, because reference_genome_ambiguous is
        # only ever True when the header actually contradicts itself.
        if (
            analysis.file_type in (FileType.BAM, FileType.CRAM, FileType.SAM)
            and analysis.reference_genome_ambiguous
        ):
            workflow["warnings"].append(
                _ambiguous_reference_genome_warning(
                    analysis.reference_genome_candidates
                )
            )

        # A BAM/CRAM/SAM aligned to anything but GRCh38 is refused, not analysed.
        #
        # The liftover added for VCF input does not reach this case: it converts a
        # *called* file's coordinates, and these formats reach PharmCAT only by
        # having variants called out of them first. That call is made against gene
        # regions PyPGx looks up by assembly, so a GRCh37-aligned file analysed as
        # GRCh38 reads its variants out of the wrong locus entirely (GRCh38's
        # CYP2D6 window is ~400 kb away from GRCh37's) and yields star alleles
        # nobody has checked -- the "confident wrong answer, not a failure" case
        # that _unanalysable_upload_reason exists to stop, and worse here than for
        # gVCF because nothing downstream errors.
        #
        # Refusing matches how every other unanalysable input is handled (FASTQ,
        # consumer arrays, BCF): say so now, with the fix the user can act on. And
        # for GRCh37 the fix is a real one -- calling variants against GRCh37 and
        # uploading the resulting VCF lands on the supported liftover lane.
        #
        # T2T-CHM13 joined this check when CONTIG_LENGTH_ASSEMBLIES learned to name
        # it (2026-08-31); before that a CHM13 alignment was simply undetected and
        # analysed as GRCh38. It gets its own copy rather than sharing GRCh37's,
        # because GRCh37's way out -- call variants yourself, we lift the VCF -- is
        # not available: there is no CHM13 chain here, and the VCF branch above
        # refuses a CHM13 VCF for the same reason. Realigning is the only way out.
        #
        # Keyed on the DETECTED build only. `None` (no usable @SQ evidence) is left
        # alone rather than guessed at, exactly as the ambiguity check above does:
        # refusing a file whose build simply could not be read would reject most
        # hand-made and minimal BAMs, which is a different defect.
        if analysis.file_type in (FileType.BAM, FileType.CRAM, FileType.SAM):
            detected_build = (analysis.reference_genome or "").lower()
            is_grch37 = any(token in detected_build for token in GRCH37_BUILD_TOKENS)
            is_t2t = any(token in detected_build for token in T2T_BUILD_TOKENS)
            if is_grch37 or is_t2t:
                file_label = analysis.file_type.value.upper()
                workflow["unsupported"] = True
                # Deliberately NOT provisional: nothing is analysed, so the flag
                # that means "analysed anyway, provisionally" would wave this past
                # the upload gate (the 23andMe mistake).
                workflow["is_provisional"] = False
                # And nothing is planned either: the BAM/CRAM/SAM branches above set
                # their whole plan (needs_hla / needs_pypgx / needs_pypgx_bam2vcf /
                # needs_mtdna / needs_gatk) before this check could know the build.
                # See _clear_needs_flags.
                _clear_needs_flags(workflow)
                workflow["unsupported_reason"] = (
                    f"This {file_label} file is aligned to "
                    f"{analysis.reference_genome}. ZaroPGx analyses against "
                    f"GRCh38/hg38 only, and reading these reads as GRCh38 would "
                    f"take every gene from the wrong position and report star "
                    f"alleles that are not yours. Automatic liftover covers VCFs, "
                    f"not aligned reads."
                )
                if is_grch37:
                    workflow["recommendations"].append(
                        "<p>Call variants against GRCh37/hg19 yourself (bcftools or "
                        "GATK HaplotypeCaller), then upload the resulting VCF. "
                        "ZaroPGx lifts that over to GRCh38 automatically.</p>"
                    )
                    workflow["recommendations"].append(
                        "<p>Or realign the reads to GRCh38/hg38 and upload the "
                        f"resulting {file_label} file.</p>"
                    )
                else:
                    workflow["recommendations"].append(
                        "<p>Realign the reads to GRCh38/hg38 and upload the "
                        f"resulting {file_label} file, or call variants from the "
                        "realigned data and upload that VCF.</p>"
                    )
                    workflow["recommendations"].append(
                        "<p>Calling variants against T2T-CHM13 and uploading that "
                        "VCF will not help: ZaroPGx refuses a T2T-CHM13 VCF too, "
                        "and lifting one over to GRCh38 yourself drops GSTT1 "
                        "(it sits on a GRCh38 alternate haplotype contig the T2T "
                        "chains exclude) and leaves CYP2D6 on a representation "
                        "nobody has characterised in CHM13.</p>"
                    )

        return workflow

    async def process_files(
        self,
        files: List,
        reference_genome: str = "hg38",
        optitype_enabled: Optional[str] = None,
        gatk_enabled: Optional[str] = None,
        pypgx_enabled: Optional[str] = None,
        report_enabled: Optional[str] = None,
        mtdna_enabled: Optional[str] = None,
    ) -> Dict:
        """
        Process multiple uploaded files and determine the appropriate workflow.

        Args:
            files: List of uploaded files
            reference_genome: Reference genome to use (default: hg38)
            optitype_enabled: Whether OptiType is enabled
            gatk_enabled: Whether GATK processing is enabled
            pypgx_enabled: Whether PyPGx analysis is enabled
            report_enabled: Whether custom report generation is enabled
            mtdna_enabled: Whether mtDNA calling (mtdna sidecar) is enabled

        Returns:
            Dictionary with analysis results and workflow configuration
        """
        try:
            logger.info(f"Processing {len(files)} files")

            if not files:
                return {"success": False, "error": "No files provided"}

            # Only one file is analysed.
            # TODO: Support multiple files in the future
            # 2 files can now be uploaded, but the use of the index file needs work.
            #
            # It is the first file that is not an index, rather than files[0]: a browser
            # FileList is commonly alphabetical, so selecting sample.bam + sample.bai put
            # the .bai first and the whole upload was refused as "Unrecognized file
            # format: unknown" without ever mentioning the BAM sitting behind it.
            data_files = [
                f
                for f in files
                if not str(f.filename or "").lower().endswith(INDEX_FILE_SUFFIXES)
            ]
            primary_file = data_files[0] if data_files else files[0]

            # Every other data file is dropped on the floor, which for a second *data*
            # file is a silent wrong answer: a paired-read upload used to be analysed as
            # one mate, and two VCFs as whichever arrived first, with nothing said.
            # Collect the names now and warn below. They are HTML-escaped there, because
            # the workflow panel renders warnings with innerHTML.
            ignored_files = [
                str(f.filename or "") for f in data_files if f is not primary_file
            ]

            # Save the uploaded file to temporary location
            self.temp_dir.mkdir(parents=True, exist_ok=True)
            temp_file_path = (
                self.temp_dir / f"upload_{safe_upload_basename(primary_file.filename)}"
            )

            try:
                # Stream the upload to disk in chunks rather than buffering the whole
                # file in memory (a multi-GB BAM would otherwise load entirely), the
                # same shape the sidecar upload saves use.
                with open(temp_file_path, "wb") as f:
                    while chunk := await primary_file.read(8 * 1024 * 1024):
                        f.write(chunk)

                logger.info(f"Saved uploaded file to: {temp_file_path}")

                # Process the file
                result = await self.process_upload(str(temp_file_path))

                if result["status"] != "success":
                    return {"success": False, "error": result["error"]}

                # Add file paths to result
                result["file_paths"] = [str(temp_file_path)]

                # Update workflow with reference genome
                workflow = result["workflow"]
                workflow["reference"] = reference_genome
                workflow["workflow_type"] = "genomic_analysis"

                if ignored_files:
                    # html.escape, not safe_upload_basename: the point of this warning is
                    # to name the file the user actually chose, and sanitising would show
                    # them a name they never typed ("my file (2).bam" -> "my_file_2.bam").
                    # Escaping is what keeps it inert -- the panel assigns warnings with
                    # innerHTML -- and these strings are only ever rendered as HTML.
                    analysed = html.escape(str(primary_file.filename or "your file"))
                    ignored = [html.escape(name) for name in ignored_files]
                    was_were = "was" if len(ignored) == 1 else "were"
                    logger.warning(
                        "Analysing %s only; ignored %s",
                        primary_file.filename,
                        ", ".join(ignored_files),
                    )
                    workflow["warnings"].append(
                        f"<p>⚠️ ZaroPGx analyses one data file per job. Only {analysed} "
                        f"was analysed; {', '.join(ignored)} {was_were} ignored. "
                        "Upload each data file as its own analysis.</p>"
                    )

                # Add service configurations - explicitly set both enabled and disabled states
                workflow["optitype_enabled"] = bool(
                    optitype_enabled and optitype_enabled.lower() == "true"
                )
                workflow["gatk_enabled"] = bool(
                    gatk_enabled and gatk_enabled.lower() == "true"
                )
                workflow["pypgx_enabled"] = bool(
                    pypgx_enabled and pypgx_enabled.lower() == "true"
                )
                workflow["report_enabled"] = bool(
                    report_enabled and report_enabled.lower() == "true"
                )
                workflow["mtdna_enabled"] = bool(
                    mtdna_enabled and mtdna_enabled.lower() == "true"
                )

                # Apply user toggle overrides to workflow flags
                # User can only disable services, not enable what the workflow doesn't need
                # Final state = workflow_needs_service AND user_hasnt_disabled_service
                if optitype_enabled is not None and not workflow["optitype_enabled"]:
                    # User disabled OptiType, so disable HLA even if workflow needs it
                    workflow["needs_hla"] = False
                if gatk_enabled is not None and not workflow["gatk_enabled"]:
                    # User disabled GATK, so disable GATK even if workflow needs it
                    workflow["needs_gatk"] = False
                if pypgx_enabled is not None and not workflow["pypgx_enabled"]:
                    # User disabled PyPGx, so disable PyPGx even if workflow needs it
                    workflow["needs_pypgx"] = False
                if report_enabled is not None and not workflow["report_enabled"]:
                    # User disabled custom reports, so disable report generation
                    workflow["needs_report"] = False
                if mtdna_enabled is not None and not workflow["mtdna_enabled"]:
                    # User disabled mtDNA calling, so disable it even if the
                    # input type would otherwise support it
                    workflow["needs_mtdna"] = False

                # Debug logging for service states
                logger.info(
                    f"User toggle states received: optitype='{optitype_enabled}', "
                    f"gatk='{gatk_enabled}', pypgx='{pypgx_enabled}', report='{report_enabled}', "
                    f"mtdna='{mtdna_enabled}'"
                )
                logger.info(
                    f"User toggle states set: optitype={workflow['optitype_enabled']}, "
                    f"gatk={workflow['gatk_enabled']}, pypgx={workflow['pypgx_enabled']}, "
                    f"report={workflow['report_enabled']}, mtdna={workflow['mtdna_enabled']}"
                )
                logger.info(
                    f"Final workflow needs (after user overrides): needs_hla={workflow.get('needs_hla')}, "
                    f"needs_gatk={workflow.get('needs_gatk')}, needs_pypgx={workflow.get('needs_pypgx')}, "
                    f"needs_report={workflow.get('needs_report')}, needs_mtdna={workflow.get('needs_mtdna')}"
                )

                return {
                    "success": True,
                    "file_analysis": result["file_analysis"],
                    "workflow": workflow,
                    "file_paths": result["file_paths"],
                }

            except Exception as e:
                logger.error(f"Error processing uploaded file: {str(e)}")
                return {"success": False, "error": f"Error processing file: {str(e)}"}

        except Exception as e:
            logger.error(f"Error in process_files: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def process_upload(
        self, file_path: str, original_wgs: Optional[str] = None
    ) -> Dict:
        """
        Process an uploaded file and determine the appropriate workflow.

        Args:
            file_path: Path to the uploaded file
            original_wgs: Optional path to original WGS file if user uploads both

        Returns:
            Dictionary with analysis results and workflow configuration
        """
        try:
            logger.info(f"Processing upload: {file_path}")
            if original_wgs:
                logger.info(f"Original WGS file provided: {original_wgs}")

            # Check if file exists and is readable
            if not os.path.exists(file_path):
                error_msg = f"File not found: {file_path}"
                logger.error(error_msg)
                return {"status": "error", "error": error_msg}

            if not os.access(file_path, os.R_OK):
                error_msg = f"File is not readable: {file_path}"
                logger.error(error_msg)
                return {"status": "error", "error": error_msg}

            # Analyze the uploaded file
            logger.info("Analyzing uploaded file...")
            analysis = await self.analyze_file(file_path)

            # Enforce exactly-one-sample policy for variant-call files. BCF is held to
            # it as well as VCF: the pipeline converts a BCF to a VCF and analyses that,
            # so a multi-sample BCF would reach PyPGx/PharmCAT as a multi-sample VCF -
            # the case this check exists to stop, arriving by a different door.
            #
            # GVCF likewise, and it needs the check more than either: a multi-sample
            # gVCF is the ordinary output of a joint-calling workflow, and GenotypeGVCFs
            # would happily genotype all of them into one multi-sample VCF. Refusing
            # here is also what keeps the lane from needing a sample-selection form
            # field nobody asked for.
            if (
                analysis.file_type in (FileType.VCF, FileType.BCF, FileType.GVCF)
                and analysis.vcf_info
            ):
                label = analysis.file_type.value.upper()
                sc = analysis.vcf_info.sample_count
                if sc is None or sc != 1:
                    error_msg = (
                        f"{label} must contain exactly one sample; found {sc or 0}."
                    )
                    logger.error(error_msg)
                    return {"status": "error", "error": error_msg}

            if analysis.file_type == FileType.UNKNOWN:
                logger.warning(f"Unknown file type for {file_path}")
                # Try to provide more information about the file
                file_info = {
                    "path": str(file_path),
                    "size": (
                        os.path.getsize(file_path)
                        if os.path.exists(file_path)
                        else "unknown"
                    ),
                    "extension": os.path.splitext(file_path)[1],
                    "exists": os.path.exists(file_path),
                    "readable": os.access(file_path, os.R_OK),
                }
                logger.warning(f"File details: {file_info}")

            # Determine workflow
            logger.info("Determining workflow...")
            workflow = self.determine_workflow(analysis)
            workflow["file_type"] = analysis.file_type.value
            logger.info(f"Workflow determined: {workflow}")

            # If original WGS file is provided, update workflow
            if original_wgs:
                try:
                    logger.info("Analyzing original WGS file...")
                    original_analysis = await self.analyze_file(original_wgs)
                    workflow["original_file_type"] = original_analysis.file_type.value

                    # If original is BAM/CRAM/SAM and current is VCF, prioritize original
                    if (
                        original_analysis.file_type
                        in [FileType.BAM, FileType.CRAM, FileType.SAM]
                        and analysis.file_type == FileType.VCF
                    ):
                        workflow["needs_gatk"] = True
                        workflow["using_original_file"] = True
                        workflow["recommendations"].append(
                            f"Using original {original_analysis.file_type.value.upper()} file for more accurate variant calling."
                        )
                        logger.info(
                            f"Using original {original_analysis.file_type.value} file instead of VCF"
                        )
                except Exception as e:
                    logger.error(f"Error analyzing original WGS file: {str(e)}")
                    workflow["warnings"].append(
                        f"Could not analyze original WGS file: {str(e)}. Using uploaded file instead."
                    )

            return {
                "file_analysis": analysis,
                "workflow": workflow,
                "status": "success",
            }

        except Exception as e:
            logger.error(f"Error processing upload: {str(e)}", exc_info=True)
            return {"status": "error", "error": str(e)}
