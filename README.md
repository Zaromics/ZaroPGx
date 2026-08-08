# ZaroPGx — A Pharmacogenomic Analysis Platform
**See the analysis in action: click below to watch a demo on YouTube:**
[<img src="https://img.youtube.com/vi/FzKI48IQb3I/hqdefault.jpg" width="600" height="400"
/>](https://www.youtube.com/embed/FzKI48IQb3I)

---
## What ZaroPGx Does
**ZaroPGx** is a containerized bioinformatic pipeline that **processes genetic data** and generates comprehensive pharmacogenetic reports guided by institutional resources. Nextflow as pipeline executor is used to orchestrate a finite-state algorithmic workflow which integrates GATK & samtools/bcftools preprocessing; **allele calling** with ZaroHLA (OptiType), mtDNA-server-2, PyPGx, and optionally PharmCAT; and report generation via PharmCAT **phenotype matching** with outside calls from up to all three aforementioned tools, unlocking its full panel of 23 core pharmacogenes, with coverage for a further 68 pharmacogenes via PyPGx — 91 in total (`config/genes.json` is the authoritative list). **Reports generated** include custom Zaromics reports in printer-friendly PDF, and interactive HTML formats, including as well the native PharmCAT HTML report, with raw data outputs available too. Report data will soon be seamlessly exportable to Personal / Electronic Health Records via the bundled HAPI FHIR server. Designed as a self-hostable Docker Compose stack, ZaroPGx enables absolute **data privacy and security** when loaded in a local and secure network. Web-facing (public) as well as local (private) deployments are straightforward to configure with the provided environment configuration templates, allowing the software stack to be securely served to users over the web. A bundled reverse proxy or authentication / authorization tool is not yet included, but you can easily add or integrate within or alongside the compose stack according to your specific needs. 

### 🚀 Quickstart -- One-Command Setup
**Quick and super simple setup script**
**(Full Getting Started section is below)**

**PowerShell (Windows):**
```powershell
iwr -useb https://raw.githubusercontent.com/Zaromics/ZaroPGx/main/bootstrap.ps1 | iex
```

**Bash (Linux / macOS / WSL):**
```bash
curl -fsSL https://raw.githubusercontent.com/Zaromics/ZaroPGx/main/bootstrap.sh | bash
```

<img width="600" height="1100" alt="zaropgx_demo" src="https://github.com/user-attachments/assets/50de2e8d-b496-424b-b2fb-0d34d7e39505" />

## Status
**This project is in early development.**
- NGS-derived GRCh38 VCF sample inputs can be processed without difficulty and readily produce substantial report content.

**Core functionality is being implemented incrementally:**

*Input formats*
- [X] Priority 0 (*Supported*): **VCF, GRCh38**/hg38, NGS-derived.
- [...] Priority 1 (*Development*): **VCF, GRCh37**/hg19, NGS-derived. *Projected release in v0.3 with bcftools liftover.*
- [...] Priority 2 (*Development*): **BAM, CRAM, SAM, FASTQ, BCF**, all NGS-derived. *Scaffolded, needs testing. Projected release in v0.4, with BAM support first and foremost.*
- [...] Priority 3 (*Research*): Other sequencing and genotyping formats.
- [...] Priority 4 (*Research*): BED, detailed gVCF, 23andMe, AncestryDNA, various TXT formats.
- [...] Priority 5 (*Early research*): T2T format, and others.

*Features*
- [X] Priority 0 (*Supported*): **PDF and interactive HTML custom in-house reports.**
- [o] Priority 1 (*Development*): **FHIR offline export** as JSON, XML; Custom PharmCAT definitions for outside calls. *Projected release in v0.3*
- [...] Priority 2 (*Development*): Wiring-in mtDNA-server-2 container. *Projected release in v0.4.*
- [...] Priority 3 (*Development*): Interactive HTML enhancements with useful visualizations, fully DB-oriented data handling. *Projected release in v0.4-0.5*
- [...] Priority 4 (*Research*): FHIR online export direct to PHR/EHR
- [...] Priority 5 (*Early research*): In-depth and targeted analytics, with specialty curation to reduce cognitive load
- [...] Priority 6 (*Early research*): Complete transition to fully DB-based workflow; pulling and normalizing data directly from published databases

## Architecture
Containerized services are orchestrated with Docker Compose with a core Nextflow-executed pipeline:

- **ZaroPGx App** - (FastAPI) - Main App providing Web UI, API, workflow progress tracking, report generation
  - *Main application orchestrating the analysis workflow*
  - Service Ports (Host → Container) 8765 → 8000
  - Python 3.12; dependencies in `pyproject.toml`/`uv.lock`
- **Nextflow executor**
  - Manages execution of the core pipeline
- **Genome Reference downloader**
  - Fetches reference materials including genome assemblies
  - Service Ports (Host → Container) 5050 → 5050
- **PostgreSQL 18 DB** - (SQLAlchemy 2, Psycopg 3; fresh schemas initialized from `db/init/00_complete_database_schema.sql`)
  - Stores data of guidelines, sample runs, workflow metadata, and generated reports, allowing for persistent and local analysis
  - Initialization SQL under `db/init`
  - Service Ports (Host → Container) 5444 → 5432
- **GATK service** - (FastAPI wrapped)
  - Handles various conversion, haplotyping, and preprocessing operations
  - Service Ports (Host → Container) 5002 → 5000
- **ZaroHLA** - (Custom FastAPI wrapped OptiType implementation)
  - Performs HLA Calling with either FASTQ or BAM inputs
  - (Needs Testing)
- **PyPGx service** - (FastAPI wrapped)
  - Performs allele calling for up to 87 total pharmacogenes
  - Provides comprehensive allele calling (including Structural Variants and Copy Number Variants) for such genes as CYP2D6 when possible with BAM input.
  - Service Ports (Host → Container) 5053 → 5000
- **PharmCAT service** - (FastAPI wrapped, Java 17)
  - Executes PharmCAT pipeline with PyPGx, OptiType, and mtDNA-server-2 outside calls to unlock its full 23-gene panel coverage
  - Service Ports (Host → Container) 5001 → 5000
- **Kroki** & **Kroki Mermaid**
  - Renders workflow diagrams to draw a visual depiction of the pipeline the report has been built from
- **HAPI FHIR server**
  - Enables export of formatted pharmacogenomic report data to Personal and Electronic Health Records (projected v0.3)
  - Service Ports (Host → Container) 8090 → 8080

**Workflow**: *Genomic data sample submission → Preprocessing (if needed) → OptiType HLA Allele Calling → mtDNA-server-2 Mitochondrial DNA Allele Calling → PyPGx Allele Calling → PharmCAT phenotype matching with Outside Calls → Report Creation → optional PHR/EHR export via FHIR*

### Data Directories (Mounted)

- Shared data: `./data` → `/data`
- Reference data: `./reference` → `/reference`
- Reports: `/data/reports/<patient_id>/<job_id>/` (nested per‑job directory)

## Requirements
<u>Software</u>

**NB: The simple bootstrap script will automatically install missing dependencies for you!**

**Linux** environment preferred
- *Docker*; *Docker Compose* (>= 2.24); *Git* -- at minimum
- Auto-install supported via: apt, yum, dnf, pacman

**Windows 10/11** requires *WSL2* installed and configured
- *WSL2*; *Docker*; *Docker Compose*; *Git*
- Auto-install supported via: winget or chocolatey

**macOS** requires either *Docker Desktop* or to run a Linux VM (e.g. Crossover)
- Auto-install supported via: homebrew (Git only; Docker Desktop must be installed manually)
- (macOS support needs testing)

<u>Hardware</u>

- *Virtualization*: Hardware Virtualization must be enabled for Windows and macOS users

(resource usage, projected)

- *Internet connection*: <u>first run only</u> requires significant bandwidth to fetch images, build containers, and load reference genomes and db content; advisable to NOT be on a metered connection, and preferably use a wired one.
- *Hardware, Minimum* (limited function): **4 CPU cores, 8 GB DDR3 RAM, 50 GB storage**
- *Hardware, Acceptable* (full function): **8 CPU cores, 32-64 GB DDR4 RAM, 1 TB NVMe SSD storage**
- *Hardware, Preferred* (fast, full function): **16 CPU cores, 128 GB ECC DDR4+ RAM, 2 TB NVMe SSD storage**

## Get Started

- Pre-built images **are** published to Docker Hub under `zaromicsresearch/zaropgx-*`, and
  `compose.yml` pulls them by default. Pin the tag with `ZAROPGX_TAG` in your `.env` (default
  `0.2.8`, or `latest` to track the newest). Pre-1.0 tags are development builds — pin one
  rather than tracking `latest` if you want reproducibility.
- You still clone this repository: it carries `compose.yml`, the `.env` templates, the database
  initialization SQL and the Nextflow pipeline. What you no longer have to do is build.
- Building locally remains fully supported and is the fallback for any change to the source:
  add `--build` to `docker compose up`. Budget real time for it — possibly an hour or more if
  your hardware is closer to "minimum" than "preferred" spec. Even when pulling, the first start
  takes ~8-15 minutes while PharmCAT downloads its GRCh38 reference data.

### One-Command Setup

**This is the simplest way to get started: launch your shell and run the command below**

**PowerShell (Windows):**
```powershell
iwr -useb https://raw.githubusercontent.com/Zaromics/ZaroPGx/main/bootstrap.ps1 | iex
```

**Bash (Linux/macOS/WSL):**
```bash
curl -fsSL https://raw.githubusercontent.com/Zaromics/ZaroPGx/main/bootstrap.sh | bash
```

This single command will:
- Check for required dependencies (Git, Docker, Docker Compose)
- Offer to automatically install missing dependencies (with your permission)
- Download the ZaroPGx bootstrap script
- Clone the repository
- Create necessary directories
- Start the Docker compose containers

**Note:** If Git, Docker, or Docker Compose are not installed, the script will:
1. Detect your package manager (winget, chocolatey, apt, yum, dnf, brew, pacman)
2. Ask if you want to install missing dependencies automatically
3. Request elevated privileges if required
4. Install the dependencies and guide you through next steps

If automatic installation is not available or you prefer manual installation, the script will provide direct links to installation pages.

**Security Note:** If you're cautious about running remote scripts (which is good practice), you can inspect the bootstrap scripts here:
- PowerShell: https://raw.githubusercontent.com/Zaromics/ZaroPGx/main/bootstrap.ps1
- Bash: https://raw.githubusercontent.com/Zaromics/ZaroPGx/main/bootstrap.sh

**To update an existing installation:**

**PowerShell:**
```powershell
iex "& { $(iwr -useb https://raw.githubusercontent.com/Zaromics/ZaroPGx/main/bootstrap.ps1) } -Update"
```

**Bash:**
```bash
curl -fsSL https://raw.githubusercontent.com/Zaromics/ZaroPGx/main/bootstrap.sh | bash -s -- --update
```

---

### Manual Installation (Advanced)

If you prefer more control or want to customize the installation:

1. **Clone the repository**
   ```bash
   git clone https://github.com/Zaromics/ZaroPGx.git
   cd ZaroPGx
   ```

2. **Choose your environment and docker compose configuration**
   
   For personal and home (LAN) use, a local deployment is recommended
   
   All three templates are tracked and carry the same set of keys; they differ only in values.
   `SECRET_KEY` and `DB_PASSWORD` ship **blank on purpose** — `start-docker.sh` /
   `start-docker.ps1` generate a per-install value on first run. Leave them blank unless you are
   managing secrets yourself, and never rotate `DB_PASSWORD` once the `zaropgx_pgdata` volume
   exists unless you also `ALTER ROLE`.

   **Local Development (default):** Your typical template for personal / home use
   ```bash
   cp .env.local .env
   # BIND_ADDRESS=8765, subnet 172.20.0.0/16, LOG_LEVEL=DEBUG
   ```
   
   **Web Deployment:** For hosting an externally accessible service on the web
   ```bash
   cp .env.production .env
   # BIND_ADDRESS=0.0.0.0:8765, subnet 172.28.0.0/16, LOG_LEVEL=INFO
   ```
   Note this template does **not** turn authentication on: it ships `ZAROPGX_AUTH_MODE=open`,
   and `ZAROPGX_DEV_MODE=false` does not enable auth either. Set
   `ZAROPGX_AUTH_MODE=password` plus `ZAROPGX_AUTH_PASSWORD` yourself, and front it with a
   TLS-terminating reverse proxy.
   
   **Custom Configuration:** More complete and in-line documented, for convenience
   ```bash
   cp .env.example .env
   # edit .env as needed
   ```

   Every internal service (`db`, `pharmcat`, `gatk-api`, `pypgx`, `zarohla`,
   `genome-downloader`, `fhir-server`, `kroki`, `docs`) publishes to
   `${INTERNAL_BIND_ADDRESS:-127.0.0.1}` — loopback on the Docker host. None of them
   authenticate, so reach them through an SSH tunnel rather than widening that value.

   **Docker Compose configuration** `compose.yml` is tracked in the repository, so you
   already have it and it updates with `git pull`. Do not edit it directly — put your
   customizations in `compose.override.yml`, which Compose merges automatically:
   ```bash
   # optional: only if you need to change service settings
   cat > compose.override.yml <<'YAML'
   services:
     app:
       ports:
         - "9000:8000"
   YAML
   ```

3. **Start services**
   
   **Option A: Simple startup script** Recommended for those unfamiliar with docker / docker compose
   
   Choose the startup script that matches your environment:
   - If the below command failed or did not appear to work, please ensure the shell script can be executed. Typically you can check the file's permissions by right clicking it. PowerShell might require you to set an 'execution policy override'.
   
   - **PowerShell (Windows):** If you are on Windows but cannot, may not, or choose not to use WSL2's virtual drive
     ```powershell
     .\start-docker.ps1
     ```
   
   - **Bash (Linux / Mac/ Windows WSL):**
     ```bash
     ./start-docker.sh
     ```
   
   **Option B: Docker Compose commands** Recommended for advanced users
   
   **Once you have configured your .env and compose yml:**
   ```bash
   docker compose pull                              # pull pre-built images (Docker Hub: zaromicsresearch/zaropgx-*)
   docker compose up -d && docker compose logs -f   # start — runs the published images, no local build
   ```
   The stack runs the **published images by default** (no build wait — only PharmCAT's
   one-time ~8-min reference-genome download on first start). To build locally instead —
   e.g. you changed the source — add `--build` (the `build:` sections are the fallback):
   ```bash
   docker compose up -d --build
   ```
   Set `ZAROPGX_TAG` in `.env` to pin a version (default `0.2.8`) or `latest`.
   
   **Using specific environment file:** (Advanced, for multiple configurations)
   ```bash
   docker compose --env-file .env.local pull && docker compose --env-file .env.local up -d
   docker compose --env-file .env.production pull && docker compose --env-file .env.production up -d
   ```

4. **Access the Main App**
   - Web UI: `http://localhost:8765`
   - Interactive API docs (FastAPI/Swagger): `http://localhost:8765/docs`
   - Project documentation (built Sphinx site, offline): `http://localhost:8765/documentation`
   - HAPI FHIR dashboard (loopback-bound): `http://localhost:8090`

**Environment Differences:**
- **Local Development**: Binds to localhost only, uses development subnet
- **Production/Web**: Binds to all interfaces (0.0.0.0), uses production subnet (Bring your own proxy!)

## Usage

### Web Interface (Recommended)

1. Open `http://localhost:8765`
2. Upload a sample VCF file
3. Observe progress, and on completion you will see links to the custom PDF and interactive HTML reports, as well as PharmCAT's report and raw data outputs.

### REST API (Advanced and Debugging)

**See the FastAPI docs on the reference instance's page: https://pgx.zaromics.com/api-reference**

**Upload a genomic file**
```bash
curl -X POST \
  -F "file=@test_data/sample_cpic.vcf" \
  -F "sample_identifier=patient123" \
  http://localhost:8765/upload/genomic-data
```

**Check processing status** (by `data_id` from the upload response; same UUID as `genetic_data.data_id`):
```bash
curl http://localhost:8765/status/<data_id>
```

**Get report URLs** (PDF/HTML interactive/PharmCAT original reports; path uses `job_id`):
```bash
curl http://localhost:8765/reports/<job_id>
```

**Generate a report:** use `POST /upload/genomic-data` (the standalone
`POST /reports/generate` stub returns **501**).

### Notes

- Development mode disables authentication by default (`ZAROPGX_DEV_MODE=true`); tokens are not required.
- Upload/status JSON uses `data_id` (not `file_id`). Cancel payloads use `job_id` only.
- Recipe catalog `GET /api/v1/workflows` is unchanged (recipes, not job instances).
- Reports are written to `/data/reports/<patient_id>/<job_id>/` with filenames:
  - `<job_id>_pgx_report.pdf`
  - `<job_id>_pgx_report_interactive.html`
  - Optional PharmCAT originals: `<job_id>_pgx_pharmcat.{html,json,tsv}`
  - Display `report_id` in templates equals `job_id`

## Testing

**Fast suite** (no Docker):

```bash
uv run pytest -q -m "not e2e"
```

On Windows, prefer the project venv if `uv run pytest` fails building pysam:
`.venv\Scripts\python.exe -m pytest -q -m "not e2e"`.

**Full-stack e2e** (Docker required; isolated project `zaropgx_e2e` on port `18765`):

```bash
./scripts/e2e.sh
```

Use Git Bash or WSL on Windows. Needs substantial Docker RAM (16 GB+ recommended).
First run may download PharmCAT reference data and build images; subsequent runs reuse
BuildKit cache. CI runs the same harness on every PR (separate required job). On
failure the stack stays up and logs land in `e2e-logs/`.

## Sample Data Access
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
│   ├── core/                 # Core utils and version management
│   ├── pharmcat/             # PharmCAT client integration
│   ├── reports/              # Report generation (PDF/HTML, FHIR export)
│   ├── services/             # Background job processing
│   ├── templates/            # UI templates
│   ├── utils/                # Utilities
│   └── visualizations/       # Workflow diagrams and visual tools using Kroki
├── data/                   # Runtime data (reports, uploads, temp files)
├── db/                     # PostgreSQL initialization SQL and manual upgrade guidance
├── docker/                 # Service Dockerfiles and service wrappers
│   ├── gatk-api/             # GATK service FastAPI
│   ├── genome-downloader/    # Reference genome fetcher (typically needs to only run once)
│   ├── nextflow/             # Nextflow executor wrapper
│   ├── pharmcat/             # PharmCAT service with FastAPI
│   └── pypgx/                # PyPGx service with FastAPI
├── docs/                   # Sphinx docs with readthedocs theme (hosted internally, allowing for offline access)
├── pipelines/              # Nextflow config
├── reference/              # Reference genomes and annotation files
├── tests/                  # pytest suite (fast unit/API tests; tests/e2e/ needs Docker)
├── pyproject.toml          # Python dependencies (locked in uv.lock)
└── compose.yml             # Docker Compose orchestration; configured via inline flags and the .env file
```

## Report Handling

- Each run writes a nested per‑job directory: `/data/reports/<patient_id>/<job_id>/`
- The app consistently generates its own reports (PDF + interactive HTML)
- When available, original PharmCAT reports are copied with normalized names (`<job_id>_pgx_pharmcat.*`)
- Served under `/reports/<patient_id>/<job_id>/…`

## FHIR Export (Optional)

- HAPI FHIR server is bundled and exposed at `http://localhost:8090`
- Bundle generation / preview / save: `/fhir/*` (`fhir_export_router`)
- `POST /reports/{report_id}/export-to-fhir` returns **501** (retired; previously posted fabricated genotypes)

## Dependency Management

- Python dependencies are managed via `pyproject.toml` (locked in `uv.lock`)
- Container‑specific dependencies are installed in each Dockerfile

## Troubleshooting

- **Check the logs**: Keep an eye on the logs and set logging level to DEBUG
```bash
docker compose logs -f
```

- **Service connectivity**: Confirm the `pgx-network` bridge exists and containers are healthy
- **File processing**: Ensure input file(s) is/are valid and contain required information
- **PDF generation**: WeasyPrint is used; if PDF creation fails, ReportLab fallback may be used instead. Check if all containers are running and healthy

## Data Cleanup

### Data Removal- Complete and Selective modes 

To completely remove all user data and reset ZaroPGx to a clean state:

**Stop services and remove all data:**
```bash
# Stop all services
docker compose down

# Remove all containers, networks, and volumes (including database data) - warning! - Irretrievable
docker compose down -v

# Remove all runtime data directories
rm -rf data/
rm -rf reference/
```

**Remove database data only:**
```bash
docker volume rm zaropgx_pgdata zaropgx_fhir-data zaropgx_pharmcat-references
```

## Contributions are welcome and are gratefully appreciated!

## Acknowledgements & Citations
**This section is incomplete as the constituent software components are being assembled and all relevant research and clinical publications are being compiled.
If your work is a part of ZaroPGx and you wish to add or amend text recognizing yourself, your work, and/or your organization, please send me a message and I will gladly attend to your request.**

- **GATK** (Genome Analysis Toolkit, Broad Institute)
  - McKenna A, et al. Genome Research. 2010;20(9):1297–1303; DePristo MA, et al. *Nature Genetics.* 2011;43(5):491–498.  Docs: https://gatk.broadinstitute.org/
- **ZaroHLA** (based on OptiType, by Schubert B, Kohlbacher O, et al.)
  - Schubert B, et al. OptiType: precision HLA typing from next-generation sequencing data. *Bioinformatics.* 2014;30(23):3310-3316. doi: 10.1093/bioinformatics/btu548. Docs: https://github.com/FRED-2/OptiType
- **PharmCAT** (Pharmacogenomics Clinical Annotation Tool, Pharmacogenomics Knowledge Base, managed at Stanford University & University of Pennsylvania)
  - Sangkuhl K, Whirl-Carrillo M, et al. *Clinical Pharmacology & Therapeutics.* 2020;107(1):203–210.  Docs: https://pharmcat.clinpgx.org/
- **PyPGx** (by Dr. Seung-been "Steven" Lee)
  - Lee S‑B, et al. *PLOS ONE.* 2022 (ClinPharmSeq); Lee S‑B, et al. *Genetics in Medicine.* 2018 (Stargazer); Lee S‑B, et al. *Clinical Pharmacology & Therapeutics.* 2019 (Stargazer, 28 genes).  Docs: https://pypgx.readthedocs.io/en/latest/index.html
- **mtDNA-server-2** (Institute of Genetic Epidemiology, Medical University of Innsbruck)
  - Weissensteiner H, Forer L, Kronenberg F, Schönherr S. mtDNA-Server 2: advancing mitochondrial DNA analysis through highly parallelized data processing and interactive analytics. *Nucleic Acids Res*. 2024 May 6:gkae296. doi: 10.1093/nar/gkae296. Epub ahead of print. PMID: 38709886.

This project was originally inspired by software such as **NeuroPGx**, available here: https://github.com/Andreater/NeuroPGx
- Zampatti, S.; Fabrizio, C.; Ragazzo, M.; Campoli, G.; Caputo, V.; Strafella, C.; Pellicano, C.; Cascella, R.; Spalletta, G.; Petrosini, L.; et al. Precision Medicine into Clinical Practice: A Web-Based Tool Enables Real-Time Pharmacogenetic Assessment of Tailored Treatments in Psychiatric Disorders. *J. Pers. Med.* 2021, 11, 851. https://doi.org/10.3390/jpm11090851

## License

[AGPLv3](LICENSE)

Copyright (C) 2024-2026 Iliya Yaroshevskiy

This project is provided under the AGPLv3 License.
