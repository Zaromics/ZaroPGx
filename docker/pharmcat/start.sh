#!/bin/sh

echo "Starting PharmCAT service..."
echo "Checking Java version:"
java -version

echo "Checking PharmCAT installation:"
# Check for pharmcat_pipeline (the correct command)
PHARMCAT_PATH=$(which pharmcat_pipeline 2>/dev/null)
if [ -z "$PHARMCAT_PATH" ]; then
        echo "ERROR: pharmcat_pipeline command not found!"
    echo "Checking pipeline directory contents:"
        ls -la /pharmcat/pipeline/
else
    echo "pharmcat_pipeline found at: $PHARMCAT_PATH"
    echo "Getting PharmCAT version:"
    pharmcat_pipeline --version
fi

# Check for bcftools and bgzip
echo "Checking required tools:"
echo "bcftools: $(which bcftools)"
echo "bgzip: $(which bgzip)"

# Copy reference genome files to pipeline directory where PharmCAT expects them
echo "Setting up reference genome files..."
if [ -f "/pharmcat/reference.fna.bgz" ]; then
    cp /pharmcat/reference.fna.bgz /pharmcat/pipeline/
    echo "✓ Reference genome copied to pipeline directory"
else
    echo "⚠ Warning: reference.fna.bgz not found"
fi

if [ -f "/pharmcat/reference.fna.bgz.gzi" ]; then
    cp /pharmcat/reference.fna.bgz.gzi /pharmcat/pipeline/
    echo "✓ Reference genome index copied to pipeline directory"
else
    echo "⚠ Warning: reference.fna.bgz.gzi not found"
fi

# Verify the files are in place
echo "Verifying reference files in pipeline directory:"
ls -la /pharmcat/pipeline/reference.fna.bgz* 2>/dev/null || echo "No reference files found in pipeline directory"

# Execute the command to verify it works
echo "Testing PharmCAT pipeline command:"
pharmcat_pipeline --help | head -n 5

# Write version manifest
mkdir -p /data/versions
PC_VER=${PHARMCAT_VERSION}
if [ -z "$PC_VER" ]; then
  # Try to parse from pharmcat_pipeline --version output
  PC_VER=$(pharmcat_pipeline --version 2>/dev/null | head -n1 | sed 's/[^0-9.]*\([0-9][0-9.]*\).*/\1/')
fi
echo "{\"name\":\"PharmCAT\",\"version\":\"${PC_VER:-unknown}\"}" > /data/versions/pharmcat.json

echo "Starting Flask app..."
python3 /app/pharmcat.py 