### ZaroPGx — Pharmacogenomic Report Generator

![Screenshot 2025-05-02 011020](https://github.com/user-attachments/assets/f352c10a-23ba-4583-a6b1-196ae32c06ae)

Containerized pipeline that processes genetic data and generates pharmacogenomic reports guided by CPIC resources. The system is under active development; expect breaking changes.

### Status

This project is a work in progress and not production‑ready.

### Features (current state)

- Process VCF files directly; BAM/CRAM → GATK path is scaffolded but not fully implemented in the app flow yet
- Allele calling via PharmCAT (pipeline v3.0.0); optional PyPGx service for CYP2D6 when enabled
- Generate reports: PDF and interactive HTML; optionally include original PharmCAT HTML/JSON/TSV outputs
- REST API and web UI for uploads and status; authentication is disabled by default in development mode
- Optional export of reports to a bundled HAPI FHIR server

### Architecture

Containerized services orchestrated with Docker Compose:

- PostgreSQL 15 (schemas: `cpic`, `user_data`, `reports`)
- FastAPI application (web UI, API, report generation, SSE progress)
- PharmCAT wrapper service (Flask) invoking PharmCAT pipeline v3.0.0
- GATK API service (HTTP wrapper; heavy memory usage)
- PyPGx service (CYP2D6 star‑allele calling)
- Reference genome downloader
- HAPI FHIR server (optional integration target)

Default host ports (host → container):

- App/UI: 8765 → 8000
- PostgreSQL: 5444 → 5432
- PharmCAT wrapper: 5001 → 5000
- GATK API: 5002 → 5000
- PyPGx: 5053 → 5000
- Genome downloader: 5050 → 5050
- HAPI FHIR: 8090 → 8080

Data directories (mounted):

- Shared data: `./data` → `/data`
- Reference data: `./reference` → `/reference`
- Reports: `/data/reports/<file_id>/` (per‑job directory)

### Requirements

- Docker and Docker Compose
- Git
- First run requires internet to fetch images and reference genomes
- Minimum: 8 GB RAM (≥16 GB recommended if using GATK), 20 GB disk (≫100 GB for WGS)

### Quick start

1) Clone the repo

   ```bash
   git clone https://github.com/Zaroganos/ZaroPGx.git
   cd ZaroPGx
   ```

2) Choose your environment configuration:

   **Local Development (default):**
   ```bash
   cp .env.local .env
   # edit .env as needed (at minimum set SECRET_KEY)
   ```

   **Production/Web Deployment:**
   ```bash
   cp .env.production .env
   # edit .env as needed (at minimum set SECRET_KEY)
   ```

   **Custom Configuration:**
   ```bash
   cp .env.example .env
   # edit .env as needed
   ```

3) Start services

   **Using default .env:**
   ```bash
   docker compose up -d --build
   ```

   **Using specific environment file:**
   ```bash
   docker compose --env-file .env.local up -d --build
   docker compose --env-file .env.production up -d --build
   ```

4) Access

- Web UI: `http://localhost:8765`
- API docs: `http://localhost:8765/docs`
- HAPI FHIR (optional): `http://localhost:8090`

**Environment Differences:**
- **Local Development**: Binds to localhost only, uses development subnet
- **Production/Web**: Binds to all interfaces (0.0.0.0), uses production subnet

### Using the web UI

1) Open `http://localhost:8765`
2) Upload a VCF file
3) Observe progress; on completion you’ll see links to PDF and interactive HTML reports

### REST API usage

- Upload a genomic file (VCF; BAM/CRAM path is not fully wired end‑to‑end yet):

```bash
curl -X POST \
  -F "file=@test_data/sample_cpic.vcf" \
  -F "patient_identifier=patient123" \
  http://localhost:8765/upload/genomic-data
```

- Check processing status:

```bash
curl http://localhost:8765/status/<file_id>
```

- Get report URLs (PDF/HTML/interactive and optional PharmCAT artifacts):

```bash
curl http://localhost:8765/reports/<file_id>
```

- Generate a report (utility endpoint; separate from the upload pipeline):

```bash
curl -X POST http://localhost:8765/reports/generate \
  -H "Content-Type: application/json" \
  -d '{"patient_id":"1","file_id":"1","report_type":"comprehensive"}'
```

Notes

- Development mode disables authentication by default (`ZAROPGX_DEV_MODE=true`); tokens are not required.
- Reports are written to `/data/reports/<file_id>/` with filenames:
  - `<file_id>_pgx_report.pdf`
  - `<file_id>_pgx_report_interactive.html`
  - Optional PharmCAT originals: `<file_id>_pgx_pharmcat.{html,json,tsv}`

### Sample data

Sample VCFs available in the repo:

- `app/static/demo/pharmcat.example.vcf`
- `test_data/sample_cpic.vcf`
- Additional examples under `test_data/`

### Project structure

```
ZaroPGx/
├── app/                    # FastAPI app, templates, static assets
│   ├── api/                # API routers, DB helpers, models
│   ├── pharmcat/           # PharmCAT client integration
│   └── reports/            # Report generation (PDF/HTML, FHIR export)
├── db/                     # Initialization and migrations
│   └── migrations/cpic/    # CPIC schema and sample data
├── docker/                 # Service Dockerfiles and wrappers
└── docker-compose.yml      # Orchestration (environment-specific via .env files)
```

### Service specifics

- PostgreSQL 15 with initialization under `db/init` and `db/migrations`
- PharmCAT pipeline v3.0.0 (Java 17) with a Flask wrapper API on port 5000 (exposed as 5001 on host)
- FastAPI app (Python 3.12; dependencies in `pyproject.toml`/`uv.lock`)
- GATK API service and PyPGx service are available via internal endpoints (`GATK_API_URL`, `PYPGX_API_URL`); the app currently stubs GATK/PyPGx steps in the background workflow

### PharmCAT outputs and report handling

- Each run writes a per‑job directory: `/data/reports/<file_id>/`
- The app consistently generates its own reports (PDF + interactive HTML)
- When available, original PharmCAT reports are copied with normalized names (`<file_id>_pgx_pharmcat.*`)
- A single global `latest_pharmcat_report.json` is not maintained; rely on per‑job directories

### FHIR export (optional)

- HAPI FHIR server is bundled and exposed at `http://localhost:8090`
- Report export endpoint: `POST /reports/{report_id}/export-to-fhir`

### Dependency management

- Python dependencies are managed via `pyproject.toml` (locked in `uv.lock`)
- Container‑specific dependencies are installed in each Dockerfile

### Troubleshooting

- Service connectivity: confirm the `pgx-network` bridge exists and containers are healthy
- File processing: ensure input VCF is valid and non‑empty
- PDF generation: WeasyPrint is used; if PDF fails, an HTML fallback may be written alongside

### Contributing

1) Create a feature branch: `git checkout -b feature/your-change`
2) Commit: `git commit -m "Describe your change"`
3) Push: `git push origin feature/your-change`
4) Open a Pull Request

### Acknowledgements

- PharmCAT (Pharmacogenomics Clinical Annotation Tool). See methods: Sangkuhl K, Whirl-Carrillo M, et al. Clinical Pharmacology & Therapeutics. 2020;107(1):203–210. Project: https://github.com/PharmGKB/PharmCAT
- GATK (Genome Analysis Toolkit). Core papers: McKenna A, et al. Genome Research. 2010;20(9):1297–1303; DePristo MA, et al. Nature Genetics. 2011;43(5):491–498. Guidance: https://gatk.broadinstitute.org/
- PyPGx for star‑allele calling. References: Lee S‑B, et al. PLOS ONE. 2022 (ClinPharmSeq); Lee S‑B, et al. Genetics in Medicine. 2018 (Stargazer); Lee S‑B, et al. Clinical Pharmacology & Therapeutics. 2019 (Stargazer, 28 genes). Docs: https://pypgx.readthedocs.io/en/latest/index.html

### License

[AGPLv3](LICENSE)

Copyright (C) 2024-2025 Iliya Yaroshevskiy
This project is licensed under the AGPLv3 License.
