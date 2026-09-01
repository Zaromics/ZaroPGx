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

A gVCF that has been written as a BCF is routed to the gVCF lane below rather than this one — the `##GVCFBlock` records are read out of the binary header to catch it.

### Processing Path — BCF
```
BCF → bcftools (VCF conversion) → Header Analysis → PyPGx → PharmCAT → Reports
```

## Genomic VCF (gVCF)
A gVCF records *reference-confidence blocks* — spans the caller is confident match the reference — alongside the variant calls. PharmCAT cannot read one: PharmCAT 3.4.0 detects a gVCF (from the filename, from a `##GVCFBlock` header record, or from a reference-block data row) and refuses it outright, so a gVCF handed to it produces an error, not a wrong answer.

ZaroPGx converts it instead, and the conversion makes a gVCF a **better** input than a plain VCF rather than merely an acceptable one. It runs GATK `GenotypeGVCFs` twice: once over PharmCAT's own position list with `--include-non-variant-sites`, and once over everything else, joining the two with `bcftools concat -a`. The first pass is the point — the homozygous-reference genotypes at the pharmacogene positions come from *your file's own reference-confidence blocks*, so they are called data. The plain-VCF lane has no such information and can only fill those positions in with PharmCAT's `--absent-to-ref`, which fabricates them; on the gVCF lane that flag is not used and is not needed.

What the report tells you, and why:

- **How much of PharmCAT's position list your file actually covered.** A gVCF that omits a region has no reference block there, so those positions are no-calls — absent is not reference.
- **That `GenotypeGVCFs` re-derives each genotype** from the recorded likelihoods rather than copying your caller's. ZaroPGx sets the calling-confidence threshold to zero so nothing is dropped for failing a cutoff you did not choose, but the genotypes analysed are still not guaranteed identical to your caller's output.
- Positions PharmCAT discards because their indel representation does not match its own definitions stay no-calls. That is the same outcome a plain VCF gets, not a cost of the conversion.

Two kinds of gVCF are refused, each because the conversion genuinely cannot proceed:

- **Non-GATK reference blocks.** DeepVariant, bcftools and some Illumina callers write `<*>` where GATK writes `<NON_REF>`, and `GenotypeGVCFs` stops on such a file with "The list of input alleles must contain `<NON_REF>` as an allele". Genotype it with your own caller's tool and upload the resulting plain VCF. **Do not** filter the reference blocks out by hand: `bcftools view -e 'ALT="<NON_REF>"'` and its equivalents delete your real variants too, because a gVCF's variant rows carry the reference allele alongside the alternate one.
- **GRCh37/hg19 gVCFs.** PharmCAT's position list — the interval list the reference pass is emitted over — exists in GRCh38 coordinates only, so there is nothing to run that pass against. Run `gatk GenotypeGVCFs` on it yourself and upload the resulting GRCh37 VCF: that *is* supported, and ZaroPGx lifts it over to GRCh38 for you.

Because the file that gets analysed is a VCF, every VCF caveat applies: no HLA typing, degraded accuracy for *CYP2D6*, and degraded accuracy for genes whose phenotypes depend on structural or copy-number variants. Exactly one sample, as for VCF and BCF — a joint-called multi-sample gVCF is refused.

### Processing Path — gVCF
```
gVCF → GATK GenotypeGVCFs ×2 (VCF conversion) → Header Analysis → PyPGx → PharmCAT → Reports
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

## Consumer genotyping arrays (23andMe, AncestryDNA) — not accepted

ZaroPGx recognises a 23andMe or AncestryDNA raw-data export and refuses it by name. **This is a decision, not a missing converter.** The coordinates in those files are perfectly good — build 37, plus strand, real positions — and turning one into a VCF is a one-line `bcftools convert --tsv2vcf`. What is wrong is what happens next.

Measured against the 1,226 positions in PharmCAT's own `pharmcat_positions.vcf` (22 genes, 157 of the positions *CYP2D6*), counting each vendor's published manifest — which is the union of every revision of that chip, and therefore an upper bound on any individual file:

| | 23andMe v3 | v4 | v5 | AncestryDNA v1 | v2 |
|---|---:|---:|---:|---:|---:|
| **All positions** | 183 (14.9%) | 193 (15.7%) | 229 (18.7%) | 43 (3.5%) | 380 (31.0%) |
| **CYP2D6** (157) | 22 | 23 | 25 | 2 | 14 |
| CYP2C9 (88) | 15 | 16 | 22 | 2 | 7 |
| CYP2C19 (35) | 17 | 19 | 17 | 5 | 8 |
| NUDT15 (20) | 0 | 0 | 1 | 0 | 0 |

Only 222 of 23andMe v5's 229 are SNVs, so its real ceiling is 18.1%. A newer chip is not a better pharmacogenomic chip: v5 covers fewer *CYP2D6* markers in the gene window than v4 does.

The variants that define the common star alleles are absent by name. 23andMe v5 has no `rs3892097` (`CYP2D6*4`, roughly 20% allele frequency in Europeans), no `rs1065852` (`*10`, the most common East Asian allele), and neither `rs16947` nor `rs1135840` (both core to `*2`); it also lacks `rs28371686` (`CYP2C9*5`) and `rs7900194` (`CYP2C9*8`). `rs35742686` (`CYP2D6*3`) and `rs3064744` (the `UGT1A1*28` TA repeat) are absent from every version of every vendor. And no chip, by any method, can detect the gene duplications and deletions that decide the phenotype for *CYP2D6* and several other genes.

PharmCAT alone would degrade honestly — it reports a position it cannot see as a no-call. **ZaroPGx does not run PharmCAT alone.** It runs PyPGx too and hands PyPGx's calls to PharmCAT as outside calls, and an outside call overrides a no-call. PyPGx's maintainer, on array input ([pypgx#142](https://github.com/sbslee/pypgx/issues/142)): missing loci "will be falsely treated as homozygous reference even though there might be variants." So a 23andMe v5 file with no `rs3892097` yields `CYP2D6 *1/*1`, and the report tells a `CYP2D6 *4/*4` poor metaboliser they metabolise codeine and tamoxifen normally. That is a confident wrong answer, not an incomplete one.

PharmCAT's own FAQ reaches the same conclusion: consumer-array data has "limited overlap with most of the gene definitions used by PharmCAT, which will result in very few callable alleles and therefore not very useful reports."

Upload sequencing data instead: a GRCh38/hg38 VCF, or a BAM, CRAM or SAM.

## Reference Genome Support
- **GRCh38/hg38** — analysed directly; the build every result is reported on.
- **GRCh37/hg19 VCF or BCF** (Legacy) — **lifted over to GRCh38 automatically** before analysis, using GATK Picard `LiftoverVcf` with UCSC's hg19→hg38 chain. A real coordinate conversion, not a contig relabelling. Variants that cannot be mapped are dropped and the step reports how many; the run fails if too much of the file cannot be lifted. A BCF is converted to a VCF first, then lifted. A native GRCh38 VCF remains the most reliable input.
- **GRCh37/hg19 gVCF** — **not accepted.** The gVCF lane's value is the reference pass it emits over PharmCAT's own position list, and that list exists in GRCh38 coordinates only. Run `gatk GenotypeGVCFs` on it yourself and upload the resulting GRCh37 VCF, which *is* lifted. See the gVCF section above.
- **GRCh37/hg19 BAM, CRAM or SAM** — **not accepted.** Liftover converts variants that have already been called. Aligned reads are analysed by calling variants out of them first, and that call reads each gene from its GRCh38 position — on GRCh37 reads those positions are wrong (GRCh38's *CYP2D6* window sits roughly 400 kb from GRCh37's), so you would get star alleles that are not yours rather than an error. Call variants against GRCh37/hg19 yourself and upload the VCF, or realign the reads to GRCh38/hg38.
- **T2T-CHM13 (any format)** — **detected and refused.** ZaroPGx reads the assembly out of the file's own contig lengths, or out of its `##reference=` line, and declines the upload. Nothing downstream would catch a CHM13 file: PharmCAT's preprocessor normalises against GRCh38.p13 without checking which assembly the input is on, and `bcftools norm -c ws` *swaps* a mismatched reference allele rather than failing — so the report would carry confidently wrong star alleles. There is no automatic liftover for it, and doing one yourself is not a workaround either: the published T2T chains exclude GRCh38's alternate haplotype contigs, so *GSTT1* (on `chr22_KI270879v1_alt`) cannot come across at all, only about 60% of T2T's segmental duplications have a clear GRCh38 orthologue — the *CYP2D6*/*CYP2D7*/*CYP2D8* cluster is one such region — and no published work characterises *CYP2D6* or *CYP2C19* in CHM13. Call your variants against GRCh38/hg38, or realign to it, and upload that.

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
