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

## Binary Call Format (BCF)
BCF is the binary encoding of a VCF. It holds exactly the same variant records, so nothing is lost by converting one — but the analysis tools in the stack decide what a file is from its *name*, so ZaroPGx converts it for real (`bcftools view -O z`, plus a tabix index) before anything else sees it, rather than relabelling it. The conversion runs inside ZaroPGx and fails the job loudly if it produces an empty or malformed VCF.

Because the file that gets analysed is a VCF, every VCF caveat applies to a BCF upload: no HLA typing, degraded accuracy for *CYP2D6*, and degraded accuracy for genes whose phenotypes depend on structural or copy-number variants. A GRCh37/hg19 BCF is converted first and then lifted over to GRCh38, exactly as a GRCh37 VCF is.

A gVCF that has been written as a BCF is still refused, for the reason gVCFs are refused generally — the `##GVCFBlock` records are read out of the binary header to catch it.

### Processing Path — BCF
```
BCF → bcftools (VCF conversion) → Header Analysis → PyPGx → PharmCAT → Reports
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
FASTQ files contain raw sequencing reads with quality scores and are the starting point for most genomic analyses. **ZaroPGx does not accept them**, single- or paired-end: no aligner ships with ZaroPGx.

Align the reads to GRCh38/hg38 yourself — `bwa-mem2` or BWA for short reads, `minimap2` for long reads, or an established end-to-end pipeline such as nf-core/sarek — and upload the resulting BAM, CRAM or SAM. A GRCh38/hg38 VCF is the fastest input of all.

## Reference Genome Support
- **GRCh38/hg38** — analysed directly; the build every result is reported on.
- **GRCh37/hg19 VCF or BCF** (Legacy) — **lifted over to GRCh38 automatically** before analysis, using GATK Picard `LiftoverVcf` with UCSC's hg19→hg38 chain. A real coordinate conversion, not a contig relabelling. Variants that cannot be mapped are dropped and the step reports how many; the run fails if too much of the file cannot be lifted. A BCF is converted to a VCF first, then lifted. A native GRCh38 VCF remains the most reliable input.
- **GRCh37/hg19 BAM, CRAM or SAM** — **not accepted.** Liftover converts variants that have already been called. Aligned reads are analysed by calling variants out of them first, and that call reads each gene from its GRCh38 position — on GRCh37 reads those positions are wrong (GRCh38's *CYP2D6* window sits roughly 400 kb from GRCh37's), so you would get star alleles that are not yours rather than an error. Call variants against GRCh37/hg19 yourself and upload the VCF, or realign the reads to GRCh38/hg38.

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
