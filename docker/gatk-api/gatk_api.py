#!/usr/bin/env python3
"""
GATK API wrapper service. This provides an HTTP API that makes appropriate
calls to the GATK container.
"""

import os
import json
import logging
import tempfile
import subprocess
import requests
from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configuration
GATK_CONTAINER = os.environ.get('GATK_CONTAINER', 'gatk')
DATA_DIR = os.environ.get('DATA_DIR', '/data')
TEMP_DIR = os.environ.get('TMPDIR', '/tmp/gatk_temp')
REFERENCE_DIR = os.environ.get('REFERENCE_DIR', '/reference')

# Create directories
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(os.path.join(DATA_DIR, 'uploads'), exist_ok=True)
os.makedirs(os.path.join(DATA_DIR, 'results'), exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

# Map reference genome names to file paths
REFERENCE_PATHS = {
    'hg19': os.path.join(REFERENCE_DIR, 'hg19', 'ucsc.hg19.fasta'),
    'hg38': os.path.join(REFERENCE_DIR, 'hg38', 'Homo_sapiens_assembly38.fasta'),
    'grch37': os.path.join(REFERENCE_DIR, 'grch37', 'human_g1k_v37.fasta'),
    'grch38': os.path.join(REFERENCE_DIR, 'hg38', 'Homo_sapiens_assembly38.fasta')  # symlink
}

def index_bam_file(bam_path):
    """
    Create an index for a BAM file using samtools.
    
    Args:
        bam_path: Path to the BAM file to index
        
    Returns:
        tuple: (success, message)
    """
    try:
        logger.info(f"Indexing BAM file: {bam_path}")
        
        # Check if samtools is installed
        try:
            subprocess.run(["samtools", "--version"], capture_output=True, check=True)
        except (subprocess.SubprocessError, FileNotFoundError):
            logger.error("samtools not found. Installing...")
            subprocess.run("apt-get update && apt-get install -y samtools", 
                          shell=True, check=True)
        
        # Create the index
        cmd = f"samtools index {bam_path}"
        process = subprocess.run(cmd, shell=True, check=True, 
                               capture_output=True, text=True)
        
        # Check if index file was created
        index_path = f"{bam_path}.bai"
        if os.path.exists(index_path):
            logger.info(f"Successfully indexed BAM file, index at {index_path}")
            return True, f"Created index at {index_path}"
        else:
            logger.warning(f"Index command completed but no index file found at {index_path}")
            return False, "Index command completed but no index file found"
            
    except subprocess.CalledProcessError as e:
        error_msg = f"Error indexing BAM file: {e.stderr}"
        logger.error(error_msg)
        return False, error_msg
    except Exception as e:
        error_msg = f"Unexpected error while indexing BAM file: {str(e)}"
        logger.error(error_msg)
        return False, error_msg

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({"status": "healthy"}), 200

@app.route('/variant-call', methods=['POST'])
def variant_call():
    """
    Call variants using GATK HaplotypeCaller
    
    This endpoint runs GATK directly using the gatk command line tool.
    GATK must be installed in this container.
    """
    try:
        if 'file' not in request.files:
            return jsonify({"error": "No file provided"}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({"error": "No filename specified"}), 400
        
        # Get parameters
        reference_genome = request.form.get('reference_genome', 'hg38')
        regions = request.form.get('regions', None)
        
        # Validate reference genome
        if reference_genome not in REFERENCE_PATHS:
            return jsonify({"error": f"Unsupported reference genome: {reference_genome}"}), 400
        
        reference_path = REFERENCE_PATHS[reference_genome]
        if not os.path.exists(reference_path):
            return jsonify({"error": f"Reference genome file not found: {reference_path}"}), 500
        
        # Save uploaded file to a temporary directory
        filename = secure_filename(file.filename)
        input_dir = tempfile.mkdtemp(dir=TEMP_DIR)
        input_path = os.path.join(input_dir, filename)
        output_path = os.path.join(input_dir, f"{os.path.splitext(filename)[0]}.vcf")
        
        file.save(input_path)
        logger.info(f"Saved uploaded file to {input_path}")
        
        # Check if file exists
        if not os.path.exists(input_path):
            return jsonify({"error": f"Failed to save uploaded file to {input_path}"}), 500
        
        # Log file details
        file_size = os.path.getsize(input_path)
        logger.info(f"File saved: {input_path}, size: {file_size} bytes")
        
        # Determine if it's a BAM/CRAM or VCF file
        file_ext = os.path.splitext(filename)[1].lower()
        
        if file_ext in ['.vcf', '.vcf.gz']:
            # If it's already a VCF, just return the path
            return jsonify({
                "message": "File already contains variants",
                "output_file": input_path
            }), 200
        
        elif file_ext in ['.bam', '.cram', '.sam']:
            # For BAM files, create an index first
            if file_ext == '.bam':
                success, message = index_bam_file(input_path)
                if not success:
                    return jsonify({
                        "error": f"Failed to index BAM file: {message}"
                    }), 500
                logger.info(f"BAM file indexed successfully: {message}")
            
            # For BAM/CRAM files, run GATK HaplotypeCaller
            # Build GATK command
            regions_arg = f"-L {regions}" if regions else ""
            max_memory = os.environ.get('MAX_MEMORY', '4g')
            
            # Run GATK HaplotypeCaller directly
            logger.info(f"Running GATK HaplotypeCaller on {input_path}")
            
            # Execute GATK directly
            gatk_command = f"gatk --java-options '-Xmx{max_memory}' HaplotypeCaller -R {reference_path} -I {input_path} -O {output_path} {regions_arg}"
            
            logger.info(f"Executing command: {gatk_command}")
            
            # Run the command
            try:
                # Use subprocess run with full logging
                process = subprocess.run(
                    gatk_command,
                    shell=True,
                    check=True,
                    text=True,
                    capture_output=True
                )
                
                logger.info(f"GATK command completed successfully")
                logger.debug(f"STDOUT: {process.stdout}")
                
                # Check if output file was created
                if not os.path.exists(output_path):
                    logger.error(f"Output file not created: {output_path}")
                    return jsonify({
                        "error": "GATK command completed but output file was not created",
                        "command": gatk_command
                    }), 500
                
                return jsonify({
                    "message": "Variant calling complete",
                    "output_file": output_path,
                    "command": gatk_command
                }), 200
                
            except subprocess.CalledProcessError as e:
                logger.error(f"GATK command failed: {e.stderr}")
                return jsonify({
                    "error": "GATK command failed",
                    "command": gatk_command,
                    "details": e.stderr
                }), 500
        
        else:
            return jsonify({"error": f"Unsupported file format: {file_ext}"}), 400
    
    except Exception as e:
        logger.exception(f"Unexpected error in variant-call endpoint: {str(e)}")
        return jsonify({"error": str(e)}), 500

def ensure_reference_dictionaries():
    """Check if GATK dictionaries exist for reference genomes and create them if needed"""
    for genome_name, fasta_path in REFERENCE_PATHS.items():
        if os.path.exists(fasta_path):
            # Check if dictionary exists
            dict_path = os.path.splitext(fasta_path)[0] + '.dict'
            if not os.path.exists(dict_path):
                logger.info(f"Creating sequence dictionary for {genome_name} at {dict_path}")
                try:
                    cmd = f"gatk CreateSequenceDictionary -R {fasta_path}"
                    subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True)
                    logger.info(f"Created sequence dictionary for {genome_name}")
                except Exception as e:
                    logger.error(f"Failed to create sequence dictionary for {genome_name}: {str(e)}")
            else:
                logger.info(f"Sequence dictionary for {genome_name} already exists at {dict_path}")
        else:
            logger.warning(f"Reference genome {genome_name} not found at {fasta_path}")

if __name__ == '__main__':
    # Make sure GATK is installed
    try:
        result = subprocess.run(["gatk", "--version"], capture_output=True, text=True)
        logger.info(f"GATK version: {result.stdout.strip()}")
        
        # Ensure reference dictionaries exist
        ensure_reference_dictionaries()
        
        # Check if samtools is installed
        try:
            result = subprocess.run(["samtools", "--version"], capture_output=True, text=True)
            logger.info(f"samtools version: {result.stdout.splitlines()[0] if result.stdout else 'Unknown'}")
        except:
            logger.warning("samtools not found. Will attempt to install when needed.")
    except Exception as e:
        logger.error(f"GATK not found or not executable: {str(e)}")
    
    app.run(host='0.0.0.0', port=5000, debug=False) 