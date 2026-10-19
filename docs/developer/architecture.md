---
title: Architecture
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
| `./docker/fhir-server/packages` | `/fhir-packages` (fhir-server, read-only) | the HL7 Genomics Reporting IG and its dependencies, which HAPI installs (below) |

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
- The run's workflow diagram (`app/visualizations/job_workflow.py`): the job's own steps
  from `job_steps`, wired as `main.nf` runs them, drawn by Kroki as one PNG that every
  report shows (sized to its PDF page by `workflow_pdf_style`; the SVG is kept as a file).
  Without Kroki, the same steps as a list; with no steps recorded, no diagram.

PharmCAT writes its own `<job_id>_pgx_pharmcat.{html,json,tsv}` into the same directory;
the mtDNA step adds `mtdna_result.json`, `mtdna_variants.vcf.gz` and, for alignments,
`mtdna_report.html`. The app serves them under `/reports/<patient_id>/<job_id>/`.

## The FHIR server's implementation guide

`fhir-server` installs the HL7 Genomics Reporting IG 3.0.0, whose profiles the FHIR export
follows, so that its `$validate` checks a bundle against them. The IG and the two packages it
depends on are committed in `docker/fhir-server/packages/`, unmodified, and `compose.yml`
mounts them read-only at `/fhir-packages`. Nothing is downloaded: each package is named by a
`file:` URL, `fetchDependencies` is off, and the dependencies are listed at the versions the
IG's `package.json` names. FHIR R4 itself is built into HAPI. A file missing on the start
that installs it stops HAPI starting, and its log names the file (`HAPI-2031: Error loading
"file:///fhir-packages/..."`).

| File | Package | sha256 |
|---|---|---|
| `hl7.fhir.uv.genomics-reporting-3.0.0.tgz` | Genomics Reporting IG 3.0.0 (STU3), 0.6 MB | `7a42cdfae8d47f0d39a2ad5c46ec9028ede4848655600e7deaff440b44d4d221` |
| `hl7.terminology.r4-6.1.0.tgz` | HL7 Terminology (THO) 6.1.0, 5.5 MB | `37d28e130903296267953f1e46f1acb5ed8ce2a485f1da53e7454d2df1b0585a` |
| `hl7.fhir.uv.extensions.r4-5.1.0.tgz` | FHIR Extensions Pack 5.1.0, 3.2 MB | `f96eec24d8ad2c043cf295acdfab46b01e0e1fcc568740aab3fd1acee4cb0456` |

Each was downloaded in October 2026 from `https://packages.fhir.org/<name>/<version>`;
packages2.fhir.org served the same bytes, and their SHA-1s match the registry's. HL7
publishes them under CC0 1.0. `SHA256SUMS` beside them holds the same hashes, for
`sha256sum -c`.

The IG is installed as resources (`STORE_AND_INSTALL`): its 42 profiles and extensions, 19
value sets and 12 code systems can be read and searched, as in
`GET /fhir/StructureDefinition?url=http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/genotype`.
THO and the extensions pack are only stored (`STORE_ONLY`): they stay in HAPI's package
cache, where the validator looks them up, rather than becoming thousands of resources. A
package installs on the first start whose database does not have it. That start takes 18 to
25 seconds longer and adds about 46 MB to the `fhir` schema; later starts skip it
(`reloadExisting=false`, the files are not read again) and take a second or two longer than
without the IG. HAPI's background pre-expansion of value sets is off
(`hapi.fhir.enable_task_pre_expand_value_sets=false`): it needs a search index (Hibernate
Search) this server does not keep, failed on every value set of the IG and logged some 85
errors on the first start. Validation expands the value sets it needs by itself.

### Validating a bundle

Nothing in the app calls `$validate`; it is there to check an export by hand. From the host:

```bash
curl -s -X POST -H 'Content-Type: application/fhir+json' \
  --data-binary @data/reports/<patient_id>/<job_id>/<job_id>_pgx_fhir_report.json \
  'http://127.0.0.1:8090/fhir/Bundle/$validate' > outcome.json
jq '[.issue[] | select(.severity == "error" or .severity == "fatal")] | length' outcome.json
```

The answer is an OperationOutcome, and the bundle is not stored. Errors and fatal issues
are where the bundle breaks a profile; those from the IG's name it (`...genotype|3.0.0`).
With no terminology server, codes are checked only against what the packages hold: the IG's
own codes and value sets and HL7's code systems. Whether a LOINC, SNOMED CT or HGNC code
exists is not checked, so expect warnings such as "Unable to expand ValueSet because
CodeSystem has CodeSystem.content=not-present but contents were not found:
http://loinc.org", and HGNC codes reported as outside the IG's HGNC value set. A terminology
server (HAPI's `hapi.fhir.remote_terminology_service`, tx.fhir.org for one) would check
them, but every bundle validated would send its codes, a patient's genes and phenotypes,
off the machine.

### Updating the packages

The IG's releases are years apart. To move to a new one:

1. Find it at https://packages.fhir.org/hl7.fhir.uv.genomics-reporting (`dist-tags`).
2. Download it into `docker/fhir-server/packages/` and read what it depends on:

   ```bash
   curl -fL -o hl7.fhir.uv.genomics-reporting-<version>.tgz \
     https://packages.fhir.org/hl7.fhir.uv.genomics-reporting/<version>
   tar -xzOf hl7.fhir.uv.genomics-reporting-<version>.tgz package/package.json
   ```

   Download each package under `dependencies` the same way, at the version given, and
   their own dependencies in turn; `hl7.fhir.r4.core` is built into HAPI.
3. Delete the old files, write the hashes (`sha256sum *.tgz > SHA256SUMS`) and update the
   table above.
4. Set the names, versions and file names in `compose.yml`'s
   `hapi.fhir.implementationguides.*`, and the IG version in `tests/test_fhir_server_ig.py`.
   That test checks that the files, `SHA256SUMS`, `compose.yml` and this table agree, that
   every dependency is installed at the version asked for, and that the IG still defines
   every profile the export names (`app/services/fhir/terminology.py`).
5. Restart `fhir-server`. It installs the new versions beside the old ones; check that
   `$validate`'s issues name the new version (`|<version>`).

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
  visualizations/    the per-job workflow diagram, the Kroki client
  templates/         the upload page (index.html)
  static/            JavaScript, demo files
docker/              one directory per sidecar: Dockerfile and its FastAPI wrapper
pipelines/pgx/       main.nf and nextflow.config
config/genes.json    the gene list and gene sets
db/init/             schema and migrations
scripts/             e2e harness, backups, the FASTQ alignment index build
tests/               pytest suite; tests/e2e/ runs against the full stack
```
