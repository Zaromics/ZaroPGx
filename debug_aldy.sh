#!/bin/bash
# Debug script for running Aldy directly in the container

set -e  # Exit on any error

# Default values
SAMPLE_VCF="./data/sample.vcf.gz"  # Default to compressed file
GENE="CYP2D6"
SAMPLE_NAME=""
SEQ_PROFILE="illumina"
GENOME=""  # Will try to auto-detect

# Parse command line arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --vcf)
      SAMPLE_VCF="$2"
      shift 2
      ;;
    --gene)
      GENE="$2"
      shift 2
      ;;
    --sample)
      SAMPLE_NAME="$2"
      shift 2
      ;;
    --profile)
      SEQ_PROFILE="$2"
      shift 2
      ;;
    --genome)
      GENOME="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
done

# Check if sample VCF exists
if [ ! -f "$SAMPLE_VCF" ]; then
  echo "Error: VCF file not found at $SAMPLE_VCF"
  exit 1
fi

# Auto-detect reference genome if not specified
if [ -z "$GENOME" ]; then
  echo "Trying to detect reference genome from VCF header..."
  
  # Extract header from the VCF file
  VCF_HEADER=$(zcat "$SAMPLE_VCF" 2>/dev/null | grep -E "^##" | head -n 200 || cat "$SAMPLE_VCF" | grep -E "^##" | head -n 200)
  
  # Try to identify reference genome from header
  if echo "$VCF_HEADER" | grep -q -E "GRCh38|hg38|GCA_000001405.15"; then
    GENOME="hg38"
    echo "Detected genome: $GENOME (from VCF header)"
  elif echo "$VCF_HEADER" | grep -q -E "GRCh37|hg19|GCA_000001405.1[^5]"; then
    GENOME="hg19"
    echo "Detected genome: $GENOME (from VCF header)"
  else
    # Default to hg38 if detection fails
    GENOME="hg38"
    echo "Could not auto-detect genome, defaulting to: $GENOME"
  fi
fi

# Get sample name if not provided
if [ -z "$SAMPLE_NAME" ]; then
  if [ -x "$(command -v bcftools)" ]; then
    SAMPLE_NAME=$(bcftools query -l "$SAMPLE_VCF" | head -n 1)
    echo "Detected sample name: $SAMPLE_NAME"
  else
    echo "bcftools not found. Please install bcftools:"
    echo "  In Ubuntu/Debian: sudo apt install bcftools"
    echo "  In CentOS/RHEL: sudo yum install bcftools"
    echo "Or specify the sample name explicitly with --sample"
    exit 1
  fi
fi

echo "Running Aldy in container with the following parameters:"
echo "  VCF: $SAMPLE_VCF"
echo "  Gene: $GENE"
echo "  Sample: $SAMPLE_NAME (used with --profile)"
echo "  Sequencing Profile: $SEQ_PROFILE"
echo "  Genome: $GENOME"

# Create output directory
echo "Setting up container environment..."
docker compose exec aldy mkdir -p /data

# Copy VCF file to container
echo "Copying compressed VCF file to container..."
DEST_FILENAME="test.vcf.gz"
docker compose cp "$SAMPLE_VCF" aldy:/data/$DEST_FILENAME

# Check if index file exists and copy it too
INDEX_FILE="${SAMPLE_VCF}.tbi"
if [ -f "$INDEX_FILE" ]; then
    echo "Found index file, copying it to container..."
    docker compose cp "$INDEX_FILE" aldy:/data/$DEST_FILENAME.tbi
else
    INDEX_FILE="${SAMPLE_VCF}.csi"
    if [ -f "$INDEX_FILE" ]; then
        echo "Found CSI index file, copying it to container..."
        docker compose cp "$INDEX_FILE" aldy:/data/$DEST_FILENAME.csi
    else
        echo "No index file found for compressed VCF. Will create one..."
        docker compose exec aldy apt-get update && docker compose exec aldy apt-get install -y tabix
        docker compose exec aldy tabix -f -p vcf /data/$DEST_FILENAME
    fi
fi

# List files in the data directory to verify
echo "Files in container data directory:"
docker compose exec aldy ls -la /data

# Run Aldy directly in the container
echo "Running Aldy in container..."
docker compose exec aldy aldy genotype -g "$GENE" -p "$SEQ_PROFILE" --profile "$SAMPLE_NAME" --genome "$GENOME" -o /data/test.json --log DEBUG /data/$DEST_FILENAME

# Check if output file was created
echo "Checking output file..."
docker compose exec aldy test -f /data/test.json && echo "✅ Output file created" || echo "❌ Output file not created"

# Display output file contents
echo "Output file contents:"
docker compose exec aldy cat /data/test.json || echo "No output file was created or it's empty"

echo "Done debugging Aldy" 