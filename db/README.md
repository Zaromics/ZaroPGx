# Database schema

- `init/00_complete_database_schema.sql`: the complete schema for a fresh database.
- `init/99_wait_for_ready.sql`: checks the fresh initialisation.
- `init/migrations/`: changes for existing databases, applied by the app at startup.

## Fresh databases

Compose mounts `db/init` at `/docker-entrypoint-initdb.d`. PostgreSQL runs the top-level
files there only when the `zaropgx_pgdata` volume is empty, so a fresh database comes from
`00_complete_database_schema.sql`, which already includes every migration's end state.

## Existing databases

PostgreSQL never re-runs the init files, so a schema change reaches an existing volume only
as a migration. At startup the app (`app/api/db_migrations.py`) applies, in order, each
`init/migrations/NN_*.sql` not yet recorded in the `schema_migrations` table, under an
advisory lock so concurrent workers do not race.

Migrations 02-05 are historical: they turn a pre-`jobs` schema into the current one, and
they are not idempotent. The first time the app finds a database that already has `jobs` but
no `schema_migrations`, it records 02-05 as applied without running them. From 06 on, every
migration runs on every database that has not recorded it, so it must be idempotent
(`IF EXISTS`, `IF NOT EXISTS`, or a guarded `DO` block).

To change the schema, edit `00_complete_database_schema.sql` and add the next numbered file
to `init/migrations/` that brings an existing database to the same state. Release notes that
need an operator's hand go in `UPGRADING.md`.

## Schemas

| Schema | In use |
|---|---|
| `public` | `jobs`, `job_steps`, `job_logs`, `genomic_file_headers`, `schema_migrations` |
| `user_data` | `patients`, `genetic_data` |
| `pharmcat` | PharmCAT's results, loaded from its JSON: `results`, `gene_summary`, `diplotypes`, `drug_gene_map`, `messages`, `variants`, `drug_recommendations`, `recommendation_conditions`, `unannotated_gene_calls`, and three views |
| `fhir` | HAPI FHIR's tables, which it manages itself |

`cpic`, `reports`, `user_data.patient_alleles` and the `gene_groups` tables are created but
not used.

## Backup and reset

```bash
# Dump
docker compose exec -T db pg_dump -U zaropgx_user zaropgx_db > zaropgx.sql

# Reset to a fresh database: deletes every job record and loaded result
docker compose down
docker volume rm zaropgx_pgdata
docker compose up -d
```

Use the role and database name set in `.env` (`DB_USER`, `DB_NAME`) if you changed them.
