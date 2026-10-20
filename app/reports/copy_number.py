"""Genes with structural-variant alleles that were called without copy number.

PyPGx calls a star allele made by a structural variant (a gene deletion, a
duplication, a hybrid with a neighbouring gene) only from the alignment's depth of
coverage, normalised against a control gene. ZaroPGx prepares those inputs only for
a GRCh38 alignment with whole-genome depth (docker/pypgx's prepare_sv_inputs).
Every other input, a VCF, BCF or gVCF, a panel or exome, a low-depth genome, gets
these genes called from small variants alone, and the call looks like one made
with copy number. On NA12878 (GeT-RM: CYP2D6 *3/*68+*4) it was *3/*4. A *1/*5
sample (Intermediate Metabolizer) or a *1/*1x2 one (Ultrarapid) reads *1/*1, Normal
Metabolizer, and that call goes to PharmCAT as an outside call. For GSTM1, GSTT1
and UGT2B17 the common null allele deletes the whole gene, so *A/*A or *1/*1 does
not say that either copy is there.

Which genes were called that way comes from PyPGx's own record. Its results fill
the CNV column (``details["copy_number"]``) only when its copy-number models ran,
and those leave cnv-calls.zip (``methods["cnv_model"]``, docker/pypgx's
pipeline_methods); without them the column is empty. Why comes from the run: the
pypgx_bam2vcf step's ``sv_calling`` record (the depth gate's decision and the
median depth it measured), else the input's type where the job recorded it. The
report says so beside each such call, in the Executive Summary's row and notes for
a gene in PharmCAT's panel, and in one alert. A whole-genome run, whose genes all
have a copy-number call, shows none of it.

Only PyPGx's calls are noted: the rows whose Call is P (app/reports/provenance.py),
its own genes and PharmCAT's rows it supplied as an outside call. A row PharmCAT
called itself (CYP2B6, CYP4F2 and G6PD unless PYPGX_PHARMCAT_PREFERENCE gives them
to PyPGx) or an outside call PyPGx did not make (OUTSIDECALLSOVERRIDE's file, a
lab's CYP2D6 *1/*5) is not PyPGx's call, and a note on it would misstate it.
"""

from __future__ import annotations

import html
from typing import Any, Dict, Iterable, List, Mapping, Optional

from app.reports.provenance import CALLED_BY_PYPGX, resolve_called_by

# What a call from small variants alone cannot show, for each of PyPGx's genes with
# SV-defined alleles: its gene table's SV and Target genes (lexicon/gene-table.csv;
# tests/test_report_copy_number.py keeps the two equal). Each lists what PyPGx 0.27's
# genotyper for the gene calls from a copy-number state (pypgx/api/genotype.py, over
# its cnv-table.csv); a state it calls Indeterminate is left out, as are *30 (CYP2B6)
# and *61 and *63 (CYP2D6), SV alleles in its allele table that no state yields. The
# hybrids are with the gene's paralog in its gene table (CYP2A7, CYP2B7, CYP2D7).
# MISSED is for the Notes to the Executive Summary; CALL_NOTE, after "Copy number
# not assessed (reason): ", for the note under the call in a narrow table column.
MISSED: Dict[str, str] = {
    "CYP2A6": "a *4 deletion, a duplication or a CYP2A7 hybrid (*12, *34)",
    "CYP2B6": "a *29 CYP2B7 hybrid or a duplication",
    "CYP2D6": "a *5 deletion, extra copies (x2, x3) or a CYP2D7 hybrid (*13, *36, *68)",
    "CYP2E1": "extra copies of the whole gene (x2, x3) or of part of it (*S1)",
    "CYP4F2": "a whole-gene deletion (*DEL)",
    "G6PD": "whether the sample has one X chromosome or two (PyPGx's MALE allele)",
    "GSTM1": "a whole-gene deletion (*0) or a duplication",
    "GSTT1": "a whole-gene deletion (*0)",
    "SLC22A2": "a noncoding (*S1) or partial (*S2) deletion",
    "SULT1A1": "a whole-gene deletion (*DEL) or extra copies (x2, x3)",
    "UGT1A4": "a noncoding deletion (*S1, *S2) or duplication (*S3)",
    "UGT2B15": "a whole-gene (*S4) or partial (*S1, *S2, *S3) deletion",
    "UGT2B17": "a whole-gene (*2) or partial (*S1, *S2, *S3) deletion",
}
CALL_NOTE: Dict[str, str] = {
    "CYP2A6": "would miss *4, duplications and hybrids",
    "CYP2B6": "would miss *29 and duplications",
    "CYP2D6": "would miss *5, duplications and hybrids",
    "CYP2E1": "would miss extra copies (*S1, x2)",
    "CYP4F2": "would miss a deletion (*DEL)",
    "G6PD": "number of X chromosomes not known",
    # Not "would miss": the call must not read as if both copies were there.
    "GSTM1": "copies may be deleted (*0) or duplicated",
    "GSTT1": "one or both copies may be deleted (*0)",
    "SLC22A2": "would miss deletions (*S1, *S2)",
    "SULT1A1": "would miss *DEL and extra copies",
    "UGT1A4": "would miss *S1, *S2 and *S3",
    "UGT2B15": "would miss deletions (*S1 to *S4)",
    "UGT2B17": "copies may be deleted, whole (*2) or in part",
}
# Genes whose common null allele deletes the whole gene (docker/pypgx's
# WHOLE_GENE_DELETION_GENES), with its name in PyPGx. Their call cannot say how many
# copies there are, and the note must not read as if both were there.
NULL_DELETION: Dict[str, str] = {"GSTM1": "*0", "GSTT1": "*0", "UGT2B17": "*2"}
# The genes PyPGx gives a phenotype (its gene table's PhenotypeMethod), which a
# missed copy-number change can move.
_PHENOTYPE_GENES = frozenset({"CYP2B6", "CYP2D6", "G6PD"})

# docker/pypgx's WGS_MIN_MEDIAN_DEPTH, for a run whose record does not carry it.
MIN_MEDIAN_DEPTH = 15.0

_VARIANT_FILES = {
    "vcf": "VCF",
    "vcf.gz": "VCF",
    "vcf.bgz": "VCF",
    "bcf": "BCF",
    "gvcf": "gVCF",
}


def genes_without_copy_number(pypgx_results: Any) -> List[str]:
    """The genes of MISSED that PyPGx called with no copy-number call, in its order.

    A gene it did not call (no diplotype, or no reads over it) has no call to qualify.
    """
    results = (
        pypgx_results.get("results") if isinstance(pypgx_results, Mapping) else None
    )
    found: List[str] = []
    if not isinstance(results, Mapping):
        return found
    for gene, result in results.items():
        name = str(gene).strip().upper()
        if name not in MISSED or not isinstance(result, Mapping):
            continue
        if not result.get("success") or not result.get("diplotype"):
            continue
        details = result.get("details")
        methods = result.get("methods")
        if (isinstance(details, Mapping) and details.get("copy_number")) or (
            isinstance(methods, Mapping) and methods.get("cnv_model")
        ):
            continue
        found.append(name)
    return found


def _depth(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def why_not_assessed(
    file_type: Optional[str], bam2vcf_output: Optional[Mapping[str, Any]]
) -> Dict[str, str]:
    """Why this run measured no copy number: {"short", "sentence", "gate"}.

    The alignment's record first: it says what happened. Then the input's type, only
    as the job recorded it (``file_type`` None when it did not: a report rebuilt
    without the job's metadata defaults to "vcf" for any input). Else only what is
    certain: no depth of coverage reached PyPGx.
    """
    record = (
        bam2vcf_output.get("sv_calling")
        if isinstance(bam2vcf_output, Mapping)
        else None
    )
    gate = MIN_MEDIAN_DEPTH
    if isinstance(record, Mapping):
        gate = _depth(record.get("min_median_depth")) or MIN_MEDIAN_DEPTH
        status = record.get("status")
        if status == "low_depth":
            # Recorded rounded down (prepare_sv_inputs), so it is never printed as
            # the gate it fell short of.
            depth = _depth(record.get("median_depth")) or 0.0
            return {
                "short": f"depth {depth:.1f}x, under {gate:g}x",
                "sentence": (
                    "The alignment's median depth at loci no pharmacogene panel "
                    f"targets was {depth:.1f}x."
                ),
                "gate": f"{gate:g}",
            }
        if status == "not_grch38":
            return {
                "short": "not a GRCh38 alignment",
                "sentence": "The alignment is not on GRCh38.",
                "gate": f"{gate:g}",
            }
        if status == "failed":
            return {
                "short": "PyPGx's depth step failed",
                "sentence": (
                    "PyPGx could not compute the alignment's depth of coverage over "
                    "these genes."
                ),
                "gate": f"{gate:g}",
            }
    label = _VARIANT_FILES.get(str(file_type or "").strip().lower())
    if label:
        return {
            "short": f"{label} input",
            "sentence": (
                f"The input was a {label}: genotypes, without the read depth across "
                "each gene that copy number is measured from."
            ),
            "gate": f"{gate:g}",
        }
    return {
        "short": "no read depth",
        "sentence": "No depth of coverage reached PyPGx in this run.",
        "gate": f"{gate:g}",
    }


def copy_number_call_note(gene: str, why: Mapping[str, str]) -> str:
    """The note under the gene's call, in a narrow table column."""
    return (
        f"Copy number not assessed ({why['short']}): {CALL_NOTE[gene.upper()]} "
        "(see Alerts)"
    )


def copy_number_note(gene: str, why: Mapping[str, str]) -> str:
    """The note on the gene in the Notes to the Executive Summary."""
    gene = gene.upper()
    note = (
        f"Copy number was not assessed ({why['short']}), so this call cannot show "
        f"{MISSED[gene]}"
    )
    if gene in NULL_DELETION:
        note += (
            f", or how many copies there are, if any: {NULL_DELETION[gene]}, the "
            "common null allele, deletes the whole gene"
        )
    elif gene in _PHENOTYPE_GENES:
        note += ", which can change the phenotype"
    return note + ". See Alerts and Warnings."


def pypgx_called(row: Mapping[str, Any]) -> bool:
    """Whether the row's call is PyPGx's: its Call letter is P."""
    return resolve_called_by(row).letter == CALLED_BY_PYPGX


def annotate_copy_number(
    genes: List[Dict[str, Any]], flagged: Iterable[str], why: Mapping[str, str]
) -> None:
    """Put the notes on each flagged gene's row whose call is PyPGx's, in place: on
    PharmCAT's row too when PyPGx supplied its call as an outside call (CYP2D6)."""
    flagged = {str(g).upper() for g in flagged}
    if not flagged:
        return
    for row in genes or []:
        if not isinstance(row, dict):
            continue
        gene = str(row.get("gene") or "").strip().upper()
        if gene in flagged and pypgx_called(row):
            row["copy_number_not_assessed"] = {
                "note": copy_number_note(gene, why),
                "call_note": copy_number_call_note(gene, why),
            }


def _gene_list(genes: List[str]) -> str:
    named = [html.escape(str(g)) for g in genes]
    return " and ".join([", ".join(named[:-1]), named[-1]] if len(named) > 1 else named)


def copy_number_alert(genes: List[str], why: Mapping[str, str]) -> Optional[str]:
    """The Alerts and Warnings entry naming the genes called without copy number, or
    None when there are none."""
    if not genes:
        return None
    return (
        f"<p>⚠️ <strong>Copy number not assessed:</strong> {_gene_list(genes)}. "
        f"{html.escape(why['sentence'])} "
        "These genes have star alleles made by a gene deletion, a duplication or a "
        "hybrid with a neighbouring gene, which PyPGx calls only from read depth. "
        "Their calls here come from small variants alone and cannot show them; the "
        "note beside each call says what it would miss. Copy number is assessed for "
        f"a GRCh38 whole-genome BAM or CRAM with a median depth of {why['gate']}x or "
        "more.</p>"
    )
