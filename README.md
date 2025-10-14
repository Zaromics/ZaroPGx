# ZaroPGx — Pharmacogenomic Analysis Platform

<img width="1352" height="2575" alt="zaropgx_demo" src="https://github.com/user-attachments/assets/50de2e8d-b496-424b-b2fb-0d34d7e39505" />

---
## What ZaroPGx does
**ZaroPGx** is a containerized bioinformatic pipeline that **processes genetic data** and generates comprehensive pharmacogenetic reports guided by institutional resources. Nextflow as pipeline executor is used to orchestrate a finite-state algorithmic workflow which integrates GATK & samtools/bcftools preprocessing; **allele calling** with hlatyping (OptiType), mtDNA-server-2, PyPGx, and optionally PharmCAT; and report generation via PharmCAT **phenotype matching** with outside calls from the three aforementioned tools, to unlock its full panel of 23 core pharmacogenes, with additional coverage for approximately 64 additional pharmacogenes via PyPgx. **Reports generated** include custom PDF (printing friendly!) and interactive HTML formats, as well as the PharmCAT original HTML report, with raw data outputs available. Report data will soon be exportable to Personal and Electronic Health Records with the included HAPI FHIR server. Designed as a self-hostable Docker Compose stack, ZaroPGx enables absolute **data privacy and security** when run locally in a secure network. Web-facing (production/public), as well as local (private), deployments are straightforward to configure with provided environment configuration templates, allowing the software stack to be safely accessible to others over the internet. A reverse proxy or any other authentication and/or authorization tooling is not included, but can be easily added or integrated according to your unique needs. 

*Last revised 2025-10-13*
## Status
**This project is in active development.**
- NGS-derived GRCh38 VCF sample inputs can be processed without difficulty and produce substantial report content.

**Remaining core functionality is being implemented incrementally:**

*Input formats*
- [X] Priority 0 (*Supported*): **VCF, GRCh38**/hg38, NGS-derived.
- [...] Priority 1 (*Development*): **VCF, GRCh37**/hg19, NGS-derived. *Projected release in v0.3 with bcftools liftover.*
- [...] Priority 2 (*Development*): **BAM, CRAM, SAM, FASTQ, BCF**, all NGS-derived. *Scaffolded, needs testing. Projected release in v0.4, with BAM support first and foremost.*
- [...] Priority 3 (*Research*): Other sequencing and genotyping formats.
- [...] Priority 4 (*Research*): BED, detailed gVCF, 23andMe, AncestryDNA, various TXT formats.
- [...] Priority 5 (*Early research*): T2T format, and all others.

*Features*
- [X] Priority 0 (*Supported*): **PDF and interactive HTML custom in-house reports.**
- [...] Priority 1 (*Development*): **FHIR offline export** as JSON, XML; Custom PharmCAT definitions for outside calls. *Projected release in v0.3*
- [...] Priority 2 (*Development*): Nextflow-based containers (**hlatyping & mtDNA-server-2**) sysbox implementation and wiring-in. *Projected release in v0.4 at the latest, depending on sysbox complexity.*
- [...] Priority 3 (*Development*): Interactive HTML enhancements with useful visualizations, fully DB-oriented data handling. *Projected release in v0.4-0.5*
- [...] Priority 4 (*Research*): FHIR online export direct to PHR/EHR
- [...] Priority 5 (*Early research*): In-depth and targeted analytics, with specialty curation to reduce cognitive load
- [...] Priority 6 (*Early research*): Complete transition to fully DB-based workflow; pulling and normalizing data directly from published databases

## Architecture
Containerized services are orchestrated with Docker Compose with a core Nextflow-executed pipeline:

- **Main App** - (FastAPI) - Main App providing Web UI, API, workflow progress tracking, report generation.
  - *Main application orchestrating the analysis workflow*
  - Service Ports (Host → Container) 8765 → 8000
  - Python 3.12; dependencies in `pyproject.toml`/`uv.lock`
- **Nextflow executor service**
  - Manages execution of the core pipeline
- **Genome Reference downloader**
  - Fetches reference materials including genomes
  - Service Ports (Host → Container) 5050 → 5050
- **PostgreSQL 17 DB** - (SQLalchemy2, psycopg 3 & schema managed with Alembic)
  - Stores guideline and sample data and workflow metadata, and generated reports, allowing for persistent and offline analysis (if so desired)
  - Initialization under `db/init` and `db/migrations` 
  - Service Ports (Host → Container) 5444 → 5432
- **GATK service** - (FastAPI wrapper)
  - Handles various conversion, haplotyping, and preprocessing operations as needed
  - Service Ports (Host → Container) 5002 → 5000
- **nf-core/hlatyping** - (nextflow OptiType container)
  - Performs HLA Calling with either FASTQ or BAM inputs
- **PyPGx service** - (FastAPI wrapper)
  - Performs allele calling for 87 total pharmacogenes.
  - Provides comprehensive allele calling (including Structural Variants and Copy Number Variants) for applicable genes such as CYP2D6 when possible; with BAM input.
  - Service Ports (Host → Container) 5053 → 5000
- **PharmCAT service** - (FastAPI wrapper, Java 17)
  - Executes PharmCAT pipeline with PyPGx, OptiType, and mtDNA-server-2 outside calls to unlock full 23-gene panel coverage
  - Service Ports (Host → Container) 5001 → 5000
- **Kroki** & **Kroki Mermaid**
  - Renders workflow diagrams to serve as a visual depiction of the pipeline the report has been built from
- **HAPI FHIR server**
  - Enables export of formatted pharmacogenomic report data to Personal and Electronic Health Records (coming in v0.3)
  - Service Ports (Host → Container) 8090 → 8080

**Workflow**: *Genomic data sample submission → Preprocessing (if needed) → OptiType HLA Allele Calling → mtDNA-server-2 Mitochondrial DNA Allele Calling → PyPGx Allele Calling → PharmCAT phenotype matching with Outside Calls → Report Creation → optional PHR/EHR export via FHIR*

### Data Directories (Mounted)

- Shared data: `./data` → `/data`
- Reference data: `./reference` → `/reference`
- Reports: `/data/reports/<file_id>/` (per‑job directory)

## Requirements
<u>Software</u>

**Linux** environment preferred
- *Docker*; *Docker Compose*; *Git* -- at minimum

**Windows 10/11** requires *WSL2* installed and configured
- *WSL2*; *Docker*; *Docker Compose*; *Git*
- If your needs require HLA and MT typing: (at this time) your device will require *Sysbox*
  - IF using *Sysbox* for free (it's open source), *Docker Desktop* may NOT be used. If you have *Docker Desktop* already installed, it should still work <u>as long as you launch ZaroPGx via shell from the WSL2 virtual drive</u>; if it doesn't work, you may have to uninstall or disable Docker Desktop until you are finished using ZaroPGx.
    - *Sysbox*
  - IF using Docker Desktop PAID/PREMIUM, *Sysbox* can be enabled directly in Docker Desktop via the <u>"Enhanced Container Isolation"</u> option. Proceed as usual.
    - *Docker Desktop* with *Sysbox* runtime enabled (ECI)

**macOS** requires either running a Linux VM (e.g. Crossover, etc.) OR using the paid/premium *Docker Desktop* with included *Sysbox*

<u>Hardware</u> (projected)

- *Internet connection*: <u>first run only</u> requires significant bandwidth to fetch images, build containers, and load reference genomes and db content; advisable to NOT be on a metered connection, and preferably use a wired one.
- *Hardware, Minimum* (limited functions): **4 CPU cores, 8 GB DDR3 RAM, 50 GB storage space**
- *Hardware, Acceptable* (all functions): **8+ CPU cores, 32-64+ GB DDR4 RAM, 1+ TB NVMe SSD storage space**
- *Hardware, Preferred* (all functions, with swiftness): **16 CPU cores, 128 GB ECC DDR4+ RAM, 2+ TB NVMe SSD storage space**; with configured parallelism and various service parameter tuning

## Quick Start

- At this time, reference pre-built docker images are not distributed. As the program approaches v1.0 release, container images will begin to be distributed through Dockerhub.
- For now, you must clone this repository and build the docker compose stack locally. This should not require any special action on your part, but it will take some time, possibly as long as an hour if your hardware is closer to "minimum" than "preferred" spec.

1. **Clone the repo**
   ```bash
   git clone https://github.com/Zaroganos/ZaroPGx.git
   cd ZaroPGx
   ```

2. **Choose your environment and docker compose configurations**
   
   For personal and home (LAN) use, a local deployment is recommended.
   
   **Local Development (default):** Your typical template for personal / home use
   ```bash
   cp .env.local .env
   # edit .env as needed (at minimum set SECRET_KEY)
   ```
   
   **Production/Web Deployment:** For hosting an externally-accessible instance on the web
   ```bash
   cp .env.production .env
   # edit .env as needed (set all Keys to a secure string)
   ```
   
   **Custom Configuration:** More complete and in-line documented, for convenience
   ```bash
   cp .env.example .env
   # edit .env as needed
   ```

   **Choose your Docker Compose configuration** Start with example template
   ```bash
   mv docker-compose.yml.example docker-compose.yml
   # edit docker-compose.yml as needed to customize service settings
   ```

3. **Start services**
   
   **Option A: Using the simple startup script (recommended if you are new to, or have never used, docker / docker compose.)**
   
   Choose the startup script that matches your environment:
   - If the below command failed, ensure the shell script can be executed, if it does not appear to work. Typically you can check the file's permissions by right clicking it. PowerShell might require a set execution policy override.
   
   - **PowerShell (Windows WSL):** If you are on Windows but cannot, may not, or choose not to use WSL2's virtual drive
     ```powershell
     .\start-docker.ps1
     ```
   
   - **Bash (Linux / Mac/ Windows WSL):**
     ```bash
     ./start-docker.sh
     ```
   
   **Option B: Manual Docker Compose commands** If you are familiar with docker compose
   
   **Once you have configured your .env and compose yml:**
   ```bash
   docker compose up -d --build && docker compose logs -f
   ```
   
   **Using specific environment file:** (Advanced, for multiple configurations)
   ```bash
   docker compose --env-file .env.local up -d --build && docker compose logs -f
   docker compose --env-file .env.production up -d --build && docker compose logs -f
   ```

4. **Access the Main App**
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
3. Observe progress; on completion you'll see links to the custom PDF and interactive HTML reports, as well as PharmCAT's report and raw data outputs.

### REST API (Advanced and Debugging)

**See the FastAPI docs on the reference instance's page: https://pgx.zimerguz.net/api-reference**

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
├── db/                     # Postgres DB initialization and migrations
├── docker/                 # Service Dockerfiles and service wrappers
│   ├── gatk-api/             # GATK service FastAPI
│   ├── genome-downloader/    # Reference genome fetcher (typically needs to only run once)
│   ├── nextflow/             # Nextflow executor wrapper
│   ├── pharmcat/             # PharmCAT service with FastAPI
│   └── pypgx/                # PyPGx service with FastAPI
├── docs/                   # Sphinx docs with readthedocs theme (hosted internally, allowing for offline access)
├── pipelines/              # Nextflow config
├── reference/              # Reference genomes and annotation files
└── docker-compose.yml      # Docker Compose orchestration instructions, configured via inline flags and with .env file
```

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

- **Check the logs**: Keep an eye on the logs and set logging level to DEBUG
```bash
docker compose logs -f
```

- **Service connectivity**: Confirm the `pgx-network` bridge exists and containers are healthy
- **File processing**: Ensure input file(s) is/are valid and contain required information
- **PDF generation**: WeasyPrint is used; if PDF creation fails, ReportLab fallback may be used instead. Check if all containers are running and healthy

## Data Cleanup

### Complete and Selective Data Removal

To completely remove all user data and reset ZaroPGx to a clean state:

**Stop services and remove all data:**
```bash
# Stop all services
docker compose down

# Remove all containers, networks, and volumes (including database data)
docker compose down -v

# Remove all runtime data directories
rm -rf data/
rm -rf reference/
```

**Remove database data only:**
```bash
docker volume rm pgx_pgdata pgx_fhir-data pgx_pharmcat-references
```

**Remove select data dirs:**
```bash
- rm -rf data/reports/    # Generated pharmacogenomic reports (PDF, HTML, JSON, etc.)
- rm -rf data/uploads/    # Uploaded genomic files (VCF, BAM, etc.)
- rm -rf data/temp/       # Temporary processing files
- rm -rf data/fhir-data/  # FHIR server data and patient records
- rm -rf data/nextflow/   # Nextflow cache and workflow execution data
- rm -rf reference/       # Reference genomes (GRCh37, GRCh38, hg19, hg38) - **Large files**
```

## Contributing (is gratefully appreciated and welcome!)

1. Create a feature branch: `git checkout -b feature/your-change`
2. Commit: `git commit -m "Describe your change"`
3. Push: `git push origin feature/your-change`
4. Open a Pull Request

## Acknowledgements & Citations

- **GATK** (Genome Analysis Toolkit, Broad Institute).
  - McKenna A, et al. Genome Research. 2010;20(9):1297–1303; DePristo MA, et al. *Nature Genetics.* 2011;43(5):491–498.  Docs: https://gatk.broadinstitute.org/
- **hlatyping** (hlatyping, with OptiType base, available in nf-core)
  -  Sven F., Christopher Mohr, Alexander Peltzer, nf-core bot, Vikesh Ajith, Mark Polster, Gisela Gabernet, Jonas Scheid, VIJAY, Phil Ewels, Maxime U Garcia, Tobias Koch, Paolo Di Tommaso, & Kevin Menden. (2025). nf-core/hlatyping: 2.1.0 - Chewbacca (2.1.0). *Zenodo.* https://doi.org/10.5281/zenodo.15212533  Docs: https://nf-co.re/hlatyping/
- **PharmCAT** (Pharmacogenomics Clinical Annotation Tool, Pharmacogenomics Knowledge Base).
  - Sangkuhl K, Whirl-Carrillo M, et al. *Clinical Pharmacology & Therapeutics.* 2020;107(1):203–210.  Docs: https://pharmcat.clinpgx.org/
- **PyPGx** (by Seung-been "Steven" Lee)
  - Lee S‑B, et al. *PLOS ONE.* 2022 (ClinPharmSeq); Lee S‑B, et al. *Genetics in Medicine.* 2018 (Stargazer); Lee S‑B, et al. *Clinical Pharmacology & Therapeutics.* 2019 (Stargazer, 28 genes).  Docs: https://pypgx.readthedocs.io/en/latest/index.html
- **mtDNA-server-2** (Institute of Genetic Epidemiology, Medical University of Innsbruck)
  - Weissensteiner H, Forer L, Kronenberg F, Schönherr S. mtDNA-Server 2: advancing mitochondrial DNA analysis through highly parallelized data processing and interactive analytics. *Nucleic Acids Res*. 2024 May 6:gkae296. doi: 10.1093/nar/gkae296. Epub ahead of print. PMID: 38709886.

This project was originally inspired by **NeuroPGx**, available here: https://github.com/Andreater/NeuroPGx
- Zampatti, S.; Fabrizio, C.; Ragazzo, M.; Campoli, G.; Caputo, V.; Strafella, C.; Pellicano, C.; Cascella, R.; Spalletta, G.; Petrosini, L.; et al. Precision Medicine into Clinical Practice: A Web-Based Tool Enables Real-Time Pharmacogenetic Assessment of Tailored Treatments in Psychiatric Disorders. *J. Pers. Med.* 2021, 11, 851. https://doi.org/10.3390/jpm11090851

## License

[AGPLv3](LICENSE)

Copyright (C) 2024-2025 Iliya Yaroshevskiy

This project is licensed under the AGPLv3 License.
