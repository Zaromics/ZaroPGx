#!/usr/bin/env python3
"""
Unified genomic file header inspector using pysam and BioPython with bcftools fallback.
Supports BAM, SAM, CRAM, FASTQ, FASTA, VCF, BCF formats.

Public API:
- inspect_header(filepath: str, max_bytes: int | None = None, timeout_sec: int | None = None) -> dict
  Returns a normalized JSON structure suitable for storage in genomic_file_headers.header_info.
"""

import os
import sys
import gzip
import argparse
from pathlib import Path
import json
import time
import subprocess
import shlex
import os
from typing import Dict, List, Optional, Union
import re
from app.api.utils.file_utils import is_compressed_file, has_index_file

try:
    import pysam
except ImportError:
    print("Error: pysam not installed. Install with: pip install pysam, or build it from source.")
    sys.exit(1)

try:
    from Bio import SeqIO
    BIOPYTHON_AVAILABLE = True
except ImportError:
    BIOPYTHON_AVAILABLE = False
    print("Warning: BioPython not available. FASTA/FASTQ support limited.")

# Environment caps (defaults: 1GB, 300s)
DEFAULT_MAX_BYTES = int(os.getenv("MAX_HEADER_READ_BYTES", str(1_000_000_000)))
DEFAULT_TIMEOUT_SEC = int(os.getenv("MAX_HEADER_PARSE_TIMEOUT_SEC", str(300)))

# TODO: refactor duplicate code -- see file_utils.py
def _has_index_file(path: Path) -> bool:
    index_extensions = ['.tbi', '.csi', '.bai', '.fai', '.crai']
    for ext in index_extensions:
        if (path.parent / f"{path.stem}{ext}").exists():
            return True
    return False


def inspect_header(filepath: str, max_bytes: Optional[int] = None, timeout_sec: Optional[int] = None) -> Dict:
    """
    Inspect genomic file header and return normalized JSON:
    {
      "file_info": {"path","format","size","compressed","has_index"},
      "metadata": {"version","created_by","reference_genome","reference_genome_path"},
      "sequences": [{"name","length"}],
      "samples": ["sampleIDs"],
      "format_specific": {...}
    }
    """
    start_time = time.time()
    max_bytes = DEFAULT_MAX_BYTES if max_bytes is None else max_bytes
    timeout_sec = DEFAULT_TIMEOUT_SEC if timeout_sec is None else timeout_sec

    path = Path(filepath)
    if not path.exists():
        return {"error": f"File not found: {filepath}"}

    # File size check (cap streaming; not a hard reject for larger files if header-only access)
    try:
        size_bytes = path.stat().st_size
    except Exception:
        size_bytes = None

    compressed = is_compressed_file(path)
    has_index = has_index_file(path)

    inspector = GenomicHeaderInspector()

    # Enforce overall timeout by measuring elapsed time and avoiding long scans
    def _ensure_time():
        if time.time() - start_time > timeout_sec:
            raise TimeoutError(f"Header inspection exceeded {timeout_sec}s")

    # Determine format via existing helper
    file_format = inspector._get_file_format(filepath)
    _ensure_time()

    # Dispatch to specific inspectors with minimal I/O
    if file_format in ('.vcf', '.bcf', 'vcf.gz', 'bcf.gz', 'vcf.bz2', 'bcf.bz2', 'vcf.bgz', 'bcf.bgz'):
        # Try pysam first
        try:
            res = inspector._inspect_vcf_bcf(filepath)
        except Exception as e:
            res = {"error": str(e)}
        # Fallback to bcftools header only if needed
        if 'error' in res or not res:
            _ensure_time()
            try:
                cmd = f"bcftools view -h {shlex.quote(filepath)}"
                cp = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=max(5, min(timeout_sec, 60)))
                header_lines = [ln for ln in cp.stdout.splitlines() if ln]
                # Minimal parse from header lines
                samples = []
                contigs: List[str] = []
                contig_lengths: Dict[str, Optional[int]] = {}
                version = None
                ref_path = None
                created_by = None
                for ln in header_lines:
                    if ln.startswith('##fileformat=') and not version:
                        version = ln.split('=', 1)[1]
                    elif ln.startswith('##reference=') and not ref_path:
                        ref_path = ln.split('=', 1)[1].strip('"')
                    elif ln.startswith('##GATKCommandLine.') and not created_by:
                        # Example: ##GATKCommandLine.HaplotypeCaller=<ID=HaplotypeCaller,Version=3.8-1_..., ...>
                        try:
                            import re as _re
                            m_id = _re.search(r"GATKCommandLine\.([^=]+)=<", ln)
                            m_ver = _re.search(r"Version=([^,>]+)", ln)
                            tool = m_id.group(1) if m_id else "GATK"
                            ver = m_ver.group(1) if m_ver else None
                            created_by = f"GATK {tool}{(' ' + ver) if ver else ''}"
                        except Exception:
                            pass
                    elif ln.startswith('##bcftools_viewVersion=') and not created_by:
                        # Example: ##bcftools_viewVersion=1.22-..., record bcftools version
                        created_by = ln.split('=', 1)[1]
                    elif ln.startswith('##contig='):
                        import re as _re
                        m_id = _re.search(r'ID=([^,>]+)', ln)
                        m_len = _re.search(r'length=([0-9]+)', ln)
                        if m_id:
                            cid = m_id.group(1)
                            contigs.append(cid)
                            try:
                                contig_lengths[cid] = int(m_len.group(1)) if m_len else None
                            except Exception:
                                contig_lengths[cid] = None
                    elif ln.startswith('#CHROM'):
                        parts = ln.split('\t')
                        if len(parts) > 9:
                            samples = parts[9:]
                res = {
                    'format': 'VCF/BCF',
                    'file': filepath,
                    'samples': samples,
                    'num_samples': len(samples),
                    'contigs': contigs,
                    'num_contigs': len(contigs),
                    'info_fields': [],
                    'format_fields': [],
                    'filter_fields': [],
                    'header_records': header_lines,
                    'contig_lengths': contig_lengths,
                    'version': version,
                    'created_by': created_by,
                }
            except Exception as e:
                res = {'error': f"bcftools fallback failed: {e}"}

        # Normalize
        md_ref = None
        md_ref_path = None
        md_version = None
        md_created_by = None
        try:
            # Try to infer ref from header_records
            for rec in res.get('header_records', []):
                if isinstance(rec, str):
                    if rec.startswith('##reference=') and not md_ref_path:
                        md_ref_path = rec.split('=', 1)[1].strip('"')
                    if rec.startswith('##fileformat=') and not md_version:
                        md_version = rec.split('=', 1)[1]
                    if rec.startswith('##GATKCommandLine.') and not md_created_by:
                        try:
                            import re as _re
                            m_id = _re.search(r"GATKCommandLine\.([^=]+)=<", rec)
                            m_ver = _re.search(r"Version=([^,>]+)", rec)
                            tool = m_id.group(1) if m_id else "GATK"
                            ver = m_ver.group(1) if m_ver else None
                            md_created_by = f"GATK {tool}{(' ' + ver) if ver else ''}"
                        except Exception:
                            pass
        except Exception:
            pass
        if md_ref_path:
            # simple inference of genome name
            base = Path(md_ref_path).name.lower()
            if 'grch38' in base or 'hg38' in base:
                md_ref = 'GRCh38'
            elif 'grch37' in base or 'hg19' in base:
                md_ref = 'GRCh37'

        # Prefer parsed values from res if available
        if not md_version:
            md_version = res.get('version')
        if not md_created_by:
            md_created_by = res.get('created_by')

        # Build sequences with lengths if available
        sequences_norm: List[Dict[str, Optional[Union[str, int]]]] = []
        contig_lengths_map = res.get('contig_lengths') or {}
        if res.get('contigs'):
            for c in res.get('contigs'):
                sequences_norm.append({'name': c, 'length': contig_lengths_map.get(c)})
        else:
            sequences_norm = [{'name': c, 'length': None} for c in (res.get('contigs') or [])]

        # Attempt fast variant count via bcftools index -n (if available)
        variant_count: Optional[int] = None
        try:
            count_cmd = f"bcftools index -n {shlex.quote(filepath)}"
            cp_cnt = subprocess.run(count_cmd, shell=True, capture_output=True, text=True, timeout=10)
            if cp_cnt.returncode == 0:
                txt = (cp_cnt.stdout or cp_cnt.stderr or '').strip()
                # bcftools may print a single integer
                variant_count = int(txt) if txt.isdigit() else None
        except Exception:
            variant_count = None

        normalized = {
            'file_info': {
                'path': str(path),
                'format': 'VCF',
                'size': size_bytes,
                'compressed': bool(compressed),
                'has_index': bool(has_index),
            },
            'metadata': {
                'version': md_version,
                'created_by': md_created_by,
                'reference_genome': md_ref or None,
                'reference_genome_path': md_ref_path,
            },
            'sequences': sequences_norm,
            'samples': res.get('samples') or [],        # <-- all samples
            'sample': (res.get('samples')[0] if (res.get('samples') and len(res.get('samples')) > 0) else None),
            'format_specific': {
                'vcf_info_fields': {k: '' for k in (res.get('info_fields') or [])},
                'vcf_format_fields': {k: '' for k in (res.get('format_fields') or [])},
                'variant_count': variant_count,
            },
        }
        return normalized

    elif file_format in ('.bam', '.sam', '.cram'):
        res = inspector._inspect_sam_bam_cram(filepath)
        header = res.get('header_dict') if isinstance(res, dict) else None
        sequences = []
        programs = []
        created_by = None
        version = None
        try:
            if isinstance(header, dict):
                for sq in header.get('SQ', []) or []:
                    name = sq.get('SN')
                    ln = sq.get('LN')
                    if name:
                        sequences.append({'name': name, 'length': ln})
                programs = header.get('PG', []) or []
                if programs:
                    created_by = programs[0].get('ID')
                    version = programs[0].get('VN')
        except Exception:
            pass
        normalized = {
            'file_info': {
                'path': str(path),
                'format': 'BAM' if file_format == '.bam' else ('SAM' if file_format == '.sam' else 'CRAM'),
                'size': size_bytes,
                'compressed': bool(compressed),
                'has_index': bool(has_index),
            },
            'metadata': {
                'version': version,
                'created_by': created_by,
                'reference_genome': None,
                'reference_genome_path': None,
            },
            'sequences': sequences,
            'sample': None,
            'format_specific': {
                'sam_header_lines': [],
                'programs': programs,
            },
        }
        return normalized

    elif file_format in ('.fastq', '.fq'):
        res = inspector._inspect_fastq(filepath)
        first_records = res.get('first_records') or []
        normalized = {
            'file_info': {
                'path': str(path),
                'format': 'FASTQ',
                'size': size_bytes,
                'compressed': bool(compressed),
                'has_index': bool(has_index),
            },
            'metadata': {
                'version': None,
                'created_by': None,
                'reference_genome': None,
                'reference_genome_path': None,
            },
            'sequences': [],
            'sample': next((rec.get('id') for rec in first_records if isinstance(rec, dict) and rec.get('id')), None),
            'format_specific': {
                'fastq_preview_records': first_records,
                'total_records': res.get('total_records') or res.get('estimated_records')
            },
        }
        return normalized

    elif file_format in ('.fasta', '.fa', '.fas', '.fna'):
        res = inspector._inspect_fasta(filepath)
        seqs = res.get('sequences') or []
        normalized = {
            'file_info': {
                'path': str(path),
                'format': 'FASTA',
                'size': size_bytes,
                'compressed': bool(compressed),
                'has_index': bool(has_index),
            },
            'metadata': {
                'version': None,
                'created_by': None,
                'reference_genome': None,
                'reference_genome_path': None,
            },
            'sequences': [{'name': s.get('id') or s.get('header'), 'length': s.get('length')} for s in seqs if isinstance(s, dict)],
            'sample': None,
            'format_specific': {
                'total_sequences': res.get('total_sequences'),
                'total_length': res.get('total_length'),
            },
        }
        return normalized

    else:
        # Unknown format from extension; attempt minimal detection via VCF header mark
        return {
            'file_info': {
                'path': str(path),
                'format': (file_format or 'unknown').upper().strip('.'),
                'size': size_bytes,
                'compressed': bool(compressed),
                'has_index': bool(has_index),
            },
            'metadata': {
                'version': None,
                'created_by': None,
                'reference_genome': None,
                'reference_genome_path': None,
            },
            'sequences': [],
            'sample': None,
            'format_specific': {},
        }


class GenomicHeaderInspector:
    """Unified tool for inspecting headers of various genomic file formats."""
    
    def __init__(self):
        self.supported_formats = {
            '.bam': self._inspect_sam_bam_cram,
            '.sam': self._inspect_sam_bam_cram,
            '.cram': self._inspect_sam_bam_cram,
            '.vcf': self._inspect_vcf_bcf,
            '.bcf': self._inspect_vcf_bcf,
            '.fastq': self._inspect_fastq,
            '.fq': self._inspect_fastq,
            '.fasta': self._inspect_fasta,
            '.fa': self._inspect_fasta,
            '.fas': self._inspect_fasta,
            '.fna': self._inspect_fasta
        }
    
    def _get_file_format(self, filepath: str) -> Optional[str]:
        """Determine file format from extension, handling compressed files."""
        path = Path(filepath)
        
        # Handle compressed files
        if path.suffix == '.gz':
            return path.with_suffix('').suffix.lower()
        elif path.suffix == '.bz2':
            return path.with_suffix('').suffix.lower()
        else:
            return path.suffix.lower()
    
    def _open_file(self, filepath: str, mode: str = 'r'):
        """Open file handling compression automatically."""
        if filepath.endswith('.gz'):
            return gzip.open(filepath, mode + 't')
        elif filepath.endswith('.bz2'):
            import bz2
            return bz2.open(filepath, mode + 't')
        else:
            return open(filepath, mode)
    
    def _inspect_sam_bam_cram(self, filepath: str) -> Dict:
        """Inspect SAM/BAM/CRAM headers using pysam."""
        try:
            with pysam.AlignmentFile(filepath, 'r') as samfile:
                header = samfile.header.to_dict()
                
                result = {
                    'format': 'SAM/BAM/CRAM',
                    'file': filepath,
                    'header_lines': len(samfile.text.strip().split('\n')) if samfile.text else 0,
                    'sequences': len(header.get('SQ', [])),
                    'read_groups': len(header.get('RG', [])),
                    'programs': len(header.get('PG', [])),
                    'header_dict': header
                }
                
                # Add some key statistics
                if 'SQ' in header:
                    total_length = sum(sq['LN'] for sq in header['SQ'] if 'LN' in sq)
                    result['total_reference_length'] = total_length
                
                return result
                
        except Exception as e:
            return {'error': f"Failed to read SAM/BAM/CRAM file: {str(e)}"}
    
    def _inspect_vcf_bcf(self, filepath: str) -> Dict:
        """Inspect VCF/BCF headers using pysam."""
        try:
            with pysam.VariantFile(filepath) as vcf:
                header = vcf.header
                
                result = {
                    'format': 'VCF/BCF',
                    'file': filepath,
                    'samples': list(vcf.header.samples),
                    'num_samples': len(list(vcf.header.samples)),
                    'contigs': [rec.name for rec in header.contigs],
                    'num_contigs': len(list(header.contigs)),
                    'info_fields': list(header.info.keys()),
                    'format_fields': list(header.formats.keys()),
                    'filter_fields': list(header.filters.keys()),
                    'header_records': []
                }
                
                # Get header records
                for rec in header.records:
                    result['header_records'].append(str(rec))
                
                return result
                
        except Exception as e:
            return {'error': f"Failed to read VCF/BCF file: {str(e)}"}
    
    def _inspect_fastq(self, filepath: str) -> Dict:
        """Inspect FASTQ file (show first few records as 'header' info)."""
        try:
            result = {
                'format': 'FASTQ',
                'file': filepath,
                'first_records': [],
                'total_records': 0
            }
            
            if BIOPYTHON_AVAILABLE:
                with self._open_file(filepath) as handle:
                    records = SeqIO.parse(handle, "fastq")
                    for i, record in enumerate(records):
                        if i < 5:  # Show first 5 records
                            result['first_records'].append({
                                'id': record.id,
                                'description': record.description,
                                'length': len(record.seq)
                            })
                        result['total_records'] = i + 1
                        if i >= 10000:  # Don't count beyond 10k for performance
                            result['total_records'] = f">{i + 1}"
                            break
            else:
                # Fallback without BioPython
                with self._open_file(filepath) as f:
                    count = 0
                    while count < 20:  # First 5 records = 20 lines
                        lines = []
                        for _ in range(4):  # FASTQ records are 4 lines each
                            line = f.readline()
                            if not line:
                                return result
                            lines.append(line.strip())
                        
                        if count // 4 < 5:
                            result['first_records'].append({
                                'id': lines[0],
                                'sequence_length': len(lines[1]),
                                'quality_length': len(lines[3])
                            })
                        count += 4
                
                # Try to count total (rough estimate)
                try:
                    with self._open_file(filepath) as f:
                        line_count = sum(1 for _ in f)
                        result['estimated_records'] = line_count // 4
                except:
                    result['estimated_records'] = 'unknown'
            
            return result
            
        except Exception as e:
            return {'error': f"Failed to read FASTQ file: {str(e)}"}
    
    def _inspect_fasta(self, filepath: str) -> Dict:
        """Inspect FASTA file headers."""
        try:
            result = {
                'format': 'FASTA',
                'file': filepath,
                'sequences': [],
                'total_sequences': 0,
                'total_length': 0
            }
            
            if BIOPYTHON_AVAILABLE:
                with self._open_file(filepath) as handle:
                    for i, record in enumerate(SeqIO.parse(handle, "fasta")):
                        seq_info = {
                            'id': record.id,
                            'description': record.description,
                            'length': len(record.seq)
                        }
                        
                        if i < 10:  # Show first 10 sequences
                            result['sequences'].append(seq_info)
                        
                        result['total_length'] += len(record.seq)
                        result['total_sequences'] = i + 1
                        
                        if i >= 10000:  # Performance limit
                            result['total_sequences'] = f">{i + 1}"
                            break
            else:
                # Fallback without BioPython
                with self._open_file(filepath) as f:
                    current_header = None
                    current_length = 0
                    seq_count = 0
                    
                    for line in f:
                        line = line.strip()
                        if line.startswith('>'):
                            if current_header is not None:
                                if seq_count < 10:
                                    result['sequences'].append({
                                        'header': current_header,
                                        'length': current_length
                                    })
                                result['total_length'] += current_length
                                seq_count += 1
                            
                            current_header = line
                            current_length = 0
                        else:
                            current_length += len(line)
                    
                    # Don't forget the last sequence
                    if current_header is not None:
                        if seq_count < 10:
                            result['sequences'].append({
                                'header': current_header,
                                'length': current_length
                            })
                        result['total_length'] += current_length
                        seq_count += 1
                    
                    result['total_sequences'] = seq_count
            
            return result
            
        except Exception as e:
            return {'error': f"Failed to read FASTA file: {str(e)}"}
    
    # Removed legacy inspect_file/print_results; normalized API is inspect_header()


def extract_raw_header_text(filepath: str) -> Optional[str]:
    """Return the raw textual header for supported formats.

    - VCF/BCF: returns the full header text (## records and #CHROM line)
    - SAM/BAM/CRAM: returns @-prefixed header lines
    - Others: returns None
    """
    try:
        # Try VCF/BCF first
        if filepath.endswith(('.vcf', '.vcf.gz', '.gvcf', '.gvcf.gz', '.bcf')):
            try:
                with pysam.VariantFile(filepath) as vcf:
                    header_text = str(vcf.header)
                    return header_text
            except Exception:
                pass

        # Try SAM/BAM/CRAM
        if filepath.endswith(('.sam', '.bam', '.cram')):
            try:
                with pysam.AlignmentFile(filepath, 'r') as samfile:
                    return samfile.text or ''
            except Exception:
                pass
    except Exception:
        return None
    return None


def _is_canonical_contig(contig_id: str) -> bool:
    """Return True if contig_id is canonical (1-22, X, Y, M/MT with or without 'chr' prefix)."""
    if not contig_id:
        return False
    cid = contig_id.strip()
    if cid.lower().startswith('chr'):
        cid = cid[3:]
    cid_upper = cid.upper()

    if cid_upper in {str(i) for i in range(1, 23)} | {'X', 'Y', 'M', 'MT'}:
        return True
    return False


def filter_header_to_canonical_contigs(header_text: str) -> str:
    """Filter header text to keep only canonical contigs.

    - For VCF: retains all header lines, but filters lines that match
      '##contig=<ID=...>' to only canonical contigs.
    - For SAM/BAM: retains all header lines, but filters '@SQ' lines to
      only canonical SN values.
    Other header lines are preserved as-is.
    """
    if not header_text:
        return header_text

    vcf_contig_re = re.compile(r'^##contig=<ID=([^,>]+)')
    sam_sq_re = re.compile(r'^@SQ\s+.*?SN:([^\s\t]+)')

    filtered_lines: List[str] = []
    for line in header_text.splitlines():
        try:
            # VCF contig line
            m = vcf_contig_re.match(line)
            if m:
                contig = m.group(1)
                if _is_canonical_contig(contig):
                    filtered_lines.append(line)
                # Skip non-canonical contigs
                continue

            # SAM/BAM SQ line
            m2 = sam_sq_re.match(line)
            if m2:
                contig = m2.group(1)
                if _is_canonical_contig(contig):
                    filtered_lines.append(line)
                # Skip non-canonical contigs
                continue

            # All other header lines
            filtered_lines.append(line)
        except Exception:
            # On any parsing error, keep the original line
            filtered_lines.append(line)

    return "\n".join(filtered_lines) + ("\n" if not header_text.endswith('\n') else '')


def main():
    parser = argparse.ArgumentParser(
        description="Unified genomic file header inspector",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Supported formats:
  - SAM/BAM/CRAM files
  - VCF/BCF files  
  - FASTA/FA files
  - FASTQ/FQ files
  
Examples:
  python genomic_inspector.py sample.bam
  python genomic_inspector.py variants.vcf.gz --verbose
  python genomic_inspector.py sequences.fasta
        """
    )
    
    parser.add_argument('file', help='Genomic file to inspect')
    parser.add_argument('-v', '--verbose', action='store_true',
                       help='Show detailed header information')
    
    args = parser.parse_args()
    
    # Use normalized API for CLI too
    try:
        normalized = inspect_header(args.file)
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(2)

    # Pretty-print normalized JSON
    print(json.dumps(normalized, indent=2))


if __name__ == "__main__":
    main()