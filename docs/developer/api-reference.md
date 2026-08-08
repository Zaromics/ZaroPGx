---
title: API Reference
---

# API Reference

Complete API documentation for ZaroPGx.

## Base URL

```
http://localhost:8765
```

## Authentication

### Development Mode
Authentication is disabled by default in development mode. All endpoints are publicly accessible.

### Production Mode
JWT-based authentication is required. Include the JWT token in the Authorization header:

```http
Authorization: Bearer <jwt_token>
```

## API Endpoints

### Upload Endpoints

Mounted at `/upload` (`upload_router.py:81`, included unconditionally at
`main.py:314`). Five routes.

| Endpoint | Method | Source |
| --- | --- | --- |
| `/upload/genomic-data` | POST | `upload_router.py:1105` |
| `/upload/status/{job_id}` | GET | `upload_router.py:1313` |
| `/upload/inspect-header` | POST | `upload_router.py:1425` |
| `/upload/reports/job/{job_id}` | GET | `upload_router.py:1490` |
| `/upload/reports/download/{patient_id}` | GET | `upload_router.py:1575` |

#### Upload Genomic Data

Upload genomic data files for pharmacogenomic analysis. This is the only route
that starts a run.

**Endpoint:** `POST /upload/genomic-data`

**Content-Type:** `multipart/form-data`

**Parameters:**
- `files` (required): One or more genomic data files. The form field is
  **`files`** (plural) and it is required — posting `file=` returns 422.
- `sample_identifier` (optional): Patient/sample identifier. When omitted the
  server mints a UUID.
- `reference_genome` (optional): Reference genome (default: `hg38`)
- `optitype_enabled` (optional): Enable HLA typing
- `gatk_enabled` (optional): Enable GATK processing
- `pypgx_enabled` (optional): Enable PyPGx analysis
- `report_enabled` (optional): Enable report generation
- `pharmcat_absent_to_ref` (optional): Treat absent positions as reference
- `pharmcat_unspecified_to_ref` (optional): Treat unspecified positions as reference

Every toggle is an optional **string** form field, not a bool, and defaults to
`None` — meaning "fall back to the server-side env default", which is not the
same as `false`. The two `pharmcat_*` flags fall back to `PHARMCAT_ABSENT_TO_REF`
and `PHARMCAT_UNSPECIFIED_TO_REF`.

**Request Example:**
```bash
curl -X POST \
  -F "files=@sample.vcf" \
  -F "sample_identifier=patient_001" \
  -F "reference_genome=hg38" \
  -F "pypgx_enabled=true" \
  http://localhost:8765/upload/genomic-data
```

Send an index file by repeating the field: `-F "files=@sample.bam" -F "files=@sample.bam.bai"`.

**Response** (`UploadResponse`):
```json
{
  "data_id": "550e8400-e29b-41d4-a716-446655440002",
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "file_type": "vcf",
  "status": "uploaded",
  "message": "Files uploaded successfully",
  "analysis_info": { },
  "workflow": {"workflow_type": "genomic_analysis", "options": { }},
  "created_at": "2026-08-08T10:00:00Z"
}
```

`analysis_info` (a `FileAnalysis`) and `workflow` (a `WorkflowInfo` carrying the
resolved `WorkflowOptions`, plus any recommendations and warnings raised during
header inspection) are both optional and are the fields the web UI reads to
render its pre-flight summary.

`data_id` is the genetic-data UUID (formerly `file_id`); use it with
`GET /status/{data_id}`. Job progress and cancel use `job_id` under
`/api/v1/jobs/...`.

**Status Codes:**
- `200`: Upload successful
- `400`: File rejected by the processor (bad format, unsupported type)
- `422`: Missing or malformed form fields
- `500`: Server error

#### Get Upload Status

**Endpoint:** `GET /upload/status/{job_id}` — the canonical status route.

`GET /status/{data_id}` (`main.py:454`) is the same view addressed by genetic-data
id instead of job id; it delegates to the upload router.

**Response:**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "running",
  "progress": 45,
  "message": "Running PyPGx analysis",
  "current_stage": "pypgx",
  "data": {
    "job_id": "550e8400-e29b-41d4-a716-446655440000",
    "patient_id": "patient_001",
    "data_id": "550e8400-e29b-41d4-a716-446655440002",
    "steps": [
      {"name": "file_validation", "status": "completed", "order": 1, "container": "app"},
      {"name": "pypgx", "status": "running", "order": 2, "container": "pypgx"}
    ]
  }
}
```

There is no `logs` array and no `estimated_completion` field here — use
`GET /api/v1/jobs/{job_id}/logs` and `GET /api/v1/jobs/{job_id}/progress` for
those. Once the job completes, the report URL keys below are **spliced in at the
top level** of this same object alongside `data`.

**Status Values** (`JobStatus`): `pending`, `running`, `completed`, `failed`,
`cancelled`. An unknown job is a 404.

#### Inspect File Header

Preview a file's header without starting an analysis. This backs the "View
Header" button in the web UI.

**Endpoint:** `POST /upload/inspect-header`

**Content-Type:** `multipart/form-data` with a single field `file` (singular
here, unlike `/upload/genomic-data`).

The file is written to a temp path, parsed, and deleted before the response is
returned. Nothing is persisted and no job is created.

**Response:**
```json
{
  "status": "success",
  "success": true,
  "filename": "sample.vcf",
  "file_size": 1048576,
  "header_info": { },
  "compat": {
    "workflow": {
      "recommendations": [],
      "warnings": [],
      "unsupported": false,
      "unsupported_reason": null
    }
  }
}
```

`header_info` is the parsed `GenomicFileHeader`. Any failure is a 500 with
`detail: "Header inspection failed: …"` — there is no 400 branch.

### Report Endpoints

Report delivery is split across three mounts: the upload router serves the URL
lookup and the per-patient ZIP, `app/main.py` serves individual files, and the
`/reports` router is a set of retired stubs.

#### Get Report URLs

**Endpoint:** `GET /upload/reports/job/{job_id}`

`GET /reports/job/{job_id}` (`main.py:470`) and `GET /reports/{job_id}`
(`main.py:484`) are thin forwarders to the same handler and return the same body.

**Response:**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "reports": {
    "pdf_report_url": "/reports/patient_001/550e8400-…/550e8400-…_pgx_report.pdf",
    "html_report_url": "/reports/patient_001/550e8400-…/550e8400-…_pgx_report_interactive.html",
    "pharmcat_html_report_url": "/reports/patient_001/550e8400-…/550e8400-…_pgx_pharmcat.html",
    "pharmcat_json_report_url": "/reports/patient_001/550e8400-…/550e8400-…_pgx_pharmcat.json",
    "pharmcat_tsv_report_url": "/reports/patient_001/550e8400-…/550e8400-…_pgx_pharmcat.tsv"
  }
}
```

The response carries **only** those three keys. It has no `diplotypes` and no
`recommendations` — read those from the PharmCAT routes under `/api/pharmcat` or
from the generated report artifacts.

Note the exact key spelling: `pharmcat_html_report_url`, not `pharmcat_html_url`.
An interactive variant may also appear as `interactive_html_report_url`. Keys are
present only when the corresponding file exists, so treat every one as optional.

**Status Codes:**
- `200`: URLs returned
- `400`: Job exists but is not `completed`
- `404`: No such job
- `500`: Server error

#### Download All Reports for a Patient

**Endpoint:** `GET /upload/reports/download/{patient_id}`

Zips `/data/reports/{patient_id}/` recursively and returns it as
`application/zip` with `Content-Disposition: attachment; filename=reports_{patient_id}.zip`.
The path is resolved through the same jail as individual file serving.

**Status Codes:** `200`, `403` (path escape attempt), `404` (no such directory),
`500`.

#### Download a Single Report File

**Endpoint:** `GET /reports/{patient_id}/{filename}` (also accepts `HEAD`)

`filename` is a `:path` parameter, so it may include the job subdirectory:
`/reports/{patient_id}/{job_id}/{filename}`.

**Parameters:**
- `patient_id` (path): Patient identifier
- `filename` (path): Report filename, or `{job_id}/{filename}`

**Response:** file content with a Content-Type guessed from the extension,
falling back to `application/octet-stream`.

**Status Codes:** `200`, `403` (resolved path escapes the reports directory),
`404` (file missing).

**Example:**
```bash
curl -O http://localhost:8765/reports/patient_001/550e8400-e29b-41d4-a716-446655440000/550e8400-e29b-41d4-a716-446655440000_pgx_report.pdf
```

Artifacts live on disk at `/data/reports/{patient_id}/{job_id}/`. The display
`report_id` in templates equals `job_id`.

#### Retired /reports Stubs

The `/reports` router (`report_router.py:13`, included at `main.py:315`) is
mounted but every one of its five routes raises **501 Not Implemented** with a
`detail` explaining the replacement. They are kept as signposts, not features.

| Endpoint | Method | Use instead |
| --- | --- | --- |
| `/reports/generate` | POST | `POST /upload/genomic-data` |
| `/reports/{report_id}/status` | GET | `GET /upload/status/{job_id}` or `GET /api/v1/jobs/{job_id}` |
| `/reports/{report_id}/download` | GET | `GET /upload/reports/download/{patient_id}` or `GET /reports/{patient_id}/{filename}` |
| `/reports/recommendations/{patient_id}` | GET | Report artifacts, or `/fhir/*` |
| `/reports/{report_id}/export-to-fhir` | POST | `/fhir/export/run/{run_id}` or `/fhir/save/*` |

**Route resolution under `/reports`.** The router is included at `main.py:315`,
*before* the `@app.get("/reports/…")` decorators execute further down the module,
and Starlette matches in registration order. The retired stubs therefore win any
tie. In practice that means a report file literally named `status` or `download`
(`GET /reports/{patient_id}/status`) resolves to the 501 stub rather than to the
file server, as does a patient whose id is literally `recommendations`. Real
report paths are three or more segments deep and are unaffected.

Cleanup for a finished job is `POST /api/cleanup/job/{job_id}`; the old
`/api/cleanup/workflow/...` path is gone.

### Job Endpoints

> **Jobs vs workflows.** These are two different things and they have two
> different mounts.
>
> - A **job** is one run instance. Jobs live under **`/api/v1/jobs`**
>   (`job_router.py:48`, mounted at `main.py:317`). Progress, steps, logs, cancel
>   and the WebSocket are all here.
> - A **workflow** is now only a *recipe* — a named template of step definitions.
>   The read-only catalog lives under **`/api/v1/workflows`**
>   (`workflow_recipe_router.py:5`, mounted at `main.py:316`).
>
> `/api/v1/workflows/{id}` is **not** an alias for `/api/v1/jobs/{id}`; the two
> mounts serve unrelated resources. The old `workflow_router` was hard-cut to
> `/api/v1/jobs` and its job-instance routes no longer answer under
> `/api/v1/workflows`. Bare `/workflows/...` has never existed.
>
> Container cancel payloads accept `job_id` only (no `workflow_id` dual-accept).

All twelve job routes are registered unconditionally.

| Endpoint | Method | Response model | Source |
| --- | --- | --- | --- |
| `/api/v1/jobs/` | POST | `JobResponse` (201) | `job_router.py:51` |
| `/api/v1/jobs/{job_id}` | GET | `JobResponse` | `job_router.py:89` |
| `/api/v1/jobs/{job_id}` | PUT | `JobResponse` | `job_router.py:133` |
| `/api/v1/jobs/{job_id}` | DELETE | empty (204) | `job_router.py:179` |
| `/api/v1/jobs/{job_id}/steps` | POST | `JobStepResponse` (201) | `job_router.py:213` |
| `/api/v1/jobs/{job_id}/steps` | GET | `JobStepResponse[]` | `job_router.py:262` |
| `/api/v1/jobs/{job_id}/steps/{step_name}` | PUT | `JobStepResponse` | `job_router.py:292` |
| `/api/v1/jobs/{job_id}/progress` | GET | `JobProgressResponse` | `job_router.py:340` |
| `/api/v1/jobs/{job_id}/logs` | POST | `JobLogResponse` (201) | `job_router.py:371` |
| `/api/v1/jobs/{job_id}/logs` | GET | `JobLogResponse[]` | `job_router.py:408` |
| `/api/v1/jobs/{job_id}/ws` | WebSocket | see WebSocket Support | `job_router.py:453` |
| `/api/v1/jobs/{job_id}/cancel` | POST | `JobResponse` | `job_router.py:722` |

Most integrations only need `GET /api/v1/jobs/{job_id}`, the progress route, the
WebSocket and cancel. The create/update/step-authoring routes exist so the upload
pipeline can build a job; calling them by hand produces a job nothing executes.

#### Get Job

**Endpoint:** `GET /api/v1/jobs/{job_id}`

**Parameters:**
- `job_id` (path): Job identifier

**Response** (`JobResponse` — note the identifier key is `id`, not `job_id`):
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "genomic_analysis sample.vcf",
  "description": null,
  "status": "running",
  "created_at": "2026-08-08T10:00:00Z",
  "started_at": "2026-08-08T10:00:05Z",
  "completed_at": null,
  "total_steps": 5,
  "completed_steps": 2,
  "metadata": {
    "patient_id": "patient_001",
    "data_id": "550e8400-e29b-41d4-a716-446655440002",
    "file_type": "vcf"
  },
  "created_by": null,
  "workflow_type": "genomic_analysis",
  "workflow_snapshot": {}
}
```

`status` is one of `pending`, `running`, `completed`, `failed`, `cancelled`. The
`processing` value earlier revisions of this page showed is not in the enum. An
unknown job is a 404.

#### Get Job Progress

**Endpoint:** `GET /api/v1/jobs/{job_id}/progress`

**Response** (`JobProgressResponse`):
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "running",
  "total_steps": 5,
  "completed_steps": 2,
  "progress_percentage": 40.0,
  "current_step": "pharmcat",
  "estimated_completion": null,
  "message": "Running PharmCAT analysis"
}
```

#### Get Job Steps

**Endpoint:** `GET /api/v1/jobs/{job_id}/steps`

Returns a **bare JSON array** of `JobStepResponse`, ordered by `step_order`. An
unknown job is a 404, not an empty array.

```json
[
  {
    "id": "…",
    "job_id": "550e8400-e29b-41d4-a716-446655440000",
    "step_name": "file_validation",
    "step_order": 1,
    "status": "completed",
    "container_name": "app",
    "started_at": "2026-08-08T10:00:00Z",
    "completed_at": "2026-08-08T10:01:00Z",
    "duration_seconds": 60,
    "output_data": {},
    "error_details": {},
    "retry_count": 0
  }
]
```

Step `status` is one of `pending`, `running`, `completed`, `failed`, `skipped`.

#### Cancel Job

Cancel a running job.

**Endpoint:** `POST /api/v1/jobs/{job_id}/cancel`

**Parameters:**
- `job_id` (path): Job identifier

**Response:** the full `JobResponse` for the cancelled job with `status` set to
`cancelled` — not the short `{job_id, status, message}` object earlier revisions
of this page showed.

**Status Codes:**
- `200`: Cancelled
- `400`: Job is already `completed`, `failed` or `cancelled`
- `404`: No such job

#### Get Job Logs

Get logs for a specific job.

**Endpoint:** `GET /api/v1/jobs/{job_id}/logs`

**Parameters:**
- `job_id` (path): Job identifier
- `limit` (query, optional): Maximum number of logs (default: 100)

There are no `level` or `container` filters. The response is a **bare JSON
array** of `JobLogResponse`, newest first — no `total`/`has_more` wrapper. An
unknown job is a 404, not an empty array.

**Response:**
```json
[
  {
    "id": 1042,
    "job_id": "550e8400-e29b-41d4-a716-446655440000",
    "step_name": "file_validation",
    "log_level": "info",
    "message": "File validation completed",
    "metadata": {},
    "timestamp": "2026-08-08T10:01:00Z"
  }
]
```

`log_level` values are lowercase and limited to `debug`, `info`, `warn`, `error`.
Note `warn`, not `warning`, and there is no `critical`.

### Workflow Recipe Catalog

Read-only. A recipe describes which steps a workflow type mints — not the state
of any run. Registered unconditionally at `main.py:316`.

| Endpoint | Method | Source |
| --- | --- | --- |
| `/api/v1/workflows` and `/api/v1/workflows/` | GET | `workflow_recipe_router.py:25-26` |
| `/api/v1/workflows/{workflow_type}` | GET | `workflow_recipe_router.py:31` |

**Response** (the list form returns a bare array of these objects):
```json
{
  "workflow_type": "genomic_analysis",
  "display_name": "Genomic Analysis",
  "description": "",
  "option_fields": ["needs_gatk", "needs_pypgx", "needs_hla", "needs_report"],
  "step_templates": [
    {"step_name": "file_validation", "container_name": "app", "when": null}
  ]
}
```

`when` is `null` for always-run steps; otherwise it names the `WorkflowOptions`
field that must be true for the step to be minted. An unknown `workflow_type`
returns 404 with `detail: "Unknown workflow_type"`.

### PharmCAT Endpoints

Mounted at `/api/pharmcat` (`pharmcat_router.py:27`, included unconditionally at
`main.py:318`). These read the parsed PharmCAT results held in the database.

A **run** is one PharmCAT execution, identified by `run_id`. `run_id` is not a
`job_id`; use the `/workflow/{workflow_id}/…` routes to reach a run from the
pipeline side. Those two routes still take the historical `workflow_id` path
name, and the value they expect is the job id.

| Endpoint | Method | Response | Source |
| --- | --- | --- | --- |
| `/api/pharmcat/load` | POST | `PharmCATLoadResponse` | `pharmcat_router.py:113` |
| `/api/pharmcat/workflow/{workflow_id}/summary` | GET | `PharmCATSummary` | `:161` |
| `/api/pharmcat/workflow/{workflow_id}/data` | GET | full parsed payload | `:194` |
| `/api/pharmcat/summary/{run_id}` | GET | `PharmCATSummary` | `:220` |
| `/api/pharmcat/genes/{run_id}` | GET | `GeneSummary[]` | `:254` |
| `/api/pharmcat/diplotypes/{run_id}` | GET | `DiplotypeInfo[]` | `:271` |
| `/api/pharmcat/drugs/{run_id}` | GET | `DrugInfo[]` | `:294` |
| `/api/pharmcat/messages/{run_id}` | GET | `MessageInfo[]` | `:315` |
| `/api/pharmcat/actionable/{run_id}` | GET | `ActionableFinding[]` | `:336` |
| `/api/pharmcat/runs` | GET | run list | `:356` |
| `/api/pharmcat/runs/{run_id}` | DELETE | deletion result | `:391` |
| `/api/pharmcat/health` | GET | liveness | `:427` |

All the list-returning routes return **bare JSON arrays**, not wrapped objects.

**Query parameters:**
- `/diplotypes/{run_id}` and `/messages/{run_id}`: optional `gene_symbol` filter
- `/drugs/{run_id}`: **required** `gene_symbol` — the route lists the drugs
  associated with one gene, not every drug in the run
- `/runs`: `limit` (default 10, 1–100) and `offset` (default 0)

#### Load a PharmCAT File

**Endpoint:** `POST /api/pharmcat/load`

`multipart/form-data` with a single `file` field holding a PharmCAT JSON report.
Parses it into the database and returns the new `run_id`.

```json
{
  "run_id": "…",
  "message": "…",
  "total_genes": 23,
  "total_diplotypes": 23,
  "actionable_findings": 4,
  "warning_messages": 2
}
```

#### PharmCAT Summary

**Endpoint:** `GET /api/pharmcat/summary/{run_id}` (or
`/api/pharmcat/workflow/{workflow_id}/summary`)

**Response** (`PharmCATSummary`):
```json
{
  "run_id": "…",
  "total_genes": 23,
  "total_diplotypes": 23,
  "actionable_findings": 4,
  "total_messages": 2,
  "genes": [
    {
      "gene_symbol": "CYP2C19",
      "call_source": "MATCHER",
      "phenotype_source": "CPIC",
      "chromosome": "chr10",
      "phased": false
    }
  ],
  "actionable_findings_list": [
    {
      "gene_symbol": "CYP2C19",
      "diplotype_label": "*1/*17",
      "phenotype": "Rapid Metabolizer",
      "activity_score": null,
      "allele1_name": "*1",
      "allele2_name": "*17"
    }
  ],
  "warning_messages": [
    {
      "gene_symbol": "CYP2D6",
      "rule_name": "…",
      "exception_type": "…",
      "message": "…"
    }
  ]
}
```

#### Diplotypes

**Endpoint:** `GET /api/pharmcat/diplotypes/{run_id}?gene_symbol=CYP2C19`

```json
[
  {
    "gene_symbol": "CYP2C19",
    "diplotype_label": "*1/*17",
    "allele1_name": "*1",
    "allele1_function": "Normal function",
    "allele2_name": "*17",
    "allele2_function": "Increased function",
    "activity_score": null,
    "phenotype": "Rapid Metabolizer"
  }
]
```

Every field except `gene_symbol` is nullable.

#### List and Delete Runs

**Endpoint:** `GET /api/pharmcat/runs?limit=10&offset=0`

```json
[
  {
    "run_id": "…",
    "run_timestamp": "2026-08-08T10:20:00Z",
    "pharmcat_version": "3.4.0",
    "data_version": "…",
    "loaded_at": "2026-08-08T10:21:00Z"
  }
]
```

`DELETE /api/pharmcat/runs/{run_id}` permanently removes a run and everything
parsed from it.

#### PharmCAT API Health

**Endpoint:** `GET /api/pharmcat/health`

```json
{"status": "healthy", "service": "pharmcat-api", "version": "0.2.8"}
```

This reports the ZaroPGx router, not the PharmCAT container. It performs no
check and cannot fail; the container's own health is reported by
`GET /services-status` under the `pharmcat` key.

### FHIR Export Endpoints

Mounted at `/fhir` (`fhir_export_router.py:27`). **Conditionally registered**:
`main.py:321` includes the router only when `FHIR_EXPORT_ENABLED` is truthy, and
that flag defaults to **true** (`main.py:156`). Set `FHIR_EXPORT_ENABLED=false`
and the whole prefix 404s.

The gate is applied twice. Even when the router is mounted, every export and save
handler re-reads its own `FHIR_EXPORT_ENABLED` from the environment
(`fhir_export_service.py:27`, also defaulting true) and returns **503** if it is
off. `GET /fhir/status` and `GET /fhir/export/formats` skip that second check and
always answer.

Bundles follow the HL7 Genomics Reporting Implementation Guide (FHIR R4) and are
built from real PharmCAT run data held in the database.

| Endpoint | Method | Source |
| --- | --- | --- |
| `/fhir/status` | GET | `fhir_export_router.py:67` |
| `/fhir/export/formats` | GET | `fhir_export_router.py:362` |
| `/fhir/export/run/{run_id}` | GET | `fhir_export_router.py:90` |
| `/fhir/export/run/{run_id}` | POST | `fhir_export_router.py:159` |
| `/fhir/export/run/{run_id}/preview` | GET | `fhir_export_router.py:295` |
| `/fhir/export/workflow/{workflow_id}` | GET | `fhir_export_router.py:231` |
| `/fhir/save/run/{run_id}` | POST | `fhir_export_router.py:434` |
| `/fhir/save/workflow/{workflow_id}` | POST | `fhir_export_router.py:498` |
| `/fhir/save/run/{run_id}/quick` | GET | `fhir_export_router.py:561` |

> **`save` means save to disk.** The `/fhir/save/*` routes write bundle files
> into `/data/reports/{patient_id or run_id}/` on the local filesystem
> (`fhir_export_service.py:246-330`). They do **not** POST to the HAPI FHIR
> server or to any other endpoint. Nothing under `/fhir/*` transmits data off the
> host.

#### FHIR Export Status

**Endpoint:** `GET /fhir/status`

```json
{
  "enabled": true,
  "message": "FHIR export is enabled",
  "supported_formats": ["json", "xml"],
  "implementation_guide": "HL7 Genomics Reporting Implementation Guide (FHIR R4)",
  "reference_url": "https://build.fhir.org/ig/HL7/genomics-reporting/pharmacogenomics.html"
}
```

`supported_formats` is `[]` when export is disabled.

#### Supported Formats

**Endpoint:** `GET /fhir/export/formats`

Returns a `formats` array (`json` → `application/fhir+json`, `xml` →
`application/fhir+xml`), an `implementation_guide` object (STU 4, FHIR R4) and
the `profiles_used` list: `genomic-report`, `genotype`,
`therapeutic-implication`, `medication-recommendation`, `genomic-study`.

#### Export a Run

**Endpoint:** `GET /fhir/export/run/{run_id}`

**Query Parameters:**
- `output_format` (optional): `json` (default) or `xml`
- `include_recommendations` (optional): default `true`

**Response:** the bundle as a **file download** — raw body with media type
`application/fhir+json` or `application/fhir+xml` and a `Content-Disposition`
attachment header. Not a JSON envelope.

```bash
curl -OJ "http://localhost:8765/fhir/export/run/{run_id}?output_format=xml"
```

**Status Codes:** `200`, `404` (unknown run, or the service could not build a
bundle), `500`, `503` (export disabled).

#### Export a Run with Patient Details

**Endpoint:** `POST /fhir/export/run/{run_id}`

**Request body** (`FHIRExportRequest`):
```json
{
  "patient_info": {
    "id": "patient_001",
    "name": {"family": "Doe", "given": ["Jane"]},
    "gender": "female",
    "birthDate": "1980-01-15"
  },
  "output_format": "json",
  "include_recommendations": true
}
```

Every field is optional. The response is the same file download as the GET form.

#### Export a Workflow

**Endpoint:** `GET /fhir/export/workflow/{workflow_id}`

Resolves the PharmCAT run linked to a workflow and exports it. Query parameter:
`output_format` (`json` or `xml`). Same file-download response.

#### Preview an Export

**Endpoint:** `GET /fhir/export/run/{run_id}/preview`

The one route that returns the bundle *in* a JSON body rather than as a download.

```json
{
  "success": true,
  "format": "json",
  "filename": "pgx_fhir_bundle_….json",
  "content": "…",
  "bundle": { },
  "xml_preview": null,
  "resource_counts": {"Patient": 1, "Observation": 12}
}
```

For `output_format=xml`, `content` and `bundle` are `null` and `xml_preview`
holds the first 2000 characters followed by `...`.

#### Save an Export to the Reports Directory

**Endpoints:**
- `POST /fhir/save/run/{run_id}`
- `POST /fhir/save/workflow/{workflow_id}`
- `GET /fhir/save/run/{run_id}/quick`

The two POST routes take a `FHIRSaveRequest` body — `patient_id` (subdirectory,
defaults to the run id), `patient_info`, `output_format` (`json`, `xml`, or
`both`), `include_recommendations`. The quick GET route takes `output_format` and
`patient_id` as query parameters instead and skips patient details.

**Response** (`FHIRSaveResponse`; the quick route adds a `message`):
```json
{
  "success": true,
  "files_saved": ["/data/reports/patient_001/pgx_fhir_bundle_….json"],
  "report_directory": "/data/reports/patient_001",
  "error": null
}
```

**Status Codes:** `200`, `500` (save failed), `503` (export disabled).

### System Endpoints

There is no patient-listing API. Patients exist only as rows created implicitly
by `POST /upload/genomic-data`; no route enumerates or reads them. There is also
no `/system/info` route — for build/feature information use `GET /services-config`
and `GET /api-status` below.

#### Health Check

Liveness probe for the app container only. It is deliberately dependency-free —
it touches no database and no sibling service, so it stays green while everything
downstream is broken. For the per-service picture use `GET /services-status`.

**Endpoint:** `GET /health`

**Response:** exactly two keys.
```json
{
  "status": "healthy",
  "timestamp": "2026-08-08 14:30:00.123456+00:00"
}
```

`status` is the literal string `healthy` — the handler has no failure branch, so
a 200 here means "the ASGI app is answering", nothing more. `timestamp` is
`str(datetime.now(timezone.utc))`, not ISO-8601 with a `T` separator.

`/health` is on the auth-gate allowlist, so it answers without credentials even
in `password` mode.

#### Service Status

Fan out a health check to every enabled service and summarise the result.

**Endpoint:** `GET /services-status`

Checked services: `app` and `database` always; `gatk` if `GATK_ENABLED`; `pypgx`
if `PYPGX_ENABLED`; `pharmcat` always (core); `zarohla` if `OPTITYPE_ENABLED`.

**Response (all healthy):**
```json
{
  "status": "ok",
  "message": "All services are available",
  "check_time": "2026-08-08 14:30:00.123456"
}
```

**Response (any service unreachable):**
```json
{
  "status": "error",
  "message": "Some services are unavailable",
  "unhealthy_services": {
    "pypgx": "Failed after 2 retries"
  },
  "check_time": "2026-08-08 14:30:00.123456"
}
```

Both variants return HTTP 200; check the `status` field, not the status code.
The per-service map is keyed by service name and the values are free-text reason
strings, not enums.

#### Service Configuration

Report which optional services are switched on. This is the closest thing to a
feature-flag endpoint.

**Endpoint:** `GET /services-config`

**Response:**
```json
{
  "services": {
    "gatk": {"enabled": true},
    "pypgx": {"enabled": true},
    "optitype": {"enabled": true},
    "genome_downloader": {"enabled": true},
    "kroki": {"enabled": true},
    "hapi_fhir": {"enabled": true},
    "fhir_export": {
      "enabled": true,
      "description": "FHIR R4 export for pharmacogenomic reports",
      "endpoints": "/fhir/*"
    },
    "pharmcat": {
      "enabled": true,
      "absent_to_ref": false,
      "unspecified_to_ref": false
    }
  }
}
```

`fhir_export.endpoints` is `null` when `FHIR_EXPORT_ENABLED` is false. `pharmcat`
is hard-coded `true` — it is a core service with no toggle.

#### API Status

Dump the live route table plus a probe of the GATK API. Intended for debugging,
not for integration.

**Endpoint:** `GET /api-status`

**Response:**
```json
{
  "timestamp": 1754661000.123,
  "gatk_api": {"available": true, "message": "Healthy", "details": {}},
  "test_job_endpoint": {"available": true, "message": "Test endpoint working", "job_id": "..."},
  "routes": [{"path": "/health", "methods": ["GET"], "name": "health_check"}],
  "app_name": "ZaroPGx API",
  "version": "0.2.8"
}
```

`timestamp` is a Unix float, not a string. On an internal failure the handler
returns HTTP 200 with `{"error": "...", "traceback": "..."}` instead.

#### Miscellaneous

| Endpoint | Method | Returns |
| --- | --- | --- |
| `/api` | GET | `{"message": "Welcome to ZaroPGx API", "docs": "/docs"}` |
| `/license` | GET | The repository `LICENSE` file as `text/plain` (404 if absent) |
| `/notice` | GET | The repository `NOTICE` file as `text/plain` (404 if absent) |
| `/docs`, `/redoc`, `/openapi.json` | GET | FastAPI's generated interactive docs and schema |
| `/documentation/` | GET | Built Sphinx HTML, mounted only when `docs/_build/html` exists (the app tries to build it at startup) |
| `/static/…` | GET | Application static assets |

#### Cleanup

| Endpoint | Method | Notes |
| --- | --- | --- |
| `/api/cleanup/job/{job_id}` | POST | Optional `patient_id` query parameter. Removes temp files for one job. |
| `/api/cleanup/old-files` | POST | Optional `max_age_hours` query parameter (default 24). |
| `/api/cleanup/status` | GET | Current size/contents of the temp directories. |

All three return whatever `cleanup_service` produces as JSON, and raise 500 on
failure.

#### Troubleshooting Endpoints

These exist to unstick a run by hand. They are not part of the integration
surface and their shapes may change without notice.

| Endpoint | Method | Notes |
| --- | --- | --- |
| `/check-reports/{job_id}` | GET | Looks for report files on disk and marks the job completed if they exist. Returns `{job_id, reports: {...}, job_status, instructions}`. |
| `/trigger-completion/{job_id}` | GET | HTML page with direct report links. |
| `/reprocess-report/{report_id}` | POST | Re-runs PharmCAT for an existing job (`report_id` is treated as `job_id`). Primarily for testing parser changes. |
| `/api/variant-call` | POST | `multipart/form-data` with `file`, `reference_genome` (default `hg38`), optional `regions`. Proxies to the GATK API service directly, outside the Nextflow pipeline. |

## Data Models

### Upload Response

```json
{
  "job_id": "string",
  "data_id": "string",
  "file_type": "string",
  "status": "string",
  "message": "string"
}
```

### Job Status

```json
{
  "job_id": "string",
  "data_id": "string",
  "status": "string",
  "progress": "number",
  "current_stage": "string",
  "message": "string",
  "logs": [
    {
      "timestamp": "string",
      "level": "string",
      "message": "string",
      "container": "string"
    }
  ],
  "estimated_completion": "string"
}
```

### Report Data

```json
{
  "job_id": "string",
  "status": "string",
  "reports": {
    "pdf_report_url": "string",
    "html_report_url": "string",
    "pharmcat_html_url": "string",
    "pharmcat_json_url": "string",
    "pharmcat_tsv_url": "string"
  },
  "diplotypes": {
    "gene_name": "string"
  },
  "recommendations": [
    {
      "gene": "string",
      "recommendation": "string",
      "severity": "string",
      "drugs": ["string"]
    }
  ]
}
```

## Error Handling

### Error Response Format

```json
{
  "error": "string",
  "message": "string",
  "details": "string",
  "timestamp": "string",
  "request_id": "string"
}
```

### Common Error Codes

- `400 Bad Request`: Invalid request parameters
- `401 Unauthorized`: Authentication required
- `403 Forbidden`: Insufficient permissions
- `404 Not Found`: Resource not found
- `413 Payload Too Large`: File too large
- `422 Unprocessable Entity`: Invalid file format
- `429 Too Many Requests`: Rate limit exceeded
- `500 Internal Server Error`: Server error
- `503 Service Unavailable`: Service temporarily unavailable

## Rate Limiting

### Default Limits

- **Uploads**: 10 requests per minute
- **Status Checks**: 60 requests per minute
- **Report Downloads**: 30 requests per minute
- **API Calls**: 100 requests per minute

### Rate Limit Headers

```http
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 95
X-RateLimit-Reset: 1642248000
```

## WebSocket Support

### Real-time Updates

Connect to the WebSocket for real-time job updates.

**Endpoint:** `ws://localhost:8765/api/v1/jobs/{job_id}/ws`

The socket is declared on the job router (`job_router.py:453`) under that
router's `/api/v1/jobs` prefix. There is no `@app.websocket` route in
`app/main.py`, so no `/ws/...` URL exists.

`job_id` must parse as a UUID or the server closes with code **4000** before
accepting. An unknown job closes with **4004** after sending an `error` frame.

**First frame** — sent immediately on connect:
```json
{
  "type": "initial_status",
  "data": {
    "job_id": "550e8400-e29b-41d4-a716-446655440000",
    "name": "genomic_analysis sample.vcf",
    "status": "running",
    "total_steps": 5,
    "completed_steps": 2,
    "progress_percentage": 40.0,
    "current_step": "pharmcat",
    "message": "Running PharmCAT analysis",
    "created_at": "2026-08-08T10:00:00Z",
    "started_at": "2026-08-08T10:00:05Z",
    "completed_at": null
  }
}
```

**Subsequent server frames** use a `job_update` envelope with the real payload
nested under `data`:
```json
{
  "type": "job_update",
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "timestamp": "2026-08-08T10:05:00.000000+00:00",
  "data": { }
}
```

Step, log, error and heartbeat updates are **double-wrapped**: the broadcast
helper puts its own message inside the same `job_update` envelope, so the
specific kind is `data.type`, not the outer `type`. Read the inner value.

| `data.type` | Extra keys inside `data` |
| --- | --- |
| `step_update` | `step_name`, `timestamp`, `data` (the step payload) |
| `log_update` | `timestamp`, `data` (the log payload) |
| `error_notification` | `error_message`, `error_details` |
| `heartbeat` | `timestamp` only |

Two frames are **not** wrapped, and arrive with the type at the top level:
`initial_status` (above) and `workflow_cancelled`
(`{type, job_id, message, timestamp, status: "cancelled"}`).

**Client frames** the server understands: `{"type": "ping", "timestamp": …}`,
answered with `{"type": "pong", "timestamp": …}`, and `{"type": "subscribe"}`,
which is logged and otherwise ignored. After 30 s of client silence the server
sends a bare top-level `{"type": "heartbeat", "timestamp": …}` and keeps waiting.
Invalid JSON from the client is dropped silently.

## SDK Examples

### Python SDK

```python
import requests

# Upload file
with open('sample.vcf', 'rb') as f:
    response = requests.post(
        'http://localhost:8765/upload/genomic-data',
        files={'file': f},
        data={'sample_identifier': 'patient_001'}
    )
    result = response.json()

# Check status
status = requests.get(f"http://localhost:8765/upload/status/{result['job_id']}")
print(status.json())

# Get reports
reports = requests.get(f"http://localhost:8765/reports/{result['job_id']}")
print(reports.json())
```

### JavaScript SDK

```javascript
// Upload file
const formData = new FormData();
formData.append('file', fileInput.files[0]);
formData.append('sample_identifier', 'patient_001');

const response = await fetch('/upload/genomic-data', {
  method: 'POST',
  body: formData
});

const result = await response.json();

// Check status
const statusResponse = await fetch(`/upload/status/${result.job_id}`);
const status = await statusResponse.json();

// Get reports
const reportsResponse = await fetch(`/reports/${result.job_id}`);
const reports = await reportsResponse.json();
```

## Next Steps

- **Architecture Overview**: {doc}`architecture`
- **Development Setup**: {doc}`development-setup`
- **Contributing**: {doc}`contributing`
- **Deployment**: {doc}`deployment`
