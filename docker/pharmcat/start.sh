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

# Reference genome cache lives on a named volume that must NOT mask /pharmcat
# (masking /pharmcat freezes the pipeline binary across image upgrades).
# Seed /pharmcat from the cache; persist new downloads back into the cache.
REF_CACHE="${PHARMCAT_REF_CACHE:-/pharmcat-references}"
mkdir -p "$REF_CACHE"

_seed_ref_from_cache() {
    for f in reference.fna.bgz reference.fna.bgz.gzi reference.fna.bgz.fai; do
        if [ -e "$REF_CACHE/$f" ] && [ ! -e "/pharmcat/$f" ]; then
            ln -sfn "$REF_CACHE/$f" "/pharmcat/$f"
            echo "✓ Linked $f from $REF_CACHE"
        fi
    done
}

_persist_ref_to_cache() {
    for f in reference.fna.bgz reference.fna.bgz.gzi reference.fna.bgz.fai; do
        # Only persist real files downloaded into the image layer, not existing symlinks.
        if [ -f "/pharmcat/$f" ] && [ ! -L "/pharmcat/$f" ]; then
            if [ ! -f "$REF_CACHE/$f" ]; then
                mv "/pharmcat/$f" "$REF_CACHE/$f"
                ln -sfn "$REF_CACHE/$f" "/pharmcat/$f"
                echo "✓ Persisted $f into $REF_CACHE"
            fi
        fi
    done
}

echo "Setting up reference genome files..."
_seed_ref_from_cache

# Check if reference genome files are available in /pharmcat/ where PharmCAT expects them
if [ -f "/pharmcat/reference.fna.bgz" ]; then
    echo "✓ Reference genome found in /pharmcat/"
else
    echo "⚠ Warning: reference.fna.bgz not found in /pharmcat/"
fi

if [ -f "/pharmcat/reference.fna.bgz.gzi" ]; then
    echo "✓ Reference genome index found in /pharmcat/"
else
    echo "⚠ Warning: reference.fna.bgz.gzi not found in /pharmcat/"
fi

# Verify the files are in place
echo "Verifying reference files in /pharmcat/:"
ls -la /pharmcat/reference.fna.bgz* 2>/dev/null || echo "No reference files found in /pharmcat/"

# If reference files are still missing, try to download them into the cache, then link.
if [ ! -f "/pharmcat/reference.fna.bgz" ] || [ ! -f "/pharmcat/reference.fna.bgz.gzi" ]; then
    echo "Reference files missing, attempting to download..."
    cd "$REF_CACHE"
    if command -v wget >/dev/null 2>&1; then
        echo "Downloading reference files using wget..."
        wget -O GRCh38_reference_fasta.tar "https://zenodo.org/record/7288118/files/GRCh38_reference_fasta.tar"
        if [ -f "GRCh38_reference_fasta.tar" ]; then
            echo "Extracting reference files..."
            tar -xf GRCh38_reference_fasta.tar
            rm GRCh38_reference_fasta.tar
            # Validate the extracted files
            if [ -f "reference.fna.bgz" ] && [ -f "reference.fna.bgz.gzi" ]; then
                echo "✓ Reference files downloaded and extracted successfully"
                # Test gzip integrity
                if gzip -t reference.fna.bgz 2>/dev/null; then
                    echo "✓ Reference file integrity verified"
                else
                    echo "⚠ Warning: Reference file appears corrupted, removing..."
                    rm -f reference.fna.bgz reference.fna.bgz.gzi reference.fna.bgz.fai
                fi
            else
                echo "⚠ Warning: Reference files not found after extraction"
            fi
        else
            echo "⚠ Warning: Failed to download reference files"
        fi
    elif command -v curl >/dev/null 2>&1; then
        echo "Downloading reference files using curl..."
        curl -L -o GRCh38_reference_fasta.tar "https://zenodo.org/record/7288118/files/GRCh38_reference_fasta.tar"
        if [ -f "GRCh38_reference_fasta.tar" ]; then
            echo "Extracting reference files..."
            tar -xf GRCh38_reference_fasta.tar
            rm GRCh38_reference_fasta.tar
            # Validate the extracted files
            if [ -f "reference.fna.bgz" ] && [ -f "reference.fna.bgz.gzi" ]; then
                echo "✓ Reference files downloaded and extracted successfully"
                # Test gzip integrity
                if gzip -t reference.fna.bgz 2>/dev/null; then
                    echo "✓ Reference file integrity verified"
                else
                    echo "⚠ Warning: Reference file appears corrupted, removing..."
                    rm -f reference.fna.bgz reference.fna.bgz.gzi reference.fna.bgz.fai
                fi
            else
                echo "⚠ Warning: Reference files not found after extraction"
            fi
        else
            echo "⚠ Warning: Failed to download reference files"
        fi
    else
        echo "⚠ Warning: Neither wget nor curl available for downloading reference files"
    fi
    cd /pharmcat
    _seed_ref_from_cache
fi

_persist_ref_to_cache

# Final verification that reference files are in place
echo "Final verification of reference files..."
if [ -f "/pharmcat/reference.fna.bgz" ] && [ -f "/pharmcat/reference.fna.bgz.gzi" ]; then
    echo "✓ Reference files are ready in /pharmcat/"
    ls -la /pharmcat/reference.fna.bgz*
else
    echo "⚠ Warning: Reference files are still missing after all attempts"
fi

# Execute the command to verify it works
echo "Testing PharmCAT pipeline command:"
pharmcat_pipeline --help | head -n 5

# Write version manifest (runtime stamps; not git-tracked — see .gitignore)
mkdir -p /data/versions
PC_VER=${PHARMCAT_VERSION}
if [ -z "$PC_VER" ]; then
  # Try to parse from pharmcat_pipeline --version output
  PC_VER=$(pharmcat_pipeline --version 2>/dev/null | head -n1 | sed 's/[^0-9.]*\([0-9][0-9.]*\).*/\1/')
fi
echo "{\"name\":\"PharmCAT\",\"version\":\"${PC_VER:-unknown}\"}" > /data/versions/pharmcat.json

echo "Starting FastAPI app..."
python3 /app/pharmcat.py
