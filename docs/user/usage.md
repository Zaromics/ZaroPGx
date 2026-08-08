---
title: User Guide
---

# User Guide
Learn how to use ZaroPGx to submit a sample for processing and receive insightful reports.
- *Last revised 2025-10-06*

## Web Interface
- The **Main Dashboard** provides:

- **File Upload**: Drag and drop or click to upload genomic files: single datafile is fine unless you have a raw FASTQ; in that case please upload both paired reads. If you have an existing index file, you may upload it as well, though it may not be necessary as a new one may be generated anyhow at some point throughout the pipeline. You may also enter an identifier for the sample.
- **System Status**: Monitor service health visually by observing the service glyphs, progress bar, and processing log.
- **Quick Actions**: Common tasks and shortcuts: you can check the header of a sample without running the pipeline. You can cancel a running pipeline cleanly. While uploading a sample, a cancel button is not provided, you may simply press the home button to reset the display. Service glyphs may be interacted with to toggle enable/disable override certain stages in the pipeline. 
- **Report Screen**: Upon completion of a workflow, a report screen will appear, offering PharmCAT and custom ZaroPGx reports. 

### Uploading Supported File Types

| Format | Extension | Description | Processing |
| --- | --- | --- | --- |
| **VCF** | `.vcf`, `.vcf.gz` | Variant calls | Direct analysis |
| **BAM** | `.bam` | Aligned reads | HLA typing → Analysis |
| **CRAM** | `.cram` | Compressed BAM | GATK → HLA typing → Analysis |
| **SAM** | `.sam` | Text alignment | GATK → HLA typing → Analysis |
| **FASTQ** | `.fastq`, `.fastq.gz` | Raw sequences | HLA typing → GATK → Analysis |

#### Upload Process

1. **Select Files**: Choose one or more genomic files
2. **Configure Options**:
   - **Sample Identifier**: Optional patient/sample name
   - **Reference Genome**: hg38 (default) or hg19 (coming in 0.3)
   - **Processing Options**: Enable/disable specific tools
3. **Start Analysis**: Click "Upload and Analyze"

#### Upload Options

**Reference Genome Selection:**
- **hg38/GRCh38**: Recommended
- **hg19/GRCh37**: Supported with automatic bcftools liftover (coming in 0.3)
- **T2T**: Not yet supported

**Processing Toggles:**
- **GATK Processing**: Enable/Disable use of GATK Tools including conversion and variant calling
- **HLA Typing**: Enable/Disable HLA allele calling
- **PyPGx Analysis**: Enable/Disable PyPGx's comprehensive star allele calling
- **Report Generation**: Enable/Disable custom ZaroPGx PDF and HTML reports

## Analysis Workflow

### Processing Stages

1. **File Validation**: Verify file format and integrity
2. **Header Analysis**: Extract metadata and contig information
3. **Preprocessing**: Convert files to VCF format if needed
4. **Allele Calling**: 
   - HLA typing (if enabled); computationally intensive
   - PyPGx analysis (if enabled, recommended)
   - PharmCAT analysis (required)
5. **Report Generation**: Create reports
6. **Data Export**: Optional FHIR export — bundle generation in JSON and XML ships today (see [Data Export](#data-export))

### Monitoring Progress

**Real-time Updates:**
- Progress percentage (estimated)
- Current processing stage (live logs)

**Detailed Logs:**
- Use `docker compose logs -f`
- See the /data directory for detailed service and Nextflow logs
  - Container-specific logs can also be accessed via `docker compose logs -f {container-name}`
- Nextflow logs will show:
  - Error messages and warnings
  - Processing statistics

## Reports

#### Custom PDF Report
- **Executive Summary**: Key findings and recommendations
- **Gene Analysis**: Detailed pharmacogene results
- **Drug Analysis**: Detailed overview of identified drugs
- **Clinical Guidelines**: CPIC, DPWG, and FDA-based recommendations
- **Technical Details**: Methodology, parameters, sampled header, etc.

#### Interactive HTML Report
- **Detailed Annotations**: Gene-specific information
- **Everything in PDF Report**: And more
- **Export Options**: Download data in various formats; FHIR R4 bundles in JSON and XML are available today via the `/fhir/*` API (see [Data Export](#data-export))
- **Interactive Tables**: Sortable, filterable results (coming soon)
- **Visualizations**: Charts and diagrams (coming soon)

#### Raw Data Files
- **PharmCAT HTML**: Original PharmCAT report
- **PharmCAT JSON**: Machine-readable results
- **PharmCAT TSV**: Tab-separated data
- **VCF Files**: Processed variant calls, if you enable intermediate file retaining

### Understanding Results

#### Star Allele Notation
- **Format**: `*1/*2` (diplotype) or `*1` (haplotype), or `*3+*15` or similar (atypical cases)
- **Interpretation**: 
  - `*1`: Reference allele. Typically synonymous with wild (pheno)type.
  - `*2`, `*3`, etc.: Variant alleles
  - `*N`: Novel or undefined alleles

#### Phenotype Categories
- **Normal Metabolizer**: Typical drug processing
- **Intermediate Metabolizer**: Reduced drug processing
- **Poor Metabolizer**: Significantly reduced processing
- **Rapid Metabolizer**: Increased drug processing
- **Ultrarapid Metabolizer**: Very high drug processing

## API Usage

Your own instance publishes interactive API docs at `/docs` and the raw schema at
`/openapi.json`. The `/api-reference` page is a wrapper that embeds that same
Swagger UI with a Back button — https://pgx.zaromics.com/api-reference is the
reference instance's copy. For the full hand-written reference, including the
WebSocket (which no OpenAPI schema can describe), see
{doc}`../developer/api-reference`.

### REST API Endpoints

#### Upload Genomic Data
The form field is `files` (plural); repeat it to send an index alongside the
data file.
```bash
curl -X POST \
  -F "files=@sample.vcf" \
  -F "sample_identifier=patient_001" \
  -F "reference_genome=hg38" \
  http://localhost:8765/upload/genomic-data
```

#### Check Analysis Status
```bash
# by job id (canonical)
curl http://localhost:8765/upload/status/{job_id}

# by genetic-data id, if that is what you kept
curl http://localhost:8765/status/{data_id}
```

#### Get Report URLs
```bash
curl http://localhost:8765/upload/reports/job/{job_id}
```

#### Download Reports
```bash
curl -O http://localhost:8765/reports/{patient_id}/{job_id}/{report_file}
```

### API Response Format

`POST /upload/genomic-data` hands back the two identifiers you need:

```json
{
  "job_id": "uuid-string",
  "data_id": "uuid-string",
  "file_type": "vcf",
  "status": "processing",
  "message": "Files uploaded successfully. Processing started."
}
```

`GET /upload/status/{job_id}` reports progress while the run is going, and once
it completes the report URLs appear alongside:

```json
{
  "job_id": "uuid-string",
  "status": "completed",
  "progress": 100,
  "message": "Report generation complete",
  "current_stage": "report",
  "pdf_report_url": "/reports/{patient_id}/{job_id}/{job_id}_pgx_report.pdf",
  "html_report_url": "/reports/{patient_id}/{job_id}/{job_id}_pgx_report_interactive.html",
  "data": {
    "job_id": "uuid-string",
    "patient_id": "patient_001",
    "data_id": "uuid-string",
    "steps": []
  }
}
```

Diplotypes and drug recommendations are not returned by these routes — read them
from the generated report, from `/api/pharmcat/*`, or from a FHIR bundle.

## Data Management

### File Organization

**Upload Directory**: `/data/uploads/`
- Original uploaded files
- Temporary processing files
- Index files (.bai, .crai, .csi, .tbi)

**Reports Directory**: `/data/reports/{patient_id}/{job_id}/`
- Generated reports (PDF, HTML); display `report_id` = `job_id`
- Raw analysis outputs
- Intermediate processing files

**Reference Directory**: `/reference/`
- Reference genome files
- Annotation databases
- Tool-specific references

### Data Retention
# (NOTE: The ZaroPGx Demo Reference server at pgx.zaromics.com is for DEMO purposes only! Do not upload your sensitive data)

- **Uploaded Files**: Retained indefinitely (configurable)
- **Processing Logs**: Retained for 30 days (configurable)
- **Reports**: Retained indefinitely (configurable)
- **Temporary Files**: Cleaned up after processing

### Data Export

#### FHIR Export

FHIR export is enabled by default (`FHIR_EXPORT_ENABLED`, default true). What
works today, and what does not:

**Shipped.** Generating an HL7 Genomics Reporting (FHIR R4) bundle from a real
PharmCAT run, in JSON or XML: download it, preview it in the browser, or save it
alongside the other report files. This is the `/fhir/*` API.

**Shipped but local only.** "Save" writes the bundle into
`/data/reports/{patient_id}/` on the machine running ZaroPGx. It does not send
anything to the bundled HAPI FHIR server or to any external system.

**Not shipped.** Pushing results into an EHR or PHR, and any round-trip with an
external FHIR server. `POST /reports/{report_id}/export-to-fhir` did talk to a
live FHIR server but built its payload from placeholder genotypes, so it is
**retired (501)**; do not use it.

```bash
# Preview the FHIR bundle for a PharmCAT run
curl http://localhost:8765/fhir/export/run/{run_id}/preview

# Download it as a file (json or xml)
curl -OJ "http://localhost:8765/fhir/export/run/{run_id}?output_format=xml"

# Save it next to the other reports on disk
curl "http://localhost:8765/fhir/save/run/{run_id}/quick?output_format=both"
```

See {doc}`../developer/api-reference` for the full endpoint list.

#### Bulk Export
```bash
# Download every report file for a patient as a single ZIP
curl -O -J http://localhost:8765/upload/reports/download/{patient_id}
```

## Best Practices

### File Preparation
1. **Choose the highest fidelity genomic datafile for submission**: Computing resources aside, make sure you choose the best file out of the files available for a given sample to upload.
2. **Include Index Files**: Provide the accompanying index file (.bai, .crai, .csi, .tbi,) if available
3. **Check Quality**: Verify file integrity before upload

### Analysis Configuration
1. **Enable Relevant Tools**: Only enable tools your device can afford to run (the program will attempt to match your hardware, but if memory or storage runs out, it may hang or crash)
2. **Monitor Resources**: Watch CPU and memory usage during processing
3. **Review Logs**: Check docker compose container logs and nextflow logs for warnings or errors

### Result Interpretation
1. **Understand Limitations**: Be aware of tool-specific limitations, especially if a VCF sample was submitted
2. **Review Quality Metrics**: Check confidence scores and coverage (see the header matter)
3. **Consider Broader Context**: Review the findings in a broad context
4. **Validate Findings**: Follow up with a qualified professional and when applicable, an accredited laboratory

## Troubleshooting

**Upload Failures:**
- Check file format and size
- Verify network connectivity
- Review server logs

**Processing Errors:**
- Check file quality and format
- Verify reference genome availability
- Review container logs

**Report Generation Issues:**
- Check drive space availability and permissions
- Verify that all software dependencies are properly configured
- Review report generation logs

### Getting Help
1. **Check Logs**: Review container and application logs
2. **Documentation**: Consult this guide
3. **Community**: Check discussions on GitHub, or start a new thread
4. **Issues**: Report bugs, request features, and suggest changes on GitHub

## Next Steps
- **Learn about file formats**: {doc}`file-formats`
- **Understand reports**: {doc}`reports`
- **Configure advanced settings**: {doc}`../advanced-configuration`
- **Troubleshoot issues**: {doc}`troubleshooting`