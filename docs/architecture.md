---
title: Architecture Overview
curation: partial
---

# Architecture Overview

## High-level

The system is a docker compose stack centered on a core FastAPI app with supporting bioinformatics and visualization services. Key components:

- FastAPI app (`app`): Web UI and API, orchestrates PGx operations.
- PostgreSQL (`db`): Main database.
- PharmCAT (`pharmcat`): API wrapper around PharmCAT JAR for phenotype/report generation.
- GATK API (`gatk-api`): Wrapper around GATK tooling.
- PyPGx (`pypgx`): Service exposing PyPGx-based calling for supported genes.
- ZaroHLA (`zarohla`): OptiType v1.5 HLA class I typing, gated on `OPTITYPE_ENABLED`.
- HAPI FHIR (`fhir-server`): Bundled FHIR R4 server for EHR/PHR integration.
- Nextflow runner (`nextflow`): Executes the `pipelines/pgx` pipeline; mounts the project workspace.
- Kroki + Mermaid (`kroki`, `mermaid`): Renders diagrams (Mermaid, Graphviz, etc.).
- Genome downloader (`genome-downloader`): Retrieves reference genomes.
- Sphinx docs (`docs`): Live-reloading docs preview; behind the `optional` Compose profile.

Not in the stack yet: `docker/mtdna-server-2/` exists in the repo and mtDNA calling is on the
roadmap, but no `mtdna-server-2` service is defined in `compose.yml`.

Ports, exactly as `compose.yml` publishes them. Every service other than `app` binds
`${INTERNAL_BIND_ADDRESS:-127.0.0.1}` — i.e. **loopback only by default**; they do not
authenticate, so reach them over an SSH tunnel rather than re-pointing that variable.

| Service | Host binding | Container port |
|---|---|---|
| `app` (UI/API) | `${BIND_ADDRESS:-8765}` — the only operator-facing port | 8000 |
| `db` | `${INTERNAL_BIND_ADDRESS:-127.0.0.1}:5444` | 5432 |
| `genome-downloader` | `${INTERNAL_BIND_ADDRESS:-127.0.0.1}:5050` | 5050 |
| `pharmcat` | `${INTERNAL_BIND_ADDRESS:-127.0.0.1}:5001` | 5000 |
| `gatk-api` | `${INTERNAL_BIND_ADDRESS:-127.0.0.1}:5002` | 5000 |
| `pypgx` | `${INTERNAL_BIND_ADDRESS:-127.0.0.1}:5053` | 5000 |
| `zarohla` | `${INTERNAL_BIND_ADDRESS:-127.0.0.1}:5060` | 5000 |
| `fhir-server` | `${INTERNAL_BIND_ADDRESS:-127.0.0.1}:8090` | 8080 |
| `kroki` | `${INTERNAL_BIND_ADDRESS:-127.0.0.1}:8001` | 8000 |
| `docs` (profile `optional`) | `${INTERNAL_BIND_ADDRESS:-127.0.0.1}:5070` | 8000 |
| `mermaid` | *not published* (`expose` only) | 8002 |
| `nextflow` | *not published at all* (`expose: 5055`) | 5055 |

`nextflow` is deliberately unpublished: its `POST /run` is unauthenticated and the service
bind-mounts `/var/run/docker.sock`, so a host mapping would be remote code execution. The app
reaches it as `http://nextflow:5055` over the Compose network.

## Data mounts

Shared host directories used across services:

- `./data` for uploads, reports, and inter-service artifacts
- `./reference` for reference genomes

## Workflows

See `app/visualizations/workflow.md` and `app/visualizations/workflow.mmd` for diagrams and detailed flows. The app also integrates with Kroki to render diagrams in the UI.

## Further Reading

For detailed technical architecture, design principles, and implementation details, see the [System Architecture](developer/architecture.md) documentation.