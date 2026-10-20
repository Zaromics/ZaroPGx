---
title: Input formats
---

# Input formats

| Input | Accepted | Before the common pipeline |
|---|---|---|
| VCF (`.vcf`, `.vcf.gz`) | GRCh38; GRCh37/hg19 is lifted over | nothing, or liftover |
| BCF | as VCF | converted to VCF (bcftools) |
| gVCF | GRCh38, GATK reference blocks | genotyped (GATK GenotypeGVCFs) |
| BAM, SAM | GRCh38 | SAM converted to BAM (samtools) |
| CRAM | GRCh38, written against one of three references (below) | converted to BAM (samtools) |
| FASTQ | short reads, up to 20 GB by default | aligned to GRCh38 (bwa-mem) |
| 23andMe, AncestryDNA, BED, FASTA, T2T-CHM13 | refused, with the reason | |

One data file per analysis; a FASTQ's two mates count as one. A VCF, BCF or gVCF must hold
one sample. Index files (`.bai`, `.crai`, `.csi`, `.tbi`, `.idx`) may be added; the
pipeline makes its own. Only FASTQ has a size limit.

Every input then runs the same steps: PyPGx, mitochondrial calling (unless switched off),
PharmCAT with PyPGx's and the other tools' calls as outside calls, and the reports.
Alignments also get HLA typing, and a VCF made from their reads at every PharmCAT position.

## Variant Call Format (VCF)

A VCF usually lists only the sites where the sample differs from the reference, so a
position with no record in it may match the reference or may not have been sequenced at
all. PyPGx reads it as the reference ([pypgx#142](https://github.com/sbslee/pypgx/issues/142)).
So ZaroPGx checks the positions PyPGx calls each gene from (its star alleles' defining
variants). A gene with no genotype at any of them (no record, or only `./.`) is not
called. A gene with a genotype at some is called, with a note saying how many of the
rest were read as the reference. The report shows both. A `./.` is never passed to
PyPGx, which would fill it in from population data.

With "Assume absent sites = reference" on ({doc}`usage`), positions with no record count
as reference and need no note; `./.` positions still do. Turn the option on only for a
whole-genome call set. Even then, *GSTM1* is called present when both its copies are
deleted, since a VCF does not record that deletion.

The VCF ZaroPGx makes from an alignment or a gVCF carries header lines (`##ZaroPGx_…`)
saying which genes and positions had reads. They are read only in the run that wrote them.
An uploaded VCF's are ignored, a VCF ZaroPGx wrote included: a file can be edited or
filtered after export (to `PASS` records, say, which drops the reference calls), and its
header would then claim reads its records no longer show. To have the reads counted, upload
the alignment or the gVCF itself.

### Processing path: VCF
```
VCF → [liftover, if GRCh37] → PyPGx and [mtDNA] → PharmCAT → reports
```

## Binary Call Format (BCF)
BCF is the binary encoding of a VCF. It holds exactly the same variant records, so nothing is lost by converting one — but the analysis tools in the stack decide what a file is from its *name*, so ZaroPGx converts it for real (`bcftools view -O z`, plus a tabix index) before anything else sees it, rather than relabelling it. The conversion runs inside ZaroPGx and fails the job loudly if it produces an empty or malformed VCF.

Because the file that gets analysed is a VCF, every VCF caveat applies to a BCF upload: no HLA typing, degraded accuracy for *CYP2D6*, and degraded accuracy for genes whose phenotypes depend on structural or copy-number variants. A GRCh37/hg19 BCF is converted first and then lifted over to GRCh38, exactly as a GRCh37 VCF is.

A gVCF that has been written as a BCF is routed to the gVCF lane below rather than this one — the `##GVCFBlock` records are read out of the binary header to catch it.

### Processing path: BCF
```
BCF → bcftools (VCF conversion) → [liftover, if GRCh37] → PyPGx and [mtDNA] → PharmCAT → reports
```

## Genomic VCF (gVCF)
A gVCF records *reference-confidence blocks* alongside the variant calls. PharmCAT refuses a gVCF, so ZaroPGx genotypes it first with GATK `GenotypeGVCFs`: over PharmCAT's positions, then over everything else. Reference genotypes at the pharmacogene positions come from your file's reference blocks rather than being assumed, which makes a gVCF a better input than a plain VCF.

PharmCAT's assume-reference checkboxes are not applied to a gVCF. Positions your file does not cover stay no-calls. PyPGx reads the genotyped VCF, which keeps no reference blocks, so the conversion also records which of PyPGx's genes, and which of the positions PyPGx defines their alleles at, the blocks show reads over; a gene with none at its positions is not called, and a call missing some says how many it read as the reference. The conversion measures the genes in ZaroPGx's copy of PyPGx's tables, which leaves out *MT-RNR1* (the mtDNA step calls it); a gene it did not measure is read as an uploaded VCF's, and not called without a genotype at its positions. A position the conversion leaves a no-call is not passed to PyPGx; under a call, it is counted in the call's note.

The report states:

- **How much of PharmCAT's position list your file covered.** Positions without a reference block are no-calls.
- **That genotypes are re-derived** from the recorded likelihoods, and may differ from your caller's. A reference call needs a reference confidence of 20 over at least 7 reads; a variant needs a genotype quality of 20 and a call quality of 30. Anything else is a no-call.
- An indel PharmCAT cannot match stays a no-call, as with a plain VCF.

Refused, with the reason:

- **Non-GATK reference blocks** (`<*>` instead of `<NON_REF>`, as written by DeepVariant, bcftools and some Illumina callers). Genotype the file with your caller's own tool and upload the VCF. Do not filter the reference blocks out by hand: that deletes your variants too.
- **GRCh37/hg19 gVCFs.** Run `gatk GenotypeGVCFs` yourself and upload the GRCh37 VCF, which is lifted over.

Every VCF caveat applies: no HLA typing, and degraded accuracy for *CYP2D6* and for structural and copy-number variants. One sample only.

### Processing path: gVCF
```
gVCF → GATK GenotypeGVCFs ×2 (VCF conversion) → PyPGx and [mtDNA] → PharmCAT → reports
```

## Binary Alignment Map (BAM)

For aligned input (BAM, CRAM, SAM, and FASTQ once aligned), PharmCAT gets a call at each of its positions, made from your reads. Reference needs at least 7 good reads for the reference and next to none for anything else; a variant needs a confident call (genotype quality 20). Anything else is a no-call, so a gene your data did not cover is reported as not called, not as normal. PharmCAT's assume-reference checkboxes are not applied. The report states how many positions were called, had no reads, or were uncertain. PyPGx's genes are judged at the positions PyPGx defines their alleles at, not anywhere in their regions, which overlap (*UGT1A4*'s and *UGT1A6*'s span *UGT1A1*'s first exon): a gene with no reads at any of its positions is not called, and a call missing some says how many it read as the reference. With whole-genome depth, a gene PyPGx counts copies of is left to its copy-number model, which reads no reads as a deletion.

Structural variants (gene deletions, duplications and hybrids, such as *CYP2D6* \*5, \*36 and \*68, or *GSTM1*, *GSTT1* and *UGT2B17* deletions) are called by PyPGx from read depth, normalised against the *VDR* gene, for GRCh38 whole-genome data only: the alignment needs a median depth of at least 15x at a set of loci no pharmacogene panel targets. A targeted panel does not qualify, because depth across capture targets is too uneven to count gene copies from one sample, and its genes get calls from small variants alone, as every VCF, BCF and gVCF's do. The report marks each call PyPGx made this way "Copy number not assessed", with the reason ({doc}`reports`).

GRCh38 also carries alternate copies of some loci as ALT contigs, among them *CYP2D6*, *UGT2B17*/*UGT2B15* and *HLA-A*, *-B* and *-C*. PyPGx reads only the main chromosomes (and GSTT1's ALT contig, `chr22_KI270879v1_alt`; against a GRCh38 without it, GSTT1 is not called and the report says why), so a read whose alignment is on an ALT copy is a read it never sees. An alignment to a GRCh38 with ALT contigs that is not ALT-aware puts many of these genes' reads on the ALT copy as their primary alignment, and many of the rest at mapping quality 0, which PyPGx also ignores. Measured on NA12878's 30x genome: aligned without ALT-awareness, 529 reads over *CYP2D6* stayed on the main chromosome, 48% of them at mapping quality 0, where its published, ALT-aware alignment has 1,570, 5.8% at mapping quality 0; PyPGx would have had 275 usable *CYP2D6* reads instead of 1,479 (*UGT2B15*: 2,460 reads, 67% at mapping quality 0, instead of 5,106, 0.6%). In ALT-aware mode (the reference's `.alt` file beside its index, as NA12878's published 1000 Genomes BAM was made), BWA keeps a read's alignment on the main chromosomes whenever it has a hit there, and writes its hit on the ALT copy only as a supplementary record, a copy PyPGx does not need; only a read with no hit outside the ALT contigs is placed there (NA12878's BAM has 1 such read on these loci's ALT contigs, beside 16,404 supplementary copies). Such a file is normally read as it is.

So when an alignment, ALT-aware or not, has reads on the *CYP2D6* or *UGT2B17*/*UGT2B15* ALT contigs (20 or more whose primary alignment is there; supplementary and secondary records are not counted), ZaroPGx aligns the reads of that locus again, to GRCh38's main chromosomes (BWA-MEM through GATK's BwaSpark, with the index the FASTQ lane uses, then duplicates marked), and PyPGx and PharmCAT's positions read those genes from the new alignments; every other gene is read as uploaded. The report says so under Alerts and Warnings, with each gene's read count and mapping-quality-0 share before and after. It needs the alignment index (`scripts/build-align-index.sh`). Without it, or if the re-alignment fails, the genes are flagged beside their calls and under Alerts and Warnings, with the reason: their calls may be wrong. Without reads on the ALT contigs, a gene is still flagged where more than half its reads had mapping quality 0. Aligning to a GRCh38 without ALT contigs (an analysis set such as NCBI's no-ALT set) avoids all of this, and BWA in ALT-aware mode (the reference's `.alt` file beside its index) nearly all. HLA typing is not affected: OptiType is given every read in the MHC and on its ALT contigs, whatever its mapping quality.

### Processing path: BAM
```
BAM → [HLA typing] → PyPGx (VCF at PharmCAT's positions, then star alleles) and [mtDNA] → PharmCAT → reports
```

## CRAM

A CRAM stores reads as differences from the exact reference sequences it was written against, so it can only be decoded with those sequences. ZaroPGx decodes CRAMs written against:

- UCSC hg38;
- the 1000 Genomes GRCh38 analysis set with decoys and HLA (`GRCh38_full_analysis_set_plus_decoy_hla`);
- NCBI's GRCh38 no-ALT analysis set, whose sequences are all among the two above.

The genome downloader keeps their sequences in a local reference cache (about 5.3 GB) once its other downloads finish, and a CRAM is decoded with whichever sequences it names. A CRAM written against any other reference is refused before conversion, with a message naming the contigs that cannot be decoded. Convert such a file to BAM with its own reference (`samtools view -b -T <that FASTA>`) and upload the BAM, which needs no reference.

### Processing path: CRAM
```
CRAM → samtools (BAM conversion) → then as BAM
```

## SAM

### Processing path: SAM
```
SAM → samtools (BAM conversion) → then as BAM
```

## FASTQ: accepted up to 20 GB

Short-read FASTQ, single-end or an R1/R2 pair uploaded together. ZaroPGx aligns it to GRCh38
with bwa-mem (bundled in GATK), marks duplicates, then runs the BAM path. The two files of a
pair must be mates of one run. The alignment index must have been built first
(`scripts/build-align-index.sh`; see {doc}`installation`).

Upload both files of a paired-end run. R1 alone can miss heterozygous variants in *CYP2B6* and *CYP2D6*.

Refused, with the reason:

- **Long reads** (Oxford Nanopore, PacBio). Align them with minimap2 against GRCh38 and upload the BAM or CRAM.
- **Over 20 GB in total**, unless `FASTQ_MAX_UPLOAD_BYTES` sets another limit ({doc}`configuration`). This covers targeted panels and exomes, not whole genomes. Align whole-genome reads yourself (nf-core/sarek, or `bwa-mem` against GRCh38) and upload the BAM or CRAM.
- **More than the free disk can hold.** Alignment keeps several BAM-sized files at once until it finishes, up to about 9 times the gzipped FASTQ's size in all (measured: 6 times, on 1 GB of reads; an uncompressed FASTQ counts as a third of its size), so the GATK service checks the free disk before it starts and refuses with how much is needed and how much is free.
- **No detectable sequencing platform.** The platform is read from the read names, which SRA re-exports lose. Upload the original run files, or align the reads yourself.

The limit is about disk and time: alignment is slow, and a whole genome would take hours. Whether duplicate marking fits the GATK service's memory at whole-genome scale has not been tested. An aligned BAM or CRAM is much faster, and a GRCh38 VCF is fastest.

## Consumer genotyping arrays (23andMe, AncestryDNA): refused for now

ZaroPGx recognises a 23andMe or AncestryDNA raw-data export and refuses it for now; support is in development. The coordinates in those files are perfectly good — build 37, plus strand, real positions — and turning one into a VCF is a one-line `bcftools convert --tsv2vcf`. What is wrong is what happens next.

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

Upload sequencing data instead, in a format from the table at the top: a VCF (GRCh38, or GRCh37 to be lifted over), BCF or gVCF, a BAM, CRAM or SAM, or FASTQ.

## Reference Genome Support
- **GRCh38/hg38** — analysed directly; the build every result is reported on.
- **GRCh37/hg19 VCF or BCF** (Legacy) — **lifted over to GRCh38 automatically** before analysis, using GATK Picard `LiftoverVcf` with UCSC's hg19→hg38 chain. A real coordinate conversion, not a contig relabelling. Variants that cannot be mapped are dropped and the step reports how many; the run fails if more than half of them cannot be lifted. A BCF is converted to a VCF first, then lifted. A native GRCh38 VCF remains the most reliable input.
- **GRCh37/hg19 gVCF** — **not accepted.** The gVCF lane's value is the reference pass it emits over PharmCAT's own position list, and that list exists in GRCh38 coordinates only. Run `gatk GenotypeGVCFs` on it yourself and upload the resulting GRCh37 VCF, which *is* lifted. See the gVCF section above.
- **GRCh37/hg19 BAM, CRAM or SAM** — **not accepted.** Liftover converts variants that have already been called. Aligned reads are analysed by calling variants out of them first, and that call reads each gene from its GRCh38 position — on GRCh37 reads those positions are wrong (GRCh38's *CYP2D6* window sits roughly 400 kb from GRCh37's), so you would get star alleles that are not yours rather than an error. Call variants against GRCh37/hg19 yourself and upload the VCF, or realign the reads to GRCh38/hg38.
- **T2T-CHM13 (any format)** — **detected and refused for now; support is in development.** ZaroPGx reads the assembly out of the file's own contig lengths, or out of its `##reference=` line, and declines the upload. Nothing downstream would catch a CHM13 file: PharmCAT's preprocessor normalises against GRCh38.p13 without checking which assembly the input is on, and `bcftools norm -c ws` *swaps* a mismatched reference allele rather than failing — so the report would carry confidently wrong star alleles. There is no automatic liftover for it, and doing one yourself is not a workaround either: the published T2T chains exclude GRCh38's alternate haplotype contigs, so *GSTT1* (on `chr22_KI270879v1_alt`) cannot come across at all, only about 60% of T2T's segmental duplications have a clear GRCh38 orthologue — the *CYP2D6*/*CYP2D7*/*CYP2D8* cluster is one such region — and no published work characterises *CYP2D6* or *CYP2C19* in CHM13. Call your variants against GRCh38/hg38, or realign to it, and upload that.
