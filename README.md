# ZaroPGx — Pharmacogenomic Report Generator

<img width="1541" height="900" alt="Screenshot 2025-08-22 143144" src="https://github.com/user-attachments/assets/57450a14-d392-4d38-85f6-911a7e7b962f" />


A containerized pipeline that processes genetic data and generates comprehensive pharmacogenomic reports guided by CPIC and other institutional resources. Nextflow is used to orchestrate an intelligent workflow which integrates GATK preprocessing, OptiType and PyPGx allele calling, and PharmCAT with outside calls to unlock PharmCAT's full potential across 23 core pharmacogenes, with additional coverage for approximately 64 additional pharmacogenes with lower evidence clinical actionability. Report data can be exported to Electronic Health Records or Personal Health Records via HAPI FHIR integration. Designed as a self-hostable docker compose stack, ZaroPGx enables absolute data privacy and security when run locally. That said, environmental configurations are provided for both local and internet-facing deployment, which allows the software to be made accessible to others over the internet.

## Status

This project is in active development and not production-ready. Core functionality is being implemented incrementally.

## Intended Function

The system is designed to:

1. **Accept genomic data files** (VCF, BAM, FASTQ) and preprocess them using GATK when necessary
2. **Perform allele calling** via PyPGx and OptiType for comprehensive pharmacogene coverage
3. **Execute PharmCAT analysis** with PyPGx and OptiType providing outside calls to unlock PharmCAT's full potential across 23 core pharmacogenes
4. **Generate comprehensive reports** covering actionable results for 23 pharmacogenes and notable findings for approximately 64 additional pharmacogenes
5. **Export data** to Electronic/Personal Health Record via the integrated HAPI FHIR service
6. **Maintain privacy** through self-hosted deployment, ensuring no genomic data leaves the user's network

## Current Implementation Status

- **VCF Processing**: Fully implemented for hg38, needs more work to implement hg19 as well
- **SAM/CRAM → GATK Pipeline**: Scaffolded but not fully integrated in the application flow
- **OptiType Integration**: Scaffolded Nextflow hlatyping container which uses OptiType
- **PyPGx Integration**: Service is integrated with main pipeline, optimization in progress to differentiate pipeline paths
- **PharmCAT with Outside Calls**: Core PharmCAT service available, PyPGx and OptiType outside calls are in testing
- **Comprehensive Reporting**: Basic PDF and interactive HTML generation is being developed
- **FHIR Export**: HAPI FHIR server integrated, export / query response functionality in development

## Features (Current State)

- Process VCF files directly; BAM/CRAM → GATK path is scaffolded but not fully implemented in the app flow yet
- Allele calling via OptiType, PyPGx, and PharmCAT (pipeline v3.0.1)
- Generate reports: PDF and interactive HTML; optionally include original PharmCAT HTML/JSON/TSV outputs
- REST API and web UI for uploads and status; authentication is disabled by default in development mode
- Optional export of reports to a bundled HAPI FHIR server

## Architecture

Containerized services orchestrated with Docker Compose to provide a complete pharmacogenomic analysis pipeline:

- **PostgreSQL** (schemas: `cpic`, `user_data`, `reports`, `fhir`) - Stores CPIC guidelines, user data, and generated reports
- **FastAPI application** (web UI, API, report generation, SSE progress) - Main application orchestrating the analysis workflow
- **GATK API service** (HTTP wrapper) - Handles preprocessing of BAM/CRAM files to VCF format
- **PyPGx service** - Provides comprehensive allele calling across multiple pharmacogenes, including difficult ones like CYP2D6
- **PharmCAT wrapper service** (Flask) - Executes PharmCAT pipeline with PyPGx and OptiType outside calls to unlock full 23-gene coverage
- **Reference genome downloader** - Manages reference genome data for accurate variant calling
- **HAPI FHIR server** - Enables export of formatted pharmacogenomic report data to Personal and Electronic Health Records
- **Kroki** - Renders workflow diagrams which serve as a visual depiction of the pipeline the report has been built from

**Workflow**: Genomic Data → GATK Preprocessing (if needed) → OptiType Allele Calling → PyPGx Allele Calling → PharmCAT Analysis with PyPGx and OptiType Outside Calls → Comprehensive Reporting → optional EHR export via FHIR

### Service Ports (Host → Container)

- App/UI: 8765 → 8000
- PostgreSQL: 5444 → 5432
- PharmCAT wrapper: 5001 → 5000
- GATK API: 5002 → 5000
- PyPGx: 5053 → 5000
- Genome downloader: 5050 → 5050
- HAPI FHIR: 8090 → 8080

### Data Directories (Mounted)

- Shared data: `./data` → `/data`
- Reference data: `./reference` → `/reference`
- Reports: `/data/reports/<file_id>/` (per‑job directory)

## Requirements

- Docker and Docker Compose
- Git
- First run requires significant internet bandwidth to fetch images and reference genomes, if they are not already cached
- Recommended Minimum: 8 GB RAM (≥16 GB recommended if using GATK), 30 GB disk (≫500 GB for WGS)

## Quick Start

1. **Clone the repo**
   ```bash
   git clone https://github.com/Zaroganos/ZaroPGx.git
   cd ZaroPGx
   ```

2. **Choose your environment configuration**
   
   For personal and home use, a local deployment is strongly recommended.
   
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

3. **Start services**
   
   **Using default .env:**
   ```bash
   docker compose up -d --build
   ```
   
   **Using specific environment file:**
   ```bash
   docker compose --env-file .env.local up -d --build
   docker compose --env-file .env.production up -d --build
   ```

4. **Access the application**
   - Web UI: `http://localhost:8765`
   - API docs: `http://localhost:8765/docs`
   - HAPI FHIR (optional): `http://localhost:8090`

**Environment Differences:**
- **Local Development**: Binds to localhost only, uses development subnet
- **Production/Web**: Binds to all interfaces (0.0.0.0), uses production subnet

## Usage

### Web UI

1. Open `http://localhost:8765`
2. Upload a VCF or BAM file
3. Observe progress; on completion you'll see links to PDF and interactive HTML reports

### REST API

**Upload a genomic file**
```bash
curl -X POST \
  -F "file=@test_data/sample_cpic.vcf" \
  -F "sample_identifier=patient123" \
  http://localhost:8765/upload/genomic-data
```

**Check processing status:**
```bash
curl http://localhost:8765/status/<file_id>
```

**Get report URLs** (PDF/HTML/interactive and optional PharmCAT artifacts):
```bash
curl http://localhost:8765/reports/<file_id>
```

**Generate a report** (utility endpoint; separate from the upload pipeline):
```bash
curl -X POST http://localhost:8765/reports/generate \
  -H "Content-Type: application/json" \
  -d '{"patient_id":"1","file_id":"1","report_type":"comprehensive"}'
```

### Notes

- Development mode disables authentication by default (`ZAROPGX_DEV_MODE=true`); tokens are not required.
- Reports are written to `/data/reports/<file_id>/` with filenames:
  - `<file_id>_pgx_report.pdf`
  - `<file_id>_pgx_report_interactive.html`
  - Optional PharmCAT originals: `<file_id>_pgx_pharmcat.{html,json,tsv}`

## Sample Data

Sample VCFs available in the repo:
- `app/static/demo/pharmcat.example.vcf`
- `test_data/sample_cpic.vcf`
- Additional examples under `test_data/`

## Project Structure

```
ZaroPGx/
├── app/                    # FastAPI app, templates, static assets
│   ├── api/                # API routers, DB helpers, models
│   ├── pharmcat/           # PharmCAT client integration
│   ├── reports/            # Report generation (PDF/HTML, FHIR export)
│   ├── services/           # Background job processing
│   ├── core/               # Core utilities and version management
│   └── visualizations/     # Workflow diagrams and visual tools
├── db/                     # Initialization and migrations
│   ├── init/               # Database initialization scripts
│   └── migrations/         # Schema migrations and CPIC data
│       └── cpic/           # CPIC schema and sample data
├── docker/                 # Service Dockerfiles and wrappers
│   ├── pharmcat/           # PharmCAT service with PyPGx integration
│   ├── pypgx/              # PyPGx allele calling service
│   ├── gatk-api/           # GATK preprocessing service
│   ├── genome-downloader/  # Reference genome management
│   └── postgresql/         # Database service configuration
├── data/                   # Runtime data (reports, uploads, temp files)
├── reference/              # Reference genomes and annotation files
└── docker-compose.yml      # Orchestration (environment-specific via .env files)
```

## Service Details

- **PostgreSQL** with initialization under `db/init` and `db/migrations` - Stores CPIC guidelines and user data
- **PharmCAT** (Java 17) with a Flask wrapper API - Core pharmacogenomic analysis engine
- **FastAPI app** (Python 3.12; dependencies in `pyproject.toml`/`uv.lock`) - Main application orchestrating the complete workflow
- **GATK** - Handles preprocessing of BAM/CRAM files to VCF format for downstream analysis
- **PyPGx** - Provides comprehensive allele calling across multiple pharmacogenes, enabling PharmCAT to achieve full 23-gene coverage through outside calls
- **HAPI FHIR** - Enables export of pharmacogenomic results to healthcare systems and personal health records

**Note**: The app currently stubs GATK/PyPGx steps in the background workflow; full integration is being implemented to achieve the intended comprehensive pharmacogene coverage.

## Report Handling

- Each run writes a per‑job directory: `/data/reports/<file_id>/`
- The app consistently generates its own reports (PDF + interactive HTML)
- When available, original PharmCAT reports are copied with normalized names (`<file_id>_pgx_pharmcat.*`)

## FHIR Export (Optional)

- HAPI FHIR server is bundled and exposed at `http://localhost:8090`
- Report export endpoint: `POST /reports/{report_id}/export-to-fhir`

## Dependency Management

- Python dependencies are managed via `pyproject.toml` (locked in `uv.lock`)
- Container‑specific dependencies are installed in each Dockerfile

## Troubleshooting

- **Service connectivity**: Confirm the `pgx-network` bridge exists and containers are healthy
- **File processing**: Ensure input VCF is valid and non‑empty
- **PDF generation**: WeasyPrint is used; if PDF fails, an HTML fallback may be written alongside

## Contributing

1. Create a feature branch: `git checkout -b feature/your-change`
2. Commit: `git commit -m "Describe your change"`
3. Push: `git push origin feature/your-change`
4. Open a Pull Request

## Acknowledgements & Citations

- **PharmCAT** (Pharmacogenomics Clinical Annotation Tool). See methods: Sangkuhl K, Whirl-Carrillo M, et al. Clinical Pharmacology & Therapeutics. 2020;107(1):203–210. Project: https://github.com/PharmGKB/PharmCAT
- **GATK** (Genome Analysis Toolkit). Core papers: McKenna A, et al. Genome Research. 2010;20(9):1297–1303; DePristo MA, et al. Nature Genetics. 2011;43(5):491–498. Guidance: https://gatk.broadinstitute.org/
- **PyPGx** for star‑allele calling. References: Lee S‑B, et al. PLOS ONE. 2022 (ClinPharmSeq); Lee S‑B, et al. Genetics in Medicine. 2018 (Stargazer); Lee S‑B, et al. Clinical Pharmacology & Therapeutics. 2019 (Stargazer, 28 genes). Docs: https://pypgx.readthedocs.io/en/latest/index.html
- **OptiType (nf-core/hlatyping)** for HLA allele calling. (to add citation)

## License

[AGPLv3](LICENSE)

Copyright (C) 2024-2025 Iliya Yaroshevskiy

This project is licensed under the AGPLv3 License.
