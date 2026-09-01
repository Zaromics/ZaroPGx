# Test Data

This directory contains sample VCF files and other test data used by the application.

## Files:

- `pharmcat.example2.vcf` - New example VCF file used for PharmCAT demo functionality (currently the default)
- `pharmcat.example.vcf` - Original example VCF file used for PharmCAT demo functionality
- `pharmcat_example.vcf` - Alternative PharmCAT example file
- `example2.vcf` - Additional test VCF file
- `sample_cpic.vcf` - Sample CPIC VCF file
- `pgx_ngs_example.bam` / `.cram` / `.sam` - Real aligned fixtures for the BAM/CRAM/SAM lanes
- `pgx_wgs_hla_example.bam` - Real aligned fixture carrying HLA reads
- `grch37_pgx_snps.vcf` - GRCh37/hg19 PGx SNP fixture for the liftover A/B test
- `NA12878.mini.bam` - **Not a BAM.** A ~270 KB GitHub HTML error page committed as a bad
  download (2025-04-24). Do not use it as a fixture; see `DOCKER_STACK_NOTES.md`.
- `test.bam` - **Not a BAM.** Same bad download as above.