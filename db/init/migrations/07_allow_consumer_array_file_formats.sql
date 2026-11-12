-- db/init/migrations/07_allow_consumer_array_file_formats.sql
-- Applied at app startup by app/api/db_migrations.py. Fresh installs get the same
-- constraint from 00_complete_database_schema.sql.
--
-- genomic_file_headers.file_format did not allow '23ANDME' or 'ANCESTRY', so a
-- consumer genotyping array upload would fail at header analysis with a
-- CheckViolation the moment those types became acceptable -- the same way every gVCF
-- upload failed before 06 (found 2026-09-27). Added here rather than by editing 06,
-- because a database that has already applied 06 would never re-run it.
--
-- VARCHAR(10) still fits: '23ANDME' is 7 characters and 'ANCESTRY' is 8.
--
-- Idempotent: the constraint is dropped and re-added.

ALTER TABLE genomic_file_headers DROP CONSTRAINT IF EXISTS genomic_file_headers_file_format_check;

ALTER TABLE genomic_file_headers ADD CONSTRAINT genomic_file_headers_file_format_check CHECK (file_format IN ('BAM','SAM','CRAM','VCF','GVCF','BCF','FASTA','FASTQ','23ANDME','ANCESTRY'));
