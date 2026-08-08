---
title: Home
curation: partial
---

# {{ project_name }} Documentation

Welcome to the documentation for {{ project_name }} — a containerized pharmacogenomics platform that processes genetic data and generates comprehensive reports.

## Overview

Orientation, the service/port map, and every configuration knob:

```{toctree}
:maxdepth: 2
:caption: Overview

getting-started
architecture
advanced-configuration
```

## For Users

Use ZaroPGx to perform pharmacogenomic analysis:

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

Contributing to or extending ZaroPGx:

```{toctree}
:maxdepth: 2
:caption: Developer Guide

developer/architecture
developer/api-reference
developer/development-setup
developer/contributing
developer/deployment
developer/testing
samples/README
```

## Roadmap

```{toctree}
:maxdepth: 1
:caption: Roadmap

to-do
```

## Quick Navigation

- **Getting Started**: {doc}`user/quick-start`
- **API Reference**: {doc}`developer/api-reference`
- **Architecture Overview**: {doc}`developer/architecture`
- **Troubleshooting**: {doc}`user/troubleshooting`

## What is ZaroPGx?

ZaroPGx is a self-hosted, containerized platform that:

- **Processes genomic data** (VCF, BAM, CRAM, FASTQ) using industry-standard tools
- **Performs comprehensive allele calling** across **91 pharmacogenes** using PharmCAT, PyPGx and
  OptiType — 23 of them make up PharmCAT's guideline-reporting panel, and PyPGx covers 87
  (68 of which are outside that panel). The authoritative list is `config/genes.json`.
- **Generates clinical reports** with actionable pharmacogenomic recommendations
- **Maintains data privacy** through local deployment with no external data transmission
- **Integrates with EHR systems** via FHIR export capabilities

The platform orchestrates multiple bioinformatics tools through Docker containers, providing a unified interface for pharmacogenomic analysis that would typically require extensive command-line expertise.