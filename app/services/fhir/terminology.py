"""Single source of truth for the FHIR terminology ZaroPGx emits.

Every coding-system URI, StructureDefinition profile, extension URL and LOINC
code used by the resource builders in
:mod:`app.services.fhir_export_service` lives here. Nothing in this module is
computed or composed: each URI is written out in full so that ``grep`` for a URI
lands on its definition, and so that a bump to one of the HL7 Genomics Reporting
IG URLs is a one-line change in one file.

References:
- HL7 Genomics Reporting IG: https://build.fhir.org/ig/HL7/genomics-reporting/
- LOINC: https://loinc.org
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
PROFILE_GENOMICS_BUNDLE = (
    "http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/genomics-bundle"
)
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

# LOINC codes for PGx observations.
LOINC_CODES = {
    "genotype": "84413-4",  # Genotype display name
    "therapeutic_implication": "83009-1",  # Genetic variation clinical significance
    "medication_assessed": "51963-7",  # Medication assessed
    "pgx_report": "51969-4",  # Genetic analysis report
    "haplotype": "84414-2",  # Haplotype Name
    "gene_studied": "48018-6",  # Gene studied
    "phenotype": "79716-7",  # Molecular consequence
}

# Gene-specific LOINC codes (common PGx genes).
GENE_LOINC_CODES = {
    "CYP2D6": "79714-2",
    "CYP2C19": "79713-4",
    "CYP2C9": "79712-6",
    "CYP3A4": "94040-2",
    "CYP3A5": "94041-0",
    "CYP1A2": "79711-8",
    "SLCO1B1": "79717-5",
    "VKORC1": "50720-0",
    "DPYD": "98059-8",
    "TPMT": "79715-9",
    "NUDT15": "98060-6",
    "UGT1A1": "79718-3",
    "HLA-B": "81247-9",
    "HLA-A": "81248-7",
    "G6PD": "79719-1",
}
