---
title: Home
---

# {{ project_name }} Documentation

Welcome to the comprehensive documentation for {{ project_name }} — a containerized pharmacogenomics (PGx) platform that processes genetic data and generates clinical reports.

## For Users

If you're looking to use ZaroPGx for pharmacogenomic analysis:

```{toctree}
:maxdepth: 2
:caption: User Guide

user/quick-start
user/installation
user/usage
user/file-formats
user/reports
user/troubleshooting
user/faq
```

## For Developers

If you're contributing to or extending ZaroPGx:

```{toctree}
:maxdepth: 2
:caption: Developer Guide

developer/architecture
developer/api-reference
developer/development-setup
developer/contributing
developer/deployment
developer/testing
```

## Quick Navigation

- **Getting Started**: {doc}`user/quick-start`
- **API Reference**: {doc}`developer/api-reference`
- **Architecture Overview**: {doc}`developer/architecture`
- **Troubleshooting**: {doc}`user/troubleshooting`

## What is ZaroPGx?

ZaroPGx is a self-hosted, containerized platform that:

- **Processes genomic data** (VCF, BAM, CRAM, FASTQ) using industry-standard tools
- **Performs comprehensive allele calling** across 23+ pharmacogenes using PharmCAT, PyPGx, and OptiType
- **Generates clinical reports** with actionable pharmacogenomic recommendations
- **Maintains data privacy** through local deployment with no external data transmission
- **Integrates with EHR systems** via FHIR export capabilities

The platform orchestrates multiple bioinformatics tools through Docker containers, providing a unified interface for pharmacogenomic analysis that would typically require extensive command-line expertise.