"""The Implications column: what a call's phenotype means, in one line.

The PDF/HTML gene table, the interactive report's Genes table, its Summary cards
and its gene panels all say this, and each chose its line by matching words in
the phenotype. Only metabolizer phenotypes had a line of their own; the rest fell
to "Consult clinical guidelines". So SLCO1B1 *1/*15, Decreased Function, read
"Consult clinical guidelines", while its Normal Function (and ABCG2's, and G6PD's
"Normal") read "Standard drug metabolism expected": a transporter and an enzyme
that metabolizes no drug, said to metabolize them.

Each line says what the phenotype means, as CPIC defines the term (Caudle et al.
2017, "Standardizing terms for clinical pharmacogenetic test results", Genet Med
19:215; CPIC's later guidelines for the terms that paper has not got: "Likely",
"Possible", G6PD, MT-RNR1, RYR1/CACNA1S), and points to the drug recommendations
for what follows from it. Which drug, which dose: those differ by drug, and the
recommendations say them. No line names a consequence of its own.

The phenotypes covered are every value the stack can put in the column:

- PharmCAT 3.4.0's, from its phenotype data (org/pharmgkb/pharmcat/phenotype/
  <GENE>.json, "generesult"), HLA-A/HLA-B's "<allele> positive|negative", and
  MT-RNR1's;
- PyPGx 0.27's (pypgx/api/data diplotype-table.csv and equation-table.csv; the
  phenotype list is lexicon/phenotype-table.csv), which spells some differently
  ("G6PD Deficient", "Increased Risk of Aminoglycoside-Induced Hearing Loss") and
  has two of its own ("Favorable Response", "Unfavorable Response");
- the report's own: "Possibly/Likely Wild Type" (generator.wild_type_phenotype),
  "N/A" (generator.NO_PHENOTYPE_STANDARD), "Unknown", and an unresolved call's
  "A or B".

tests/test_report_implications.py checks every one of them gets a line of its
own, not FALLBACK.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Optional

# Short: the PDF's Implications column holds about 24 characters a line.
RECS = "see recommendations"

# Genes whose "Function" phenotypes are a transporter's (CPIC's transporter terms,
# Caudle et al. 2017 Table 1). PharmCAT and PyPGx give these terms to no other gene.
TRANSPORTER_GENES = frozenset({"ABCG2", "SLCO1B1"})

# Only for a value no source above produces: a pointer, not "Consult clinical
# guidelines". The tests fail if a known phenotype reaches it.
FALLBACK = "See recommendations"

# A gene the run did not call: the interactive report tables every gene, and the
# PDF says the same of these under "Genes examined without a call".
NOT_CALLED = "Not called: phenotype unknown, so no genotype-based guidance"

# PyPGx's "Indeterminate" diplotype: it could not settle the genotype.
NOT_DETERMINED = "Genotype not determined; no phenotype follows"

# An unphased call PharmCAT could not narrow to one diplotype. report_json gives
# the call one phenotype when every candidate has it, and "A or B" when not.
UNRESOLVED = (
    "Several diplotypes fit, with different phenotypes: see the list below the"
    ' table, and guidance marked "Applies if"'
)
SHARED = "Every diplotype that fits has this phenotype: "

# Metabolizer phenotypes: enzyme activity (Caudle et al. 2017). "Likely" (CYP2C19,
# CPIC 2022) and "Possible" (TPMT, NUDT15, CYP3A5) qualify the same terms.
_METABOLIZER = {
    # Set in the first version of the table, and accurate: kept as it was.
    "normal metabolizer": "Standard drug metabolism expected",
    "intermediate metabolizer": f"Reduced drug metabolism, between normal and poor; {RECS}",
    "likely intermediate metabolizer": (
        f"Likely reduced drug metabolism, between normal and poor; {RECS}"
    ),
    # By gene below (_POSSIBLE_INTERMEDIATE); this for any other.
    "possible intermediate metabolizer": f"Possibly reduced drug metabolism; {RECS}",
    "poor metabolizer": f"Little to no drug metabolism by this enzyme; {RECS}",
    "likely poor metabolizer": (
        f"Likely little to no drug metabolism by this enzyme; {RECS}"
    ),
    "rapid metabolizer": f"Increased drug metabolism, above normal; {RECS}",
    "ultrarapid metabolizer": (
        f"Increased drug metabolism, above a rapid metabolizer's; {RECS}"
    ),
}

# CPIC's "Possible Intermediate Metabolizer" differs by gene (api.cpicpgx.org
# gene_result). TPMT: one no-function allele and one of uncertain, unknown or
# decreased function, "not likely to be any higher than that of an intermediate
# metabolizer ... may be even lower"; NUDT15: one no-function and one
# uncertain-function allele, which by the same reasoning is likely bounded the
# same way (CPIC's NUDT15 text states no bound). CYP3A5: one normal-function allele
# and one of uncertain or unknown function (*1/*8).
_POSSIBLE_INTERMEDIATE = {
    "TPMT": f"Reduced drug metabolism, likely no higher than intermediate, possibly lower; {RECS}",
    "NUDT15": f"Reduced drug metabolism, likely no higher than intermediate, possibly lower; {RECS}",
    "CYP3A5": (
        "Possibly reduced drug metabolism (one allele's function uncertain or"
        f" unknown); {RECS}"
    ),
}

# Function phenotypes (SLCO1B1, ABCG2), with "{what}" the transporter's or, for a
# gene not known to be one, the gene's.
_FUNCTION = {
    "normal function": "Normal {what} expected",
    "decreased function": "Decreased {what}, between normal and poor; " + RECS,
    # CPIC (SLCO1B1): one no-function allele and one of uncertain or unknown
    # function.
    "possible decreased function": (
        "Possibly decreased {what} (one no-function allele, one of uncertain or"
        " unknown function); " + RECS
    ),
    "poor function": "Little to no {what}; " + RECS,
    "increased function": "Increased {what}, above normal; " + RECS,
}

# Everything else, by the phenotype's own words (lower-cased; PyPGx's "G6PD "
# prefix removed first).
_OTHER = {
    # G6PD (CPIC 2022). PharmCAT's alleles: Normal is WHO class IV on each X;
    # Deficient, class II or III; Deficient with CNSHA, class I; Variable, one
    # deficient and one normal allele, whose activity X inactivation decides.
    "normal": "Normal G6PD activity expected",
    "deficient": f"Deficient G6PD activity; {RECS}",
    "deficient with cnsha": (
        "Deficient G6PD activity, of the class linked to chronic nonspherocytic"
        f" hemolytic anemia (CNSHA); {RECS}"
    ),
    "variable": (
        "G6PD activity may range from normal to deficient (one deficient allele);"
        f" {RECS}"
    ),
    # CPIC: one or both alleles of uncertain or unknown function.
    "indeterminate": f"No phenotype: an allele's function is uncertain or unknown; {RECS}",
    # RYR1, CACNA1S (CPIC 2018).
    # PharmCAT's Uncertain Susceptibility includes a variant of uncertain function.
    "malignant hyperthermia susceptibility": (
        f"Pathogenic malignant hyperthermia variant detected; {RECS}"
    ),
    "uncertain susceptibility": (
        "No pathogenic malignant hyperthermia variant detected; susceptibility not"
        " ruled out"
    ),
    # CFTR (CPIC ivacaftor). PharmCAT's one non-responsive diplotype is
    # Reference/Reference.
    "ivacaftor responsive in cf patients": (
        f"Ivacaftor-responsive variant detected; relevant in cystic fibrosis ({RECS})"
    ),
    "ivacaftor non-responsive in cf patients": (
        "No ivacaftor-responsive variant detected; relevant only in cystic fibrosis"
    ),
    # MT-RNR1 (CPIC 2021). PharmCAT lower-cases these; PyPGx capitalises them.
    "normal risk of aminoglycoside-induced hearing loss": (
        "No variant linked to aminoglycoside-induced hearing loss detected"
    ),
    "increased risk of aminoglycoside-induced hearing loss": (
        "Variant conferring increased risk of aminoglycoside-induced hearing loss"
        f" detected; {RECS}"
    ),
    "uncertain risk of aminoglycoside-induced hearing loss": (
        "Variant of uncertain significance for aminoglycoside-induced hearing"
        f" loss; {RECS}"
    ),
    # PyPGx's own terms (_RESPONSE below by gene), for a gene not there.
    "favorable response": f"PyPGx's response category for this genotype; {RECS}",
    "unfavorable response": f"PyPGx's response category for this genotype; {RECS}",
    # The report's own labels (generator.wild_type_phenotype, NO_PHENOTYPE_STANDARD),
    # which say what the legends under the Executive Summary and table say.
    "likely wild type": "No variant found at the positions tested",
    "possibly wild type": "Reference genotype where the file has data",
    "n/a": "No phenotype defined for this gene",
    "unknown": f"No phenotype was reported for this call; {RECS}",
}

# PyPGx 0.27's "Favorable/Unfavorable Response", which it gives CFTR, F5 and IFNL3
# (pypgx/api/data/diplotype-table.csv): CFTR favorable, an ivacaftor-responsive
# variant on either copy, unfavorable only F508del/F508del; F5 favorable only
# Reference/Reference, unfavorable a Factor V Leiden copy; IFNL3 favorable only
# Reference/Reference (rs12979860 CC, CPIC's favorable response genotype),
# unfavorable a rs12979860 T copy (CT or TT, CPIC's unfavorable one).
_RESPONSE = {
    ("CFTR", "favorable response"): (
        f"Ivacaftor-responsive variant detected; relevant in cystic fibrosis ({RECS})"
    ),
    ("CFTR", "unfavorable response"): (
        "No ivacaftor-responsive variant detected (F508del on both copies);"
        " relevant only in cystic fibrosis"
    ),
    # PyPGx gives CFTR Reference/Reference and Reference/F508del "Indeterminate";
    # neither carries an ivacaftor-responsive variant.
    ("CFTR", "indeterminate"): (
        "No ivacaftor-responsive variant detected; relevant only in cystic fibrosis"
    ),
    ("F5", "favorable response"): "Factor V Leiden not detected",
    ("F5", "unfavorable response"): f"Factor V Leiden detected; {RECS}",
    ("IFNL3", "favorable response"): (
        f"rs12979860 CC, CPIC's favorable response genotype; {RECS}"
    ),
    ("IFNL3", "unfavorable response"): (
        f"rs12979860 T carried, CPIC's unfavorable response genotype; {RECS}"
    ),
}

# HLA-A/HLA-B: "*57:01 negative", "*15:02 negative, *57:01 positive, ...".
# CPIC: positive, a high-risk allele detected (one or two copies); negative, not.
_ALLELE_RESULT = re.compile(r"(\*\d+:\d+)\s+(positive|negative)\b", re.IGNORECASE)


# PharmCAT's phenotypes.tsv (lexicon/phenotypes.tsv) lists VKORC1's lookup keys,
# "-1639 AG" and the like, as its CPIC phenotypes. 3.4 reports VKORC1 with none
# (generator.PHARMCAT_GENES_WITHOUT_PHENOTYPE), but a key is a genotype if it shows.
_VKORC1_KEY = re.compile(r"-1639 [ACGT]{2}")


def _hla(text: str) -> Optional[str]:
    results = _ALLELE_RESULT.findall(text)
    if not results or _ALLELE_RESULT.sub("", text).strip(" ,;"):
        return None
    positive = [allele for allele, result in results if result.lower() == "positive"]
    if positive:
        return f"High-risk allele detected ({', '.join(positive)}); {RECS}"
    if len(results) == 1:
        return "High-risk allele not detected"
    return "None of the high-risk alleles screened for was detected"


def phenotype_implication(
    phenotype: Any, gene: Any = None, unresolved: bool = False
) -> str:
    """The Implications line for a call's phenotype."""
    text = re.sub(r"\s+", " ", str(phenotype or "")).strip()
    if " or " in text:
        return UNRESOLVED
    if unresolved:
        # Candidates, one phenotype: what that phenotype means, said of them all.
        line = phenotype_implication(text, gene)
        if text.lower() in ("", "n/a", "unknown", "indeterminate"):
            # No phenotype to share: the line holds for each candidate as it is.
            return line
        if line[1:2].isupper() or line[1:2].isdigit():
            # A line opening with a symbol or code ("G6PD activity ...") keeps its case.
            return SHARED + line
        return SHARED + line[0].lower() + line[1:]
    key = text.lower()
    gene_key = str(gene or "").strip().upper()
    if key.startswith("g6pd "):
        key = key[len("g6pd ") :]
    if not key:
        key = "unknown"
    if (
        key == "possible intermediate metabolizer"
        and gene_key in _POSSIBLE_INTERMEDIATE
    ):
        return _POSSIBLE_INTERMEDIATE[gene_key]
    if (gene_key, key) in _RESPONSE:
        return _RESPONSE[(gene_key, key)]
    if key in _METABOLIZER:
        return _METABOLIZER[key]
    if key in _FUNCTION:
        is_transporter = str(gene or "").strip().upper() in TRANSPORTER_GENES
        what = "transporter function" if is_transporter else "function"
        line = _FUNCTION[key].format(what=what)
        return line[0].upper() + line[1:]
    if key in _OTHER:
        return _OTHER[key]
    if _VKORC1_KEY.fullmatch(text):
        return f"Genotype at VKORC1 -1639G>A (rs9923231); {RECS}"
    return _hla(text) or FALLBACK


def _field(row: Any, name: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(name)
    return getattr(row, name, None)


def row_implication(row: Any) -> str:
    """The Implications line for a gene-table row (a filter: ``d|implication``)."""
    diplotype = _field(row, "diplotype")
    # generator.gene_was_called, which imports this module: "Unknown" or
    # "Unknown/Unknown", or nothing, is no call.
    if diplotype is not None and (
        not str(diplotype).strip()
        or str(diplotype).strip().lower().startswith("unknown")
    ):
        return NOT_CALLED
    if str(diplotype or "").strip().lower() == "indeterminate":
        return NOT_DETERMINED
    return phenotype_implication(
        _field(row, "phenotype"),
        _field(row, "gene"),
        bool(_field(row, "diplotype_candidates")),
    )


# The gene tables' line for an N/A phenotype. "No phenotype defined for this gene"
# filled the Implications column of 40 rows on a whole genome's report, under a
# legend that says what N/A means; a dash says there is no implication to give.
NO_IMPLICATION = "\u2013"


def table_implication(row: Any) -> str:
    """The Implications cell of a gene-table row (a filter: ``d|table_implication``):
    row_implication, but a dash for a call the tool defines no phenotype for. The
    interactive report's gene cards, which have no legend beside them, keep
    row_implication's words."""
    if (
        re.sub(r"\s+", " ", str(_field(row, "phenotype") or "")).strip().lower()
        == "n/a"
    ):
        diplotype = str(_field(row, "diplotype") or "").strip().lower()
        if diplotype and not diplotype.startswith("unknown"):
            return NO_IMPLICATION
    return row_implication(row)
