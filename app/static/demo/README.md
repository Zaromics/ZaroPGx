# The demo

**Run Demo** analyses `NA12878.pgx-demo.cram`: reads of NA12878 (Coriell GM12878), the
most studied reference sample, from the 1000 Genomes Project's 30x whole genomes,
sequenced at the New York Genome Center and aligned to GRCh38 (the 1000 Genomes analysis
set with decoys and HLA). Source: run
[ERR3239334](https://www.ebi.ac.uk/ena/browser/view/ERR3239334),
[NA12878.final.cram](https://ftp.sra.ebi.ac.uk/vol1/run/ERR323/ERR3239334/NA12878.final.cram),
15.8 GB, MD5 7b60651143aa1b18e807ced767886fd2.

`scripts/make-demo-cram.sh` cuts it to the reads its calls depend on, 2.0 M reads in 30 MB:
PyPGx's gene regions and PharmCAT's positions, the windows whose depth tells a whole
genome from a panel, HLA-A, -B and -C with the HLA allele contigs, and one chrM read in
ten. `NA12878.pgx-demo.regions.bed` lists the regions; the script says why each is there.
The demo runs every stage: the CRAM's conversion, HLA typing, PyPGx with its deletion and
duplication calls, mtDNA, PharmCAT and the reports. Its stages took about 8 minutes on a
10-core machine, most of it PyPGx.

## What it gives

Checked on 2026-10-20 against the whole genome, through ZaroPGx's own services: the same
66 PyPGx calls and copy numbers, the same HLA alleles and haplogroup, the same genotype at
every PharmCAT position (1,199 reference, 24 variant, 3 uncertain), and the same 22
PharmCAT calls. Where NA12878 has a published answer, it agrees:

| Gene | Demo | Published |
|---|---|---|
| CYP2D6 | \*3/\*68+\*4, Poor Metabolizer | \*3/\*68+\*4 (GeT-RM; Gaedigk et al. 2019) |
| HLA-A, -B, -C | A\*01:01/\*11:01, B\*08:01/\*56:01, C\*01:02/\*07:01 | the same (Nariai et al. 2015) |
| Mitochondria | haplogroup H13a1a1a; MT-RNR1 reference | H13a1a1, on an older PhyloTree |
| CYP2C19 | \*1/\*2, Intermediate Metabolizer | \*2 carrier (Coriell) |
| DPYD | c.1601G>A (\*4) and c.1627A>G (\*5) | \*4 carrier (GeT-RM; Pratt et al. 2016) |
| CYP2C9, CYP3A5, NAT2, UGT1A1 | \*1/\*2, \*3/\*3, \*4/\*5, \*1/\*80+\*28 | the genotypes in GIAB's HG001 benchmark (v4.2.1) |
| SLCO1B1 | \*1/\*15, Decreased Function | \*1/\*15 or \*1/\*45: the benchmark leaves a position out |
| GSTM1, UGT2B17 | \*0/\*B; \*2/\*2 (no copy) | deletions in 1000 Genomes' structural-variant calls |

## Terms

The 1000 Genomes data are free to use. Publications using them should acknowledge the
1000 Genomes Project and cite Byrska-Bishop et al., *Cell* 2022;185(18):3426-3440,
doi:[10.1016/j.cell.2022.08.004](https://doi.org/10.1016/j.cell.2022.08.004). The NA12878
cell line and DNA come from the NIGMS Human Genetic Cell Repository at the Coriell
Institute for Medical Research. These data were generated at the New York Genome Center
with funds provided by NHGRI Grant 3UM1HG008901-03S1.
