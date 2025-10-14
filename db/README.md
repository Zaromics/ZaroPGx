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

## Usage

The database is automatically initialized when the PostgreSQL container starts. No manual migration steps are required during early development.

**Note:** Alembic is installed as a dependency but not actively used until approaching v1.0 release.

## Testing

To test the schema after a fresh initialization:

```bash
# Start the database with a fresh volume
docker compose down -v
docker compose up -d db

# Connect and verify all schemas exist
docker exec -it pgx_db psql -U cpic_user -d cpic_db -c "\dn"

# Check that all tables exist in each schema
docker exec -it pgx_db psql -U cpic_user -d cpic_db -c "\dt cpic.*"
docker exec -it pgx_db psql -U cpic_user -d cpic_db -c "\dt user_data.*"
docker exec -it pgx_db psql -U cpic_user -d cpic_db -c "\dt job_monitoring.*"
docker exec -it pgx_db psql -U cpic_user -d cpic_db -c "\dt pharmcat.*"

# Verify PharmCAT views were created
docker exec -it pgx_db psql -U cpic_user -d cpic_db -c "\dv pharmcat.*"

# Check public tables
docker exec -it pgx_db psql -U cpic_user -d cpic_db -c "\dt public.*"
```

## Fresh Database Reset

To reset the database and re-run initialization scripts:

```bash
# Stop containers and remove database volume
docker compose down -v

# Start fresh (init scripts will run automatically)
docker compose up -d
```
