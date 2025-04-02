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
    FASTQ = "fastq"
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

            # Get basic file info
            file_size = file_path.stat().st_size
            is_compressed = self._is_compressed(file_path)
            has_index = self._has_index_file(file_path)

            # Determine file type
            file_type = self._detect_file_type(file_path)
            
            # If it's a VCF, analyze the header
            vcf_info = None
            if file_type == FileType.VCF:
                vcf_info = await self._analyze_vcf_header(file_path)

            return FileAnalysis(
                file_type=file_type,
                is_compressed=is_compressed,
                has_index=has_index,
                vcf_info=vcf_info,
                file_size=file_size
            )

        except Exception as e:
            logger.error(f"Error analyzing file {file_path}: {str(e)}")
            return FileAnalysis(
                file_type=FileType.UNKNOWN,
                is_compressed=False,
                has_index=False,
                error=str(e)
            )

    def _is_compressed(self, file_path: Path) -> bool:
        """Check if file is compressed (zip, gzip, etc.)"""
        try:
            with open(file_path, 'rb') as f:
                header = f.read(2)
                return header.startswith(b'PK') or header.startswith(b'\x1f\x8b')
        except Exception:
            return False

    def _has_index_file(self, file_path: Path) -> bool:
        """Check if file has an associated index file"""
        index_extensions = ['.tbi', '.csi', '.bai', '.fai']
        for ext in index_extensions:
            if (file_path.parent / f"{file_path.stem}{ext}").exists():
                return True
        return False

    def _detect_file_type(self, file_path: Path) -> FileType:
        """Detect the type of genomic file"""
        # Check file extension
        ext = file_path.suffix.lower()
        if ext in ['.vcf', '.vcf.gz']:
            return FileType.VCF
        elif ext == '.bam':
            return FileType.BAM
        elif ext in ['.fastq', '.fq', '.fastq.gz', '.fq.gz']:
            return FileType.FASTQ
        elif ext in ['.txt', '.csv']:
            # Check if it's a 23andMe file
            try:
                with open(file_path, 'r') as f:
                    header = f.readline()
                    if '23andMe' in header:
                        return FileType.TWENTYTHREE_AND_ME
            except Exception:
                pass

        return FileType.UNKNOWN

    async def _analyze_vcf_header(self, file_path: Path) -> VCFHeaderInfo:
        """Analyze VCF header to extract important information"""
        try:
            # Use bcftools to read header
            cmd = ['bcftools', 'view', '-h', str(file_path)]
            result = subprocess.run(cmd, capture_output=True, text=True)
            header_lines = result.stdout.split('\n')

            # Initialize default values
            reference_genome = "unknown"
            sequencing_platform = "unknown"
            sequencing_profile = SequencingProfile.UNKNOWN
            contigs = []
            sample_count = 0

            # Parse header lines
            for line in header_lines:
                if line.startswith('##reference='):
                    reference_genome = line.split('=')[1].strip('"')
                elif line.startswith('##platform='):
                    sequencing_platform = line.split('=')[1].strip('"')
                elif line.startswith('##contig='):
                    contig_match = re.search(r'ID=([^,]+)', line)
                    if contig_match:
                        contigs.append(contig_match.group(1))
                elif line.startswith('#CHROM'):
                    # Count samples from header line
                    sample_count = len(line.split('\t')) - 9  # VCF standard has 9 fixed columns

            # Determine sequencing profile based on contigs
            if len(contigs) > 20:  # WGS typically has all chromosomes
                sequencing_profile = SequencingProfile.WGS
            elif len(contigs) > 0 and all('chr' in c.lower() for c in contigs):
                sequencing_profile = SequencingProfile.WES
            else:
                sequencing_profile = SequencingProfile.TARGETED

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
        """
        workflow = {
            "needs_gatk": False,
            "needs_stargazer": False,
            "needs_conversion": False,
            "is_provisional": False,
            "recommendations": [],
            "warnings": []
        }

        if analysis.file_type == FileType.VCF and analysis.vcf_info:
            vcf_info = analysis.vcf_info
            
            # Check if we need Stargazer for CYP2D6
            if vcf_info.sequencing_profile == SequencingProfile.WGS:
                workflow["needs_stargazer"] = True
                workflow["recommendations"].append(
                    "Using Stargazer for CYP2D6 analysis due to WGS data"
                )
            else:
                workflow["warnings"].append(
                    "Limited CYP2D6 analysis due to non-WGS data"
                )

            # Check if we need GATK
            if not vcf_info.has_index:
                workflow["needs_gatk"] = True
                workflow["recommendations"].append(
                    "Indexing VCF file for better analysis"
                )

        elif analysis.file_type == FileType.TWENTYTHREE_AND_ME:
            workflow["needs_conversion"] = True
            workflow["is_provisional"] = True
            workflow["warnings"].append(
                "Results will be provisional due to limited variant coverage"
            )

        return workflow

    async def process_upload(self, file_path: str, original_wgs: Optional[str] = None) -> Dict:
        """
        Process an uploaded file and determine the appropriate workflow.
        """
        try:
            # Analyze the uploaded file
            analysis = await self.analyze_file(file_path)
            
            # Determine workflow
            workflow = self.determine_workflow(analysis)

            return {
                "file_analysis": analysis,
                "workflow": workflow,
                "status": "success"
            }

        except Exception as e:
            logger.error(f"Error processing upload: {str(e)}")
            return {
                "status": "error",
                "error": str(e)
            } 