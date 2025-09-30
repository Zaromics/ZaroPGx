---
title: Installation Guide
---

# Installation Guide

Detailed installation instructions for different deployment scenarios.

## System Requirements

### Minimum Requirements (will work with VCF sample )
- **CPU**: 4 cores (8+ recommended)
- **RAM**: 8 GB (16+ GB recommended)
- **Storage**: 30 GB free space (500+ GB for WGS)
- **OS**: Linux, macOS, or Windows with WSL2
- **Docker**: 20.10+ with Docker Compose 2.0+

### Recommended Requirements
- **CPU**: 8+ cores
- **RAM**: 32+ GB
- **Storage**: 1+ TB SSD
- **Network**: Stable internet for initial setup

## Docker Installation

### Install Docker

**Ubuntu/Debian:**
```bash
# Update package index
sudo apt update

# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Add user to docker group
sudo usermod -aG docker $USER
newgrp docker
```

**macOS:**
- Download Docker Desktop from https://www.docker.com/products/docker-desktop
- Install and start Docker Desktop

**Windows:**
- Download Docker Desktop from https://www.docker.com/products/docker-desktop
- Enable WSL2 backend for better performance
- Install and start Docker Desktop

### Verify Installation

```bash
docker --version
docker compose version
```

## ZaroPGx Installation

### 1. Clone Repository

```bash
git clone https://github.com/Zaroganos/ZaroPGx.git
cd ZaroPGx
```

### 2. Environment Configuration

Choose the appropriate environment file:

#### Local Development
```bash
cp .env.local .env
```

**Features:**
- Binds to localhost only
- Development subnet (172.28.0.0/16)
- Authentication disabled by default
- Debug logging enabled

#### Production Deployment
```bash
cp .env.production .env
```

**Features:**
- Binds to all interfaces (0.0.0.0)
- Production subnet
- Authentication enabled
- Optimized logging

#### Custom Configuration
```bash
cp .env.example .env
# Edit .env with your specific settings
```

### 3. Configure Environment Variables

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

### 4. Start Services

```bash
# Start all services
docker compose up -d --build

# Or start specific services
docker compose up -d --build app db
```

### 5. Verify Installation

Check service status:
```bash
docker compose ps
```

Expected output:
```
NAME                IMAGE                    COMMAND                  SERVICE             CREATED             STATUS              PORTS
zaro-pgx-app        zaro-pgx-app:latest     "uvicorn app.main:app"   app                 2 minutes ago       Up 2 minutes        0.0.0.0:8765->8000/tcp
zaro-pgx-db         postgres:15             "docker-entrypoint.s…"   db                  2 minutes ago       Up 2 minutes        0.0.0.0:5444->5432/tcp
zaro-pgx-pharmcat   zaro-pgx-pharmcat:lat   "python app.py"          pharmcat            2 minutes ago       Up 2 minutes        0.0.0.0:5001->5000/tcp
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
# Monitor download progress
docker compose logs genome-downloader

# Check downloaded references
ls -la reference/
```

### 2. Initialize Database

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

## Configuration Options

### Environment Variables

See {doc}`../advanced-configuration` for complete configuration options.

### Docker Compose Overrides

Create `docker-compose.override.yml` for local customizations:

```yaml
version: '3.8'
services:
  app:
    environment:
      - LOG_LEVEL=DEBUG
    volumes:
      - ./custom-config:/app/config
```

## Troubleshooting Installation

### Common Issues

**Port conflicts:**
```bash
# Check what's using ports
netstat -tulpn | grep :8765
# Change ports in .env file
```

**Permission errors:**
```bash
# Fix Docker permissions
sudo chown -R $USER:$USER .
# Or run with sudo (not recommended)
```

**Out of disk space:**
```bash
# Clean up Docker
docker system prune -a
# Check disk usage
df -h
```

**Memory issues:**
```bash
# Increase Docker memory limit
# In Docker Desktop: Settings → Resources → Memory
```

### Logs and Debugging

View service logs:
```bash
# All services
docker compose logs

# Specific service
docker compose logs app
docker compose logs db

# Follow logs in real-time
docker compose logs -f app
```

## Next Steps

- **Configure your environment**: {doc}`../advanced-configuration`
- **Learn to use the system**: {doc}`usage`
- **Understand file formats**: {doc}`file-formats`
- **Set up monitoring**: {doc}`../developer/deployment`
