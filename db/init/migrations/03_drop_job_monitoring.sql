-- Hand-run only (auto-migration runner is CUT).
-- Drops the unused legacy job_monitoring schema and all Job tables.
-- Fresh installs no longer create this schema (see 00_complete_database_schema.sql).
--
-- Example:
--   docker exec -it pgx_db psql -U zaropgx_user -d zaropgx_db \
--     -f /docker-entrypoint-initdb.d/migrations/03_drop_job_monitoring.sql

DROP SCHEMA IF EXISTS job_monitoring CASCADE;
