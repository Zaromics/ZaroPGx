#!/usr/bin/env python3
"""
Simplified PharmCAT Wrapper Service for debugging
"""

import os
import json
import logging
import subprocess
import gzip
import shutil
from pathlib import Path
from flask import Flask, request, jsonify
import psutil
import time
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("pharmcat_wrapper")

# Initialize Flask app
app = Flask(__name__)

# Data directory for VCF files
DATA_DIR = os.environ.get("DATA_DIR", "/data")
# Path to the PharmCAT JAR file (mounted from the PharmCAT container)
PHARMCAT_JAR = os.environ.get("PHARMCAT_JAR", "/pharmcat/pharmcat.jar")

os.makedirs(DATA_DIR, exist_ok=True)

print(f"Starting PharmCAT wrapper service with DATA_DIR={DATA_DIR}")
print(f"PharmCAT JAR location: {PHARMCAT_JAR}")

# Add these global variables after the existing ones
processing_status = {
    "current_file": None,
    "start_time": None,
    "status": "idle",
    "progress": 0,
    "last_error": None
}

# Simple home endpoint
@app.route('/', methods=['GET'])
def home():
    return jsonify({
        "service": "PharmCAT Wrapper",
        "status": "running",
        "endpoints": ["/", "/health", "/genotype"]
    })

# Health check endpoint
@app.route('/health', methods=['GET'])
def health_check():
    """API endpoint to check if the service is running."""
    logger.info("Health check called")
    
    # Check if the JAR file exists
    jar_exists = os.path.exists(PHARMCAT_JAR)
    
    return jsonify({
        "status": "ok",
        "service": "pharmcat-wrapper",
        "java_version": subprocess.check_output(["java", "-version"], stderr=subprocess.STDOUT).decode(),
        "pharmcat_jar_exists": jar_exists,
        "pharmcat_jar_path": PHARMCAT_JAR,
        "data_dir_exists": os.path.exists(DATA_DIR),
        "data_dir_contents": os.listdir(DATA_DIR)
    })

@app.route('/genotype', methods=['POST'])
def process_genotype():
    """
    API endpoint to process VCF files with PharmCAT.
    """
    try:
        # Check if file is in request
        if 'file' not in request.files:
            return jsonify({
                "success": False,
                "message": "No file provided"
            }), 400
            
        vcf_file = request.files['file']
        
        # Validate file
        if vcf_file.filename == '':
            return jsonify({
                "success": False,
                "message": "Empty filename"
            }), 400
            
        if not vcf_file.filename.endswith(('.vcf', '.vcf.gz')):
            return jsonify({
                "success": False,
                "message": "File must be a VCF (.vcf or .vcf.gz)"
            }), 400
        
        # Save file to data directory
        file_path = os.path.join(DATA_DIR, vcf_file.filename)
        vcf_file.save(file_path)
        
        logger.info(f"VCF file saved to {file_path}")
        
        # For debugging, just return success without actually calling PharmCAT
        return jsonify({
            "success": True,
            "message": "File uploaded successfully (PharmCAT processing disabled for debugging)",
            "file_path": file_path,
            "file_size": os.path.getsize(file_path)
        })
    
    except Exception as e:
        logger.error(f"Error processing request: {str(e)}")
        return jsonify({
            "success": False,
            "message": f"Error: {str(e)}"
        })

@app.route('/status', methods=['GET'])
def get_status():
    """API endpoint to get the current processing status."""
    try:
        # Get container stats
        container_stats = {
            "cpu_percent": psutil.cpu_percent(),
            "memory_percent": psutil.virtual_memory().percent,
            "disk_usage": psutil.disk_usage('/').percent
        }
        
        # Get process stats if we're processing a file
        process_stats = None
        if processing_status["current_file"]:
            process_stats = {
                "file": processing_status["current_file"],
                "start_time": processing_status["start_time"].isoformat() if processing_status["start_time"] else None,
                "elapsed_time": (datetime.now() - processing_status["start_time"]).total_seconds() if processing_status["start_time"] else 0
            }
        
        return jsonify({
            "status": "ok",
            "service": "pharmcat-wrapper",
            "processing_status": processing_status,
            "process_stats": process_stats,
            "container_stats": container_stats,
            "pharmcat_jar_exists": os.path.exists(PHARMCAT_JAR),
            "data_dir_contents": os.listdir(DATA_DIR)
        })
    except Exception as e:
        logger.error(f"Error getting status: {str(e)}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/process', methods=['POST'])
def process_file():
    """
    API endpoint to process files with PharmCAT and return results.
    """
    try:
        # Check if file is in request
        if 'file' not in request.files:
            return jsonify({
                "success": False,
                "message": "No file provided"
            }), 400
            
        uploaded_file = request.files['file']
        
        # Validate file
        if uploaded_file.filename == '':
            return jsonify({
                "success": False,
                "message": "Empty filename"
            }), 400
        
        # Update processing status
        processing_status.update({
            "current_file": uploaded_file.filename,
            "start_time": datetime.now(),
            "status": "processing",
            "progress": 0,
            "last_error": None
        })
        
        # Create a temporary output directory
        import tempfile
        with tempfile.TemporaryDirectory() as temp_dir:
            # Save uploaded file
            file_path = os.path.join(temp_dir, uploaded_file.filename)
            uploaded_file.save(file_path)
            
            # Get the base name without extension for output files
            base_name = os.path.splitext(uploaded_file.filename)[0]
            
            # Run PharmCAT pipeline
            try:
                # First, check if the JAR exists and is accessible
                if not os.path.exists(PHARMCAT_JAR):
                    raise FileNotFoundError(f"PharmCAT JAR not found at {PHARMCAT_JAR}")
                
                # Check Java version
                java_version = subprocess.check_output(["java", "-version"], stderr=subprocess.STDOUT).decode()
                logger.info(f"Using Java version: {java_version}")
                
                # Set environment variables
                env = os.environ.copy()
                env["JAVA_TOOL_OPTIONS"] = "-Xmx4g -XX:+UseG1GC"
                env["PHARMCAT_LOG_LEVEL"] = "DEBUG"
                
                # Run the pipeline with all options
                cmd = [
                    "pharmcat_pipeline",  # Use the pipeline script
                    "-G",               # Bypass gVCF check
                    "-o", temp_dir,     # Output directory
                    "-v",              # Verbose output
                    file_path         # Input file should be the last argument
                ]
                
                logger.info(f"Executing command: {' '.join(cmd)}")
                
                # Run the command with timeout
                process = subprocess.run(
                    cmd,
                    check=True,
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=300  # 5 minute timeout
                )
                
                logger.info(f"PharmCAT pipeline stdout: {process.stdout}")
                
                # Check for results files with the correct base name
                results_files = {
                    "match_results": os.path.join(temp_dir, f"{base_name}.match.json"),
                    "phenotype_results": os.path.join(temp_dir, f"{base_name}.phenotype.json"),
                    "report": os.path.join(temp_dir, f"{base_name}.report.html")
                }
                
                # Verify results exist
                if not all(os.path.exists(f) for f in results_files.values()):
                    missing_files = [k for k, v in results_files.items() if not os.path.exists(v)]
                    error_msg = f"Missing required output files: {', '.join(missing_files)}"
                    logger.error(error_msg)
                    return jsonify({
                        "success": False,
                        "message": error_msg
                    }), 500
                
                # Read results
                results = {}
                for name, path in results_files.items():
                    with open(path, 'r') as f:
                        if name == "report":
                            results[name] = f.read()
                        else:
                            results[name] = json.load(f)
                
                # Update processing status to success
                processing_status.update({
                    "status": "completed",
                    "progress": 100,
                    "last_error": None
                })
                
                # Return success response with results and report URLs
                return jsonify({
                    "success": True,
                    "message": "PharmCAT analysis completed successfully",
                    "results": results,
                    "report_url": f"/reports/{base_name}.report.html",  # URL for the report
                    "data": {
                        "pdf_report_url": f"/reports/{base_name}.report.pdf",
                        "html_report_url": f"/reports/{base_name}.report.html",
                        "results": results
                    }
                })
                
            except subprocess.CalledProcessError as e:
                # Update processing status to error
                error_msg = f"PharmCAT pipeline execution failed: {e.stderr or e.stdout or str(e)}"
                processing_status.update({
                    "status": "error",
                    "progress": 0,
                    "last_error": error_msg
                })
                logger.error(error_msg)
                return jsonify({
                    "success": False,
                    "message": "PharmCAT pipeline execution failed",
                    "error": str(e),
                    "stdout": e.stdout,
                    "stderr": e.stderr,
                    "temp_dir_contents": os.listdir(temp_dir) if os.path.exists(temp_dir) else []
                }), 500
                
    except Exception as e:
        # Update processing status to error
        error_msg = f"Error processing request: {str(e)}"
        processing_status.update({
            "status": "error",
            "progress": 0,
            "last_error": error_msg
        })
        logger.error(error_msg)
        return jsonify({
            "success": False,
            "message": error_msg
        }), 500

@app.route('/test', methods=['GET'])
def test_connection():
    """
    Test endpoint to verify connectivity.
    """
    return jsonify({
        "status": "ok",
        "message": "PharmCAT wrapper is running",
        "data_dir": os.getenv('DATA_DIR', '/data'),
        "pharmcat_jar": PHARMCAT_JAR,
        "jar_exists": os.path.exists(PHARMCAT_JAR)
    })

if __name__ == '__main__':
    # Start the Flask app
    logger.info("Starting PharmCAT wrapper service with DATA_DIR=%s", os.getenv('DATA_DIR', '/data'))
    logger.info("PharmCAT JAR location: %s", PHARMCAT_JAR)
    
    # Configure Flask to listen on all interfaces
    app.run(
        host='0.0.0.0',
        port=5000,
        debug=True,
        use_reloader=False  # Disable reloader in debug mode to avoid duplicate processes
    ) 