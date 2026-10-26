# GeT-RM validation

Checks ZaroPGx's calls against GeT-RM, CDC's consensus genotypes for Coriell reference
samples, on the samples whose 1000 Genomes 30x genome is public: 208 of GeT-RM's 363.

| File | What it is |
|---|---|
| `truth.tsv` | GeT-RM's genotypes for those 208 samples, copied as published, with the study each came from; HLA-A, -B and -C from IGSR's typing of the 1000 Genomes panel |
| `samples.tsv` | Each sample's population and CRAM URL |
| `core.txt` | The 60 samples run by default: every NUDT15 and TPMT variant carrier, the DPYD variants that change dosing, 27 CYP2D6 diplotypes with deletions, duplications and hybrids, and all five continental groups |
| `pharmcat_alleles.tsv` | The alleles PharmCAT 3.4.0 defines, so the score can tell an allele PharmCAT cannot name from a wrong call |
| `build_truth.py` | Writes `truth.tsv` and `samples.tsv` from the sources below |
| `cut.sh` | Cuts each sample's CRAM to the reads ZaroPGx reads (`scripts/make-demo-cram.sh`, as the demo is made): about 30 MB and 10 minutes a sample, fetched by region |
| `run.py` | Runs each cut through a ZaroPGx instance and keeps the outputs the score reads |
| `score.py` | Compares the calls with GeT-RM's |
| `stellarpgx-comparison.md` | What a second caller, StellarPGx, scored on the same genomes, and why it is not in the pipeline |

## Running it

On a test instance with `ZAROPGX_AUTH_MODE=open`, never one holding anyone's own data:

```bash
ZAROPGX_REFERENCE=./reference scripts/getrm/cut.sh ~/getrm/cuts
python scripts/getrm/run.py --base-url http://127.0.0.1:18765 --cuts ~/getrm/cuts --out ~/getrm/results
python scripts/getrm/score.py ~/getrm/results
```

`score.py` writes `calls.tsv`, a row per sample, gene and caller, and `summary.tsv`, the
counts per gene. Both name the caller: the report's (PharmCAT, or PyPGx or OptiType where
PharmCAT takes an outside call), and PyPGx's own call for the genes PharmCAT also calls.

## Reading the score

GeT-RM's genotypes are not complete ones, and its names are those of each study's time.
The score compares only what both sides can speak to:

- **`*1` in GeT-RM means none of the alleles the study tested.** An allele ZaroPGx calls
  that the study never reported in any sample is read as the reference allele, and so is
  one GeT-RM reports that the caller has no definition of (PharmCAT 3.4 has no UGT1A1
  \*60 or NUDT15 \*2). The verdict is then `match_projected` and says what was set aside,
  or `caller_cannot_name` when they still disagree.
- **Alleles in parentheses** were confirmed by one assay only; their rows are marked
  tentative.
- **DPYD** is compared variant by variant, at the variants the study tested.
- **SLCO1B1** is compared at c.521T>C, the variant its function turns on: GeT-RM's names
  predate PharmVar's 2021 renaming of the gene.
- **VKORC1** is compared at rs9923231, **HLA** at two fields.
- **GSTM1 and GSTT1**: most GeT-RM assays tell a gene present from absent, not one copy
  from two, so `*A/*A` there can be one copy.

A value the script cannot read is `not_scored`, never guessed. There are three.

## Sources

- GeT-RM Consolidated PGx and HLA table, 2025-08 version
  ([CDC](https://www.cdc.gov/lab-quality/php/get-rm/reference-materials.html); Scheinfeldt
  et al., *J Mol Diagn* 2025;27(6):457-464), which gives each gene's call from the most
  recent of GeT-RM's nine studies.
- 1000 Genomes high-coverage sequence indexes and populations (Byrska-Bishop et al.,
  *Cell* 2022;185(18):3426-3440). Read the data reuse statement there before publishing
  numbers made with these genomes.
- HLA types of the 1000 Genomes panel (Abi-Rached et al., *PLoS One* 2018,
  doi:10.1371/journal.pone.0206512), typed from exomes.

`build_truth.py` checks each file against its SHA-256: a source that changes makes it
stop rather than write a different truth.
