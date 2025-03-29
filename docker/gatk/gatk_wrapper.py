#!/usr/bin/env python3
"""
GATK Wrapper Service for ZaroPGx
Provides REST API endpoints for running GATK variant calling on genomic data
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
from typing import Dict, List, Any, Optional, Union, Tuple

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import pysam
import pandas as pd
import numpy as np

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("gatk_wrapper")

# Environment variables and constants
DATA_DIR = os.environ.get('DATA_DIR', '/data')
REFERENCE_DIR = os.environ.get('REFERENCE_DIR', '/reference')
MAX_MEMORY = os.environ.get('MAX_MEMORY', '4g')
TEMP_DIR = Path('/tmp')

# Initialize Flask app
app = Flask(__name__)
CORS(app)

# Ensure directories exist
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(REFERENCE_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

# Reference genome paths
REFERENCE_GENOMES = {
    'hg19': f"{REFERENCE_DIR}/hg19/ucsc.hg19.fasta",
    'hg38': f"{REFERENCE_DIR}/hg38/Homo_sapiens_assembly38.fasta",
    'grch37': f"{REFERENCE_DIR}/grch37/human_g1k_v37.fasta",
    'grch38': f"{REFERENCE_DIR}/grch38/Homo_sapiens_assembly38.fasta"
}

# Pharmacogenomic regions of interest (common PGx genes)
PGX_REGIONS = {
    'CYP2D6': {'chr': 'chr22', 'start': 42522500, 'end': 42526883, 'hg19': 'chr22:42522500-42526883', 'hg38': 'chr22:42126499-42130881'},
    'CYP2C19': {'chr': 'chr10', 'start': 96522463, 'end': 96612671, 'hg19': 'chr10:96522463-96612671', 'hg38': 'chr10:94762681-94853205'},
    'CYP2C9': {'chr': 'chr10', 'start': 96698414, 'end': 96749148, 'hg19': 'chr10:96698414-96749148', 'hg38': 'chr10:94938657-94989391'},
    'DPYD': {'chr': 'chr1', 'start': 97543300, 'end': 98386615, 'hg19': 'chr1:97543300-98386615', 'hg38': 'chr1:97058759-97901074'},
    'TPMT': {'chr': 'chr6', 'start': 18128556, 'end': 18155373, 'hg19': 'chr6:18128556-18155373', 'hg38': 'chr6:18128556-18155373'},
    'SLCO1B1': {'chr': 'chr12', 'start': 21329101, 'end': 21392730, 'hg19': 'chr12:21329101-21392730', 'hg38': 'chr12:21176808-21240437'},
    'VKORC1': {'chr': 'chr16', 'start': 31102163, 'end': 31106317, 'hg19': 'chr16:31102163-31106317', 'hg38': 'chr16:31099176-31103330'},
    'CYP3A5': {'chr': 'chr7', 'start': 99245813, 'end': 99277648, 'hg19': 'chr7:99245813-99277648', 'hg38': 'chr7:99648194-99680029'},
    'UGT1A1': {'chr': 'chr2', 'start': 234668880, 'end': 234682917, 'hg19': 'chr2:234668880-234682917', 'hg38': 'chr2:233758780-233772817'}
}

@app.route('/health')
def health_check():
    """Health check endpoint to verify service is running"""
    return jsonify({
        'status': 'healthy',
        'service': 'gatk-wrapper',
        'supported_regions': list(PGX_REGIONS.keys()),
        'available_reference_genomes': list(REFERENCE_GENOMES.keys()),
        'timestamp': time.time()
    })

@app.route('/variant-call', methods=['POST'])
def variant_call():
    """
    Process input files with GATK to call variants in PGx regions
    Accepts BAM/CRAM/SAM/VCF files
    Returns VCF with called variants in specified regions
    """
    if 'file' not in request.files:
        return jsonify({'status': 'error', 'error': 'No file provided'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'status': 'error', 'error': 'No file selected'}), 400
    
    # Get parameters
    reference_genome = request.form.get('reference_genome', 'hg19')
    regions = request.form.get('regions', None)  # Comma-separated list of PGx genes
    output_format = request.form.get('output_format', 'vcf')  # vcf or json
    
    # Create a unique job ID
    job_id = str(uuid.uuid4())
    job_dir = os.path.join(TEMP_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)
    
    # Save the input file
    input_filepath = os.path.join(job_dir, file.filename)
    file.save(input_filepath)
    
    try:
        # Detect file type
        file_type = detect_file_type(input_filepath)
        logger.info(f"Detected file type: {file_type}")
        
        # Check if reference genome is valid
        if reference_genome not in REFERENCE_GENOMES:
            return jsonify({
                'status': 'error', 
                'error': f"Invalid reference genome. Supported: {list(REFERENCE_GENOMES.keys())}"
            }), 400
        
        reference_path = REFERENCE_GENOMES[reference_genome]
        
        # Determine regions to process
        target_regions = []
        if regions:
            gene_list = [gene.strip() for gene in regions.split(',')]
            for gene in gene_list:
                if gene in PGX_REGIONS:
                    target_regions.append(PGX_REGIONS[gene][reference_genome])
                else:
                    return jsonify({
                        'status': 'error',
                        'error': f"Invalid gene: {gene}. Supported: {list(PGX_REGIONS.keys())}"
                    }), 400
        else:
            # Default to all PGx regions
            target_regions = [PGX_REGIONS[gene][reference_genome] for gene in PGX_REGIONS]
        
        # Call variants based on file type
        vcf_result_path = os.path.join(job_dir, "variants.vcf")
        
        if file_type == 'vcf':
            # For VCF files, filter to our regions of interest
            logger.info("Processing VCF file")
            result = filter_vcf_to_regions(input_filepath, vcf_result_path, target_regions, reference_genome)
        else:
            # For BAM/CRAM/SAM, run HaplotypeCaller
            logger.info(f"Running GATK HaplotypeCaller on {file_type} file")
            result = run_haplotype_caller(input_filepath, vcf_result_path, reference_path, target_regions, file_type)
        
        if not result['success']:
            return jsonify({'status': 'error', 'error': result['error']}), 500
        
        # Convert VCF to JSON if requested
        if output_format == 'json':
            json_result_path = os.path.join(job_dir, "variants.json")
            vcf_to_json(vcf_result_path, json_result_path)
            result_path = json_result_path
            mimetype = 'application/json'
        else:
            result_path = vcf_result_path
            mimetype = 'text/plain'
        
        # Save to data directory for persistence
        persistent_path = os.path.join(DATA_DIR, f"{job_id}_variants.{output_format}")
        shutil.copy2(result_path, persistent_path)
        
        # Prepare response
        return send_file(
            result_path,
            mimetype=mimetype,
            as_attachment=True,
            download_name=f"pgx_variants.{output_format}"
        )
        
    except Exception as e:
        logger.exception("Error processing file")
        return jsonify({'status': 'error', 'error': str(e)}), 500
    finally:
        # Clean up temporary files
        try:
            shutil.rmtree(job_dir)
        except Exception as e:
            logger.warning(f"Failed to clean up temporary directory: {str(e)}")

def detect_file_type(filepath: str) -> str:
    """
    Detect the type of genomic file based on extension and file content.
    Returns one of 'vcf', 'bam', 'sam', 'cram', or 'unknown'
    """
    file_ext = os.path.splitext(filepath.lower())[1]
    
    # Check extensions first
    if file_ext in ['.vcf', '.vcf.gz']:
        return 'vcf'
    elif file_ext == '.bam':
        return 'bam'
    elif file_ext == '.sam':
        return 'sam'
    elif file_ext == '.cram':
        return 'cram'
    
    # For files without typical extensions, check file signature (magic bytes)
    try:
        with open(filepath, 'rb') as f:
            header = f.read(8)  # Read first 8 bytes
            
            # BAM files start with "BAM\1"
            if header.startswith(b'BAM\1'):
                return 'bam'
            
            # CRAM files start with "CRAM"
            if header.startswith(b'CRAM'):
                return 'cram'
            
            # Check if it might be a text-based VCF or SAM
            f.seek(0)
            first_line = f.readline().decode('utf-8', errors='ignore')
            if first_line.startswith('##fileformat=VCF'):
                return 'vcf'
            elif first_line.startswith('@HD') or first_line.startswith('@SQ'):
                return 'sam'
    except Exception as e:
        logger.warning(f"Error detecting file type: {str(e)}")
    
    # Default to unknown if we couldn't determine
    return 'unknown'

def filter_vcf_to_regions(input_vcf: str, output_vcf: str, regions: List[str], reference_genome: str) -> Dict[str, Any]:
    """Filter a VCF file to the specified genomic regions"""
    try:
        regions_arg = " ".join([f"-L {region}" for region in regions])
        
        # Use GATK SelectVariants to filter regions
        cmd = f"gatk SelectVariants -V {input_vcf} -O {output_vcf} {regions_arg} --reference {REFERENCE_GENOMES[reference_genome]}"
        
        logger.info(f"Running command: {cmd}")
        process = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        
        if process.returncode != 0:
            logger.error(f"Error filtering VCF: {process.stderr}")
            return {
                'success': False,
                'error': f"Failed to filter VCF: {process.stderr}"
            }
        
        return {'success': True, 'output_file': output_vcf}
    
    except Exception as e:
        logger.exception("Error in filter_vcf_to_regions")
        return {'success': False, 'error': str(e)}

def run_haplotype_caller(input_file: str, output_vcf: str, reference: str, regions: List[str], file_type: str) -> Dict[str, Any]:
    """Run GATK HaplotypeCaller on input file to generate VCF"""
    try:
        # Prepare input based on file type
        if file_type == 'sam':
            # Convert SAM to BAM
            temp_bam = input_file + '.bam'
            cmd_convert = f"samtools view -bS {input_file} > {temp_bam}"
            logger.info(f"Converting SAM to BAM: {cmd_convert}")
            process = subprocess.run(cmd_convert, shell=True, capture_output=True, text=True)
            if process.returncode != 0:
                return {'success': False, 'error': f"Failed to convert SAM to BAM: {process.stderr}"}
            input_file = temp_bam
        
        # Validate the file is properly indexed
        if file_type in ['bam', 'cram']:
            # Check if index exists, create if not
            index_suffix = '.bai' if file_type == 'bam' else '.crai'
            if not os.path.exists(input_file + index_suffix):
                cmd_index = f"samtools index {input_file}"
                logger.info(f"Indexing {file_type.upper()} file: {cmd_index}")
                process = subprocess.run(cmd_index, shell=True, capture_output=True, text=True)
                if process.returncode != 0:
                    return {'success': False, 'error': f"Failed to index {file_type.upper()}: {process.stderr}"}
        
        # Combine regions into a single argument
        regions_arg = " ".join([f"-L {region}" for region in regions])
        
        # Run HaplotypeCaller
        cmd = f"gatk --java-options '-Xmx{MAX_MEMORY}' HaplotypeCaller -I {input_file} -O {output_vcf} -R {reference} {regions_arg}"
        logger.info(f"Running HaplotypeCaller: {cmd}")
        
        process = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        
        if process.returncode != 0:
            logger.error(f"HaplotypeCaller failed: {process.stderr}")
            return {
                'success': False,
                'error': f"HaplotypeCaller failed: {process.stderr}"
            }
        
        return {'success': True, 'output_file': output_vcf}
    
    except Exception as e:
        logger.exception("Error in run_haplotype_caller")
        return {'success': False, 'error': str(e)}

def vcf_to_json(vcf_file: str, json_file: str) -> Dict[str, Any]:
    """Convert VCF file to JSON format"""
    try:
        variants = []
        
        # Use pysam to read the VCF
        vcf = pysam.VariantFile(vcf_file)
        
        for record in vcf.fetch():
            variant = {
                'chrom': record.chrom,
                'pos': record.pos,
                'id': record.id if record.id else None,
                'ref': record.ref,
                'alt': list(record.alts) if record.alts else [],
                'qual': record.qual,
                'filter': list(record.filter.keys()) if record.filter else ["PASS"],
                'info': {key: record.info[key] for key in record.info},
                'format': list(record.format.keys()) if record.format else [],
                'samples': {}
            }
            
            # Add sample information
            for sample in record.samples:
                variant['samples'][sample] = {
                    key: list(record.samples[sample][key]) if isinstance(record.samples[sample][key], tuple) else record.samples[sample][key]
                    for key in record.format
                }
            
            variants.append(variant)
        
        # Write to JSON file
        with open(json_file, 'w') as f:
            json.dump({'variants': variants}, f, indent=2)
        
        return {'success': True, 'output_file': json_file}
    
    except Exception as e:
        logger.exception("Error in vcf_to_json")
        return {'success': False, 'error': str(e)}

if __name__ == "__main__":
    # Start the Flask app
    app.run(host='0.0.0.0', port=5000, debug=False) 