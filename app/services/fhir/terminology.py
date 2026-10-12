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
  tx.fhir.org on 2026-10-12.
"""

# ---------------------------------------------------------------------------
# Code systems (the "system" of a Coding)
# ---------------------------------------------------------------------------

LOINC = "http://loinc.org"
SNOMED_CT = "http://snomed.info/sct"
RXNORM = "http://www.nlm.nih.gov/research/umls/rxnorm"
PHARMVAR = "http://www.pharmvar.org"
HGNC_GENE_ID = "http://www.genenames.org/geneId"
UCUM = "http://unitsofmeasure.org"

OBSERVATION_CATEGORY = "http://terminology.hl7.org/CodeSystem/observation-category"
DIAGNOSTIC_SERVICE_SECTION = "http://terminology.hl7.org/CodeSystem/v2-0074"

# "To be determined" code system the Genomics Reporting IG uses for concepts
# that have no LOINC/SNOMED equivalent yet (therapeutic-implication,
# activity-score, evidence-level, ...).
GENOMICS_TBD_CODES = "http://hl7.org/fhir/uv/genomics-reporting/CodeSystem/tbd-codes-cs"

# ZaroPGx-local identifier namespaces.
ZAROPGX_PATIENT_ID = "urn:zaropgx:patient-id"
ZAROPGX_REPORT_ID = "urn:zaropgx:report-id"
ZAROPGX_CONCLUSION_CODES = "urn:zaropgx:conclusion-codes"

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
}

# Their official display names (LOINC's, as tx.fhir.org returns them).
LOINC_DISPLAYS = {
    "84413-4": "Genotype display name",
    "51963-7": "Medication assessed [ID]",
    "51969-4": "Genetic analysis report",
    "84414-2": "Haplotype name",
    "48018-6": "Gene studied [ID]",
}

# SNOMED CT 405824009, as the International Edition names it.
SNOMED_GENETIC_TEST = ("405824009", "Genetic test")
