# Database migrations

The app applies these at startup, in order, and records each in `schema_migrations`; see
`db/README.md`. PostgreSQL's init entrypoint does not run this directory.

| File | Change |
|---|---|
| `02_fix_variant_column_lengths.sql` | Widen the `pharmcat.variants` columns |
| `03_drop_job_monitoring.sql` | Drop the unused `job_monitoring` schema |
| `04_rename_workflows_to_jobs.sql` | Rename `workflows*` to `jobs*` |
| `05_job_workflow_type_snapshot.sql` | Add `jobs.workflow_type` and `jobs.workflow_snapshot` |
| `06_allow_gvcf_file_format.sql` | Allow `GVCF` in `genomic_file_headers.file_format` |

02-05 are historical and not idempotent; a database that already has the `jobs` table records
them without running them. A new migration takes the next number and must be idempotent.
