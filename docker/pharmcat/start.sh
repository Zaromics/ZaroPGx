#!/bin/sh

echo "Starting PharmCAT wrapper..."
echo "Checking Java version:"
java -version
echo "Checking PharmCAT installation:"
which pharmcat_pipeline
echo "Starting Flask app..."
python3 /app/pharmcat_wrapper.py 