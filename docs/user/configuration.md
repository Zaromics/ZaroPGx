---
title: Configuration
---

# Configuration

Settings are environment variables in `.env`, which Compose reads for `compose.yml` and
passes to the app. After changing one, apply it with `docker compose up -d`. Change
`compose.yml` itself only through `compose.override.yml` (gitignored), which Compose merges
in.

A boolean is on for `1`, `true`, `yes` or `on`; blank or anything else is off. Exceptions:
`PDF_FALLBACK`, `DOWNLOAD_ON_STARTUP`, `REF_CACHE_ENABLED` and `ANNOTATION_ENABLED` are on only for `true`.

Where a template ships a different value from the code's default, both are given.

## Required

| Variable | |
|---|---|
| `DB_PASSWORD` | The database password. `start-docker.sh` generates one on first run; Compose will not start without it. Do not change it once the database volume exists |
| `SECRET_KEY` | Signs login tokens. `start-docker.sh` generates one; the app refuses to start with it blank or a known placeholder |

## Access

| Variable | Default | |
|---|---|---|
| `BIND_ADDRESS` | `127.0.0.1:8765` | The app's address and port. The default serves this machine only. `0.0.0.0:8765` (or a bare `8765`) listens on every interface, for using the app from other devices on your network; without `password` mode, anyone who can reach it gets in. Beyond your network, put it behind a TLS proxy ({doc}`deployment`) |
| `INTERNAL_BIND_ADDRESS` | `127.0.0.1` | The address every other service publishes on (database, PharmCAT, GATK, PyPGx, ZaroHLA, mtDNA, genome downloader, HAPI FHIR, Kroki, docs). None of them authenticates: keep it on loopback and use an SSH tunnel |
| `ZAROPGX_AUTH_MODE` | `open` | `open`: no login. `password`: every request needs the shared password (a session cookie from `/login`, or a Bearer token from `/token`). `audit`: no login, but logs what `password` would refuse |
| `ZAROPGX_AUTH_PASSWORD` | | The shared password for `password` mode. Anyone who has it can read every report on the instance |
| `ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES` | `HS256`, `30` | Login token signing and lifetime |
| `ZAROPGX_DEV_MODE` | `true` | Legacy; it no longer turns authentication on or off |

## Images and network

| Variable | Default | |
|---|---|---|
| `ZAROPGX_TAG` | the release | Tag of the `zaromicsresearch/zaropgx-*` images to pull. `latest` follows the newest |
| `HAPI_FHIR_TAG` | `v8.12.0-2` | The HAPI FHIR image. A new version migrates its database schema on first start, one way: back up first |
| `PHARMCAT_VERSION` | `3.4.0` | The PharmCAT release built into the PharmCAT image |
| `NETWORK_SUBNET` | `172.28.0.0/16` (`.env.local`: `172.20.0.0/16`) | The Compose network's subnet; change it if it collides with yours |
| `JAVA_OPTS` | `-Xmx4g` | PharmCAT's JVM options |
| `DB_USER`, `DB_NAME`, `DB_HOST`, `DB_PORT` | `zaropgx_user`, `zaropgx_db`, `db`, `5432` | The database's user, name, host and port, which the templates set. The app's database address is built from all four with no defaults, so keep them in `.env`. The user and name are fixed when the database volume is first created |

## Analysis

| Variable | Default | |
|---|---|---|
| `GATK_ENABLED`, `PYPGX_ENABLED`, `OPTITYPE_ENABLED`, `MTDNA_ENABLED` | `true` | The default state of the step switches on the upload page. The services run either way. With GATK off, inputs that need conversion (CRAM, SAM, BCF, gVCF, FASTQ) fail |
| `PHARMCAT_ABSENT_TO_REF`, `PHARMCAT_UNSPECIFIED_TO_REF` | `false` | The default state of PharmCAT's assume-reference options on the upload page; see {doc}`usage` |
| `REF_CACHE_ENABLED` | `true` | Build the CRAM reference cache (3.2 GB download, 5.3 GB kept). Without it, only CRAMs written against UCSC hg38 decode, and **Run Demo**, whose CRAM needs the cache, answers 503 |
| `DOWNLOAD_ON_STARTUP` | `true` | Let the genome downloader stage references when it starts. On an install that finished staging, it still fetches an item a later release added (with `false`, `POST /start-download` to its port 5050 does) |
| `DOWNLOAD_RETRY_DELAYS` | `30,120` | Seconds the genome downloader waits before trying a failed download again: one try more per number. Only failures that can pass are retried (a dropped connection, a timeout, a 5xx, 408 or 429, a body that fails its SHA-256); a 404 is not |
| `ANNOTATION_ENABLED` | `true` | Build the annotation the check for variants outside the allele definitions reads (about 1 GB fetched once, 3 MB kept, in `reference/annotation`): Ensembl's MANE transcripts, AlphaMissense, gnomAD v4.1 and ClinVar, cut to the pharmacogenes' coding exons. Its sources are pinned (ClinVar to a dated release) and refreshed with each ZaroPGx release, which rebuilds it. Without it, reports say the check did not run |
| `NEXTFLOW_MAX_WAIT_SECONDS` | `86400` | How long the app waits for a pipeline run before failing the job |
| `FASTQ_MAX_UPLOAD_BYTES` | `21474836480` (20 GB) | The largest FASTQ aligned, both mates together, in bytes; the app and the GATK service both read it. What limits it is disk and time: aligning needs about 9 times the FASTQ's size in free disk, which the GATK service checks before it starts, and a whole genome takes hours. Whether duplicate marking fits the GATK service's memory at whole-genome scale has not been tested |
| `PYPGX_MEMORY_LIMIT`, `PYPGX_MAX_PARALLEL_GENES`, `PYPGX_BATCH_SIZE` | `7G`, `8`, `4` | PyPGx's memory and parallelism |
| `MAX_MEMORY` | `20g` | The GATK service's Java heap for variant calling. For an input over 2 GB, 70% of the host's memory (as the container reads it) if that is less |
| `PYPGX_PHARMCAT_PREFERENCE` | `auto` | Which genes PyPGx genotypes. `pharmcat`: not those PharmCAT calls itself (it still calls CYP2D6, which PharmCAT takes from it). `pypgx`: all 87, and PharmCAT uses PyPGx's calls over its own. `auto`: the first for a VCF, BCF or gVCF; all 87 for reads (BAM, CRAM, SAM, FASTQ), so PyPGx measures copy number for PharmCAT's CYP2B6 and CYP4F2 too, but PharmCAT keeps its own calls |
| `OUTSIDECALLSOVERRIDE` | off | Use a hand-written outside-calls file instead of the generated one |
| `MAX_HEADER_READ_BYTES`, `MAX_HEADER_PARSE_TIMEOUT_SEC` | `1000000000`, `300` | Limits on reading an upload's header |

## Reports

| Variable | Default | |
|---|---|---|
| `INCLUDE_PHARMCAT_HTML`, `INCLUDE_PHARMCAT_JSON`, `INCLUDE_PHARMCAT_TSV` | `true`, `false`, `false` (templates: all `true`) | Offer PharmCAT's own reports with the results |
| `EXECSUM_USE_TSV` | `false` (templates: `true`) | Build the Executive Summary from PharmCAT's TSV rather than its JSON |
| `PDF_ENGINE` | `weasyprint` | `weasyprint` or `reportlab` |
| `PDF_FALLBACK` | `true` | Try the other engine if the first fails |
| `KROKI_ENABLED` | `true` | Draw the reports' workflow diagram with Kroki. Off, the reports list the run's steps as text instead; the Kroki containers run either way |
| `AUTHOR_NAME`, `SOURCE_URL`, `ZAROPGX_VERSION` | from `pyproject.toml`, the GitHub URL | Shown in the reports' footer and Platform table |
| `FHIR_EXPORT_ENABLED` | `true` | The `/fhir` endpoints and the FHIR bundles written with each report. A job's bundle is sent to `FHIR_SERVER_URL` only when asked (`POST /fhir/push/workflow/{job_id}`) |
| `HAPI_FHIR_ENABLED` | `true` | Whether this installation has a FHIR server to send bundles to. With `FHIR_EXPORT_ENABLED` on too, a finished job's results on the web page have a **Push to FHIR server** button ({doc}`reports`). The bundled HAPI server runs either way |
| `KEEP_UPLOADS` | `false` | Keep an upload after its job ends. By default it is deleted when the job completes, fails or is cancelled |

## Storage

| Variable | Default | |
|---|---|---|
| `ZAROPGX_SCRATCH` | `./data/temp` | Where ZaroHLA works. HLA typing a whole genome needs tens of GB; point this at a large disk if `data/` is small |
| `ZAROHLA_SAMTOOLS_THREADS` | `4` | Threads samtools uses as ZaroHLA extracts the HLA reads and converts them for OptiType. A genome with tens of millions of unplaced reads spends most of its HLA time there |
| `ZAROPGX_ALIGN_REFERENCE` | `./reference/pypgx` | Where the FASTQ alignment index is read from |
| `LOG_LEVEL` | `DEBUG` (`.env.production`: `INFO`) | The app's log level |

## Set by Compose

`compose.yml` sets the services' URLs (`PHARMCAT_API_URL`, `GATK_API_URL`, `PYPGX_API_URL`,
`FHIR_SERVER_URL`, `KROKI_URL`, `NEXTFLOW_RUNNER_URL`, `JOB_API_BASE`), `DATABASE_URL`, and
the services' paths. Values for these in `.env` are ignored; they matter only to code run
outside Compose. `ZAROHLA_API_URL` (`http://zarohla:5000`) comes from `.env`: it is where
the app checks ZaroHLA's health; the pipeline calls ZaroHLA at `http://zarohla:5000`
whatever it says. `GENOME_DOWNLOADER_API_URL` and `DATA_DIR` in the templates have no effect. Nor has
`GENOME_DOWNLOADER_ENABLED`, which only `/services-config` reports: the genome downloader
runs either way (`DOWNLOAD_ON_STARTUP` decides whether it downloads). `HAPI_FHIR_VERSION`
replaces the version the app reads from the FHIR server, but no report or page shows it.
PharmCAT always logs at `DEBUG` whatever `PHARMCAT_LOG_LEVEL` says.
