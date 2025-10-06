# ZaroPGx — Pharmacogenomic Analysis Platform

<img width="1352" height="2575" alt="zaropgx_demo" src="https://github.com/user-attachments/assets/50de2e8d-b496-424b-b2fb-0d34d7e39505" />

---

**ZaroPGx** is a containerized bioinformatic pipeline that processes genetic data and generates comprehensive pharmacogenomic reports guided by institutional resources. Nextflow as pipeline executor is used to orchestrate a finite-state algorithmic workflow which integrates GATK & samtools/bcftools preprocessing, allele calling with hlatyping (OptiType), mtDNA-server-2, and PyPGx, and report generation via PharmCAT phenotype matching with outside calls from the three aforementioned tools, to unlock its full potential across 23 core pharmacogenes, with additional coverage for approximately 64 additional pharmacogenes via PyPgx with associated guidelines of lesser confidence. Report data will be exportable to Personal/Electronic Health Records with the included HAPI FHIR server. Designed as a self-hostable docker compose stack, ZaroPGx enables absolute data privacy and security when run locally. For web facing deployments as well as local, environment configurations are provided which allow the software stack to be made safely accessible to others over the internet, though a reverse proxy and any other authentication, authorization tooling is not included, any typical modern solution should work. 

*Last revised 2025-10-06*
## Status
This project is in active development. Core functionality is being implemented incrementally.

## What ZaroPGx can do
The system is designed to:

1. **Accept genomic data files** (VCF, BAM, SAM, CRAM, FASTQ), preprocessing them when necessary
2. **Perform allele calling** via PyPGx, hlatyping (OptiType), and mtDNA-server-2 for comprehensive pharmacogene coverage
3. **Execute PharmCAT analysis** with PyPGx, hlatyping, and mtDNA-server-2 providing outside calls to unlock PharmCAT's full potential across 23 core pharmacogenes
4. **Generate comprehensive reports** covering actionable results for 23 pharmacogenes and notable findings for approximately 90 genes in total
5. **Export data** to Electronic/Personal Health Record via the integrated HAPI FHIR service in a FHIR compliant report
6. **Maintain privacy** with self-hosted deployment, ensuring no sensitive PHI data leaves the local network

## Current Implementation Status

- **VCF Processing**: Fully implemented for GRCh38, support for GRCh37 coming soon in v0.3 with bcftools liftover
- **FASTQ/BAM/SAM/CRAM inputs**: Scaffolded but not fully implemented; needs testing
- **mtDNA-server-2 Integration**: Scaffolding to start shortly (TO DO)
- **OptiType Integration**: Scaffolded Nextflow hlatyping OptiType pipeline, needs sysbox work to enable docker-in-docker (TO DO)
- **PyPGx Integration**: Core PyPGx service is integrated; optimization in progress to differentiate pipeline paths
- **PharmCAT with Outside Calls**: Core PharmCAT service is integrated; PyPGx, OptiType, and mtDNA-server-2 outside calls dictionary curation is in progress
- **Comprehensive Reporting**: Custom PDF and interactive HTML reports generation is available, being rapidly iterated
- **FHIR Export**: HAPI FHIR server integrated, export / query response functionality in development (basic XML export coming in 0.3)

## Features (Current State)

- Process VCF files directly
- Allele calling via OptiType, PyPGx, and PharmCAT
- Generate reports: PDF and interactive HTML; optionally include original PharmCAT HTML/JSON/TSV outputs
- REST API and web UI for uploads and status; authentication is disabled by default in development mode
- Optional export of reports to a bundled HAPI FHIR server (coming in 0.3)

## Architecture
Containerized services orchestrated with Docker Compose with a core Nextflow-coordinated pipeline:

- **Main FastAPI App** (Web UI, API, report generation, websocket progress tracking) - Main application orchestrating the analysis workflow
  - Service Ports (Host → Container) 8765 → 8000
- **Nextflow service** - Handles execution of the pipeline
- **Genome Reference downloader** - Manages reference genome data for accurate variant calling
  - Service Ports (Host → Container) 5050 → 5050
- **PostgreSQL DB** (via psycopg v3 & managed with Alembic) - Stores guidelines, sample data, and generated reports
  - Service Ports (Host → Container) 5444 → 5432
- **GATK service** (FastAPI wrapper) - Handles various conversion and preprocessing operations
  - Service Ports (Host → Container) 5002 → 5000
- **nf-core/hlatyping** (nextflow OptiType container) - Performs HLA Calling with either FASTQ or BAM inputs
- **PyPGx service** - (FastAPI wrapper) - Provides comprehensive allele calling across multiple pharmacogenes, including difficult ones like CYP2D6
  - Service Ports (Host → Container) 5053 → 5000
- **PharmCAT service** (FastAPI wrapper) - Executes PharmCAT pipeline with PyPGx and OptiType outside calls to unlock full 23-gene coverage
  - Service Ports (Host → Container) 5001 → 5000
- **Kroki** - Renders workflow diagrams which serve as a visual depiction of the pipeline the report has been built from
- **HAPI FHIR server** - Enables export of formatted pharmacogenomic report data to Personal and Electronic Health Records
  - Service Ports (Host → Container) 8090 → 8080


**Workflow**: Genomic data sample submission → Preprocessing (if needed) → OptiType HLA Allele Calling → PyPGx Star Allele Calling → PharmCAT Matching with PyPGx and OptiType Outside Calls → Report Creation → optional EHR export via FHIR

### Data Directories (Mounted)

- Shared data: `./data` → `/data`
- Reference data: `./reference` → `/reference`
- Reports: `/data/reports/<file_id>/` (per‑job directory)

## Requirements

- Docker and Docker Compose
  - if on Windows WSL, Docker Desktop is recommended
- Git

- Internet connection: first run requires significant bandwidth to fetch images, build containers, and load reference genomes
- Hardware, Bare Minimum (limited functions): 4 CPU cores, 8 GB DDR3 RAM, 50 GB storage space
- Hardware, Acceptable (all pipeline functions): 8+ CPU cores, 64+ GB DDR4 RAM, 1+ TB NVMe SSD storage space 
- Hardware, Preferred (all pipeline functions with quickness): 16 CPU cores, 128 GB ECC DDR4+ RAM, 2+ TB NVMe SSD storage space; with configured parallelism

## Quick Start

- At this time, reference pre-built docker images are not distributed.
- You must clone the repository and build the compose stack locally.

1. **Clone the repo**
   ```bash
   git clone https://github.com/Zaroganos/ZaroPGx.git
   cd ZaroPGx
   ```

2. **Choose your environment configuration**
   
   For personal and home (LAN) use, a local deployment is recommended.
   
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
   
   **Option A: Using the simple startup script (recommended for CLI hesitant users)**
   
   Choose the startup script that matches your environment:
   - Ensure the shell script can be executed, if it does not appear to work.
   
   - **PowerShell (Windows):** (If you are on Windows but cannot or may not use WSL)
     ```powershell
     .\start-docker.ps1
     ```
   
   - **Bash/Linux/Mac/Windows with WSL:**
     ```bash
     ./start-docker.sh
     ```
   
   **Option B: Manual Docker Compose commands**
   
   **Using default .env:**
   ```bash
   docker compose up -d --build
   ```
   
   **Using specific environment file:** (Advanced, for multiple configurations)
   ```bash
   docker compose --env-file .env.local up -d --build
   docker compose --env-file .env.production up -d --build
   ```

4. **Access the application**
   - Web UI: `http://localhost:8765`
   - Documentation: `http://localhost:8765/docs`
   - HAPI FHIR dashboard (optional): `http://localhost:8090`

**Environment Differences:**
- **Local Development**: Binds to localhost only, uses development subnet
- **Production/Web**: Binds to all interfaces (0.0.0.0), uses production subnet (Bring your own proxy!)

## Usage

### Web UI (Recommended)

1. Open `http://localhost:8765`
2. Upload a sample VCF file
3. Observe progress; on completion you'll see links to PDF and interactive HTML reports

### REST API (Advanced and Debugging)

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

**Get report URLs** (PDF/HTML interactive/PharmCAT original reports):
```bash
curl http://localhost:8765/reports/<file_id>
```

**Generate a report** (API-only utility endpoint):
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
For real-world sample data, try browsing the **Personal Genome Project**:
- https://my.pgp-hms.org/public_genetic_data

Filtered sample VCFs available in the repo:
- `app/static/demo/pharmcat.example.vcf`
- `test_data/sample_cpic.vcf`

## Project Structure (Abridged)

```
ZaroPGx/
├── app/                    # FastAPI core App, templates, static assets, etc.
│   ├── api/                  # API routers, DB helpers, models
│   ├── pharmcat/             # PharmCAT client integration
│   ├── reports/              # Report generation (PDF/HTML, FHIR export)
│   ├── services/             # Background job processing
│   ├── core/                 # Core utils and version management
│   └── visualizations/       # Workflow diagrams and visual tools using Kroki
├── db/                     # Postgres DB initialization and migrations
│   ├── init/                 # Database initialization scripts
│   └── migrations/           # Schema migrations
├── docker/                 # Service Dockerfiles and service wrappers
│   ├── pharmcat/             # PharmCAT service with FastAPI
│   ├── pypgx/                # PyPGx service with FastAPI
│   ├── gatk-api/             # GATK service FastAPI
│   ├── genome-downloader/    # Reference genome fetcher (typically needs to only run once)
│   └── postgresql/           # Database service configuration
├── data/                   # Runtime data (reports, uploads, temp files)
├── reference/              # Reference genomes and annotation files
└── docker-compose.yml      # Container orchestration, configured via inline flags or .env file(s)
```

## Service Details (Abridged)

- **Core FastAPI App** (Python 3.12; dependencies in `pyproject.toml`/`uv.lock`) - Main application orchestrating the complete workflow and report creation
- **PostgreSQL** DB with initialization under `db/init` and `db/migrations` - Stores guideline data and user data for persistent and offline analysis
- **GATK** - Handles various preprocessing and conversions of input files for downstream analysis
- **PharmCAT** (Java 17) with a FastAPI wrapper, the central pharmacogenomic analysis engine
- **PyPGx** - Provides comprehensive allele calling across multiple pharmacogenes, enabling PharmCAT to achieve full 23-gene coverage through outside calls, plus reports calls for 87 total pharmacogenes of varying evidence level
- **HAPI FHIR** - Enables export of pharmacogenomic results to healthcare systems and personal health records (coming in v0.3)

## Report Handling

- Each run writes a per‑job directory: `/data/reports/<file_id>/`
- The app consistently generates its own reports (PDF + interactive HTML)
- When available, original PharmCAT reports are copied with normalized names (`<file_id>_pgx_pharmcat.*`)

## FHIR Export (Optional) (Coming in v0.3)

- HAPI FHIR server is bundled and exposed at `http://localhost:8090`
- Report export endpoint: `POST /reports/{report_id}/export-to-fhir`

## Dependency Management

- Python dependencies are managed via `pyproject.toml` (locked in `uv.lock`)
- Container‑specific dependencies are installed in each Dockerfile

## Troubleshooting

- **Service connectivity**: Confirm the `pgx-network` bridge exists and containers are healthy
- **File processing**: Ensure input VCF is valid and contains required information
- **PDF generation**: WeasyPrint is used; if PDF creation fails, ReportLab fallback may be used instead.

## Contributing

1. Create a feature branch: `git checkout -b feature/your-change`
2. Commit: `git commit -m "Describe your change"`
3. Push: `git push origin feature/your-change`
4. Open a Pull Request

## Acknowledgements & Citations

- **GATK** (Genome Analysis Toolkit).
  - McKenna A, et al. Genome Research. 2010;20(9):1297–1303; DePristo MA, et al. *Nature Genetics.* 2011;43(5):491–498.  Docs: https://gatk.broadinstitute.org/
- **hlatyping** (hlatyping from nextflow-core, with OptiType base)
  -  Sven F., Christopher Mohr, Alexander Peltzer, nf-core bot, Vikesh Ajith, Mark Polster, Gisela Gabernet, Jonas Scheid, VIJAY, Phil Ewels, Maxime U Garcia, Tobias Koch, Paolo Di Tommaso, & Kevin Menden. (2025). nf-core/hlatyping: 2.1.0 - Chewbacca (2.1.0). *Zenodo.* https://doi.org/10.5281/zenodo.15212533  Docs: https://nf-co.re/hlatyping/
- **PharmCAT** (Pharmacogenomics Clinical Annotation Tool).
  - Sangkuhl K, Whirl-Carrillo M, et al. *Clinical Pharmacology & Therapeutics.* 2020;107(1):203–210.  Docs: https://pharmcat.clinpgx.org/
- **PyPGx**
  - Lee S‑B, et al. *PLOS ONE.* 2022 (ClinPharmSeq); Lee S‑B, et al. *Genetics in Medicine.* 2018 (Stargazer); Lee S‑B, et al. *Clinical Pharmacology & Therapeutics.* 2019 (Stargazer, 28 genes).  Docs: https://pypgx.readthedocs.io/en/latest/index.html
- **mtDNA-server-2** 
  - Weissensteiner H, Forer L, Kronenberg F, Schönherr S. mtDNA-Server 2: advancing mitochondrial DNA analysis through highly parallelized data processing and interactive analytics. *Nucleic Acids Res*. 2024 May 6:gkae296. doi: 10.1093/nar/gkae296. Epub ahead of print. PMID: 38709886.


## License

[AGPLv3](LICENSE)

Copyright (C) 2024-2025 Iliya Yaroshevskiy

This project is licensed under the AGPLv3 License.
