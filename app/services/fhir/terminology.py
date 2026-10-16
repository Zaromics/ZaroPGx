"""Single source of truth for the FHIR terminology ZaroPGx emits.

Every coding-system URI, StructureDefinition profile, extension URL and LOINC
code used by the resource builders in
:mod:`app.services.fhir_export_service` lives here. Nothing in this module is
computed or composed: each URI is written out in full so that ``grep`` for a URI
lands on its definition, and so that a bump to one of the HL7 Genomics Reporting
IG URLs is a one-line change in one file.

References:
- HL7 Genomics Reporting IG, STU3 (3.0.0):
  http://hl7.org/fhir/uv/genomics-reporting/STU3/
- LOINC: https://loinc.org. Every LOINC code below is sent with its official
  display name (LOINC_DISPLAYS), as LOINC's licence requires; checked against
  tx.fhir.org on 2026-10-12 and 2026-10-16.

The bundle these build validates against the IG's profiles with no errors
(HAPI FHIR 8.12 ``$validate``, IG 3.0.0 loaded, tx.fhir.org for LOINC, SNOMED CT
and HGNC; 2026-10-16). Where a value below changed for that, its comment says why.
"""

# ---------------------------------------------------------------------------
# Code systems (the "system" of a Coding)
# ---------------------------------------------------------------------------

LOINC = "http://loinc.org"
SNOMED_CT = "http://snomed.info/sct"
# HGNC as the IG's hgnc-vs names it; its codes are HGNC ids ("HGNC:2625"), the
# symbol is the display. "http://www.genenames.org/geneId" with the symbol as the
# code, sent before, is in no value set and no terminology server knows it.
HGNC = "http://www.genenames.org"
UCUM = "http://unitsofmeasure.org"

OBSERVATION_CATEGORY = "http://terminology.hl7.org/CodeSystem/observation-category"
DIAGNOSTIC_SERVICE_SECTION = "http://terminology.hl7.org/CodeSystem/v2-0074"

# "To be determined" code system the Genomics Reporting IG uses for concepts
# that have no LOINC/SNOMED equivalent yet. Its content is complete: only the codes
# in GENOMICS_TBD_DISPLAYS exist. "activity-score", "evidence-level",
# "medication-assessed" and "medication-recommendation", sent before, do not.
GENOMICS_TBD_CODES = "http://hl7.org/fhir/uv/genomics-reporting/CodeSystem/tbd-codes-cs"

# The IG's displays for the tbd codes ZaroPGx sends.
GENOMICS_TBD_DISPLAYS = {
    "therapeutic-implication": "Therapeutic Implication",
    "conclusion-string": "Conclusion Text",
}

# ZaroPGx-local identifier namespaces.
ZAROPGX_PATIENT_ID = "urn:zaropgx:patient-id"
ZAROPGX_REPORT_ID = "urn:zaropgx:report-id"

# ---------------------------------------------------------------------------
# Profiles (the "meta.profile" of a resource)
# ---------------------------------------------------------------------------

PROFILE_PATIENT = "http://hl7.org/fhir/StructureDefinition/Patient"
PROFILE_GENOMIC_REPORT = (
    "http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/genomic-report"
)
PROFILE_GENOMIC_STUDY = (
    "http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/genomic-study"
)
PROFILE_GENOTYPE = (
    "http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/genotype"
)
PROFILE_THERAPEUTIC_IMPLICATION = "http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/therapeutic-implication"
PROFILE_MEDICATION_RECOMMENDATION = "http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/medication-recommendation"

# ---------------------------------------------------------------------------
# Extension URLs (the "url" of an Extension)
# ---------------------------------------------------------------------------

EXT_RECOMMENDED_ACTION = (
    "http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/recommended-action"
)
EXT_GENOMIC_STUDY_REFERENCE = "http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/genomic-study-reference"
EXT_WORKFLOW_RELATED_ARTIFACT = (
    "http://hl7.org/fhir/StructureDefinition/workflow-relatedArtifact"
)

# ---------------------------------------------------------------------------
# LOINC codes
# ---------------------------------------------------------------------------

# LOINC codes for PGx observations. A genotype is coded as a genotype, whatever the
# gene: there are gene-specific LOINC codes, but they are interpretation panels
# ("CYP2D6 gene product metabolic activity interpretation"), not genotypes, and the
# map once here sent most genes another gene's code (CYP2D6 under CYP2C19's
# 79714-2, CYP2C9 under an HLA-A*31:01 code, five codes that do not exist).
LOINC_CODES = {
    "genotype": "84413-4",
    "medication_assessed": "51963-7",
    "pgx_report": "51969-4",
    "haplotype": "84414-2",
    "gene_studied": "48018-6",
    # The IG's evidence-level code (its implication profile's component).
    "evidence_level": "93044-6",
}

# LOINC's activity-score codes, one per gene; a gene without one gets a text code.
ACTIVITY_SCORE_LOINC = {
    "CYP2B6": "104666-3",
    "CYP2C19": "104667-1",
    "CYP2C9": "104668-9",
    "CYP2D6": "104669-7",
    "DPYD": "104665-5",
}

# Their official display names (LOINC's, as tx.fhir.org returns them).
LOINC_DISPLAYS = {
    "84413-4": "Genotype display name",
    "51963-7": "Medication assessed [ID]",
    "51969-4": "Genetic analysis report",
    "84414-2": "Haplotype name",
    "48018-6": "Gene studied [ID]",
    "93044-6": "Level of evidence",
    "104666-3": "CYP2B6 Activity Score in Blood or Tissue",
    "104667-1": "CYP2C19 Activity Score in Blood or Tissue",
    "104668-9": "CYP2C9 Activity Score in Blood or Tissue",
    "104669-7": "CYP2D6 Activity Score in Blood or Tissue",
    "104665-5": "DPYD Activity Score in Blood or Tissue",
}

# SNOMED CT 405824009, as the International Edition names it: the genomic study's
# Procedure.code. It was the Procedure's category, which the IG's genomic-study
# profile fixes to observation-category "laboratory".
SNOMED_GENETIC_TEST = ("405824009", "Genetic test")

# ---------------------------------------------------------------------------
# HGNC ids
# ---------------------------------------------------------------------------

# The HGNC id of every gene ZaroPGx reports (PharmCAT's and PyPGx's genes, HLA-A,
# -B and -C, MT-RNR1), from rest.genenames.org, all "Approved", 2026-10-16. A gene
# not listed is sent by its symbol alone.
HGNC_IDS = {
    "ABCB1": "HGNC:40",
    "ABCG2": "HGNC:74",
    "ACYP2": "HGNC:180",
    "ADRA2A": "HGNC:281",
    "ADRB2": "HGNC:286",
    "ANKK1": "HGNC:21027",
    "APOE": "HGNC:613",
    "ATM": "HGNC:795",
    "BCHE": "HGNC:983",
    "BDNF": "HGNC:1033",
    "CACNA1S": "HGNC:1397",
    "CFTR": "HGNC:1884",
    "COMT": "HGNC:2228",
    "CYP17A1": "HGNC:2593",
    "CYP19A1": "HGNC:2594",
    "CYP1A1": "HGNC:2595",
    "CYP1A2": "HGNC:2596",
    "CYP1B1": "HGNC:2597",
    "CYP26A1": "HGNC:2603",
    "CYP2A13": "HGNC:2608",
    "CYP2A6": "HGNC:2610",
    "CYP2B6": "HGNC:2615",
    "CYP2C19": "HGNC:2621",
    "CYP2C8": "HGNC:2622",
    "CYP2C9": "HGNC:2623",
    "CYP2D6": "HGNC:2625",
    "CYP2E1": "HGNC:2631",
    "CYP2F1": "HGNC:2632",
    "CYP2J2": "HGNC:2634",
    "CYP2R1": "HGNC:20580",
    "CYP2S1": "HGNC:15654",
    "CYP2W1": "HGNC:20243",
    "CYP3A4": "HGNC:2637",
    "CYP3A43": "HGNC:17450",
    "CYP3A5": "HGNC:2638",
    "CYP3A7": "HGNC:2640",
    "CYP4A11": "HGNC:2642",
    "CYP4A22": "HGNC:20575",
    "CYP4B1": "HGNC:2644",
    "CYP4F2": "HGNC:2645",
    "DBH": "HGNC:2689",
    "DPYD": "HGNC:3012",
    "DRD2": "HGNC:3023",
    "F2": "HGNC:3535",
    "F5": "HGNC:3542",
    "G6PD": "HGNC:4057",
    "GRIK1": "HGNC:4579",
    "GRIK4": "HGNC:4582",
    "GRIN2B": "HGNC:4586",
    "GSTM1": "HGNC:4632",
    "GSTP1": "HGNC:4638",
    "GSTT1": "HGNC:4641",
    "HLA-A": "HGNC:4931",
    "HLA-B": "HGNC:4932",
    "HLA-C": "HGNC:4933",
    "HTR1A": "HGNC:5286",
    "HTR2A": "HGNC:5293",
    "IFNL3": "HGNC:18365",
    "IFNL4": "HGNC:44480",
    "ITGB3": "HGNC:6156",
    "ITPA": "HGNC:6176",
    "MT-RNR1": "HGNC:7470",
    "MTHFR": "HGNC:7436",
    "NAT1": "HGNC:7645",
    "NAT2": "HGNC:7646",
    "NUDT15": "HGNC:23063",
    "OPRK1": "HGNC:8154",
    "OPRM1": "HGNC:8156",
    "POR": "HGNC:9208",
    "PTGIS": "HGNC:9603",
    "RARG": "HGNC:9866",
    "RYR1": "HGNC:10483",
    "SLC15A2": "HGNC:10921",
    "SLC22A2": "HGNC:10966",
    "SLC28A3": "HGNC:16484",
    "SLC47A2": "HGNC:26439",
    "SLC6A4": "HGNC:11050",
    "SLCO1B1": "HGNC:10959",
    "SLCO1B3": "HGNC:10961",
    "SLCO2B1": "HGNC:10962",
    "SULT1A1": "HGNC:11453",
    "TBXAS1": "HGNC:11609",
    "TPMT": "HGNC:12014",
    "UGT1A1": "HGNC:12530",
    "UGT1A4": "HGNC:12536",
    "UGT1A6": "HGNC:12538",
    "UGT2B15": "HGNC:12546",
    "UGT2B17": "HGNC:12547",
    "UGT2B7": "HGNC:12554",
    "VKORC1": "HGNC:23663",
    "XPC": "HGNC:12816",
}
