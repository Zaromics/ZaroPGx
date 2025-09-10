---
title: Architecture Overview
---

## High-level

The system is a docker-compose stack centered on a FastAPI app with supporting bioinformatics and visualization services. Key components:

- FastAPI app (`app`): Web UI and API, orchestrates PGx operations.
- PostgreSQL (`db`): Main database with multiple schemas (e.g., `fhir`, `cpic`, `user_data`).
- PharmCAT (`pharmcat`): API wrapper around PharmCAT JAR for phenotype/report generation.
- GATK API (`gatk-api`): Wrapper around GATK tooling.
- PyPGx (`pypgx`): Service exposing PyPGx-based calling for supported genes.
- HAPI FHIR (`fhir-server`): External FHIR server for EHR integration.
- Nextflow runner (`nextflow`): Orchestrates pipelines; mounts the project workspace.
- Kroki + Mermaid (`kroki`, `mermaid`): Renders diagrams (Mermaid, Graphviz, etc.).
- Genome downloader (`genome-downloader`): Retrieves reference genomes.

Ports (host → container):

- App UI/API: `${BIND_ADDRESS:-8765} → 8000`
- DB: `5444 → 5432`
- PharmCAT: `5001 → 5000`
- GATK API: `5002 → 5000`
- PyPGx: `5053 → 5000`
- FHIR: `8090 → 8080`
- Kroki: `8001 → 8000`
- Nextflow runner: `5055 → 5055`
- Docs (this service): `5070 → 8000`

## Data mounts

Shared host directories used across services:

- `./data` for uploads, reports, and inter-service artifacts
- `./reference` for reference genomes

## Workflows

See `app/visualizations/workflow.md` and `app/visualizations/workflow.mmd` for diagrams and detailed flows. The app also integrates with Kroki to render diagrams in the UI.
