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

# Execute the command to verify it works
echo "Testing PharmCAT pipeline command:"
pharmcat_pipeline --help | head -n 5

echo "Starting Flask app..."
python3 /app/pharmcat.py 