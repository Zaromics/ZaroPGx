---
title: API
---

# API

The running app serves its own API reference, generated from the code:

- `http://localhost:8765/api/docs` (Swagger UI, where requests can be tried)
- `http://localhost:8765/api/redoc`
- `http://localhost:8765/openapi.json`

This page covers what those do not: how the endpoints fit together.

## Access

With `ZAROPGX_AUTH_MODE=open` (the default) no credentials are needed. With
`ZAROPGX_AUTH_MODE=password`, get a token with the shared password and send it as a Bearer
header:

```bash
TOKEN=$(curl -s -X POST http://localhost:8765/token \
  -d "username=me&password=$ZAROPGX_AUTH_PASSWORD" | jq -r .access_token)
curl -H "Authorization: Bearer $TOKEN" http://localhost:8765/health
```

## Running an analysis

```bash
curl -X POST http://localhost:8765/upload/genomic-data \
  -F "files=@test_data/pharmcat.example.vcf" \
  -F "sample_identifier=example-1"
```

The form field is `files`; send a FASTQ's two mates as two `files` parts, and an index file
the same way. The other fields are optional:

| Field | Values | Effect |
|---|---|---|
| `sample_identifier` | `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$` | Printed as the report's Sample ID; a UUID if omitted |
| `optitype_enabled`, `gatk_enabled`, `pypgx_enabled`, `mtdna_enabled`, `report_enabled` | `true`, `false` | `false` skips that step. A step the input does not need never runs |
| `pharmcat_absent_to_ref`, `pharmcat_unspecified_to_ref` | `true`, `false` | PharmCAT's assume-reference flags, VCF and BCF only (see {doc}`../user/usage`) |

The response carries `job_id` and `data_id`, the detected `file_type`, and the workflow
the job will run. An input that cannot be analysed is refused with 400 and a reason, before
any job is created.

Then:

| Purpose | Request |
|---|---|
| Progress | `GET /api/v1/jobs/{job_id}/progress` |
| Status and report links | `GET /upload/status/{job_id}` |
| Report links | `GET /upload/reports/job/{job_id}` |
| Cancel | `POST /api/v1/jobs/{job_id}/cancel` |
| One report file | `GET /reports/{patient_id}/{job_id}/{file name}` |
| All of a patient's reports, zipped | `GET /upload/reports/download/{patient_id}` |

## Endpoint groups

| Prefix | What it is |
|---|---|
| `/upload` | Uploads, header preview (`POST /upload/inspect-header`), status, report links |
| `/api/v1/jobs` | Jobs, their steps and logs, progress, cancel. The pipeline's containers report their steps here (`PUT /api/v1/jobs/{job_id}/steps/{step_name}`) |
| `/api/v1/workflows` | The workflow recipes and their steps |
| `/api/pharmcat` | PharmCAT results loaded into the database, by run (`run_id` is the job id) |
| `/fhir` | FHIR R4 bundles of a run's results: preview, download, save to disk. Present when `FHIR_EXPORT_ENABLED=true` |
| `/api/cleanup` | Delete a job's files, or temporary files past an age |
| `/health`, `/services-status`, `/services-config`, `/api-status` | The app's health, each service's health, the step toggles' defaults, versions |

The report router's own endpoints (`POST /reports/generate`, `GET /reports/{id}/status` and
the like) are retired and return 501: reports are produced by an upload.

## Actionable findings

An actionable finding is a gene that a guideline's prescribing change for this sample comes
from: a PharmCAT annotation recommends a different drug, a dose change or other prescribing
guidance, and the change comes from that gene. These are the genes the interactive report's
drug-gene network draws. A change resting only on a risk screen's all-clear (a negative HLA
result, MT-RNR1's normal risk) is not one, and a drug's change is tied to the HLA result
PharmCAT looked its guideline up by: phenytoin's guideline is looked up by HLA-B `*15:02`, so
for a `*57:01` carrier whose phenytoin dose changes, the finding is CYP2C9, not HLA-B.

The phenotype alone decides nothing. A Normal Metabolizer is a finding where the guideline
changes the dose (CYP3A5 and tacrolimus, CYP2C19 and the proton-pump inhibitors), and so is
RYR1's and CACNA1S's Uncertain Susceptibility, for which CPIC and the FDA labels guide the use
of volatile anaesthetics and succinylcholine; a poor metabolizer is not one if no guideline
in the run changes a prescription for it.

Earlier versions counted, under the same names, every diplotype whose phenotype was not
"n/a", "Normal Metabolizer" or "Uncertain Susceptibility", so Normal Function, G6PD Normal, a
negative HLA result, Indeterminate and No Result counted. The fields keep their names and
types, and each finding gains `drugs` and `prescribing_changes`; what they hold is:

| Where | What it holds |
|---|---|
| `GET /api/pharmcat/actionable/{run_id}` | One finding per gene: `gene_symbol`, the call as the report states it (`diplotype_label`, `phenotype` with every HLA-B result, `activity_score`, `allele1_name`, `allele2_name`), and `drugs` and `prescribing_changes` behind it |
| `GET /api/pharmcat/summary/{run_id}`, `GET /api/pharmcat/workflow/{workflow_id}/summary` | `actionable_findings` is their number, `actionable_findings_list` the findings |
| `POST /api/pharmcat/load` | `actionable_findings` is their number |
| The FHIR bundle's DiagnosticReport | `conclusion`: "N genes analyzed, M actionable findings identified", M their number |

Runs stored earlier follow this definition too: it is applied to their stored PharmCAT
annotations when read. The definition lives in one function,
`app.reports.actionable.actionable_genes`.
