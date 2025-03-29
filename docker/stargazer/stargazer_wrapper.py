#!/usr/bin/env python3
"""
Stargazer Wrapper Service for ZaroPGx
Provides REST API endpoints for calling CYP2D6 star alleles using Stargazer
"""

import os
import sys
import json
import subprocess
import tempfile
import time
import uuid
import logging
import shutil
from pathlib import Path
from typing import Dict, List, Any, Optional, Union

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import pandas as pd

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("stargazer_wrapper")

# Environment variables and constants
DATA_DIR = os.environ.get('DATA_DIR', '/data')
REFERENCE_DIR = os.environ.get('REFERENCE_DIR', '/reference')
TEMP_DIR = Path('/tmp')
STARGAZER_DIR = Path('/stargazer')

# Initialize Flask app
app = Flask(__name__)
CORS(app)

# Ensure directories exist
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(REFERENCE_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

# Supported genes for Stargazer (focusing on CYP2D6 for this pipeline)
SUPPORTED_GENES = ["CYP2D6"]

# Map from activity scores to phenotypes for CYP2D6
CYP2D6_PHENOTYPES = {
    0: "Poor Metabolizer",
    0.5: "Intermediate Metabolizer",
    1.0: "Normal Metabolizer",
    1.5: "Normal to Rapid Metabolizer",
    2.0: "Rapid Metabolizer",
    ">2.0": "Ultrarapid Metabolizer"
}

@app.route('/health')
def health_check():
    """Health check endpoint to verify service is running"""
    return jsonify({
        'status': 'healthy',
        'service': 'stargazer-wrapper',
        'supported_genes': SUPPORTED_GENES,
        'timestamp': time.time()
    })

@app.route('/supported_genes')
def supported_genes():
    """Return the list of genes supported by this service"""
    return jsonify({
        'status': 'success',
        'genes': SUPPORTED_GENES
    })

@app.route('/genotype', methods=['POST'])
def genotype():
    """
    Run Stargazer on a VCF file to determine CYP2D6 star alleles
    Takes a VCF file as input
    Returns star allele calls, diplotype, and activity score
    """
    if 'file' not in request.files:
        return jsonify({'status': 'error', 'error': 'No file provided'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'status': 'error', 'error': 'No file selected'}), 400
    
    # Get parameters
    gene = request.form.get('gene', 'CYP2D6')
    if gene not in SUPPORTED_GENES:
        return jsonify({
            'status': 'error',
            'error': f"Gene {gene} not supported. Only {SUPPORTED_GENES} are supported."
        }), 400
    
    reference_genome = request.form.get('reference_genome', 'hg19')
    
    # Create a unique job ID
    job_id = str(uuid.uuid4())
    job_dir = os.path.join(TEMP_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)
    
    # Save the input file
    input_filepath = os.path.join(job_dir, file.filename)
    file.save(input_filepath)
    
    try:
        # Process VCF with Stargazer
        logger.info(f"Processing {gene} genotyping with Stargazer")
        result = run_stargazer(input_filepath, job_dir, gene, reference_genome)
        
        if not result['success']:
            return jsonify({
                'status': 'error',
                'error': result['error']
            }), 500
        
        # Process results to prepare the response
        diplotype = result.get('diplotype', 'Unknown')
        allele1, allele2 = parse_diplotype(diplotype)
        
        # Calculate activity score based on function of called alleles
        activity_score = calculate_activity_score(allele1, allele2)
        
        # Map to phenotype
        phenotype = map_activity_to_phenotype(activity_score)
        
        # Create response object
        response = {
            'status': 'success',
            'gene': gene,
            'input_file': file.filename,
            'diplotype': diplotype,
            'alleles': {
                'allele1': allele1,
                'allele2': allele2
            },
            'activity_score': activity_score,
            'phenotype': phenotype,
            'job_id': job_id
        }
        
        # Include detailed results if available
        if 'details' in result:
            response['details'] = result['details']
        
        # Save results to data directory for persistence
        results_file = os.path.join(DATA_DIR, f"{job_id}_{gene}_results.json")
        with open(results_file, 'w') as f:
            json.dump(response, f, indent=2)
        
        return jsonify(response)
    
    except Exception as e:
        logger.exception("Error in genotype")
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500
    finally:
        # Clean up temporary files
        try:
            shutil.rmtree(job_dir)
        except Exception as e:
            logger.warning(f"Failed to clean up temporary directory: {str(e)}")

def run_stargazer(vcf_path: str, output_dir: str, gene: str, reference_genome: str = 'hg19') -> Dict[str, Any]:
    """Run Stargazer for star allele calling on the input VCF"""
    try:
        # Set up command
        stargazer_cmd = f"python3 -m stargazer genotype " \
                       f"--vcf {vcf_path} " \
                       f"--gene {gene} " \
                       f"--genome {reference_genome} " \
                       f"--output {output_dir}/output"
        
        logger.info(f"Running Stargazer command: {stargazer_cmd}")
        
        # Execute Stargazer
        process = subprocess.run(
            stargazer_cmd,
            shell=True,
            capture_output=True,
            text=True
        )
        
        # Check process output
        if process.returncode != 0:
            logger.error(f"Stargazer failed: {process.stderr}")
            return {
                'success': False,
                'error': f"Stargazer failed: {process.stderr}"
            }
        
        # Parse results
        results_file = os.path.join(output_dir, 'output', f"{gene}.report.txt")
        
        if not os.path.exists(results_file):
            return {
                'success': False,
                'error': f"Stargazer did not produce results file: {results_file}"
            }
        
        # Parse the report file to extract diplotype and other information
        diplotype, details = parse_stargazer_report(results_file)
        
        return {
            'success': True,
            'diplotype': diplotype,
            'details': details
        }
    
    except Exception as e:
        logger.exception("Error running Stargazer")
        return {
            'success': False,
            'error': f"Error running Stargazer: {str(e)}"
        }

def parse_stargazer_report(report_file: str) -> tuple:
    """Parse the Stargazer report file to extract diplotype and details"""
    try:
        with open(report_file, 'r') as f:
            report_content = f.read()
        
        # Extract diplotype from report
        diplotype = "Unknown"
        details = {}
        
        # Parse line by line
        lines = report_content.strip().split('\n')
        for line in lines:
            if line.startswith('Genotype:'):
                diplotype = line.split(':', 1)[1].strip()
            
            # Extract other information that might be useful
            if line.startswith('Novel SNVs:'):
                details['novel_snvs'] = line.split(':', 1)[1].strip()
            elif line.startswith('Novel INDELs:'):
                details['novel_indels'] = line.split(':', 1)[1].strip()
            elif line.startswith('Multiallelic SNP genotypes:'):
                details['multiallelic_snps'] = line.split(':', 1)[1].strip()
        
        return diplotype, details
    
    except Exception as e:
        logger.exception(f"Error parsing Stargazer report: {str(e)}")
        return "Unknown", {}

def parse_diplotype(diplotype: str) -> tuple:
    """Parse a diplotype string into its component alleles"""
    try:
        if '/' in diplotype:
            alleles = diplotype.split('/')
            return alleles[0].strip(), alleles[1].strip()
        elif '+' in diplotype:
            alleles = diplotype.split('+')
            return alleles[0].strip(), alleles[1].strip()
        else:
            # For homozygous diplotypes or unknown format
            return diplotype, diplotype
    except:
        return "Unknown", "Unknown"

def calculate_activity_score(allele1: str, allele2: str) -> float:
    """
    Calculate CYP2D6 activity score based on alleles
    This is a simplified version - a real implementation would use a comprehensive lookup table
    """
    # Functional activity scores for common CYP2D6 alleles
    allele_scores = {
        "*1": 1.0,     # Normal function
        "*2": 1.0,     # Normal function
        "*1xN": 2.0,   # Increased function 
        "*2xN": 2.0,   # Increased function
        "*3": 0.0,     # No function
        "*4": 0.0,     # No function
        "*5": 0.0,     # No function (gene deletion)
        "*6": 0.0,     # No function
        "*10": 0.5,    # Decreased function
        "*17": 0.0,    # No function
        "*41": 0.5     # Decreased function
    }
    
    # Default for unknown alleles
    score1 = allele_scores.get(allele1, 0.0)
    score2 = allele_scores.get(allele2, 0.0)
    
    # Handle gene duplications that aren't in our standard table
    if 'xN' in allele1 and allele1 not in allele_scores:
        base_allele = allele1.split('xN')[0]
        base_score = allele_scores.get(base_allele, 0.0)
        score1 = base_score * 2  # Simplified duplication effect
    
    if 'xN' in allele2 and allele2 not in allele_scores:
        base_allele = allele2.split('xN')[0]
        base_score = allele_scores.get(base_allele, 0.0)
        score2 = base_score * 2  # Simplified duplication effect
    
    return score1 + score2

def map_activity_to_phenotype(activity_score: float) -> str:
    """Map CYP2D6 activity score to phenotype"""
    if activity_score == 0:
        return "Poor Metabolizer"
    elif 0 < activity_score < 1.0:
        return "Intermediate Metabolizer"
    elif activity_score == 1.0:
        return "Normal Metabolizer"
    elif 1.0 < activity_score < 2.0:
        return "Normal to Rapid Metabolizer"
    elif activity_score == 2.0:
        return "Rapid Metabolizer"
    elif activity_score > 2.0:
        return "Ultrarapid Metabolizer"
    else:
        return "Unknown"

if __name__ == "__main__":
    # Start the Flask app
    app.run(host='0.0.0.0', port=5000, debug=False) 