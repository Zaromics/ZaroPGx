# Database Schema Management

## Overview

This directory contains ZaroPGx's canonical fresh-install schema and deliberately hand-run SQL
for existing volumes. No automatic migration runner exists.

## Structure

- `init/00_complete_database_schema.sql` - **Single source of truth** for a fresh ZaroPGx schema
- `init/99_wait_for_ready.sql` - Fresh-initialization validation
- `init/migrations/` - Operator-reviewed SQL that is available inside the container but is
  never run automatically

## What's Included

The consolidated schema includes:

### Core Schemas
- **`cpic`** - Pharmacogenomic guidelines and reference data
- **`user_data`** - Patient and genetic data (HIPAA-compliant, UUID-based)
- **`reports`** - Generated reports and analysis outputs
- **`job_monitoring`** - Workflow and job tracking system
- **`fhir`** - HAPI FHIR server tables
- **`pharmcat`** - PharmCAT analysis results and pharmacogenomic data

### Public Tables
- **`genomic_file_headers`** - File metadata storage
- **`gene_groups`** - Gene categorization for UI
- **`gene_group_members`** - Gene-to-group relationships
- **`workflows`** - Workflow orchestration tracking
- **`workflow_steps`** - Individual step tracking within workflows
- **`workflow_logs`** - Execution logs for debugging and monitoring

### PharmCAT Schema Details
The `pharmcat` schema includes 9 tables and 3 convenience views:

**Tables:**
- `results` - Raw PharmCAT JSON results and metadata
- `gene_summary` - Flattened gene information
- `diplotypes` - Individual diplotype calls and phenotypes
- `drug_gene_map` - Drug-gene relationships
- `messages` - Analysis warnings and errors
- `variants` - Genetic variants found
- `drug_recommendations` - CPIC/DPWG drug recommendations
- `recommendation_conditions` - Conditions triggering recommendations
- `unannotated_gene_calls` - Gene calls that couldn't be fully annotated

**Views:**
- `actionable_findings` - Non-normal phenotypes requiring action
- `drug_recommendations_summary` - Aggregated drug recommendations
- `gene_analysis_summary` - Gene analysis overview with counts

### Features
- ✅ UUID primary keys throughout
- ✅ Proper foreign key relationships
- ✅ Performance indexes
- ✅ Sample data for testing
- ✅ Utility functions
- ✅ Complete permissions setup

## Schema Lifecycle

### Fresh Volumes

Compose mounts `db/init` at `/docker-entrypoint-initdb.d`. The PostgreSQL image runs the
top-level initialization files only when `zaropgx_pgdata` is empty. A fresh schema therefore
comes from `db/init/00_complete_database_schema.sql`.

### Existing Volumes

PostgreSQL skips `/docker-entrypoint-initdb.d` when the data directory already contains a
database. Editing the canonical schema or restarting the container does not update an existing
volume.

Every existing-volume change must be deliberate:
1. Back up the database.
2. Review the exact SQL required by the release.
3. Stop services that write to the database.
4. Hand-run the reviewed file and verify the result.

There is no migration ledger, application-startup DDL, or automatic migration runner.

### Break-Glass Manual Upgrade

The nested `init/migrations` directory is mounted into the database container but is not
executed by the PostgreSQL entrypoint. This makes a reviewed file available for an explicit
operator command without turning it into startup automation:

```bash
# Back up the target database first.
docker compose exec -T db pg_dump \
  -U zaropgx_user \
  -d zaropgx_db > zaropgx-before-manual-upgrade.sql

# Apply one reviewed upgrade and stop on the first SQL error.
docker compose exec -T db psql \
  -v ON_ERROR_STOP=1 \
  -U zaropgx_user \
  -d zaropgx_db \
  -f /docker-entrypoint-initdb.d/migrations/02_fix_variant_column_lengths.sql
```

Use the database role and name configured for the target volume. Release-specific operator
steps belong in `UPGRADING.md`; the v0.2.4 to v0.2.5 credential recovery uses
`scripts/fix-legacy-credentials.sh`.

## Testing

To test the schema after a fresh initialization:

```bash
# Start the database with a fresh volume
docker compose down -v
docker compose up -d db

# Connect and verify all schemas exist
docker exec -it pgx_db psql -U zaropgx_user -d zaropgx_db -c "\dn"

# Check that all tables exist in each schema
docker exec -it pgx_db psql -U zaropgx_user -d zaropgx_db -c "\dt cpic.*"
docker exec -it pgx_db psql -U zaropgx_user -d zaropgx_db -c "\dt user_data.*"
docker exec -it pgx_db psql -U zaropgx_user -d zaropgx_db -c "\dt job_monitoring.*"
docker exec -it pgx_db psql -U zaropgx_user -d zaropgx_db -c "\dt pharmcat.*"

# Verify PharmCAT views were created
docker exec -it pgx_db psql -U zaropgx_user -d zaropgx_db -c "\dv pharmcat.*"

# Check public tables
docker exec -it pgx_db psql -U zaropgx_user -d zaropgx_db -c "\dt public.*"
```

## Fresh Database Reset

To reset the database and re-run initialization scripts:

```bash
# Stop containers and remove database volume
docker compose down -v

# Start fresh (init scripts will run automatically)
docker compose up -d
```
