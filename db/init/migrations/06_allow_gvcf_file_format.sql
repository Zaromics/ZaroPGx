-- db/init/migrations/06_allow_gvcf_file_format.sql
-- Applied at app startup by app/api/db_migrations.py. Fresh installs get the same
-- constraint from 00_complete_database_schema.sql.
--
-- genomic_file_headers.file_format did not allow 'GVCF', so every gVCF upload made
-- through the app failed at header analysis with a CheckViolation, before the
-- pipeline started (found 2026-09-27; the gVCF lane had only been exercised by
-- running Nextflow directly). Idempotent: the constraint is dropped and re-added.

ALTER TABLE genomic_file_headers DROP CONSTRAINT IF EXISTS genomic_file_headers_file_format_check;

ALTER TABLE genomic_file_headers ADD CONSTRAINT genomic_file_headers_file_format_check CHECK (file_format IN ('BAM','SAM','CRAM','VCF','GVCF','BCF','FASTA','FASTQ'));
