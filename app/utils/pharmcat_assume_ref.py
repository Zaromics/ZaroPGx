# app/utils/pharmcat_assume_ref.py
from __future__ import annotations

from typing import Optional, Union

Boolish = Union[str, bool, None]


def parse_bool(value: Boolish, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s == "":
        return default
    return s in {"1", "true", "yes", "on"}


def resolve_assume_ref_flags(
    *,
    form_absent: Boolish,
    form_unspecified: Boolish,
    env_absent: Boolish,
    env_unspecified: Boolish,
) -> tuple[bool, bool]:
    absent = (
        parse_bool(form_absent) if form_absent is not None else parse_bool(env_absent)
    )
    unspecified = (
        parse_bool(form_unspecified)
        if form_unspecified is not None
        else parse_bool(env_unspecified)
    )
    return absent, unspecified


# Inputs whose PharmCAT VCF is genotyped at every PharmCAT position by the pipeline
# itself: an alignment's is force-called from the reads (the pypgx wrapper's
# genotype_pharmcat_positions), a gVCF's comes from its own reference blocks
# (gatk-api /gvcf-to-vcf). A position missing or ./. there means "not covered", and
# the assume-reference flags could only relabel that as reference, so they are never
# applied to these. main.nf's explicit_call_input_types is the pipeline's copy of
# this list; tests/test_pharmcat_explicit_calls.py keeps the two identical.
EXPLICIT_CALL_INPUT_TYPES = frozenset({"fastq", "bam", "cram", "sam", "gvcf"})


def pharmcat_flags_for_input(
    input_type: Optional[str], absent: bool, unspecified: bool
) -> tuple[bool, bool]:
    """The assume-reference flags PharmCAT actually runs with for this input type."""
    if str(input_type or "").strip().lower() in EXPLICIT_CALL_INPUT_TYPES:
        return False, False
    return absent, unspecified


ALIGNMENT_INPUT_TYPES = frozenset({"fastq", "bam", "cram", "sam"})


def _count(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def explicit_calls_paragraph(
    input_type: Optional[str],
    requested_absent: bool,
    requested_unspecified: bool,
    positions: Optional[dict] = None,
) -> Optional[str]:
    """The report's paragraph for an input PharmCAT saw explicit calls for, or None.

    ``positions`` is the pypgx_bam2vcf step's ``pharmcat_positions`` output_data: what
    the force-call actually found, not what was planned. A gVCF's counts are already
    stated by gvcf_provenance_paragraph, so for a gVCF this only says whether flags the
    run asked for were set aside.
    """
    kind = str(input_type or "").strip().lower()
    if kind not in EXPLICIT_CALL_INPUT_TYPES:
        return None
    parts = []
    if kind in ALIGNMENT_INPUT_TYPES:
        stats = positions or {}
        total = _count(stats.get("n_pharmcat_positions"))
        if total:
            ref = _count(stats.get("n_reference")) or 0
            var = _count(stats.get("n_variant")) or 0
            no_reads = _count(stats.get("n_no_reads")) or 0
            unsure = _count(stats.get("n_uncertain")) or 0
            reads = (
                _count(stats.get("min_reference_reads"))
                or _count(stats.get("min_reference_depth"))
                or 7
            )
            parts.append(
                f"Genotypes at PharmCAT's {total:,} positions were called from the "
                f"alignment: {ref:,} reference, {var:,} variant, {no_reads:,} with no "
                f"reads and {unsure:,} uncertain. A reference call needs at least "
                f"{reads} good reads for the reference and next to none for anything "
                "else. Anything less is a no-call, and a gene with no position called is "
                "reported as not called."
            )
        else:
            parts.append(
                "Genotypes at PharmCAT's positions were called from the alignment. A "
                "position without enough reads is a no-call."
            )
    if requested_absent or requested_unspecified:
        parts.append(
            "The assume-reference flags set for this run were not applied; on this "
            "input they would only turn uncovered positions into reference calls."
        )
    if not parts:
        return None
    return "<p><strong>PharmCAT positions:</strong> " + " ".join(parts) + "</p>"


def pharmcat_cli_ref_flags(absent: bool, unspecified: bool) -> list[str]:
    if absent and unspecified:
        return ["--missing-to-ref"]
    flags: list[str] = []
    if absent:
        flags.append("--absent-to-ref")
    if unspecified:
        flags.append("--unspecified-to-ref")
    return flags


def methodology_assume_ref_paragraph(absent: bool, unspecified: bool) -> Optional[str]:
    if not absent and not unspecified:
        return None
    if absent and unspecified:
        mode = (
            "PharmCAT preprocessor flag <code>--missing-to-ref</code> "
            "(absent and unspecified PGx sites treated as homozygous reference 0/0)"
        )
    elif absent:
        mode = (
            "PharmCAT preprocessor flag <code>--absent-to-ref</code> "
            "(absent PGx sites treated as homozygous reference 0/0)"
        )
    else:
        mode = (
            "PharmCAT preprocessor flag <code>--unspecified-to-ref</code> "
            "(unspecified genotypes <code>./.</code> treated as homozygous reference 0/0)"
        )
    return (
        f"<p><strong>Assume reference when missing:</strong> This research run used {mode}. "
        "Fabricating reference calls can over-call *1/Reference and normal phenotypes; "
        "PharmCAT documents these flags as dangerous / research-oriented. "
        "Confirm assayed coverage before interpreting results.</p>"
    )
