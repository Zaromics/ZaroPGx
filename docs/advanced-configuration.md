## Advanced Configuration
This document lists environment variables and configuration flags used in the ZaroPGx codebase and containers.

*Last Revised 2025-10-07*

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

*Application configs*
- **LOG_LEVEL**: Logging level for the app. Default: `DEBUG`.
- **SECRET_KEY**: Secret key for auth/token signing. Required in production. Default in code: `supersecretkey`.
- **ZAROPGX_DEV_MODE**: If `true`, disables authentication for development. Default: `true`.
- **ALGORITHM**: JWT algorithm. Used as constant `HS256` in code.
- **ACCESS_TOKEN_EXPIRE_MINUTES**: Token expiry minutes. Used as constant `30` in code.
- **AUTHOR_NAME**: Override author shown in reports. If unset, read from `pyproject.toml` or fallback to `Zaromics Initiative`.
- **SOURCE_URL**: Project source URL in UI and reports. Default: `https://github.com/Zaroganos/ZaroPGx`.
- **ZAROPGX_VERSION**: Overrides app version used in reports. If unset, read from `pyproject.toml`.

*Reports composition and content*
- **INCLUDE_PHARMCAT_HTML**: Include PharmCAT HTML in reports. Default: `true`.
- **INCLUDE_PHARMCAT_JSON**: Include PharmCAT JSON output in reports. Default: `false`.
- **INCLUDE_PHARMCAT_TSV**: Include PharmCAT calls-only TSV output in reports. Default: `false`.
- **EXECSUM_USE_TSV**: Use TSV rather than JSON report to generate Executive Summary. Default: `false`.
- **PDF_ENGINE**: Primary PDF engine. `weasyprint` or `reportlab`. Default: `weasyprint`.
- **PDF_FALLBACK**: If `true`, try alternate engine on failure. Default: `true`.

*Upload/header safety limits*
- **MAX_HEADER_READ_BYTES**: Header inspection byte cap. Default: `1000000000` (1 GB).
- **MAX_HEADER_PARSE_TIMEOUT_SEC**: Header parsing timeout seconds. Default: `300`.
- **MAX_UPLOAD_SIZE_BYTES**: Not directly referenced in code; may be used externally for reverse proxies or UI.
- **MAX_UPLOAD_TIMEOUT_SEC**: Not directly referenced in code; may be used externally for reverse proxies or UI.

### Docker compose
*runtime*
- **BIND_ADDRESS**: Host bind for main app port mapping. Default: `8765` (host port).
- **NETWORK_SUBNET**: Compose network subnet. Default: `172.28.0.0/16`.

*service URLs*
- **GENOME_DOWNLOADER_API_URL**: Genome downloader API URL. Default: `http://genome-downloader:5050`.
- **NEXTFLOW_RUNNER_URL**: Nextflow executor base URL. Default: `http://nextflow:5055`.
- **GATK_API_URL**: GATK wrapper API base URL. Default: `http://gatk-api:5000`.
- **PYPGX_API_URL**: PyPGx wrapper API base URL. Default: `http://pypgx:5000`.
- **PHARMCAT_API_URL**: PharmCAT wrapper base URL. Default: `http://pharmcat:5000`.
- **KROKI_URL**: Kroki rendering service base URL. Default: `http://localhost:8001` (code) or `http://kroki:8000` (compose).
- **FHIR_SERVER_URL**: HAPI FHIR server URL. Default: `http://fhir-server:8080/fhir`.

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
- **DB_USER**: Database user. Default: `cpic_user` (app/db.py);
- **DB_PASSWORD**: Database password. Default: `cpic_password` (app/db.py). In docker-compose init: `test123`.
- **DB_HOST**: Database host. Default: `db`.
- **DB_PORT**: Database port. Default: `5432`.
- **DB_NAME**: Database name. Default: `cpic_db`.
- **DATABASE_URL**: Full SQLAlchemy URL. If not provided, constructed from the above.
- **POSTGRES_PASSWORD**: Postgres container password (docker-compose).

### Nextflow executor and workflow orchestration
- **NXF_HOME**: Nextflow home/cache directory. Defaults to `/opt/nextflow` in containers or set to `/data/nextflow` for persistence in some wrappers.
- **NXF_OPTS**: Nextflow JVM options. Defaults vary by container, e.g. `-Xms1g -Xmx4g`.

### hlatyping (OptiType) service
- **HLATYPING_PIPELINE_VERSION**: nf-core/hlatyping pipeline version. Default: `2.1.0`, current as of 0.3 release.
- **HLATYPING_PROFILE**: Nextflow profile for hlatyping. Default: `docker`. A conda-based profile is provided as fallback alternative.

### GATK wrapper service
- **GATK_CONTAINER**: Container name for GATK. Default: `gatk`.
- **DATA_DIR**: Data directory. Default: `/data`.
- **TMPDIR**: Temp directory variable used as `TEMP_DIR`. Default: `/tmp/gatk_temp`.
- **REFERENCE_DIR**: Reference files directory. Default: `/reference`.
- **MAX_MEMORY**: Memory hint for Java jobs. Default: `20g`.

### PyPGx wrapper service
- **PYPGX_MEMORY_LIMIT**: Memory limit hint for PyPGx. Default: `12G`.
- **PYPGX_MAX_PARALLEL_GENES**: Max concurrent gene tasks. Default: `8`.
- **PYPGX_BATCH_SIZE**: Batch size for processing. Default: `4`.
- **PYPGX_PHARMCAT_PREFERENCE**: Gene set preference: `auto` | `pypgx` | `pharmcat`. Default: `auto`.
- **PYPGX_PREFERRED**: In report generator, optional hint to prefer PyPGx where both can call. Default: `false`.
- **PHARMCAT_PREFERRED**: In report generator, optional hint to prefer PharmCAT where both can call. Default: `false`.

### PharmCAT wrapper service
- **PHARMCAT_VERSION**: Version for pipeline package in container build (ARG and runtime metadata).
- **PHARMCAT_LOG_LEVEL**: Log level inside PharmCAT wrapper. Default: `DEBUG`.
- **PHARMCAT_JAR_PATH**: Path to PharmCAT JAR for fallback direct execution. Default: `/pharmcat/pharmcat.jar`.
- **PHARMCAT_REFERENCE_DIR**: PharmCAT references directory. Default: `/pharmcat`.
- **PHARMCAT_PIPELINE_DIR**: PharmCAT pipeline directory. Default: `/pharmcat/pipeline`.
- **PHARMCAT_TEE**: If `true`, tee PharmCAT pipeline logs to file. Default: `true`.

### HAPI FHIR container (abridged; see HAPI FHIR docs)
These are passed through docker-compose to the HAPI container:
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