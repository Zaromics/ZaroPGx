#!/usr/bin/env python3
"""
Test script for the enhanced Aldy service with multi-gene support.
This script tests the functionality of the Aldy wrapper's multi-gene analysis capabilities.
"""

import os
import sys
import json
import argparse
import requests
from pathlib import Path
from pprint import pprint
from typing import Dict, List, Any, Optional

# Default Aldy service URL
DEFAULT_ALDY_URL = "http://localhost:5002"

def test_health_check(base_url: str) -> bool:
    """Test the Aldy service health endpoint"""
    try:
        response = requests.get(f"{base_url}/health")
        response.raise_for_status()
        data = response.json()
        
        print("Health check results:")
        print(f"Status: {data.get('status', 'unknown')}")
        print(f"Supported genes: {len(data.get('supported_genes', []))} genes")
        print(f"Gene groups: {len(data.get('gene_groups', {}))} groups")
        
        # Print the first few genes from each group
        for group, genes in data.get('gene_groups', {}).items():
            print(f"  {group}: {', '.join(genes[:3])}{'...' if len(genes) > 3 else ''}")
        
        return data.get('status') == 'healthy'
    except Exception as e:
        print(f"Health check failed: {str(e)}")
        return False

def test_supported_genes(base_url: str) -> Dict[str, Any]:
    """Test the supported genes endpoint"""
    try:
        response = requests.get(f"{base_url}/supported_genes")
        response.raise_for_status()
        data = response.json()
        
        print(f"\nService supports {len(data.get('genes', []))} genes across "
              f"{len(data.get('groups', {}))} functional groups")
        
        return data
    except Exception as e:
        print(f"Failed to get supported genes: {str(e)}")
        return {}

def test_single_gene(base_url: str, vcf_path: str, gene: str = "CYP2D6") -> Dict[str, Any]:
    """Test single gene analysis"""
    try:
        if not os.path.exists(vcf_path):
            print(f"Error: VCF file {vcf_path} does not exist")
            return {}
        
        print(f"\nTesting single gene analysis for {gene} with {vcf_path}")
        
        with open(vcf_path, 'rb') as f:
            files = {'file': f}
            data = {'gene': gene}
            response = requests.post(f"{base_url}/genotype", files=files, data=data)
        
        response.raise_for_status()
        result = response.json()
        
        # Extract and display the key information
        status = result.get('status', 'unknown')
        gene_name = result.get('gene', 'unknown')
        group = result.get('group', 'unknown')
        diplotype = result.get('diplotype', 'unknown')
        activity_score = result.get('activity_score', 'unknown')
        
        print(f"Results for {gene_name} (Group: {group}):")
        print(f"Status: {status}")
        print(f"Diplotype: {diplotype}")
        print(f"Activity Score: {activity_score}")
        
        return result
    except Exception as e:
        print(f"Single gene analysis failed: {str(e)}")
        return {}

def test_gene_group(base_url: str, vcf_path: str, group: str = "CYP450_Enzymes") -> Dict[str, Any]:
    """Test gene group analysis"""
    try:
        if not os.path.exists(vcf_path):
            print(f"Error: VCF file {vcf_path} does not exist")
            return {}
        
        print(f"\nTesting gene group analysis for {group} with {vcf_path}")
        
        with open(vcf_path, 'rb') as f:
            files = {'file': f}
            data = {'group': group}
            response = requests.post(f"{base_url}/multi_genotype", files=files, data=data)
        
        response.raise_for_status()
        result = response.json()
        
        # Display a summary of results
        status = result.get('status', 'unknown')
        genes_results = result.get('genes', {})
        
        print(f"Gene group analysis status: {status}")
        print(f"Analyzed {len(genes_results)} genes in group {group}")
        
        # Print summary of each gene result
        for gene, gene_result in genes_results.items():
            diplotype = gene_result.get('diplotype', 'unknown')
            gene_status = gene_result.get('status', 'unknown')
            
            if gene_status == 'success':
                print(f"  {gene}: {diplotype}")
            else:
                error = gene_result.get('error', 'unknown error')
                print(f"  {gene}: ERROR - {error}")
        
        return result
    except Exception as e:
        print(f"Gene group analysis failed: {str(e)}")
        return {}

def test_multiple_genes(base_url: str, vcf_path: str, genes: List[str]) -> Dict[str, Any]:
    """Test analysis of multiple specified genes"""
    try:
        if not os.path.exists(vcf_path):
            print(f"Error: VCF file {vcf_path} does not exist")
            return {}
        
        genes_str = ','.join(genes)
        print(f"\nTesting multiple genes analysis for {genes_str} with {vcf_path}")
        
        with open(vcf_path, 'rb') as f:
            files = {'file': f}
            data = {'genes': genes_str}
            response = requests.post(f"{base_url}/multi_genotype", files=files, data=data)
        
        response.raise_for_status()
        result = response.json()
        
        # Display a summary of results
        status = result.get('status', 'unknown')
        genes_results = result.get('genes', {})
        
        print(f"Multiple genes analysis status: {status}")
        print(f"Analyzed {len(genes_results)} genes")
        
        # Print summary of each gene result
        for gene, gene_result in genes_results.items():
            diplotype = gene_result.get('diplotype', 'unknown')
            gene_status = gene_result.get('status', 'unknown')
            
            if gene_status == 'success':
                print(f"  {gene}: {diplotype}")
            else:
                error = gene_result.get('error', 'unknown error')
                print(f"  {gene}: ERROR - {error}")
        
        return result
    except Exception as e:
        print(f"Multiple genes analysis failed: {str(e)}")
        return {}

def save_results(result: Dict[str, Any], output_path: str) -> None:
    """Save analysis results to a JSON file"""
    try:
        output_file = Path(output_path)
        with open(output_file, 'w') as f:
            json.dump(result, f, indent=2)
        print(f"\nResults saved to {output_path}")
    except Exception as e:
        print(f"Failed to save results: {str(e)}")

def main():
    parser = argparse.ArgumentParser(description="Test the enhanced Aldy service with multi-gene support")
    parser.add_argument("--url", type=str, default=DEFAULT_ALDY_URL, help="Aldy service URL")
    parser.add_argument("--vcf", type=str, required=True, help="Path to a VCF file for testing")
    parser.add_argument("--gene", type=str, help="Single gene to test")
    parser.add_argument("--group", type=str, help="Gene group to test")
    parser.add_argument("--genes", type=str, help="Comma-separated list of genes to test")
    parser.add_argument("--output", type=str, help="Output path to save results as JSON")
    parser.add_argument("--full", action="store_true", help="Run all tests")
    
    args = parser.parse_args()
    
    # Validate arguments
    if not args.full and not any([args.gene, args.group, args.genes]):
        parser.error("Either --gene, --group, --genes, or --full must be specified")
    
    # Test health check
    if not test_health_check(args.url):
        print("Health check failed. Service may not be available.")
        sys.exit(1)
    
    # Get supported genes
    supported_data = test_supported_genes(args.url)
    
    result = {}
    
    # Test single gene if specified or full test
    if args.gene or args.full:
        gene = args.gene if args.gene else "CYP2D6"
        single_result = test_single_gene(args.url, args.vcf, gene)
        result["single_gene"] = single_result
    
    # Test gene group if specified or full test
    if args.group or args.full:
        group = args.group if args.group else "CYP450_Enzymes"
        group_result = test_gene_group(args.url, args.vcf, group)
        result["gene_group"] = group_result
    
    # Test multiple genes if specified or full test
    if args.genes or args.full:
        if args.genes:
            genes = [g.strip() for g in args.genes.split(',')]
        else:
            # For full test, select a few genes from different groups
            genes = ["CYP2D6", "CYP2C19", "UGT1A1", "SLCO1B1"]
        
        multi_result = test_multiple_genes(args.url, args.vcf, genes)
        result["multiple_genes"] = multi_result
    
    # Save results if output path is specified
    if args.output:
        save_results(result, args.output)

if __name__ == "__main__":
    main() 