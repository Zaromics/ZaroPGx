"""How a bundle's genotypes were obtained, as the Observation's ``method``.

Before this, nothing about the input reached the FHIR export. The report page says it
plainly -- that a 23andMe export carried about a fifth of the rs-identified positions in
PharmCAT's list, that a gVCF was genotyped by GATK and how many positions it covered,
that a VCF was lifted from GRCh37 and how many variants that dropped, whether absent
positions were assumed to be reference -- and the bundle said none of it. A genotype
from a consumer chip and one from a 30x genome arrived at a receiving system looking
the same.

**Why ``Observation.method``.** It is the element R4 gives for "the mechanism used to
perform the observation", and the Genomics Reporting IG (3.0.0, the latest published,
checked 2026-11-17) leaves it on the genotype and variant profiles with R4's example
binding, so a coding and a text are both allowed. Every Observation measured from the
input carries it: the genotypes and the variants. The implications are interpretations
of those, not measurements, and do not.

**The coding** is from the IG's own Genomic Study Method Type CodeSystem
(``genomic-study-method-type-cs``, content complete), and only where the input type
settles it: reads, alignments and gVCFs are sequencing (``ngs-mps``; a gVCF's reference
blocks are written by a read-based caller), and a 23andMe or AncestryDNA export is a
``microarray``. A VCF or BCF gets **no coding**: a VCF can come from a chip as easily as
from a sequencer, the file does not say which, and a code would assert what nobody
measured. Its text says what is known instead -- that the calls are the submitting
caller's.

**The text** is the facts that change what a genotype means, from the same step rows
the report's paragraphs read (``array_to_vcf``, ``gvcf_to_vcf``, ``liftover``), so the
page and the bundle cannot be told different things. Written for a receiving system,
not for the patient: no HTML and no "your file". A count the step did not record is
left out, never rendered as zero.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from app.services.fhir import terminology

#: The input types the method coding can be settled from.
_SEQUENCING = {"bam", "cram", "sam", "fastq", "gvcf"}
_ARRAY = {"23andme", "ancestry"}

_VENDOR = {
    "23andme": "23andMe",
    "ancestrydna": "AncestryDNA",
    "ancestry": "AncestryDNA",
}


def _count(value: Any) -> Optional[int]:
    """A non-negative int, or None. bool is an int subclass and is not a count."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _array_clauses(file_type: str, step: Mapping[str, Any]) -> List[str]:
    vendor = _VENDOR.get(
        str(step.get("vendor_shape") or file_type).strip().lower(), "consumer"
    )
    clauses = [
        f"{vendor} genotyping array export, read onto PharmCAT's GRCh38 positions "
        "by rs identifier"
    ]
    carried = _count(step.get("n_positions_carried"))
    available = _count(step.get("n_pharmcat_positions_rsid"))
    if carried is not None and available:
        clauses.append(
            f"{carried:,} of the {available:,} rs-identified positions in "
            "PharmCAT's list present"
        )
    clauses.append("no sequencing reads, so no copy-number or structural calls")
    return clauses


def _gvcf_clauses(step: Mapping[str, Any]) -> List[str]:
    clauses = [
        "Sequencing gVCF, genotyped at PharmCAT's positions by GATK GenotypeGVCFs"
    ]
    called = _count(step.get("n_pgx_positions_called"))
    total = _count(step.get("n_pharmcat_positions"))
    if called is not None and total:
        clauses.append(f"{called:,} of {total:,} positions carried a call")
    build = str(step.get("positions_expressed_in") or "").strip()
    if build:
        clauses.append(
            f"genotyped in {build} and lifted back to GRCh38, PharmCAT's own build"
        )
    return clauses


def _reads_clauses(file_type: str) -> List[str]:
    if file_type == "fastq":
        return [
            "Sequencing reads (FASTQ), aligned to GRCh38 with bwa-mem and genotyped "
            "at PharmCAT's positions from the reads"
        ]
    return [
        f"Aligned sequencing reads ({file_type.upper()}), genotyped at PharmCAT's "
        "positions from the reads"
    ]


def _variant_call_clauses(
    file_type: str,
    absent_to_ref: Optional[bool],
    unspecified_to_ref: Optional[bool],
) -> List[str]:
    clauses = [
        f"Variant calls supplied as {file_type.upper()}; the genotypes are the "
        "submitting caller's"
    ]
    if absent_to_ref is True:
        clauses.append(
            "positions absent from the file were assumed reference (PharmCAT "
            "--absent-to-ref)"
        )
    elif absent_to_ref is False:
        clauses.append("positions absent from the file were not assumed reference")
    if unspecified_to_ref:
        clauses.append(
            "positions the file wrote as no-calls were assumed reference (PharmCAT "
            "--unspecified-to-ref)"
        )
    return clauses


def _liftover_clause(step: Mapping[str, Any]) -> Optional[str]:
    source = str(step.get("source_build") or "").strip() or "GRCh37"
    target = str(step.get("target_build") or "").strip() or "GRCh38"
    lifted = _count(step.get("n_lifted"))
    rejected = _count(step.get("n_rejected"))
    if lifted is None and rejected is None:
        return f"lifted over from {source} to {target}"
    parts = [
        f"{n:,} {what}"
        for n, what in ((lifted, "lifted"), (rejected, "dropped as unliftable"))
        if n is not None
    ]
    return f"lifted over from {source} to {target} ({', '.join(parts)})"


def method_concept(data: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """The CodeableConcept for ``Observation.method``, or None where the input is not
    known (a bundle rebuilt from PharmCAT's database rows carries no input type)."""
    facts = (
        data.get("input_method")
        if isinstance(data.get("input_method"), Mapping)
        else {}
    )
    file_type = (
        str(facts.get("file_type") or data.get("file_type") or "").strip().lower()
    )
    if not file_type or file_type == "unknown":
        return None

    def step(name: str) -> Mapping[str, Any]:
        value = facts.get(name)
        return value if isinstance(value, Mapping) else {}

    if file_type in _ARRAY:
        clauses, code = _array_clauses(file_type, step("array")), "microarray"
    elif file_type == "gvcf":
        clauses, code = _gvcf_clauses(step("gvcf")), "ngs-mps"
    elif file_type in _SEQUENCING:
        clauses, code = _reads_clauses(file_type), "ngs-mps"
    elif file_type in {"vcf", "bcf"}:
        clauses = _variant_call_clauses(
            file_type, facts.get("absent_to_ref"), facts.get("unspecified_to_ref")
        )
        code = None
    else:
        return None

    if step("liftover"):
        lift = _liftover_clause(step("liftover"))
        if lift:
            clauses.append(lift)

    concept: Dict[str, Any] = {"text": "; ".join(clauses) + "."}
    if code:
        concept["coding"] = [
            {
                "system": terminology.GENOMIC_STUDY_METHOD_TYPE,
                "code": code,
                "display": terminology.GENOMIC_STUDY_METHOD_TYPE_DISPLAYS[code],
            }
        ]
    return concept
