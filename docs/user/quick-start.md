---
title: Quick Start Guide
---

# Quick Start Guide

Get ZaroPGx running in minutes with this step-by-step guide.

## Prerequisites

- **Docker and Docker Compose** installed on your system
- **8+ GB RAM** (16+ GB recommended for GATK processing)
- **30+ GB free disk space** (500+ GB for whole genome sequencing)
- **Internet connection** for initial setup (to download containers and reference data)

## Installation

### 1. Clone the Repository

```bash
git clone https://github.com/Zaroganos/ZaroPGx.git
cd ZaroPGx
```

### 2. Choose Your Environment

**For local development and testing:**
```bash
cp .env.local .env
```

**For production deployment:**
```bash
cp .env.production .env
```

**For custom configuration:**
```bash
cp .env.example .env
# Edit .env with your specific settings
```

### 3. Start the Services

```bash
docker compose up -d --build
```

This will:
- Download and build all required containers
- Initialize the PostgreSQL database
- Download reference genomes (if not cached)
- Start all services

### 4. Verify Installation

Check that all services are running:
```bash
docker compose ps
```

You should see all services with "Up" status.

## First Analysis

### 1. Access the Web Interface

Open your browser and navigate to:
- **Main Application**: http://localhost:8765
- **API Documentation**: http://localhost:8765/docs
- **FHIR Server** (optional): http://localhost:8090

### 2. Upload a Sample File

1. Go to the main interface at http://localhost:8765
2. Click "Upload Files" or drag and drop a VCF file
3. Use the provided sample file: `test_data/sample_cpic.vcf`
4. Click "Start Analysis"

### 3. Monitor Progress

- Watch the real-time progress updates
- View detailed logs for each processing step
- Monitor resource usage in the interface

### 4. View Results

Once complete, you'll see:
- **PDF Report**: Clinical pharmacogenomic report
- **Interactive HTML**: Detailed analysis with visualizations
- **Raw Data**: PharmCAT outputs (if enabled)

## Supported File Types

| Format | Description | Processing Path |
|--------|-------------|-----------------|
| **VCF** | Variant Call Format | Direct → PyPGx → PharmCAT |
| **BAM** | Binary Alignment Map | hlatyping → PyPGx → PharmCAT |
| **CRAM** | Compressed BAM | GATK → hlatyping → PyPGx → PharmCAT |
| **SAM** | Sequence Alignment Map | GATK → hlatyping → PyPGx → PharmCAT |
| **FASTQ** | Raw sequencing data | hlatyping → GATK → PyPGx → PharmCAT |

## Next Steps

- **Learn about file formats**: {doc}`file-formats`
- **Understand reports**: {doc}`reports`
- **Configure advanced settings**: {doc}`../advanced-configuration`
- **Troubleshoot issues**: {doc}`troubleshooting`

## Common Issues

**Services won't start?**
- Check Docker is running
- Ensure ports 8765, 5444, 5001, 5002, 5053, 8090 are available
- Review logs: `docker compose logs`

**Out of memory errors?**
- Increase Docker memory allocation
- Use smaller reference genome subsets
- Disable GATK processing for VCF files

**Slow processing?**
- Ensure sufficient RAM allocation
- Use SSD storage for better I/O performance
- Consider disabling optional services

For detailed troubleshooting, see {doc}`troubleshooting`.
