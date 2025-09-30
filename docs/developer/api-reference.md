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
  "patient_id": "550e8400-e29b-41d4-a716-446655440001",
  "file_id": "550e8400-e29b-41d4-a716-446655440002",
  "status": "uploaded",
  "message": "Files uploaded successfully",
  "workflow_id": "550e8400-e29b-41d4-a716-446655440003"
}
```

**Status Codes:**
- `200`: Upload successful
- `400`: Invalid file format or parameters
- `413`: File too large
- `500`: Server error

#### Get Upload Status

Get the processing status of an uploaded file.

**Endpoint:** `GET /upload/status/{job_id}`

**Parameters:**
- `job_id` (path): Job identifier

**Response:**
```json
{
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
    "pdf_report_url": "/reports/patient_001/report.pdf",
    "html_report_url": "/reports/patient_001/report.html",
    "pharmcat_html_url": "/reports/patient_001/pharmcat.html",
    "pharmcat_json_url": "/reports/patient_001/pharmcat.json",
    "pharmcat_tsv_url": "/reports/patient_001/pharmcat.tsv"
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

#### Download Report

Download a specific report file.

**Endpoint:** `GET /reports/{patient_id}/{filename}`

**Parameters:**
- `patient_id` (path): Patient identifier
- `filename` (path): Report filename

**Response:**
- File content with appropriate Content-Type header

**Example:**
```bash
curl -O http://localhost:8765/reports/patient_001/report.pdf
```

#### Generate Report

Generate a report for existing analysis data.

**Endpoint:** `POST /reports/generate`

**Content-Type:** `application/json`

**Request Body:**
```json
{
  "patient_id": "patient_001",
  "file_id": "file_001",
  "report_type": "comprehensive"
}
```

**Response:**
```json
{
  "report_id": "report_001",
  "status": "generated",
  "pdf_url": "/reports/patient_001/report.pdf",
  "html_url": "/reports/patient_001/report.html"
}
```

### Workflow Endpoints

#### Get Workflow Status

Get detailed workflow status and progress.

**Endpoint:** `GET /workflows/{workflow_id}`

**Parameters:**
- `workflow_id` (path): Workflow identifier

**Response:**
```json
{
  "workflow_id": "550e8400-e29b-41d4-a716-446655440000",
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
    "sample_identifier": "patient_001"
  }
}
```

#### Cancel Workflow

Cancel a running workflow.

**Endpoint:** `POST /workflows/{workflow_id}/cancel`

**Parameters:**
- `workflow_id` (path): Workflow identifier

**Response:**
```json
{
  "workflow_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "cancelled",
  "message": "Workflow cancelled successfully"
}
```

#### Get Workflow Logs

Get logs for a specific workflow.

**Endpoint:** `GET /workflows/{workflow_id}/logs`

**Parameters:**
- `workflow_id` (path): Workflow identifier
- `level` (query, optional): Log level filter (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- `container` (query, optional): Container filter
- `limit` (query, optional): Maximum number of logs (default: 100)

**Response:**
```json
{
  "workflow_id": "550e8400-e29b-41d4-a716-446655440000",
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

### Patient Endpoints

#### List Patients

Get a list of all patients.

**Endpoint:** `GET /patients`

**Query Parameters:**
- `limit` (optional): Maximum number of patients (default: 100)
- `offset` (optional): Number of patients to skip (default: 0)
- `search` (optional): Search term for patient identifier

**Response:**
```json
{
  "patients": [
    {
      "id": "patient_001",
      "identifier": "SAMPLE_001",
      "created_at": "2024-01-15T10:00:00Z",
      "last_analysis": "2024-01-15T10:30:00Z",
      "analysis_count": 3
    }
  ],
  "total": 1,
  "limit": 100,
  "offset": 0
}
```

#### Get Patient Details

Get detailed information about a specific patient.

**Endpoint:** `GET /patients/{patient_id}`

**Parameters:**
- `patient_id` (path): Patient identifier

**Response:**
```json
{
  "id": "patient_001",
  "identifier": "SAMPLE_001",
  "created_at": "2024-01-15T10:00:00Z",
  "updated_at": "2024-01-15T10:30:00Z",
  "genetic_data": [
    {
      "id": "data_001",
      "file_type": "VCF",
      "file_path": "/data/uploads/sample.vcf",
      "created_at": "2024-01-15T10:00:00Z"
    }
  ],
  "workflows": [
    {
      "id": "workflow_001",
      "status": "completed",
      "created_at": "2024-01-15T10:00:00Z",
      "completed_at": "2024-01-15T10:30:00Z"
    }
  ]
}
```

### System Endpoints

#### Health Check

Check system health and service status.

**Endpoint:** `GET /health`

**Response:**
```json
{
  "status": "healthy",
  "timestamp": "2024-01-15T10:30:00Z",
  "services": {
    "database": "healthy",
    "pharmcat": "healthy",
    "pypgx": "healthy",
    "gatk": "healthy",
    "fhir": "healthy"
  },
  "version": "1.0.0",
  "uptime": 3600
}
```

#### System Information

Get system information and configuration.

**Endpoint:** `GET /system/info`

**Response:**
```json
{
  "version": "1.0.0",
  "build_date": "2024-01-15T00:00:00Z",
  "git_commit": "abc123def456",
  "environment": "development",
  "features": {
    "gatk_enabled": true,
    "pypgx_enabled": true,
    "optitype_enabled": true,
    "fhir_enabled": true
  },
  "limits": {
    "max_upload_size": "1GB",
    "max_concurrent_workflows": 10
  }
}
```

## Data Models

### Upload Response

```json
{
  "job_id": "string",
  "patient_id": "string",
  "file_id": "string",
  "status": "string",
  "message": "string",
  "workflow_id": "string"
}
```

### Workflow Status

```json
{
  "workflow_id": "string",
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

Connect to WebSocket for real-time workflow updates:

**Endpoint:** `ws://localhost:8765/ws/workflows/{workflow_id}`

**Message Format:**
```json
{
  "type": "progress_update",
  "workflow_id": "string",
  "progress": "number",
  "stage": "string",
  "message": "string"
}
```

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
