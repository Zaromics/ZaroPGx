---
title: Installation Guide
---

# Installation Guide

Detailed installation instructions for different deployment scenarios.

## System Requirements (minimum for VCF inputs; recommended for others)
- **CPU**: 4 cores (8+ recommended)
- **RAM**: 8 GB (64+ GB recommended)
- **Storage**: 50 GB free space (1000+ GB recommended)
- **OS**: Linux, macOS, or Windows with WSL2
- **Network**: Stable internet needed for initial setup only (TODO: check nextflow behavior)
- **Docker and docker compose**

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
git clone https://github.com/Zaroganos/ZaroPGx.git
cd ZaroPGx
```

### 2. Review environment configuration options

Choose an appropriate starting .env file: local|production|custom

**Local:**
- Binds to localhost only
- Development subnet (172.28.0.0/16)
- Authentication disabled by default
- Debug logging enabled

```bash
cp .env.local .env
```

**Production:**
- Binds to all interfaces (0.0.0.0)
- Production subnet
- Authentication enabled
- Optimized logging

```bash
cp .env.production .env
```

**Custom**
```bash
cp .env.example .env
```

### 3. Configure options via environment variables

Edit `.env` file with your settings:

```bash
# Required for production
SECRET_KEY=your-secret-key-here
# Database settings
POSTGRES_PASSWORD=your-db-password
# Optional: Customize ports
BIND_ADDRESS=8765
# Optional: Feature toggles
GATK_ENABLED=true
PYPGX_ENABLED=true
OPTITYPE_ENABLED=true
```

See {doc}`../advanced-configuration` for complete .env conf options.

### 4. Start Services

```bash
docker compose up -d --build && docker compose logs app -f
```

### 5. Verify Installation

Check service status:
```bash
docker compose ps
```

## Service Ports

| Service | Host Port | Container Port | Description |
|---------|-----------|----------------|-------------|
| **App/UI** | 8765 | 8000 | Main web interface and API |
| **Database** | 5444 | 5432 | PostgreSQL database |
| **PharmCAT** | 5001 | 5000 | PharmCAT analysis service |
| **GATK API** | 5002 | 5000 | GATK preprocessing service |
| **PyPGx** | 5053 | 5000 | PyPGx allele calling service |
| **FHIR Server** | 8090 | 8080 | HAPI FHIR server |
| **Kroki** | 8001 | 8000 | Diagram rendering service |

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
