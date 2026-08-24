---
title: Supported File Formats
curation: not
---

# Supported File Formats

ZaroPGx supports multiple genomic data formats with automatic conversion and processing.

## Variant Call Format (VCF)
VCF is the standard format for storing genetic variant information. ZaroPGx can process VCF 4.x files directly without preprocessing.

### Processing Path — VCF
```
VCF → Header Analysis → PyPGx → PharmCAT → Reports
```

## Binary Alignment Map (BAM)
BAM files contain aligned sequencing reads and are commonly used for variant calling and analysis.

### Processing Path — BAM
```
BAM → HLA Typing → PyPGx → PharmCAT → Reports
```

## Compressed BAM (CRAM)
CRAM is a compressed version of BAM that uses reference-based compression for smaller file sizes.

### Processing Path — CRAM
```
CRAM → GATK (BAM conversion) → HLA Typing → PyPGx → PharmCAT → Reports
```

## Sequence Alignment Map (SAM)
SAM is the text-based format for aligned sequences, often used as an intermediate format.

### Processing Path — SAM
```
SAM → GATK (BAM conversion) → HLA Typing → PyPGx → PharmCAT → Reports
```

## FASTQ Format — not accepted
FASTQ files contain raw sequencing reads with quality scores and are the starting point for most genomic analyses. **ZaroPGx does not accept them.** No aligner ships with ZaroPGx, so raw reads cannot be turned into the aligned data every later step needs. A FASTQ upload is refused with an explanatory message rather than accepted and failed later, and this applies to single- and paired-end reads alike.

Align the reads to GRCh38/hg38 yourself — `bwa-mem2` or BWA for short reads, `minimap2` for long reads, or an established end-to-end pipeline such as nf-core/sarek — and upload the resulting BAM, CRAM or SAM. A GRCh38/hg38 VCF is the fastest input of all.

## Reference Genome Support
- **GRCh38/hg38** — analysed directly; the build every result is reported on.
- **GRCh37/hg19** (Legacy) — **lifted over to GRCh38 automatically** before analysis, using GATK Picard `LiftoverVcf` with UCSC's hg19→hg38 chain (a real coordinate conversion, not a contig relabelling). Variants that cannot be mapped onto GRCh38 are dropped — the job's liftover step reports how many, and the run fails outright if an implausibly large share of the file cannot be lifted. A lifted-over file's results may differ from a file sequenced and called directly against GRCh38/hg38, so a native GRCh38 VCF (or an upstream BAM/CRAM aligned to GRCh38) remains the most reliable input.

## File Size Considerations

### Typical File Sizes
| Format | Whole Genome | Exome | Targeted Panel |
|--------|--------------|-------|----------------|
| **VCF** | 1-5 GB | 50-200 MB | 1-10 MB |
| **BAM** | 50-100 GB | 2-5 GB | 50-500 MB |
| **CRAM** | 15-30 GB | 500 MB-1 GB | 10-100 MB |


## Next Steps

- **Learn about usage**: {doc}`usage`
- **Understand reports**: {doc}`reports`
- **Configure processing**: {doc}`../advanced-configuration`
- **Troubleshoot issues**: {doc}`troubleshooting`
