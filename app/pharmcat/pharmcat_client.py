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

# Import pysam for VCF sample extraction
try:
    import pysam  # type: ignore
    PYSAM_AVAILABLE = True
except ImportError:
    PYSAM_AVAILABLE = False

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# PharmCAT service configuration
PHARMCAT_API_URL = os.environ.get("PHARMCAT_API_URL", "http://pharmcat:5000")
PHARMCAT_JAR_PATH = os.environ.get("PHARMCAT_JAR_PATH", "/pharmcat/pharmcat.jar")

def extract_sample_id_from_vcf(vcf_path: str) -> Optional[str]:
    """
    Extract the sample ID from a VCF file.
    
    Args:
        vcf_path: Path to the VCF file
        
    Returns:
        Sample ID from the VCF file, or None if not found
    """
    try:
        # Try using pysam first (more reliable)
        if PYSAM_AVAILABLE:
            try:
                with pysam.VariantFile(vcf_path) as vcf:
                    samples = list(vcf.header.samples)
                    if samples:
                        sample_id = samples[0]  # Use first sample
                        logger.info(f"Extracted sample ID from VCF using pysam: {sample_id}")
                        return sample_id
            except Exception as e:
                logger.warning(f"Failed to extract sample ID using pysam: {e}")
        
        # Fallback to bcftools
        try:
            # Use bcftools query to get sample names
            cmd = ["bcftools", "query", "-l", vcf_path]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0 and result.stdout.strip():
                samples = result.stdout.strip().split('\n')
                if samples and samples[0]:
                    sample_id = samples[0].strip()
                    logger.info(f"Extracted sample ID from VCF using bcftools: {sample_id}")
                    return sample_id
            else:
                logger.warning(f"bcftools query failed: {result.stderr}")
        except Exception as e:
            logger.warning(f"Failed to extract sample ID using bcftools: {e}")
        
        # Final fallback: try to parse VCF header manually
        try:
            with open(vcf_path, 'r') as f:
                for line in f:
                    if line.startswith('#CHROM'):
                        # Parse the header line to get sample names
                        parts = line.strip().split('\t')
                        if len(parts) > 9:  # Should have at least CHROM, POS, ID, REF, ALT, QUAL, FILTER, INFO, FORMAT, and sample(s)
                            sample_id = parts[9]  # First sample is at index 9
                            logger.info(f"Extracted sample ID from VCF header manually: {sample_id}")
                            return sample_id
                        break
        except Exception as e:
            logger.warning(f"Failed to extract sample ID manually: {e}")
        
        logger.warning(f"Could not extract sample ID from VCF file: {vcf_path}")
        return None
        
    except Exception as e:
        logger.error(f"Error extracting sample ID from VCF: {e}")
        return None

def call_pharmcat_service(vcf_path: str, output_json: Optional[str] = None, sample_id: Optional[str] = None, report_id: Optional[str] = None, patient_id: Optional[str] = None, sample_identifier: Optional[str] = None, outside_tsv_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Call the PharmCAT service to process a VCF file.
    
    Args:
        vcf_path: Path to the VCF file
        output_json: Optional path to save the JSON output
        sample_id: Optional sample ID to use
        report_id: Optional report ID to use for consistent directory naming
        patient_id: Optional patient ID to use for organizing reports in patient directories
        patient_identifier: Optional original patient identifier from user input (preferred over patient_id)
        
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
                # Attach outside call TSV if provided
                if outside_tsv_path and os.path.exists(outside_tsv_path):
                    try:
                        files['outside_tsv'] = (os.path.basename(outside_tsv_path), open(outside_tsv_path, 'rb'), 'text/tab-separated-values')
                        logger.info(f"Including outside TSV in request: {outside_tsv_path}")
                    except Exception as e:
                        logger.warning(f"Failed to attach outside TSV: {str(e)}")
                data = {}
                if sample_id:
                    data['sampleId'] = sample_id
                if report_id:
                    data['reportId'] = report_id
                    logger.info(f"Using report_id: {report_id} for consistent directory naming")
                
                # Always use patient_id (internal UUID) for directory naming
                if patient_id:
                    data['patientId'] = patient_id
                    logger.info(f"Using database patient ID for organizing reports: {patient_id}")
                elif sample_id:
                    # Fallback to sample_id if patient_id is unavailable
                    data['patientId'] = sample_id
                    logger.info(f"Using sample_id for organizing reports: {sample_id}")
                # Optionally pass a display identifier for UI/report embedding (service may ignore)
                if sample_identifier:
                    data['displayId'] = sample_identifier
                    logger.info(f"Passing display sample identifier: {sample_identifier}")
                
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
                logger.info(f"Response structure: {list(results.keys())}")
                
                # If the response contains URLs to report files, fetch the actual content
                if "data" in results and isinstance(results["data"], dict):
                    data = results["data"]
                    logger.info(f"Response data keys: {list(data.keys())}")
                    
                    # Look for report URLs and fetch the content
                    report_json_content = None
                    report_tsv_content = None
                    
                    # Try to get the JSON report content
                    for url_key in ["pharmcat_json_report_url", "json_report_url", "raw_report_url"]:
                        if url_key in data:
                            url = data[url_key]
                            # Skip if URL is None
                            if url is None:
                                logger.info(f"JSON URL ({url_key}) is None, skipping")
                                continue
                                
                            logger.info(f"Found report URL ({url_key}): {url}")
                            
                            # Convert relative URL to absolute path and read the file
                            if url.startswith("/"):
                                # Remove leading slash and try both relative and absolute paths
                                relative_path = url.lstrip("/")
                                absolute_path = f"/data/{relative_path}"
                                
                                logger.info(f"Trying relative path: {relative_path}")
                                logger.info(f"Trying absolute path: {absolute_path}")
                                
                                # Try relative path first
                                if os.path.exists(relative_path):
                                    file_path = relative_path
                                    logger.info(f"Found report file at relative path: {file_path}")
                                elif os.path.exists(absolute_path):
                                    file_path = absolute_path
                                    logger.info(f"Found report file at absolute path: {file_path}")
                                else:
                                    logger.warning(f"Report file not found at either path")
                                    continue
                                
                                try:
                                    with open(file_path, "r") as f:
                                        report_json_content = json.load(f)
                                        logger.info(f"Loaded JSON report with keys: {list(report_json_content.keys())}")
                                        break
                                except Exception as e:
                                    logger.warning(f"Failed to read JSON report from {file_path}: {str(e)}")
                    
                    # Try to get the TSV report content if available
                    logger.info("Checking for TSV report content...")
                    for url_key in ["pharmcat_tsv_report_url", "tsv_report_url"]:
                        if url_key in data:
                            url = data[url_key]
                            # Skip if URL is None
                            if url is None:
                                logger.info(f"TSV URL ({url_key}) is None, skipping")
                                continue
                                
                            logger.info(f"Found TSV URL ({url_key}): {url}")
                            
                            if url.startswith("/"):
                                # Remove leading slash and try both relative and absolute paths
                                relative_path = url.lstrip("/")
                                absolute_path = f"/data/{relative_path}"
                                
                                logger.info(f"Trying relative path: {relative_path}")
                                logger.info(f"Trying absolute path: {absolute_path}")
                                
                                # Try relative path first
                                if os.path.exists(relative_path):
                                    file_path = relative_path
                                    logger.info(f"Found TSV file at relative path: {file_path}")
                                elif os.path.exists(absolute_path):
                                    file_path = absolute_path
                                    logger.info(f"Found TSV file at absolute path: {file_path}")
                                else:
                                    logger.warning(f"TSV file not found at either path")
                                    continue
                                
                                try:
                                    with open(file_path, "r") as f:
                                        report_tsv_content = f.read()
                                        logger.info(f"Loaded TSV report with {len(report_tsv_content)} characters")
                                        break
                                except Exception as e:
                                    logger.warning(f"Failed to read TSV report from {file_path}: {str(e)}")
                    
                    # Log TSV content availability
                    if report_tsv_content:
                        logger.info("TSV report content successfully loaded and will be included in response")
                    else:
                        logger.info("No TSV report content found")
                    
                    # If we found report content, include it in the response
                    if report_json_content or report_tsv_content:
                        # Create a response structure that normalize_pharmcat_results can process
                        enhanced_results = {
                            "success": results.get("success", True),
                            "data": results.get("data", {}),
                            "report_json": report_json_content,
                            "report_tsv": report_tsv_content
                        }
                        logger.info("Enhanced response with actual report content")
                        if report_json_content:
                            logger.info(f"Included JSON report content ({len(report_json_content)} characters)")
                        if report_tsv_content:
                            logger.info(f"Included TSV report content ({len(report_tsv_content)} characters)")
                        results = enhanced_results
                
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
            results = run_pharmcat_jar(vcf_path, output_dir, sample_id, patient_id)
            
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
    Normalize PharmCAT results from report.json or report.tsv format.
    Prioritizes JSON parsing with TSV as a backup option.
    
    Args:
        response (dict): Raw PharmCAT API response or direct report.json content
        
    Returns:
        dict: Normalized response with gene and drug data
    """
    logger = get_logger()
    
    logger.info(f"=== NORMALIZE PHARMCAT RESULTS START ===")
    logger.info(f"Input response keys: {list(response.keys())}")
    logger.info(f"Input response type: {type(response)}")
    
    # Initialize normalized response structure
    normalized_response = {
        "success": False,
        "message": "",
        "data": {
            "genes": [],
            "drugRecommendations": [],
            "pdf_report_url": "",
            "html_report_url": ""
        }
    }
    
    try:
        # If response is a direct report.json (common PharmCAT output format)
        # Check if it has the expected top-level format of a PharmCAT report
        if all(key in response for key in ["pharmcatVersion", "genes", "drugs"]):
            logger.info("Input appears to be a direct PharmCAT report.json structure")
            # Use the response directly as the JSON data
            json_data = response
            json_processing_success = True
        else:
            # Check if we received a successful response from the API
            if "success" in response and not response.get("success", False):
                error_msg = f"PharmCAT analysis failed: {response.get('message', 'Unknown error')}"
                logger.error(error_msg)
                normalized_response["message"] = error_msg
                return normalized_response
            
            # APPROACH 1: Process JSON data (primary method)
            json_processing_success = False
            json_data = None
            tsv_content = None
            
            # Find the JSON data in the response structure
            if "report_json" in response:
                json_data = response["report_json"]
                logger.info("Found JSON data in response.report_json")
                logger.info(f"JSON data type: {type(json_data)}")
                if isinstance(json_data, dict):
                    logger.info(f"JSON data keys: {list(json_data.keys())}")
            elif "data" in response and "report_json" in response["data"]:
                json_data = response["data"]["report_json"]
                logger.info("Found JSON data in response.data.report_json")
                logger.info(f"JSON data type: {type(json_data)}")
                if isinstance(json_data, dict):
                    logger.info(f"JSON data keys: {list(json_data.keys())}")
            elif "data" in response and "results" in response["data"] and "report_json" in response["data"]["results"]:
                json_data = response["data"]["results"]["report_json"]
                logger.info("Found JSON data in response.data.results.report_json")
                logger.info(f"JSON data type: {type(json_data)}")
                if isinstance(json_data, dict):
                    logger.info(f"JSON data keys: {list(json_data.keys())}")
            elif "results" in response and "report_json" in response["results"]:
                json_data = response["results"]["report_json"]
                logger.info("Found JSON data in response.results.report_json")
                logger.info(f"JSON data type: {type(json_data)}")
                if isinstance(json_data, dict):
                    logger.info(f"JSON data keys: {list(json_data.keys())}")
            else:
                logger.warning("No JSON data found in any expected location")
                logger.warning(f"Available keys: {list(response.keys())}")
                if "data" in response:
                    logger.warning(f"Data keys: {list(response['data'].keys())}")
                    if "results" in response["data"]:
                        logger.warning(f"Results keys: {list(response['data']['results'].keys())}")
            
            # Also look for TSV data as a backup
            if "report_tsv" in response:
                tsv_content = response["report_tsv"]
                logger.info("Found TSV data in response.report_tsv")
            elif "data" in response and "report_tsv" in response["data"]:
                tsv_content = response["data"]["report_tsv"]
                logger.info("Found TSV data in response.data.report_tsv")
            elif "data" in response and "results" in response["data"] and "report_tsv" in response["data"]["results"]:
                tsv_content = response["data"]["results"]["report_tsv"]
                logger.info("Found TSV data in response.data.results.report_tsv")
            elif "results" in response and "report_tsv" in response["results"]:
                tsv_content = response["results"]["report_tsv"]
                logger.info("Found TSV data in response.results.report_tsv")
        
        # If we have JSON data, try to process it
        if json_data:
            genes_data = []
            drug_recommendations = []
            
            # Process PharmCAT v3format (genes, drugs structure)
            if "genes" in json_data or "drugs" in json_data:
                logger.info("Processing PharmCAT v3format with genes/drugs structure")
                
                # Extract genes from genes section if available
                if "genes" in json_data and isinstance(json_data["genes"], dict):
                    logger.info(f"Processing {len(json_data['genes'])} guideline sources in genes")
                    
                    for guideline_source, genes_dict in json_data["genes"].items():
                        if not isinstance(genes_dict, dict):
                            continue
                            
                        logger.info(f"Processing guideline source: {guideline_source}")
                        logger.info(f"Found {len(genes_dict)} genes in {guideline_source}")
                        
                        # Each guideline source contains multiple genes
                        for gene_id, gene_report in genes_dict.items():
                            if not isinstance(gene_report, dict):
                                continue
                                
                            logger.info(f"Processing gene {gene_id} from {guideline_source}")
                            
                            # Extract basic gene information
                            diplotype = "Unknown/Unknown"
                            function = "Unknown"
                            activity_score = None
                            
                            # Look for phenotype information in recommendationDiplotypes
                            if "recommendationDiplotypes" in gene_report and isinstance(gene_report["recommendationDiplotypes"], list) and gene_report["recommendationDiplotypes"]:
                                # Use the first recommendation diplotype
                                rec_diplotype = gene_report["recommendationDiplotypes"][0]
                                
                                # Extract diplotype
                                if "label" in rec_diplotype:
                                    diplotype = rec_diplotype["label"]
                                
                                # Extract phenotype
                                if "phenotypes" in rec_diplotype:
                                    phenotypes = rec_diplotype["phenotypes"]
                                    if isinstance(phenotypes, list):
                                        function = ", ".join(phenotypes)
                                    else:
                                        function = str(phenotypes)
                                
                                # Extract activity score
                                if "activityScore" in rec_diplotype:
                                    activity_score = rec_diplotype["activityScore"]
                            
                            # DO NOT DO NOT DO NOT Fall back to sourceDiplotypes if recommendationDiplotypes not available
                            
                            # Create gene entry
                            gene_entry = {
                                "gene": gene_id,
                                "diplotype": diplotype,
                                "phenotype": function,
                                "activity_score": activity_score,
                                "guideline_source": guideline_source
                            }
                            
                            genes_data.append(gene_entry)
                            logger.info(f"Added gene from v3format: {gene_entry}")
                            
                            # Extract drug information from relatedDrugs array
                            if "relatedDrugs" in gene_report and isinstance(gene_report["relatedDrugs"], list) and gene_report["relatedDrugs"]:
                                logger.info(f"Found {len(gene_report['relatedDrugs'])} related drugs for {gene_id}")
                                
                                for drug_info in gene_report["relatedDrugs"]:
                                    if not isinstance(drug_info, dict):
                                        continue
                                    
                                    # Extract drug information
                                    drug_name = drug_info.get("name", "Unknown")
                                    drug_id = drug_info.get("id", "")
                                    
                                    # Create drug recommendation entry
                                    drug_recommendations.append({
                                        "gene": gene_id,
                                        "drug": drug_name,
                                        "drugId": drug_id,
                                        "guideline": guideline_source,
                                        "recommendation": f"See {guideline_source} guidelines for {gene_id}",
                                        "classification": "Related drug"
                                    })
                
                # Extract drug recommendations from drugs section if available
                if "drugs" in json_data and isinstance(json_data["drugs"], dict):
                    logger.info(f"Processing drugs section with {len(json_data['drugs'])} guideline sources")
                    
                    # Dictionary to collect drug recommendations by drug name
                    drug_recommendations_by_drug = {}
                    
                    # Process each guideline source (CPIC, DPWG, FDA)
                    for guideline_source, drugs_in_source in json_data["drugs"].items():
                        if not isinstance(drugs_in_source, dict):
                            continue
                        
                        logger.info(f"Processing {guideline_source} with {len(drugs_in_source)} drugs")
                        
                        # Process each drug within the guideline source
                        for drug_name, drug_info in drugs_in_source.items():
                            if not isinstance(drug_info, dict):
                                continue
                            
                            # Initialize drug entry if not exists
                            if drug_name not in drug_recommendations_by_drug:
                                drug_recommendations_by_drug[drug_name] = {
                                    "drug": drug_name,
                                    "drugId": drug_info.get("id", ""),
                                    "genes": set(),  # Use set to avoid duplicates
                                    "recommendations": []
                                }
                            
                            # Extract drug recommendations from guidelines
                            if "guidelines" in drug_info and isinstance(drug_info["guidelines"], list):
                                for guideline in drug_info["guidelines"]:
                                    guideline_name = guideline.get("name", "")
                                    
                                    # Extract annotations
                                    annotations = guideline.get("annotations", [])
                                    for annotation in annotations:
                                        # Extract recommendation text
                                        recommendation_text = ""
                                        if "drugRecommendation" in annotation:
                                            recommendation_text = annotation["drugRecommendation"]
                                        elif "text" in annotation:
                                            recommendation_text = annotation["text"]
                                        else:
                                            recommendation_text = "See report for details"
                                        
                                        # Extract classification and strength of evidence
                                        # PharmCAT uses strengthOfEvidence for CPIC levels (A, B, C)
                                        classification = ""
                                        strength_of_evidence = annotation.get("strengthOfEvidence", "")
                                        
                                        # Use strengthOfEvidence if available (preferred for CPIC levels)
                                        if strength_of_evidence:
                                            classification = strength_of_evidence
                                        elif "classification" in annotation:
                                            class_obj = annotation.get("classification", {})
                                            if isinstance(class_obj, dict):
                                                classification = class_obj.get("term", "")
                                            else:
                                                classification = str(class_obj)
                                        
                                        # Identify genes for this drug
                                        genes_for_drug = []
                                        
                                        # Try to extract gene from lookupKey (e.g., {'HLA-B': '*57:01 positive'})
                                        lookup_key = annotation.get('lookupKey', {})
                                        if isinstance(lookup_key, dict) and lookup_key:
                                            genes_for_drug = list(lookup_key.keys())
                                        
                                        # Try phenotypes as fallback (e.g., {'HLA-B': '*57:01 positive'})
                                        if not genes_for_drug:
                                            phenotypes = annotation.get('phenotypes', {})
                                            if isinstance(phenotypes, dict) and phenotypes:
                                                genes_for_drug = list(phenotypes.keys())
                                        
                                        # Try genotypes array as another fallback
                                        if not genes_for_drug:
                                            genotypes = annotation.get('genotypes', [])
                                            if genotypes and isinstance(genotypes[0], dict):
                                                diplotypes = genotypes[0].get('diplotypes', [])
                                                if diplotypes and isinstance(diplotypes[0], dict):
                                                    gene = diplotypes[0].get('gene')
                                                    if gene:
                                                        genes_for_drug = [gene]
                                        
                                        # Legacy fallbacks
                                        if not genes_for_drug:
                                            if "genes" in drug_info:
                                                genes_for_drug = drug_info.get("genes", [])
                                            elif "gene" in annotation:
                                                genes_for_drug = [annotation.get("gene", "")]
                                        
                                        if not genes_for_drug:
                                            genes_for_drug = ["Unknown"]
                                        
                                        # Add genes to the drug's gene set (deduplication)
                                        for gene in genes_for_drug:
                                            drug_recommendations_by_drug[drug_name]["genes"].add(gene)
                                            
                                            # Create recommendation entry
                                            recommendation = {
                                                "gene": gene,
                                                "guideline": guideline_name,
                                                "guideline_source": guideline_source,
                                                "recommendation": recommendation_text,
                                                "classification": classification
                                            }
                                            drug_recommendations_by_drug[drug_name]["recommendations"].append(recommendation)
                    
                    # Convert sets to lists and create final drug recommendations list
                    for drug_name, drug_data in drug_recommendations_by_drug.items():
                        drug_data["genes"] = list(drug_data["genes"])  # Convert set to list
                        drug_recommendations.append(drug_data)
                
                # If we found either genes or drug recommendations, consider JSON processing successful
                if genes_data or drug_recommendations:
                    json_processing_success = True
                    normalized_response.update({
                        "success": True,
                        "message": "PharmCAT v3results normalized successfully",
                        "data": {
                            "genes": genes_data,
                            "drugRecommendations": drug_recommendations
                        }
                    })
                    
                    logger.info(f"Successfully parsed {len(genes_data)} genes and {len(drug_recommendations)} drug recommendations from v3format")
                    logger.info(f"Final normalized response: {json.dumps(normalized_response, indent=2)}")
                    logger.info(f"=== NORMALIZE PHARMCAT RESULTS END (SUCCESS) ===")
                    return normalized_response
                else:
                    logger.warning("No genes or drug recommendations found in PharmCAT v3format")
                    logger.warning(f"Available keys: {list(json_data.keys())}")
                    if "genes" in json_data:
                        for guideline, genes in json_data["genes"].items():
                            logger.warning(f"Guideline {guideline}: {list(genes.keys()) if isinstance(genes, dict) else type(genes)}")
                    logger.warning(f"Drug recommendations count: {len(json_data.get('drugRecommendations', []))}")
                    logger.warning(f"Drugs section: {list(json_data.get('drugs', {}).keys()) if 'drugs' in json_data else 'Not found'}")
            
            # If we get here, the v3format processing failed
            logger.error("Failed to process PharmCAT v3format data")
            logger.error(f"JSON data keys: {list(json_data.keys())}")
            if "geneReports" in json_data:
                logger.error(f"Gene reports structure: {type(json_data['geneReports'])}")
                if isinstance(json_data["geneReports"], dict):
                    logger.error(f"Gene reports keys: {list(json_data['geneReports'].keys())}")
                
        # If JSON processing failed or no suitable data found, try TSV as a backup
        if not json_processing_success:
            logger.warning("No JSON data found in PharmCAT response, trying TSV")
            
            # If we have TSV content, try to use it
            if tsv_content:
                try:
                    logger.info("Trying TSV processing as backup method")
                    
                    # Try to get phenotype data for drug recommendations
                    phenotype_data = None
                    if "data" in response and "results" in response["data"] and "phenotype_results" in response["data"]["results"]:
                        logger.info("Found phenotype data in response.data.results.phenotype_results")
                        phenotype_data = response["data"]["results"]["phenotype_results"]
                    elif "results" in response and "phenotype_results" in response["results"]:
                        logger.info("Found phenotype data in response.results.phenotype_results")
                        phenotype_data = response["results"]["phenotype_results"]
                    elif "phenotype_results" in response:
                        logger.info("Found phenotype data in response.phenotype_results")
                        phenotype_data = response["phenotype_results"]
                    
                    # Use TSV report parser with phenotype data if available
                    normalized_data = parse_pharmcat_tsv_report(tsv_content, phenotype_data)
                    normalized_response.update({
                        "success": True,
                        "message": "PharmCAT results normalized successfully from TSV (backup method)",
                        "data": {
                            "genes": normalized_data["genes"],
                            "drugRecommendations": normalized_data.get("drugRecommendations", [])
                        }
                    })
                    logger.info(f"Normalized {len(normalized_data['genes'])} genes from TSV report")
                    
                    # Return the normalized response
                    return normalized_response
                except Exception as e:
                    logger.error(f"Error parsing PharmCAT TSV report: {str(e)}")
                    # Continue to error handling below
            else:
                logger.error("No TSV report found and JSON processing failed")
        
        # If we get here, both JSON and TSV processing failed
        logger.error("Both JSON and TSV processing failed for PharmCAT response")
        logger.error(f"Response structure: {list(response.keys())}")
        if "data" in response:
            logger.error(f"Data section keys: {list(response['data'].keys())}")
        
        # Return a minimal response with the available data
        normalized_response.update({
            "success": False,
            "message": "Failed to parse PharmCAT results from both JSON and TSV formats",
            "data": {
                "genes": [],
                "drugRecommendations": []
            }
        })
        
        logger.info(f"=== NORMALIZE PHARMCAT RESULTS END (FAILURE) ===")
        return normalized_response
        
    except Exception as e:
        error_msg = f"Failed to normalize PharmCAT results: {str(e)}"
        logger.error(error_msg)
        logger.error(traceback.format_exc())
        normalized_response.update({
            "success": False,
            "message": error_msg
        })
        return normalized_response

def get_logger():
    """Get the module logger"""
    return logging.getLogger(__name__)

async def async_call_pharmcat_api(input_file: str, report_id: Optional[str] = None, patient_id: Optional[str] = None, sample_identifier: Optional[str] = None) -> Dict[str, Any]:
    """
    Call the PharmCAT API asynchronously
    
    Args:
        input_file: Path to the VCF file to analyze
        report_id: Optional report ID to use for consistent directory naming
        patient_id: Optional internal UUID to use for organizing reports in patient directories
        sample_identifier: Optional user-entered Sample ID (preferred over patient_id for display)
        
    Returns:
        Dictionary containing PharmCAT results or error information
    """
    try:
        logger.info(f"Calling PharmCAT API asynchronously for file: {input_file}" + 
                    (f" with report_id: {report_id}" if report_id else "") +
                    (f" with patient_id: {patient_id}" if patient_id else "") +
                    (f" with sample_identifier: {sample_identifier}" if sample_identifier else ""))
        
        # Get the PharmCAT API URL from environment or use default
        pharmcat_api_url = os.environ.get("PHARMCAT_API_URL", "http://pharmcat:5000")

        # Read the file as bytes
        with open(input_file, 'rb') as f:
            file_content = f.read()
        
        # Prepare form data
        files = {"file": (os.path.basename(input_file), file_content, "application/octet-stream")}
        data = {}
        
        # Add report_id if provided
        if report_id:
            data["reportId"] = report_id
            logger.info(f"Added report_id to request: {report_id}")
        
        # Use patient_id for file naming (not sample_identifier)
        if patient_id:
            data["patientId"] = patient_id
            logger.info(f"Added database patient ID to request: {patient_id}")
        
        # Pass sample_identifier as displayId for display purposes only
        if sample_identifier:
            data["displayId"] = sample_identifier
            logger.info(f"Added user's sample identifier for display: {sample_identifier}")
        
        async with httpx.AsyncClient(timeout=300) as client:  # 5 minute timeout
            # Make the POST request with both files and form data
            response = await client.post(
                f"{pharmcat_api_url}/process",
                files=files,
                data=data
            )
            
            # Check if request was successful
            response.raise_for_status()
            
            # Parse response
            results = response.json()
            logger.info(f"Async PharmCAT API call successful")
            logger.info(f"Response structure: {list(results.keys())}")
            logger.info(f"Full response: {json.dumps(results, indent=2)}")
            
            # If the response contains URLs to report files, fetch the actual content
            if "data" in results and isinstance(results["data"], dict):
                data = results["data"]
                logger.info(f"Response data keys: {list(data.keys())}")
                logger.info(f"Response data content: {json.dumps(data, indent=2)}")
                
                # Look for report URLs and fetch the content
                report_json_content = None
                report_tsv_content = None
                
                # Try to get the JSON report content
                for url_key in ["pharmcat_json_report_url", "json_report_url", "raw_report_url"]:
                    if url_key in data:
                        url = data[url_key]
                        # Skip if URL is None
                        if url is None:
                            logger.info(f"JSON URL ({url_key}) is None, skipping")
                            continue
                            
                        logger.info(f"Found report URL ({url_key}): {url}")
                        
                        if url.startswith("/"):
                            # Remove leading slash and try both relative and absolute paths
                            relative_path = url.lstrip("/")
                            absolute_path = f"/data/{relative_path}"
                            
                            logger.info(f"Trying relative path: {relative_path}")
                            logger.info(f"Trying absolute path: {absolute_path}")
                            
                            # Try relative path first
                            if os.path.exists(relative_path):
                                file_path = relative_path
                                logger.info(f"Found report file at relative path: {file_path}")
                            elif os.path.exists(absolute_path):
                                file_path = absolute_path
                                logger.info(f"Found report file at absolute path: {file_path}")
                            else:
                                logger.warning(f"Report file not found at either path")
                                continue
                            
                            try:
                                with open(file_path, "r") as f:
                                    report_json_content = json.load(f)
                                    logger.info(f"Loaded JSON report with keys: {list(report_json_content.keys())}")
                                    break
                            except Exception as e:
                                logger.warning(f"Failed to read JSON report from {file_path}: {str(e)}")
                
                # Try to get the TSV report content if available
                for url_key in ["pharmcat_tsv_report_url", "tsv_report_url"]:
                    if url_key in data:
                        url = data[url_key]
                        # Skip if URL is None
                        if url is None:
                            logger.info(f"TSV URL ({url_key}) is None, skipping")
                            continue
                            
                        logger.info(f"Found TSV URL ({url_key}): {url}")
                        
                        if url.startswith("/"):
                            # Remove leading slash and try both relative and absolute paths
                            relative_path = url.lstrip("/")
                            absolute_path = f"/data/{relative_path}"
                            
                            logger.info(f"Trying relative path: {relative_path}")
                            logger.info(f"Trying absolute path: {absolute_path}")
                            
                            # Try relative path first
                            if os.path.exists(relative_path):
                                file_path = relative_path
                                logger.info(f"Found TSV file at relative path: {file_path}")
                            elif os.path.exists(absolute_path):
                                file_path = absolute_path
                                logger.info(f"Found TSV file at absolute path: {file_path}")
                            else:
                                logger.warning(f"TSV file not found at either path")
                                continue
                            
                            try:
                                with open(file_path, "r") as f:
                                    report_tsv_content = f.read()
                                    logger.info(f"Loaded TSV report with {len(report_tsv_content)} characters")
                                    break
                            except Exception as e:
                                logger.warning(f"Failed to read TSV report from {file_path}: {str(e)}")
                
                # If we found report content, include it in the response
                if report_json_content or report_tsv_content:
                    # Create a response structure that normalize_pharmcat_results can process
                    enhanced_results = {
                        "success": results.get("success", True),
                        "data": results.get("data", {}),
                        "report_json": report_json_content,
                        "report_tsv": report_tsv_content
                    }
                    logger.info("Enhanced response with actual report content")
                    if report_json_content:
                        logger.info(f"Included JSON report content ({len(report_json_content)} characters)")
                    if report_tsv_content:
                        logger.info(f"Included TSV report content ({len(report_tsv_content)} characters)")
                    return enhanced_results
            
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
                f"{PHARMCAT_API_URL}/api/process",
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

def run_pharmcat_jar(input_file: str, output_dir: str, sample_id: Optional[str] = None, patient_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Run PharmCAT directly using the JAR file.
    
    Args:
        input_file: Path to the input file
        output_dir: Directory to store the results
        sample_id: Optional sample ID to use
        patient_id: Optional patient ID to use for organizing reports in patient directories
        
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
        
        # Extract actual sample ID from VCF file for PharmCAT -s parameter
        vcf_sample_id = extract_sample_id_from_vcf(input_file)
        if vcf_sample_id:
            logger.info(f"Extracted VCF sample ID for PharmCAT: {vcf_sample_id}")
        else:
            logger.warning("Could not extract sample ID from VCF file - PharmCAT may fail")
        
        # Instead of calling the JAR directly, use the pharmcat_pipeline script
        # which handles preprocessing and proper execution
        logger.info(f"Running PharmCAT pipeline with input: {input_file}")
        
        # Prepare command
        cmd = [
            "pharmcat_pipeline",
            # "-G",  # Bypass gVCF check -- may need to use GATK to convert gVCF to VCF in the future. TO DO
            "-o", output_dir,  # Output directory
            "-v",  # Verbose output
        ]
        
        # Add VCF preprocessor flags based on environment variables
        # These flags control how missing/absent/unspecified PGx positions are handled
        # Default to False if not set (as per requirement)
        def str_to_bool(value: Optional[str]) -> bool:
            """Convert string to boolean, defaulting to False if None or empty."""
            if value is None:
                return False
            return str(value).lower() in ("true", "1", "yes", "on")
        
        pharmcat_absent_to_ref = str_to_bool(os.environ.get("PHARMCAT_ABSENT_TO_REF"))
        pharmcat_unspecified_to_ref = str_to_bool(os.environ.get("PHARMCAT_UNSPECIFIED_TO_REF"))
        
        # --missing-to-ref (-0) is equivalent to both --absent-to-ref and --unspecified-to-ref
        # If both are enabled, use --missing-to-ref for simplicity
        if pharmcat_absent_to_ref and pharmcat_unspecified_to_ref:
            cmd.append("--missing-to-ref")
            logger.info("Using --missing-to-ref flag (equivalent to both --absent-to-ref and --unspecified-to-ref)")
        else:
            # Add individual flags if only one is enabled
            if pharmcat_absent_to_ref:
                cmd.append("--absent-to-ref")
                logger.info("Using --absent-to-ref flag: assuming absent PGx sites are homozygous reference (0/0)")
            if pharmcat_unspecified_to_ref:
                cmd.append("--unspecified-to-ref")
                logger.info("Using --unspecified-to-ref flag: converting unspecified genotypes (./.) to homozygous reference (0/0)")
        
        # Add sample ID parameter only if we successfully extracted it from VCF
        if vcf_sample_id:
            cmd.extend(["-s", vcf_sample_id])
            logger.info(f"Using VCF sample ID for PharmCAT -s parameter: {vcf_sample_id}")
        else:
            logger.warning("No VCF sample ID available - PharmCAT will use default behavior")
        
        # Add input file as the last argument
        cmd.append(input_file)
        
        # Note: By default, pharmcat_pipeline runs the complete pipeline:
        # 1. NamedAlleleMatcher (generates .match.json)
        # 2. Phenotyper (generates .phenotype.json) 
        # 3. Reporter (generates HTML, JSON, TSV reports)
        # The -reporter flag would run only the reporter step independently
        
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
            "report_html": os.path.join(output_dir, f"{base_name}.report.html"),
            "report_json": os.path.join(output_dir, f"{base_name}.report.json"),
            "report_tsv": os.path.join(output_dir, f"{base_name}.report.tsv")
        }
        
        # Check if the essential output files exist (match and phenotype)
        essential_files = ["match_results", "phenotype_results"]
        if not all(os.path.exists(results_files[f]) for f in essential_files):
            missing_files = [k for k in essential_files if not os.path.exists(results_files[k])]
            error_msg = f"Missing required output files: {', '.join(missing_files)}"
            logger.error(error_msg)
            return {
                "success": False,
                "message": error_msg
            }
        
        # Read results
        results = {}
        for name, path in results_files.items():
            if os.path.exists(path):
                with open(path, 'r') as f:
                    if name == "report_html":
                        results[name] = f.read()
                    elif name == "report_tsv":
                        results[name] = f.read()
                    else:
                        results[name] = json.load(f)
            else:
                logger.warning(f"Optional output file not found: {path}")
        
        # Copy PharmCAT reports to per-job directory for direct access
        reports_root = Path(os.getenv("REPORT_DIR", "/data/reports"))
        reports_root.mkdir(parents=True, exist_ok=True)
        
        # Use patient_id for directory naming if provided, otherwise use base_name
        if patient_id:
            patient_dir = reports_root / str(patient_id)
            logger.info(f"Using patient_id {patient_id} for directory: {patient_dir}")
        else:
            patient_dir = reports_root / base_name
            logger.info(f"Using base_name {base_name} for directory: {patient_dir}")
        
        patient_dir.mkdir(parents=True, exist_ok=True)
        
        # Map of source files to destination files
        # Use patient_id in filename if provided, otherwise use base_name
        if patient_id:
            report_files = {
                f"{base_name}.report.html": f"{patient_id}_pgx_pharmcat.html",
                f"{base_name}.report.json": f"{patient_id}_pgx_pharmcat.json",
                f"{base_name}.report.tsv": f"{patient_id}_pgx_pharmcat.tsv",
                f"{base_name}.match.json": f"{patient_id}_pgx_match.json",
                f"{base_name}.phenotype.json": f"{patient_id}_pgx_phenotype.json"
            }
        else:
            report_files = {
                f"{base_name}.report.html": f"{base_name}_pgx_pharmcat.html",
                f"{base_name}.report.json": f"{base_name}_pgx_pharmcat.json",
                f"{base_name}.report.tsv": f"{base_name}_pgx_pharmcat.tsv",
                f"{base_name}.match.json": f"{base_name}_pgx_match.json",
                f"{base_name}.phenotype.json": f"{base_name}_pgx_phenotype.json"
            }
        
        # Copy all report files that exist
        for src_name, dest_name in report_files.items():
            src_path = Path(output_dir) / src_name
            dest_path = patient_dir / dest_name
            
            if os.path.exists(src_path):
                shutil.copy2(src_path, dest_path)
                logger.info(f"Report file copied to {dest_path}")
            else:
                logger.warning(f"Report file not found at {src_path}")
        
        # Also keep a copy of the latest report for each format as reference
        if os.path.exists(Path(output_dir) / f"{base_name}.report.json"):
            shutil.copy2(
                Path(output_dir) / f"{base_name}.report.json", 
                reports_root / "latest_pharmcat_report.json"
            )
            logger.info("Updated latest_pharmcat_report.json reference")
            
        if os.path.exists(Path(output_dir) / f"{base_name}.report.html"):
            shutil.copy2(
                Path(output_dir) / f"{base_name}.report.html", 
                reports_root / "latest_pharmcat_report.html"
            )
            logger.info("Updated latest_pharmcat_report.html reference")
            
        if os.path.exists(Path(output_dir) / f"{base_name}.report.tsv"):
            shutil.copy2(
                Path(output_dir) / f"{base_name}.report.tsv", 
                reports_root / "latest_pharmcat_report.tsv"
            )
            logger.info("Updated latest_pharmcat_report.tsv reference")
        
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
                            "diplotype": gene_info.get("diplotype", "Unknown"),
                            "phenotype": gene_info.get("phenotype", "Unknown"),
                            "activity_score": gene_info.get("activityScore")
                        }
                        genes_data.append(gene_entry)
                        
                # Extract drug recommendations
                if "drugRecommendations" in phenotype_data:
                    drug_recommendations = phenotype_data["drugRecommendations"]
                    
                logger.info(f"Extracted {len(genes_data)} genes and {len(drug_recommendations)} drug recommendations")
            except Exception as e:
                logger.error(f"Error parsing phenotype file: {str(e)}")
        
        # Prepare the result data with URLs to all report formats (normalize to copied patient directory paths)
        # Determine directory and file base used for copied PharmCAT reports
        patient_dir_name = str(patient_id) if patient_id else base_name
        pharmcat_base = str(patient_id) if patient_id else base_name
        pharmcat_html_name = f"{pharmcat_base}_pgx_pharmcat.html"
        pharmcat_json_name = f"{pharmcat_base}_pgx_pharmcat.json"
        pharmcat_tsv_name = f"{pharmcat_base}_pgx_pharmcat.tsv"

        return {
            "success": True,
            "message": "PharmCAT analysis completed successfully",
            "data": {
                "job_id": base_name,
                # Keep legacy fields (not used for navigation here)
                "pdf_report_url": f"/reports/{patient_dir_name}/{pharmcat_base}_pgx_report.pdf",
                "html_report_url": f"/reports/{patient_dir_name}/{pharmcat_base}_pgx_report_interactive.html",
                "interactive_html_report_url": f"/reports/{patient_dir_name}/{pharmcat_base}_pgx_report_interactive.html",
                "json_report_url": f"/reports/{patient_dir_name}/{pharmcat_json_name}",
                "tsv_report_url": f"/reports/{patient_dir_name}/{pharmcat_tsv_name}",
                "match_json_url": f"/reports/{patient_dir_name}/{pharmcat_base}_pgx_match.json",
                "phenotype_json_url": f"/reports/{patient_dir_name}/{pharmcat_base}_pgx_phenotype.json",
                # Normalized PharmCAT original report URLs used by UI
                "pharmcat_html_report_url": f"/reports/{patient_dir_name}/{pharmcat_html_name}",
                "pharmcat_json_report_url": f"/reports/{patient_dir_name}/{pharmcat_json_name}",
                "pharmcat_tsv_report_url": f"/reports/{patient_dir_name}/{pharmcat_tsv_name}",
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
                        "classification": annotation.get("strengthOfEvidence") or annotation.get("classification", "Unknown"),
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

# Refactor! We also have TSV parser dedicated file
def parse_pharmcat_tsv_report(tsv_content, phenotype_data=None):
    """
    Parse PharmCAT TSV report format into normalized data structure.
    This is used as a backup method when JSON parsing fails.
    
    NOTE: Future enhancement possibility - integrate with a local database of 
    drug recommendations instead of relying on the phenotype JSON data.
    This would make the TSV format fully self-sufficient.
    
    Args:
        tsv_content (str): Raw TSV content as string
        phenotype_data (dict, optional): Phenotype data from PharmCAT phenotype.json file
        
    Returns:
        dict: Normalized data with genes and drug recommendations
    """
    logger = get_logger()
    
    # Initialize return structure
    normalized_data = {
        "genes": [],
        "drugRecommendations": []
    }
    
    try:
        lines = tsv_content.strip().split('\n')
        
        if len(lines) < 2:
            raise ValueError("TSV content has insufficient data")
        
        # Extract PharmCAT version from first line
        pharmcat_version = lines[0].strip()
        logger.info(f"Parsing PharmCAT TSV report: {pharmcat_version}")
        
        # Parse header row to get column indices
        headers = lines[1].split('\t')
        
        # Define column indices with safe fallbacks (-1 indicates column not found)
        try:
            col_indices = {
                "gene": headers.index("Gene") if "Gene" in headers else -1,
                "diplotype": headers.index("Source Diplotype") if "Source Diplotype" in headers else -1,
                "phenotype": headers.index("Phenotype") if "Phenotype" in headers else -1,
                "activity_score": headers.index("Activity Score") if "Activity Score" in headers else -1,
                "hap1": headers.index("Haplotype 1") if "Haplotype 1" in headers else -1,
                "hap1_function": headers.index("Haplotype 1 Function") if "Haplotype 1 Function" in headers else -1,
                "hap1_activity": headers.index("Haplotype 1 Activity Value") if "Haplotype 1 Activity Value" in headers else -1,
                "hap2": headers.index("Haplotype 2") if "Haplotype 2" in headers else -1,
                "hap2_function": headers.index("Haplotype 2 Function") if "Haplotype 2 Function" in headers else -1,
                "hap2_activity": headers.index("Haplotype 2 Activity Value") if "Haplotype 2 Activity Value" in headers else -1,
                "outside_call": headers.index("Outside Call") if "Outside Call" in headers else -1,
                "match_score": headers.index("Match Score") if "Match Score" in headers else -1,
                "missing_positions": headers.index("Missing positions") if "Missing positions" in headers else -1,
                "lookup_diplotype": headers.index("Recommendation Lookup Diplotype") if "Recommendation Lookup Diplotype" in headers else -1,
                "lookup_phenotype": headers.index("Recommendation Lookup Phenotype") if "Recommendation Lookup Phenotype" in headers else -1,
                "lookup_activity_score": headers.index("Recommendation Lookup Activity Score") if "Recommendation Lookup Activity Score" in headers else -1
            }
            
            # Validate that we have at least the essential columns
            if col_indices["gene"] == -1:
                logger.warning("No 'Gene' column found in TSV, trying alternate headers")
                # Try an alternate header format
                if "gene" in [h.lower() for h in headers]:
                    col_indices["gene"] = [h.lower() for h in headers].index("gene")
                else:
                    raise ValueError("Required 'Gene' column not found in TSV headers")
                    
        except ValueError as e:
            logger.error(f"Error parsing TSV headers: {e}")
            logger.error(f"Available headers: {headers}")
            raise ValueError(f"Error parsing TSV headers: {e}")
        
        # Check if essential columns are present
        essential_columns = ["gene"]
        missing_columns = [col for col in essential_columns if col_indices[col] == -1]
        if missing_columns:
            raise ValueError(f"Required columns missing from TSV: {', '.join(missing_columns)}")
        
        # Process each gene data row (skip header rows)
        for i in range(2, len(lines)):
            row = lines[i].split('\t')
            
            if len(row) < max(c for c in col_indices.values() if c >= 0) + 1:
                # Skip incomplete rows
                logger.warning(f"Skipping incomplete row (line {i+1}): expected {len(headers)} columns, got {len(row)}")
                continue
                
            # Extract gene information
            gene_id = row[col_indices["gene"]] if col_indices["gene"] >= 0 and col_indices["gene"] < len(row) else "Unknown"
            
            # Skip rows with empty gene IDs
            if not gene_id or gene_id.strip() == "":
                logger.warning(f"Skipping row with empty gene ID (line {i+1})")
                continue
                
            diplotype = row[col_indices["diplotype"]] if col_indices["diplotype"] >= 0 and col_indices["diplotype"] < len(row) else "Unknown/Unknown"
            phenotype = row[col_indices["phenotype"]] if col_indices["phenotype"] >= 0 and col_indices["phenotype"] < len(row) else "Unknown"
            
            # Extract activity score (may be empty)
            activity_score_str = row[col_indices["activity_score"]] if col_indices["activity_score"] >= 0 and col_indices["activity_score"] < len(row) else ""
            activity_score = None
            if activity_score_str:
                try:
                    activity_score = float(activity_score_str)
                except ValueError:
                    logger.warning(f"Invalid activity score value '{activity_score_str}' for gene {gene_id}")
            
            # Use Recommendation Lookup fields when available, otherwise use main fields
            lookup_diplotype = None
            if col_indices["lookup_diplotype"] >= 0 and col_indices["lookup_diplotype"] < len(row):
                lookup_diplotype = row[col_indices["lookup_diplotype"]]
            
            lookup_phenotype = None
            if col_indices["lookup_phenotype"] >= 0 and col_indices["lookup_phenotype"] < len(row):
                lookup_phenotype = row[col_indices["lookup_phenotype"]]
            
            # Use lookup values if available, otherwise use main values
            final_diplotype = lookup_diplotype if lookup_diplotype else diplotype
            final_phenotype = lookup_phenotype if lookup_phenotype else phenotype
            
            # Extract lookup activity score
            lookup_activity_score = None
            if col_indices["lookup_activity_score"] >= 0 and col_indices["lookup_activity_score"] < len(row):
                try:
                    val = row[col_indices["lookup_activity_score"]]
                    if val and val.strip():
                        lookup_activity_score = float(val)
                except ValueError:
                    logger.warning(f"Invalid lookup activity score value for gene {gene_id}")
            
            # If no lookup activity score, use main activity score
            final_activity_score = lookup_activity_score if lookup_activity_score is not None else activity_score
            
            # Set activity_score to 2.0 for Normal Metabolizers if not specified
            if final_activity_score is None and ("Normal Metabolizer" in final_phenotype or "Normal Function" in final_phenotype):
                final_activity_score = 2.0
            
            gene_entry = {
                "gene": gene_id,
                "diplotype": final_diplotype,
                "phenotype": final_phenotype,
                "activity_score": final_activity_score
            }
            
            normalized_data["genes"].append(gene_entry)
            logger.info(f"Added gene from TSV: {gene_entry}")
        
        # Extract drug recommendations from phenotype data if available
        if phenotype_data:
            drug_recs = extract_drug_recommendations_from_phenotype(phenotype_data)
            normalized_data["drugRecommendations"] = drug_recs
            logger.info(f"Added {len(drug_recs)} drug recommendations from phenotype data")
        
        logger.info(f"Successfully parsed {len(normalized_data['genes'])} genes from TSV report")
        return normalized_data
        
    except Exception as e:
        logger.error(f"Error parsing PharmCAT TSV report: {str(e)}")
        logger.error(traceback.format_exc())
        raise

def extract_drug_recommendations_from_phenotype(phenotype_data):
    """
    Extract drug recommendations from PharmCAT phenotype.json data
    
    Args:
        phenotype_data (dict): PharmCAT phenotype.json data
        
    Returns:
        list: List of normalized drug recommendation objects
    """
    logger = get_logger()
    drug_recommendations = []
    
    try:
        # Extract directly from the drugRecommendations field if present
        if "drugRecommendations" in phenotype_data and isinstance(phenotype_data["drugRecommendations"], list):
            for drug_rec in phenotype_data["drugRecommendations"]:
                if not isinstance(drug_rec, dict):
                    continue
                    
                # Extract drug name
                drug_name = "Unknown"
                if "drug" in drug_rec:
                    if isinstance(drug_rec["drug"], dict) and "name" in drug_rec["drug"]:
                        drug_name = drug_rec["drug"]["name"]
                    else:
                        drug_name = str(drug_rec["drug"])
                
                # Create normalized drug recommendation
                normalized_rec = {
                    "gene": drug_rec.get("gene", "Multiple"),
                    "drug": drug_name,
                    "drugId": drug_rec.get("drugId", ""),
                    "guideline": drug_rec.get("guidelineName", ""),
                    "recommendation": drug_rec.get("recommendationText", "See report for details"),
                    "classification": drug_rec.get("strengthOfEvidence") or drug_rec.get("classification", "")
                }
                
                drug_recommendations.append(normalized_rec)
                logger.info(f"Added drug recommendation from phenotype data: {normalized_rec}")
        
        # Also check for other phenotype data structures
        elif "phenotypes" in phenotype_data:
            # Extract drug recommendations from phenotypes if possible
            for gene_id, gene_data in phenotype_data["phenotypes"].items():
                if "drugRecommendations" in gene_data and isinstance(gene_data["drugRecommendations"], list):
                    for drug_rec in gene_data["drugRecommendations"]:
                        if not isinstance(drug_rec, dict):
                            continue
                            
                        # Extract drug name
                        drug_name = "Unknown"
                        if "drug" in drug_rec:
                            if isinstance(drug_rec["drug"], dict) and "name" in drug_rec["drug"]:
                                drug_name = drug_rec["drug"]["name"]
                            else:
                                drug_name = str(drug_rec["drug"])
                        
                        # Create normalized drug recommendation
                        normalized_rec = {
                            "gene": gene_id,
                            "drug": drug_name,
                            "drugId": drug_rec.get("drugId", ""),
                            "guideline": drug_rec.get("guidelineName", ""),
                            "recommendation": drug_rec.get("recommendationText", "See report for details"),
                            "classification": drug_rec.get("strengthOfEvidence") or drug_rec.get("classification", "")
                        }
                        
                        drug_recommendations.append(normalized_rec)
                        logger.info(f"Added drug recommendation from phenotype.phenotypes data: {normalized_rec}")
        
        return drug_recommendations
    except Exception as e:
        logger.error(f"Error extracting drug recommendations from phenotype data: {str(e)}")
        logger.error(traceback.format_exc())
        return [] 