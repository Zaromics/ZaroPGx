#!/usr/bin/env python3
"""
Unified genomic file header inspector using pysam and BioPython with bcftools fallback.
Supports BAM, SAM, CRAM, FASTQ, FASTA, VCF, BCF formats.

Public API:
- inspect_header(filepath: str, max_bytes: int | None = None, timeout_sec: int | None = None) -> dict
  Returns a normalized JSON structure suitable for storage in genomic_file_headers.header_info.
"""

# Standard library imports
import argparse
import bz2
import gzip
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Union

# Local imports
from app.api.utils.file_utils import has_index_file, is_compressed_file

# Third-party imports (optional dependencies)
# pysam is optional here the same way it is in file_processor.py: importing this module
# must not kill the process. The VCF/BCF path falls back to `bcftools view -h` (see
# inspect_header) and the SAM/BAM/CRAM path returns an error dict its caller handles.
try:
    import pysam  # type: ignore

    _HAS_PYSAM = True
# Broad: a broken libhts link surfaces as OSError, not ImportError.
except Exception:
    pysam = None  # type: ignore
    _HAS_PYSAM = False
    print(
        "Warning: pysam not installed; header inspection will fall back to bcftools. "
        "Install with: pip install pysam, or build it from source.",
        file=sys.stderr,
    )

try:
    from Bio import SeqIO

    BIOPYTHON_AVAILABLE = True
except ImportError:
    BIOPYTHON_AVAILABLE = False
    print("Warning: BioPython not available. FASTA/FASTQ support limited.")

# Environment caps (defaults: 1GB, 300s)
DEFAULT_MAX_BYTES = int(os.getenv("MAX_HEADER_READ_BYTES", str(1_000_000_000)))
DEFAULT_TIMEOUT_SEC = int(os.getenv("MAX_HEADER_PARSE_TIMEOUT_SEC", str(300)))


# --- Genome build detection --------------------------------------------------
#
# Assembly-defining sequence lengths, keyed by the contig name with any `chr`
# prefix removed (GRCh37/b37 headers say `1`, hg19/hg38 headers say `chr1`).
#
# These are facts about the assemblies rather than heuristics: no two GRC builds
# give the same chromosome the same length, so a nine-digit number appearing as
# the `length=` of the correspondingly named `##contig` record cannot be the
# coincidence that a three-character token in free text can.
#
# The same table, under the same reasoning, drives the SAM/BAM/CRAM detector in
# docker/gatk-api/gatk_api.py (SQ_LENGTH_ASSEMBLIES) -- that service is a
# separate image and cannot import from the app, so the values are duplicated
# deliberately. They were read out of the sequence dictionaries this deployment
# ships (reference/hg38/Homo_sapiens_assembly38.dict, reference/hg19/ucsc.hg19.dict,
# reference/grch37/human_g1k_v37.dict); reference/ is gitignored, so no test can
# pin them -- re-read the dictionaries if you touch this table.
#
# Unlike gatk-api's copy, this one answers with the *assembly* alone and does not
# read the `chr` prefix as evidence of which GRCh37 FASTA to align against: the
# app stores an assembly name in metadata.reference_genome, and no caller here
# picks a reference file from it.
CONTIG_LENGTH_ASSEMBLIES: Dict[tuple, str] = {
    ("1", 248956422): "GRCh38",
    ("1", 249250621): "GRCh37",
    ("2", 242193529): "GRCh38",
    ("2", 243199373): "GRCh37",
    ("3", 198295559): "GRCh38",
    ("3", 198022430): "GRCh37",
    ("X", 156040895): "GRCh38",
    ("X", 155270560): "GRCh37",
}

# Tokens that name an assembly when they appear in a free-text `##reference=`
# value. Beyond the two build names, these cover the reference files that are
# actually in use and whose names carry no build name of their own:
# human_g1k_v37.fasta, and the GATK bundle's Homo_sapiens_assembly38.fasta /
# Homo_sapiens_assembly19.fasta (assembly19 is a GRCh37 reference).
#
# Deliberately no bare `b37` / `b38`: three characters is a substring, not a
# token, and this value is a path that may carry a hash or a project name.
ASSEMBLY_NAME_TOKENS: Dict[str, tuple] = {
    "GRCh38": ("grch38", "hg38", "assembly38"),
    "GRCh37": ("grch37", "hg19", "g1k_v37", "assembly19"),
}


def _normalise_contig_name(name: Optional[str]) -> str:
    """`chr1` / `1` / `CHR1` -> `1`, so both naming conventions hit one table."""
    cid = (name or "").strip()
    if cid.lower().startswith("chr"):
        cid = cid[3:]
    return cid.upper()


def parse_vcf_contig_lengths(header_records) -> Dict[str, Optional[int]]:
    """Parse `##contig=<ID=...,length=...>` records into {ID: length}.

    Field-wise, not by a regex over the whole line: the fields of a structured
    header line are unordered and a `##contig` record may carry `assembly=`,
    `md5=` and more between the two that matter here.
    """
    lengths: Dict[str, Optional[int]] = {}
    for record in header_records or []:
        if not isinstance(record, str):
            continue
        line = record.strip()
        if not line.startswith("##contig=<") or not line.endswith(">"):
            continue
        contig_id: Optional[str] = None
        length: Optional[int] = None
        for field in line[len("##contig=<") : -1].split(","):
            key, sep, value = field.partition("=")
            if not sep:
                continue
            key = key.strip()
            value = value.strip().strip('"')
            if key == "ID":
                contig_id = value
            elif key == "length":
                try:
                    length = int(value)
                except ValueError:
                    length = None
        if contig_id:
            # A record that repeats a contig without its length must not erase a
            # length already read for it.
            if contig_id not in lengths or lengths[contig_id] is None:
                lengths[contig_id] = length
    return lengths


def merged_contig_lengths(
    header_records=None, contig_lengths=None
) -> Dict[str, Optional[int]]:
    """Combine already-parsed contig lengths with those in the header text.

    The two readers in this module supply different halves: the bcftools
    fallback parses `contig_lengths` itself, while the pysam path returns the
    header records verbatim and no lengths at all.
    """
    merged: Dict[str, Optional[int]] = {}
    for name, length in (contig_lengths or {}).items():
        if isinstance(name, str):
            merged[name] = length if isinstance(length, int) else None
    for name, length in parse_vcf_contig_lengths(header_records).items():
        if merged.get(name) is None:
            merged[name] = length
    return merged


def _assemblies_from_contig_lengths(contig_lengths) -> List[str]:
    """Every assembly the (name, length) pairs are consistent with, sorted."""
    seen = set()
    for name, length in (contig_lengths or {}).items():
        if not isinstance(length, int):
            continue
        assembly = CONTIG_LENGTH_ASSEMBLIES.get((_normalise_contig_name(name), length))
        if assembly:
            seen.add(assembly)
    return sorted(seen)


def _assemblies_from_reference_values(values) -> List[str]:
    """Every assembly named by the free text of a `##reference=` value, sorted.

    A single value naming two builds -- `hg19_to_hg38_lifted.fasta` -- returns
    both, so the caller reports the conflict instead of taking whichever the
    checks happened to test first.
    """
    seen = set()
    for value in values or []:
        if not isinstance(value, str):
            continue
        lowered = value.lower()
        for assembly, tokens in ASSEMBLY_NAME_TOKENS.items():
            if any(token in lowered for token in tokens):
                seen.add(assembly)
    return sorted(seen)


def reference_values_from_header(header_records) -> List[str]:
    """The value of every `##reference=` line, in header order."""
    values: List[str] = []
    for record in header_records or []:
        if isinstance(record, str) and record.strip().startswith("##reference="):
            values.append(record.strip().split("=", 1)[1].strip().strip('"'))
    return values


def detect_reference_assembly(header_records=None, contig_lengths=None) -> Dict:
    """Decide which assembly a header describes, structured evidence first.

    Returns::

        {"assembly": "GRCh38" | "GRCh37" | None,
         "source": "contig_lengths" | "reference_line" | None,
         "ambiguous": bool,
         "candidates": [assembly, ...]}

    `assembly` is None whenever the answer is not established -- either no
    evidence at all, or evidence that contradicts itself. A caller must treat
    None as "no answer" rather than as a detection: naming the wrong build means
    the sample is analysed against the wrong coordinates with nothing in the
    output saying so, which is worse than declining to answer.

    Decision order:

    1. `##contig=<ID=...,length=...>` records (or lengths already parsed off
       them, e.g. from a SAM `@SQ` dictionary). These describe the coordinate
       system every position in the file is expressed in.
    2. The free-text `##reference=` line, and only when step 1 found nothing.
       It records what the writing tool was pointed at, which a liftover leaves
       untouched -- a GRCh38 file with a stale `##reference=...hg19.fasta` is
       exactly the case this ordering exists for -- so it breaks ties and never
       overrides the records.

    Contig records that name two assemblies (or that conflict with each other)
    end the decision as ambiguous; the reference line is not consulted, because
    free text cannot resolve a file whose own records disagree.
    """
    lengths = merged_contig_lengths(header_records, contig_lengths)
    contig_assemblies = _assemblies_from_contig_lengths(lengths)
    if contig_assemblies:
        ambiguous = len(contig_assemblies) > 1
        return {
            "assembly": None if ambiguous else contig_assemblies[0],
            "source": "contig_lengths",
            "ambiguous": ambiguous,
            "candidates": contig_assemblies,
        }

    ref_assemblies = _assemblies_from_reference_values(
        reference_values_from_header(header_records)
    )
    if ref_assemblies:
        ambiguous = len(ref_assemblies) > 1
        return {
            "assembly": None if ambiguous else ref_assemblies[0],
            "source": "reference_line",
            "ambiguous": ambiguous,
            "candidates": ref_assemblies,
        }

    return {
        "assembly": None,
        "source": None,
        "ambiguous": False,
        "candidates": [],
    }


def inspect_header(
    filepath: str, max_bytes: Optional[int] = None, timeout_sec: Optional[int] = None
) -> Dict:
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
    if file_format in (
        ".vcf",
        ".bcf",
        "vcf.gz",
        "bcf.gz",
        "vcf.bz2",
        "bcf.bz2",
        "vcf.bgz",
        "bcf.bgz",
    ):
        # Try pysam first
        try:
            res = inspector._inspect_vcf_bcf(filepath)
        except Exception as e:
            res = {"error": str(e)}
        # Fallback to bcftools header only if needed
        if "error" in res or not res:
            _ensure_time()
            try:
                cmd = f"bcftools view -h {shlex.quote(filepath)}"
                cp = subprocess.run(
                    cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=max(5, min(timeout_sec, 60)),
                )
                header_lines = [ln for ln in cp.stdout.splitlines() if ln]
                # Minimal parse from header lines
                samples = []
                contigs: List[str] = []
                contig_lengths: Dict[str, Optional[int]] = {}
                version = None
                ref_path = None
                created_by = None
                for ln in header_lines:
                    if ln.startswith("##fileformat=") and not version:
                        version = ln.split("=", 1)[1]
                    elif ln.startswith("##reference=") and not ref_path:
                        ref_path = ln.split("=", 1)[1].strip('"')
                    elif ln.startswith("##GATKCommandLine.") and not created_by:
                        # Example: ##GATKCommandLine.HaplotypeCaller=<ID=HaplotypeCaller,Version=3.8-1_..., ...>
                        try:
                            m_id = re.search(r"GATKCommandLine\.([^=]+)=<", ln)
                            m_ver = re.search(r"Version=([^,>]+)", ln)
                            tool = m_id.group(1) if m_id else "GATK"
                            ver = m_ver.group(1) if m_ver else None
                            created_by = f"GATK {tool}{(' ' + ver) if ver else ''}"
                        except Exception:
                            pass
                    elif ln.startswith("##bcftools_viewVersion=") and not created_by:
                        # Example: ##bcftools_viewVersion=1.22-..., record bcftools version
                        created_by = ln.split("=", 1)[1]
                    elif ln.startswith("##contig="):
                        m_id = re.search(r"ID=([^,>]+)", ln)
                        m_len = re.search(r"length=([0-9]+)", ln)
                        if m_id:
                            cid = m_id.group(1)
                            contigs.append(cid)
                            try:
                                contig_lengths[cid] = (
                                    int(m_len.group(1)) if m_len else None
                                )
                            except Exception:
                                contig_lengths[cid] = None
                    elif ln.startswith("#CHROM"):
                        parts = ln.split("\t")
                        if len(parts) > 9:
                            samples = parts[9:]
                res = {
                    "format": "VCF/BCF",
                    "file": filepath,
                    "samples": samples,
                    "num_samples": len(samples),
                    "contigs": contigs,
                    "num_contigs": len(contigs),
                    "info_fields": [],
                    "format_fields": [],
                    "filter_fields": [],
                    "header_records": header_lines,
                    "contig_lengths": contig_lengths,
                    "version": version,
                    "created_by": created_by,
                }
            except Exception as e:
                res = {"error": f"bcftools fallback failed: {e}"}

        # Normalize
        md_ref_path = None
        md_version = None
        md_created_by = None
        try:
            # Try to infer ref from header_records
            for rec in res.get("header_records", []):
                if isinstance(rec, str):
                    if rec.startswith("##reference=") and not md_ref_path:
                        md_ref_path = rec.split("=", 1)[1].strip('"')
                    if rec.startswith("##fileformat=") and not md_version:
                        md_version = rec.split("=", 1)[1]
                    if rec.startswith("##GATKCommandLine.") and not md_created_by:
                        try:
                            m_id = re.search(r"GATKCommandLine\.([^=]+)=<", rec)
                            m_ver = re.search(r"Version=([^,>]+)", rec)
                            tool = m_id.group(1) if m_id else "GATK"
                            ver = m_ver.group(1) if m_ver else None
                            md_created_by = f"GATK {tool}{(' ' + ver) if ver else ''}"
                        except Exception:
                            pass
        except Exception:
            pass
        # Which assembly the file is expressed against: the ##contig records
        # first, the ##reference= line only as a tie-breaker. The previous
        # inference read nothing but that free-text line, so a lifted-over VCF
        # carrying a stale `##reference=...hg19.fasta` reported GRCh37 while
        # every contig in it was GRCh38.
        build = detect_reference_assembly(
            header_records=res.get("header_records") or [],
            contig_lengths=res.get("contig_lengths") or {},
        )
        md_ref = build["assembly"]

        # Prefer parsed values from res if available
        if not md_version:
            md_version = res.get("version")
        if not md_created_by:
            md_created_by = res.get("created_by")

        # Build sequences with lengths if available. The lengths come from the
        # ##contig records when the reader did not parse them itself -- the
        # pysam path returns records but no contig_lengths, which used to leave
        # every sequence here with length None.
        sequences_norm: List[Dict[str, Optional[Union[str, int]]]] = []
        contig_lengths_map = merged_contig_lengths(
            res.get("header_records") or [], res.get("contig_lengths") or {}
        )
        if res.get("contigs"):
            for c in res.get("contigs"):
                sequences_norm.append({"name": c, "length": contig_lengths_map.get(c)})
        else:
            sequences_norm = [
                {"name": c, "length": None} for c in (res.get("contigs") or [])
            ]

        # Attempt fast variant count via bcftools index -n (if available)
        variant_count: Optional[int] = None
        try:
            count_cmd = f"bcftools index -n {shlex.quote(filepath)}"
            cp_cnt = subprocess.run(
                count_cmd, shell=True, capture_output=True, text=True, timeout=10
            )
            if cp_cnt.returncode == 0:
                txt = (cp_cnt.stdout or cp_cnt.stderr or "").strip()
                # bcftools may print a single integer
                variant_count = int(txt) if txt.isdigit() else None
        except Exception:
            variant_count = None

        normalized = {
            "file_info": {
                "path": str(path),
                "format": "VCF",
                "size": size_bytes,
                "compressed": bool(compressed),
                "has_index": bool(has_index),
            },
            "metadata": {
                "version": md_version,
                "created_by": md_created_by,
                "reference_genome": md_ref or None,
                "reference_genome_path": md_ref_path,
                # The evidence trail behind reference_genome. Additive keys:
                # reference_genome itself keeps its contract (an assembly name,
                # or None when undetectable), so callers reading only that are
                # unaffected, while a None caused by a self-contradicting header
                # stays distinguishable from one caused by an empty header.
                "reference_genome_source": build["source"],
                "reference_genome_ambiguous": build["ambiguous"],
                "reference_genome_candidates": build["candidates"],
            },
            "sequences": sequences_norm,
            "samples": res.get("samples") or [],  # <-- all samples
            "sample": (
                res.get("samples")[0]
                if (res.get("samples") and len(res.get("samples")) > 0)
                else None
            ),
            "format_specific": {
                "vcf_info_fields": {k: "" for k in (res.get("info_fields") or [])},
                "vcf_format_fields": {k: "" for k in (res.get("format_fields") or [])},
                "variant_count": variant_count,
            },
        }
        return normalized

    elif file_format in (".bam", ".sam", ".cram"):
        if not _HAS_PYSAM:
            # Unlike the VCF/BCF branch above there is no bcftools fallback here, and the
            # normalization below would otherwise return a well-formed header with empty
            # sequences and no error key — i.e. an unreadable file looking like a valid one.
            return {"error": "pysam is required to inspect SAM/BAM/CRAM headers"}
        res = inspector._inspect_sam_bam_cram(filepath)
        if isinstance(res, dict) and "error" in res:
            return {"error": res["error"]}
        header = res.get("header_dict") if isinstance(res, dict) else None
        sequences = []
        programs = []
        created_by = None
        version = None
        try:
            if isinstance(header, dict):
                for sq in header.get("SQ", []) or []:
                    name = sq.get("SN")
                    ln = sq.get("LN")
                    if name:
                        sequences.append({"name": name, "length": ln})
                programs = header.get("PG", []) or []
                if programs:
                    created_by = programs[0].get("ID")
                    version = programs[0].get("VN")
        except Exception:
            pass
        normalized = {
            "file_info": {
                "path": str(path),
                "format": (
                    "BAM"
                    if file_format == ".bam"
                    else ("SAM" if file_format == ".sam" else "CRAM")
                ),
                "size": size_bytes,
                "compressed": bool(compressed),
                "has_index": bool(has_index),
            },
            "metadata": {
                "version": version,
                "created_by": created_by,
                "reference_genome": None,
                "reference_genome_path": None,
            },
            "sequences": sequences,
            "sample": None,
            "format_specific": {
                "sam_header_lines": [],
                "programs": programs,
            },
        }
        return normalized

    elif file_format in (".fastq", ".fq"):
        res = inspector._inspect_fastq(filepath)
        first_records = res.get("first_records") or []
        normalized = {
            "file_info": {
                "path": str(path),
                "format": "FASTQ",
                "size": size_bytes,
                "compressed": bool(compressed),
                "has_index": bool(has_index),
            },
            "metadata": {
                "version": None,
                "created_by": None,
                "reference_genome": None,
                "reference_genome_path": None,
            },
            "sequences": [],
            "sample": next(
                (
                    rec.get("id")
                    for rec in first_records
                    if isinstance(rec, dict) and rec.get("id")
                ),
                None,
            ),
            "format_specific": {
                "fastq_preview_records": first_records,
                "total_records": res.get("total_records")
                or res.get("estimated_records"),
            },
        }
        return normalized

    elif file_format in (".fasta", ".fa", ".fas", ".fna"):
        res = inspector._inspect_fasta(filepath)
        seqs = res.get("sequences") or []
        normalized = {
            "file_info": {
                "path": str(path),
                "format": "FASTA",
                "size": size_bytes,
                "compressed": bool(compressed),
                "has_index": bool(has_index),
            },
            "metadata": {
                "version": None,
                "created_by": None,
                "reference_genome": None,
                "reference_genome_path": None,
            },
            "sequences": [
                {"name": s.get("id") or s.get("header"), "length": s.get("length")}
                for s in seqs
                if isinstance(s, dict)
            ],
            "sample": None,
            "format_specific": {
                "total_sequences": res.get("total_sequences"),
                "total_length": res.get("total_length"),
            },
        }
        return normalized

    else:
        # Unknown format from extension; attempt minimal detection via VCF header mark
        return {
            "file_info": {
                "path": str(path),
                "format": (file_format or "unknown").upper().strip("."),
                "size": size_bytes,
                "compressed": bool(compressed),
                "has_index": bool(has_index),
            },
            "metadata": {
                "version": None,
                "created_by": None,
                "reference_genome": None,
                "reference_genome_path": None,
            },
            "sequences": [],
            "sample": None,
            "format_specific": {},
        }


class GenomicHeaderInspector:
    """Unified tool for inspecting headers of various genomic file formats."""

    def __init__(self):
        self.supported_formats = {
            ".bam": self._inspect_sam_bam_cram,
            ".sam": self._inspect_sam_bam_cram,
            ".cram": self._inspect_sam_bam_cram,
            ".vcf": self._inspect_vcf_bcf,
            ".bcf": self._inspect_vcf_bcf,
            ".fastq": self._inspect_fastq,
            ".fq": self._inspect_fastq,
            ".fasta": self._inspect_fasta,
            ".fa": self._inspect_fasta,
            ".fas": self._inspect_fasta,
            ".fna": self._inspect_fasta,
        }

    def _get_file_format(self, filepath: str) -> Optional[str]:
        """Determine file format from extension, handling compressed files."""
        path = Path(filepath)

        # Handle compressed files
        if path.suffix == ".gz":
            return path.with_suffix("").suffix.lower()
        elif path.suffix == ".bz2":
            return path.with_suffix("").suffix.lower()
        else:
            return path.suffix.lower()

    def _open_file(self, filepath: str, mode: str = "r"):
        """Open file handling compression automatically."""
        if filepath.endswith(".gz"):
            return gzip.open(filepath, mode + "t")
        elif filepath.endswith(".bz2"):
            return bz2.open(filepath, mode + "t")
        else:
            return open(filepath, mode)

    def _inspect_sam_bam_cram(self, filepath: str) -> Dict:
        """Inspect SAM/BAM/CRAM headers using pysam."""
        try:
            with pysam.AlignmentFile(filepath, "r") as samfile:
                header = samfile.header.to_dict()

                result = {
                    "format": "SAM/BAM/CRAM",
                    "file": filepath,
                    "header_lines": (
                        len(samfile.text.strip().split("\n")) if samfile.text else 0
                    ),
                    "sequences": len(header.get("SQ", [])),
                    "read_groups": len(header.get("RG", [])),
                    "programs": len(header.get("PG", [])),
                    "header_dict": header,
                }

                # Add some key statistics
                if "SQ" in header:
                    total_length = sum(sq["LN"] for sq in header["SQ"] if "LN" in sq)
                    result["total_reference_length"] = total_length

                return result

        except Exception as e:
            return {"error": f"Failed to read SAM/BAM/CRAM file: {str(e)}"}

    def _inspect_vcf_bcf(self, filepath: str) -> Dict:
        """Inspect VCF/BCF headers using pysam."""
        try:
            with pysam.VariantFile(filepath) as vcf:
                header = vcf.header

                result = {
                    "format": "VCF/BCF",
                    "file": filepath,
                    "samples": list(vcf.header.samples),
                    "num_samples": len(list(vcf.header.samples)),
                    "contigs": [rec.name for rec in header.contigs],
                    "num_contigs": len(list(header.contigs)),
                    "info_fields": list(header.info.keys()),
                    "format_fields": list(header.formats.keys()),
                    "filter_fields": list(header.filters.keys()),
                    "header_records": [],
                }

                # Get header records
                for rec in header.records:
                    result["header_records"].append(str(rec))

                return result

        except Exception as e:
            return {"error": f"Failed to read VCF/BCF file: {str(e)}"}

    def _inspect_fastq(self, filepath: str) -> Dict:
        """Inspect FASTQ file (show first few records as 'header' info)."""
        try:
            result = {
                "format": "FASTQ",
                "file": filepath,
                "first_records": [],
                "total_records": 0,
            }

            if BIOPYTHON_AVAILABLE:
                with self._open_file(filepath) as handle:
                    records = SeqIO.parse(handle, "fastq")
                    for i, record in enumerate(records):
                        if i < 5:  # Show first 5 records
                            result["first_records"].append(
                                {
                                    "id": record.id,
                                    "description": record.description,
                                    "length": len(record.seq),
                                }
                            )
                        result["total_records"] = i + 1
                        if i >= 10000:  # Don't count beyond 10k for performance
                            result["total_records"] = f">{i + 1}"
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
                            result["first_records"].append(
                                {
                                    "id": lines[0],
                                    "sequence_length": len(lines[1]),
                                    "quality_length": len(lines[3]),
                                }
                            )
                        count += 4

                # Try to count total (rough estimate)
                try:
                    with self._open_file(filepath) as f:
                        line_count = sum(1 for _ in f)
                        result["estimated_records"] = line_count // 4
                except OSError:
                    result["estimated_records"] = "unknown"

            return result

        except Exception as e:
            return {"error": f"Failed to read FASTQ file: {str(e)}"}

    def _inspect_fasta(self, filepath: str) -> Dict:
        """Inspect FASTA file headers."""
        try:
            result = {
                "format": "FASTA",
                "file": filepath,
                "sequences": [],
                "total_sequences": 0,
                "total_length": 0,
            }

            if BIOPYTHON_AVAILABLE:
                with self._open_file(filepath) as handle:
                    for i, record in enumerate(SeqIO.parse(handle, "fasta")):
                        seq_info = {
                            "id": record.id,
                            "description": record.description,
                            "length": len(record.seq),
                        }

                        if i < 10:  # Show first 10 sequences
                            result["sequences"].append(seq_info)

                        result["total_length"] += len(record.seq)
                        result["total_sequences"] = i + 1

                        if i >= 10000:  # Performance limit
                            result["total_sequences"] = f">{i + 1}"
                            break
            else:
                # Fallback without BioPython
                with self._open_file(filepath) as f:
                    current_header = None
                    current_length = 0
                    seq_count = 0

                    for line in f:
                        line = line.strip()
                        if line.startswith(">"):
                            if current_header is not None:
                                if seq_count < 10:
                                    result["sequences"].append(
                                        {
                                            "header": current_header,
                                            "length": current_length,
                                        }
                                    )
                                result["total_length"] += current_length
                                seq_count += 1

                            current_header = line
                            current_length = 0
                        else:
                            current_length += len(line)

                    # Don't forget the last sequence
                    if current_header is not None:
                        if seq_count < 10:
                            result["sequences"].append(
                                {"header": current_header, "length": current_length}
                            )
                        result["total_length"] += current_length
                        seq_count += 1

                    result["total_sequences"] = seq_count

            return result

        except Exception as e:
            return {"error": f"Failed to read FASTA file: {str(e)}"}

    # Removed legacy inspect_file/print_results; normalized API is inspect_header()


def extract_raw_header_text(filepath: str) -> Optional[str]:
    """Return the raw textual header for supported formats.

    - VCF/BCF: returns the full header text (## records and #CHROM line)
    - SAM/BAM/CRAM: returns @-prefixed header lines
    - Others: returns None
    """
    try:
        # Try VCF/BCF first
        if filepath.endswith((".vcf", ".vcf.gz", ".gvcf", ".gvcf.gz", ".bcf")):
            try:
                with pysam.VariantFile(filepath) as vcf:
                    header_text = str(vcf.header)
                    return header_text
            except Exception:
                pass

        # Try SAM/BAM/CRAM
        if filepath.endswith((".sam", ".bam", ".cram")):
            try:
                with pysam.AlignmentFile(filepath, "r") as samfile:
                    return samfile.text or ""
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
    if cid.lower().startswith("chr"):
        cid = cid[3:]
    cid_upper = cid.upper()

    if cid_upper in {str(i) for i in range(1, 23)} | {"X", "Y", "M", "MT"}:
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

    vcf_contig_re = re.compile(r"^##contig=<ID=([^,>]+)")
    sam_sq_re = re.compile(r"^@SQ\s+.*?SN:([^\s\t]+)")

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

    return "\n".join(filtered_lines) + ("\n" if not header_text.endswith("\n") else "")


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
        """,
    )

    parser.add_argument("file", help="Genomic file to inspect")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Show detailed header information"
    )

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
