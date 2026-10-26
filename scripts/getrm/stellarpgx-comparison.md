# StellarPGx compared with ZaroPGx on GeT-RM, 2026-10-25

StellarPGx (<https://github.com/SBIMB/StellarPGx>, MIT) genotypes star alleles from a
variation graph built over each gene and its pseudogene, where ZaroPGx's callers read a
linear alignment. Its CYP2D6 benchmark is the best published for a short-read caller
(Twesigomwe et al., Clin Pharmacol Ther 2021;110(3):741-749, PMID 33492672: 99% diplotype
concordance on 109 genomes, against Cyrius 98%, Stargazer 84%, Aldy 82%), and its licence
is one ZaroPGx can ship. So it was measured, on the same genomes and the same score as
ZaroPGx itself, to see whether it makes any report more complete or more accurate.

It does not, and it is not adopted. The numbers below say why, and are kept so the
question does not have to be re-argued from memory.

## What was run

StellarPGx v1.2.8 (the release; `master` a76db42 as well for the genes that changed since),
Docker image `twesigomwedavid/stellarpgx-dev:latest`, on the 61 genomes of `core.txt` plus
the structural controls, from the same cuts ZaroPGx was scored on (converted to BAM: its
graphtyper step cannot read these CRAMs). 21 genes, one pipeline run each. ZaroPGx's side
is the v0.4.1 release, scored by `score.py` on the same truth.

## Result

On the genes StellarPGx calls at all, scored against GeT-RM by `score.py`:

| | Agrees with GeT-RM |
|---|---|
| ZaroPGx 0.4.1 | 766/804 (95.3%) |
| StellarPGx 1.2.8 | 306/329 (93.0%) |

StellarPGx is scored on fewer calls because it declines far more often.

**Where StellarPGx was right and ZaroPGx wrong (2 of 329):**

- CYP2D6 `*1/*21` where ZaroPGx says `*2/*21`: a phasing difference, same activity score
  and phenotype.
- CYP4F2 `*2/*3` where ZaroPGx says `*1/*4`: the same two-phasings-one-genotype ambiguity
  the ZaroPGx score already reports on this gene.

**Where ZaroPGx was right and StellarPGx wrong (6):** CYP2C8 `*18` and `*17` (both read as
`*1/*1`), CYP2D6 `*36x2+*10` versus `*36+*10x2`, NUDT15 `*6` twice (a thiopurine dosing
allele), TPMT `*3A/*16` read as `*16/*3E`.

**Where StellarPGx says nothing:** no star-allele call on any genome for HLA-A/-B/-C, DPYD,
SLCO1B1 or VKORC1; CYP3A4 declined on 5 of 28. Nine further genes ZaroPGx reports are not
in its set at all.

## Three defects found in StellarPGx itself

- **CYP1A2, v1.2.8:** `scripts/cyp1a2/hg38/bin/snv_def_modules.py` has a Python syntax error
  (an `elif` after an unindented statement), so every call crashes while the pipeline still
  reports success. Fixed on `master`, not in any release.
- **CYP2E1:** listed as supported, but its caller only prints copy number and variants, and
  `main.nf` passes it the argument list the other genes' callers take, so it crashes before
  that. No star allele on any genome.
- **NAT2:** `nat2.nf` runs `whatshap`, which the project's own published container does not
  have: all 67 genomes fail.

## master changes nothing

`master` (a76db42) differs from v1.2.8 in CYP2D6's databases, CYP1A2's caller and NAT2's.
Re-run on all 61 genomes:

- **CYP2D6:** not one call changed. Both callers 90% on this gene.
- **CYP1A2:** now runs, and is not comparable: StellarPGx names CYP1A2 alleles by PharmVar's
  numbers (`*1` to `*47`), GeT-RM and PyPGx by the older letters (`*1A`, `*1C`, `*1F`,
  `*1L`). "*1/*30" and "*1A/*1F" may be the same genome; neither caller can be called better
  here, and mixing the two systems in one report would mislead.
- **NAT2:** still fails, for the `whatshap` reason above.

## Reproducing it

The run is driven outside this repository (StellarPGx is a Nextflow pipeline of its own).
What it needs: its repo and container, the cuts converted to BAM, and a config that enables
Docker and mounts the reference and BAMs read-only. Its output, one
`<gene>/alleles/<sample>_<gene>.alleles` per sample with the call on the line after
`Result:`, is written into the layout `score.py` reads, and scored with the same readings as
ZaroPGx's own calls.
