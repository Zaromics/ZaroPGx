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

#### Upload Genomic Data

Upload genomic data files for pharmacogenomic analysis.

**Endpoint:** `POST /upload/genomic-data`

**Content-Type:** `multipart/form-data`

**Parameters:**
- `files` (required): List of genomic data files
- `sample_identifier` (optional): Patient/sample identifier
- `reference_genome` (optional): Reference genome (default: "hg38")
- `optitype_enabled` (optional): Enable HLA typing (default: null)
- `gatk_enabled` (optional): Enable GATK processing (default: null)
- `pypgx_enabled` (optional): Enable PyPGx analysis (default: null)
- `report_enabled` (optional): Enable report generation (default: null)

**Request Example:**
```bash
curl -X POST \
  -F "file=@sample.vcf" \
  -F "sample_identifier=patient_001" \
  -F "reference_genome=hg38" \
  -F "pypgx_enabled=true" \
  http://localhost:8765/upload/genomic-data
```

**Response:**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "data_id": "550e8400-e29b-41d4-a716-446655440002",
  "status": "uploaded",
  "message": "Files uploaded successfully",
  "file_type": "vcf"
}
```

`data_id` is the genetic-data UUID (formerly `file_id`). Use it with `GET /status/{data_id}`. Job progress and cancel use `job_id` under `/api/v1/jobs/...`. The recipe catalog at `/api/v1/workflows` is unchanged (recipes, not job instances).

**Status Codes:**
- `200`: Upload successful
- `400`: Invalid file format or parameters
- `413`: File too large
- `500`: Server error

#### Get Upload Status

Get the processing status of an uploaded file.

**Endpoint:** `GET /status/{data_id}` (also `GET /upload/status/{job_id}` for job-scoped status)

**Parameters:**
- `data_id` (path): Genetic data identifier (= `genetic_data.data_id`; was `file_id`)

**Response:**
```json
{
  "data_id": "550e8400-e29b-41d4-a716-446655440002",
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "processing",
  "progress": 45,
  "current_stage": "pypgx_analysis",
  "message": "Running PyPGx analysis...",
  "logs": [
    {
      "timestamp": "2024-01-15T10:30:00Z",
      "level": "INFO",
      "message": "Starting PyPGx analysis",
      "container": "pypgx"
    }
  ],
  "estimated_completion": "2024-01-15T10:45:00Z"
}
```

**Status Values:**
- `uploaded`: File uploaded, waiting for processing
- `processing`: Currently being processed
- `completed`: Processing completed successfully
- `failed`: Processing failed
- `cancelled`: Processing was cancelled

### Report Endpoints

#### Get Report URLs

Get URLs for generated reports.

**Endpoint:** `GET /reports/{job_id}`

**Parameters:**
- `job_id` (path): Job identifier

**Response:**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "reports": {
    "pdf_report_url": "/reports/patient_001/550e8400-e29b-41d4-a716-446655440000/550e8400-e29b-41d4-a716-446655440000_pgx_report.pdf",
    "html_report_url": "/reports/patient_001/550e8400-e29b-41d4-a716-446655440000/550e8400-e29b-41d4-a716-446655440000_pgx_report_interactive.html",
    "pharmcat_html_url": "/reports/patient_001/550e8400-e29b-41d4-a716-446655440000/550e8400-e29b-41d4-a716-446655440000_pgx_pharmcat.html",
    "pharmcat_json_url": "/reports/patient_001/550e8400-e29b-41d4-a716-446655440000/550e8400-e29b-41d4-a716-446655440000_pgx_pharmcat.json",
    "pharmcat_tsv_url": "/reports/patient_001/550e8400-e29b-41d4-a716-446655440000/550e8400-e29b-41d4-a716-446655440000_pgx_pharmcat.tsv"
  },
  "diplotypes": {
    "CYP2D6": "*1/*2",
    "CYP2C19": "*1/*1",
    "TPMT": "*1/*1"
  },
  "recommendations": [
    {
      "gene": "CYP2D6",
      "recommendation": "Consider alternative dosing",
      "severity": "yellow",
      "drugs": ["codeine", "tramadol"]
    }
  ]
}
```

Artifacts live on disk at `/data/reports/{patient_id}/{job_id}/`. Display `report_id` in templates equals `job_id`.

#### Download Report

Download a specific report file.

**Endpoint:** `GET /reports/{patient_id}/{filename}`

Nested layout examples use `{patient_id}/{job_id}/{filename}` under the same route
(`filename` may include the job subdirectory path).

**Parameters:**
- `patient_id` (path): Patient identifier
- `filename` (path): Report filename (or `{job_id}/{filename}`)

**Response:**
- File content with appropriate Content-Type header

**Example:**
```bash
curl -O http://localhost:8765/reports/patient_001/550e8400-e29b-41d4-a716-446655440000/550e8400-e29b-41d4-a716-446655440000_pgx_report.pdf
```

#### Generate Report (retired)

**Endpoint:** `POST /reports/generate` — returns **501**.

Report generation runs via `POST /upload/genomic-data`. Status and file delivery:

- `GET /status/{data_id}` / `GET /upload/status/{job_id}` / `GET /api/v1/jobs/{job_id}`
- `GET /upload/reports/job/{job_id}` / `GET /upload/reports/download/{patient_id}`
- `GET /reports/{patient_id}/{filename}` (nested `{patient_id}/{job_id}/…`)
- Recipe catalog: `GET /api/v1/workflows` (unchanged; not job-instance status)

Also retired (**501**): `GET /reports/{id}/status`, `GET /reports/{id}/download`,
`GET /reports/recommendations/{patient_id}`, `POST /reports/{id}/export-to-fhir`
(use `/fhir/*` for real FHIR export).

Cleanup: `POST /api/cleanup/job/{job_id}` (the old `/api/cleanup/workflow/...` path is removed).

### Workflow Endpoints

> **Note (137c):** Job-instance progress, cancel, logs, and WebSocket live under
> `/api/v1/jobs/{job_id}`. The recipe catalog remains at `/api/v1/workflows`
> (unchanged). Container cancel payloads accept `job_id` only (no `workflow_id`
> dual-accept). Legacy `/workflows/{workflow_id}` paths below are historical
> examples; prefer `/api/v1/jobs/...`.

#### Get Workflow Status

Get detailed workflow status and progress.

**Endpoint:** `GET /api/v1/jobs/{job_id}` (preferred) / historical `GET /workflows/{workflow_id}`

**Parameters:**
- `job_id` (path): Job identifier

**Response:**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "processing",
  "progress": 65,
  "current_stage": "pharmcat_analysis",
  "steps": [
    {
      "step_name": "file_validation",
      "status": "completed",
      "start_time": "2024-01-15T10:00:00Z",
      "end_time": "2024-01-15T10:01:00Z",
      "duration": 60
    },
    {
      "step_name": "pypgx_analysis",
      "status": "completed",
      "start_time": "2024-01-15T10:01:00Z",
      "end_time": "2024-01-15T10:15:00Z",
      "duration": 840
    },
    {
      "step_name": "pharmcat_analysis",
      "status": "processing",
      "start_time": "2024-01-15T10:15:00Z",
      "end_time": null,
      "duration": null
    }
  ],
  "metadata": {
    "file_type": "VCF",
    "reference_genome": "hg38",
    "sample_identifier": "patient_001",
    "data_id": "550e8400-e29b-41d4-a716-446655440002"
  }
}
```

#### Cancel Job

Cancel a running job.

**Endpoint:** `POST /api/v1/jobs/{job_id}/cancel`

**Parameters:**
- `job_id` (path): Job identifier

Container cancel JSON payloads use `job_id` only (no `workflow_id` alias).

**Response:**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "cancelled",
  "message": "Job cancelled successfully"
}
```

#### Get Job Logs

Get logs for a specific job.

**Endpoint:** `GET /api/v1/jobs/{job_id}/logs`

**Parameters:**
- `job_id` (path): Job identifier
- `level` (query, optional): Log level filter (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- `container` (query, optional): Container filter
- `limit` (query, optional): Maximum number of logs (default: 100)

**Response:**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "logs": [
    {
      "timestamp": "2024-01-15T10:00:00Z",
      "level": "INFO",
      "message": "Starting workflow",
      "container": "app",
      "step": "file_validation"
    },
    {
      "timestamp": "2024-01-15T10:01:00Z",
      "level": "INFO",
      "message": "File validation completed",
      "container": "app",
      "step": "file_validation"
    }
  ],
  "total": 150,
  "has_more": true
}
```

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

Connect to WebSocket for real-time job updates:

**Endpoint:** `ws://localhost:8765/api/v1/jobs/{job_id}/ws`

**Message Format:**
```json
{
  "type": "job_update",
  "job_id": "string",
  "progress": "number",
  "stage": "string",
  "message": "string"
}
```

Envelope `type` is `job_update` (was `workflow_update`).

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
