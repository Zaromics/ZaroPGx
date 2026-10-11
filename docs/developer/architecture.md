---
title: Architecture
curation: not
---

# Architecture

ZaroPGx is a Docker Compose stack. A FastAPI app takes uploads, tracks jobs and writes
the reports; a Nextflow pipeline runs the analysis by calling one container per tool over
HTTP; each tool reports its progress back to the app.

## Services

Every service except `app` publishes to `${INTERNAL_BIND_ADDRESS:-127.0.0.1}`, the Docker
host's loopback. None of them authenticates; reach them through an SSH tunnel rather than
widening that address. `app` publishes `${BIND_ADDRESS:-127.0.0.1:8765}`; the templates
set the same, and a bare port there would listen on every interface.

| Service | Image | Host port → container | Role |
|---|---|---|---|
| `app` | `zaromicsresearch/zaropgx-app` | `${BIND_ADDRESS:-127.0.0.1:8765}` → 8000 | Web UI, API, job tracking, report generation, the docs at `/docs` |
| `db` | `postgres:18.6` | 5444 → 5432 | Jobs, steps and logs; loaded PharmCAT results; HAPI FHIR's schema |
| `nextflow` | `zaromicsresearch/zaropgx-nextflow` | not published (5055) | Runs `pipelines/pgx/main.nf` for each job |
| `genome-downloader` | `zaromicsresearch/zaropgx-genome-downloader` | 5050 → 5050 | Stages reference genomes, chain files, PharmCAT's position list and the CRAM reference cache into `./reference` |
| `gatk-api` | `zaromicsresearch/zaropgx-gatk-api` | 5002 → 5000 | GATK 4.7.0.0, samtools and bcftools: CRAM/SAM to BAM, FASTQ alignment, BCF to VCF, gVCF genotyping, liftover |
| `pypgx` | `zaromicsresearch/zaropgx-pypgx` | 5053 → 5000 | PyPGx 0.27.0: genotypes alignments at PharmCAT's positions, star-allele calls for 87 genes |
| `zarohla` | `zaromicsresearch/zaropgx-zarohla` | 5060 → 5000 | OptiType 1.5.0: HLA class I typing from alignments |
| `mtdna` | `zaromicsresearch/zaropgx-mtdna` | 5062 → 5000 | mtDNA-Server 2 v2.1.16: mitochondrial variants, haplogroup, the MT-RNR1 outside call |
| `pharmcat` | `zaromicsresearch/zaropgx-pharmcat` | 5001 → 5000 | PharmCAT 3.4.0 with the outside calls; writes its HTML, JSON and TSV |
| `fhir-server` | `hapiproject/hapi:v8.12.0-2` | 8090 → 8080 | HAPI FHIR server; the app only reads its version |
| `kroki`, `mermaid` | `yuzutech/kroki:0.32.1`, `kroki-mermaid` | 8001 → 8000; mermaid not published | Render the workflow diagram in the reports |
| `docs` | built locally, profile `optional` | 5070 → 8000 | Live-reloading docs preview |

The ZaroPGx images are published for `linux/amd64` only and tagged `${ZAROPGX_TAG}`.
Each `build:` section in `compose.yml` builds the same image from source.

## Storage

| Host path | Mounted at | Holds |
|---|---|---|
| `./data` | `/data` (app, nextflow, sidecars) | uploads, per-job reports, scratch (`data/temp`), version manifests (`data/versions`), progress logs |
| `./reference` | `/reference` | reference genomes and indexes, chain files, the CRAM reference cache |
| `./` | `/app` (app), `/workspace` (nextflow) | the working tree: the app and the pipeline run the checked-out code |
| volume `pgdata` | `/var/lib/postgresql` (db) | the database |
| volume `pharmcat-references` | `/pharmcat-references` (pharmcat) | PharmCAT's GRCh38 reference, downloaded on first start |

Reports go to `/data/reports/<patient_id>/<job_id>/`. `zarohla` and `mtdna` run as uid 999,
so `start-docker.sh` makes `data/temp` and `data/versions` world-writable (sticky) for them.

## How a job runs

1. **Upload.** `POST /upload/genomic-data` saves the first data file (and a FASTQ's second
   mate) under `/data/uploads`. `app/api/utils/file_processor.py` reads the header, detects
   the format and reference build, and decides the workflow: a set of `needs_*` flags. An
   input it cannot analyse is refused here, before any job exists.
2. **Job.** The app creates a row in `jobs` and one in `job_steps` per step that will run.
   The steps come from `app/services/workflow_registry.py`, chosen by the flags.
3. **Pipeline.** The app posts the job to `nextflow` (`POST /run`). Each process in
   `pipelines/pgx/main.nf` calls one sidecar with `curl`, and the sidecar reports its step's
   status to the app through `app/utils/job_client.py`
   (`PUT /api/v1/jobs/<job_id>/steps/<step_name>`).
4. **Wait.** The app polls `nextflow` (`GET /status/<job_key>`) until the pipeline ends,
   fails or is cancelled.
5. **Report.** The app loads PharmCAT's JSON into the `pharmcat` schema (the run id is the
   job id) and generates the reports (`report_generation`).

Steps by input type (`[...]` runs only when needed or enabled):

| Input | Steps |
|---|---|
| VCF | `header_analysis` → `[liftover]` (GRCh37/hg19) → `pypgx_analysis` and `[mtdna_analysis]` → `pharmcat_analysis` → `report_generation` |
| BCF | `bcf_to_vcf`, then as VCF |
| gVCF | `gvcf_to_vcf` (GenotypeGVCFs at PharmCAT's positions), then as VCF |
| BAM | `[hla_typing]` → `pypgx_bam2vcf` → `pypgx_analysis`, with `[mtdna_analysis]` alongside → `pharmcat_analysis` |
| CRAM, SAM | `gatk_cram_sam_to_bam`, then as BAM |
| FASTQ | `gatk_alignment` (bwa-mem through GATK), then as BAM |

`mtdna_analysis` runs alongside the other steps and must finish before PharmCAT, which
consumes its MT-RNR1 call. For
alignments, gVCFs and FASTQ, PharmCAT's assume-reference flags are always off: those inputs
are genotyped at every PharmCAT position.

Progress is reported in stages (`app/services/workflow_stages.py`): upload, analysis, GATK,
HLA, PyPGx, mtDNA, PharmCAT, report. `app/services/workflow_progress_calculator.py` weights
each step by how long it takes on a 30x genome, so the bar follows the work done.

Cancelling (`POST /api/v1/jobs/<job_id>/cancel`) stops the pipeline and tells each sidecar
to stop. When a job ends, `app/services/cleanup_service.py` deletes its upload
(`KEEP_UPLOADS=true` keeps it) and the sidecars' per-job working directories.

## Database

Fresh databases are created from `db/init/00_complete_database_schema.sql`. At startup,
`app/api/db_migrations.py` applies any migration in `db/init/migrations/` not yet recorded in
the `schema_migrations` table, so an existing volume reaches the current schema without a
reset.

| Schema | Tables in use |
|---|---|
| `public` | `jobs`, `job_steps`, `job_logs`, `genomic_file_headers`, `schema_migrations` |
| `user_data` | `patients`, `genetic_data` |
| `pharmcat` | `results`, `gene_summary`, `diplotypes`, `drug_gene_map`, `messages`, `variants`, `drug_recommendations`, `recommendation_conditions`, `unannotated_gene_calls`, and three views |
| `fhir` | owned by HAPI FHIR |

The `cpic` and `reports` schemas and `user_data.patient_alleles` are created but nothing
reads or writes them.

## Reports

`app/reports/generator.py` renders Jinja templates from `app/reports/templates/`:

- `<job_id>_pgx_report.pdf`, through WeasyPrint. `app/reports/pdf_fit.py` lays the PDF out,
  measures the sections that must fit their pages (the Executive Summary is one page) and
  tightens their spacing and type a step at a time until they do.
- `<job_id>_pgx_report_interactive.html`, written last so its Export tab can list the other
  files. It inlines `app/static/js/pgx-report.js` and the vendored Chart.js and D3
  (`app/static/vendor/`), so it needs neither the app nor a network to open.
- The workflow diagram (SVG and PNG), rendered by Kroki.

PharmCAT writes its own `<job_id>_pgx_pharmcat.{html,json,tsv}` into the same directory;
the mtDNA step adds `mtdna_result.json`, `mtdna_variants.vcf.gz` and, for alignments,
`mtdna_report.html`. The app serves them under `/reports/<patient_id>/<job_id>/`.

## Code layout

```text
app/
  main.py            FastAPI app, routers, startup (migrations, docs build)
  api/               routers (upload, jobs, workflows, pharmcat, FHIR, reports),
                     db.py (SQLAlchemy models), models.py (Pydantic), middleware/ (auth gate),
                     utils/ (file_processor, header_inspector)
  services/          job_service, workflow_registry, workflow_stages,
                     workflow_progress_calculator, cleanup_service, FHIR export
  pharmcat/          PharmCAT client and the parser that loads its JSON
  reports/           report generator, PDF fitting, Jinja templates
  mtdna/             mtDNA report context
  utils/             job_client (sidecar callbacks), assume-reference rules, provenance
  visualizations/    workflow diagrams
  templates/         the upload page (index.html)
  static/            JavaScript, demo files
docker/              one directory per sidecar: Dockerfile and its FastAPI wrapper
pipelines/pgx/       main.nf and nextflow.config
config/genes.json    the gene list and gene sets
db/init/             schema and migrations
scripts/             e2e harness, backups, the FASTQ alignment index build
tests/               pytest suite; tests/e2e/ runs against the full stack
```
