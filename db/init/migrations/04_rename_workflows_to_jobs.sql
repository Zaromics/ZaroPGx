-- Hand-run only (auto-migration CUT). Fresh installs use jobs* in 00_complete_database_schema.sql.
-- Safe for internal test DBs; no prod migration path required.

ALTER TABLE IF EXISTS workflows RENAME TO jobs;
ALTER TABLE IF EXISTS workflow_steps RENAME TO job_steps;
ALTER TABLE IF EXISTS workflow_logs RENAME TO job_logs;

ALTER TABLE IF EXISTS job_steps RENAME COLUMN workflow_id TO job_id;
ALTER TABLE IF EXISTS job_logs RENAME COLUMN workflow_id TO job_id;

ALTER TABLE IF EXISTS jobs RENAME COLUMN workflow_metadata TO job_metadata;

-- Optional: rename constraints/indexes if they still carry workflow_* names (ignore errors if absent)
ALTER INDEX IF EXISTS idx_workflow_steps_workflow_id RENAME TO idx_job_steps_job_id;
ALTER INDEX IF EXISTS idx_workflow_steps_step_order RENAME TO idx_job_steps_step_order;
ALTER INDEX IF EXISTS idx_workflow_logs_workflow_id RENAME TO idx_job_logs_job_id;
ALTER INDEX IF EXISTS idx_workflow_logs_timestamp RENAME TO idx_job_logs_timestamp;
