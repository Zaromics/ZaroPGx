# Test Data

Fixtures for the test suite and for trying the stack by hand.

## Variant calls

- `pharmcat.example.vcf` - PharmCAT's example VCF. The full-stack e2e harness uploads it.
- `pharmcat.example2.vcf` - A second PharmCAT example; input of `pharmcat.example2.report.tsv`.
- `sample_cpic.vcf` - A small VCF for manual uploads (see the README).
- `grch37_pgx_snps.vcf` - GRCh37 PGx SNPs, for the liftover tests.
- `t2t_chm13_pgx_snps.vcf` - The same SNPs on T2T-CHM13, for the refusal tests.
- `pharmcat.unphased.example.vcf` - `pharmcat.example.vcf` cut to four heterozygous sites
  (SLCO1B1 c.388A>G and c.521T>C, CYP2B6 c.516G>T and c.785A>G), unphased and
  variant-only, so PharmCAT returns several possible diplotypes per gene.

## Alignments

- `pgx_ngs_example.bam` / `.cram` / `.sam` - Reads over pharmacogenes, for the BAM, CRAM and SAM lanes.
- `pgx_wgs_hla_example.bam` - Reads over pharmacogenes and HLA-A/B/C, for HLA typing.

## PharmCAT output

- `pharmcat.example.report.json` / `.tsv`, `pharmcat.example2.report.tsv` - Parser inputs.
- `pharmcat.unphased.example.report.json` / `.tsv` - PharmCAT 3.4 on
  `pharmcat.unphased.example.vcf` (JSON trimmed: no `variants` lists; simvastatin and
  efavirenz only), for the unresolved-call tests.
- `pharmcat.example.v340.report.json` / `.tsv`, `pharmcat.example.nested.v2.report.json` -
  Golden files for PharmCAT 3.4.0's report layouts.
