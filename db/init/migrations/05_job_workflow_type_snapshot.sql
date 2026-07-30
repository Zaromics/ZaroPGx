-- db/init/migrations/05_job_workflow_type_snapshot.sql
-- Hand-run only (auto-migration CUT). Fresh installs get columns from 00_complete_database_schema.sql.

ALTER TABLE IF EXISTS jobs ADD COLUMN IF NOT EXISTS workflow_type VARCHAR;
ALTER TABLE IF EXISTS jobs ADD COLUMN IF NOT EXISTS workflow_snapshot JSONB;

CREATE INDEX IF NOT EXISTS idx_jobs_workflow_type ON jobs(workflow_type);
