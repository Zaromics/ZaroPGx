---
title: Supported File Formats
---

# Supported File Formats

ZaroPGx supports multiple genomic data formats with automatic conversion and processing.

## Variant Call Format (VCF)

### Description
VCF is the standard format for storing genetic variant information. ZaroPGx can process VCF files directly without preprocessing.

### Supported Versions
- **VCF 4.0**: Full support
- **VCF 4.1**: Full support
- **VCF 4.2**: Full support (latest)

### File Extensions
- `.vcf` - Uncompressed VCF
- `.vcf.gz` - Gzip-compressed VCF
- `.vcf.bgz` - Block-gzipped VCF

### Required Fields
- **CHROM**: Chromosome identifier
- **POS**: Position (1-based)
- **ID**: Variant identifier
- **REF**: Reference allele
- **ALT**: Alternate allele(s)
- **QUAL**: Quality score
- **FILTER**: Filter status
- **INFO**: Additional information
- **FORMAT**: Genotype format
- **Sample columns**: Genotype data

### Example VCF Header
```vcf
##fileformat=VCFv4.2
##reference=GRCh38
##contig=<ID=1,length=248956422>
##INFO=<ID=AC,Number=A,Type=Integer,Description="Allele count in genotypes">
##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
#CHROM	POS	ID	REF	ALT	QUAL	FILTER	INFO	FORMAT	SAMPLE
1	12345	rs123456	A	T	100	PASS	AC=1	GT	0/1
```

### Processing Path
```
VCF → Header Analysis → PyPGx → PharmCAT → Reports
```

## Binary Alignment Map (BAM)

### Description
BAM files contain aligned sequencing reads and are commonly used for variant calling and analysis.

### File Extensions
- `.bam` - Binary alignment format
- `.bai` - BAM index file (recommended)

### Processing Path
```
BAM → HLA Typing → PyPGx → PharmCAT → Reports
```

### Requirements
- **Index File**: Include `.bai` file for better performance
- **Reference Genome**: Must match the reference used for alignment
- **Quality Scores**: Should include base quality scores

## Compressed BAM (CRAM)

### Description
CRAM is a compressed version of BAM that uses reference-based compression for smaller file sizes.

### File Extensions
- `.cram` - Compressed BAM format
- `.crai` - CRAM index file (recommended)

### Processing Path
```
CRAM → GATK (BAM conversion) → HLA Typing → PyPGx → PharmCAT → Reports
```

### Requirements
- **Reference Genome**: Required for decompression
- **Index File**: Include `.crai` file for better performance
- **GATK Processing**: Automatically enabled for CRAM files

## Sequence Alignment Map (SAM)

### Description
SAM is the text-based format for aligned sequences, often used as an intermediate format.

### File Extensions
- `.sam` - Text alignment format

### Processing Path
```
SAM → GATK (BAM conversion) → HLA Typing → PyPGx → PharmCAT → Reports
```

### Requirements
- **Reference Genome**: Required for processing
- **GATK Processing**: Automatically enabled for SAM files

## FASTQ Format

### Description
FASTQ files contain raw sequencing reads with quality scores and are the starting point for most genomic analyses.

### File Extensions
- `.fastq` - Uncompressed FASTQ
- `.fastq.gz` - Gzip-compressed FASTQ
- `.fq` - Alternative extension
- `.fq.gz` - Compressed alternative

### Processing Path
```
FASTQ → HLA Typing → GATK (BAM generation) → PyPGx → PharmCAT → Reports
```

### Requirements
- **Paired-end Reads**: Recommended for better accuracy
- **Quality Scores**: Required for variant calling
- **Sufficient Coverage**: Minimum 30x recommended

## Index Files

### BAM Index (.bai)
- **Purpose**: Enables random access to BAM files
- **Generation**: `samtools index input.bam`
- **Benefits**: Faster processing, lower memory usage

### CRAM Index (.crai)
- **Purpose**: Enables random access to CRAM files
- **Generation**: `samtools index input.cram`
- **Benefits**: Faster processing, lower memory usage

### VCF Index (.tbi, .csi)
- **Purpose**: Enables random access to VCF files
- **Generation**: `tabix -p vcf input.vcf.gz`
- **Benefits**: Faster processing, lower memory usage

## Reference Genome Support

### GRCh38/hg38 (Recommended)
- **Full Support**: All tools and features
- **Default Reference**: Used when not specified
- **Best Performance**: Optimized for this reference

### GRCh37/hg19 (Legacy)
- **Supported**: With automatic liftover
- **Liftover Process**: Converts coordinates to hg38
- **Limitations**: Some tools may have reduced accuracy

### Custom References
- **Supported**: With proper configuration
- **Requirements**: Must include all required contigs
- **Configuration**: Set in environment variables

## File Size Considerations

### Typical File Sizes
| Format | Whole Genome | Exome | Targeted Panel |
|--------|--------------|-------|----------------|
| **VCF** | 1-5 GB | 50-200 MB | 1-10 MB |
| **BAM** | 50-100 GB | 2-5 GB | 50-500 MB |
| **CRAM** | 15-30 GB | 500 MB-1 GB | 10-100 MB |
| **FASTQ** | 100-200 GB | 5-10 GB | 100 MB-1 GB |

### Storage Requirements
- **Temporary Files**: 2-3x input file size
- **Reference Data**: 10-50 GB (depending on reference)
- **Reports**: 1-10 MB per analysis
- **Logs**: 10-100 MB per analysis

## Quality Requirements

### Minimum Coverage
- **Whole Genome**: 30x average coverage
- **Exome**: 50x average coverage
- **Targeted Panel**: 100x average coverage

### Quality Scores
- **Base Quality**: Phred score ≥ 20
- **Mapping Quality**: Phred score ≥ 30
- **Variant Quality**: Phred score ≥ 30

### File Integrity
- **Checksums**: Verify file integrity before upload
- **Format Validation**: Ensure files follow standard specifications
- **Index Consistency**: Verify index files match data files

## Upload Guidelines

### File Preparation
1. **Validate Format**: Use standard tools to validate files
2. **Include Indexes**: Provide index files when available
3. **Check Quality**: Verify coverage and quality metrics
4. **Compress When Possible**: Use compressed formats to save space

### Upload Process
1. **Select Files**: Choose all related files (data + index)
2. **Configure Options**: Set appropriate processing options
3. **Monitor Progress**: Watch for errors during upload
4. **Verify Results**: Check that all files were processed correctly

## Troubleshooting File Issues

### Common Problems
- **Format Errors**: Invalid VCF/BAM/SAM format
- **Missing Index**: Performance issues with large files
- **Reference Mismatch**: Coordinates don't match reference
- **Quality Issues**: Low-quality data affecting results

### Solutions
- **Validate Files**: Use standard validation tools
- **Regenerate Indexes**: Create missing index files
- **Check Reference**: Ensure correct reference genome
- **Filter Data**: Remove low-quality variants/reads

## Next Steps

- **Learn about usage**: {doc}`usage`
- **Understand reports**: {doc}`reports`
- **Configure processing**: {doc}`../advanced-configuration`
- **Troubleshoot issues**: {doc}`troubleshooting`
