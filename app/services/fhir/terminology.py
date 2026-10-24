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
# The IG's systems for a variant's names (its examples, 3.0.0): HGVS expressions,
# RefSeq accessions and ClinVar's variation ids.
HGVS = "http://varnomen.hgvs.org"
REFSEQ = "http://www.ncbi.nlm.nih.gov/refseq"
CLINVAR = "http://www.ncbi.nlm.nih.gov/clinvar"

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
    "diagnostic-implication": "Diagnostic Implication",
    "conclusion-string": "Conclusion Text",
}

# HL7's DataAbsentReason code system (HL7 Terminology), with its displays for the
# codes ZaroPGx sends: why a gene's genotype holds no call. The IG's genotype profile
# requires a value (1..1) and obs-6 forbids dataAbsentReason beside one, so the
# reason goes in the value, as a coding of it: R4 Observation.dataAbsentReason's
# comment allows exceptional values "in the value". This follows US Core's pattern
# for a coded element whose binding is example, preferred or extensible (this one's
# is example): a DataAbsentReason code in the CodeableConcept. US Core's Missing
# Data rule sends it only where there is neither text nor code; here it sits
# beside the text, which a reader still sees.
DATA_ABSENT_REASON = "http://terminology.hl7.org/CodeSystem/data-absent-reason"
DATA_ABSENT_REASON_DISPLAYS = {
    # "The value is not available because the observation procedure (test, etc.)
    # was not performed": nothing tested the gene.
    "not-performed": "Not Performed",
    # "Some system or workflow process error means that the information is not
    # available": the tool that tried the gene failed.
    "error": "Error",
    # "The value is expected to exist but is not known": a tool tested the gene
    # and could not call it.
    "unknown": "Unknown",
}

# ZaroPGx-local identifier namespaces.
ZAROPGX_PATIENT_ID = "urn:zaropgx:patient-id"
ZAROPGX_REPORT_ID = "urn:zaropgx:report-id"

# An identifier whose value is a URI (here the resource's urn:uuid), RFC 3986.
URI_IDENTIFIER = "urn:ietf:rfc:3986"

# The tag a push puts on every resource of a job but the Patient (meta.tag), so a
# later push of the job can find and delete what its bundle no longer holds.
ZAROPGX_JOB_TAG = "urn:zaropgx:job-id"

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
PROFILE_VARIANT = "http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/variant"
PROFILE_DIAGNOSTIC_IMPLICATION = "http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/diagnostic-implication"
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

# The variant profile's components and the diagnostic implication's clinical
# significance, for a variant no allele definition lists (app/reports/unlisted.py).
VARIANT_LOINC = {
    "variant": "69548-6",
    "assembly": "62374-4",
    "chromosome": "48000-4",
    "start_end": "81254-5",
    "coordinate_system": "92822-6",
    "ref_allele": "69547-8",
    "alt_allele": "69551-0",
    "coding_hgvs": "48004-6",
    "transcript": "51958-7",
    "protein_hgvs": "48005-3",
    "allelic_state": "53034-5",
    "source_class": "48002-0",
    "variation_code": "81252-9",
    "allelic_read_depth": "82121-5",
    "allelic_frequency": "81258-6",
    "clinical_significance": "53037-8",
    "predicted_phenotype": "81259-4",
}
# Their answers: present, GRCh38, 1-based, germline, the allelic states, the clinical
# significances (LL4034-6), and the chromosomes (LL2938-0).
VARIANT_ANSWERS = {
    "present": "LA9633-4",
    "GRCh38": "LA26806-2",
    "one_based": "LA30102-0",
    "germline": "LA6683-2",
    "heterozygous": "LA6706-1",
    "homozygous": "LA6705-3",
    "hemizygous": "LA6707-9",
    "pathogenic": "LA6668-3",
    "likely_pathogenic": "LA26332-9",
    "uncertain": "LA26333-7",
}
CHROMOSOME_ANSWERS = {
    "chr1": "LA21254-0", "chr2": "LA21255-7", "chr3": "LA21256-5", "chr4": "LA21257-3",
    "chr5": "LA21258-1", "chr6": "LA21259-9", "chr7": "LA21260-7", "chr8": "LA21261-5",
    "chr9": "LA21262-3", "chr10": "LA21263-1", "chr11": "LA21264-9", "chr12": "LA21265-6",
    "chr13": "LA21266-4", "chr14": "LA21267-2", "chr15": "LA21268-0", "chr16": "LA21269-8",
    "chr17": "LA21270-6", "chr18": "LA21271-4", "chr19": "LA21272-2", "chr20": "LA21273-0",
    "chr21": "LA21274-8", "chr22": "LA21275-5", "chrX": "LA21276-3", "chrY": "LA21277-1",
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
    "104666-3": "CYP2B6 Activity Score in Blood or Tissue",
    "104667-1": "CYP2C19 Activity Score in Blood or Tissue",
    "104668-9": "CYP2C9 Activity Score in Blood or Tissue",
    "104669-7": "CYP2D6 Activity Score in Blood or Tissue",
    "104665-5": "DPYD Activity Score in Blood or Tissue",
    "LA10315-2": "Ultrarapid metabolizer",
    "LA25390-8": "Rapid metabolizer",
    "LA25391-6": "Normal metabolizer",
    "LA10317-8": "Intermediate metabolizer",
    "LA9657-3": "Poor metabolizer",
    "LA25392-4": "Increased function",
    "LA25393-2": "Normal function",
    "LA25395-7": "Decreased function",
    "LA25394-0": "Poor function",
    "LA6677-4": "Responsive",
    "LA19541-4": "High risk",
    "LA19542-2": "Low risk",
    # The variant's (VARIANT_LOINC, VARIANT_ANSWERS, CHROMOSOME_ANSWERS); tx.fhir.org,
    # LOINC 2.82, 2026-10-24.
    "69548-6": "Genetic variant assessment",
    "62374-4": "Human reference sequence assembly version",
    "48000-4": "Chromosome [Identifier] in Blood or Tissue by Molecular genetics method",
    "81254-5": "Genomic allele start-end",
    "92822-6": "Genomic coordinate system [Type]",
    "69547-8": "Genomic ref allele [ID]",
    "69551-0": "Genomic alt allele [ID]",
    "48004-6": "DNA change (c.HGVS)",
    "51958-7": "Transcript reference sequence [ID]",
    "48005-3": "Amino acid change (pHGVS)",
    "53034-5": "Allelic state",
    "48002-0": "Genomic source class [Type]",
    "81252-9": "Discrete genetic variant",
    "82121-5": "Allelic read depth",
    "81258-6": "Sample variant allelic frequency [NFr]",
    "53037-8": "Genetic variation clinical significance [Imp]",
    "81259-4": "Associated phenotype",
    "LA9633-4": "Present",
    "LA26806-2": "GRCh38",
    "LA30102-0": "1-based character counting",
    "LA6683-2": "Germline",
    "LA6706-1": "Heterozygous",
    "LA6705-3": "Homozygous",
    "LA6707-9": "Hemizygous",
    "LA6668-3": "Pathogenic",
    "LA26332-9": "Likely pathogenic",
    "LA26333-7": "Uncertain significance",
    "LA21254-0": "Chromosome 1", "LA21255-7": "Chromosome 2", "LA21256-5": "Chromosome 3",
    "LA21257-3": "Chromosome 4", "LA21258-1": "Chromosome 5", "LA21259-9": "Chromosome 6",
    "LA21260-7": "Chromosome 7", "LA21261-5": "Chromosome 8", "LA21262-3": "Chromosome 9",
    "LA21263-1": "Chromosome 10", "LA21264-9": "Chromosome 11", "LA21265-6": "Chromosome 12",
    "LA21266-4": "Chromosome 13", "LA21267-2": "Chromosome 14", "LA21268-0": "Chromosome 15",
    "LA21269-8": "Chromosome 16", "LA21270-6": "Chromosome 17", "LA21271-4": "Chromosome 18",
    "LA21272-2": "Chromosome 19", "LA21273-0": "Chromosome 20", "LA21274-8": "Chromosome 21",
    "LA21275-5": "Chromosome 22", "LA21276-3": "Chromosome X", "LA21277-1": "Chromosome Y",
}

# The phenotypes ZaroPGx reports, by the answer of the IG's
# genetic-therapeutic-implications-vs (3.0.0, LOINC) that says the same thing. The
# key is the phenotype in lower case, spaces collapsed. Only exact equivalents:
# a hedged phenotype ("Likely Poor Metabolizer", "Possible Decreased Function"),
# G6PD's activity terms (Normal, Deficient, Variable), an uncertain result, the
# wild-type labels and "A or B" have no code that says the same, and stay text.
# The risk statements take High/Low risk, as CPIC's own EHR priority for them
# reads ("Abnormal/Priority/High Risk", "Normal/Routine/Low Risk").
PHENOTYPE_ANSWERS = {
    "ultrarapid metabolizer": "LA10315-2",
    "rapid metabolizer": "LA25390-8",
    "normal metabolizer": "LA25391-6",
    "intermediate metabolizer": "LA10317-8",
    "poor metabolizer": "LA9657-3",
    "increased function": "LA25392-4",
    "normal function": "LA25393-2",
    "decreased function": "LA25395-7",
    "poor function": "LA25394-0",
    "ivacaftor responsive in cf patients": "LA6677-4",
    # PharmCAT 3.4's RYR1 and CACNA1S tables write "Susceptibility"; "Susceptible"
    # alone left an MH-susceptible result uncoded.
    "malignant hyperthermia susceptibility": "LA19541-4",
    "malignant hyperthermia susceptible": "LA19541-4",
    "increased risk of aminoglycoside-induced hearing loss": "LA19541-4",
    "normal risk of aminoglycoside-induced hearing loss": "LA19542-2",
}

# PharmCAT's HLA phenotypes name a risk allele and whether it was found
# ("*31:01 negative"); HLA-B has one per allele it screens, which ZaroPGx joins
# with ", " ("*15:02 negative, *57:01 positive, *58:01 negative"). High risk if
# any is positive, Low risk if every one is negative (_hla_results_code).
HLA_ALLELE_RESULT_ANSWERS = {
    "positive": "LA19541-4",
    "negative": "LA19542-2",
}

# CPIC's EHR priority term behind each risk code above. The value set has no
# answer for an allele found or not, a risk of hearing loss or a susceptibility,
# so High or Low risk states CPIC's priority for the call, not the call itself;
# the observation says so in a note. Provisional (docs/samples/README.md).
CPIC_PRIORITY_TERMS = {
    "LA19541-4": "Abnormal/Priority/High Risk",
    "LA19542-2": "Normal/Routine/Low Risk",
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
