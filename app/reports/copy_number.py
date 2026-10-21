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

These notes are for PyPGx's calls: the rows whose Call is P (app/reports/
provenance.py), its own genes and PharmCAT's rows it supplied as an outside call.
An outside call PyPGx did not make (OUTSIDECALLSOVERRIDE's file, a lab's CYP2D6
*1/*5) is neither tool's, and gets no note: its maker may have measured copy number.

PharmCAT's own calls (Call C) get notes of their own, on PharmCAT's authority.
Its matcher calls star alleles from the small variants in its allele definitions,
on every input, a whole genome included, and it names the structural alleles that
leaves out (PHARMCAT_MISSED). Its own HTML report says so in a disclaimer; its
report.json, which this report is built from, does not, so the report says it
here, as for PyPGx's calls. G6PD gets no such note: PharmCAT lists no structural
G6PD allele (one X chromosome or two is its "G6PD hemizygous samples" message).
"""

from __future__ import annotations

import html
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from markupsafe import Markup

from app.reports.provenance import (
    CALLED_BY_PHARMCAT,
    CALLED_BY_PYPGX,
    resolve_called_by,
)

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
    "CYP2B6": "a *29 CYP2B7 hybrid or a duplication (and *30, which PyPGx does not call)",
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

# The structural alleles PharmCAT 3.4.0 says it cannot detect, in its own words:
# "Structural variation star alleles that cannot be detected using VCF file data:
# CYP2B7-CYP2B6 hybrids: CYP2B6*29, CYP2B6*30; Partial and whole gene deletions:
# CYP2C19*36, CYP2C19*37, CYP4F2*16, SLCO1B1*48, SLCO1B1*49" (its HTML report's
# disclaimers, org/pharmgkb/pharmcat/reporter/disclaimers.hbs). None of them has an
# allele definition in its matcher (definition/alleles/*_translation.json). All but
# CYP4F2 *16 are in its phenotype data, so could be read from an outside call;
# CYP4F2 has no phenotype data at all. PyPGx 0.27 calls CYP2B6 *29 and duplications
# and the CYP4F2 deletion (*DEL) from read depth (MISSED above); it has no
# copy-number model for CYP2C19 or SLCO1B1 (its gene table: SV FALSE), and none of
# its CYP2B6 copy-number states yields *30.
PHARMCAT_MISSED: Dict[str, str] = {
    "CYP2B6": "a CYP2B7-CYP2B6 hybrid (*29, *30)",
    "CYP2C19": "a partial or whole-gene deletion (*36, *37)",
    "CYP4F2": "a partial or whole-gene deletion (*16)",
    "SLCO1B1": "a partial or whole-gene deletion (*48, *49)",
}
PHARMCAT_CALL_NOTE: Dict[str, str] = {
    "CYP2B6": "would miss hybrids (*29, *30)",
    "CYP2C19": "would miss deletions (*36, *37)",
    "CYP4F2": "would miss a deletion (*16)",
    "SLCO1B1": "would miss deletions (*48, *49)",
}
# The same, allele names first, for the alert's list.
PHARMCAT_ALERT: Dict[str, str] = {
    "CYP2B6": "CYP2B6 *29 and *30 (hybrids with CYP2B7)",
    "CYP2C19": "CYP2C19 *36 and *37 (partial or whole-gene deletions)",
    "CYP4F2": "CYP4F2 *16 (a partial or whole-gene deletion)",
    "SLCO1B1": "SLCO1B1 *48 and *49 (partial or whole-gene deletions)",
}
# The genes whose phenotype PharmCAT's alleles above move: CYP2B6 *29 is decreased
# function and *30 no function; CYP2C19 *36 and *37 and SLCO1B1 *48 and *49 are no
# function (its phenotype data). CYP4F2 has no phenotype.
_PHARMCAT_PHENOTYPE_GENES = frozenset({"CYP2B6", "CYP2C19", "SLCO1B1"})
# PyPGx's own calls of PharmCAT's listed genes that its read depth cannot help:
# CYP2C19 and SLCO1B1, which it has no copy-number model for, and CYP2B6 *30, which
# none of its copy-number states yields (pypgx/api/genotype.py, CYP2B6Genotyper).
PYPGX_NO_MODEL = frozenset({"CYP2C19", "SLCO1B1"})
PYPGX_NO_STATE = frozenset({"CYP2B6"})

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


def pharmcat_called(row: Mapping[str, Any]) -> bool:
    """Whether the row's call is PharmCAT's own: its Call letter is C."""
    return resolve_called_by(row).letter == CALLED_BY_PHARMCAT


def pypgx_copy_number(pypgx_results: Any, gene: str) -> Optional[str]:
    """PyPGx's copy-number call for the gene in this run (its CNV column), if any."""
    results = (
        pypgx_results.get("results") if isinstance(pypgx_results, Mapping) else None
    )
    if not isinstance(results, Mapping):
        return None
    for name, result in results.items():
        if str(name).strip().upper() != gene.upper() or not isinstance(result, Mapping):
            continue
        details = result.get("details")
        if result.get("success") and isinstance(details, Mapping):
            code = str(details.get("copy_number") or "").strip()
            return code or None
    return None


# The Executive Summary's line under a noted call, by kind. A call that cannot show
# an allele (nothing measured it) or may be wrong (PyPGx measured a structural allele
# PharmCAT's call cannot show) is marked ⚠ (``warn``). A call whose copy number PyPGx
# measured is not: only CYP2B6 *30, which no copy-number state yields, is left, and
# its line is plain. A measured gene with nothing left (CYP4F2) has no line at all.
NOT_ASSESSED_MARKER = "Copy number not assessed"
FOUND_MARKER = "PyPGx found a structural allele"
STAR30_MARKER = "*30 not assessed"
# What PyPGx's normal copy number for a gene on PharmCAT's list rules out: what its
# copy-number states for the gene can show (MISSED). CYP2C19 and SLCO1B1 have none.
MEASURED_NORMAL: Dict[str, str] = {
    "CYP2B6": "no *29 hybrid or duplication",
    "CYP4F2": "no whole-gene deletion",
}
STAR30 = "*30, a hybrid with CYP2B7"


def _measured_entry(kind: str, code: Optional[str]) -> Dict[str, Any]:
    """CYP2B6 with its copy number measured: *30 is all it cannot show."""
    if kind == "measured":
        note = (
            f"PyPGx measured normal copy number ({MEASURED_NORMAL['CYP2B6']}); no "
            f"tool here calls {STAR30}. See Alerts and Warnings."
        )
    else:
        note = (
            f"None of PyPGx's copy-number states is {STAR30}, so this call cannot "
            "show it. See Alerts and Warnings."
        )
    entry = {
        "kind": kind,
        "warn": False,
        "marker": STAR30_MARKER,
        "call_note": "*30 not assessed (see Alerts)",
        "note": note,
    }
    if code:
        entry["pypgx_code"] = code
    return entry


def listed_alleles_entry(
    row: Mapping[str, Any], pypgx_results: Any = None
) -> Optional[Dict[str, Any]]:
    """The note for a call of a gene on PharmCAT's list, or None when there is
    nothing to say beside it. The kinds:

    - ``pharmcat``: PharmCAT's own call (Call C) with no copy number measured;
    - ``found``: PharmCAT's call, and PyPGx measured a copy number other than
      normal, which the call cannot show: it may be wrong;
    - ``measured``: PharmCAT's CYP2B6 call, PyPGx's copy number normal: only *30
      is left (CYP4F2 measured normal has nothing left, and no entry);
    - ``no_model``: PyPGx's call (Call P) of CYP2C19 or SLCO1B1, which it has no
      copy-number model for;
    - ``no_state``: PyPGx's CYP2B6 call with its copy number measured: *30.
    """
    gene = str(row.get("gene") or "").strip().upper()
    if gene not in PHARMCAT_MISSED:
        return None
    moves = gene in _PHARMCAT_PHENOTYPE_GENES
    if pharmcat_called(row):
        code = pypgx_copy_number(pypgx_results, gene)
        if not code:
            return {
                "kind": "pharmcat",
                "warn": True,
                "marker": NOT_ASSESSED_MARKER,
                "call_note": (
                    f"Copy number not assessed: {PHARMCAT_CALL_NOTE[gene]} (see Alerts)"
                ),
                "note": (
                    "Copy number not assessed, so this call would miss "
                    f"{PHARMCAT_MISSED[gene]}"
                    + (", which can change the phenotype" if moves else "")
                    + ". See Alerts and Warnings."
                ),
            }
        if code.strip().lower() != "normal":
            # generator imports this module, so its helper is imported here.
            from app.reports.generator import copy_number_summary

            plain = copy_number_summary(code) or "a structural variant"
            return {
                "kind": "found",
                "warn": True,
                "pypgx_code": code,
                "plain": plain,
                "marker": FOUND_MARKER,
                "call_note": f"PyPGx found {plain}, which this call cannot show "
                "(see Alerts)",
                "note": (
                    f"PyPGx found {plain}, which PharmCAT's call, from small "
                    "variants, cannot show: the call"
                    + (" and its phenotype" if moves else "")
                    + " may be wrong. See Alerts and Warnings."
                ),
            }
        if gene in PYPGX_NO_STATE:
            return _measured_entry("measured", code)
        return None
    if not pypgx_called(row):
        return None
    if gene in PYPGX_NO_MODEL:
        return {
            "kind": "no_model",
            "warn": True,
            "marker": NOT_ASSESSED_MARKER,
            "call_note": (
                "Copy number not assessed (no copy-number model): "
                f"{PHARMCAT_CALL_NOTE[gene]} (see Alerts)"
            ),
            "note": (
                "Copy number not assessed (PyPGx has no copy-number model for this "
                f"gene), so this call would miss {PHARMCAT_MISSED[gene]}"
                + (", which can change the phenotype" if moves else "")
                + ". See Alerts and Warnings."
            ),
        }
    if gene in PYPGX_NO_STATE:
        return _measured_entry("no_state", pypgx_copy_number(pypgx_results, gene))
    return None


def annotate_pharmcat_copy_number(
    genes: List[Dict[str, Any]], pypgx_results: Any = None
) -> None:
    """Note each call that cannot show an allele on PharmCAT's list
    (listed_alleles_entry), in place. A row already noted (PyPGx's call without
    copy number, MISSED) keeps that note."""
    for row in genes or []:
        if not isinstance(row, dict) or row.get("copy_number_not_assessed"):
            continue
        entry = listed_alleles_entry(row, pypgx_results)
        if entry:
            row["copy_number_not_assessed"] = dict(entry, listed=True)


def _listed(rows: Iterable[Mapping[str, Any]]) -> List[Tuple[str, Dict[str, Any]]]:
    found = []
    for row in rows or []:
        entry = (
            row.get("copy_number_not_assessed") if isinstance(row, Mapping) else None
        )
        if isinstance(entry, Mapping) and entry.get("listed"):
            found.append((str(row.get("gene") or "").upper(), dict(entry)))
    return found


def measured_normal_genes(
    rows: Iterable[Mapping[str, Any]], pypgx_results: Any = None
) -> List[str]:
    """PharmCAT's calls of the genes on its list whose copy number PyPGx measured
    in this run and found normal, in the rows' order."""
    from app.reports.generator import gene_was_called

    found = []
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        gene = str(row.get("gene") or "").strip().upper()
        if (
            gene in MEASURED_NORMAL
            and gene not in found
            and gene_was_called(row.get("diplotype"))
            and pharmcat_called(row)
        ):
            code = pypgx_copy_number(pypgx_results, gene)
            if code and code.strip().lower() == "normal":
                found.append(gene)
    return found


def _gene_list(genes: List[str], last: str = "and") -> str:
    named = [html.escape(str(g)) for g in genes]
    if len(named) < 2:
        return "".join(named)
    return f"{', '.join(named[:-1])} {last} {named[-1]}"


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


def _found_paragraph(found: List[Tuple[str, Dict[str, Any]]]) -> str:
    genes = [g for g, _ in found]
    what = "; ".join(f"{html.escape(g)}, {html.escape(e['plain'])}" for g, e in found)
    one = len(found) == 1
    text = (
        f"<p>⚠️ <strong>Structural allele found:</strong> {_gene_list(genes)}. PyPGx "
        f"measured copy number that PharmCAT's {'call' if one else 'calls'}, from "
        f"small variants, cannot show: {what}. "
        f"{'This call' if one else 'These calls'} may be wrong"
    )
    moved = [g for g in genes if g in _PHARMCAT_PHENOTYPE_GENES]
    if moved:
        text += ", and so may the phenotype"
        if not one:
            text += f" of {_gene_list(moved)}"
    return text + ".</p>"


_ALERT_PARTS = re.compile(r"^(.*) \((.*)\)$")


def _alleles(genes: List[str]) -> str:
    """PHARMCAT_ALERT's alleles for the genes, those of a kind together and the kind
    said once: "CYP2C19 *36 and *37 or SLCO1B1 *48 and *49 (partial or whole-gene
    deletions)"."""
    groups: List[Tuple[str, List[Tuple[str, str]]]] = []
    for gene in genes:
        names, kind = _ALERT_PARTS.match(PHARMCAT_ALERT[gene]).groups()
        key = "deletion" if "deletion" in kind else kind
        if groups and groups[-1][0] == key:
            groups[-1][1].append((names, kind))
        else:
            groups.append((key, [(names, kind)]))
    said = []
    for key, members in groups:
        kind = (
            members[0][1]
            if len(members) == 1
            else ("partial or whole-gene deletions" if key == "deletion" else key)
        )
        said.append(f"{_gene_list([n for n, _ in members], 'or')} ({kind})")
    return ", or ".join(said)


def _not_assessed_paragraph(entries: List[Tuple[str, Dict[str, Any]]]) -> str:
    genes = [g for g, _ in entries]
    by_kind: Dict[str, List[str]] = {}
    for g, e in entries:
        by_kind.setdefault(e["kind"], []).append(g)
    text = (
        f"<p>⚠️ <strong>Structural alleles not assessed:</strong> "
        f"{_gene_list(genes)}."
    )
    pharmcats = by_kind.get("pharmcat", [])
    if pharmcats:
        who = (
            ("them" if len(genes) > 1 else "it")
            if len(pharmcats) == len(genes)
            else _gene_list(pharmcats)
        )
        text += (
            f" PharmCAT called {who} from small variants, as it does on every input, "
            "a whole genome included."
        )
    if by_kind.get("no_model"):
        models = by_kind["no_model"]
        text += (
            f" PyPGx called {_gene_list(models)}, and has no copy-number model for "
            f"{'it' if len(models) == 1 else 'them'}."
        )
    text += (
        f" {'These calls' if len(genes) > 1 else 'This call'} cannot show "
        f"{_alleles(genes)}, structural alleles PharmCAT lists as undetectable from "
        "a VCF."
    )
    moved = [g for g in genes if g in _PHARMCAT_PHENOTYPE_GENES]
    if moved:
        text += " A missed allele can change the phenotype"
        if len(moved) < len(genes):
            text += f" of {_gene_list(moved)}"
        text += "."
    if any(g in pharmcats for g in MEASURED_NORMAL):
        text += (
            " PyPGx measures copy number for CYP2B6 (not *30) and CYP4F2 from a "
            "GRCh38 whole-genome BAM or CRAM, unless PYPGX_PHARMCAT_PREFERENCE is "
            "pharmcat; no tool here measures it for CYP2C19 or SLCO1B1."
        )
    else:
        unmodelled = [g for g in pharmcats if g in PYPGX_NO_MODEL]
        if unmodelled:
            text += (
                " No tool here measures copy number for "
                f"{_gene_list(unmodelled, 'or')}."
            )
    return text + "</p>"


def _measured_paragraph(measured: List[str], star30: Optional[str]) -> str:
    """PyPGx's normal copy number for PharmCAT's calls, said once, and CYP2B6 *30.
    ``star30``: "measured" (PharmCAT's CYP2B6, measured normal) or "no_state"
    (PyPGx's own CYP2B6 call), or None."""
    parts = []
    if measured:
        parts.append(
            "PyPGx measured normal copy number for "
            + _gene_list([f"{g} ({MEASURED_NORMAL[g]})" for g in measured])
            + "."
        )
    if star30 == "no_state":
        parts.append(
            f"PyPGx measured CYP2B6's copy number, but none of its copy-number states "
            f"is {STAR30}, so its call cannot show it."
        )
    elif star30:
        parts.append(f"No tool here calls CYP2B6 {STAR30}.")
    return " ".join(parts)


def pharmcat_copy_number_alert(
    rows: Iterable[Mapping[str, Any]], pypgx_results: Any = None
) -> Optional[str]:
    """The Alerts and Warnings entry for the genes on PharmCAT's list
    (listed_alleles_entry): one box, or None when there is nothing to say.

    A structural allele PyPGx found comes first, then the calls nothing measured
    (⚠, named in the heading), then, once and plainly, the genes whose copy
    number PyPGx measured and found normal, and CYP2B6 *30, which nothing calls."""
    rows = [r for r in rows or [] if isinstance(r, Mapping)]
    entries = _listed(rows)
    found = [(g, e) for g, e in entries if e["kind"] == "found"]
    unassessed = [(g, e) for g, e in entries if e["kind"] in ("pharmcat", "no_model")]
    star30 = next(
        (e["kind"] for g, e in entries if e["kind"] in ("measured", "no_state")),
        None,
    )
    measured = measured_normal_genes(rows, pypgx_results)
    paragraphs = []
    if found:
        paragraphs.append(_found_paragraph(found))
    if unassessed:
        paragraphs.append(_not_assessed_paragraph(unassessed))
    tail = _measured_paragraph(measured, star30)
    if tail:
        if paragraphs:
            paragraphs.append(f"<p>{tail}</p>")
        else:
            paragraphs.append(f"<p><strong>Structural alleles:</strong> {tail}</p>")
    return "".join(paragraphs) or None


# PyPGx's positions note (docker/pypgx's positions_read_as_reference_note).
_POSITIONS = re.compile(
    r"(\d+) of (\d+) positions had (.+?) and were read as reference\.?$"
)


def positions_summary_note(rows: Iterable[Mapping[str, Any]]) -> Dict[str, str]:
    """One entry for the Notes to the Executive Summary for several calls with
    positions read as reference: {"genes", "note"}. One note a gene ran the notes
    past their page on a run where PyPGx called every gene; grouped by reason, each
    gene gives its count ("CFTR 5 of 93")."""
    groups: Dict[str, List[str]] = {}
    other: List[str] = []
    genes: List[str] = []
    for row in rows or []:
        gene = str(row.get("gene") or "")
        note = str(row.get("positions_note") or "").strip()
        if not gene or not note:
            continue
        genes.append(gene)
        match = _POSITIONS.match(note)
        if match:
            unread, total, why = match.groups()
            groups.setdefault(html.escape(why), []).append(
                f"{html.escape(gene)} {unread} of {total}"
            )
        else:
            other.append(f"{html.escape(gene)}: {html.escape(note)}")
    parts = [
        f"{', '.join(counts)} positions had {why} and were read as reference"
        for why, counts in groups.items()
    ] + other
    # Escaped here and marked safe, so the template does not escape it twice.
    return {
        "genes": Markup(_gene_list(genes)),
        "note": Markup(
            "; ".join(parts)
            + ", so a variant there would not have been seen and these calls may be "
            "wrong. Genetic Results notes each beside its call."
        ),
    }
