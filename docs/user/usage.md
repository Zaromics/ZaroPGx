---
title: User Guide
---

# User Guide

Learn how to use ZaroPGx for pharmacogenomic analysis.

## Web Interface

### Main Dashboard

The main dashboard provides:
- **File Upload Area**: Drag and drop or click to upload genomic files
- **Recent Analyses**: View and access previous analyses
- **System Status**: Monitor service health and resource usage
- **Quick Actions**: Common tasks and shortcuts

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
   - **Reference Genome**: hg38 (default) or hg19
   - **Processing Options**: Enable/disable specific tools
3. **Start Analysis**: Click "Upload and Analyze"

#### Upload Options

**Reference Genome Selection:**
- **hg38/GRCh38**: Recommended, fully supported
- **hg19/GRCh37**: Supported with automatic liftover

**Processing Toggles:**
- **GATK Processing**: Enable for BAM/CRAM/SAM files
- **HLA Typing**: Enable for HLA allele calling
- **PyPGx Analysis**: Enable for comprehensive allele calling
- **Report Generation**: Enable for PDF/HTML reports

## Analysis Workflow

### Processing Stages

1. **File Validation**: Verify file format and integrity
2. **Header Analysis**: Extract metadata and contig information
3. **Preprocessing**: Convert files to VCF format if needed
4. **Allele Calling**: 
   - HLA typing (if enabled)
   - PyPGx analysis
   - PharmCAT analysis
5. **Report Generation**: Create clinical reports
6. **Data Export**: Optional FHIR export

### Monitoring Progress

**Real-time Updates:**
- Progress percentage
- Current processing stage
- Estimated time remaining
- Resource usage (CPU, memory)

**Detailed Logs:**
- Container-specific logs
- Error messages and warnings
- Processing statistics
- Intermediate results

## Reports

### Report Types

#### PDF Clinical Report
- **Executive Summary**: Key findings and recommendations
- **Gene Analysis**: Detailed pharmacogene results
- **Clinical Guidelines**: CPIC-based recommendations
- **Technical Details**: Methodology and parameters

#### Interactive HTML Report
- **Interactive Tables**: Sortable, filterable results
- **Visualizations**: Charts and diagrams
- **Detailed Annotations**: Gene-specific information
- **Export Options**: Download data in various formats

#### Raw Data Files
- **PharmCAT HTML**: Original PharmCAT report
- **PharmCAT JSON**: Machine-readable results
- **PharmCAT TSV**: Tab-separated data
- **VCF Files**: Processed variant calls

### Understanding Results

#### Star Allele Notation
- **Format**: `*1/*2` (diplotype) or `*1` (haplotype)
- **Interpretation**: 
  - `*1`: Reference allele
  - `*2`, `*3`, etc.: Variant alleles
  - `*N`: Novel or undefined alleles

#### Phenotype Categories
- **Normal Metabolizer**: Standard drug processing
- **Intermediate Metabolizer**: Reduced drug processing
- **Poor Metabolizer**: Significantly reduced processing
- **Rapid Metabolizer**: Increased drug processing
- **Ultrarapid Metabolizer**: Very high drug processing

#### Clinical Recommendations
- **Green**: No action needed
- **Yellow**: Consider alternative dosing
- **Red**: Avoid or use with extreme caution
- **Blue**: Additional monitoring recommended

## API Usage

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

### Data Retention

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

1. **Use Standard Formats**: Ensure files follow standard specifications
2. **Include Index Files**: Provide .bai, .crai, .csi, .tbi files when available
3. **Check Quality**: Verify file integrity before upload
4. **Use Descriptive Names**: Include sample identifiers in filenames

### Analysis Configuration

1. **Choose Appropriate Reference**: Use hg38 for new data, hg19 for legacy data
2. **Enable Relevant Tools**: Only enable tools you need to save resources
3. **Monitor Resources**: Watch CPU and memory usage during processing
4. **Review Logs**: Check logs for warnings or errors

### Result Interpretation

1. **Understand Limitations**: Be aware of tool-specific limitations
2. **Review Quality Metrics**: Check confidence scores and coverage
3. **Consider Clinical Context**: Interpret results in clinical context
4. **Validate Findings**: Cross-reference with multiple sources when possible

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
- Check disk space availability
- Verify report template files
- Review PDF generation logs

### Getting Help

1. **Check Logs**: Review container and application logs
2. **Documentation**: Consult this guide and API documentation
3. **Community**: Join discussions on GitHub
4. **Issues**: Report bugs and request features on GitHub

## Next Steps

- **Learn about file formats**: {doc}`file-formats`
- **Understand reports**: {doc}`reports`
- **Configure advanced settings**: {doc}`../advanced-configuration`
- **Troubleshoot issues**: {doc}`troubleshooting`
