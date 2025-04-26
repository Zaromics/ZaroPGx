#!/bin/bash

# Setup script for reference genomes used by GATK and PyPGx
# This script downloads and prepares reference genomes for the ZaroPGx pipeline

set -e

# Create reference directory structure
mkdir -p reference/hg19
mkdir -p reference/hg38
mkdir -p reference/grch37
mkdir -p reference/grch38

echo "Created reference directories"

# Download hg19 (UCSC)
echo "Downloading hg19 reference genome from UCSC..."
wget -c -O reference/hg19/ucsc.hg19.fasta.gz http://hgdownload.cse.ucsc.edu/goldenPath/hg19/bigZips/hg19.fa.gz
gunzip -c reference/hg19/ucsc.hg19.fasta.gz > reference/hg19/ucsc.hg19.fasta

# Download hg38 (UCSC)
echo "Downloading hg38 reference genome from UCSC..."
wget -c -O reference/hg38/Homo_sapiens_assembly38.fasta.gz http://hgdownload.cse.ucsc.edu/goldenPath/hg38/bigZips/hg38.fa.gz
gunzip -c reference/hg38/Homo_sapiens_assembly38.fasta.gz > reference/hg38/Homo_sapiens_assembly38.fasta

# Download GRCh37/hg19 (1000 Genomes Project)
echo "Downloading GRCh37 reference genome from 1000 Genomes Project..."
wget -c -O reference/grch37/human_g1k_v37.fasta.gz ftp://ftp.1000genomes.ebi.ac.uk/vol1/ftp/technical/reference/human_g1k_v37.fasta.gz
gunzip -c reference/grch37/human_g1k_v37.fasta.gz > reference/grch37/human_g1k_v37.fasta

# GRCh38 is the same as hg38, so we'll just create a symlink
echo "Creating symlink for GRCh38..."
ln -sf ../hg38/Homo_sapiens_assembly38.fasta reference/grch38/Homo_sapiens_assembly38.fasta

# Create index files for all references using samtools
echo "Creating index files using samtools..."
for ref in reference/hg19/ucsc.hg19.fasta reference/hg38/Homo_sapiens_assembly38.fasta reference/grch37/human_g1k_v37.fasta; do
  echo "Indexing $ref..."
  samtools faidx $ref
done

# Create GATK-specific index files
echo "Creating GATK dictionary files..."
for ref in reference/hg19/ucsc.hg19.fasta reference/hg38/Homo_sapiens_assembly38.fasta reference/grch37/human_g1k_v37.fasta; do
  echo "Creating dictionary for $ref..."
  gatk CreateSequenceDictionary -R $ref
done

echo "Reference genome setup complete!" 