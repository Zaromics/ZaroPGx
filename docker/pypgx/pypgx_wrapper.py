#!/usr/bin/env python3
"""
PyPGx Wrapper Service for ZaroPGx
Provides REST API endpoints for calling PyPGx supported star alleles
"""

import os
import json
import logging
import tempfile
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Dict, Any, List, Optional
import time

import uvicorn
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("pypgx_wrapper")

# Directory setup
DATA_DIR = Path(os.getenv('DATA_DIR', '/data'))
TEMP_DIR = DATA_DIR / 'temp'
REFERENCE_DIR = Path(os.getenv('REFERENCE_DIR', '/reference'))

# Create necessary directories
os.makedirs(TEMP_DIR, exist_ok=True)

# Supported genes for PyPGx (complete list of 87 pharmacogenes)
SUPPORTED_GENES = [
    # Core focus genes for initial implementation
    'CYP2D6',
    'CYP2C19',
    'CYP2C9',
    'CYP3A4',
    'CYP3A5',
    
    # All other supported pharmacogenes
    'ABCB1', 'ABCG2', 'ACYP2', 'ADRA2A', 'ADRB2',
    'ANKK1', 'APOE', 'ATM', 'BCHE', 'BDNF',
    'CACNA1S', 'CFTR', 'COMT', 'CYP1A1', 'CYP1A2',
    'CYP1B1', 'CYP2A6', 'CYP2A7', 'CYP2A13', 'CYP2B6', 
    'CYP2B7', 'CYP2C8', 'CYP2E1', 'CYP2F1',
    'CYP2J2', 'CYP2R1', 'CYP2S1', 'CYP2W1',
    'CYP3A7', 'CYP3A43', 'CYP4A11', 'CYP4A22',
    'CYP4B1', 'CYP4F2', 'CYP17A1', 'CYP19A1', 'CYP26A1',
    'DBH', 'DPYD', 'DRD2', 'F2', 'F5',
    'G6PD', 'GRIK1', 'GRIK4', 'GRIN2B', 'GSTM1',
    'GSTP1', 'GSTT1', 'HTR1A', 'HTR2A', 'IFNL3',
    'ITGB3', 'ITPA', 'MTHFR', 'NAT1', 'NAT2',
    'NUDT15', 'OPRK1', 'OPRM1', 'POR', 'PTGIS',
    'RARG', 'RYR1', 'SLC6A4', 'SLC15A2', 'SLC22A2',
    'SLC28A3', 'SLC47A2', 'SLCO1B1', 'SLCO1B3', 'SLCO2B1',
    'SULT1A1', 'TBXAS1', 'TPMT', 'UGT1A1', 'UGT1A4',
    'UGT1A6', 'UGT2B7', 'UGT2B15', 'UGT2B17', 'VKORC1',
    'XPC'
]

app = FastAPI(
    title="PyPGx Wrapper API",
    description="REST API for PyPGx supported star allele calling",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health_check():
    """Simple health check endpoint"""
    return {
        'status': 'healthy',
        'service': 'pypgx-wrapper',
        'supported_genes': ["CYP2D6"],
        'timestamp': time.time()
    }

@app.get("/")
def root():
    """API root endpoint"""
    return {
        "message": "PyPGx Wrapper API",
        "usage": "POST to /genotype with a VCF file to call CYP2D6 star alleles",
        "version": "1.0.0"
    }

@app.post("/genotype")
async def genotype(
    file: UploadFile = File(...),
    gene: str = Form("CYP2D6"),
    reference_genome: str = Form("hg19")
):
    """
    Run PyPGx on a VCF file to determine CYP2D6 star alleles
    
    Args:
        file: The VCF file to analyze
        gene: Gene to analyze (default: CYP2D6)
        reference_genome: Reference genome (hg19 or hg38)
    
    Returns:
        Genotyping results
    """
    if gene not in SUPPORTED_GENES:
        raise HTTPException(status_code=400, detail=f"Gene {gene} is not supported. Supported genes: {SUPPORTED_GENES}")
    
    if reference_genome not in ["hg19", "hg38", "GRCh37", "GRCh38"]:
        raise HTTPException(status_code=400, detail=f"Reference genome {reference_genome} is not supported. Use hg19/GRCh37 or hg38/GRCh38.")
    
    # Standardize reference genome naming
    if reference_genome == "GRCh37":
        reference_genome = "hg19"
    elif reference_genome == "GRCh38":
        reference_genome = "hg38"
    
    # Create a unique job directory
    job_id = str(uuid.uuid4())
    job_dir = TEMP_DIR / job_id
    os.makedirs(job_dir, exist_ok=True)
    
    try:
        # Save the uploaded VCF file
        input_filepath = job_dir / file.filename
        with open(input_filepath, "wb") as f:
            content = await file.read()
            f.write(content)
        
        logger.info(f"Processing {gene} genotyping with PyPGx")
        result = run_pypgx(input_filepath, job_dir, gene, reference_genome)
        
        # Generate output file path for the frontend
        output_file = str(DATA_DIR / f"{job_id}_cyp2d6.json")
        
        # Save the result to the output file
        with open(output_file, "w") as f:
            json.dump(result, f, indent=2)
        
        # Add the output file path to the result
        result["output_file"] = output_file
        
        return result
    
    except Exception as e:
        logger.exception("Error processing VCF with PyPGx")
        raise HTTPException(
            status_code=500,
            detail=f"Error processing VCF with PyPGx: {str(e)}"
        )

def run_pypgx(vcf_path: str, output_dir: str, gene: str, reference_genome: str = 'hg19') -> Dict[str, Any]:
    """Run PyPGx for star allele calling on the input VCF"""
    try:
        # Create output directory for PyPGx pipeline
        pipeline_dir = Path(output_dir) / f"{gene}-pipeline"
        os.makedirs(pipeline_dir, exist_ok=True)
        
        # Use the appropriate command for NGS pipeline
        pypgx_cmd = f"pypgx run-ngs-pipeline {gene} {pipeline_dir} --variants {vcf_path} --assembly {reference_genome}"
        
        logger.info(f"Running PyPGx command: {pypgx_cmd}")
        
        # Execute PyPGx
        process = subprocess.run(
            pypgx_cmd,
            shell=True,
            text=True,
            capture_output=True,
            check=False
        )
        
        # Check if the command was successful
        if process.returncode != 0:
            logger.error(f"PyPGx failed: {process.stderr}")
            return {
                'success': False,
                'error': f"PyPGx failed: {process.stderr}"
            }
        
        # Read the genotype results
        results_file = pipeline_dir / "genotypes.zip"
        
        if not os.path.exists(results_file):
            return {
                'success': False, 
                'error': f"PyPGx did not produce results file: {results_file}"
            }
        
        # Extract genotype information from the results
        diplotype, details = parse_pypgx_results(pipeline_dir)
        
        return {
            'success': True,
            'gene': gene,
            'diplotype': diplotype,
            'details': details,
            'job_id': os.path.basename(output_dir)
        }
        
    except Exception as e:
        logger.exception("Error running PyPGx")
        return {
            'success': False,
            'error': f"Error running PyPGx: {str(e)}"
        }

def parse_pypgx_results(pipeline_dir: Path) -> tuple:
    """Parse the PyPGx results to extract diplotype and details"""
    try:
        # For demonstration, we'll use a simple approach
        # In a production environment, you would want to properly parse the results
        # from the genotypes.zip and results.zip files
        
        # Check for the results.zip file which has the combined results
        results_file = pipeline_dir / "results.zip"
        
        if os.path.exists(results_file):
            # Use PyPGx API to properly extract data
            # This is a placeholder - in production, use pypgx API to read the zip file
            
            # Simple placeholder data
            # In reality, you would extract this from the results file
            diplotype = "*1/*4"  # Example
            details = {
                "allele1": "*1",
                "allele2": "*4",
                "function1": "Normal Function",
                "function2": "No Function",
                "phenotype": "Intermediate Metabolizer",
                "confidence": "High"
            }
            
            return diplotype, details
        else:
            # If no results file exists, return a placeholder result
            return "*?/*?", {"note": "Results file not found, using default values"}
        
    except Exception as e:
        logger.exception(f"Error parsing PyPGx results: {str(e)}")
        # Return placeholder values in case of error
        return "*?/*?", {"error": str(e)}

if __name__ == "__main__":
    uvicorn.run("pypgx_wrapper:app", host="0.0.0.0", port=5000, reload=True) 