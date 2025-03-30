import os
import requests
import logging
import subprocess
import json
import tempfile
from typing import Dict, List, Any, Optional

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# PharmCAT service configuration
PHARMCAT_SERVICE_URL = os.environ.get("PHARMCAT_SERVICE_URL", "http://pharmcat:8080")
PHARMCAT_DOCKER_IMAGE = os.environ.get("PHARMCAT_DOCKER_IMAGE", "pgkb/pharmcat:latest")
PHARMCAT_JAR_PATH = os.environ.get("PHARMCAT_JAR_PATH", "/app/lib/pharmcat.jar")

def call_pharmcat_service(vcf_path: str, output_json: Optional[str] = None, sample_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Call the PharmCAT service to process a VCF file.
    
    Args:
        vcf_path: Path to the VCF file
        output_json: Optional path to save the JSON output
        sample_id: Optional sample ID to use
        
    Returns:
        Dictionary containing PharmCAT results or error information
    """
    try:
        logger.info(f"Calling PharmCAT service for file: {vcf_path}")
        
        # Make sure the file exists
        if not os.path.exists(vcf_path):
            logger.error(f"Input file does not exist: {vcf_path}")
            return {"success": False, "message": f"Input file does not exist: {vcf_path}"}
        
        # Check file size to make sure it's not empty
        if os.path.getsize(vcf_path) == 0:
            logger.error(f"Input file is empty: {vcf_path}")
            return {"success": False, "message": f"Input file is empty: {vcf_path}"}
        
        # Try direct JAR execution or wrapper API
        pharmcat_jar = os.environ.get("PHARMCAT_JAR_PATH", "/pharmcat/pharmcat.jar")
        
        if os.path.exists(pharmcat_jar):
            logger.info(f"Found PharmCAT JAR at {pharmcat_jar}, using direct execution")
            # If output_json is provided, use it for storing results
            output_dir = os.path.dirname(output_json) if output_json else tempfile.mkdtemp()
            results = run_pharmcat_jar(vcf_path, output_dir, sample_id)
            
            # Save output to the specified location if requested
            if output_json and isinstance(results, dict) and results.get("success", False):
                with open(output_json, 'w') as f:
                    json.dump(results, f, indent=2)
                logger.info(f"Saved PharmCAT results to {output_json}")
                
            return results
        else:
            # Try the wrapper API instead
            logger.info("PharmCAT JAR not found, trying wrapper API")
            pharmcat_api_url = os.environ.get("PHARMCAT_API_URL", "http://pharmcat-wrapper:5000")
            
            logger.info(f"Calling PharmCAT wrapper API at {pharmcat_api_url}/process")
            with open(vcf_path, 'rb') as f:
                files = {'file': f}
                data = {}
                if sample_id:
                    data['sampleId'] = sample_id
                
                response = requests.post(
                    f"{pharmcat_api_url}/process",
                    files=files,
                    data=data,
                    timeout=300  # 5 minute timeout
                )
                
                # Check for HTTP errors
                try:
                    response.raise_for_status()
                except requests.exceptions.HTTPError as e:
                    logger.error(f"PharmCAT API HTTP error: {str(e)}")
                    return {"success": False, "message": f"PharmCAT API HTTP error: {str(e)}"}
                
                # Parse the response
                try:
                    results = response.json()
                    logger.info(f"PharmCAT API call successful: {results}")
                    
                    # Save output to the specified location if requested
                    if output_json and isinstance(results, dict):
                        with open(output_json, 'w') as f:
                            json.dump(results, f, indent=2)
                        logger.info(f"Saved PharmCAT results to {output_json}")
                    
                    # Add success if not present
                    if "success" not in results:
                        results["success"] = True
                    
                    return results
                except ValueError as e:
                    error_msg = f"Failed to parse PharmCAT API response: {str(e)}"
                    logger.error(error_msg)
                    return {"success": False, "message": error_msg}
            
    except Exception as e:
        error_msg = f"Error calling PharmCAT: {str(e)}"
        logger.error(error_msg)
        return {"success": False, "message": error_msg}

def call_pharmcat_api(input_file: str) -> Dict[str, Any]:
    """
    Call PharmCAT REST API service.
    
    Args:
        input_file: Path to the input file
        
    Returns:
        Dictionary containing PharmCAT results
    """
    try:
        # Open the file for reading
        with open(input_file, 'rb') as f:
            # Prepare the file for upload
            files = {'file': f}
            
            # Make the POST request
            response = requests.post(
                f"{PHARMCAT_SERVICE_URL}/api/process",
                files=files,
                timeout=300  # 5 minute timeout
            )
            
            # Check if request was successful
            response.raise_for_status()
            
            # Parse response
            results = response.json()
            logger.info(f"PharmCAT API call successful: {len(results)} results returned")
            
            return results
    except requests.exceptions.RequestException as e:
        logger.error(f"Error calling PharmCAT API: {str(e)}")
        raise

def run_pharmcat_jar(input_file: str, output_dir: str, sample_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Run PharmCAT directly using the JAR file.
    
    Args:
        input_file: Path to the input file
        output_dir: Directory to store the results
        sample_id: Optional sample ID to use
        
    Returns:
        Dictionary containing PharmCAT results
    """
    try:
        # Make sure output directory exists
        os.makedirs(output_dir, exist_ok=True)
        
        # Get the PharmCAT JAR path
        pharmcat_jar = os.environ.get("PHARMCAT_JAR_PATH", "/pharmcat/pharmcat.jar")
        
        # Determine file mode based on file extension
        file_ext = os.path.splitext(input_file)[1].lower()
        if file_ext == '.vcf':
            file_mode = 'vcf'
        else:
            # Default to VCF for now
            file_mode = 'vcf'
        
        logger.info(f"Running PharmCAT JAR: {pharmcat_jar} with input: {input_file} in mode: {file_mode}")
        
        # Prepare command
        cmd = [
            "java", "-jar", pharmcat_jar,
            "-m", file_mode,
            "-i", input_file,
            "-o", output_dir
        ]
        
        # Add sample ID if provided
        if sample_id:
            cmd.extend(["-s", sample_id])
        
        # Run PharmCAT
        logger.info(f"Executing PharmCAT command: {' '.join(cmd)}")
        process = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True
        )
        
        logger.info(f"PharmCAT execution completed with output: {process.stdout}")
        
        # Check for results.json which is the standard PharmCAT output
        results_file = os.path.join(output_dir, "results.json")
        if os.path.exists(results_file):
            with open(results_file, 'r') as f:
                results = json.load(f)
            
            # Add sample ID if provided
            if sample_id:
                results["sampleId"] = sample_id
            
            # Add success flag for consistency
            results["success"] = True
            
            return results
        else:
            # No results file found
            error_msg = f"PharmCAT did not generate results.json. Stdout: {process.stdout}, Stderr: {process.stderr}"
            logger.error(error_msg)
            return {
                "success": False,
                "message": "PharmCAT did not generate results.json",
                "stdout": process.stdout,
                "stderr": process.stderr
            }
    
    except subprocess.CalledProcessError as e:
        error_msg = f"PharmCAT execution failed: {e.stderr}"
        logger.error(error_msg)
        return {
            "success": False,
            "message": "PharmCAT execution failed",
            "error": str(e),
            "stderr": e.stderr
        }
    
    except Exception as e:
        error_msg = f"Error running PharmCAT: {str(e)}"
        logger.error(error_msg)
        return {
            "success": False,
            "message": error_msg
        }

def run_pharmcat_docker(input_file: str) -> Dict[str, Any]:
    """
    Run PharmCAT using Docker container.
    
    Args:
        input_file: Path to the input file
        
    Returns:
        Dictionary containing PharmCAT results
    """
    try:
        # Create a temporary directory for output
        with tempfile.TemporaryDirectory() as temp_dir:
            # Get the absolute path to the input file and temporary directory
            abs_input_file = os.path.abspath(input_file)
            abs_temp_dir = os.path.abspath(temp_dir)
            
            # Extract directory and filename
            input_dir = os.path.dirname(abs_input_file)
            input_filename = os.path.basename(abs_input_file)
            
            logger.info(f"Running PharmCAT Docker for file: {input_filename}")
            
            # Docker run command
            command = [
                "docker", "run", "--rm",
                "-v", f"{input_dir}:/input",
                "-v", f"{abs_temp_dir}:/output",
                PHARMCAT_DOCKER_IMAGE,
                "-m", "23andme",
                "-i", f"/input/{input_filename}",
                "-o", "/output"
            ]
            
            process = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=True
            )
            
            logger.info(f"PharmCAT Docker execution successful: {process.stdout}")
            
            # Read the results JSON file
            results_file = os.path.join(temp_dir, "results.json")
            if os.path.exists(results_file):
                with open(results_file, 'r') as f:
                    results = json.load(f)
                return results
            else:
                raise FileNotFoundError(f"PharmCAT results file not found: {results_file}")
    except subprocess.CalledProcessError as e:
        logger.error(f"Error running PharmCAT Docker: {e.stderr}")
        raise
    except Exception as e:
        logger.error(f"Error processing PharmCAT Docker results: {str(e)}")
        raise

def parse_pharmcat_results(results: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse PharmCAT results into a standardized format.
    
    Args:
        results: Raw PharmCAT results
        
    Returns:
        Standardized results dictionary
    """
    try:
        # Extract gene-specific information
        genes = {}
        for gene in results.get("genes", []):
            gene_symbol = gene.get("gene")
            diplotype = gene.get("diplotype", {}).get("name", "Unknown")
            phenotype = gene.get("phenotype", {}).get("info", "Unknown")
            activity_score = gene.get("diplotype", {}).get("activityScore")
            
            genes[gene_symbol] = {
                "diplotype": diplotype,
                "phenotype": phenotype,
                "activity_score": activity_score
            }
        
        # Extract drug recommendations
        recommendations = []
        for drug in results.get("drugRecommendations", []):
            drug_name = drug.get("drug", {}).get("name", "Unknown")
            recommendations.append({
                "drug": drug_name,
                "guidelines": drug.get("guidelines", []),
                "recommendation": drug.get("recommendation", "Unknown"),
                "classification": drug.get("classification", "Unknown")
            })
        
        # Return standardized format
        return {
            "genes": genes,
            "recommendations": recommendations,
            "report_id": results.get("reportId"),
            "report_time": results.get("reportTime")
        }
    except Exception as e:
        logger.error(f"Error parsing PharmCAT results: {str(e)}")
        raise 