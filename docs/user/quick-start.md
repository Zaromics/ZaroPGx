---
title: Quick Start Guide
---

# Quick Start Guide

Get ZaroPGx up and running in no time with this step-by-step guide.

## Prerequisites

- **Docker and Docker Compose** installed on your system
- **8+ GB RAM** (64+ GB recommended for the most memory-intensive operations)
- **50+ GB free drive space** (1000+ GB for the most storage-intensive workflows)
- **Internet connection** for initial setup (to download containers and reference materials)

## Installation

### 1. Clone the Repository

```bash
git clone https://github.com/Zaroganos/ZaroPGx.git
cd ZaroPGx
```

### 2. Choose Your Environment

**Local development and testing:**
```bash
cp .env.local .env
```
**Production deployment:**
```bash
cp .env.production .env
```
**Custom configuration (edit as needed):**
```bash
cp .env.example .env
```

### 3. Start the Services

```bash
docker compose up -d --build && docker compose logs app -f
```

This will:
- Download and build the software stack;
- Initialize the PostgreSQL database;
- Download reference materials (if not cached);
- Start all services

### 4. Verify Installation

Check that all services are running:
```bash
docker compose ps
# for all logs try
docker compose logs -f
```

You should see all services with "Up" status.

## First Run-through

### 1. Access the Web Interface

Open your browser and navigate to:
- **Main Application**: http://localhost:8765

### 2. Upload a Sample File

- If you would like to run the demo instead with a pre-loaded sample, click "Run Demo"

1. Click "Browse..." or drag and drop the sample to be processed
2. Click "Upload" to start the workflow, or click "View Header" to check some details from the sample's header first

### 3. Monitor Progress

- Watch the real-time progress updates

### 4. View Results

Once complete, you'll see:
- **PDF Report**: Custom ZaroPGx pharmacogenomic report
- **HTML Report**: Custom ZaroPGx interactive pgx report with detailed analysis and visualizations
- **PharmCAT Report**: PharmCAT outputs (if enabled; HTML Report (main), JSON output, TSV calls-only output)

## Supported File Types

| Format | Description | Processing Path |
|--------|-------------|-----------------|
| **VCF** | Variant Call Format | Direct → PyPGx → PharmCAT |
| **BAM** | Binary Alignment Map | hlatyping → PyPGx → PharmCAT |
| **CRAM** | Compressed BAM | GATK → hlatyping → PyPGx → PharmCAT |
| **SAM** | Sequence Alignment Map | GATK → hlatyping → PyPGx → PharmCAT |
| **FASTQ** | Raw sequencing data | hlatyping → GATK → PyPGx → PharmCAT |

## Next Steps

- **File formats**: {doc}`file-formats`
- **Reports**: {doc}`reports`
- **Advanced settings**: {doc}`../advanced-configuration`
- **Troubleshooting**: {doc}`troubleshooting`
