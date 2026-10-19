---
title: FHIR R4 Sample Exports
---

# ZaroPGx FHIR R4 Sample Exports

## Overview

An example of the FHIR R4 bundle ZaroPGx writes with each report, for three genes. Its resources are modelled on the profiles of the **HL7 Genomics Reporting Implementation Guide**.

**Reference:** [HL7 Genomics Reporting IG, STU3 (3.0.0)](http://hl7.org/fhir/uv/genomics-reporting/STU3/). The bundle validates against the IG's profiles with no errors: HAPI FHIR 8.12's `$validate` with the IG loaded and tx.fhir.org checking the LOINC and SNOMED CT codes (October 2026). The warnings it still gives are listed under [Validation](#validation).

## Files

| File | Description |
|------|-------------|
| `sample_fhir_r4_pgx_bundle.json` | Complete FHIR R4 Bundle containing a sample PGx report with 3 genes and 3 drug recommendations |

## Bundle Structure

The sample Bundle (`type: collection`) contains the following FHIR resources in order:

### 1. DiagnosticReport (Genomic Report)
- **Profile:** `genomic-report`
- Top-level report that references all observations, links to the genomic study, and lists recommended actions via extensions
- Includes a `conclusion` text: how many genes were analysed and how many findings are actionable

### 2. Patient
- **Profile:** `Patient`
- Identified by `urn:zaropgx:patient-id` system
- Includes optional demographics (name, gender, birthDate) when provided at export time

### 3. Observation — Genotype (one per gene)
- **Profile:** `genotype`
- Categories "laboratory" and Genetics (`GE`), as the IG requires
- Coded as "Genotype display name" (`84413-4`) for every gene
- `valueCodeableConcept` carries the diplotype as text (`*1/*4`), as the IG's PharmVar example does
- Components include:
  - **Gene studied** (`48018-6`) — the gene's HGNC id (`HGNC:2625`), its symbol as the display
  - **Activity score** — LOINC's activity-score code for CYP2B6, CYP2C19, CYP2C9, CYP2D6 and DPYD (`104669-7` for CYP2D6), a text code for any other gene
  - **Haplotype name** (`84414-2`) — one per allele (allele1, allele2)
- No phenotype: the genotype profile has no place for one (next section)

### 4. Observation — Therapeutic Implication of the genotype (one per gene with a phenotype)
- **Profile:** `therapeutic-implication`, code `therapeutic-implication`, text "CYP2D6 phenotype"
- `derivedFrom` its genotype; no medication: the phenotype is the genotype's, for every drug
- One component, `therapeutic-implication`, whose value is the phenotype: coded from the
  IG's `genetic-therapeutic-implications-vs` (LOINC answers) where the value set has a code
  that says the same thing, with ZaroPGx's words as its text; text alone where it has none
  (table below)
- None for a gene whose phenotype is "N/A" (the calling tool defines none) or not reported

This is where the IG states a pharmacogenomic phenotype: "a predicted ramification based
on the presence of associated molecular finding(s)", such as "alterations in drug
metabolism", in a therapeutic implication derived from the genotype. The IG's own examples
put it in each drug's implication (TxImp01-06); ZaroPGx states it once per gene, so genes
with no drug keep theirs and a drug that depends on several genes does not have to say
which phenotype is whose.

| Phenotype | Sent as |
|---|---|
| Ultrarapid / Rapid / Normal / Intermediate / Poor Metabolizer | LOINC `LA10315-2` / `LA25390-8` / `LA25391-6` / `LA10317-8` / `LA9657-3` |
| Increased / Normal / Decreased / Poor Function | `LA25392-4` / `LA25393-2` / `LA25395-7` / `LA25394-0` |
| HLA, any result positive: `*31:01 positive`, HLA-B `*15:02 negative, *57:01 positive, *58:01 negative` | `LA19541-4` High risk |
| HLA, every result negative: `*31:01 negative`, HLA-B `*15:02 negative, *57:01 negative, *58:01 negative` | `LA19542-2` Low risk |
| HLA with a part that is not `*NN:NN positive` or `negative` | text |
| increased / normal risk of aminoglycoside-induced hearing loss (MT-RNR1) | `LA19541-4` High risk / `LA19542-2` Low risk |
| Malignant Hyperthermia Susceptible (RYR1, CACNA1S) | `LA19541-4` High risk |
| ivacaftor responsive in CF patients (CFTR) | `LA6677-4` Responsive |
| Likely / Possible Intermediate Metabolizer, Likely Poor Metabolizer, Possible Decreased Function | text: the value set has only the definite terms |
| G6PD Normal, Deficient, Deficient with CNSHA, Variable | text: enzyme activity terms with no equivalent in the value set |
| Uncertain Susceptibility, uncertain risk of aminoglycoside-induced hearing loss, Indeterminate, No Result | text: no result to code |
| ivacaftor non-responsive in CF patients | text: the value set has "Presumed non-responsive", not the same statement |
| Likely Wild Type, Possibly Wild Type, "A or B" (unphased, more than one fit) | text: ZaroPGx's labels |
| anything else | text |

The risk statements take High or Low risk as CPIC's own EHR priority for them reads
("Abnormal/Priority/High Risk", "Normal/Routine/Low Risk"). The match is on the whole
phenotype, ignoring case and spacing. An HLA phenotype has one result per risk allele
PharmCAT screens, joined with ", ": one for HLA-A (`*31:01`), three for HLA-B (`*15:02`,
`*57:01`, `*58:01`). They hold at once, so one positive result makes the phenotype High
risk, and it is Low risk only when every result is negative.

### 5. Observation — Therapeutic Implication (one per drug)
- **Profile:** `therapeutic-implication`
- Categories "laboratory" and Genetics (`GE`)
- Components include:
  - **Medication assessed** (`51963-7`) — the drug's name, as text: PharmCAT names drugs by ClinPGx id, not RxNorm code
  - **Conclusion text** (`conclusion-string`) — the first recommendation's text
- `derivedFrom` links back to the genotype observation(s) that informed the implication
- Optional `extension` for related artifacts (CPIC/DPWG guideline citations)

### 6. Task — Medication Recommendation (one per recommendation)
- **Profile:** `medication-recommendation`
- `status: requested`, `intent: proposal`
- `description` contains the actionable recommendation text
- `for` → Patient reference
- `reasonReference` → links to the corresponding Therapeutic Implication
- `input` items for the medication assessed (`51963-7`) and CPIC's strength of recommendation (Strong, Moderate, Optional, No recommendation, or Unspecified for a guideline without one), as text under a text type: LOINC has no code for it, and its "Level of evidence" is another thing. None is sent when PharmCAT gave none
- No `code`: the profile requires one of LOINC's medication usage suggestions (consider an alternative, decrease the dose, ...), and picking one would mean interpreting the guideline's text

### 7. Procedure — Genomic Study
- **Profile:** `genomic-study`
- Category "laboratory", which the profile fixes
- Coded as SNOMED CT "Genetic test" (`405824009`)
- `note` records the PharmCAT version used

## Sending a bundle to a FHIR server

The Bundle is a `collection`, a standalone export. A FHIR server's base endpoint takes
`transaction` Bundles, so the app converts it when asked to send a job's bundle:

```bash
curl -X POST http://127.0.0.1:8765/fhir/push/workflow/<job_id>
```

posts the job's `<job_id>_pgx_fhir_report.json` to `FHIR_SERVER_URL` (the bundled HAPI
server) as one transaction, every entry conditional on the resource's identifier (the
entries' `request`s):

```json
[
  {"method": "POST", "url": "Patient",
   "ifNoneExist": "identifier=urn:zaropgx:patient-id|<patient id>"},
  {"method": "PUT",
   "url": "DiagnosticReport?identifier=urn:zaropgx:report-id|<report id>"},
  {"method": "PUT",
   "url": "Observation?identifier=urn:ietf:rfc:3986|urn:uuid:<id>"}
]
```

so the Patient is created once and reused, and pushing a job again updates its report and
resources instead of adding copies. Each resource but the Patient is tagged with the job
(`meta.tag` `urn:zaropgx:job-id|<job_id>`); before posting, the push lists the job's tagged
resources on the server and adds a `DELETE` for each one the bundle no longer has, so a
regenerated report with fewer recommendations leaves none of the old ones behind.

Notes:
- Each resource's id is a name-based UUID of what it is (this report's CYP2D6 genotype),
  so a job's bundle names its resources the same way each time it is written. The
  observations, recommendations and study carry it as their identifier
  (`urn:ietf:rfc:3986`, `urn:uuid:<id>`); the Patient and the report carry ZaroPGx's
  patient and report ids
- `fullUrl`s are `urn:uuid:` and Bundle-internal references use them; the server
  replaces them with its own ids
- Resources reference the HL7 Genomics Reporting IG's profiles; the Bundle itself claims
  none (the IG defines no Bundle profile)

## Genes Included in Sample

| Gene | Diplotype | Phenotype | Drugs Affected |
|------|-----------|-----------|---------------|
| CYP2D6 | \*1/\*4 | Intermediate Metabolizer | codeine |
| CYP2C19 | \*1/\*2 | Intermediate Metabolizer | clopidogrel |
| CYP2C9 | \*1/\*3 | Intermediate Metabolizer | warfarin |

The recommendation texts in the sample are illustrative, not PharmCAT's.

## Code Systems Used

| System | URI | Usage |
|--------|-----|-------|
| LOINC | `http://loinc.org` | Report, observation, component and Task input codes, each with its official display name |
| SNOMED CT | `http://snomed.info/sct` | The genomic study's procedure code |
| HGNC | `http://www.genenames.org` | Gene ids (`HGNC:2625`) |
| HL7 Genomics IG | `http://hl7.org/fhir/uv/genomics-reporting/CodeSystem/tbd-codes-cs` | `therapeutic-implication`, `conclusion-string` |
| HL7 terminology | `http://terminology.hl7.org/CodeSystem/observation-category`, `.../v2-0074` | Categories ("laboratory", Genetics) |
| ZaroPGx | `urn:zaropgx:*` | Internal identifiers |

## Validation

Errors: none. The validator's remaining warnings, and why they stay:

- **No narrative** (`dom-6`, every resource): a best-practice rule. The PDF and HTML reports are the human-readable form; a generated `text` for each resource would repeat them.
- **No performer** (every observation): ZaroPGx does not know the laboratory or clinician, and does not invent one.
- **Recommendation matches two profiles**: the IG's `recommended-action` extension allows a medication or a follow-up recommendation, and a Task without a code fits both.
- **HGNC codes cannot be checked**: no terminology server the validator can reach carries HGNC. The ids are HGNC's own (rest.genenames.org).
- **A phenotype without a code** ("No code provided, and a code should be provided from the value set 'Genetic Therapeutic Implications'"): the value set is extensible, so text is allowed where it has no equivalent; the table above says which phenotypes those are.

## Generation

Sample exports are generated by `app/services/fhir_export_service.py` via:
- **API:** `GET /fhir/export/run/{run_id}` or `POST /fhir/export/run/{run_id}`
- **Report generator:** Automatically during report generation when `FHIR_EXPORT_ENABLED=true`

A real export holds every gene in the run's report data, not three, and its drug
recommendations. The report generator writes one with every report as
`<job_id>_pgx_fhir_report.json` and `.xml` in the job's directory.

## Terminology notices

This material contains content from LOINC (http://loinc.org). LOINC is copyright © Regenstrief Institute, Inc. and the Logical Observation Identifiers Names and Codes (LOINC) Committee and is available at no cost under the license at http://loinc.org/license. LOINC® is a registered United States trademark of Regenstrief Institute, Inc.

This material includes SNOMED Clinical Terms® (SNOMED CT®) which is used by permission of the International Health Terminology Standards Development Organisation (IHTSDO). All rights reserved. SNOMED CT®, was originally created by The College of American Pathologists. "SNOMED" and "SNOMED CT" are registered trademarks of the IHTSDO. Use of SNOMED CT is subject to its licence (https://www.snomed.org/get-snomed).

HL7®, FHIR® and the FHIR [FLAME DESIGN] are the registered trademarks of Health Level Seven International and their use does not constitute endorsement by HL7.
