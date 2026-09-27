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

ZaroPGx converts it instead, and the conversion makes a gVCF a **better** input than a plain VCF rather than merely an acceptable one. It runs GATK `GenotypeGVCFs` twice: once over PharmCAT's own position list with `--include-non-variant-sites`, and once over everything else, joining the two with `bcftools concat -a`. The first pass is the point — the homozygous-reference genotypes at the pharmacogene positions come from *your file's own reference-confidence blocks*, so they are called data. The plain-VCF lane has no such information and can only fill those positions in with PharmCAT's `--absent-to-ref`, which fabricates them; the gVCF lane needs no such flag, and ZaroPGx adds none of its own.

The two assume-reference checkboxes under PharmCAT are not applied to a gVCF, whatever they are set to. The reference pass emits a row at *every* position in PharmCAT's list, so a position your file did not cover arrives as a `./.` row, and the only thing `--unspecified-to-ref` could do here is turn those uncovered positions into reference calls you did not make. The report says so if you ticked one.

What the report tells you, and why:

- **How much of PharmCAT's position list your file actually covered.** A gVCF that omits a region has no reference block there, so those positions are no-calls — absent is not reference.
- **That `GenotypeGVCFs` re-derives each genotype** from the recorded likelihoods rather than copying your caller's. At PharmCAT's positions ZaroPGx sets the calling-confidence threshold to zero and applies its own: a reference call needs a reference confidence of 20 or more over at least 7 reads, and no other allele your caller saw across the position; a variant call needs a genotype quality of 20 and a call quality of 30. Anything else is a no-call, so the genotypes analysed are not guaranteed identical to your caller's output.
- A variant whose indel representation PharmCAT cannot match stays a no-call, the same outcome a plain VCF gets. Reference calls at indel positions are written with PharmCAT's own alleles, so PharmCAT reads them.

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

For every aligned input (BAM, CRAM, SAM, and FASTQ once aligned), PharmCAT is given a call at each of its own positions, made from your reads: reference where every base of the position has at least 7 good reads for the reference and next to none for anything else (none below 20 reads, up to 5% above), a variant where the caller makes a confident call (genotype quality 20 or more, in the caller's own representation, so an allele PharmCAT does not list is never read as reference), and nothing where the reads support neither. A position with nothing is a no-call, not a reference call, so a gene your data did not cover is reported as not called rather than as normal. PharmCAT's assume-reference checkboxes are not applied to aligned inputs, for the same reason as for a gVCF. The report states how many positions were called, how many had no reads, and how many had too few.

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

## FASTQ Format — accepted up to 20 GB

FASTQ files contain raw sequencing reads with quality scores and are the starting point for most genomic analyses. ZaroPGx accepts short-read FASTQ, single-end or as an R1/R2 pair uploaded together, and aligns it for you with BWA against GRCh38 before running the usual pipeline. The two files of a pair are checked against each other: they must come from the same platform and their read names must pair up, so two unrelated read sets cannot be aligned as mates, and one file uploaded twice is refused too.

If your run is paired-end, upload both files. R1 alone is aligned as single-end, with half the reads and no mate to help place reads in genes with close copies (CYP2B6, CYP2D6), so heterozygous variants there can be missed. ZaroPGx warns when a lone file's name looks like one mate (`_R1`, `_2.fq.gz`).

Three limits apply, and all three are about the file rather than about ZaroPGx:

**Short reads only.** Oxford Nanopore and PacBio reads are recognised and refused: the aligner and the HLA typing are both short-read tools. Align long reads with minimap2 against GRCh38 and upload the BAM or CRAM.

**Size — 20 GB across all uploaded reads.** This covers targeted PGx panels and exome-sized read sets. It does not cover whole-genome FASTQ: alignment writes the reads out three times (unaligned, aligned and duplicate-marked BAM) on one machine, so a whole-genome read set would need several times its own size in free disk and many hours of alignment. Align whole-genome reads yourself (nf-core/sarek, or `bwa-mem` against GRCh38) and upload the BAM or CRAM.

**A detectable sequencing platform.** Alignment has to record which instrument produced the reads — the `@RG PL:` field — because GATK and PyPGx both read it. A FASTQ does not state this anywhere, so ZaroPGx works it out from the structure of the read names (Illumina encodes flowcell coordinates, MGI/DNBSEQ its lane, column and row, Oxford Nanopore its run and channel ids, PacBio its ZMW numbers) and checks that against the read lengths. If the reads have been re-exported from SRA, that naming is stripped and the platform cannot be established — ZaroPGx refuses rather than assuming Illumina, because a wrong platform silently changes how downstream tools treat your data. Upload the original run files, or align them yourself.

Alignment is by far the slowest step in the stack. If you already have a BAM, CRAM or SAM, uploading that is much faster, and a GRCh38/hg38 VCF is the fastest input of all.

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

Only 222 of 23andMe v5's 229 are SNVs, so its real ceiling is 18.1%. A newer chip is not uniformly a better pharmacogenomic chip: v5 covers fewer *CYP2C19* markers in the gene window than v4 does (17 against 19).

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
