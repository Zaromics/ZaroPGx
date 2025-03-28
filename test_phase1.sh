#!/bin/bash

# Script to test Phase 1 implementation of ZaroPGx
# This script rebuilds the necessary containers and runs tests for the enhanced gene group functionality

set -e  # Exit on any error

echo "==== ZaroPGx Phase 1 Testing ===="
echo "Testing multi-gene analysis and gene grouping functionality"
echo

# Ensure we're in the project root directory
if [ ! -f "docker-compose.yml" ]; then
    echo "Error: This script must be run from the project root directory"
    exit 1
fi

# Make test scripts executable
chmod +x test_aldy_multi_gene.py

# Build and restart the Aldy service
echo "Building and restarting the Aldy service..."
docker compose build aldy
docker compose up -d aldy

echo "Waiting for Aldy service to be ready..."
sleep 10

# Run the database migration to create gene groups schema
echo "Running database migration for gene groups schema..."
docker compose exec db psql -U cpic_user -d cpic_db -f /docker-entrypoint-initdb.d/03_gene_groups_schema.sql

# Check if a sample VCF file exists
SAMPLE_VCF="./data/sample.vcf"
if [ ! -f "$SAMPLE_VCF" ]; then
    echo "Warning: Sample VCF file not found at $SAMPLE_VCF"
    echo "Please provide a path to a valid VCF file for testing:"
    read -p "> " SAMPLE_VCF
    
    if [ ! -f "$SAMPLE_VCF" ]; then
        echo "Error: VCF file not found. Testing cannot continue."
        exit 1
    fi
fi

echo "Using VCF file: $SAMPLE_VCF"

# Test the Aldy service health
echo "Testing Aldy service health..."
./test_aldy_multi_gene.py --url http://localhost:5002 --vcf "$SAMPLE_VCF" --gene CYP2D6

echo
echo "Testing gene group analysis for CYP450 enzymes..."
./test_aldy_multi_gene.py --url http://localhost:5002 --vcf "$SAMPLE_VCF" --group CYP450_Enzymes --output "results_cyp450.json"

echo
echo "Testing multiple genes from different groups..."
./test_aldy_multi_gene.py --url http://localhost:5002 --vcf "$SAMPLE_VCF" --genes "CYP2D6,CYP2C19,UGT1A1,SLCO1B1" --output "results_mixed.json"

echo
echo "==== Phase 1 Testing Complete ===="
echo "Results have been saved to:"
echo "- results_cyp450.json"
echo "- results_mixed.json"
echo
echo "Next Steps:"
echo "1. Verify results in the JSON files"
echo "2. Update report templates to include gene group information"
echo "3. Update the UI to display gene group results"

exit 0 