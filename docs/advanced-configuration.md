---
title: Advanced Configuration
curation: partial
---

# Advanced Configuration

This document lists environment variables and configuration flags used in the ZaroPGx codebase
and containers. `.env.example` is the annotated template and carries the same set; when the two
disagree, `.env.example` and the source are right and this page is stale.

*Last revised 2026-08-08 against ZaroPGx 0.2.8.*

### General
- Defaults listed above reflect current code paths; docker compose may set different values. When both exist, the container environment overrides code defaults.
- Boolean flags accept any of: `1`, `true`, `yes`, `on` (case-insensitive).
- **JAVA_OPTS**: General JVM options in various Java using containers (PharmCAT, HAPI, Nextflow images).

### Main App
*Feature toggles*
- **GENOME_DOWNLOADER_ENABLED**: Enable genome downloader integration. Default: `true`.
- **GATK_ENABLED**: Enable GATK checks/integration. Default: `true`.
- **OPTITYPE_ENABLED**: Enable OptiType integration. Default: `true`.
- **PYPGX_ENABLED**: Enable PyPGx checks/integration. Default: `true`.
- **KROKI_ENABLED**: Enable Kroki diagram rendering. Default: `true`.
- **HAPI_FHIR_ENABLED**: Enable HAPI FHIR integration checks. Default: `true`.
- **FHIR_EXPORT_ENABLED**: Gates the entire `/fhir` router (bundle generation, preview, save).
  Default: `true` — compose passes `${FHIR_EXPORT_ENABLED:-true}` to the app. When `false`,
  every `/fhir/*` endpoint returns an error explaining the flag.

*Application configs*
- **LOG_LEVEL**: Logging level for the app. Default: `DEBUG`.
- **SECRET_KEY**: Secret key for auth/token signing. Required; start-docker generates a per-install value. Empty or known placeholders refuse to start.
- **ZAROPGX_DEV_MODE**: Legacy flag. `false` does **not** enable auth (logs a warning). Prefer `ZAROPGX_AUTH_MODE`. Default: `true`.
- **ZAROPGX_AUTH_MODE**: Front-door gate mode: `open` (default, no-op), `audit` (log would-deny), or `password` (require cookie/Bearer).
- **ZAROPGX_AUTH_PASSWORD**: Shared install password when mode is `password`. Anyone who knows it can reach every patient report on that instance.
- **ALGORITHM**: JWT algorithm. Used as constant `HS256` in code.
- **ACCESS_TOKEN_EXPIRE_MINUTES**: Token expiry minutes. Used as constant `30` in code.
- **AUTHOR_NAME**: Override author shown in reports. If unset, read from `pyproject.toml` or fallback to `Zaromics Initiative`.
- **SOURCE_URL**: Project source URL in UI and reports. Default: `https://github.com/Zaromics/ZaroPGx`.
- **ZAROPGX_VERSION**: Overrides app version used in reports. If unset, read from `pyproject.toml`.

*Reports composition and content*
- **INCLUDE_PHARMCAT_HTML**: Include PharmCAT HTML in reports. Default: `true`.
- **INCLUDE_PHARMCAT_JSON**: Include PharmCAT JSON output in reports. Default: `true` (via `.env`; delivered through app `env_file`).
- **INCLUDE_PHARMCAT_TSV**: Include PharmCAT calls-only TSV output in reports. Default: `true` (via `.env`; delivered through app `env_file`).
- **EXECSUM_USE_TSV**: Use TSV rather than JSON report to generate Executive Summary. Code
  default `false` (`app/reports/generator.py`), but every tracked `.env.*` template ships `true`.
- **OUTSIDECALLSOVERRIDE**: When `true`, the pipeline looks for a manual outside-calls override
  file instead of the generated one (`app/utils/outside_calls_override.py`). Default: `false`
  (blank in the templates).
- **PDF_ENGINE**: Primary PDF engine. `weasyprint` or `reportlab`. Default: `weasyprint`.
- **PDF_FALLBACK**: If `true`, try alternate engine on failure. Default: `true`.

*Upload/header safety limits*
- **MAX_HEADER_READ_BYTES**: Header inspection byte cap. Default: `1000000000` (1 GB).
- **MAX_HEADER_PARSE_TIMEOUT_SEC**: Header parsing timeout seconds. Default: `300`.
- **MAX_UPLOAD_SIZE_BYTES**: Referenced nowhere in the repo and shipped in no `.env` template;
  listed only because reverse proxies in front of ZaroPGx often use the name.
- **MAX_UPLOAD_TIMEOUT_SEC**: Same — not referenced in the repo.

### Docker compose
*images*
- **ZAROPGX_TAG**: Tag of the pre-built `zaromicsresearch/zaropgx-*` images to pull from Docker
  Hub. Default: `0.2.8`. Set to `latest` to track the newest. `docker compose build` (or
  `up --build`) overrides the pull with a local build.
- **HAPI_FHIR_TAG**: Pinned tag for the bundled `hapiproject/hapi` image. Default: `v8.10.0-2`.
  Bump deliberately — the service owns a live Postgres schema and point releases have shipped
  non-zero-downtime migrations. Keep `data/versions/hapi.json` in step.
- **PHARMCAT_VERSION**: Doubles as the build ARG for the PharmCAT image. See below.

*runtime*
- **BIND_ADDRESS**: Host bind for the main app port mapping. Default: `8765` (host port only,
  i.e. all interfaces). `.env.local` uses `8765`; `.env.production` uses `0.0.0.0:8765`. A bare
  `BIND_ADDRESS=0.0.0.0` is not a valid Compose host port — always give a port.
- **INTERNAL_BIND_ADDRESS**: Host interface for every *internal* published service (`db`,
  `pharmcat`, `gatk-api`, `pypgx`, `zarohla`, `genome-downloader`, `fhir-server`, `kroki`,
  `docs`). Default: `127.0.0.1`. None of those services authenticate, so widening this exposes
  them — and the database — directly. Prefer an SSH tunnel:
  `ssh -L 5444:127.0.0.1:5444 <host>`. `nextflow` is not published at any value of this knob.
- **NETWORK_SUBNET**: Compose network subnet. Default: `172.28.0.0/16` (`.env.local` overrides
  to `172.20.0.0/16`).

*service URLs*
- **GENOME_DOWNLOADER_API_URL**: Genome downloader API URL. Default: `http://genome-downloader:5050`.
- **NEXTFLOW_RUNNER_URL**: Nextflow executor base URL. Default: `http://nextflow:5055`.
- **GATK_API_URL**: GATK wrapper API base URL. Default: `http://gatk-api:5000`.
- **PYPGX_API_URL**: PyPGx wrapper API base URL. Default: `http://pypgx:5000`.
- **PHARMCAT_API_URL**: PharmCAT wrapper base URL. Default: `http://pharmcat:5000`.
- **KROKI_URL**: Kroki rendering service base URL. Compose sets `http://kroki:8000` (topology); do not set this in `.env`.
- **FHIR_SERVER_URL**: HAPI FHIR server URL. Default: `http://fhir-server:8080/fhir`.
- **ZAROHLA_API_URL**: ZaroHLA (OptiType) service URL used by the app's health and
  services-status probes. Default: `http://zarohla:5000`. The pipeline's `/call-hla` calls in
  `pipelines/pgx/main.nf` use that hostname directly and ignore this variable.

### Paths and storage
- **DATA_DIR**: Base data directory (varies by service). Common default: `/data`.
- **TEMP_DIR**: Temp directory for services. Defaults:
  - PharmCAT wrapper: `/tmp/pharmcat`
  - GATK API: `TMPDIR` or `/tmp/gatk_temp`
- **UPLOAD_DIR**: Uploads directory. Default: `/data/uploads`.
- **REPORT_DIR**: Reports directory. Default: `/data/reports`.

### Reference genome assembly build and other content fetcher service
- **DOWNLOAD_ON_STARTUP**: If `true`, schedule downloads at startup. Default: `true`.

### PostgreSQL Database
- **DB_USER**: Database user. Default: `zaropgx_user` (app/db.py);
- **DB_PASSWORD**: Database password. Required when `DATABASE_URL` is unset. Compose hard-fails if unset (`${DB_PASSWORD:?...}`); start-docker generates a per-install value only when no Postgres volume exists yet.
- **DB_HOST**: Database host. Default: `db`.
- **DB_PORT**: Database port. Default: `5432`.
- **DB_NAME**: Database name. Default: `zaropgx_db`.
- **DATABASE_URL**: Full SQLAlchemy URL. Preferred when set (compose always passes one); otherwise constructed from the `DB_*` parts.
- **POSTGRES_PASSWORD**: Postgres container password — sourced from `DB_PASSWORD` in compose.

### Nextflow executor and workflow orchestration
- **NXF_HOME**: Nextflow home/cache directory. Defaults to `/opt/nextflow` in containers or set to `/data/nextflow` for persistence in some wrappers.
- **NXF_OPTS**: Nextflow JVM options. Defaults vary by container, e.g. `-Xms1g -Xmx4g`.

### ZaroHLA (OptiType) service
- **OPTITYPE_ENABLED**: Gates HLA typing end to end (see *Feature toggles* above). Default: `true`.
- **ZAROHLA_API_URL**: See *service URLs* above.
- **DATA_DIR**, **JOB_API_BASE**: Set by compose (`/data`, `http://app:8000/api/v1`); the
  service posts step progress back to the app through the latter.

The service is a FastAPI wrapper around OptiType v1.5, not a Nextflow pipeline: there are no
`ZAROHLA_PIPELINE_VERSION` or `ZAROHLA_PROFILE` variables in the codebase.

### GATK wrapper service
- **GATK_CONTAINER**: Container name for GATK. Default: `gatk`.
- **DATA_DIR**: Data directory. Default: `/data`.
- **TMPDIR**: Temp directory variable used as `TEMP_DIR`. Default: `/tmp/gatk_temp`.
- **REFERENCE_DIR**: Reference files directory. Default: `/reference`.
- **MAX_MEMORY**: Memory hint for Java jobs. Default: `20g`.

### PyPGx wrapper service
- **PYPGX_MEMORY_LIMIT**: Memory limit hint for PyPGx. Default: `7G`.
- **PYPGX_MAX_PARALLEL_GENES**: Max concurrent gene tasks. Default: `8`.
- **PYPGX_BATCH_SIZE**: Batch size for processing. Default: `4`.
- **PYPGX_PHARMCAT_PREFERENCE**: Gene set preference: `auto` | `pypgx` | `pharmcat`. Code
  default `auto` (`docker/pypgx/pypgx_wrapper.py`), but compose and every `.env.*` template
  ship `pharmcat`.
- **PYPGX_PREFERRED**: In report generator, optional hint to prefer PyPGx where both can call. Default: `false`.
- **PHARMCAT_PREFERRED**: In report generator, optional hint to prefer PharmCAT where both can call. Default: `false`.

### PharmCAT wrapper service
- **PHARMCAT_VERSION**: Version for pipeline package in container build (ARG) and runtime
  metadata / version stamp (env). Default `3.4.0`.
- **PHARMCAT_REF_CACHE**: Named-volume mount path for GRCh38 reference files (default
  `/pharmcat-references`). Must not be `/pharmcat` — that path comes from the image.
- **PHARMCAT_LOG_LEVEL**: Log level inside PharmCAT wrapper. Default: `DEBUG`.
- **PHARMCAT_JAR_PATH**: Path to PharmCAT JAR for fallback direct execution. Default: `/pharmcat/pharmcat.jar`.
- **PHARMCAT_REFERENCE_DIR**: PharmCAT references directory. Default: `/pharmcat`.
- **PHARMCAT_PIPELINE_DIR**: PharmCAT pipeline directory. Default: `/pharmcat/pipeline`.
- **PHARMCAT_TEE**: If `true`, tee PharmCAT pipeline logs to file. Default: `true`.
- **PHARMCAT_ABSENT_TO_REF**: Pass PharmCAT's "treat absent positions as reference" flag.
  Default: `false`. Turning it on makes PharmCAT assume reference at every position missing
  from the VCF, which is only safe for a VCF you know covers all callable positions.
- **PHARMCAT_UNSPECIFIED_TO_REF**: Same idea for positions present but unspecified.
  Default: `false`.

### HAPI FHIR container (abridged; see HAPI FHIR docs)
- **HAPI_FHIR_TAG**: Image tag — see *Docker compose → images* above.
- **HAPI_FHIR_VERSION**: Optional display string for the report's platform/versions table. When
  unset, `app/core/version_manager.py` auto-detects it where possible.

These settings are passed straight through `compose.yml` to the HAPI container:
- `hapi.fhir.allow_external_references`
- `hapi.fhir.allow_multiple_delete`
- `hapi.fhir.reuse_cached_search_results_millis`
- `hapi.fhir.jpa.database_schema`
- `hapi.fhir.jpa.auto_create_tables`
- `hapi.fhir.jpa.database_schema_auto_create`
- `hapi.fhir.jpa.database_schema_auto_validate`
- `hapi.fhir.jpa.disable_temporary_table_creation`
- `hapi.fhir.max_page_size`
- `hapi.fhir.default_page_size`
- `hapi.fhir.subscription.resthook_enabled`
- `hapi.fhir.subscription.websocket_enabled`
- `spring.datasource.url`
- `spring.datasource.username`
- `spring.datasource.password`
- `spring.jpa.hibernate.ddl-auto`
- `spring.jpa.properties.hibernate.default_schema`
- `spring.jpa.open-in-view`
- `JAVA_OPTS`

Note: These are container-level settings; the main app consumes `FHIR_SERVER_URL` to interact with the server.