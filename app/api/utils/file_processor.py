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

# Configure logging
logger = logging.getLogger(__name__)

@dataclass
class FileAnalysis:
    file_type: FileType
    is_compressed: bool
    has_index: bool
    vcf_info: Optional[VCFHeaderInfo] = None
    file_size: Optional[int] = None
    error: Optional[str] = None

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
            
            # Detect compression status BEFORE detecting file type
            is_compressed = self._is_compressed(file_path)
            logger.info(f"Is compressed: {is_compressed}")
            
            has_index = self._has_index_file(file_path)
            logger.info(f"Has index: {has_index}")

            # Determine file type
            file_type = self._detect_file_type(file_path)
            logger.info(f"Detected file type: {file_type.value}")
            
            # If it's a VCF or alignment, use the independent header inspector
            vcf_info = None
            try:
                from app.api.utils.header_inspector import inspect_header
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

    def _is_compressed(self, file_path: Path) -> bool:
        """
        Check if file is compressed (zip, gzip, etc.)
        
        Detects compression based on:
        1. File extension (.gz, .zip, .bgz)
        2. Magic bytes at beginning of file
        """
        # First check extension
        if any(str(file_path).lower().endswith(ext) for ext in ['.gz', '.bgz', '.zip', '.bz2']):
            logger.debug(f"File {file_path} detected as compressed based on extension")
            return True
            
        # Then check magic bytes
        try:
            with open(file_path, 'rb') as f:
                magic_bytes = f.read(4)
                
                # Gzip: 1F 8B
                if magic_bytes.startswith(b'\x1f\x8b'):
                    logger.debug(f"File {file_path} detected as gzip based on magic bytes")
                    return True
                    
                # Zip: PK
                if magic_bytes.startswith(b'PK'):
                    logger.debug(f"File {file_path} detected as zip based on magic bytes") 
                    return True
                    
                # Bzip2: BZ
                if magic_bytes.startswith(b'BZ'):
                    logger.debug(f"File {file_path} detected as bzip2 based on magic bytes")
                    return True
                    
            return False
        except Exception as e:
            logger.warning(f"Error checking if file {file_path} is compressed: {str(e)}")
            # If we can't check, assume it's not compressed
            return False

    def _has_index_file(self, file_path: Path) -> bool:
        """Check if file has an associated index file"""
        index_extensions = ['.tbi', '.csi', '.bai', '.fai', '.crai']
        for ext in index_extensions:
            if (file_path.parent / f"{file_path.stem}{ext}").exists():
                return True
        return False

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
            "needs_hla": False,
            "needs_pypgx": False,
            "needs_conversion": False,
            "needs_pypgx_bam2vcf": False,
            "is_provisional": False,
            "go_directly_to_pharmcat": False,
            "recommendations": [],
            "warnings": [],
            "unsupported": False,
            "unsupported_reason": None
        }

        # FASTQ -> hg38 reference to be indexed and aligned
        if analysis.file_type == FileType.FASTQ:
            workflow["needs_alignment"] = True
            workflow["needs_gatk"] = True
            workflow["needs_hla"] = True
            workflow["needs_pypgx"] = True
            workflow["unsupported"] = True
            workflow["unsupported_reason"] = (
                "FASTQ files require alignment to a reference genome before variant calling. "
                "This functionality is not yet implemented."
            )

            # Detailed FASTQ alignment recommendations based on read type and hardware
            workflow["recommendations"].append(
                "FASTQ files need alignment to hg38 reference genome. Based on read type:"
            )
            workflow["recommendations"].append(
                "• Step 1: HLA typing using OptiType (preserves FASTQ format)"
            )
            workflow["recommendations"].append(
                "• Step 2: Alignment to hg38 reference genome:"
            )
            workflow["recommendations"].append(
                "  - Long-read: Use minimap2 for alignment"
            )
            workflow["recommendations"].append(
                "  - Short-read: Use bwa-mem2 (if ≥64GB RAM available) or BWA (Burrows-Wheeler Aligner)"
            )
            workflow["recommendations"].append(
                "• Step 3: Convert aligned BAM to VCF using PyPGx create-input-vcf"
            )
            workflow["recommendations"].append(
                "• Step 4: PyPGx star allele calling and PharmCAT analysis"
            )
            workflow["recommendations"].append(
                "Consider using nf-core pipelines for comprehensive FASTQ processing."
            )

        # CRAM -> to be converted to BAM (lossy)
        elif analysis.file_type == FileType.CRAM:
            workflow["go_directly_to_pharmcat"] = False  # Production: always use full pipeline
            workflow["needs_gatk"] = True
            workflow["needs_pypgx"] = True
            workflow["recommendations"].append(
                "CRAM files will be converted to BAM using samtools:"
            )
            workflow["recommendations"].append(
                "• Command: samtools view -b -T <refgenome.fa> -o <output_file.bam> <input_file.cram>"
            )
            workflow["recommendations"].append(
                "• Note: CRAM files are smaller but require original reference FASTA for conversion"
            )
            workflow["recommendations"].append(
                "• Alternative: Use nf-core/bamtofastq pipeline for CRAM to FASTQ conversion"
            )
            workflow["recommendations"].append(
                "• See: https://pharmcat.clinpgx.org/using/Calling-HLA/"
            )

            # Check if index exists
            if not analysis.has_index:
                workflow["recommendations"].append(
                    "Creating index for CRAM file for faster processing"
                )

        # SAM -> to be converted to BAM
        elif analysis.file_type == FileType.SAM:
            workflow["go_directly_to_pharmcat"] = False  # Production: always use full pipeline
            workflow["needs_gatk"] = True
            workflow["needs_pypgx"] = True
            workflow["recommendations"].append(
                "SAM file will be converted to BAM using GATK or samtools:"
            )
            workflow["recommendations"].append(
                "• GATK: Picard SortSam and BuildBamIndex for quality control"
            )
            workflow["recommendations"].append(
                "• Alternative: samtools view -b -o output.bam input.sam"
            )

            # Check if index exists
            if not analysis.has_index:
                workflow["recommendations"].append(
                    "Creating index for SAM file for faster processing"
                )

        # BAM -> can enter pipeline directly, but OptiType will internally convert to FASTQ
        elif analysis.file_type == FileType.BAM:
            workflow["go_directly_to_pharmcat"] = False  # Production: always use full pipeline
            workflow["needs_hla"] = True
            workflow["needs_pypgx"] = True
            workflow["needs_pypgx_bam2vcf"] = True  # Use PyPGx create-input-vcf

            workflow["recommendations"].append(
                "BAM files will be processed with the complete pipeline:"
            )
            workflow["recommendations"].append(
                "• Step 1: OptiType/HLA typing - extracts HLA alleles from BAM (~100GB intermediate FASTQ)"
            )
            workflow["recommendations"].append(
                "• Step 2: PyPGx create-input-vcf - calls SNVs/indels for all target genes"
            )
            workflow["recommendations"].append(
                "• Step 3: PyPGx star allele calling for enhanced pharmacogene analysis"
            )
            workflow["recommendations"].append(
                "• Step 4: PharmCAT with outside calls including HLA data"
            )
            workflow["recommendations"].append(
                "• Result: Complete 23/23 highest clinical evidence pharmacogenes"
            )
            workflow["recommendations"].append(
                "• Reference: https://pharmcat.clinpgx.org/using/Calling-HLA/"
            )
            workflow["recommendations"].append(
                "• PyPGx docs: https://pypgx.readthedocs.io/en/latest/cli.html#run-ngs-pipeline"
            )

            # Check if index exists
            if not analysis.has_index:
                workflow["recommendations"].append(
                    "Creating index for BAM file for faster processing"
                )

        # VCF ("quick pipeline")
        elif analysis.file_type == FileType.VCF:
            workflow["go_directly_to_pharmcat"] = False
            workflow["needs_pypgx"] = True

            workflow["recommendations"].append(
                "VCF files use the quick pipeline:"
            )
            workflow["recommendations"].append(
                "• Skip OptiType (HLA) - no HLA outside calls available"
            )
            workflow["recommendations"].append(
                "• Run PyPGx for star allele calling on pharmacogenes"
            )
            workflow["recommendations"].append(
                "• Continue to PharmCAT with PyPGx outside calls"
            )
            workflow["recommendations"].append(
                "• Note: Original sequencing files (FASTQ/BAM) provide more complete and accurate results"
            )

            # Check reference genome compatibility
            if analysis.vcf_info:
                vcf_info = analysis.vcf_info
                reference = vcf_info.reference_genome.lower()

                # Normalize reference genome string for comparison
                is_hg38 = any(ref_id in reference for ref_id in ["hg38", "grch38", "38"])
                if is_hg38:
                    workflow["recommendations"].append(
                        f"✓ Compatible hg38/GRCh38 reference genome detected: {vcf_info.reference_genome}"
                    )
                elif reference != "unknown":
                    workflow["warnings"].append(
                        f"⚠️ File uses {vcf_info.reference_genome} reference genome. Only hg38/GRCh38 is fully supported."
                    )
                    workflow["is_provisional"] = True

                # Enhanced sequencing profile recommendations
                if vcf_info.sequencing_profile == SequencingProfile.WGS:
                    workflow["recommendations"].append(
                        "✓ Whole Genome Sequencing detected - full pharmacogene coverage available"
                    )
                elif vcf_info.sequencing_profile == SequencingProfile.WES:
                    workflow["recommendations"].append(
                        "✓ Whole Exome Sequencing detected - good pharmacogene coverage"
                    )
                else:
                    workflow["warnings"].append(
                        "⚠️ Targeted sequencing may have limited pharmacogene coverage"
                    )

            # Check if index exists
            if analysis.vcf_info and not analysis.vcf_info.has_index:
                workflow["recommendations"].append(
                    "Creating index for VCF file for faster processing"
                )

        # 23andMe files need conversion
        elif analysis.file_type == FileType.TWENTYTHREE_AND_ME:
            workflow["needs_conversion"] = True
            workflow["go_directly_to_pharmcat"] = True  # After conversion (exception to the rule)
            workflow["is_provisional"] = True
            workflow["unsupported"] = True
            workflow["unsupported_reason"] = (
                "23andMe data format requires conversion to VCF before analysis. "
                "This functionality is not yet implemented."
            )
            workflow["recommendations"].append(
                "23andMe format conversion needed - create schema reference and translation"
            )
            workflow["warnings"].append(
                "23andMe data has limited variant coverage compared to clinical sequencing. "
                "Results will be provisional and may miss important variants."
            )

        # FASTA - reference genome files
        elif analysis.file_type == FileType.FASTA:
            workflow["unsupported"] = True
            workflow["unsupported_reason"] = "FASTA files are reference genome files and cannot be analyzed directly."
            workflow["recommendations"].append(
                "FASTA files contain reference genome sequences:"
            )
            workflow["recommendations"].append(
                "• Use FASTA files as reference for alignment (BWA, minimap2, etc.)"
            )
            workflow["recommendations"].append(
                "• Convert FASTQ reads to BAM using this reference"
            )
            workflow["recommendations"].append(
                "• Then use the resulting BAM for pharmacogenomic analysis"
            )

        # GVCF - genomic VCF with reference calls
        elif analysis.file_type == FileType.GVCF:
            workflow["go_directly_to_pharmcat"] = False  # Production: always use full pipeline
            workflow["needs_pypgx"] = True
            workflow["recommendations"].append(
                "GVCF files (genomic VCF with reference calls):"
            )
            workflow["recommendations"].append(
                "• Will be processed through PyPGx and PharmCAT pipeline"
            )
            workflow["recommendations"].append(
                "• GVCFs contain both variant and reference calls"
            )
            workflow["recommendations"].append(
                "• May require conversion to standard VCF for some tools"
            )

        # BCF - binary VCF format
        elif analysis.file_type == FileType.BCF:
            workflow["go_directly_to_pharmcat"] = False  # Production: always use full pipeline
            workflow["needs_pypgx"] = True
            workflow["recommendations"].append(
                "BCF files (binary VCF format):"
            )
            workflow["recommendations"].append(
                "• Will be converted to VCF format if needed"
            )
            workflow["recommendations"].append(
                "• Use bcftools for conversion: bcftools view input.bcf > output.vcf"
            )
            workflow["recommendations"].append(
                "• Standard PyPGx + PharmCAT pipeline will be applied"
            )

        # BED - genome interval/annotation files
        elif analysis.file_type == FileType.BED:
            workflow["unsupported"] = True
            workflow["unsupported_reason"] = "BED files contain genomic intervals and cannot be analyzed for pharmacogenomics."
            workflow["recommendations"].append(
                "BED files contain genomic interval data:"
            )
            workflow["recommendations"].append(
                "• Used for defining target regions in sequencing"
            )
            workflow["recommendations"].append(
                "• Can be used with tools like bedtools for region-based analysis"
            )
            workflow["recommendations"].append(
                "• Not suitable for direct pharmacogenomic variant analysis"
            )


        # Unknown file type
        else:
            workflow["unsupported"] = True
            workflow["unsupported_reason"] = f"Unrecognized file format: {analysis.file_type.value}."
            workflow["recommendations"].append(
                "Supported formats: VCF, BAM, CRAM, SAM, FASTQ"
            )
            workflow["recommendations"].append(
                "Please upload a supported genomic file format for pharmacogenomic analysis."
            )

        return workflow

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
                        workflow["go_directly_to_pharmcat"] = False
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