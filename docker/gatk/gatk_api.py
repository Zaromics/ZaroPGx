import os
import subprocess
import tempfile
import logging
import json
from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

DATA_DIR = os.environ.get('DATA_DIR', '/data')
REFERENCE_DIR = os.environ.get('REFERENCE_DIR', '/reference')
MAX_MEMORY = os.environ.get('MAX_MEMORY', '4g')

# Create necessary directories
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(os.path.join(DATA_DIR, 'uploads'), exist_ok=True)
os.makedirs(os.path.join(DATA_DIR, 'results'), exist_ok=True)

@app.route('/health', methods=['GET'])
def health_check():
    """Check if the service is healthy."""
    return jsonify({"status": "healthy"}), 200

@app.route('/variant-call', methods=['POST'])
def variant_call():
    """Call variants using GATK HaplotypeCaller."""
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400
    
    # Get parameters
    reference_genome = request.form.get('reference_genome', 'hg38')
    regions = request.form.get('regions', None)  # Optional regions to restrict variant calling
    
    # Map reference genome to path
    reference_paths = {
        'hg19': os.path.join(REFERENCE_DIR, 'hg19', 'ucsc.hg19.fasta'),
        'hg38': os.path.join(REFERENCE_DIR, 'hg38', 'Homo_sapiens_assembly38.fasta'),
        'grch37': os.path.join(REFERENCE_DIR, 'grch37', 'human_g1k_v37.fasta'),
        'grch38': os.path.join(REFERENCE_DIR, 'hg38', 'Homo_sapiens_assembly38.fasta')  # symlink
    }
    
    if reference_genome not in reference_paths:
        return jsonify({"error": f"Unsupported reference genome: {reference_genome}"}), 400
    
    reference_path = reference_paths[reference_genome]
    if not os.path.exists(reference_path):
        return jsonify({"error": f"Reference genome not found at {reference_path}"}), 500
    
    # Save the uploaded file
    filename = secure_filename(file.filename)
    input_path = os.path.join(DATA_DIR, 'uploads', filename)
    file.save(input_path)
    
    # Determine file type and process accordingly
    file_ext = os.path.splitext(filename)[1].lower()
    
    try:
        if file_ext in ['.bam']:
            # BAM file workflow - first convert to VCF with GATK HaplotypeCaller
            output_vcf = os.path.join(DATA_DIR, 'results', f"{os.path.splitext(filename)[0]}.vcf")
            
            # Define regions argument if provided
            regions_arg = f"-L {regions}" if regions else ""
            
            # Run GATK HaplotypeCaller
            gatk_cmd = f"gatk --java-options '-Xmx{MAX_MEMORY}' HaplotypeCaller -R {reference_path} -I {input_path} -O {output_vcf} {regions_arg}"
            logger.info(f"Running GATK command: {gatk_cmd}")
            
            process = subprocess.run(gatk_cmd, shell=True, check=True, text=True, capture_output=True)
            logger.info(f"GATK HaplotypeCaller output: {process.stdout}")
            
            return jsonify({
                "message": "Variant calling complete",
                "output_file": output_vcf,
                "command": gatk_cmd,
                "stdout": process.stdout
            }), 200
            
        elif file_ext in ['.vcf', '.vcf.gz']:
            # VCF file - already has variants, just return
            return jsonify({
                "message": "File already contains variants",
                "output_file": input_path
            }), 200
            
        else:
            return jsonify({"error": f"Unsupported file format: {file_ext}"}), 400
            
    except subprocess.CalledProcessError as e:
        logger.error(f"GATK command failed: {e.stderr}")
        return jsonify({
            "error": "Variant calling failed",
            "details": e.stderr
        }), 500
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False) 