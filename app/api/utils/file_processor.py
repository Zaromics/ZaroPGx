import os
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Optional, Tuple, List
import zipfile
import re
from dataclasses import dataclass
from enum import Enum

# Optional import: pysam for rich header parsing (VCF/BAM/CRAM). Fallbacks are provided.
try:
    import pysam  # type: ignore
    _HAS_PYSAM = True
except Exception:  # optional dependency at runtime
    pysam = None  # type: ignore
    _HAS_PYSAM = False

# Import models from models.py to ensure consistency
from app.api.models import (
    FileType, SequencingProfile, VCFHeaderInfo,
    GenomicFileHeader, FileInfo, MetadataInfo,
    SequenceInfo, ProgramInfo, FormatSpecificInfo
)

from app.api.utils.header_inspector import inspect_header
from app.api.utils.file_utils import is_compressed_file, has_index_file

# Configure logging
logger = logging.getLogger(__name__)

@dataclass
class FileAnalysis:
    file_type: FileType
    is_compressed: bool
    has_index: bool
    read_type: Optional[str] = None # WGS / WES / Short-read / Long-read / NGS / Sanger / Chip , etc.
    vcf_info: Optional[VCFHeaderInfo] = None # ONLY for VCF files
    file_size: Optional[int] = None
    error: Optional[str] = None
    is_valid: bool = True
    validation_errors: Optional[List[str]] = None

class FileProcessor:
    def __init__(self, temp_dir: str = "/tmp"):
        self.temp_dir = Path(temp_dir)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

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
            try:
                normalized = inspect_header(str(file_path))
                # Map normalized structure to VCFHeaderInfo when applicable
                if file_type == FileType.VCF and isinstance(normalized, dict):
                    # Reference genome inference
                    reference_genome = (normalized.get('metadata') or {}).get('reference_genome') or "unknown"
                    # Sequencing profile inference based on contigs count
                    contigs_list = [c.get('name') for c in (normalized.get('sequences') or []) if isinstance(c, dict) and c.get('name')]
                    seq_profile = SequencingProfile.UNKNOWN
                    if len(contigs_list) > 20:
                        seq_profile = SequencingProfile.WGS
                    elif len(contigs_list) > 0:
                        seq_profile = SequencingProfile.WES
                    samples = normalized.get('samples') or []
                    vcf_info = VCFHeaderInfo(
                        reference_genome=reference_genome,
                        sequencing_platform=(normalized.get('metadata') or {}).get('created_by') or 'unknown',
                        sequencing_profile=seq_profile,
                        has_index=has_index,
                        is_bgzipped=is_compressed or str(file_path).endswith('.gz'),
                        contigs=contigs_list,
                        sample_count=len(samples),
                        variant_count=None,
                    )
            except Exception as e:
                logger.warning(f"Independent header inspector failed, falling back for type {file_type}: {e}")
                # Fall back to prior behavior for VCF only
                if file_type == FileType.VCF:
                    try:
                        vcf_info = await self._analyze_vcf_header(file_path)
                        vcf_info.is_bgzipped = is_compressed or vcf_info.is_bgzipped or str(file_path).endswith('.gz')
                    except Exception:
                        pass

            # Create the file analysis object with all the gathered information
            analysis = FileAnalysis(
                file_type=file_type,
                is_compressed=is_compressed,
                has_index=has_index,
                vcf_info=vcf_info,
                file_size=file_size
            )
            
            logger.info(f"Analysis complete: {analysis}")
            return analysis

        except Exception as e:
            logger.error(f"Error analyzing file {file_path}: {str(e)}")
            return FileAnalysis(
                file_type=FileType.UNKNOWN,
                is_compressed=False,
                has_index=False,
                error=str(e)
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
        - GVCF (.gvcf, .gvcf.gz)
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
        
        # Check for double extensions like .vcf.gz
        if ext == '.gz' and len(file_path.suffixes) > 1:
            prev_ext = file_path.suffixes[-2].lower()
            logger.info(f"Previous extension for compressed file: {prev_ext}")
            
            # Check for VCF format
            if prev_ext == '.vcf':
                logger.info("Identified as compressed VCF file")
                return FileType.VCF
            # Check for GVCF format
            elif prev_ext == '.gvcf':
                logger.info("Identified as compressed GVCF file")
                return FileType.GVCF
            # Check for FASTQ format
            elif prev_ext in ['.fastq', '.fq']:
                logger.info("Identified as compressed FASTQ file")
                return FileType.FASTQ
            # Check for FASTA format
            elif prev_ext in ['.fasta', '.fa', '.fna']:
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
        if ext in ['.vcf']:
            logger.info("Identified as VCF file")
            return FileType.VCF
        elif ext == '.bam':
            logger.info("Identified as BAM file")
            return FileType.BAM
        elif ext == '.cram':
            logger.info("Identified as CRAM file")
            return FileType.CRAM
        elif ext == '.sam':
            logger.info("Identified as SAM file")
            return FileType.SAM
        elif ext in ['.fastq', '.fq']:
            logger.info("Identified as FASTQ file")
            return FileType.FASTQ
        elif ext in ['.fasta', '.fa', '.fna']:
            logger.info("Identified as FASTA file")
            return FileType.FASTA
        elif ext in ['.gvcf']:
            logger.info("Identified as GVCF file")
            return FileType.GVCF
        elif ext == '.bcf':
            logger.info("Identified as BCF file")
            return FileType.BCF
        elif ext == '.bed':
            logger.info("Identified as BED file")
            return FileType.BED
        elif ext in ['.txt', '.csv']:
            # Check if it's a 23andMe file by examining the header
            try:
                with open(file_path, 'r') as f:
                    header = f.readline()
                    if '23andMe' in header:
                        logger.info("Identified as 23andMe file")
                        return FileType.TWENTYTHREE_AND_ME
            except Exception as e:
                logger.debug(f"Error checking for 23andMe format: {str(e)}")
        
        # If extension doesn't match, try to determine from content first
        try:
            # For possibly compressed files, use gzip to open
            if ext == '.gz':
                import gzip
                with gzip.open(file_path, 'rt', errors='ignore') as f:
                    first_line = f.readline().strip()
                    if first_line.startswith('##fileformat=VCF'):
                        logger.info("Identified as gzipped VCF from content")
                        return FileType.VCF
                    
                    # If not VCF, check if it might be FASTQ
                    f.seek(0)
                    first_line = f.readline().strip()
                    if first_line.startswith('@'):
                        second_line = f.readline().strip()
                        third_line = f.readline().strip()
                        fourth_line = f.readline().strip()
                        if third_line.startswith('+') and len(second_line) > 0 and len(fourth_line) > 0:
                            logger.info("Identified as gzipped FASTQ from content")
                            return FileType.FASTQ
            else:
                # Regular file check
                with open(file_path, 'rb') as f:
                    try:
                        header = f.read(20).decode('utf-8', errors='ignore')
                        if '##fileformat=VCF' in header:
                            logger.info("Identified as VCF from content")
                            return FileType.VCF
                        elif header.startswith('@HD') or header.startswith('@SQ'):
                            logger.info("Identified as SAM from content")
                            return FileType.SAM
                        
                        # BAM is binary, check for BAM magic bytes
                        f.seek(0)
                        if f.read(4) == b'BAM\1':
                            logger.info("Identified as BAM from content")
                            return FileType.BAM
                        # CRAM magic bytes
                        f.seek(0)
                        if f.read(4) == b'CRAM':
                            logger.info("Identified as CRAM from content")
                            return FileType.CRAM
                    except UnicodeDecodeError:
                        # If we can't decode as text, it might be binary
                        pass
                
                # Check for FASTQ format by looking at first few lines
                try:
                    with open(file_path, 'r', errors='ignore') as f:
                        first_line = f.readline().strip()
                        if first_line.startswith('@') and len(first_line) > 1:
                            second_line = f.readline().strip()
                            third_line = f.readline().strip()
                            fourth_line = f.readline().strip()
                            if third_line.startswith('+') and len(second_line) > 0 and len(fourth_line) > 0:
                                logger.info("Identified as FASTQ from content")
                                return FileType.FASTQ
                except Exception:
                    pass
        
        except Exception as e:
            logger.debug(f"Error detecting file type from content: {str(e)}")

        # If content-based detection failed, try filename patterns as fallback
        if ext == '.gz':
            filename = file_path.name.lower()
            if 'vcf' in filename and 'gvcf' not in filename:
                logger.info("Identified as gzipped VCF file (from filename pattern)")
                return FileType.VCF
            elif 'gvcf' in filename:
                logger.info("Identified as gzipped GVCF file (from filename pattern)")
                return FileType.GVCF
            elif any(pattern in filename for pattern in ['fastq', 'fq']):
                logger.info("Identified as gzipped FASTQ file (from filename pattern)")
                return FileType.FASTQ
            elif any(pattern in filename for pattern in ['fasta', 'fa', 'fna']):
                logger.info("Identified as gzipped FASTA file (from filename pattern)")
                return FileType.FASTA

        logger.warning(f"Could not determine file type for {file_path}")
        return FileType.UNKNOWN

    # Removed: VCF header analysis is handled by header_inspector.inspect_header

    def _analyze_alignment_header_with_pysam(self, file_path: Path) -> Optional[Dict[str, any]]:
        """
        Extract alignment header information for BAM/CRAM/SAM files using pysam when available.
        Returns a dict with selected fields or None on failure/unavailability.
        """
        if not _HAS_PYSAM:
            return None
        try:
            af = pysam.AlignmentFile(str(file_path), 'r')
            header_dict = af.header.to_dict() if hasattr(af.header, 'to_dict') else {}
            contigs = []
            if isinstance(header_dict, dict) and 'SQ' in header_dict:
                contigs = [sq.get('SN') for sq in header_dict.get('SQ', []) if isinstance(sq, dict) and sq.get('SN')]
            read_groups = header_dict.get('RG', []) if isinstance(header_dict, dict) else []
            platform = None
            for rg in read_groups:
                if isinstance(rg, dict) and rg.get('PL'):
                    platform = rg.get('PL')
                    break
            info = {
                'contigs': contigs,
                'read_group_count': len(read_groups) if isinstance(read_groups, list) else 0,
                'platform': platform or 'unknown',
            }
            logger.info(f"Alignment header (pysam): contigs={len(contigs)}, platform={info['platform']}, RGs={info['read_group_count']}")
            return info
        except Exception as e:
            logger.debug(f"pysam.AlignmentFile failed to read header for {file_path}: {e}")
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
            path_parts = reference_path.split('/')
            filename = path_parts[-1] if path_parts else reference_path

            # Remove file extensions using regex
            base_name = re.sub(r'\.(fa|fasta|fna|gz)$', '', filename, flags=re.IGNORECASE)

            # Look for embedded genome patterns (most common case)
            grch38_patterns = [r'GRCh38', r'grch38', r'hg38', r'HG38']
            grch37_patterns = [r'GRCh37', r'grch37', r'hg19', r'HG19']

            for pattern in grch38_patterns:
                if re.search(pattern, base_name, re.IGNORECASE):
                    return 'GRCh38'

            for pattern in grch37_patterns:
                if re.search(pattern, base_name, re.IGNORECASE):
                    return 'GRCh37'

            # Handle exact matches
            if base_name.lower() == 'hg38':
                return 'GRCh38'
            elif base_name.lower() == 'hg19':
                return 'GRCh37'

            # Handle prefix matches
            if base_name.lower().startswith('grch38'):
                return 'GRCh38'
            elif base_name.lower().startswith('grch37'):
                return 'GRCh37'

            # If it starts with GRCh, it's likely already properly formatted
            if base_name.startswith('GRCh'):
                return base_name

            # Try to extract GRCh pattern from anywhere in the name
            grch_match = re.search(r'(GRCh\d+)', base_name)
            if grch_match:
                return grch_match.group(1)

            # Last resort: return a cleaned version
            logger.warning(f"Could not extract genome name from {base_name}, returning as-is")
            return base_name

        except Exception as e:
            logger.debug(f"Error extracting genome name from path {reference_path}: {e}")
            return "unknown"

    def determine_workflow(self, analysis: FileAnalysis) -> Dict:
        """
        Determine the appropriate workflow based on file analysis.

        This method implements the detailed workflow logic from workflow_logic.md:
        - FASTQ files: alignment with specific tools based on read type and hardware
        - CRAM files: conversion to BAM with specific tools and considerations
        - BAM files: OptiType/HLA typing + PyPGx pipeline with detailed recommendations
        - VCF files: direct PyPGx + PharmCAT with outside calls
        - GVCF files: genomic VCF with reference calls, treated as VCF
        - BCF files: binary VCF format, converted as needed
        - SAM files: conversion to BAM using GATK or samtools
        - FASTA files: reference genome files (unsupported for direct analysis)
        - BED files: genomic interval files (unsupported for direct analysis)

        Returns a dictionary with workflow configuration and recommendations.
        """
        workflow = {
            "needs_gatk": False,
            "needs_indexing": False,
            "needs_alignment": False,
            "needs_liftover": False, # If VCF, if GRCh37 (hg19) reference, bcftools liftover to GRCh38 (hg38)
            "needs_conversion": False,
            "needs_hla": False,
            "needs_pypgx": False,
            "needs_pypgx_bam2vcf": False,
            "is_provisional": False,
            "recommendations": [],
            "warnings": [],
            "unsupported": False,
            "unsupported_reason": None
        }

        # FASTQ (curated on 2025-09-27)
        if analysis.file_type == FileType.FASTQ:
            workflow["needs_hla"] = True
            workflow["needs_alignment"] = True
            workflow["needs_gatk"] = True
            workflow["needs_pypgx"] = True
            workflow["unsupported"] = True
            workflow["unsupported_reason"] = (
                "FASTQ datafiles are an ideal starting point, however, ZaroPGx does not support this workflow yet."
                "Once support reaches completion, paired-read FASTQ datafiles can be uploaded as inputs."
                "Support for single FASTQ datafile as input is being reviewed.")
            # Detailed FASTQ alignment recommendations based on read type and hardware resources
            workflow["recommendations"].append(
                "<p>Step 1: HLA typing using OptiType. nf-core/hlatyping is the tool which provides OptiType.</p>"
            )
            workflow["recommendations"].append(
                "<p>Step 2: Alignment to GRCh38 (hg38) reference genome, based on read type: "
                "If Long-read: Use minimap2 for alignment. "
                "If Short-read: Use bwa-mem2 (requires copious memory, please ensure ≥64GB RAM available), or BWA (Burrows-Wheeler Aligner). "
                "These tools are not yet implemented. (TO DO)</p>"
            )
            workflow["recommendations"].append(
                "<p>Step 3: PyPGx star allele calling</p>"
            )
            workflow["recommendations"].append(
                "<p>Step 3: Convert aligned BAM to VCF using PyPGx create-input-vcf</p>"
            )

            workflow["recommendations"].append(
                "<p>Consider using nf-core pipelines for comprehensive FASTQ processing.</p>"
            )

        # CRAM -> to be converted to BAM (lossy)
        elif analysis.file_type == FileType.CRAM:
            workflow["needs_gatk"] = True
            workflow["needs_pypgx"] = True
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
            workflow["warnings"].append(
                "<p>⚠️ VCF datafiles lack the necessary information to perform complete pharmacogenomic analysis.</p>"
            )
            workflow["warnings"].append(
                "<p>The analysis can proceed, however, the results will be incomplete and have degraded accuracy.</p>"
            )
            workflow["warnings"].append(
                "<p>⚠️ HLA-A, HLA-B, and HLA-C typing can not be performed.</p>"
            )
            workflow["warnings"].append(
                "<p>⚠️ CYP2D6 typing will be performed, albeit with degraded accuracy.</p>"
            )
            workflow["warnings"].append(
                "<p>⚠️ All genes whose phenotypes are affected by structural variants and copy-number variants will be evaluated with degraded accuracy.</p>"
            )
            workflow["warnings"].append(
                "<p>If you have an upstream, or original, datafile, such as FASTQ/BAM/SAM/CRAM, please consider uploading it instead in order for the PGx analysis to yield results with optimal fidelity.</p>"
            )
            workflow["warnings"].append(
                "<p>Although significant computation and processing time is required, if possible, using an upstream datafile(s) is strongly recommended.</p>"
            )
            workflow["recommendations"].append(
                "<p>VCF files use the quick pipeline:</p>"
            )
            workflow["warnings"].append(
                "<p>Skip HLA typing with nf-core/hlatyping (OptiType); VCF datafiles are unsuitable inputs for this operation.</p>"
            )
            workflow["warnings"].append(
                "<p>If you have an upstream/original datafile e.g. FASTQ or BAM, please upload that instead in order to have HLA typing performed.</p>"
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
                is_hg38 = any(ref_id in reference for ref_id in ["hg38", "grch38", "38"])
                if is_hg38:
                    workflow["recommendations"].append(
                        f"<p>✓ Compatible GRCh38 reference genome detected: {vcf_info.reference_genome}</p>"
                    )
                elif reference != "unknown":
                    workflow["unsupported"] = True
                    workflow["unsupported_reason"] = (
                        f"The uploaded VCF file is not aligned to the GRCh38/hg38 reference genome."
                    )
                    workflow["warnings"].append(
                        f"<p>⚠️ The uploaded VCF file is aligned to the {vcf_info.reference_genome} reference genome. Only GRCh38/hg38 is currently supported.</p>"
                    )
                    workflow["recommendations"].append(
                        "<p>Step 0: Convert the VCF file to GRCh38/hg38 using bcftools. (TO DO)</p>"
                    )
                    workflow["recommendations"].append(
                        "<p>Once the VCF file has been re-aligned to GRCh38/hg38, it will proceed to Step 1.</p>"
                    )
                    workflow["warnings"].append(
                        "<p>⚠️ Realigning the VCF file to the GRCh38 reference genome may result in a loss of fidelity.</p>"
                    )
                    workflow["warnings"].append(
                        "<p>While no realignment tool yields perfect fidelity, bcftools' liftover is used as it is perhaps the best tool for the job.</p>"
                    )
                    workflow["is_provisional"] = True

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

        # 23andMe files need conversion
        elif analysis.file_type == FileType.TWENTYTHREE_AND_ME:
            workflow["needs_conversion"] = True
            workflow["is_provisional"] = True
            workflow["unsupported"] = True
            workflow["unsupported_reason"] = (
                "23andMe data format requires conversion to VCF before analysis. "
                "This functionality is not yet implemented."
            )
            workflow["recommendations"].append(
                "<p>23andMe format conversion needed - create schema reference and translation</p>"
            )
            workflow["warnings"].append(
                "<p>23andMe data has limited variant coverage compared to clinical sequencing. Results will be provisional and may miss important variants.</p>"
            )

        # FASTA - reference genome files
        elif analysis.file_type == FileType.FASTA:
            workflow["unsupported"] = True
            workflow["unsupported_reason"] = "FASTA files are reference genome files and cannot be analyzed directly."
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

        # GVCF - genomic VCF with reference calls
        elif analysis.file_type == FileType.GVCF:
            workflow["needs_pypgx"] = True
            workflow["recommendations"].append(
                "<p>GVCF files (genomic VCF with reference calls):</p>"
            )
            workflow["recommendations"].append(
                "<p>• Will be processed through PyPGx and PharmCAT pipeline</p>"
            )
            workflow["recommendations"].append(
                "<p>• GVCFs contain both variant and reference calls</p>"
            )
            workflow["recommendations"].append(
                "<p>• May require conversion to standard VCF for some tools</p>"
            )

        # BCF - binary VCF format
        elif analysis.file_type == FileType.BCF:
            workflow["needs_pypgx"] = True
            workflow["recommendations"].append(
                "<p>BCF files (binary VCF format):</p>"
            )
            workflow["recommendations"].append(
                "<p>• Will be converted to VCF format if needed</p>"
            )
            workflow["recommendations"].append(
                "<p>• Use bcftools for conversion: bcftools view input.bcf > output.vcf</p>"
            )
            workflow["recommendations"].append(
                "<p>• Standard PyPGx + PharmCAT pipeline will be applied</p>"
            )

        # BED - genome interval/annotation files
        elif analysis.file_type == FileType.BED:
            workflow["unsupported"] = True
            workflow["unsupported_reason"] = "BED files are typically downstream of sequencing / genotyping, and may contain genomic intervals or other information in an unusual format that cannot be directly analyzed."
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
            workflow["unsupported_reason"] = f"Unrecognized file format: {analysis.file_type.value}."
            workflow["recommendations"].append(
                "<p>The file(s) you have selected could not be recognized.</p>"
            )
            workflow["recommendations"].append(
                "<p>If this is a bug, please report it on GitHub, see bottom of the page. Apologies for the inconvenience.</p>"
            )
            workflow["recommendations"].append(
                "<p>Supported formats:</p>"
            )
            workflow["recommendations"].append(
                "<p>Priority 0 (Supported): VCF, GRCh38/hg38, NGS-derived.</p>"
            )
            workflow["recommendations"].append(
                "<p>Priority 1 (Development): VCF, GRCh37/hg19, NGS-derived.</p>"
            )
            workflow["recommendations"].append(
                "<p>Priority 2 (Development): BAM, CRAM, SAM, FASTQ, BCF, all NGS-derived.</p>"
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

        return workflow

    async def process_files(self, files: List, reference_genome: str = "hg38", 
                          optitype_enabled: Optional[str] = None,
                          gatk_enabled: Optional[str] = None,
                          pypgx_enabled: Optional[str] = None,
                          report_enabled: Optional[str] = None) -> Dict:
        """
        Process multiple uploaded files and determine the appropriate workflow.
        
        Args:
            files: List of uploaded files
            reference_genome: Reference genome to use (default: hg38)
            optitype_enabled: Whether OptiType is enabled
            gatk_enabled: Whether GATK processing is enabled
            pypgx_enabled: Whether PyPGx analysis is enabled
            report_enabled: Whether custom report generation is enabled
            
        Returns:
            Dictionary with analysis results and workflow configuration
        """
        try:
            logger.info(f"Processing {len(files)} files")
            
            if not files:
                return {
                    "success": False,
                    "error": "No files provided"
                }
            
            # For now, process only the first file (primary file)
            # TODO: Support multiple files in the future
            # 2 files can now be uploaded, but the use of the index file needs work.
            primary_file = files[0]
            
            # Save the uploaded file to temporary location
            temp_file_path = self.temp_dir / f"upload_{primary_file.filename}"
            
            try:
                # Write file content
                with open(temp_file_path, "wb") as f:
                    content = await primary_file.read()
                    f.write(content)
                
                logger.info(f"Saved uploaded file to: {temp_file_path}")
                
                # Process the file
                result = await self.process_upload(str(temp_file_path))
                
                if result["status"] != "success":
                    return {
                        "success": False,
                        "error": result["error"]
                    }
                
                # Add file paths to result
                result["file_paths"] = [str(temp_file_path)]
                
                # Update workflow with reference genome
                workflow = result["workflow"]
                workflow["reference"] = reference_genome
                workflow["workflow_type"] = "genomic_analysis"
                
                # Add service configurations - explicitly set both enabled and disabled states
                workflow["optitype_enabled"] = bool(optitype_enabled and optitype_enabled.lower() == "true")
                workflow["gatk_enabled"] = bool(gatk_enabled and gatk_enabled.lower() == "true")
                workflow["pypgx_enabled"] = bool(pypgx_enabled and pypgx_enabled.lower() == "true")
                workflow["report_enabled"] = bool(report_enabled and report_enabled.lower() == "true")
                
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
                
                # Debug logging for service states
                logger.info(f"User toggle states received: optitype='{optitype_enabled}', "
                           f"gatk='{gatk_enabled}', pypgx='{pypgx_enabled}', report='{report_enabled}'")
                logger.info(f"User toggle states set: optitype={workflow['optitype_enabled']}, "
                           f"gatk={workflow['gatk_enabled']}, pypgx={workflow['pypgx_enabled']}, "
                           f"report={workflow['report_enabled']}")
                logger.info(f"Final workflow needs (after user overrides): needs_hla={workflow.get('needs_hla')}, "
                           f"needs_gatk={workflow.get('needs_gatk')}, needs_pypgx={workflow.get('needs_pypgx')}, "
                           f"needs_report={workflow.get('needs_report')}")
                
                return {
                    "success": True,
                    "file_analysis": result["file_analysis"],
                    "workflow": workflow,
                    "file_paths": result["file_paths"]
                }
                
            except Exception as e:
                logger.error(f"Error processing uploaded file: {str(e)}")
                return {
                    "success": False,
                    "error": f"Error processing file: {str(e)}"
                }
                
        except Exception as e:
            logger.error(f"Error in process_files: {str(e)}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    async def process_upload(self, file_path: str, original_wgs: Optional[str] = None) -> Dict:
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
                return {
                    "status": "error",
                    "error": error_msg
                }
                
            if not os.access(file_path, os.R_OK):
                error_msg = f"File is not readable: {file_path}"
                logger.error(error_msg)
                return {
                    "status": "error",
                    "error": error_msg
                }
            
            # Analyze the uploaded file
            logger.info("Analyzing uploaded file...")
            analysis = await self.analyze_file(file_path)
            
            # Enforce exactly-one-sample policy for VCF
            if analysis.file_type == FileType.VCF and analysis.vcf_info:
                sc = analysis.vcf_info.sample_count
                if sc is None or sc != 1:
                    error_msg = f"VCF must contain exactly one sample; found {sc or 0}."
                    logger.error(error_msg)
                    return {
                        "status": "error",
                        "error": error_msg
                    }

            if analysis.file_type == FileType.UNKNOWN:
                logger.warning(f"Unknown file type for {file_path}")
                # Try to provide more information about the file
                file_info = {
                    "path": str(file_path),
                    "size": os.path.getsize(file_path) if os.path.exists(file_path) else "unknown",
                    "extension": os.path.splitext(file_path)[1],
                    "exists": os.path.exists(file_path),
                    "readable": os.access(file_path, os.R_OK)
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
                    if (original_analysis.file_type in [FileType.BAM, FileType.CRAM, FileType.SAM] and 
                        analysis.file_type == FileType.VCF):
                        workflow["needs_gatk"] = True
                        workflow["using_original_file"] = True
                        workflow["recommendations"].append(
                            f"Using original {original_analysis.file_type.value.upper()} file for more accurate variant calling."
                        )
                        logger.info(f"Using original {original_analysis.file_type.value} file instead of VCF")
                except Exception as e:
                    logger.error(f"Error analyzing original WGS file: {str(e)}")
                    workflow["warnings"].append(
                        f"Could not analyze original WGS file: {str(e)}. Using uploaded file instead."
                    )

            return {
                "file_analysis": analysis,
                "workflow": workflow,
                "status": "success"
            }

        except Exception as e:
            logger.error(f"Error processing upload: {str(e)}", exc_info=True)
            return {
                "status": "error",
                "error": str(e)
            } 