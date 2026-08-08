---
title: Installation Guide
curation: partial
---

# Installation Guide

Detailed installation instructions for different deployment scenarios.

## System Requirements (minimum for VCF inputs; recommended for others)
- **CPU**: 4 cores (8+ recommended)
- **RAM**: 16 GB (64+ GB recommended)
- **Storage**: 50 GB free space (1000+ GB recommended)
- **OS**: Linux, macOS, or Windows with WSL2
- **Network**: Stable internet needed for the first run only — see [Network and offline use](#network-and-offline-use)
- **Docker and docker compose**

### Network and offline use

The first start needs the internet, and needs a fair amount of it: it pulls (or builds) the
container images, fetches reference genomes through the `genome-downloader` service, and lets
PharmCAT download its GRCh38 reference data. Avoid a metered connection for that run.

After that, an analysis is a local affair:

- Nextflow itself is baked into the image at a pinned version — it is not downloaded at run time.
- The pipeline's processes declare no per-task containers (`pipelines/pgx/main.nf`); each step
  calls a service already in the stack over the private Compose network, so nothing is pulled
  mid-run.
- `NXF_HOME` is `/opt/nextflow`, bind-mounted to `./data/nextflow`, so whatever Nextflow caches
  survives restarts.

**Caveat:** a genuinely air-gapped install has not been tested. Nextflow can attempt to fetch
plugins on startup, and neither that nor a fully offline first run has been verified here. If
you need an air-gapped deployment, do the first run on a connected machine and confirm before
you commit to it.

## Docker Installation
- If you do not have docker and docker composed installed, the easiest way to get up and running is: 
- https://www.docker.com/products/docker-desktop/
- This is particularly handy if you are on Windows w/ WSL2 or macOS.
- On Windows, ensure WSL2 backend is enabled in Docker Desktop.
- Otherwise, here's an example for Debian/Ubuntu-based systems:

**Ubuntu/Debian:**
```bash
sudo apt update
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER
newgrp docker
```

### Verify Installation

```bash
docker --version
docker compose version
```

## Deploy ZaroPGx via docker compose

### 1. Grab Repository

```bash
git clone https://github.com/Zaromics/ZaroPGx.git
cd ZaroPGx
```

### 2. Review environment configuration options

Choose an appropriate starting .env file: local|production|custom

All three are tracked templates with the same set of keys; they differ only in values. All
three ship `SECRET_KEY=` and `DB_PASSWORD=` blank on purpose — `start-docker.sh` /
`start-docker.ps1` generate per-install values on first run.

**Local:** (`BIND_ADDRESS=8765`, `NETWORK_SUBNET=172.20.0.0/16`, `LOG_LEVEL=DEBUG`)
- App published on the host port only, no explicit interface
- Front-door gate open (`ZAROPGX_AUTH_MODE=open`)
- Debug logging enabled

```bash
cp .env.local .env
```

**Production:** (`BIND_ADDRESS=0.0.0.0:8765`, `NETWORK_SUBNET=172.28.0.0/16`, `LOG_LEVEL=INFO`)
- App bound to all interfaces
- Optimized logging

```bash
cp .env.production .env
```

```{warning}
`.env.production` does **not** enable authentication. It ships `ZAROPGX_AUTH_MODE=open`
exactly like `.env.local`, and `ZAROPGX_DEV_MODE=false` does not enable auth either. If you
expose the app, set `ZAROPGX_AUTH_MODE=password` and `ZAROPGX_AUTH_PASSWORD=...` yourself, and
put it behind a reverse proxy with TLS.
```

**Custom** — the fullest template, with every key commented inline:
```bash
cp .env.example .env
```

### 3. Configure options via environment variables

Edit `.env` with your settings. The keys most installs touch:

```bash
# Leave blank to have start-docker generate one; never commit a real value
SECRET_KEY=
# Database password. Leave blank on a fresh install (start-docker generates it).
# Do NOT rotate this once the zaropgx_pgdata volume exists unless you also ALTER ROLE.
DB_PASSWORD=
# Host binding for the app: "8765" (host port) or "0.0.0.0:8765" (all interfaces)
BIND_ADDRESS=8765
# Host interface for the internal services; loopback by default, and it should stay there
INTERNAL_BIND_ADDRESS=127.0.0.1
# Which pre-built image tag to pull (or "latest")
ZAROPGX_TAG=0.2.8
# Feature toggles
GATK_ENABLED=true
PYPGX_ENABLED=true
OPTITYPE_ENABLED=true
```

The variable is `DB_PASSWORD`, not `POSTGRES_PASSWORD` — compose derives the container's
`POSTGRES_PASSWORD` from it, and hard-fails at parse time if it is unset.

See {doc}`../advanced-configuration` for the complete list.

### 4. Start Services

The stack runs **pre-built images from Docker Hub** (`zaromicsresearch/zaropgx-*`) by default,
so no build wait:

```bash
docker compose pull
docker compose up -d && docker compose logs app -f
```

To build from your checkout instead — you changed the source, or you want a platform Docker Hub
does not publish — add `--build`; the `build:` sections in `compose.yml` are the fallback:

```bash
docker compose up -d --build && docker compose logs app -f
```

Expect a long first start either way: PharmCAT downloads its GRCh38 reference (~8-15 min) before
it reports healthy, and the app waits on that. A local build adds substantially more.

### 5. Verify Installation

Check service status:
```bash
docker compose ps
```

## Service Ports

Only the app is meant to be reachable from other machines. Every other published port binds
`${INTERNAL_BIND_ADDRESS:-127.0.0.1}` — loopback on the Docker host — because none of those
services authenticate. `curl http://localhost:5001/health` from the host still works; reaching
them from elsewhere is deliberately an SSH tunnel away
(`ssh -L 5444:127.0.0.1:5444 <host>`), not an `INTERNAL_BIND_ADDRESS` change.

| Service | Host binding | Container Port | Description |
|---------|--------------|----------------|-------------|
| **App/UI** | `${BIND_ADDRESS:-8765}` | 8000 | Main web interface and API |
| **Database** | 127.0.0.1:5444 | 5432 | PostgreSQL 18 |
| **Genome downloader** | 127.0.0.1:5050 | 5050 | Reference genome/annotation fetcher |
| **PharmCAT** | 127.0.0.1:5001 | 5000 | PharmCAT analysis service |
| **GATK API** | 127.0.0.1:5002 | 5000 | GATK preprocessing service |
| **PyPGx** | 127.0.0.1:5053 | 5000 | PyPGx allele calling service |
| **ZaroHLA** | 127.0.0.1:5060 | 5000 | OptiType HLA class I typing |
| **FHIR Server** | 127.0.0.1:8090 | 8080 | HAPI FHIR R4 server |
| **Kroki** | 127.0.0.1:8001 | 8000 | Diagram rendering service |
| **Docs** | 127.0.0.1:5070 | 8000 | Sphinx preview; `--profile optional` only |
| **Mermaid** | not published | 8002 | Kroki's Mermaid renderer, internal only |
| **Nextflow** | **not published** | 5055 | Pipeline executor; see note below |

`nextflow` has no host mapping by design — its `POST /run` is unauthenticated and it
bind-mounts the Docker socket, so publishing it would be remote code execution. The app calls
it as `http://nextflow:5055` inside the Compose network.

`docker/mtdna-server-2/` exists in the repository but is not yet a Compose service; mtDNA
calling is roadmap work, not a running container.

## Initial Setup

### 1. Download Reference Data

The system will automatically download reference genomes on first run:

```bash
docker compose logs genome-downloader
ls -la reference/
```

### 2. Initialize Postgres Database

The database initializes automatically with:
- CPIC guidelines and data
- User management tables
- Workflow tracking tables

### 3. Test Installation

Upload a test file:
```bash
curl -X POST \
  -F "file=@test_data/sample_cpic.vcf" \
  -F "sample_identifier=test_sample" \
  http://localhost:8765/upload/genomic-data
```


## Troubleshooting

### Common Issues

**Port conflicts:**
- Check what's using ports
```bash
netstat -tulpn | grep :8765
```
- Change ports accordingly in .env


**Permission errors:**
```bash
sudo chown -R $USER:$USER .
```

**Out of storage space:**
- Check drive space
```bash
df -h
```
- Clean up docker files (make sure you back up anything important!)
```bash
docker system prune -a
```

**Out of memory issues:**
- Increase Docker memory limit
- In Docker Desktop: Settings → Resources → Memory

### Logs and Debugging

View service logs:
```bash
docker compose logs -f
```

## Next Steps

- **Advanced Configuration**: {doc}`../advanced-configuration`
- **Usage Guide**: {doc}`usage`
- **File Formats**: {doc}`file-formats`
- **Deployment Guide**: {doc}`../developer/deployment`
