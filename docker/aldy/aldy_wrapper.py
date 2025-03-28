#!/usr/bin/env python3
"""
Aldy wrapper script for pharmacogenomic gene genotyping from VCF files.
This script provides a RESTful API endpoint to process VCF files
and return genotype calls for multiple supported genes.
"""

import os
import json
import logging
import subprocess
import tempfile
from typing import Dict, Any, Optional, List, Union
from flask import Flask, request, jsonify

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)

# Data directory for VCF files
DATA_DIR = os.environ.get("DATA_DIR", "/data")
os.makedirs(DATA_DIR, exist_ok=True)

# Supported genes grouped by function
SUPPORTED_GENES = {
    "CYP450_Enzymes": [
        "CYP2D6", "CYP2C19", "CYP2C9", "CYP2C8", "CYP2B6",
        "CYP1A2", "CYP2A6", "CYP2E1", "CYP3A4", "CYP3A5", "CYP3A7", "CYP4F2"
    ],
    "Phase_II_Enzymes": [
        "UGT1A1", "NAT1", "NAT2", "TPMT", "DPYD", "NUDT15", "GSTM1", "GSTP1"
    ],
    "Drug_Transporters": [
        "SLCO1B1", "ABCG2"
    ],
    "Drug_Targets": [
        "VKORC1", "CACNA1S", "RYR1", "CFTR"
    ],
    "Other_PGx_Genes": [
        "COMT", "G6PD", "IFNL3", "UGT2B7"
    ]
}

# Flatten list for easy lookup
ALL_SUPPORTED_GENES = [gene for group in SUPPORTED_GENES.values() for gene in group]

def get_gene_group(gene: str) -> str:
    """Get the functional group for a given gene"""
    for group, genes in SUPPORTED_GENES.items():
        if gene in genes:
            return group
    return "Unknown"

def run_aldy(vcf_path: str, gene: str = "CYP2D6") -> Dict[str, Any]:
    """
    Run Aldy genotyping on a VCF file.
    
    Args:
        vcf_path: Path to the VCF file
        gene: Gene to genotype (default: CYP2D6)
        
    Returns:
        Dictionary containing Aldy results
    """
    try:
        if gene not in ALL_SUPPORTED_GENES:
            return {
                "gene": gene,
                "status": "error",
                "error": f"Gene {gene} is not supported by this Aldy wrapper"
            }
            
        logger.info(f"Running Aldy on {vcf_path} for gene {gene}")
        
        # Create a temporary file for output
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as temp:
            output_file = temp.name
        
        # Run Aldy command
        cmd = [
            "aldy", "genotype",
            "--gene", gene,
            "--output", output_file,
            "--json",
            vcf_path
        ]
        
        process = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True
        )
        
        logger.info(f"Aldy stdout: {process.stdout}")
        
        # Read the results JSON file
        with open(output_file, 'r') as f:
            results = json.load(f)
        
        # Clean up temporary file
        os.unlink(output_file)
        
        group = get_gene_group(gene)
        
        return {
            "gene": gene,
            "group": group,
            "results": results,
            "diplotype": extract_diplotype(results, gene),
            "activity_score": extract_activity_score(results),
            "status": "success"
        }
    except subprocess.CalledProcessError as e:
        logger.error(f"Error running Aldy: {e.stderr}")
        return {
            "gene": gene,
            "group": get_gene_group(gene),
            "status": "error",
            "error": str(e),
            "stderr": e.stderr
        }
    except Exception as e:
        logger.error(f"Error processing Aldy results: {str(e)}")
        return {
            "gene": gene,
            "group": get_gene_group(gene),
            "status": "error",
            "error": str(e)
        }

def run_multi_gene_analysis(vcf_path: str, genes: List[str]) -> Dict[str, Any]:
    """
    Run Aldy genotyping for multiple genes.
    
    Args:
        vcf_path: Path to the VCF file
        genes: List of genes to analyze
        
    Returns:
        Dictionary containing results for all genes
    """
    results = {}
    
    for gene in genes:
        gene_result = run_aldy(vcf_path, gene)
        results[gene] = gene_result
    
    return {
        "status": "success",
        "genes": results,
        "groups": SUPPORTED_GENES
    }

def extract_diplotype(results: Dict[str, Any], gene: str) -> Optional[str]:
    """
    Extract the diplotype from Aldy results.
    
    Args:
        results: Aldy results dictionary
        gene: Gene name
        
    Returns:
        Diplotype string or None if not found
    """
    try:
        # Extract diplotype based on Aldy's JSON structure
        solutions = results.get("solutions", [])
        if not solutions:
            return None
        
        # Get the first solution (highest score)
        solution = solutions[0]
        alleles = solution.get("major", {}).get("alleles", [])
        
        if len(alleles) >= 2:
            return f"*{alleles[0]}/*{alleles[1]}"
        elif len(alleles) == 1:
            return f"*{alleles[0]}/*{alleles[0]}"  # Homozygous
        else:
            return None
    except Exception as e:
        logger.error(f"Error extracting diplotype: {str(e)}")
        return None

def extract_activity_score(results: Dict[str, Any]) -> Optional[float]:
    """
    Extract the activity score from Aldy results if available.
    
    Args:
        results: Aldy results dictionary
        
    Returns:
        Activity score or None if not found
    """
    try:
        solutions = results.get("solutions", [])
        if not solutions:
            return None
        
        solution = solutions[0]
        activity = solution.get("scores", {}).get("activity")
        
        return activity
    except Exception as e:
        logger.error(f"Error extracting activity score: {str(e)}")
        return None

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint."""
    return jsonify({
        "status": "healthy",
        "supported_genes": ALL_SUPPORTED_GENES,
        "gene_groups": SUPPORTED_GENES
    })

@app.route('/supported_genes', methods=['GET'])
def get_supported_genes():
    """Return the list of supported genes and their groupings."""
    return jsonify({
        "genes": ALL_SUPPORTED_GENES,
        "groups": SUPPORTED_GENES
    })

@app.route('/genotype', methods=['POST'])
def genotype():
    """
    Process a VCF file and return genotype for a specified gene.
    
    Request should contain:
    - file: VCF file
    - gene: (optional) Gene to genotype (default: CYP2D6)
    """
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400
    
    file = request.files['file']
    if not file.filename:
        return jsonify({"error": "Empty file provided"}), 400
    
    if not file.filename.endswith(('.vcf', '.vcf.gz')):
        return jsonify({"error": "Invalid file format. Must be VCF."}), 400
    
    gene = request.form.get('gene', 'CYP2D6')
    
    # Save the file
    file_path = os.path.join(DATA_DIR, f"{os.path.basename(file.filename)}")
    file.save(file_path)
    
    # Process with Aldy
    results = run_aldy(file_path, gene)
    
    return jsonify(results)

@app.route('/multi_genotype', methods=['POST'])
def multi_genotype():
    """
    Process a VCF file and return genotypes for multiple genes.
    
    Request should contain:
    - file: VCF file
    - genes: (optional) Comma-separated list of genes to genotype
    - group: (optional) Gene group to analyze (e.g., "CYP450_Enzymes")
    """
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400
    
    file = request.files['file']
    if not file.filename:
        return jsonify({"error": "Empty file provided"}), 400
    
    if not file.filename.endswith(('.vcf', '.vcf.gz')):
        return jsonify({"error": "Invalid file format. Must be VCF."}), 400
    
    # Get genes to analyze
    genes_param = request.form.get('genes', '')
    group = request.form.get('group', '')
    
    if genes_param:
        genes = [g.strip() for g in genes_param.split(',')]
        # Filter out any unsupported genes
        genes = [g for g in genes if g in ALL_SUPPORTED_GENES]
    elif group:
        # Get all genes in the specified group
        genes = SUPPORTED_GENES.get(group, [])
    else:
        # Default to CYP2D6 only
        genes = ["CYP2D6"]
    
    # Save the file
    file_path = os.path.join(DATA_DIR, f"{os.path.basename(file.filename)}")
    file.save(file_path)
    
    # Process multiple genes
    results = run_multi_gene_analysis(file_path, genes)
    
    return jsonify(results)

if __name__ == "__main__":
    # Run the Flask application
    app.run(host='0.0.0.0', port=5000) 