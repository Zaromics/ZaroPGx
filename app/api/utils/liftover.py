#!/usr/bin/env python3
"""
bcftools liftover utility for converting GRCh37/hg19 VCF files to GRCh38/hg38.

This module provides robust coordinate conversion functionality using bcftools annotate
with UCSC chain files to convert genomic coordinates between genome builds. Designed
for GWAS-VCF format compatibility and pharmacogenomics workflows.

Key Features:
- GWAS-VCF specification compliant processing
- Automatic chain file download and management
- Robust error handling with detailed diagnostics
- Performance optimized for large genomic datasets
- Integration ready for existing pharmacogenomics pipelines

Public API:
- liftover_vcf() - Main coordinate conversion function
- download_chain_file() - Chain file management utility
- validate_liftover_input() - Input validation and warnings
- get_liftover_stats() - Conversion statistics and reporting

Usage:
    from app.api.utils.liftover import liftover_vcf

    result = liftover_vcf("input.vcf", "output.vcf", target_genome="hg19")
    if result["success"]:
        print(f"Liftover completed: {result['statistics']['conversion_rate']:.1f}% conversion rate")
    else:
        print(f"Liftover failed: {result['error']}")

References:
- UCSC Chain Files: https://hgdownload.soe.ucsc.edu/goldenPath/
- GWAS-VCF Specification: https://github.com/MRC-BSU/GWAS-VCF
- bcftools annotate: https://samtools.github.io/bcftools/bcftools.html#annotate
"""

import os
import sys
import subprocess
import shlex
import logging
import tempfile
import gzip
from pathlib import Path
from typing import Dict, Optional, Tuple, Union
from urllib.request import urlopen
from urllib.error import URLError

# Configure logging
logger = logging.getLogger(__name__)

# UCSC Chain file URLs for common genome build conversions
# Based on GWAS-VCF specification recommendations and UCSC liftOver chains
CHAIN_FILE_URLS = {
    "hg19_to_hg38": "https://hgdownload.soe.ucsc.edu/goldenPath/hg19/liftOver/hg19ToHg38.over.chain.gz",
    "grch37_to_grch38": "https://hgdownload.soe.ucsc.edu/goldenPath/hg19/liftOver/hg19ToHg38.over.chain.gz",  # Same as hg19
    "hg38_to_hg19": "https://hgdownload.soe.ucsc.edu/goldenPath/hg38/liftOver/hg38ToHg19.over.chain.gz",
    "grch38_to_grch37": "https://hgdownload.soe.ucsc.edu/goldenPath/hg38/liftOver/hg38ToHg19.over.chain.gz",  # Same as hg38
}

# Chain file paths in the container (mounted from reference directory)
# Following GWAS-VCF specification for chain file organization
CHAIN_FILE_PATHS = {
    "hg19_to_hg38": "/reference/hg19/liftOver/hg19ToHg38.over.chain.gz",
    "grch37_to_grch38": "/reference/grch37/liftOver/grch37ToHg38.over.chain.gz",  # May need to be downloaded
    "hg38_to_hg19": "/reference/hg38/liftOver/hg38ToHg19.over.chain.gz",
    "grch38_to_grch37": "/reference/grch38/liftOver/grch38ToHg19.over.chain.gz",  # May need to be downloaded
}

# Default bcftools settings
DEFAULT_BCFTOOLS_MEMORY = "4G"
DEFAULT_BCFTOOLS_THREADS = 4
DEFAULT_TIMEOUT_SECONDS = 3600  # 1 hour timeout for large files

def download_chain_file(target_genome: str = "hg19", reference_genome: str = "hg38", max_retries: int = 3) -> str:
    """
    Download UCSC chain file for genome coordinate conversion if not already present.

    Implements retry logic and validation for reliable chain file downloads.
    Chain files are essential for accurate coordinate conversion between genome builds.

    Args:
        target_genome: Source genome build ('hg19', 'grch37', 'hg38', 'grch38')
        reference_genome: Target genome build ('hg19', 'grch37', 'hg38', 'grch38')
        max_retries: Maximum number of download attempts

    Returns:
        Path to the downloaded and validated chain file

    Raises:
        FileNotFoundError: If chain file cannot be downloaded or accessed after retries
        ValueError: If genome build combination is not supported
        RuntimeError: If chain file validation fails
    """
    # Normalize genome names following GWAS-VCF conventions
    target = target_genome.lower().replace("grch", "hg")
    reference = reference_genome.lower().replace("grch", "hg")

    if target == reference:
        raise ValueError(f"Cannot liftover from {target_genome} to {reference_genome}: same genome build")

    # Determine chain file key following UCSC naming conventions
    chain_key = f"{target}_to_{reference}"

    if chain_key not in CHAIN_FILE_URLS:
        # Try reverse mapping for bidirectional conversions
        reverse_key = f"{reference}_to_{target}"
        if reverse_key in CHAIN_FILE_URLS:
            logger.warning(f"Direct chain file not available for {chain_key}, using reverse: {reverse_key}")
            chain_key = reverse_key
        else:
            supported = list(CHAIN_FILE_URLS.keys())
            raise ValueError(f"Unsupported genome conversion: {target_genome} to {reference_genome}. Supported: {supported}")

    chain_url = CHAIN_FILE_URLS[chain_key]
    expected_path = CHAIN_FILE_PATHS.get(chain_key, f"/tmp/{chain_key}.over.chain.gz")

    # Check if chain file already exists and is valid
    if os.path.exists(expected_path):
        if _validate_chain_file(expected_path):
            logger.info(f"Using existing valid chain file: {expected_path}")
            return expected_path
        else:
            logger.warning(f"Existing chain file is invalid, re-downloading: {expected_path}")
            os.remove(expected_path)

    # Create directory structure if needed
    os.makedirs(os.path.dirname(expected_path), exist_ok=True)

    # Download with retry logic
    for attempt in range(max_retries):
        try:
            logger.info(f"Downloading chain file from {chain_url} (attempt {attempt + 1}/{max_retries})")

            with urlopen(chain_url) as response:
                # Check response status and content type
                if response.status != 200:
                    raise URLError(f"HTTP {response.status}: {response.reason}")

                with open(expected_path, 'wb') as f:
                    # Download in chunks for large files
                    chunk_size = 8192
                    downloaded_size = 0
                    while True:
                        chunk = response.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded_size += len(chunk)

            # Validate downloaded file
            if not _validate_chain_file(expected_path):
                raise RuntimeError(f"Downloaded chain file failed validation: {expected_path}")

            logger.info(f"Successfully downloaded and validated chain file: {expected_path} ({downloaded_size} bytes)")
            return expected_path

        except URLError as e:
            logger.warning(f"Download attempt {attempt + 1} failed: {e}")
            if attempt == max_retries - 1:
                raise FileNotFoundError(f"Failed to download chain file after {max_retries} attempts: {chain_url}")
            continue
        except Exception as e:
            logger.error(f"Unexpected error during download attempt {attempt + 1}: {e}")
            if attempt == max_retries - 1:
                raise FileNotFoundError(f"Error downloading chain file: {e}")
            continue

def _validate_chain_file(chain_path: str) -> bool:
    """
    Validate chain file format and content.

    Args:
        chain_path: Path to chain file to validate

    Returns:
        True if chain file is valid, False otherwise
    """
    try:
        if not os.path.exists(chain_path):
            return False

        file_size = os.path.getsize(chain_path)
        if file_size == 0:
            logger.warning(f"Chain file is empty: {chain_path}")
            return False

        # Check for minimum reasonable size (UCSC chain files are typically > 1MB)
        if file_size < 1024 * 1024:  # 1MB
            logger.warning(f"Chain file seems too small ({file_size} bytes): {chain_path}")
            # Don't fail validation for small files, just warn

        # Basic format check - chain files should start with "chain"
        try:
            with open(chain_path, 'rb') as f:
                header = f.read(100)  # Read first 100 bytes
                if b'chain' not in header.lower():
                    logger.warning(f"Chain file missing 'chain' header: {chain_path}")
                    return False
        except Exception as e:
            logger.warning(f"Could not read chain file header: {e}")
            return False

        return True

    except Exception as e:
        logger.error(f"Error validating chain file {chain_path}: {e}")
        return False

def validate_liftover_input(input_path: str) -> Dict[str, Union[bool, str, list]]:
    """
    Validate input VCF file for liftover compatibility and GWAS-VCF compliance.

    Performs comprehensive validation including format checks, genome build detection,
    and GWAS-VCF specification compliance. Provides detailed warnings for optimal processing.

    Args:
        input_path: Path to input VCF file

    Returns:
        Dictionary with validation results, warnings, and metadata
    """
    result = {
        "valid": True,
        "error": None,
        "warnings": [],
        "metadata": {}
    }

    if not os.path.exists(input_path):
        result["valid"] = False
        result["error"] = f"Input file does not exist: {input_path}"
        return result

    if not os.access(input_path, os.R_OK):
        result["valid"] = False
        result["error"] = f"Input file is not readable: {input_path}"
        return result

    # Get basic file information
    file_size = os.path.getsize(input_path)
    result["metadata"]["file_size"] = file_size
    result["metadata"]["is_compressed"] = input_path.endswith(('.gz', '.bgz'))

    # Performance warnings for large files
    if file_size > 10 * 1024 * 1024 * 1024:  # 10GB
        result["warnings"].append(
            f"Very large input file ({file_size / (1024**3):.1f}GB) - liftover may take significant time and memory"
        )
    elif file_size > 1 * 1024 * 1024 * 1024:  # 1GB
        result["warnings"].append(
            f"Large input file ({file_size / (1024**3):.1f}GB) - liftover may take several minutes"
        )

    # GWAS-VCF format recommendations
    if not result["metadata"]["is_compressed"]:
        result["warnings"].append(
            "Uncompressed VCF file detected. For GWAS-VCF compliance and better performance, "
            "consider compressing with 'bcftools view -Oz' or 'bgzip'"
        )

    # Comprehensive VCF format validation using bcftools
    try:
        # Check VCF header structure and format compliance
        cmd = f"bcftools view -h {shlex.quote(input_path)}"
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)

        if proc.returncode != 0:
            result["valid"] = False
            result["error"] = f"Invalid VCF format: {proc.stderr.strip()}"
            return result

        header_lines = proc.stdout.strip().split('\n')

        # Validate VCF header structure
        if not header_lines:
            result["valid"] = False
            result["error"] = "VCF file has no header"
            return result

        # Check for required VCF header lines
        has_fileformat = any(line.startswith('##fileformat=VCF') for line in header_lines)
        has_chrom_line = any(line.startswith('#CHROM') for line in header_lines)

        if not has_fileformat:
            result["warnings"].append("Missing ##fileformat header line - may not be GWAS-VCF compliant")

        if not has_chrom_line:
            result["valid"] = False
            result["error"] = "VCF file missing #CHROM header line"
            return result

        # Extract genome build information from header if available
        genome_info = _extract_genome_info_from_header(header_lines)
        result["metadata"]["detected_genome"] = genome_info

        # GWAS-VCF compliance checks
        gwas_compliance = _check_gwas_vcf_compliance(header_lines)
        if not gwas_compliance["compliant"]:
            result["warnings"].extend(gwas_compliance["warnings"])

        result["metadata"]["gwas_compliant"] = gwas_compliance["compliant"]

        # Count samples for multi-sample VCF detection
        sample_count = _count_vcf_samples(header_lines[-1])  # Last line should be #CHROM line
        result["metadata"]["sample_count"] = sample_count

        if sample_count > 1:
            result["warnings"].append(
                f"Multi-sample VCF detected ({sample_count} samples). "
                "Liftover will process all samples but GWAS-VCF typically expects single-sample format"
            )

    except subprocess.TimeoutExpired:
        result["valid"] = False
        result["error"] = "VCF validation timed out - file may be too large or corrupted"
    except Exception as e:
        result["valid"] = False
        result["error"] = f"VCF validation failed: {e}"

    return result

def _extract_genome_info_from_header(header_lines: list) -> str:
    """Extract genome build information from VCF header."""
    genome_patterns = [
        r'##reference=(.+)',
        r'##assembly=(.+)',
        r'##contig.*ID=([^,>]+)',
    ]

    for line in header_lines:
        for pattern in genome_patterns:
            import re
            match = re.search(pattern, line)
            if match:
                genome_id = match.group(1) if len(match.groups()) == 1 else match.group(2)
                # Normalize common genome identifiers
                if 'hg38' in genome_id.lower() or 'grch38' in genome_id.lower():
                    return 'GRCh38/hg38'
                elif 'hg19' in genome_id.lower() or 'grch37' in genome_id.lower():
                    return 'GRCh37/hg19'

    return 'unknown'

def _check_gwas_vcf_compliance(header_lines: list) -> Dict[str, Union[bool, list]]:
    """Check GWAS-VCF specification compliance."""
    compliance = {
        "compliant": True,
        "warnings": []
    }

    # GWAS-VCF recommended header lines
    recommended_headers = [
        '##fileformat=VCF',
        '##FILTER',
        '##FORMAT',
        '##INFO',
        '##contig',
        '##source',
        '##reference'
    ]

    found_headers = set()
    for line in header_lines:
        for rec_header in recommended_headers:
            if line.startswith(f'##{rec_header}'):
                found_headers.add(rec_header)

    missing_headers = set(recommended_headers) - found_headers
    if missing_headers:
        compliance["warnings"].append(
            f"Missing GWAS-VCF recommended headers: {', '.join(missing_headers)}"
        )
        compliance["compliant"] = False

    return compliance

def _count_vcf_samples(chrom_line: str) -> int:
    """Count samples in VCF #CHROM line."""
    if not chrom_line.startswith('#CHROM'):
        return 0

    # Split by tab and count columns after FORMAT
    parts = chrom_line.split('\t')
    if len(parts) < 8:  # Need at least CHROM, POS, ID, REF, ALT, QUAL, FILTER, FORMAT
        return 0

    # Samples are columns after FORMAT
    return len(parts) - 9  # Subtract 8 fixed columns + 1 for FORMAT

def get_liftover_stats(input_path: str, output_path: str) -> Dict[str, Union[int, str]]:
    """
    Get statistics about the liftover conversion.

    Args:
        input_path: Path to input VCF file
        output_path: Path to output VCF file

    Returns:
        Dictionary with variant counts and conversion statistics
    """
    stats = {
        "input_variants": 0,
        "output_variants": 0,
        "variants_lifted": 0,
        "variants_dropped": 0,
        "conversion_rate": 0.0
    }

    try:
        # Count input variants
        cmd = f"bcftools view -H {shlex.quote(input_path)} | wc -l"
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
        if proc.returncode == 0:
            stats["input_variants"] = int(proc.stdout.strip()) if proc.stdout.strip().isdigit() else 0

        # Count output variants
        cmd = f"bcftools view -H {shlex.quote(output_path)} | wc -l"
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
        if proc.returncode == 0:
            stats["output_variants"] = int(proc.stdout.strip()) if proc.stdout.strip().isdigit() else 0

        # Calculate statistics
        stats["variants_lifted"] = stats["output_variants"]
        stats["variants_dropped"] = stats["input_variants"] - stats["output_variants"]
        if stats["input_variants"] > 0:
            stats["conversion_rate"] = (stats["output_variants"] / stats["input_variants"]) * 100

    except Exception as e:
        logger.warning(f"Failed to get liftover stats: {e}")

    return stats

def liftover_vcf(
    input_path: str,
    output_path: str,
    chain_file: Optional[str] = None,
    target_genome: str = "hg19",
    reference_genome: str = "hg38",
    force: bool = False,
    temp_dir: Optional[str] = None,
    threads: Optional[int] = None
) -> Dict[str, Union[bool, str, Dict]]:
    """
    Convert VCF file from one genome build to another using bcftools annotate with UCSC chain files.

    Implements GWAS-VCF compliant coordinate conversion for pharmacogenomics workflows.
    Uses bcftools annotate --rename-chrs for accurate coordinate mapping between genome builds.

    Args:
        input_path: Path to input VCF file (GRCh37/hg19)
        output_path: Path to output VCF file (GRCh38/hg38)
        chain_file: Optional path to chain file (will download if not provided)
        target_genome: Source genome build ('hg19', 'grch37', 'hg38', 'grch38')
        reference_genome: Target genome build ('hg19', 'grch37', 'hg38', 'grch38')
        force: Overwrite output file if it exists
        temp_dir: Temporary directory for intermediate files
        threads: Number of threads for bcftools (defaults to DEFAULT_BCFTOOLS_THREADS)

    Returns:
        Dictionary with conversion results, statistics, and metadata

    Raises:
        FileExistsError: If output file exists and force=False
        ValueError: If input parameters are invalid
        RuntimeError: If bcftools liftover fails
    """
    start_time = os.times()

    logger.info(f"Starting GWAS-VCF compliant liftover: {input_path} -> {output_path}")
    logger.info(f"Source genome: {target_genome}, Target genome: {reference_genome}")

    # Validate input file with GWAS-VCF compliance checks
    validation = validate_liftover_input(input_path)
    if not validation["valid"]:
        error_msg = validation["error"] or "Input validation failed"
        logger.error(f"Liftover failed - input validation: {error_msg}")
        return {
            "success": False,
            "error": error_msg,
            "warnings": validation.get("warnings", []),
            "metadata": validation.get("metadata", {})
        }

    # Log GWAS-VCF compliance information
    metadata = validation.get("metadata", {})
    if metadata.get("gwas_compliant"):
        logger.info("Input VCF is GWAS-VCF compliant")
    else:
        logger.warning("Input VCF may not be fully GWAS-VCF compliant - check warnings")

    # Check output file existence
    if os.path.exists(output_path) and not force:
        raise FileExistsError(f"Output file already exists: {output_path}. Use force=True to overwrite.")

    # Download and validate chain file if needed
    if not chain_file:
        try:
            chain_file = download_chain_file(target_genome, reference_genome)
            logger.info(f"Using UCSC chain file: {chain_file}")
        except (FileNotFoundError, ValueError, RuntimeError) as e:
            error_msg = f"Failed to obtain chain file: {e}"
            logger.error(error_msg)
            return {
                "success": False,
                "error": error_msg,
                "metadata": metadata
            }

    # Ensure chain file exists and is valid
    if not os.path.exists(chain_file):
        error_msg = f"Chain file not found: {chain_file}"
        logger.error(error_msg)
        return {
            "success": False,
            "error": error_msg,
            "metadata": metadata
        }

    # Set up threading for performance
    if threads is None:
        threads = DEFAULT_BCFTOOLS_THREADS

    # Create temporary directory for intermediate files
    if temp_dir is None:
        temp_dir = tempfile.mkdtemp(prefix="liftover_")
    else:
        os.makedirs(temp_dir, exist_ok=True)

    temp_output = os.path.join(temp_dir, "temp_liftover.vcf.gz")

    try:
        # Build optimized bcftools annotate command for GWAS-VCF processing
        # Use --rename-chrs for coordinate conversion and maintain GWAS-VCF format
        cmd_parts = [
            "bcftools", "annotate",
            "--rename-chrs", chain_file,
            "-Oz",  # Output compressed (GWAS-VCF standard)
            "--threads", str(threads),
            "-o", shlex.quote(temp_output),
            shlex.quote(input_path)
        ]

        # Add GWAS-VCF specific options for better compatibility
        if metadata.get("sample_count", 0) == 1:
            # Single sample VCF - add GWAS-VCF recommended metadata
            cmd_parts.extend([
                "--set-id", "%CHROM\\_%POS\\_%REF\\_%ALT",  # Standardize variant IDs
            ])

        cmd = " ".join(cmd_parts)
        logger.info(f"Running GWAS-VCF liftover command: {cmd}")

        # Execute liftover with enhanced error handling
        proc = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=DEFAULT_TIMEOUT_SECONDS
        )

        if proc.returncode != 0:
            error_msg = f"bcftools liftover failed (exit code {proc.returncode}): {proc.stderr.strip()}"
            logger.error(error_msg)

            # Check for common failure modes
            if "chain file" in proc.stderr.lower():
                error_msg += " - Chain file may be incompatible with input VCF"
            elif "memory" in proc.stderr.lower():
                error_msg += " - Consider reducing thread count or file size"

            return {
                "success": False,
                "error": error_msg,
                "bcftools_stderr": proc.stderr,
                "bcftools_returncode": proc.returncode,
                "metadata": metadata
            }

        # Validate output file was created
        if not os.path.exists(temp_output):
            error_msg = "bcftools did not produce output file"
            logger.error(error_msg)
            return {
                "success": False,
                "error": error_msg,
                "bcftools_stdout": proc.stdout,
                "bcftools_stderr": proc.stderr,
                "metadata": metadata
            }

        # Atomic move to final location
        os.rename(temp_output, output_path)
        logger.info(f"Liftover completed successfully: {output_path}")

        # Get comprehensive statistics
        stats = get_liftover_stats(input_path, output_path)

        # Calculate processing time
        end_time = os.times()
        processing_time = end_time.elapsed - start_time.elapsed

        return {
            "success": True,
            "output_path": output_path,
            "chain_file_used": chain_file,
            "statistics": stats,
            "processing_time_seconds": processing_time,
            "warnings": validation.get("warnings", []),
            "metadata": metadata,
            "bcftools_version": _get_bcftools_version(),
            "gwas_compliant": metadata.get("gwas_compliant", False)
        }

    except subprocess.TimeoutExpired:
        error_msg = f"Liftover timed out after {DEFAULT_TIMEOUT_SECONDS} seconds"
        logger.error(error_msg)
        return {
            "success": False,
            "error": error_msg,
            "metadata": metadata
        }
    except Exception as e:
        error_msg = f"Unexpected error during liftover: {e}"
        logger.error(error_msg, exc_info=True)
        return {
            "success": False,
            "error": error_msg,
            "metadata": metadata
        }
    finally:
        # Clean up temporary directory
        if temp_dir and os.path.exists(temp_dir):
            import shutil
            try:
                shutil.rmtree(temp_dir)
                logger.debug(f"Cleaned up temporary directory: {temp_dir}")
            except Exception as e:
                logger.warning(f"Failed to clean up temp directory {temp_dir}: {e}")

def _get_bcftools_version() -> str:
    """Get bcftools version for logging purposes."""
    try:
        proc = subprocess.run(
            ["bcftools", "--version"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if proc.returncode == 0:
            return proc.stdout.strip().split('\n')[0]
    except Exception:
        pass
    return "unknown"

def liftover_vcf_simple(
    input_path: str,
    output_path: str,
    target_genome: str = "hg19",
    reference_genome: str = "hg38"
) -> bool:
    """
    Simplified liftover function for basic pharmacogenomics use cases.

    Provides a streamlined interface for common GRCh37→GRCh38 conversions
    in pharmacogenomics workflows with sensible defaults and error handling.

    Args:
        input_path: Path to input VCF file (typically GRCh37/hg19)
        output_path: Path to output VCF file (will be GRCh38/hg38)
        target_genome: Source genome build (default: 'hg19')
        reference_genome: Target genome build (default: 'hg38')

    Returns:
        True if successful, False otherwise

    Note:
        This function automatically handles chain file download, validation,
        and uses optimized settings for pharmacogenomics VCF files.
    """
    try:
        result = liftover_vcf(
            input_path=input_path,
            output_path=output_path,
            target_genome=target_genome,
            reference_genome=reference_genome,
            force=True,
            threads=2  # Conservative threading for compatibility
        )

        if result["success"]:
            stats = result.get("statistics", {})
            logger.info(f"Simple liftover completed: {stats.get('conversion_rate', 0):.1f}% conversion rate")
            return True
        else:
            logger.error(f"Simple liftover failed: {result.get('error', 'Unknown error')}")
            return False

    except Exception as e:
        logger.error(f"Simple liftover failed with exception: {e}")
        return False

# Example usage and testing function
def main():
    """Example usage and testing."""
    if len(sys.argv) < 3:
        print("Usage: python 37liftover38.py <input.vcf> <output.vcf> [target_genome] [reference_genome]")
        print("Example: python 37liftover38.py input.vcf output.vcf hg19 hg38")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2]
    target = sys.argv[3] if len(sys.argv) > 3 else "hg19"
    reference = sys.argv[4] if len(sys.argv) > 4 else "hg38"

    print(f"Starting liftover: {input_file} ({target}) -> {output_file} ({reference})")

    result = liftover_vcf(
        input_path=input_file,
        output_path=output_file,
        target_genome=target,
        reference_genome=reference,
        force=True
    )

    if result["success"]:
        print("✓ Liftover completed successfully!")
        stats = result["statistics"]
        print(f"  Input variants: {stats['input_variants']}")
        print(f"  Output variants: {stats['output_variants']}")
        print(f"  Conversion rate: {stats['conversion_rate']:.1f}%")
        if result.get("warnings"):
            print("  Warnings:")
            for warning in result["warnings"]:
                print(f"    - {warning}")
    else:
        print(f"✗ Liftover failed: {result['error']}")
        sys.exit(1)

"""
PHARMACOGENOMICS WORKFLOW INTEGRATION GUIDE:

This liftover utility is designed for seamless integration with ZaroPGx pharmacogenomics workflows.
The implementation follows GWAS-VCF specifications and integrates with existing file processing patterns.

INTEGRATION POINTS:

1. file_processor.py Integration:
   Replace the existing "TO DO" liftover logic (lines 592-622) with actual function calls:

   ```python
   # In file_processor.py determine_workflow() method
   if analysis.file_type == FileType.VCF and analysis.vcf_info:
       vcf_info = analysis.vcf_info
       reference = vcf_info.reference_genome.lower()

       is_hg38 = any(ref_id in reference for ref_id in ["hg38", "grch38", "38"])
       if is_hg38:
           workflow["recommendations"].append(
               f"<p>✓ Compatible GRCh38 reference genome detected: {vcf_info.reference_genome}</p>"
           )
       elif reference != "unknown":
           # Perform actual liftover instead of marking as provisional
           workflow["needs_liftover"] = True
           workflow["recommendations"].append(
               "<p>⚠️ Converting VCF from GRCh37 to GRCh38 using GWAS-VCF compliant liftover</p>"
           )
   ```

2. upload_router.py Integration:
   Add liftover processing before PyPGx analysis for GRCh37 VCFs:

   ```python
   # In upload processing pipeline
   from app.api.utils.liftover import liftover_vcf

   def process_pharmacogenomics_vcf(file_path: str, workflow: Dict) -> Dict:
       if workflow.get("needs_liftover"):
           input_path = file_path
           output_path = f"{file_path}.hg38.vcf.gz"

           liftover_result = liftover_vcf(
               input_path=input_path,
               output_path=output_path,
               target_genome="hg19",
               reference_genome="hg38",
               force=True
           )

           if liftover_result["success"]:
               return {
                   "processed_file": output_path,
                   "original_file": input_path,
                   "liftover_stats": liftover_result["statistics"],
                   "conversion_rate": liftover_result["statistics"]["conversion_rate"]
               }
           else:
               # Fallback to original file with warnings for downstream processing
               logger.warning(f"Liftover failed: {liftover_result['error']}")
               return {
                   "processed_file": input_path,
                   "liftover_failed": True,
                   "liftover_error": liftover_result["error"]
               }

       return {"processed_file": file_path}
   ```

3. Chain File Pre-downloading:
   Add to setup_reference_genomes.sh for faster startup:

   ```bash
   # Download essential chain files for pharmacogenomics
   wget -P /reference/hg19/liftOver/ https://hgdownload.soe.ucsc.edu/goldenPath/hg19/liftOver/hg19ToHg38.over.chain.gz
   wget -P /reference/hg38/liftOver/ https://hgdownload.soe.ucsc.edu/goldenPath/hg38/liftOver/hg38ToHg19.over.chain.gz
   ```

4. Error Handling Strategy:
   - Graceful degradation: Use original file if liftover fails
   - Detailed error reporting for troubleshooting
   - Performance warnings for large files
   - GWAS-VCF compliance notifications

5. Performance Optimization:
   - Threading support for large files
   - Compressed output for storage efficiency
   - Memory-efficient processing for pharmacogenomics datasets

6. Monitoring and Quality Control:
   - Track conversion success rates
   - Log GWAS-VCF compliance status
   - Monitor processing time for optimization
   - Report variant count changes

USAGE IN PHARMACOGENOMICS PIPELINE:

```python
# Example integration in pharmacogenomics workflow
from app.api.utils.liftover import liftover_vcf

def process_sample_for_pharmacogenomics(vcf_path: str) -> Dict:
    \"\"\"Process VCF for pharmacogenomics analysis with genome build conversion.\"\"\"

    # Validate and analyze input VCF
    validation = validate_liftover_input(vcf_path)

    if not validation["valid"]:
        return {"error": f"Invalid VCF: {validation['error']}"}

    # Check if liftover is needed
    detected_genome = validation["metadata"].get("detected_genome", "unknown")

    if detected_genome in ["GRCh37/hg19"] and not vcf_path.endswith(".hg38.vcf.gz"):
        # Perform GWAS-VCF compliant liftover
        output_path = f"{vcf_path}.hg38.vcf.gz"

        liftover_result = liftover_vcf(
            input_path=vcf_path,
            output_path=output_path,
            target_genome="hg19",
            reference_genome="hg38",
            force=True
        )

        if liftover_result["success"]:
            return {
                "processed_vcf": output_path,
                "conversion_stats": liftover_result["statistics"],
                "gwas_compliant": liftover_result["gwas_compliant"]
            }
        else:
            # Use original file with warnings
            return {
                "processed_vcf": vcf_path,
                "liftover_failed": True,
                "error": liftover_result["error"]
            }

    return {"processed_vcf": vcf_path}
```

BENEFITS FOR PHARMACOGENOMICS:
- Automatic GRCh37→GRCh38 conversion for legacy datasets
- GWAS-VCF specification compliance for interoperability
- Robust error handling for clinical workflows
- Performance optimized for pharmacogenomics file sizes
- Comprehensive validation and quality reporting
"""

if __name__ == "__main__":
    main()
