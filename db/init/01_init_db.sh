#!/bin/bash
set -e

# Create schemas and tables
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    \i /docker-entrypoint-initdb.d/migrations/cpic/01_create_schema.sql
    \i /docker-entrypoint-initdb.d/migrations/cpic/02_insert_sample_data.sql
    \i /docker-entrypoint-initdb.d/03_gene_groups_schema.sql
EOSQL 