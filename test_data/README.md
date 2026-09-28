# Test Data

Fixtures for the test suite and for trying the stack by hand.

## Variant calls

- `pharmcat.example.vcf` - PharmCAT's example VCF. The full-stack e2e harness uploads it.
- `pharmcat.example2.vcf` - A second PharmCAT example; input of `pharmcat.example2.report.tsv`.
- `sample_cpic.vcf` - A small VCF for manual uploads (see the README).
- `grch37_pgx_snps.vcf` - GRCh37 PGx SNPs, for the liftover tests.
- `t2t_chm13_pgx_snps.vcf` - The same SNPs on T2T-CHM13, for the refusal tests.

## Alignments

- `pgx_ngs_example.bam` / `.cram` / `.sam` - Reads over pharmacogenes, for the BAM, CRAM and SAM lanes.
- `pgx_wgs_hla_example.bam` - Reads over pharmacogenes and HLA-A/B/C, for HLA typing.

## PharmCAT output

- `pharmcat.example.report.json` / `.tsv`, `pharmcat.example2.report.tsv` - Parser inputs.
- `pharmcat.example.v340.report.json` / `.tsv`, `pharmcat.example.nested.v2.report.json` -
  Golden files for PharmCAT 3.4.0's report layouts.
