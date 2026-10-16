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
  - **Phenotype** (text) — e.g., "Intermediate Metabolizer"
  - **Activity score** — LOINC's activity-score code for CYP2B6, CYP2C19, CYP2C9, CYP2D6 and DPYD (`104669-7` for CYP2D6), a text code for any other gene
  - **Haplotype name** (`84414-2`) — one per allele (allele1, allele2)

### 4. Observation — Therapeutic Implication (one per drug)
- **Profile:** `therapeutic-implication`
- Categories "laboratory" and Genetics (`GE`)
- Components include:
  - **Medication assessed** (`51963-7`) — the drug's name, as text: PharmCAT names drugs by ClinPGx id, not RxNorm code
  - **Conclusion text** (`conclusion-string`) — the first recommendation's text
- `derivedFrom` links back to the genotype observation(s) that informed the implication
- Optional `extension` for related artifacts (CPIC/DPWG guideline citations)

### 5. Task — Medication Recommendation (one per recommendation)
- **Profile:** `medication-recommendation`
- `status: requested`, `intent: proposal`
- `description` contains the actionable recommendation text
- `for` → Patient reference
- `reasonReference` → links to the corresponding Therapeutic Implication
- `input` items for the medication assessed (`51963-7`) and the level of evidence (`93044-6`: Strong, Moderate, Optional)
- No `code`: the profile requires one of LOINC's medication usage suggestions (consider an alternative, decrease the dose, ...), and picking one would mean interpreting the guideline's text

### 6. Procedure — Genomic Study
- **Profile:** `genomic-study`
- Category "laboratory", which the profile fixes
- Coded as SNOMED CT "Genetic test" (`405824009`)
- `note` records the PharmCAT version used

## HAPI FHIR Compatibility

The Bundle is a `collection`, a standalone export. A FHIR server's base endpoint accepts
only `transaction` and `batch` Bundles, so convert it first (below), then post it to the
bundled HAPI server, which listens on `127.0.0.1:8090`:

```bash
curl -X POST http://localhost:8090/fhir \
  -H "Content-Type: application/fhir+json" \
  -d @transaction_bundle.json
```

### Key compatibility notes:
- All resource IDs use valid UUID format
- All `fullUrl` entries use `urn:uuid:` scheme for Bundle-internal references
- Resources reference the HL7 Genomics Reporting IG's profiles; the Bundle itself claims none (the IG defines no Bundle profile)
- The Bundle `type` is `collection` (standalone export, not a transaction)
- To use as a transaction Bundle for HAPI FHIR, change `type` to `"transaction"` and add `request` objects to each entry

### Converting to a Transaction Bundle

To POST as a transaction (creating all resources on the server):

```json
{
  "type": "transaction",
  "entry": [
    {
      "fullUrl": "urn:uuid:...",
      "resource": { ... },
      "request": {
        "method": "POST",
        "url": "DiagnosticReport"
      }
    }
  ]
}
```

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
