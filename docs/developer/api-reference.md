---
title: API Reference
---

# API Reference

API documentation for ZaroPGx, hand-written against the code at v0.2.8.

The server also publishes a generated OpenAPI schema, which is authoritative
whenever this page and the code disagree:

- `/docs` — interactive Swagger UI
- `/redoc` — ReDoc
- `/openapi.json` — the raw schema

Routes marked `include_in_schema=False` (`/api-reference`, `/login`, `/logout`)
do not appear there; they are listed here instead. WebSockets never appear in an
OpenAPI schema, so `/api/v1/jobs/{job_id}/ws` is documented only on this page.

Source citations on this page name a **file and a symbol**, never a line number.
An earlier revision cited line numbers throughout; a single wave of unrelated
merges shifted most of them by 20–180 lines, and a citation that points at the
wrong line is worse than none because it still looks authoritative. Search for
the route path or the function name instead — those survive edits above them.

## Base URL

```
http://localhost:8765
```

Port 8765 is the published host port; inside the compose network the app listens
on 8000.

## Authentication

There is one front-door gate, `AuthGateMiddleware`, and one environment variable
that controls it: `ZAROPGX_AUTH_MODE`.

| Mode | Behaviour |
| --- | --- |
| `open` | **Default.** Every request passes. No credential is checked anywhere. |
| `audit` | Every request passes, but unauthenticated ones are logged at WARNING as `would-deny`. |
| `password` | A session cookie or `Authorization: Bearer` token is required. |

**The default is open, and it is open in production too.** `ZAROPGX_DEV_MODE=false`
is *not* an auth switch — with `ZAROPGX_AUTH_MODE` unset it still resolves to
`open` and logs a warning saying so. If you need the gate, set
`ZAROPGX_AUTH_MODE=password` and `ZAROPGX_AUTH_PASSWORD` explicitly.

### Obtaining a token

```http
POST /token
Content-Type: application/x-www-form-urlencoded

username=<anything>&password=<ZAROPGX_AUTH_PASSWORD>
```

Returns `{"access_token": "…", "token_type": "bearer"}`. Send it as:

```http
Authorization: Bearer <jwt_token>
```

In `open` and `audit` modes `/token` still accepts the legacy `test` / `test`
credentials so API explorers work, but those tokens deliberately omit the `gate`
claim and cannot unlock `password` mode.

### Always-open paths

These bypass the gate even in `password` mode: `/health`, `/openapi.json`,
`/docs`, `/redoc`, `/docs/oauth2-redirect`, `/api-reference`, `/login`,
`/logout`, `/token`, `/favicon.ico`; anything under `/docs/` or `/redoc/`; and
anything under `/static/`, `/documentation/`, `/api/v1/jobs/` or
`/api/v1/workflows/` (bare `/api/v1/jobs` and `/api/v1/workflows` are allowlisted
too).

That allowlist covers the entire job API, so `password` mode does not protect job
status, logs or cancel.

### Denial behaviour

In `password` mode an unauthenticated request gets `401` with
`{"detail": "Authentication required"}` and a `WWW-Authenticate: Bearer` header —
unless it is a `GET`/`HEAD` whose `Accept` header prefers HTML, which gets a
`303` redirect to `/login?next=…` instead.

### Per-route authentication

Several handlers declare `current_user: str = Depends(get_optional_user)`. That
dependency never rejects anything: it returns the string `"test"` when no token
is present or the token fails to validate. It is not an authorization check, and
no route restricts access by user.

## API Endpoints

### Upload Endpoints

Mounted at `/upload` (`upload_router.py`, included unconditionally in
`app/main.py`). Five routes, all in `app/api/routes/upload_router.py`.

| Endpoint | Method | Handler |
| --- | --- | --- |
| `/upload/genomic-data` | POST | `upload_genomic_data` |
| `/upload/status/{job_id}` | GET | `get_upload_status` |
| `/upload/inspect-header` | POST | `inspect_file_header` |
| `/upload/reports/job/{job_id}` | GET | `get_report_urls` |
| `/upload/reports/download/{patient_id}` | GET | `download_all_reports` |

#### Upload Genomic Data

Upload genomic data files for pharmacogenomic analysis. This is the only route
that starts a run.

**Endpoint:** `POST /upload/genomic-data`

**Content-Type:** `multipart/form-data`

**Parameters:**
- `files` (required): One or more genomic data files. The form field is
  **`files`** (plural) and it is required — posting `file=` returns 422.
- `sample_identifier` (optional): Patient/sample identifier. When omitted the
  server mints a UUID. When supplied it must match the pipeline-token allowlist
  below, or the request is a **400**.
- `reference_genome` (optional): Reference genome (default: `hg38`). Always
  validated against the same allowlist.
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
  "status": "processing",
  "message": "Files uploaded successfully. Processing started.",
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
- `200`: Upload successful, job created and started
- `400`: The file was rejected — either the processor could not read it, or it is
  an input the pipeline has no branch for. `detail` is the reason.
- `422`: Missing or malformed form fields
- `500`: Unhandled server error, with `detail: "Upload failed: …"`

> **Branch on the status code.** An earlier revision of this page said this route
> could not return 400, because the handler's terminal `except Exception`
> re-wrapped its own `HTTPException` as a 500. That is fixed:
> `upload_genomic_data` now has an `except HTTPException: raise` guard ahead of
> the catch-all, like every other handler in the module. A rejected file arrives
> as a real **`400`** with the bare reason in `detail`, not as
> `"Upload failed: 400: <reason>"`.

**The 400 refusals.** Three distinct checks produce one, in this order:

1. **Field validation.** `validate_pipeline_token` (in `upload_router.py`)
   constrains `reference_genome` — always — and `sample_identifier` when the
   caller supplied a non-blank one, to `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`:
   letters, digits, dot, underscore and hyphen, 1–64 characters, first character
   alphanumeric. `NA12878`, `Sample_01`, `HG002.GRCh38`, `patient-123`, `hg38`,
   `GRCh38`, `T2T-CHM13` all pass; a **space**, quote, semicolon, `$`, backtick,
   parenthesis or newline does not. `detail` names the offending field and echoes
   the received value.

   This is a boundary guard, not cosmetics: `pipelines/pgx/main.nf` assembles
   `curl` argv inside a bash `shell:` block, and Nextflow does not escape
   `val`/`params` strings before interpolating them, so an unfiltered value would
   be spliced verbatim into a shell running in a container that bind-mounts the
   Docker socket. The same allowlist is applied to the sample name derived from
   the VCF header, which is equally attacker-controlled; there an unusual value is
   silently **dropped** rather than rejected, so it never fails an otherwise-valid
   job.
2. **Unreadable file.** `FileProcessor.process_files` returned `success: false` —
   the file could not be read or parsed at all.
3. **Unanalysable input.** `_unanalysable_upload_reason` rejected the derived
   workflow. This fires when the workflow is flagged `unsupported`, is **not**
   flagged `is_provisional`, and the detected `file_type` is outside
   `NEXTFLOW_INPUT_TYPES` (`vcf`, `bam`, `cram`, `sam`, `fastq`) — i.e. FASTA,
   BED and unrecognised formats. `main.nf` has no branch for those, so accepting
   one could only ever mint a job that fails minutes later. The reason string is
   the workflow's `unsupported_reason` when it has one, otherwise
   `"Files of type '<file_type>' cannot be analysed."`

   An input flagged `unsupported` but marked `is_provisional`, and any input whose
   type is in `NEXTFLOW_INPUT_TYPES`, is accepted and analysed — `unsupported` on
   its own is not a refusal.

All three fire **before** any patient, genetic-data or job row is created, so a
400 leaves nothing behind and returns no `job_id`.

#### Get Upload Status

**Endpoint:** `GET /upload/status/{job_id}` — the canonical status route.

`GET /status/{data_id}` (`app/main.py`) is the same view addressed by genetic-data
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

**Stage Values** (`current_stage`, from the `WorkflowStage` enum in
`app/services/workflow_stages.py`): `upload`, `analysis`, `gatk`, `hla`,
`pypgx`, `pharmcat`, `report`, `completed`. Which stages a given run passes
through depends on the input file type and the toggles it was submitted with, so
do not assume all eight occur.

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

`GET /reports/job/{job_id}` and `GET /reports/{job_id}` (both in `app/main.py`)
are thin forwarders to the same handler and return the same body.

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

The `/reports` router (`report_router.py`, included in `app/main.py`) is
mounted but every one of its five routes raises **501 Not Implemented** with a
`detail` explaining the replacement. They are kept as signposts, not features.

| Endpoint | Method | Use instead |
| --- | --- | --- |
| `/reports/generate` | POST | `POST /upload/genomic-data` |
| `/reports/{report_id}/status` | GET | `GET /upload/status/{job_id}` or `GET /api/v1/jobs/{job_id}` |
| `/reports/{report_id}/download` | GET | `GET /upload/reports/download/{patient_id}` or `GET /reports/{patient_id}/{filename}` |
| `/reports/recommendations/{patient_id}` | GET | Report artifacts, or `/fhir/*` |
| `/reports/{report_id}/export-to-fhir` | POST | `/fhir/export/run/{run_id}` or `/fhir/save/*` |

**Route resolution under `/reports`.** The router is included near the top of
`app/main.py`, *before* the `@app.get("/reports/…")` decorators execute further
down the module, and Starlette matches in registration order. The retired stubs therefore win any
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
>   (`job_router.py`). Progress, steps, logs, cancel and the WebSocket are all
>   here.
> - A **workflow** is now only a *recipe* — a named template of step definitions.
>   The read-only catalog lives under **`/api/v1/workflows`**
>   (`workflow_recipe_router.py`).
>
> `/api/v1/workflows/{id}` is **not** an alias for `/api/v1/jobs/{id}`; the two
> mounts serve unrelated resources. The old `workflow_router` was hard-cut to
> `/api/v1/jobs` and its job-instance routes no longer answer under
> `/api/v1/workflows`. Bare `/workflows/...` has never existed.
>
> Container cancel payloads accept `job_id` only (no `workflow_id` dual-accept).

All twelve job routes are registered unconditionally, all in
`app/api/routes/job_router.py`.

| Endpoint | Method | Response model |
| --- | --- | --- |
| `/api/v1/jobs/` | POST | `JobResponse` (201) |
| `/api/v1/jobs/{job_id}` | GET | `JobResponse` |
| `/api/v1/jobs/{job_id}` | PUT | `JobResponse` |
| `/api/v1/jobs/{job_id}` | DELETE | empty (204) |
| `/api/v1/jobs/{job_id}/steps` | POST | `JobStepResponse` (201) |
| `/api/v1/jobs/{job_id}/steps` | GET | `JobStepResponse[]` |
| `/api/v1/jobs/{job_id}/steps/{step_name}` | PUT | `JobStepResponse` |
| `/api/v1/jobs/{job_id}/progress` | GET | `JobProgressResponse` |
| `/api/v1/jobs/{job_id}/logs` | POST | `JobLogResponse` (201) |
| `/api/v1/jobs/{job_id}/logs` | GET | `JobLogResponse[]` |
| `/api/v1/jobs/{job_id}/ws` | WebSocket | see WebSocket Support |
| `/api/v1/jobs/{job_id}/cancel` | POST | `JobResponse` |

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
of any run. Registered unconditionally; both routes live in
`app/api/routes/workflow_recipe_router.py`.

| Endpoint | Method | Handler |
| --- | --- | --- |
| `/api/v1/workflows` and `/api/v1/workflows/` | GET | `list_workflow_recipes` (one function, two decorators) |
| `/api/v1/workflows/{workflow_type}` | GET | `get_workflow_recipe` |

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

Mounted at `/api/pharmcat` (`pharmcat_router.py`, included unconditionally in
`app/main.py`). These read the parsed PharmCAT results held in the database.

A **run** is one PharmCAT execution, identified by `run_id`. `run_id` is not a
`job_id`; use the `/workflow/{workflow_id}/…` routes to reach a run from the
pipeline side. Those two routes still take the historical `workflow_id` path
name, and the value they expect is the job id.

| Endpoint | Method | Response |
| --- | --- | --- |
| `/api/pharmcat/load` | POST | `PharmCATLoadResponse` |
| `/api/pharmcat/workflow/{workflow_id}/summary` | GET | `PharmCATSummary` |
| `/api/pharmcat/workflow/{workflow_id}/data` | GET | full parsed payload |
| `/api/pharmcat/summary/{run_id}` | GET | `PharmCATSummary` |
| `/api/pharmcat/genes/{run_id}` | GET | `GeneSummary[]` |
| `/api/pharmcat/diplotypes/{run_id}` | GET | `DiplotypeInfo[]` |
| `/api/pharmcat/drugs/{run_id}` | GET | `DrugInfo[]` |
| `/api/pharmcat/messages/{run_id}` | GET | `MessageInfo[]` |
| `/api/pharmcat/actionable/{run_id}` | GET | `ActionableFinding[]` |
| `/api/pharmcat/runs` | GET | run list |
| `/api/pharmcat/runs/{run_id}` | DELETE | deletion result |
| `/api/pharmcat/health` | GET | liveness |

All the list-returning routes return **bare JSON arrays**, not wrapped objects.

> **The three summary routes work.** An earlier revision of this page said
> `POST /load`, `GET /summary/{run_id}` and
> `GET /workflow/{workflow_id}/summary` were broken and always returned 500,
> because they fed `get_pharmcat_summary()`'s `actionable_findings` **list** into
> a response field typed `int`. That is repaired. A shared
> `_build_summary_response()` helper in `pharmcat_router.py` now does the mapping
> for both summary routes: `actionable_findings` on the response is the count
> (taken from the parser's `actionable_findings_count`) and
> `actionable_findings_list` carries the findings themselves. `POST /load` reads
> `actionable_findings_count` directly. All the previously missing required
> fields (`total_messages`, `genes`, `actionable_findings_list`) are populated.
>
> The 404s work too. Every handler that raises one now has an
> `except HTTPException: raise` guard ahead of its catch-all, so an unknown run
> or workflow is a real **404**, not a 500 indistinguishable from a bug.

**Missing-data behaviour differs by route, and the split is deliberate:**

- `/summary/{run_id}` and both `/workflow/{workflow_id}/…` routes **404** when
  there is no such run/workflow. `get_pharmcat_summary()` returns `None` rather
  than a zero-filled summary precisely so "no such run" cannot be mistaken for
  "a run that called nothing".
- The list routes — `/genes/`, `/diplotypes/`, `/drugs/`, `/messages/`,
  `/actionable/` — have no such guard and return an **empty array** for an unknown
  `run_id`. Do not treat `[]` from those as proof the run exists.
- `POST /load` returns `400` for a non-`.json` filename or unparseable JSON, and
  `500` if the parsed run did not persist.

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

Returns **404** when no run (or no workflow-linked run) matches.

**Response** (`PharmCATSummary`):
```json
{
  "run_id": "…",
  "pharmcat_version": "3.4.0",
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

`pharmcat_version` is the `pharmcatVersion` recorded on the parsed run and is
nullable — a run loaded from a report that omitted it reports `null`.

Two same-named fields that mean different things, and both bite:
`actionable_findings` on this model is a **count** (the findings themselves are
in `actionable_findings_list`), and `warning_messages` here is a **list** —
those messages whose `exception_type` is `warning` or `error` — whereas
`warning_messages` on `PharmCATLoadResponse` is their count.

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
    "phenotype": "Rapid Metabolizer",
    "match_score": 2,
    "inferred": false,
    "combination": false
  }
]
```

Every field except `gene_symbol` is nullable.

The last three come straight from the PharmCAT matcher's `matchScore`,
`inferred` and `combination` on each diplotype and are the call's own account of
how it was arrived at:

| Field | Type | Meaning |
| --- | --- | --- |
| `match_score` | int \| null | PharmCAT's matcher score for the call |
| `inferred` | bool \| null | The diplotype was *inferred* rather than directly called |
| `combination` | bool \| null | The call is a combination/partial diplotype |

`null` means the loaded report did not carry the value — it is not the same as
`false`. Report rendering uses these to distinguish a confident call from an
inferred or partial one, so pass them through rather than flattening them to a
label.

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

Mounted at `/fhir` (`fhir_export_router.py`). **Conditionally registered**:
`app/main.py` includes the router only when `FHIR_EXPORT_ENABLED` is truthy, and
that flag defaults to **true**. Boot with `FHIR_EXPORT_ENABLED=false` and the
whole prefix **404s**.

Seven of the nine handlers — everything under `/fhir/export/run/…`,
`/fhir/export/workflow/…` and `/fhir/save/…` — also open with
`if not fhir_export_enabled(): raise HTTPException(503, …)`. `/fhir/status` and
`/fhir/export/formats` carry no such guard.

> **That 503 is reachable; write the branch.** An earlier revision of this page
> called it dead code, on the reasoning that the mount and the guard read the
> same module-level constant. They no longer read a constant.
> `fhir_export_enabled()` (`app/services/fhir_export_service.py`) is deliberately
> a **function**, resolved on every call, because a constant evaluated at import
> time would snapshot the environment *before* `app/main.py` runs `load_dotenv()`
> — which once made every `/fhir/*` route answer 503 while plainly being mounted.
>
> The consequence is that the flag is no longer frozen at import. The mount
> decision is still made once at startup, but the guard is re-evaluated per
> request against the live process environment. If that environment changes after
> startup — anything mutating `os.environ` in-process — a request can reach a
> mounted route whose guard now says disabled and get a **503**. With a fixed
> process environment (the normal deployment: Compose sets the variable once at
> container start) the two always agree and only the 404 is observable.

Whitespace is stripped before the truthiness test, so `FHIR_EXPORT_ENABLED='true '`
is a trailing-space typo, not a request to disable export. The truthy set is
`1`, `true`, `yes`, `on`, case-insensitive; **unset means enabled**.

By the same reasoning, the "disabled" wording inside `GET /fhir/status` and the
empty `supported_formats` it returns are observable under those same conditions —
`/fhir/status` is unguarded, so it answers whatever `fhir_export_enabled()` says
at request time.

Bundles follow the HL7 Genomics Reporting Implementation Guide (FHIR R4) and are
built from real PharmCAT run data held in the database.

All nine live in `app/api/routes/fhir_export_router.py`.

| Endpoint | Method | 503 guard |
| --- | --- | --- |
| `/fhir/status` | GET | no |
| `/fhir/export/formats` | GET | no |
| `/fhir/export/run/{run_id}` | GET | yes |
| `/fhir/export/run/{run_id}` | POST | yes |
| `/fhir/export/run/{run_id}/preview` | GET | yes |
| `/fhir/export/workflow/{workflow_id}` | GET | yes |
| `/fhir/save/run/{run_id}` | POST | yes |
| `/fhir/save/workflow/{workflow_id}` | POST | yes |
| `/fhir/save/run/{run_id}/quick` | GET | yes |

> **`save` means save to disk.** The `/fhir/save/*` routes write bundle files
> into `/data/reports/{subdirectory}/` on the local filesystem
> (`FHIRExportService.save_fhir_export` and `save_fhir_export_for_workflow`).
> They do **not** POST to the HAPI FHIR server or to any other endpoint. Nothing
> under `/fhir/*` transmits data off the host.

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

This route is **not** 503-guarded: it reports whatever `fhir_export_enabled()`
answers at request time. Its disabled branch returns `enabled: false`, an empty
`supported_formats`, and a `message` telling you to set the variable — reachable
only under the in-process environment change described above, since a process
that booted with the flag off never mounted this route at all.

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
attachment header naming `pgx_report_{run_id}.json` / `.xml`
(`FHIRExportService.export_pgx_report`). Not a JSON envelope.

```bash
curl -OJ "http://localhost:8765/fhir/export/run/{run_id}?output_format=xml"
```

**Status Codes:** `200`, `404` (unknown run, or the service could not build a
bundle), `500`.

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
  "filename": "pgx_report_{run_id}.json",
  "content": "…",
  "bundle": { },
  "xml_preview": null,
  "resource_counts": {"Patient": 1, "Observation": 12}
}
```

For `output_format=xml`, `content` and `bundle` are `null` and `xml_preview`
carries the XML — truncated to the first 2000 characters followed by `...` only
when the document exceeds 2000 characters, otherwise whole and un-suffixed.

#### Save an Export to the Reports Directory

**Endpoints:**
- `POST /fhir/save/run/{run_id}`
- `POST /fhir/save/workflow/{workflow_id}`
- `GET /fhir/save/run/{run_id}/quick`

The two POST routes take a `FHIRSaveRequest` body — `patient_id`, `patient_info`,
`output_format` (`json`, `xml`, or `both`), `include_recommendations`. The quick
GET route takes `output_format` and `patient_id` as query parameters instead and
skips patient details.

`patient_id` names the subdirectory under `/data/reports/`. When it is omitted
the fallback differs by route: the run routes fall back to the **run id**, and
`/fhir/save/workflow/{workflow_id}` falls back to the **workflow id**
(`FHIRExportService.save_fhir_export_for_workflow`).

**Response** (`FHIRSaveResponse`; the quick route adds a `message`):
```json
{
  "success": true,
  "files_saved": [
    {
      "format": "json",
      "path": "/data/reports/patient_001/pgx_fhir_report.json",
      "filename": "pgx_fhir_report.json",
      "url": "/reports/patient_001/pgx_fhir_report.json"
    }
  ],
  "report_directory": "/data/reports/patient_001",
  "error": null
}
```

`files_saved` is a list of **objects**, not path strings. The saved filename is
the fixed `pgx_fhir_report.json` / `pgx_fhir_report.xml`
(`FHIRExportService.save_fhir_export`) — it does not carry the run id, so saving a second
run into the same subdirectory overwrites the first. Note this differs from the
download/preview filename, which is `pgx_report_{run_id}.{ext}`.

**Status Codes:** `200`, `500` (save failed).

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

HTML pages: `/` (the upload dashboard), `/api-reference` (a wrapper page that
iframes **Swagger UI at `/docs`** — not this document — with a Back button),
`/login` (GET renders the form, POST submits it) and `/logout` (GET or POST, both
clear the session cookie and redirect to `/login`).

Of those, only `/api-reference`, `/login` and `/logout` carry
`include_in_schema=False`. **`/` is in the OpenAPI schema** (`app/main.py`), so
`/openapi.json` lists it even though it returns a web page.

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

Every model below is a Pydantic model in `app/api/models.py`. Field types come
from that file; the canonical definitions are in `/openapi.json`.

### Enumerations

| Enum | Values |
| --- | --- |
| `JobStatus` | `pending`, `running`, `completed`, `failed`, `cancelled` |
| `StepStatus` | `pending`, `running`, `completed`, `failed`, `skipped` |
| `LogLevel` | `debug`, `info`, `warn`, `error` |

All values are lowercase. `JobStatus` has no `processing` member and `LogLevel`
has no `warning` or `critical`.

### UploadResponse

`UploadResponse` in `app/api/models.py`. Returned by `POST /upload/genomic-data`.

| Field | Type | Notes |
| --- | --- | --- |
| `data_id` | string | Genetic-data UUID (formerly `file_id`) |
| `job_id` | string | Run instance id |
| `file_type` | string | Detected format |
| `status` | string | |
| `message` | string | |
| `analysis_info` | `FileAnalysis` \| null | Header/format analysis |
| `workflow` | `WorkflowInfo` \| null | Resolved `workflow_type` and options |
| `created_at` | datetime | |

### JobResponse

`JobResponse` in `app/api/models.py`. Returned by `GET/POST/PUT /api/v1/jobs/…` and by cancel.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | string | The job id. **Not** named `job_id` on this model |
| `name` | string | |
| `description` | string \| null | |
| `status` | `JobStatus` | |
| `created_at` | datetime | |
| `started_at` | datetime \| null | |
| `completed_at` | datetime \| null | |
| `total_steps` | int \| null | |
| `completed_steps` | int \| null | |
| `metadata` | object | Free-form; holds `patient_id`, `data_id`, `file_type`, report URLs |
| `created_by` | string \| null | |
| `workflow_type` | string \| null | Recipe key |
| `workflow_snapshot` | object \| null | Recipe as resolved at creation |

### JobProgressResponse

`JobProgressResponse` in `app/api/models.py`. Fields: `job_id`, `status` (`JobStatus`), `total_steps`,
`completed_steps`, `progress_percentage` (float, 0–100), `current_step`,
`estimated_completion`, `message`.

### JobStepResponse

`JobStepResponse` in `app/api/models.py`. Fields: `id`, `job_id`, `step_name`, `step_order`, `status`
(`StepStatus`), `container_name`, `started_at`, `completed_at`,
`duration_seconds`, `output_data`, `error_details`, `retry_count`.

### JobLogResponse

`JobLogResponse` in `app/api/models.py`. Fields: `id` (**int**, not a UUID), `job_id`, `step_name`,
`log_level` (`LogLevel`), `message`, `metadata`, `timestamp`.

### Report URL payload

Returned by `GET /upload/reports/job/{job_id}` and its two forwarders. Not a
Pydantic model — it is assembled from job metadata, so treat every key inside
`reports` as optional.

```json
{
  "job_id": "string",
  "status": "completed",
  "reports": {
    "pdf_report_url": "string",
    "html_report_url": "string",
    "interactive_html_report_url": "string",
    "pharmcat_html_report_url": "string",
    "pharmcat_json_report_url": "string",
    "pharmcat_tsv_report_url": "string"
  }
}
```

Request models worth knowing: `JobCreate` and `JobUpdate` (both in `app/api/models.py`), `JobStepCreate`/`JobStepUpdate`, `JobLogCreate`, `FHIRExportRequest` and
`FHIRSaveRequest` (`fhir_export_router.py`). **Every** `Job*` model — the five
request models and the four response models alike — sets `extra="forbid"`, so an
unrecognised key in the request body is a 422, not a silently ignored field.

## Error Handling

### Error Response Format

The app installs no custom exception handler, so errors use FastAPI's defaults.

**`HTTPException` (400/403/404/500/501 …)** — a single `detail` string:
```json
{"detail": "Job not found"}
```

**Validation errors (422)** — `detail` is an array of location records:
```json
{
  "detail": [
    {
      "type": "missing",
      "loc": ["body", "files"],
      "msg": "Field required",
      "input": null
    }
  ]
}
```

There is no `error`, `message`, `details`, `timestamp` or `request_id` envelope.
Do not parse for one.

Two exceptions to watch for, both returning HTTP 200 on failure:
`GET /api-status` returns `{"error": …, "traceback": …}`, and
`GET /services-status` signals trouble through its `status` field.

### Common Error Codes

| Code | When |
| --- | --- |
| `400 Bad Request` | On `POST /upload/genomic-data`: a `sample_identifier` or `reference_genome` outside the pipeline-token allowlist, a file the processor could not read, or an input the pipeline cannot analyse. Elsewhere: job not in a cancellable state; job not completed; a non-JSON or unparseable file on `POST /api/pharmcat/load` |
| `401 Unauthorized` | Only in `ZAROPGX_AUTH_MODE=password`, and only on non-allowlisted paths |
| `403 Forbidden` | A report path resolved outside `/data/reports` (path-jail rejection) |
| `404 Not Found` | Unknown job, run, PharmCAT run/workflow summary, report file, or workflow recipe |
| `422 Unprocessable Entity` | Request body or form fields failed validation |
| `500 Internal Server Error` | Unhandled server error |
| `501 Not Implemented` | A retired `/reports/*` stub |
| `503 Service Unavailable` | A guarded `/fhir/*` route whose `FHIR_EXPORT_ENABLED` reads false at request time — see the FHIR section |

Both the 400 and the 503 rows are new relative to earlier revisions of this page,
which said this API emitted neither. Both now do.

A process booted with `FHIR_EXPORT_ENABLED=false` never mounts `/fhir/*` at all,
so in that configuration the observable code for those paths is 404, not 503.

`413 Payload Too Large` and `429 Too Many Requests` are **not** emitted. There is
no application-level upload size cap and no rate limiting of any kind — no
limiter middleware, no `X-RateLimit-*` headers. If you need either, put a reverse
proxy in front of the app.

## WebSocket Support

### Real-time Updates

Connect to the WebSocket for real-time job updates.

**Endpoint:** `ws://localhost:8765/api/v1/jobs/{job_id}/ws`

The socket is declared on the job router (`app/api/routes/job_router.py`) under that
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

Every inner message also repeats `job_id` and `timestamp`, so those appear at
both levels.

| `data.type` | Keys inside `data` |
| --- | --- |
| `step_update` | `job_id`, `step_name`, `timestamp`, `data` (the step payload) |
| `log_update` | `job_id`, `timestamp`, `data` (the log payload) |
| `error_notification` | `job_id`, `timestamp`, `error_message`, `error_details` |
| `heartbeat` | `job_id`, `timestamp` |

Two frames are **not** wrapped, and arrive with the type at the top level:
`initial_status` (above) and `workflow_cancelled`
(`{type, job_id, message, timestamp, status: "cancelled"}`).

**Client frames** the server understands: `{"type": "ping", "timestamp": …}`,
answered with `{"type": "pong", "timestamp": …}`, and `{"type": "subscribe"}`,
which is logged and otherwise ignored. After 30 s of client silence the server
sends a bare top-level `{"type": "heartbeat", "timestamp": …}` and keeps waiting.
Invalid JSON from the client is dropped silently.

## Client Examples

There is no published SDK; these are plain HTTP calls. Note the form field is
`files`, plural — the API rejects `file` with a 422.

### Python

```python
import time

import requests

BASE = "http://localhost:8765"

# 1. Upload. Repeat the 'files' key to send an index alongside the data file.
with open("sample.vcf", "rb") as f:
    result = requests.post(
        f"{BASE}/upload/genomic-data",
        files=[("files", ("sample.vcf", f))],
        data={"sample_identifier": "patient_001", "reference_genome": "hg38"},
    ).json()

job_id = result["job_id"]

# 2. Poll until the job leaves the running state.
while True:
    status = requests.get(f"{BASE}/upload/status/{job_id}").json()
    print(status["status"], status["progress"], status["current_stage"])
    if status["status"] in {"completed", "failed", "cancelled"}:
        break
    time.sleep(5)

# 3. Collect the report URLs and download one. Every key is optional.
reports = requests.get(f"{BASE}/upload/reports/job/{job_id}").json()["reports"]
if "pdf_report_url" in reports:
    pdf = requests.get(f"{BASE}{reports['pdf_report_url']}")
    open("report.pdf", "wb").write(pdf.content)
```

Prefer the WebSocket at `/api/v1/jobs/{job_id}/ws` over polling for anything
interactive.

### JavaScript

```javascript
// Upload
const formData = new FormData();
for (const file of fileInput.files) {
  formData.append('files', file);   // 'files', not 'file'
}
formData.append('sample_identifier', 'patient_001');

const result = await (await fetch('/upload/genomic-data', {
  method: 'POST',
  body: formData,
})).json();

// Follow progress over the WebSocket
const ws = new WebSocket(
  `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}` +
  `/api/v1/jobs/${result.job_id}/ws`
);

ws.onmessage = (event) => {
  const frame = JSON.parse(event.data);
  if (frame.type === 'initial_status') {
    render(frame.data);
  } else if (frame.type === 'job_update') {
    // step/log/error/heartbeat updates are nested one level deeper
    const inner = frame.data ?? {};
    if (inner.type === 'step_update') {
      renderStep(inner.step_name, inner.data);
    } else {
      render(inner);
    }
  } else if (frame.type === 'workflow_cancelled') {
    renderCancelled();
  }
};

// Reports, once the job reports 'completed'
const { reports } = await (
  await fetch(`/upload/reports/job/${result.job_id}`)
).json();
```

## Next Steps

- **Architecture Overview**: {doc}`architecture`
- **Development Setup**: {doc}`development-setup`
- **Contributing**: {doc}`contributing`
- **Deployment**: {doc}`deployment`
