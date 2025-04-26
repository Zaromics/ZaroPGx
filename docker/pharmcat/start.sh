#!/bin/sh

echo "Starting PharmCAT service..."
echo "Checking Java version:"
java -version

echo "Checking PharmCAT installation:"
# Check for the pharmcat script (from the pipeline distribution)
command -v pharmcat
if [ $? -ne 0 ]; then
    echo "WARNING: pharmcat command not found!"
    echo "Looking for pharmcat_pipeline command..."
    command -v pharmcat_pipeline
    if [ $? -ne 0 ]; then
        echo "ERROR: pharmcat_pipeline command not found!"
        echo "Checking pipeline directory..."
        ls -la /pharmcat/pipeline/
    fi
fi

# Check for bcftools and bgzip
echo "Checking required tools:"
echo "bcftools: $(which bcftools)"
echo "bgzip: $(which bgzip)"

# Execute the command to verify it works
echo "Testing PharmCAT pipeline command:"
pharmcat --help | head -n 5

echo "Starting Flask app..."
python3 /app/pharmcat.py 