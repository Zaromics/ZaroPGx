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

# Configure logging
logger = logging.getLogger(__name__)

class FileType(Enum):
    VCF = "vcf"
    BAM = "bam"
    CRAM = "cram"
    SAM = "sam"
    FASTQ = "fastq"
    FASTA = "fasta"
    TWENTYTHREE_AND_ME = "23andme"
    UNKNOWN = "unknown"

class SequencingProfile(Enum):
    WGS = "whole_genome_sequencing"
    WES = "whole_exome_sequencing"
    TARGETED = "targeted_sequencing"
    UNKNOWN = "unknown"

@dataclass
class VCFHeaderInfo:
    reference_genome: str
    sequencing_platform: str
    sequencing_profile: SequencingProfile
    has_index: bool
    is_bgzipped: bool
    contigs: List[str]
    sample_count: int
    variant_count: Optional[int] = None

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
            
            # If it's a VCF, analyze the header
            vcf_info = None
            if file_type == FileType.VCF:
                logger.info("Analyzing VCF header...")
                vcf_info = await self._analyze_vcf_header(file_path)
                logger.info(f"VCF info - Reference genome: {vcf_info.reference_genome}, "
                          f"Sequencing profile: {vcf_info.sequencing_profile.value}, "
                          f"Sample count: {vcf_info.sample_count}")
                
                # Make sure the bgzipped status is correctly reflected
                vcf_info.is_bgzipped = is_compressed or vcf_info.is_bgzipped or str(file_path).endswith('.gz')

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
        - FASTA (.fasta ??? ) (to add)
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
            # Check for FASTQ format
            elif prev_ext in ['.fastq', '.fq']:
                logger.info("Identified as compressed FASTQ file")
                return FileType.FASTQ
            # Handle vcf.gz without dot notation
            elif "vcf" in str(file_path).lower():
                logger.info("Identified as compressed VCF file (from filename)")
                return FileType.VCF
        
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
        
        # If we have a .gz file but couldn't determine type from suffix, try filename patterns
        if ext == '.gz':
            filename = file_path.name.lower()
            if 'vcf' in filename:
                logger.info("Identified as gzipped VCF file (from filename pattern)")
                return FileType.VCF
            elif any(pattern in filename for pattern in ['fastq', 'fq']):
                logger.info("Identified as gzipped FASTQ file (from filename pattern)")
                return FileType.FASTQ

        # If extension doesn't match, try to determine from content
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

        logger.warning(f"Could not determine file type for {file_path}")
        return FileType.UNKNOWN

    async def _analyze_vcf_header(self, file_path: Path) -> VCFHeaderInfo:
        """Analyze VCF header to extract important information"""
        try:
            # Use bcftools to read header
            cmd = ['bcftools', 'view', '-h', str(file_path)]
            result = subprocess.run(cmd, capture_output=True, text=True)
            header_lines = result.stdout.split('\n')
            
            logger.info(f"Analyzing VCF header for {file_path}")
            logger.info(f"Header contains {len(header_lines)} lines")

            # Initialize default values
            reference_genome = "unknown"
            sequencing_platform = "unknown"
            sequencing_profile = SequencingProfile.UNKNOWN
            contigs = []
            sample_count = 0
            
            # Track possible reference genome hints 
            ref_hints = []

            # Parse header lines
            for line in header_lines:
                # Look for explicit reference field
                if line.startswith('##reference='):
                    reference_path = line.split('=')[1].strip('"').strip("'")
                    reference_genome = reference_path
                    logger.info(f"Found reference in header: {reference_genome}")
                    ref_hints.append(reference_path)
                
                # Extract reference from contig lines
                elif line.startswith('##contig='):
                    contig_match = re.search(r'ID=([^,]+)', line)
                    if contig_match:
                        contigs.append(contig_match.group(1))
                    
                    # Check for reference hints in contig lines
                    for ref_pattern in ["hg38", "GRCh38", "grch38", "hg19", "GRCh37", "grch37"]:
                        if ref_pattern.lower() in line.lower():
                            ref_hints.append(ref_pattern)
                
                # Look for platform information
                elif line.startswith('##platform='):
                    sequencing_platform = line.split('=')[1].strip('"')
                
                # Count samples from header line
                elif line.startswith('#CHROM'):
                    sample_count = len(line.split('\t')) - 9  # VCF standard has 9 fixed columns
                
                # Look for other reference hints
                elif "hg38" in line.lower() or "grch38" in line.lower():
                    ref_hints.append("hg38")
                elif "hg19" in line.lower() or "grch37" in line.lower():
                    ref_hints.append("hg19")

            # Try to detect reference genome if not explicitly stated
            if reference_genome == "unknown" and ref_hints:
                logger.info(f"Reference hints found: {ref_hints}")
                
                # Prioritize hg38/GRCh38 references
                for hint in ref_hints:
                    hint_lower = hint.lower()
                    if "hg38" in hint_lower or "grch38" in hint_lower:
                        reference_genome = "hg38"
                        logger.info(f"Detected reference genome as hg38/GRCh38 from hints")
                        break
                    elif "hg19" in hint_lower or "grch37" in hint_lower:
                        reference_genome = "hg19"
                        logger.info(f"Detected reference genome as hg19/GRCh37 from hints")
                        break
            
            # Final reference genome detection
            if reference_genome == "unknown":
                logger.warning(f"Could not determine reference genome for {file_path}")
            else:
                logger.info(f"Reference genome determined as: {reference_genome}")

            # Determine sequencing profile based on contigs
            if len(contigs) > 20:  # WGS typically has all chromosomes
                sequencing_profile = SequencingProfile.WGS
                logger.info(f"Detected as whole genome sequencing (WGS) based on {len(contigs)} contigs")
            elif len(contigs) > 0 and any('chr' in c.lower() for c in contigs):
                sequencing_profile = SequencingProfile.WES
                logger.info(f"Detected as whole exome sequencing (WES) based on contig patterns")
            else:
                sequencing_profile = SequencingProfile.TARGETED
                logger.info(f"Detected as targeted sequencing based on limited contigs")

            return VCFHeaderInfo(
                reference_genome=reference_genome,
                sequencing_platform=sequencing_platform,
                sequencing_profile=sequencing_profile,
                has_index=self._has_index_file(file_path),
                is_bgzipped=str(file_path).endswith('.gz'),
                contigs=contigs,
                sample_count=sample_count
            )

        except Exception as e:
            logger.error(f"Error analyzing VCF header: {str(e)}")
            raise

    def determine_workflow(self, analysis: FileAnalysis) -> Dict:
        """
        Determine the appropriate workflow based on file analysis.
        
        This method implements the following logic flow:
        - Only hg38 reference genome is fully supported
        - VCF files can go directly to PharmCAT, but users are advised to upload original files if available
        - BAM/CRAM/SAM files go through the GATK pipeline
        - FASTQ files need alignment (not yet implemented)
        - 23andMe files need conversion to VCF (not yet implemented)
        
        Returns a dictionary with workflow configuration and recommendations.
        """
        workflow = {
            "needs_gatk": False,
            "needs_alignment": False,
            "needs_pypgx": False,
            "needs_conversion": False,
            "is_provisional": False,
            "go_directly_to_pharmcat": False,
            "recommendations": [],
            "warnings": [],
            "unsupported": False,
            "unsupported_reason": None
        }
        
        # Check reference genome for VCF files
        if analysis.file_type == FileType.VCF and analysis.vcf_info:
            vcf_info = analysis.vcf_info
            reference = vcf_info.reference_genome.lower()
            
            # Normalize reference genome string for comparison
            is_hg38 = any(ref_id in reference for ref_id in ["hg38", "grch38", "38"])
            if is_hg38:
                logger.info(f"Detected compatible hg38/GRCh38 reference genome: {reference}")
            
            # Check if we have a non-hg38 reference
            if reference != "unknown" and not is_hg38:
                workflow["warnings"].append(
                    f"File uses {vcf_info.reference_genome} reference genome. Currently, only hg38/GRCh38 is fully supported."
                )
                # Don't mark as unsupported yet, but flag it
                workflow["is_provisional"] = True
                logger.warning(f"Non-hg38 reference detected: {reference}")
            
            # VCF files can go directly to PharmCAT, but recommend original files if available
            workflow["go_directly_to_pharmcat"] = True
            workflow["recommendations"].append(
                "Your VCF file can be processed directly, but if you have the original sequencing file "
                "(BAM/CRAM), uploading that instead would provide more accurate variant calling with our "
                "latest GATK pipeline."
            )
            
            # For WGS data, use PyPGx for star alleles
            if vcf_info.sequencing_profile == SequencingProfile.WGS:
                workflow["needs_pypgx"] = True
                workflow["recommendations"].append(
                    "Using PyPGx for enhanced analysis thanks to whole genome sequencing data"
                )
            else:
                workflow["warnings"].append(
                    "Limited analysis may be available due to non-whole genome sequencing data for genes such as CYP2D6"
                )

            # Create index if needed
            if not vcf_info.has_index:
                workflow["recommendations"].append(
                    "Creating index for VCF file for faster processing"
                )
        
        # BAM uses PyPGx create-input-vcf (recommended by PyPGx docs). CRAM/SAM stay on GATK.
        elif analysis.file_type in [FileType.BAM, FileType.CRAM, FileType.SAM]:
            workflow["needs_pypgx"] = True  # We still run PyPGx later for star alleles

            if analysis.file_type == FileType.BAM:
                # Use PyPGx to create input VCF from BAM
                workflow["needs_gatk"] = False
                workflow["needs_pypgx_bam2vcf"] = True
                workflow["recommendations"].append(
                    "BAM file will be converted to VCF using PyPGx create-input-vcf (recommended)."
                )
            elif analysis.file_type == FileType.CRAM:
                # Keep CRAM on GATK path
                workflow["needs_gatk"] = True
                workflow["needs_pypgx_bam2vcf"] = False
                workflow["recommendations"].append(
                    "CRAM file will be processed through GATK for variant calling."
                )
            elif analysis.file_type == FileType.SAM:
                # Keep SAM on GATK path
                workflow["needs_gatk"] = True
                workflow["needs_pypgx_bam2vcf"] = False
                workflow["recommendations"].append(
                    "SAM file will be processed through GATK for variant calling."
                )
                
            # Check if index exists, if not we'll need to create one
            if not analysis.has_index:
                workflow["recommendations"].append(
                    f"Creating index for {analysis.file_type.value.upper()} file for faster processing"
                )
        
        # FASTQ files need alignment first
        elif analysis.file_type == FileType.FASTQ:
            workflow["needs_alignment"] = True
            workflow["needs_gatk"] = True
            workflow["needs_pypgx"] = True
            workflow["unsupported"] = True
            workflow["unsupported_reason"] = (
                "FASTQ files require alignment to a reference genome before variant calling. "
                "This functionality is not yet implemented."
            )
            workflow["recommendations"].append(
                "Consider aligning your FASTQ data to hg38 reference genome using an aligner like BWA-MEM "
                "and uploading the resulting BAM file."
            )
        
        # 23andMe files need conversion
        elif analysis.file_type == FileType.TWENTYTHREE_AND_ME:
            workflow["needs_conversion"] = True
            workflow["go_directly_to_pharmcat"] = True  # After conversion
            workflow["is_provisional"] = True
            workflow["unsupported"] = True
            workflow["unsupported_reason"] = (
                "23andMe data format requires conversion to VCF before analysis. "
                "This functionality is not yet implemented."
            )
            workflow["warnings"].append(
                "23andMe data has limited variant coverage compared to clinical sequencing. "
                "Results will be provisional and may miss important variants."
            )
        
        # Unknown file type
        else:
            workflow["unsupported"] = True
            workflow["unsupported_reason"] = f"Unrecognized file format: {analysis.file_type.value}."
            workflow["recommendations"].append(
                "Please upload a VCF, BAM, CRAM, or SAM file for pharmacogenomic analysis."
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
            logger.info("Starting file analysis...")
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