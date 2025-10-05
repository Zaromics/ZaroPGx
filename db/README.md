# Database Schema Management

## Overview

This directory contains the database schema for ZaroPGx. During early development (pre-v1.0), all schema changes are managed through direct SQL file modifications. Alembic is kept as a dependency for future production use.

## Structure

- `init/00_complete_database_schema.sql` - **Single source of truth** for all database schemas (current approach)

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

## Usage

The database is automatically initialized when the PostgreSQL container starts. No manual migration steps are required during early development.

**Note:** Alembic is installed as a dependency but not actively used until approaching v1.0 release.

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
