#!/usr/bin/env python
"""
Test script for the PharmCAT TSV parser implementation

This script tests the TSV parser with the example files provided:
- pharmcat.example.report.tsv
- pharmcat.example2.report.tsv
"""

import os
import sys
import json
from pathlib import Path

# Add the app directory to the path so we can import the pharmcat client
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

# Import the pharmcat client
from app.pharmcat.pharmcat_client import parse_pharmcat_tsv_report, extract_drug_recommendations_from_phenotype

def test_tsv_parser():
    """Test the TSV parser with the example files"""
    # Get the path to the example files
    example_tsv = os.path.join(os.path.dirname(__file__), '..', 'pharmcat.example.report.tsv')
    example2_tsv = os.path.join(os.path.dirname(__file__), '..', 'pharmcat.example2.report.tsv')
    
    # Check if the files exist
    if not os.path.exists(example_tsv):
        print(f"Error: Example file {example_tsv} not found")
        return
        
    if not os.path.exists(example2_tsv):
        print(f"Error: Example file {example2_tsv} not found")
        return
    
    # Read the example files
    with open(example_tsv, 'r') as f:
        example_content = f.read()
        
    with open(example2_tsv, 'r') as f:
        example2_content = f.read()
    
    # Mock phenotype data for testing
    mock_phenotype = {
        "phenotypes": {
            "CYP2D6": {
                "diplotype": "*1/*3",
                "phenotype": "Intermediate Metabolizer",
                "activityScore": 1.0,
                "drugRecommendations": [
                    {
                        "drug": {"name": "codeine"},
                        "drugId": "N02AA59",
                        "guidelineName": "CPIC",
                        "recommendationText": "Use label recommended age- or weight-specific dosing.",
                        "classification": "Strong"
                    }
                ]
            },
            "CYP2C19": {
                "diplotype": "*2/*2",
                "phenotype": "Poor Metabolizer",
                "drugRecommendations": [
                    {
                        "drug": {"name": "clopidogrel"},
                        "drugId": "B01AC04",
                        "guidelineName": "CPIC",
                        "recommendationText": "Consider alternative antiplatelet therapy.",
                        "classification": "Strong"
                    }
                ]
            }
        }
    }
    
    # Parse the example files
    print("Testing parse_pharmcat_tsv_report with example.report.tsv...")
    example_result = parse_pharmcat_tsv_report(example_content, mock_phenotype)
    
    print("Testing parse_pharmcat_tsv_report with example2.report.tsv...")
    example2_result = parse_pharmcat_tsv_report(example2_content, mock_phenotype)
    
    # Print the results
    print(f"\nExample 1 Results: {len(example_result['genes'])} genes, {len(example_result['drugRecommendations'])} drug recommendations")
    print(json.dumps(example_result, indent=2))
    
    print(f"\nExample 2 Results: {len(example2_result['genes'])} genes, {len(example2_result['drugRecommendations'])} drug recommendations")
    print(json.dumps(example2_result, indent=2))
    
    # Verify key genes
    print("\nVerifying key genes in Example 1:")
    verify_gene(example_result, "CYP2D6", "*1/*3", "Intermediate Metabolizer", 1.0)
    verify_gene(example_result, "CYP2C19", "*38/*38", "Normal Metabolizer", None)
    
    print("\nVerifying key genes in Example 2:")
    verify_gene(example2_result, "CYP2D6", None, None, None)  # CYP2D6 not present in example2
    verify_gene(example2_result, "CYP2C19", "*2/*2", "Poor Metabolizer", None)
    
    print("\nTest completed successfully!")
    
def verify_gene(result, gene_id, expected_diplotype, expected_phenotype, expected_activity_score):
    """Verify that a gene has the expected values"""
    # Find the gene in the result
    gene = next((g for g in result["genes"] if g["gene"] == gene_id), None)
    
    if gene is None:
        print(f"  {gene_id}: Not found")
        return
        
    # Check if values match expected
    diplotype_match = expected_diplotype is None or gene["diplotype"] == expected_diplotype
    phenotype_match = expected_phenotype is None or gene["phenotype"] == expected_phenotype
    activity_score_match = expected_activity_score is None or gene["activity_score"] == expected_activity_score
    
    # Print the result
    if diplotype_match and phenotype_match and activity_score_match:
        print(f"  {gene_id}: ✓ Matches expected values - {gene['diplotype']}, {gene['phenotype']}, {gene['activity_score']}")
    else:
        print(f"  {gene_id}: ✗ Does not match expected values")
        print(f"    Expected: {expected_diplotype}, {expected_phenotype}, {expected_activity_score}")
        print(f"    Actual: {gene['diplotype']}, {gene['phenotype']}, {gene['activity_score']}")

if __name__ == "__main__":
    print("Testing PharmCAT TSV parser...")
    test_tsv_parser() 