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


# The two spellings a gVCF arrives under. GATK writes `sample.g.vcf[.gz]`, whose last
# suffix is `.vcf`; other callers write `sample.gvcf[.gz]`. Only the second pair ever
# reached the extension table in _detect_file_type, so the GATK spelling was typed VCF
# and analysed as an ordinary variant call set - see _detect_file_type for why that is a
# wrong answer rather than a wrong label.
GVCF_NAME_SUFFIXES = (".gvcf", ".gvcf.gz", ".g.vcf", ".g.vcf.gz")

# The header record every gVCF carries and no plain VCF does: one per
# reference-confidence band, e.g. `##GVCFBlock0-1=minGQ=0(inclusive),...`.
GVCF_HEADER_MARKER = "##GVCFBlock"

# Bounds on the header read below. A VCF header is a few hundred lines of a few hundred
# bytes; these caps exist so a hostile or corrupt file cannot turn a type check into an
# unbounded read.
GVCF_HEADER_SCAN_LINES = 2000
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
    far above any real VCF header (a few hundred lines of a few hundred bytes) and the
    name rule in _is_gvcf covers the ordinary case without reading anything at all.
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


def _is_gvcf(file_path: Path) -> bool:
    """Decide gVCF from the name, and from the header when the name says only "VCF"."""
    name = str(file_path).lower()
    if name.endswith(GVCF_NAME_SUFFIXES):
        return True
    if name.endswith((".vcf", ".vcf.gz")):
        return _header_declares_gvcf_blocks(file_path)
    return False


# Companion index files. The upload form invites one alongside the data file, and
# process_files does not use it yet (see the TODO there) - but it is not a second
# dataset either, so it must not raise the "extra files were not analysed" warning.
INDEX_FILE_SUFFIXES = (".bai", ".crai", ".csi", ".tbi", ".idx")


@dataclass
class FileAnalysis:
    file_type: FileType
    is_compressed: bool
    has_index: bool
    read_type: Optional[str] = (
        None  # WGS / WES / Short-read / Long-read / NGS / Sanger / Chip , etc.
    )
    vcf_info: Optional[VCFHeaderInfo] = None  # ONLY for VCF files
    file_size: Optional[int] = None
    error: Optional[str] = None
    is_valid: bool = True
    validation_errors: Optional[List[str]] = None
    # Same evidence VCFHeaderInfo.reference_genome_ambiguous/candidates carry for
    # VCF (Task 6/8), but for BAM/CRAM/SAM: vcf_info stays VCF-only (see above),
    # so a self-contradicting alignment header's @SQ evidence travels through
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

            # If it's a VCF or alignment, use the independent header inspector
            vcf_info = None
            reference_genome_ambiguous = False
            reference_genome_candidates: List[str] = []
            alignment_reference_genome: Optional[str] = None
            try:
                normalized = inspect_header(str(file_path))
                # Map normalized structure to VCFHeaderInfo when applicable
                if file_type == FileType.VCF and isinstance(normalized, dict):
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
                    # to live in -- vcf_info stays VCF-only (see FileAnalysis's
                    # dataclass comment), so it travels on FileAnalysis itself
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
        - BCF (.bcf)
        - BED (.bed)
        - 23andMe (.txt)
        """
        # Debug logging
        logger.info(f"Detecting file type for: {file_path}")
        logger.info(f"File suffixes: {file_path.suffixes}")

        # Check file extension
        ext = file_path.suffix.lower()
        logger.info(f"File extension: {ext}")

        # gVCF is decided ahead of the extension table, because the extension table
        # cannot decide it: `sample.g.vcf[.gz]` ends in `.vcf` and was typed VCF, and
        # a gVCF handed to the VCF lane is a wrong *answer*, not a wrong label. Its
        # <NON_REF> allele and ##GVCFBlock records assert reference confidence over
        # whole spans, which PharmCAT has not been validated against - so it would
        # emit star alleles nobody has checked rather than fail. determine_workflow
        # refuses gVCF outright; getting the type right is what lets it.
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
            # Check if it's a 23andMe file by examining the header
            try:
                with open(file_path, "r") as f:
                    header = f.readline()
                    if "23andMe" in header:
                        logger.info("Identified as 23andMe file")
                        return FileType.TWENTYTHREE_AND_ME
            except Exception as e:
                logger.debug(f"Error checking for 23andMe format: {str(e)}")

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
                        if _header_declares_gvcf_blocks(file_path):
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
                            if _header_declares_gvcf_blocks(file_path):
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
        - GVCF files: refused (PharmCAT is not validated against reference blocks)
        - BCF files: refused (no conversion step ships, and the sidecars gate on the
          filename, so a BCF job ends with no results rather than an error)
        - SAM files: conversion to BAM using GATK or samtools
        - FASTA files: reference genome files (unsupported for direct analysis)
        - BED files: genomic interval files (unsupported for direct analysis)

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
            "needs_conversion": False,
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
        elif analysis.file_type == FileType.CRAM:
            workflow["needs_gatk"] = True
            workflow["needs_pypgx"] = True
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
                "<p>See: https://pharmcat.clinpgx.org/using/Calling-HLA/</p>"
            )

            # Check if index exists
            if not analysis.has_index:
                workflow["recommendations"].append(
                    "<p>Creating index for CRAM file for faster processing</p>"
                )

        # SAM -> to be converted to BAM
        elif analysis.file_type == FileType.SAM:
            workflow["needs_gatk"] = True
            workflow["needs_pypgx"] = True
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
            workflow["needs_pypgx"] = True
            # main.nf hands the sidecar the ORIGINAL upload (never a lifted copy --
            # see its comment on mtdna_variants_ch) and reads whatever chrM/MT
            # records it finds; a VCF with none simply produces a no-call, the
            # same outcome PharmCAT already reports for MT-RNR1 with no outside
            # call. NOTE: the warning below ("mtDNA typing can not be performed")
            # predates this wiring and is now stale for this branch -- left as-is
            # here since updating that copy is a later task's call.
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
            workflow["warnings"].append(
                "<p>⚠️ HLA typing as well as mtDNA typing can not be performed.</p>"
            )
            workflow["warnings"].append(
                "<p>⚠️ CYP2D6 typing will be performed with degraded accuracy.</p>"
            )
            workflow["warnings"].append(
                "<p>⚠️ All genes with phenotypes affected by structural variants and copy-number variants will be evaluated with degraded accuracy.</p>"
            )

            workflow["recommendations"].append(
                "<p>VCF files use the quick pipeline:</p>"
            )
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
                is_hg38 = any(
                    ref_id in reference for ref_id in ["hg38", "grch38", "38"]
                )
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
                    # A named build that is neither GRCh38 nor GRCh37 (T2T-CHM13,
                    # say): no chain is staged for it, so no liftover exists and the
                    # old honest-provisional handling still applies unchanged.
                    workflow["unsupported"] = True
                    workflow["unsupported_reason"] = (
                        f"ZaroPGx supports GRCh38/hg38 VCF files only. This file is aligned "
                        f"to {vcf_info.reference_genome}, so any results are provisional. "
                        "Convert it to GRCh38/hg38 yourself and upload it again."
                    )
                    workflow["warnings"].append(
                        f"<p>⚠️ This file is aligned to {vcf_info.reference_genome}. Only "
                        "GRCh38/hg38 is supported, so these results are provisional.</p>"
                    )
                    workflow["recommendations"].append(
                        "<p>Convert this file to GRCh38/hg38 yourself and upload it again. "
                        "Automatic liftover covers GRCh37/hg19 only.</p>"
                    )
                    workflow["warnings"].append(
                        "<p>⚠️ Converting between reference genomes may result in a loss of fidelity.</p>"
                    )
                    workflow["is_provisional"] = True
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

        # 23andMe: refused at upload, not analysed.
        #
        # `is_provisional` used to be set here alongside `unsupported`, which read as
        # "analysed anyway, provisionally" -- the meaning it genuinely carries for a
        # GRCh37 VCF -- and waved 23andMe past the upload refusal gate. Nothing here is
        # provisional: no converter exists, and pipelines/pgx/main.nf has no `23andme`
        # branch, so the run hit `error "Unsupported input type"` and the job failed.
        # The flag was aspirational, describing an intent rather than a behaviour.
        elif analysis.file_type == FileType.TWENTYTHREE_AND_ME:
            workflow["needs_conversion"] = True
            workflow["unsupported"] = True
            workflow["unsupported_reason"] = (
                "ZaroPGx cannot analyse 23andMe genotyping files. They must be "
                "converted to VCF first, and that conversion is not implemented yet, so "
                "there is nothing ZaroPGx can run on this file. Upload a GRCh38/hg38 VCF "
                "from sequencing, or a BAM, CRAM or SAM file, instead."
            )
            workflow["recommendations"].append(
                "<p>23andMe format conversion needed - create schema reference and translation</p>"
            )
            workflow["warnings"].append(
                "<p>⚠️ Even once conversion exists, 23andMe data has limited variant coverage compared to clinical sequencing: results would be provisional and may miss important variants.</p>"
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

        # GVCF: refused at upload, not analysed.
        #
        # A gVCF is not a VCF with extra rows. Its <NON_REF> symbolic allele and its
        # ##GVCFBlock records assert reference confidence over whole spans, and PharmCAT
        # has not been validated against either. That makes it worse than the formats
        # refused above rather than better: routed onto the vcf lane it would not error,
        # it would emit star alleles nobody has checked. Until the validation exists,
        # saying so at upload beats publishing a confident wrong answer. None of the
        # needs_* flags are set, because there is no workflow to plan.
        elif analysis.file_type == FileType.GVCF:
            workflow["unsupported"] = True
            # No angle brackets around NON_REF, deliberately. This string is the 400's
            # plain-text `detail` *and* the panel's red alert, and the panel assigns it
            # with innerHTML - a literal "<NON_REF>" would be parsed as a tag and vanish
            # from the sentence. Escaping instead would fix the panel and leave
            # "&lt;NON_REF&gt;" in the API error. The bare name reads correctly in both.
            workflow["unsupported_reason"] = (
                "ZaroPGx cannot analyse gVCF files. A gVCF records reference-confidence "
                "blocks (the NON_REF allele and ##GVCFBlock header records) alongside "
                "its variant calls, and PharmCAT has not been validated against them, so "
                "analysing one would produce star alleles nobody has checked rather than "
                "a clear failure. Convert it to a plain single-sample GRCh38/hg38 VCF "
                "first, or upload the BAM, CRAM or SAM it was called from."
            )
            workflow["recommendations"].append(
                "<p>Convert the gVCF to a plain VCF, then upload that:</p>"
            )
            workflow["recommendations"].append(
                "<p>• GATK: GenotypeGVCFs turns a gVCF into a genotyped VCF</p>"
            )
            workflow["recommendations"].append(
                "<p>• bcftools: drop the reference blocks, e.g. "
                "bcftools view -e 'ALT=\"&lt;NON_REF&gt;\"' input.g.vcf.gz</p>"
            )
            workflow["recommendations"].append(
                "<p>• Or upload the BAM, CRAM or SAM the gVCF was called from and let "
                "ZaroPGx call the variants.</p>"
            )

        # BCF: refused at upload, not analysed.
        #
        # Renaming it onto the pipeline's vcf branch was tried and reverted, because the
        # branch does not convert anything - it stages the upload verbatim, so the file
        # arrives downstream still called `upload_sample.bcf`. docker/pharmcat's
        # /genotype gates on that name (`.vcf`/`.vcf.gz`/`.vcf.bgz`) and answers 400,
        # and main.nf's PharmCAT curl ends in `|| true`, so the 400 is swallowed and the
        # run "completes" with no PharmCAT output at all. That is a silent wrong answer
        # where there used to be a loud `error "Unsupported input type"` - strictly
        # worse. (docker/pypgx stores it as {uuid}.vcf and feeds it to PyPGx unchecked,
        # which is its own unvalidated path.)
        #
        # Accepting BCF honestly needs a real conversion step. ZaroPGx ships none, so
        # until it does, the user is told the one command that fixes it. None of the
        # needs_* flags are set, because there is no workflow to plan.
        elif analysis.file_type == FileType.BCF:
            workflow["unsupported"] = True
            workflow["unsupported_reason"] = (
                "ZaroPGx cannot analyse BCF files. BCF is the binary encoding of a VCF, "
                "but ZaroPGx ships no conversion step, and the analysis tools are handed "
                "the file under its original name - so a BCF job would end with no "
                "results rather than an error. Convert it to a bgzipped VCF first and "
                "upload that: bcftools view -O z sample.bcf > sample.vcf.gz"
            )
            workflow["recommendations"].append(
                "<p>Convert the BCF to a bgzipped VCF, then upload that:</p>"
            )
            workflow["recommendations"].append(
                "<p>• bcftools view -O z sample.bcf &gt; sample.vcf.gz</p>"
            )
            workflow["recommendations"].append(
                "<p>• Index it if you have one to spare: bcftools index -t "
                "sample.vcf.gz</p>"
            )
            workflow["recommendations"].append(
                "<p>• A GRCh38/hg38 VCF is the fastest input ZaroPGx accepts.</p>"
            )

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
                "<p>Priority 2 (Development): BAM, CRAM, SAM, all NGS-derived.</p>"
            )
            workflow["recommendations"].append(
                "<p>Not accepted: FASTQ. ZaroPGx ships no aligner — align the reads to GRCh38/hg38 yourself and upload the resulting BAM, CRAM or SAM.</p>"
            )
            workflow["recommendations"].append(
                "<p>Not accepted: BCF. Convert it first: bcftools view -O z sample.bcf &gt; sample.vcf.gz</p>"
            )
            workflow["recommendations"].append(
                "<p>Priority 3 (Research): Other sequencing and genotyping formats.</p>"
            )
            workflow["recommendations"].append(
                "<p>Priority 4 (Research): BED, gVCF, 23andMe, AncestryDNA, various TXT formats.</p>"
            )
            workflow["recommendations"].append(
                "<p>Priority 5 (Early research): T2T format, and all else.</p>"
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

        # A GRCh37/hg19-aligned BAM/CRAM/SAM is refused, not analysed.
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
        # 23andMe, BCF): say so now, with the fix the user can act on. And the fix
        # is a real one now -- calling variants against GRCh37 and uploading the
        # resulting VCF lands on the supported liftover lane.
        #
        # Keyed on the DETECTED build only. `None` (no usable @SQ evidence) is left
        # alone rather than guessed at, exactly as the ambiguity check above does:
        # refusing a file whose build simply could not be read would reject most
        # hand-made and minimal BAMs, which is a different defect.
        if analysis.file_type in (FileType.BAM, FileType.CRAM, FileType.SAM):
            detected_build = (analysis.reference_genome or "").lower()
            if any(token in detected_build for token in ("grch37", "hg19", "b37")):
                file_label = analysis.file_type.value.upper()
                workflow["unsupported"] = True
                # Deliberately NOT provisional: nothing is analysed, so the flag
                # that means "analysed anyway, provisionally" would wave this past
                # the upload gate (the 23andMe mistake).
                workflow["is_provisional"] = False
                workflow["unsupported_reason"] = (
                    f"This {file_label} file is aligned to "
                    f"{analysis.reference_genome}. ZaroPGx analyses against "
                    f"GRCh38/hg38 only, and reading these reads as GRCh38 would "
                    f"take every gene from the wrong position and report star "
                    f"alleles that are not yours. Automatic liftover covers VCFs, "
                    f"not aligned reads."
                )
                workflow["recommendations"].append(
                    "<p>Call variants against GRCh37/hg19 yourself (bcftools or "
                    "GATK HaplotypeCaller), then upload the resulting VCF. "
                    "ZaroPGx lifts that over to GRCh38 automatically.</p>"
                )
                workflow["recommendations"].append(
                    "<p>Or realign the reads to GRCh38/hg38 and upload the "
                    f"resulting {file_label} file.</p>"
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

            # Enforce exactly-one-sample policy for VCF
            if analysis.file_type == FileType.VCF and analysis.vcf_info:
                sc = analysis.vcf_info.sample_count
                if sc is None or sc != 1:
                    error_msg = f"VCF must contain exactly one sample; found {sc or 0}."
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
