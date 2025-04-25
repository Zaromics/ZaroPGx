import os
import requests
import logging
import subprocess
import json
import tempfile
import shutil
import traceback
from typing import Dict, List, Any, Optional
from pathlib import Path
import httpx

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# PharmCAT service configuration
PHARMCAT_SERVICE_URL = os.environ.get("PHARMCAT_SERVICE_URL", "http://pharmcat:5000")
PHARMCAT_DOCKER_IMAGE = os.environ.get("PHARMCAT_DOCKER_IMAGE", "pgkb/pharmcat:latest")
PHARMCAT_JAR_PATH = os.environ.get("PHARMCAT_JAR_PATH", "/pharmcat/pharmcat.jar")

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
        
        # Try the wrapper API first as it's more reliable
        logger.info("Trying PharmCAT API")
        pharmcat_api_url = os.environ.get("PHARMCAT_API_URL", "http://pharmcat:5000")
        
        try:
            logger.info(f"Calling PharmCAT API at {pharmcat_api_url}/process")
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
                response.raise_for_status()
                
                # Parse the response
                results = response.json()
                logger.info(f"PharmCAT API call successful")
                
                # Process and normalize the results
                normalized_results = normalize_pharmcat_results(results)
                
                # Save output to the specified location if requested
                if output_json:
                    os.makedirs(os.path.dirname(output_json), exist_ok=True)
                    with open(output_json, 'w') as f:
                        json.dump(normalized_results, f, indent=2)
                    logger.info(f"Saved PharmCAT results to {output_json}")
                
                return normalized_results
        
        except (requests.exceptions.RequestException, ValueError) as api_error:
            logger.warning(f"PharmCAT API call failed: {str(api_error)}. Falling back to direct JAR execution.")
            # Fall back to direct JAR execution
        
        # Try direct JAR execution if API call failed
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
            
            # Normalize results
            normalized_results = normalize_pharmcat_results(results)
            return normalized_results
        else:
            error_msg = "PharmCAT JAR not found and API call failed"
            logger.error(error_msg)
            return {"success": False, "message": error_msg}
            
    except Exception as e:
        error_msg = f"Error calling PharmCAT: {str(e)}"
        logger.error(error_msg)
        return {"success": False, "message": error_msg}

def normalize_pharmcat_results(response):
    """
    Normalize PharmCAT results from report.json format
    
    Args:
        response (dict): Raw PharmCAT API response
        
    Returns:
        dict: Normalized response with gene and drug data
    """
    logger = get_logger()
    
    # Initialize normalized response structure
    normalized_response = {
        "success": False,
        "message": "",
        "genes": [],
        "drugRecommendations": [],
        "pdf_report_url": response.get("data", {}).get("pdf_report_url", ""),
        "html_report_url": response.get("data", {}).get("html_report_url", "")
    }
    
    try:
        # Check if we received a successful response
        if not response.get("success", False):
            error_msg = f"PharmCAT analysis failed: {response.get('message', 'Unknown error')}"
            logger.error(error_msg)
            normalized_response["message"] = error_msg
            return normalized_response
        
        # Extract genes data from the CPIC section
        genes_data = []
        genes_section = response.get("genes", {}).get("CPIC", {})
        
        if not genes_section:
            logger.warning("No genes.CPIC data found in PharmCAT report - trying alternative structure")
            # Try alternative structure - directly in genes object
            genes_section = response.get("genes", {})
        
        # Process each gene in the CPIC section
        for gene_id, gene_info in genes_section.items():
            logger.info(f"Processing gene {gene_id}")
            
            # First try the direct gene call structure
            diplotype = "Unknown/Unknown"
            function = "Unknown"
            activity_score = None
            
            # Try to get data from sourceDiplotypes first (most reliable)
            if "sourceDiplotypes" in gene_info and isinstance(gene_info["sourceDiplotypes"], list) and gene_info["sourceDiplotypes"]:
                source_diplotype = gene_info["sourceDiplotypes"][0]
                
                # Get alleles from source diplotype
                if "allele1" in source_diplotype and "allele2" in source_diplotype:
                    allele1 = source_diplotype.get("allele1")
                    allele2 = source_diplotype.get("allele2")
                    
                    # Extract diplotype from alleles - handle case where alleles can be None
                    allele1_name = allele1.get("name", "Unknown") if allele1 is not None else "Unknown"
                    allele2_name = allele2.get("name", "Unknown") if allele2 is not None else "Unknown"
                    diplotype = f"{allele1_name}/{allele2_name}"
                    
                    # Extract function/phenotype
                    if "phenotypes" in source_diplotype and source_diplotype["phenotypes"]:
                        function = ", ".join(source_diplotype["phenotypes"])
                    
                    # Extract activity score
                    activity_score = source_diplotype.get("activityScore")
            
            # If no sourceDiplotypes, look in other places
            elif "call" in gene_info:
                # PharmCAT v2 structure - most detailed
                call_info = gene_info.get("call", {})
                diplotype = call_info.get("diplotype", "Unknown/Unknown")
                function = call_info.get("function", "Unknown")
                activity_score = call_info.get("activityScore")
            elif "diplotype" in gene_info:
                # Direct diplotype field - simpler structure
                diplotype = gene_info.get("diplotype", "Unknown/Unknown")
                function = gene_info.get("phenotype", "Unknown")
            elif "alleles" in gene_info:
                # Extract from alleles if present
                alleles = gene_info.get("alleles", [])
                if len(alleles) > 0:
                    diplotype = "/".join([a.get("name", "Unknown") for a in alleles[:2]])
            
            # Sometimes diplotype might be in a nested structure
            if isinstance(diplotype, dict):
                diplotype = diplotype.get("name", "Unknown/Unknown")
            
            # Set activity_score to 2.0 for Normal Metabolizers if not specified
            if activity_score is None and function == "Normal Metabolizer":
                activity_score = 2.0
            
            gene_entry = {
                "gene": gene_id,
                "diplotype": diplotype,
                "phenotype": function,
                "activity_score": activity_score
            }
            
            genes_data.append(gene_entry)
            logger.info(f"Added gene: {gene_entry}")
        
        # Extract drug recommendations
        drug_recommendations = []
        
        # First try to find drugs in the main "drugs" section
        drugs_section = response.get("drugs", {})
        if not drugs_section:
            logger.warning("No drugs data found in PharmCAT report")
        
        # Look for annotations and guidelines
        for drug_id, drug_info in drugs_section.items():
            logger.info(f"Processing drug {drug_id}")
            
            # First try to extract information from guidelines->annotations (PharmCAT v2 structure)
            if "guidelines" in drug_info and drug_info["guidelines"]:
                for guideline in drug_info["guidelines"]:
                    # Extract annotations for each guideline
                    annotations = guideline.get("annotations", [])
                    guideline_name = guideline.get("name", "")
                    
                    for annotation in annotations:
                        # Try different fields for recommendation text
                        recommendation_text = None
                        if "text" in annotation:
                            recommendation_text = annotation.get("text", "")
                        elif "drugRecommendation" in annotation:
                            recommendation_text = annotation.get("drugRecommendation", "")
                        elif "recommendation" in annotation:
                            recommendation_text = annotation.get("recommendation", "")
                        
                        if not recommendation_text:
                            recommendation_text = "See report for details"
                        
                        # Try to get classification
                        classification = ""
                        if "classification" in annotation:
                            # Could be a string or object
                            class_obj = annotation.get("classification", {})
                            if isinstance(class_obj, dict):
                                classification = class_obj.get("term", "")
                            else:
                                classification = str(class_obj)
                        
                        # Identify genes for this drug - multiple ways to find them
                        genes_for_drug = []
                        
                        # Try different places to find associated genes
                        if "genes" in drug_info:
                            genes_for_drug = drug_info.get("genes", [])
                        elif "phenotypes" in annotation:
                            # Extract gene names from phenotypes keys
                            genes_for_drug = list(annotation.get("phenotypes", {}).keys())
                        elif "gene" in annotation:
                            genes_for_drug = [annotation.get("gene", "")]
                        
                        # If no genes found, still create at least one recommendation
                        if not genes_for_drug:
                            genes_for_drug = ["Unknown"]
                        
                        # Create a recommendation for each gene associated with this drug
                        for gene in genes_for_drug:
                            drug_rec = {
                                "gene": gene,
                                "drug": drug_id,
                                "drugId": drug_info.get("rxnormId", ""),
                                "guideline": guideline_name,
                                "recommendation": recommendation_text,
                                "classification": classification
                            }
                            drug_recommendations.append(drug_rec)
                            logger.info(f"Added drug recommendation: {drug_rec}")
            
            # Handle case with no guidelines structure but direct annotations
            elif "annotations" in drug_info:
                annotations = drug_info.get("annotations", [])
                for annotation in annotations:
                    # Create a generic recommendation
                    drug_rec = {
                        "gene": "Multiple",
                        "drug": drug_id,
                        "drugId": drug_info.get("rxnormId", ""),
                        "guideline": drug_info.get("guidelineName", ""),
                        "recommendation": annotation.get("text", "See report for details"),
                        "classification": ""
                    }
                    drug_recommendations.append(drug_rec)
                    logger.info(f"Added drug recommendation from direct annotation: {drug_rec}")
            
            # If no guidelines or annotations, but the drug section has a generic recommendation
            elif "recommendation" in drug_info:
                drug_rec = {
                    "gene": "Multiple",
                    "drug": drug_id,
                    "drugId": drug_info.get("rxnormId", ""),
                    "guideline": drug_info.get("guidelineName", ""),
                    "recommendation": drug_info.get("recommendation", "See report for details"),
                    "classification": drug_info.get("classification", "")
                }
                drug_recommendations.append(drug_rec)
                logger.info(f"Added generic drug recommendation: {drug_rec}")
                
        # If still no drug recommendations, try to find them in the messages section
        if len(drug_recommendations) == 0:
            messages = response.get("messages", [])
            for message in messages:
                if isinstance(message, dict) and "text" in message:
                    text = message.get("text", "")
                    
                    # Try to extract gene and drug info from the message
                    if ":" in text:
                        parts = text.split(":", 1)
                        gene_drug_info = parts[0].strip()
                        recommendation = parts[1].strip()
                        
                        # Check if it contains gene and drug info
                        if ":" in gene_drug_info:
                            gene = gene_drug_info.split(":")[0].strip()
                            drug = "Multiple"
                        else:
                            gene = "Unknown"
                            drug = gene_drug_info
                        
                        drug_rec = {
                            "gene": gene,
                            "drug": drug,
                            "drugId": "",
                            "guideline": "From messages",
                            "recommendation": recommendation,
                            "classification": ""
                        }
                        drug_recommendations.append(drug_rec)
                        logger.info(f"Added drug recommendation from messages: {drug_rec}")
        
        # Update the normalized response
        normalized_response.update({
            "success": True,
            "message": "PharmCAT results normalized successfully",
            "genes": genes_data,
            "drugRecommendations": drug_recommendations
        })
        
        logger.info(f"Normalized {len(genes_data)} genes and {len(drug_recommendations)} drug recommendations")
        return normalized_response
        
    except Exception as e:
        error_msg = f"Error normalizing PharmCAT results: {str(e)}"
        logger.error(error_msg)
        logger.error(traceback.format_exc())
        normalized_response["message"] = error_msg
        return normalized_response

def get_logger():
    """Get the module logger"""
    return logging.getLogger(__name__)

async def async_call_pharmcat_api(input_file: str) -> Dict[str, Any]:
    """
    Call the PharmCAT API asynchronously
    
    Args:
        input_file: Path to the VCF file to analyze
        
    Returns:
        Dictionary containing PharmCAT results or error information
    """
    try:
        logger.info(f"Calling PharmCAT API asynchronously for file: {input_file}")
        
        # Get the PharmCAT API URL from environment or use default
        pharmcat_api_url = os.environ.get("PHARMCAT_API_URL", "http://pharmcat:5000")

        # Read the file as bytes
        with open(input_file, 'rb') as f:
            file_content = f.read()
        
        async with httpx.AsyncClient(timeout=300) as client:  # 5 minute timeout
            # Create form data
            files = {"file": (os.path.basename(input_file), file_content, "application/octet-stream")}
            
            # Make the POST request
            response = await client.post(
                f"{pharmcat_api_url}/process",
                files=files
            )
            
            # Check if request was successful
            response.raise_for_status()
            
            # Parse response
            results = response.json()
            logger.info(f"Async PharmCAT API call successful")
            
            return results
            
    except httpx.HTTPError as e:
        logger.error(f"HTTP error calling PharmCAT API: {str(e)}")
        return {"success": False, "message": f"HTTP error: {str(e)}"}
    except Exception as e:
        logger.error(f"Error calling PharmCAT API asynchronously: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return {"success": False, "message": f"Error: {str(e)}"}

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
        
        # Use just the filename without extension as base name
        base_name = Path(input_file).stem
        if base_name.endswith('.vcf'):
            base_name = base_name[:-4]
        
        # Instead of calling the JAR directly, use the pharmcat_pipeline script
        # which handles preprocessing and proper execution
        logger.info(f"Running PharmCAT pipeline with input: {input_file}")
        
        # Prepare command
        cmd = [
            "pharmcat_pipeline",
            "-G",  # Bypass gVCF check
            "-o", output_dir,  # Output directory
            "-v",  # Verbose output
            input_file  # Input file should be the last argument
        ]
        
        # Add sample ID if provided
        if sample_id:
            cmd.extend(["-s", sample_id])
        
        # Set environment variables
        env = os.environ.copy()
        env["JAVA_TOOL_OPTIONS"] = "-Xmx4g -XX:+UseG1GC"
        env["PHARMCAT_LOG_LEVEL"] = "DEBUG"
        
        # Run PharmCAT pipeline
        logger.info(f"Executing PharmCAT command: {' '.join(cmd)}")
        process = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            env=env,
            timeout=300  # 5 minute timeout
        )
        
        logger.info(f"PharmCAT execution completed with output: {process.stdout}")
        
        # Expected output files
        results_files = {
            "match_results": os.path.join(output_dir, f"{base_name}.match.json"),
            "phenotype_results": os.path.join(output_dir, f"{base_name}.phenotype.json"),
            "report": os.path.join(output_dir, f"{base_name}.report.html")
        }
        
        # Check if all expected files exist
        if not all(os.path.exists(f) for f in results_files.values()):
            missing_files = [k for k, v in results_files.items() if not os.path.exists(v)]
            error_msg = f"Missing required output files: {', '.join(missing_files)}"
            logger.error(error_msg)
            return {
                "success": False,
                "message": error_msg
            }
        
        # Read results
        results = {}
        for name, path in results_files.items():
            with open(path, 'r') as f:
                if name == "report":
                    results[name] = f.read()
                else:
                    results[name] = json.load(f)
        
        # Copy PharmCAT reports to /data/reports for direct access
        reports_dir = Path("/data/reports")
        reports_dir.mkdir(parents=True, exist_ok=True)
        
        dest_html_path = reports_dir / f"{base_name}_pgx_report.html"
        src_html_path = Path(output_dir) / f"{base_name}.report.html"
        
        if os.path.exists(src_html_path):
            shutil.copy2(src_html_path, dest_html_path)
            logger.info(f"HTML report copied to {dest_html_path}")
            
        # Also copy the JSON report for inspection
        dest_json_path = reports_dir / f"{base_name}_pgx_report.json"
        src_json_path = Path(output_dir) / f"{base_name}.report.json"
        
        if os.path.exists(src_json_path):
            shutil.copy2(src_json_path, dest_json_path)
            logger.info(f"JSON report copied to {dest_json_path}")
        else:
            logger.warning(f"JSON report not found at {src_json_path}")
        
        # Extract gene data and drug recommendations
        genes_data = []
        drug_recommendations = []
        
        phenotype_path = Path(output_dir) / f"{base_name}.phenotype.json"
        if os.path.exists(phenotype_path):
            try:
                with open(phenotype_path, 'r') as f:
                    phenotype_data = json.load(f)
                    
                # Extract gene data from phenotype file
                if "phenotypes" in phenotype_data:
                    for gene_id, gene_info in phenotype_data["phenotypes"].items():
                        gene_entry = {
                            "gene": gene_id,
                            "diplotype": {
                                "name": gene_info.get("diplotype", "Unknown"),
                                "activityScore": gene_info.get("activityScore")
                            },
                            "phenotype": {
                                "info": gene_info.get("phenotype", "Unknown")
                            }
                        }
                        genes_data.append(gene_entry)
                        
                # Extract drug recommendations
                if "drugRecommendations" in phenotype_data:
                    drug_recommendations = phenotype_data["drugRecommendations"]
                    
                logger.info(f"Extracted {len(genes_data)} genes and {len(drug_recommendations)} drug recommendations")
            except Exception as e:
                logger.error(f"Error parsing phenotype file: {str(e)}")
        
        # Prepare the result data
        return {
            "success": True,
            "message": "PharmCAT analysis completed successfully",
            "data": {
                "job_id": base_name,
                "pdf_report_url": f"/reports/{base_name}_pgx_report.pdf",
                "html_report_url": f"/reports/{base_name}_pgx_report.html",
                "genes": genes_data,
                "drugRecommendations": drug_recommendations,
                "results": results
            }
        }
    
    except subprocess.CalledProcessError as e:
        error_msg = f"PharmCAT execution failed: {e.stderr}" if e.stderr else str(e)
        logger.error(error_msg)
        return {
            "success": False,
            "message": "PharmCAT execution failed",
            "error": str(e),
            "stderr": e.stderr if hasattr(e, 'stderr') else None
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
        # Extract gene-specific information using consistent approach
        genes = {}
        
        # PharmCAT v2 format - Extract from CPIC genes structure
        if "genes" in results and isinstance(results["genes"], dict) and "CPIC" in results["genes"]:
            for gene_id, gene_info in results["genes"]["CPIC"].items():
                if not isinstance(gene_info, dict):
                    continue
                
                # Extract using recommendationDiplotypes - most reliable source
                if "recommendationDiplotypes" in gene_info and isinstance(gene_info["recommendationDiplotypes"], list) and gene_info["recommendationDiplotypes"]:
                    # Use the first recommendation diplotype
                    diplotype_info = gene_info["recommendationDiplotypes"][0]
                    
                    # Get phenotype
                    phenotypes = diplotype_info.get("phenotypes", ["Unknown"])
                    phenotype = ", ".join(phenotypes) if isinstance(phenotypes, list) else str(phenotypes)
                    
                    # Extract diplotype label
                    diplotype = diplotype_info.get("label", "Unknown")
                    
                    # Extract activity score
                    activity_score = diplotype_info.get("activityScore")
                    
                    # Store gene info in the standardized format
                    genes[gene_id] = {
                        "diplotype": diplotype,
                        "phenotype": phenotype,
                        "activity_score": activity_score
                    }
        
        # Extract drug recommendations using consistent approach
        recommendations = []
        
        # Primary approach: Extract from recommendations section (most comprehensive)
        if "recommendations" in results and isinstance(results["recommendations"], dict):
            for drug_id, drug_data in results["recommendations"].items():
                if not isinstance(drug_data, dict):
                    continue
                
                # Get drug name
                drug_name = drug_data.get("drug", {}).get("name", drug_id)
                
                # Process annotations
                if "annotations" in drug_data and isinstance(drug_data["annotations"], list):
                    for annotation in drug_data["annotations"]:
                        # Create standardized recommendation entry
                        recommendations.append({
                            "drug": drug_name,
                            "guidelines": drug_data.get("guidelines", []),
                            "recommendation": annotation.get("drugRecommendation", "See report for details"),
                            "classification": annotation.get("classification", "Unknown"),
                            "implications": annotation.get("implications", [])
                        })
                else:
                    # Add basic entry if no annotations
                    recommendations.append({
                        "drug": drug_name,
                        "guidelines": drug_data.get("guidelines", []),
                        "recommendation": "See PharmCAT report for details",
                        "classification": drug_data.get("classification", "Unknown"),
                        "implications": []
                    })
        
        # Fallback approach: Extract from drugRecommendations array if present
        elif "drugRecommendations" in results and isinstance(results["drugRecommendations"], list):
            for drug in results["drugRecommendations"]:
                if not isinstance(drug, dict):
                    continue
                
                drug_name = drug.get("drug", {}).get("name", "Unknown")
                recommendations.append({
                    "drug": drug_name,
                    "guidelines": drug.get("guidelines", []),
                    "recommendation": drug.get("recommendation", "Unknown"),
                    "classification": drug.get("classification", "Unknown"),
                    "implications": drug.get("implications", [])
                })
        
        # Return standardized format
        return {
            "genes": genes,
            "recommendations": recommendations,
            "report_id": results.get("reportId") or results.get("title"),
            "report_time": results.get("reportTime") or results.get("timestamp")
        }
    except Exception as e:
        logger.error(f"Error parsing PharmCAT results: {str(e)}")
        raise 