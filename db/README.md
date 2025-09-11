# Database Schema Management

## Overview

This directory contains the consolidated database schema for ZaroPGx. All database initialization is handled through a single, comprehensive SQL file.

## Structure

- `init/00_complete_database_schema.sql` - **Single source of truth** for all database schemas
- `init/*.old` - Backed up old migration files (can be safely deleted after testing)

## What's Included

The consolidated schema includes:

### Core Schemas
- **`cpic`** - Pharmacogenomic guidelines and reference data
- **`user_data`** - Patient and genetic data (HIPAA-compliant, UUID-based)
- **`reports`** - Generated reports and analysis outputs
- **`job_monitoring`** - Workflow and job tracking system
- **`fhir`** - HAPI FHIR server tables

### Public Tables
- **`genomic_file_headers`** - File metadata storage
- **`gene_groups`** - Gene categorization for UI
- **`gene_group_members`** - Gene-to-group relationships

### Features
- ✅ UUID primary keys throughout
- ✅ Proper foreign key relationships
- ✅ Performance indexes
- ✅ Sample data for testing
- ✅ Utility functions
- ✅ Complete permissions setup

## Migration History

The following files have been consolidated into `00_complete_database_schema.sql`:

- `01_create_all_schemas.sql` → **Consolidated**
- `02_create_fhir_schema.sql` → **Consolidated** (backed up as `.old`)
- `03_gene_groups_schema.sql` → **Consolidated** (backed up as `.old`)
- `04_create_genomic_file_headers.sql` → **Consolidated** (backed up as `.old`)
- `migrations/` directory → **Consolidated** (backed up as `.old`)
- `alembic/` directory → **Consolidated** (backed up as `.old`)

## Usage

The database is automatically initialized when the PostgreSQL container starts. No manual migration steps are required.

## Benefits of Consolidation

1. **Single source of truth** - No more conflicting schema definitions
2. **No race conditions** - All tables created in correct order
3. **Simplified maintenance** - One file to manage instead of multiple systems
4. **Consistent UUID usage** - All tables use UUIDs from the start
5. **Complete initialization** - Database is fully ready after container startup

## Testing

To test the new schema:

```bash
# Start the database
docker compose up -d db

# Connect and verify schemas exist
docker exec -it pgx_db psql -U cpic_user -d cpic_db -c "\dn"

# Check that all tables exist
docker exec -it pgx_db psql -U cpic_user -d cpic_db -c "\dt cpic.*"
docker exec -it pgx_db psql -U cpic_user -d cpic_db -c "\dt user_data.*"
docker exec -it pgx_db psql -U cpic_user -d cpic_db -c "\dt job_monitoring.*"
```

## Cleanup

After confirming everything works, you can safely delete the `.old` files:

```bash
rm db/init/*.old
rm -rf db/migrations.old
rm -rf alembic.old
rm alembic.ini.old
```
