---
title: User Guide
---

# User Guide

Learn how to use ZaroPGx to submit a sample for processing and receive insightful reports.

## Web Interface

### Main Dashboard

The main dashboard provides:
- **File Upload**: Drag and drop or click to upload genomic files: single datafile is fine unless you have a raw FASTQ; in that case please upload both paired reads. If you have an existing index file, you may upload it as well, though it may not be necessary as a new one may be generated anyhow at some point throughout the pipeline. You may also enter an identifier for the sample.
- **System Status**: Monitor service health visually by observing the service glyphs, progress bar, and processing log.
- **Quick Actions**: Common tasks and shortcuts: you can check the header of a sample without running the pipeline. You can cancel a running pipeline cleanly. While uploading a sample, a cancel button is not provided, you may simply press the home button to reset the display. Service glyphs may be interacted with to toggle enable/disable override certain stages in the pipeline. 
- **Report Screen**: Upon completion of a workflow, a report screen will appear, offering PharmCAT and custom ZaroPGx reports. 

### Uploading Files

#### Supported File Types

| Format | Extension | Description | Processing |
|--------|-----------|-------------|------------|
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
6. **Data Export**: Optional FHIR export (first XML, coming in 0.3)

### Monitoring Progress

**Real-time Updates:**
- Progress percentage (estimated)
- Current processing stage (live logs)

**Detailed Logs:**: Use docker compose logs -f , and see the /data dir on the ZaroPGx instance's host device for detailed Nextflow logs
- Container-specific logs
- Error messages and warnings
- Processing statistics
- Intermediate results

## Reports

### Report Types

#### Custom PDF Report
- **Executive Summary**: Key findings and recommendations
- **Gene Analysis**: Detailed pharmacogene results
- **Drug Analysis**: Detailed overview of identified drugs
- **Clinical Guidelines**: CPIC, DPWG, and FDA-based recommendations
- **Technical Details**: Methodology, parameters, sampled header, etc.

#### Interactive HTML Report
- **Detailed Annotations**: Gene-specific information
- **Everything in PDF Report**: And more
- **Export Options**: Download data in various formats (FHIR in XML coming in 0.3)
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
  - `*1`: Reference allele. Typically synonymous with wild type.
  - `*2`, `*3`, etc.: Variant alleles
  - `*N`: Novel or undefined alleles

#### Phenotype Categories
- **Normal Metabolizer**: Typical drug processing
- **Intermediate Metabolizer**: Reduced drug processing
- **Poor Metabolizer**: Significantly reduced processing
- **Rapid Metabolizer**: Increased drug processing
- **Ultrarapid Metabolizer**: Very high drug processing

## API Usage (NEEDS REVIEW)
- **API Reference**: See https://pgx.zimerguz.net/api-reference for the standard ZaroPGx implementation's public API reference 

### REST API Endpoints

#### Upload Genomic Data
```bash
curl -X POST \
  -F "file=@sample.vcf" \
  -F "sample_identifier=patient_001" \
  -F "reference_genome=hg38" \
  http://localhost:8765/upload/genomic-data
```

#### Check Analysis Status
```bash
curl http://localhost:8765/status/{job_id}
```

#### Get Report URLs
```bash
curl http://localhost:8765/reports/{job_id}
```

#### Download Reports
```bash
curl -O http://localhost:8765/reports/{patient_id}/{report_file}
```

### API Response Format

```json
{
  "job_id": "uuid-string",
  "status": "completed",
  "progress": 100,
  "pdf_report_url": "/reports/patient_id/report.pdf",
  "html_report_url": "/reports/patient_id/report.html",
  "diplotypes": {
    "CYP2D6": "*1/*2",
    "CYP2C19": "*1/*1"
  },
  "recommendations": [
    {
      "gene": "CYP2D6",
      "recommendation": "Consider alternative dosing",
      "severity": "yellow"
    }
  ]
}
```

## Data Management

### File Organization

**Upload Directory**: `/data/uploads/`
- Original uploaded files
- Temporary processing files
- Index files (.bai, .crai, .csi, .tbi)

**Reports Directory**: `/data/reports/{patient_id}/`
- Generated reports (PDF, HTML)
- Raw analysis outputs
- Intermediate processing files

**Reference Directory**: `/reference/`
- Reference genome files
- Annotation databases
- Tool-specific references

### Data Retention (NOTE: The ZaroPGx Demo Reference server at pgx.zimerguz.net is for DEMO purposes only. Do not upload your sensitive data.)

- **Uploaded Files**: Retained indefinitely (configurable)
- **Processing Logs**: Retained for 30 days (configurable)
- **Reports**: Retained indefinitely (configurable)
- **Temporary Files**: Cleaned up after processing

### Data Export

#### FHIR Export
```bash
curl -X POST \
  http://localhost:8765/reports/{report_id}/export-to-fhir
```

#### Bulk Export
```bash
# Export all reports for a patient
curl http://localhost:8765/patients/{patient_id}/export
```

## Best Practices

### File Preparation
1. **Choose the highest fidelity genomic datafile for submission**: Computing resources aside, make sure you choose the best file out of what's available per sample to upload.
2. **Include Index Files**: Provide .bai, .crai, .csi, .tbi files when available
3. **Check Quality**: Verify file integrity before upload

### Analysis Configuration
1. **Enable Relevant Tools**: Only enable tools your device can afford to run (the program will attempt to match your hardware, but if memory runs out, it may crash)
2. **Monitor Resources**: Watch CPU and memory usage during processing (see Nextflow)
3. **Review Logs**: Check docker compose container logs and nextflow logs for warnings or errors

### Result Interpretation
1. **Understand Limitations**: Be aware of tool-specific limitations, especially if a VCF sample was submitted
2. **Review Quality Metrics**: Check confidence scores and coverage (see the header matter)
3. **Consider Broader Context**: Review the findings in a broad context
4. **Validate Findings**: Follow up with a qualified professional and when applicable, an accredited laboratory

## Troubleshooting

### Common Issues

**Upload Failures:**
- Check file format and size
- Verify network connectivity
- Review server logs

**Processing Errors:**
- Check file quality and format
- Verify reference genome availability
- Review container logs

**Report Generation Issues:**
- Check disk space availability and permissions
- Verify report template files
- Review report generation logs

### Getting Help

1. **Check Logs**: Review container and application logs
2. **Documentation**: Consult this guide
3. **Community**: See discussions on GitHub
4. **Issues**: Report bugs and request features on GitHub

## Next Steps

- **Learn about file formats**: {doc}`file-formats`
- **Understand reports**: {doc}`reports`
- **Configure advanced settings**: {doc}`../advanced-configuration`
- **Troubleshoot issues**: {doc}`troubleshooting`
