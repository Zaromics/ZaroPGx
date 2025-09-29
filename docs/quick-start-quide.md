## Quick Start Guide

This guide helps you self-host ZaroPGx locally using Docker Compose.

### Prerequisites
- **Docker Desktop**: Install and start Docker Desktop
  - Windows: enable **WSL2** integration
  - macOS/Linux: standard Docker Engine is fine
- **Git**: To clone the repository
- Optional: **Bash** (to use `start-docker.sh`) or use the PowerShell/Docker commands below

### Clone the repository
```bash
git clone https://github.com/zarolab/ZaroPGx.git
cd ZaroPGx
```

### Environment setup
- Copy an example environment file and adjust as needed:
```bash
cp .env.example .env
```
- At minimum, set the following in `.env` (defaults work for local dev):
  - `SECRET_KEY` (any random string for local use)
  - `NETWORK_SUBNET` (optional; default `172.28.0.0/16`)
  - Service URLs are already wired for internal networking; no change needed

See `docs/advanced-configuration.md` for all environment variables.

### Start the stack

Option A — Use the helper script (Git Bash, WSL, or Linux/macOS):
```bash
./start-docker.sh
```

Option B — Run Docker Compose directly (PowerShell or Bash):
```bash
mkdir -p data/uploads data/reports data/nextflow/work data/nextflow/assets reference
docker compose down --remove-orphans
docker compose up -d --build
```

### Verify services
- Main app (FastAPI + Web UI): `http://localhost:8765`
- Health check: `http://localhost:8765/health`

Optional individual services (useful for debugging):
- PharmCAT: `http://localhost:5001/health`
- GATK API: `http://localhost:5002/health`
- PyPGx: `http://localhost:5053/health`
- HLAtyping: `http://localhost:5060/health`
- Kroki: `http://localhost:8001/health`
- FHIR Server metadata: `http://localhost:8090/fhir/metadata`

Check container status and logs:
```bash
docker compose ps
docker compose logs -f
```

### Use the Web UI
1. Open `http://localhost:8765`
2. Upload a VCF file (see `test_data/` for examples)
3. Start a run and monitor progress in the UI
4. Generated reports appear under `data/reports`

### Command-line testing (optional)
Basic health check:
```bash
curl http://localhost:8765/health
```

### Stop and clean up
```bash
docker compose down
```
Persistent data lives under `./data` and `./reference` (and named volumes defined in `docker-compose.yml`).

### Notes
- The main app binds to host port `8765` → container `8000` (configurable via `BIND_ADDRESS` in `.env`).
- The app depends on multiple services. Compose handles ordering and networking automatically.
- For advanced setups (production, resource limits, Nextflow), see:
  - `docker-compose.yml`
  - `docs/advanced-configuration.md`
  - `docker/*` for service-specific details
