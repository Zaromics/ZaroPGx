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
NOT_CALLED = "Not called: no phenotype or guidance follows"

UNRESOLVED = (
    "Several diplotypes fit, each with its own phenotype: see the list below the"
    ' table, and guidance marked "Applies if"'
)

# Metabolizer phenotypes: enzyme activity (Caudle et al. 2017). "Likely" (CYP2C19,
# CPIC 2022) and "Possible" (TPMT, NUDT15, CYP3A5) qualify the same terms.
_METABOLIZER = {
    # Set in the first version of the table, and accurate: kept as it was.
    "normal metabolizer": "Standard drug metabolism expected",
    "intermediate metabolizer": f"Reduced drug metabolism, between normal and poor; {RECS}",
    "likely intermediate metabolizer": (
        f"Likely reduced drug metabolism, between normal and poor; {RECS}"
    ),
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

# Function phenotypes (SLCO1B1, ABCG2), with "{what}" the transporter's or, for a
# gene not known to be one, the gene's.
_FUNCTION = {
    "normal function": "Normal {what} expected",
    "decreased function": "Decreased {what}, between normal and poor; " + RECS,
    "possible decreased function": "Possibly decreased {what}; " + RECS,
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
        f"Variant linked to aminoglycoside-induced hearing loss detected; {RECS}"
    ),
    "uncertain risk of aminoglycoside-induced hearing loss": (
        "Variant of uncertain significance for aminoglycoside-induced hearing"
        f" loss; {RECS}"
    ),
    # PyPGx's own terms for CFTR, F5 and IFNL3. It does not define them beyond the
    # name, and they mean different things for each gene: say whose they are.
    "favorable response": f"PyPGx's response category for this genotype; {RECS}",
    "unfavorable response": f"PyPGx's response category for this genotype; {RECS}",
    # The report's own labels (generator.wild_type_phenotype, NO_PHENOTYPE_STANDARD),
    # which say what the legends under the Executive Summary and table say.
    "likely wild type": "Reference genotype: no variant found at the positions tested",
    "possibly wild type": "Reference genotype where the file has data",
    "n/a": "No phenotype defined for this gene",
    "unknown": f"No phenotype was reported for this call; {RECS}",
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
    if unresolved or " or " in text:
        return UNRESOLVED
    key = text.lower()
    if key.startswith("g6pd "):
        key = key[len("g6pd ") :]
    if not key:
        key = "unknown"
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
    return phenotype_implication(
        _field(row, "phenotype"),
        _field(row, "gene"),
        bool(_field(row, "diplotype_candidates")),
    )
