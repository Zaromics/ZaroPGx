import json
import logging
import os
import shutil
import subprocess
import tempfile
import traceback
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import requests

from app.pharmcat.report_json import (
    detect_format,
    extract_recommendation_call,
    iter_gene_blocks,
)
from app.utils.outside_calls_override import get_override_file_path

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
                        logger.info(
                            f"Extracted sample ID from VCF using pysam: {sample_id}"
                        )
                        return sample_id
            except Exception as e:
                logger.warning(f"Failed to extract sample ID using pysam: {e}")

        # Fallback to bcftools
        try:
            # Use bcftools query to get sample names
            cmd = ["bcftools", "query", "-l", vcf_path]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

            if result.returncode == 0 and result.stdout.strip():
                samples = result.stdout.strip().split("\n")
                if samples and samples[0]:
                    sample_id = samples[0].strip()
                    logger.info(
                        f"Extracted sample ID from VCF using bcftools: {sample_id}"
                    )
                    return sample_id
            else:
                logger.warning(f"bcftools query failed: {result.stderr}")
        except Exception as e:
            logger.warning(f"Failed to extract sample ID using bcftools: {e}")

        # Final fallback: try to parse VCF header manually
        try:
            with open(vcf_path, "r") as f:
                for line in f:
                    if line.startswith("#CHROM"):
                        # Parse the header line to get sample names
                        parts = line.strip().split("\t")
                        if (
                            len(parts) > 9
                        ):  # Should have at least CHROM, POS, ID, REF, ALT, QUAL, FILTER, INFO, FORMAT, and sample(s)
                            sample_id = parts[9]  # First sample is at index 9
                            logger.info(
                                f"Extracted sample ID from VCF header manually: {sample_id}"
                            )
                            return sample_id
                        break
        except Exception as e:
            logger.warning(f"Failed to extract sample ID manually: {e}")

        logger.warning(f"Could not extract sample ID from VCF file: {vcf_path}")
        return None

    except Exception as e:
        logger.error(f"Error extracting sample ID from VCF: {e}")
        return None


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
            "html_report_url": "",
        },
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
            elif (
                "data" in response
                and "results" in response["data"]
                and "report_json" in response["data"]["results"]
            ):
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
                        logger.warning(
                            f"Results keys: {list(response['data']['results'].keys())}"
                        )

            # Also look for TSV data as a backup
            if "report_tsv" in response:
                tsv_content = response["report_tsv"]
                logger.info("Found TSV data in response.report_tsv")
            elif "data" in response and "report_tsv" in response["data"]:
                tsv_content = response["data"]["report_tsv"]
                logger.info("Found TSV data in response.data.report_tsv")
            elif (
                "data" in response
                and "results" in response["data"]
                and "report_tsv" in response["data"]["results"]
            ):
                tsv_content = response["data"]["results"]["report_tsv"]
                logger.info("Found TSV data in response.data.results.report_tsv")
            elif "results" in response and "report_tsv" in response["results"]:
                tsv_content = response["results"]["report_tsv"]
                logger.info("Found TSV data in response.results.report_tsv")

        # If we have JSON data, try to process it
        if json_data:
            genes_data = []
            drug_recommendations = []

            # Process PharmCAT v3 format (genes, drugs structure)
            if "genes" in json_data or "drugs" in json_data:
                logger.info("Processing PharmCAT v3 format with genes/drugs structure")

                # Extract genes from genes section if available
                if "genes" in json_data and isinstance(json_data["genes"], dict):
                    genes_section = json_data["genes"]
                    fmt = detect_format(genes_section)
                    logger.info(
                        "Detected %s PharmCAT genes format (%d top-level keys)",
                        fmt.upper(),
                        len(genes_section),
                    )

                    for block in iter_gene_blocks(genes_section):
                        call = extract_recommendation_call(block.gene_data)
                        gene_entry = {
                            "gene": block.gene_symbol,
                            "diplotype": call["diplotype"],
                            "phenotype": call["phenotype"],
                            "activity_score": call["activity_score"],
                            "guideline_source": block.source,
                        }
                        genes_data.append(gene_entry)
                        logger.info(f"Added gene from v3format: {gene_entry}")

                        related = block.gene_data.get("relatedDrugs")
                        if isinstance(related, list) and related:
                            for drug_info in related:
                                if not isinstance(drug_info, dict):
                                    continue
                                drug_name = drug_info.get("name", "Unknown")
                                drug_id = drug_info.get("id", "")
                                drug_recommendations.append(
                                    {
                                        "gene": block.gene_symbol,
                                        "drug": drug_name,
                                        "drugId": drug_id,
                                        "guideline": block.source,
                                        "recommendation": (
                                            f"See {block.source} guidelines "
                                            f"for {block.gene_symbol}"
                                        ),
                                        "classification": "Related drug",
                                    }
                                )
                elif "genes" in json_data and json_data.get("genes") is not None:
                    logger.warning(
                        f"Unexpected PharmCAT JSON shape: json_data['genes'] is {type(json_data.get('genes'))}, expected dict"
                    )

                # Extract drug recommendations from drugs section if available
                if "drugs" in json_data and isinstance(json_data["drugs"], dict):
                    logger.info(
                        f"Processing drugs section with {len(json_data['drugs'])} guideline sources"
                    )

                    # Dictionary to collect drug recommendations by drug name
                    drug_recommendations_by_drug = {}

                    # Process each guideline source (CPIC, DPWG, FDA)
                    for guideline_source, drugs_in_source in json_data["drugs"].items():
                        if not isinstance(drugs_in_source, dict):
                            logger.warning(
                                f"Unexpected PharmCAT JSON shape: drugs_in_source is {type(drugs_in_source)}, expected dict"
                            )
                            continue

                        logger.info(
                            f"Processing {guideline_source} with {len(drugs_in_source)} drugs"
                        )

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
                                    "recommendations": [],
                                }

                            # Extract drug recommendations from guidelines
                            if "guidelines" in drug_info and isinstance(
                                drug_info["guidelines"], list
                            ):
                                for guideline in drug_info["guidelines"]:
                                    guideline_name = guideline.get("name", "")

                                    # Extract annotations
                                    annotations = guideline.get("annotations", [])
                                    for annotation in annotations:
                                        # Extract recommendation text
                                        recommendation_text = ""
                                        if "drugRecommendation" in annotation:
                                            recommendation_text = annotation[
                                                "drugRecommendation"
                                            ]
                                        elif "text" in annotation:
                                            recommendation_text = annotation["text"]
                                        else:
                                            recommendation_text = (
                                                "See report for details"
                                            )

                                        # Extract classification and strength of evidence
                                        # PharmCAT uses strengthOfEvidence for CPIC levels (A, B, C)
                                        classification = ""
                                        strength_of_evidence = annotation.get(
                                            "strengthOfEvidence", ""
                                        )

                                        # Use strengthOfEvidence if available (preferred for CPIC levels)
                                        if strength_of_evidence:
                                            classification = strength_of_evidence
                                        elif "classification" in annotation:
                                            class_obj = annotation.get(
                                                "classification", {}
                                            )
                                            if isinstance(class_obj, dict):
                                                classification = class_obj.get(
                                                    "term", ""
                                                )
                                            else:
                                                classification = str(class_obj)

                                        # Identify genes for this drug
                                        genes_for_drug = []

                                        # Try to extract gene from lookupKey (e.g., {'HLA-B': '*57:01 positive'})
                                        lookup_key = annotation.get("lookupKey", {})
                                        if isinstance(lookup_key, dict) and lookup_key:
                                            genes_for_drug = list(lookup_key.keys())

                                        # Try phenotypes as fallback (e.g., {'HLA-B': '*57:01 positive'})
                                        if not genes_for_drug:
                                            phenotypes = annotation.get(
                                                "phenotypes", {}
                                            )
                                            if (
                                                isinstance(phenotypes, dict)
                                                and phenotypes
                                            ):
                                                genes_for_drug = list(phenotypes.keys())

                                        # Try genotypes array as another fallback
                                        if not genes_for_drug:
                                            genotypes = annotation.get("genotypes", [])
                                            if genotypes and isinstance(
                                                genotypes[0], dict
                                            ):
                                                diplotypes = genotypes[0].get(
                                                    "diplotypes", []
                                                )
                                                if diplotypes and isinstance(
                                                    diplotypes[0], dict
                                                ):
                                                    gene = diplotypes[0].get("gene")
                                                    if gene:
                                                        genes_for_drug = [gene]

                                        # Legacy fallbacks
                                        if not genes_for_drug:
                                            if "genes" in drug_info:
                                                genes_for_drug = drug_info.get(
                                                    "genes", []
                                                )
                                            elif "gene" in annotation:
                                                genes_for_drug = [
                                                    annotation.get("gene", "")
                                                ]

                                        if not genes_for_drug:
                                            genes_for_drug = ["Unknown"]

                                        # Add genes to the drug's gene set (deduplication)
                                        for gene in genes_for_drug:
                                            drug_recommendations_by_drug[drug_name][
                                                "genes"
                                            ].add(gene)

                                            # Create recommendation entry
                                            recommendation = {
                                                "gene": gene,
                                                "guideline": guideline_name,
                                                "guideline_source": guideline_source,
                                                "recommendation": recommendation_text,
                                                "classification": classification,
                                            }
                                            drug_recommendations_by_drug[drug_name][
                                                "recommendations"
                                            ].append(recommendation)

                    # Convert sets to lists and create final drug recommendations list
                    for drug_name, drug_data in drug_recommendations_by_drug.items():
                        drug_data["genes"] = list(
                            drug_data["genes"]
                        )  # Convert set to list
                        drug_recommendations.append(drug_data)
                elif "drugs" in json_data and json_data.get("drugs") is not None:
                    logger.warning(
                        f"Unexpected PharmCAT JSON shape: json_data['drugs'] is {type(json_data.get('drugs'))}, expected dict"
                    )

                # If we found either genes or drug recommendations, consider JSON processing successful
                if genes_data or drug_recommendations:
                    json_processing_success = True
                    normalized_response.update(
                        {
                            "success": True,
                            "message": "PharmCAT v3results normalized successfully",
                            "data": {
                                "genes": genes_data,
                                "drugRecommendations": drug_recommendations,
                            },
                        }
                    )

                    logger.info(
                        f"Successfully parsed {len(genes_data)} genes and {len(drug_recommendations)} drug recommendations from v3format"
                    )
                    logger.info(
                        f"Final normalized response: {json.dumps(normalized_response, indent=2)}"
                    )
                    logger.info(f"=== NORMALIZE PHARMCAT RESULTS END (SUCCESS) ===")
                    return normalized_response
                else:
                    logger.warning(
                        "No genes or drug recommendations found in PharmCAT v3format"
                    )
                    logger.warning(f"Available keys: {list(json_data.keys())}")
                    if "genes" in json_data:
                        for guideline, genes in json_data["genes"].items():
                            logger.warning(
                                f"Guideline {guideline}: {list(genes.keys()) if isinstance(genes, dict) else type(genes)}"
                            )
                    logger.warning(
                        f"Drug recommendations count: {len(json_data.get('drugRecommendations', []))}"
                    )
                    logger.warning(
                        f"Drugs section: {list(json_data.get('drugs', {}).keys()) if 'drugs' in json_data else 'Not found'}"
                    )

            # If we get here, the v3format processing failed
            logger.error("Failed to process PharmCAT v3format data")
            logger.error(f"JSON data keys: {list(json_data.keys())}")
            if "geneReports" in json_data:
                logger.error(
                    f"Gene reports structure: {type(json_data['geneReports'])}"
                )
                if isinstance(json_data["geneReports"], dict):
                    logger.error(
                        f"Gene reports keys: {list(json_data['geneReports'].keys())}"
                    )

        # If JSON processing failed or no suitable data found, try TSV as a backup
        if not json_processing_success:
            logger.warning("No JSON data found in PharmCAT response, trying TSV")

            # If we have TSV content, try to use it
            if tsv_content:
                try:
                    logger.info("Trying TSV processing as backup method")

                    # Try to get phenotype data for drug recommendations
                    phenotype_data = None
                    if (
                        "data" in response
                        and "results" in response["data"]
                        and "phenotype_results" in response["data"]["results"]
                    ):
                        logger.info(
                            "Found phenotype data in response.data.results.phenotype_results"
                        )
                        phenotype_data = response["data"]["results"][
                            "phenotype_results"
                        ]
                    elif (
                        "results" in response
                        and "phenotype_results" in response["results"]
                    ):
                        logger.info(
                            "Found phenotype data in response.results.phenotype_results"
                        )
                        phenotype_data = response["results"]["phenotype_results"]
                    elif "phenotype_results" in response:
                        logger.info(
                            "Found phenotype data in response.phenotype_results"
                        )
                        phenotype_data = response["phenotype_results"]

                    # Use TSV report parser with phenotype data if available
                    normalized_data = parse_pharmcat_tsv_report(
                        tsv_content, phenotype_data
                    )
                    normalized_response.update(
                        {
                            "success": True,
                            "message": "PharmCAT results normalized successfully from TSV (backup method)",
                            "data": {
                                "genes": normalized_data["genes"],
                                "drugRecommendations": normalized_data.get(
                                    "drugRecommendations", []
                                ),
                            },
                        }
                    )
                    logger.info(
                        f"Normalized {len(normalized_data['genes'])} genes from TSV report"
                    )

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
        normalized_response.update(
            {
                "success": False,
                "message": "Failed to parse PharmCAT results from both JSON and TSV formats",
                "data": {"genes": [], "drugRecommendations": []},
            }
        )

        logger.info(f"=== NORMALIZE PHARMCAT RESULTS END (FAILURE) ===")
        return normalized_response

    except Exception as e:
        error_msg = f"Failed to normalize PharmCAT results: {str(e)}"
        logger.error(error_msg)
        logger.error(traceback.format_exc())
        normalized_response.update({"success": False, "message": error_msg})
        return normalized_response


def get_logger():
    """Get the module logger"""
    return logging.getLogger(__name__)


async def async_call_pharmcat_api(
    input_file: str,
    report_id: Optional[str] = None,
    patient_id: Optional[str] = None,
    sample_identifier: Optional[str] = None,
) -> Dict[str, Any]:
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
        logger.info(
            f"Calling PharmCAT API asynchronously for file: {input_file}"
            + (f" with report_id: {report_id}" if report_id else "")
            + (f" with patient_id: {patient_id}" if patient_id else "")
            + (
                f" with sample_identifier: {sample_identifier}"
                if sample_identifier
                else ""
            )
        )

        # Get the PharmCAT API URL from environment or use default
        pharmcat_api_url = os.environ.get("PHARMCAT_API_URL", "http://pharmcat:5000")

        effective_outside_tsv_path = get_override_file_path()

        # Read the file as bytes
        with open(input_file, "rb") as f:
            file_content = f.read()

        outside_content = None
        outside_filename = None
        if effective_outside_tsv_path and os.path.exists(effective_outside_tsv_path):
            with open(effective_outside_tsv_path, "rb") as f:
                outside_content = f.read()
            outside_filename = os.path.basename(effective_outside_tsv_path)

        # Prepare form data
        files = {
            "file": (
                os.path.basename(input_file),
                file_content,
                "application/octet-stream",
            )
        }
        if outside_content is not None and outside_filename is not None:
            files["outside_tsv"] = (
                outside_filename,
                outside_content,
                "text/tab-separated-values",
            )

        data = {}

        # Add report_id if provided
        if report_id:
            data["report_id"] = report_id
            logger.info(f"Added report_id to request: {report_id}")

        # Use patient_id for file naming (not sample_identifier)
        if patient_id:
            data["patient_id"] = patient_id
            logger.info(f"Added database patient ID to request: {patient_id}")

        # Pass sample_identifier as displayId for display purposes only
        if sample_identifier:
            data["sample_identifier"] = sample_identifier
            logger.info(
                f"Added user's sample identifier for display: {sample_identifier}"
            )

        async with httpx.AsyncClient(timeout=300) as client:  # 5 minute timeout
            # Make the POST request with both files and form data
            response = await client.post(
                f"{pharmcat_api_url}/genotype", files=files, data=data
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
                for url_key in [
                    "pharmcat_json_report_url",
                    "json_report_url",
                    "raw_report_url",
                ]:
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
                                logger.info(
                                    f"Found report file at relative path: {file_path}"
                                )
                            elif os.path.exists(absolute_path):
                                file_path = absolute_path
                                logger.info(
                                    f"Found report file at absolute path: {file_path}"
                                )
                            else:
                                logger.warning(f"Report file not found at either path")
                                continue

                            try:
                                with open(file_path, "r") as f:
                                    report_json_content = json.load(f)
                                    logger.info(
                                        f"Loaded JSON report with keys: {list(report_json_content.keys())}"
                                    )
                                    break
                            except Exception as e:
                                logger.warning(
                                    f"Failed to read JSON report from {file_path}: {str(e)}"
                                )

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
                                logger.info(
                                    f"Found TSV file at relative path: {file_path}"
                                )
                            elif os.path.exists(absolute_path):
                                file_path = absolute_path
                                logger.info(
                                    f"Found TSV file at absolute path: {file_path}"
                                )
                            else:
                                logger.warning(f"TSV file not found at either path")
                                continue

                            try:
                                with open(file_path, "r") as f:
                                    report_tsv_content = f.read()
                                    logger.info(
                                        f"Loaded TSV report with {len(report_tsv_content)} characters"
                                    )
                                    break
                            except Exception as e:
                                logger.warning(
                                    f"Failed to read TSV report from {file_path}: {str(e)}"
                                )

                # If we found report content, include it in the response
                if report_json_content or report_tsv_content:
                    # Create a response structure that normalize_pharmcat_results can process
                    enhanced_results = {
                        "success": results.get("success", True),
                        "data": results.get("data", {}),
                        "report_json": report_json_content,
                        "report_tsv": report_tsv_content,
                    }
                    logger.info("Enhanced response with actual report content")
                    if report_json_content:
                        logger.info(
                            f"Included JSON report content ({len(report_json_content)} characters)"
                        )
                    if report_tsv_content:
                        logger.info(
                            f"Included TSV report content ({len(report_tsv_content)} characters)"
                        )
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
        with open(input_file, "rb") as f:
            # Prepare the file for upload
            files = {"file": f}

            # Make the POST request
            response = requests.post(
                f"{PHARMCAT_API_URL}/genotype",
                files=files,
                timeout=300,  # 5 minute timeout
            )

            # Check if request was successful
            response.raise_for_status()

            # Parse response
            results = response.json()
            logger.info(
                f"PharmCAT API call successful: {len(results)} results returned"
            )

            return results
    except requests.exceptions.RequestException as e:
        logger.error(f"Error calling PharmCAT API: {str(e)}")
        raise


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
    normalized_data = {"genes": [], "drugRecommendations": []}

    try:
        lines = tsv_content.strip().split("\n")

        if len(lines) < 2:
            raise ValueError("TSV content has insufficient data")

        # Extract PharmCAT version from first line
        pharmcat_version = lines[0].strip()
        logger.info(f"Parsing PharmCAT TSV report: {pharmcat_version}")

        # Parse header row to get column indices
        headers = lines[1].split("\t")

        # Define column indices with safe fallbacks (-1 indicates column not found)
        try:
            col_indices = {
                "gene": headers.index("Gene") if "Gene" in headers else -1,
                "diplotype": (
                    headers.index("Source Diplotype")
                    if "Source Diplotype" in headers
                    else -1
                ),
                "phenotype": (
                    headers.index("Phenotype") if "Phenotype" in headers else -1
                ),
                "activity_score": (
                    headers.index("Activity Score")
                    if "Activity Score" in headers
                    else -1
                ),
                "hap1": (
                    headers.index("Haplotype 1") if "Haplotype 1" in headers else -1
                ),
                "hap1_function": (
                    headers.index("Haplotype 1 Function")
                    if "Haplotype 1 Function" in headers
                    else -1
                ),
                "hap1_activity": (
                    headers.index("Haplotype 1 Activity Value")
                    if "Haplotype 1 Activity Value" in headers
                    else -1
                ),
                "hap2": (
                    headers.index("Haplotype 2") if "Haplotype 2" in headers else -1
                ),
                "hap2_function": (
                    headers.index("Haplotype 2 Function")
                    if "Haplotype 2 Function" in headers
                    else -1
                ),
                "hap2_activity": (
                    headers.index("Haplotype 2 Activity Value")
                    if "Haplotype 2 Activity Value" in headers
                    else -1
                ),
                "outside_call": (
                    headers.index("Outside Call") if "Outside Call" in headers else -1
                ),
                "match_score": (
                    headers.index("Match Score") if "Match Score" in headers else -1
                ),
                "missing_positions": (
                    headers.index("Missing positions")
                    if "Missing positions" in headers
                    else -1
                ),
                "lookup_diplotype": (
                    headers.index("Recommendation Lookup Diplotype")
                    if "Recommendation Lookup Diplotype" in headers
                    else -1
                ),
                "lookup_phenotype": (
                    headers.index("Recommendation Lookup Phenotype")
                    if "Recommendation Lookup Phenotype" in headers
                    else -1
                ),
                "lookup_activity_score": (
                    headers.index("Recommendation Lookup Activity Score")
                    if "Recommendation Lookup Activity Score" in headers
                    else -1
                ),
            }

            # Validate that we have at least the essential columns
            if col_indices["gene"] == -1:
                logger.warning(
                    "No 'Gene' column found in TSV, trying alternate headers"
                )
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
            raise ValueError(
                f"Required columns missing from TSV: {', '.join(missing_columns)}"
            )

        # Process each gene data row (skip header rows)
        for i in range(2, len(lines)):
            row = lines[i].split("\t")

            if len(row) < max(c for c in col_indices.values() if c >= 0) + 1:
                # Skip incomplete rows
                logger.warning(
                    f"Skipping incomplete row (line {i+1}): expected {len(headers)} columns, got {len(row)}"
                )
                continue

            # Extract gene information
            gene_id = (
                row[col_indices["gene"]]
                if col_indices["gene"] >= 0 and col_indices["gene"] < len(row)
                else "Unknown"
            )

            # Skip rows with empty gene IDs
            if not gene_id or gene_id.strip() == "":
                logger.warning(f"Skipping row with empty gene ID (line {i+1})")
                continue

            diplotype = (
                row[col_indices["diplotype"]]
                if col_indices["diplotype"] >= 0 and col_indices["diplotype"] < len(row)
                else "Unknown/Unknown"
            )
            phenotype = (
                row[col_indices["phenotype"]]
                if col_indices["phenotype"] >= 0 and col_indices["phenotype"] < len(row)
                else "Unknown"
            )

            # Extract activity score (may be empty)
            activity_score_str = (
                row[col_indices["activity_score"]]
                if col_indices["activity_score"] >= 0
                and col_indices["activity_score"] < len(row)
                else ""
            )
            activity_score = None
            if activity_score_str:
                try:
                    activity_score = float(activity_score_str)
                except ValueError:
                    logger.warning(
                        f"Invalid activity score value '{activity_score_str}' for gene {gene_id}"
                    )

            # Use Recommendation Lookup fields when available, otherwise use main fields
            lookup_diplotype = None
            if col_indices["lookup_diplotype"] >= 0 and col_indices[
                "lookup_diplotype"
            ] < len(row):
                lookup_diplotype = row[col_indices["lookup_diplotype"]]

            lookup_phenotype = None
            if col_indices["lookup_phenotype"] >= 0 and col_indices[
                "lookup_phenotype"
            ] < len(row):
                lookup_phenotype = row[col_indices["lookup_phenotype"]]

            # Use lookup values if available, otherwise use main values
            final_diplotype = lookup_diplotype if lookup_diplotype else diplotype
            final_phenotype = lookup_phenotype if lookup_phenotype else phenotype

            # Extract lookup activity score
            lookup_activity_score = None
            if col_indices["lookup_activity_score"] >= 0 and col_indices[
                "lookup_activity_score"
            ] < len(row):
                try:
                    val = row[col_indices["lookup_activity_score"]]
                    if val and val.strip():
                        lookup_activity_score = float(val)
                except ValueError:
                    logger.warning(
                        f"Invalid lookup activity score value for gene {gene_id}"
                    )

            # If no lookup activity score, use main activity score
            final_activity_score = (
                lookup_activity_score
                if lookup_activity_score is not None
                else activity_score
            )

            # Set activity_score to 2.0 for Normal Metabolizers if not specified
            if final_activity_score is None and (
                "Normal Metabolizer" in final_phenotype
                or "Normal Function" in final_phenotype
            ):
                final_activity_score = 2.0

            gene_entry = {
                "gene": gene_id,
                "diplotype": final_diplotype,
                "phenotype": final_phenotype,
                "activity_score": final_activity_score,
            }

            normalized_data["genes"].append(gene_entry)
            logger.info(f"Added gene from TSV: {gene_entry}")

        # Extract drug recommendations from phenotype data if available
        if phenotype_data:
            drug_recs = extract_drug_recommendations_from_phenotype(phenotype_data)
            normalized_data["drugRecommendations"] = drug_recs
            logger.info(
                f"Added {len(drug_recs)} drug recommendations from phenotype data"
            )

        logger.info(
            f"Successfully parsed {len(normalized_data['genes'])} genes from TSV report"
        )
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
        if "drugRecommendations" in phenotype_data and isinstance(
            phenotype_data["drugRecommendations"], list
        ):
            for drug_rec in phenotype_data["drugRecommendations"]:
                if not isinstance(drug_rec, dict):
                    continue

                # Extract drug name
                drug_name = "Unknown"
                if "drug" in drug_rec:
                    if (
                        isinstance(drug_rec["drug"], dict)
                        and "name" in drug_rec["drug"]
                    ):
                        drug_name = drug_rec["drug"]["name"]
                    else:
                        drug_name = str(drug_rec["drug"])

                # Create normalized drug recommendation
                normalized_rec = {
                    "gene": drug_rec.get("gene", "Multiple"),
                    "drug": drug_name,
                    "drugId": drug_rec.get("drugId", ""),
                    "guideline": drug_rec.get("guidelineName", ""),
                    "recommendation": drug_rec.get(
                        "recommendationText", "See report for details"
                    ),
                    "classification": drug_rec.get("strengthOfEvidence")
                    or drug_rec.get("classification", ""),
                }

                drug_recommendations.append(normalized_rec)
                logger.info(
                    f"Added drug recommendation from phenotype data: {normalized_rec}"
                )

        # Also check for other phenotype data structures
        elif "phenotypes" in phenotype_data:
            # Extract drug recommendations from phenotypes if possible
            for gene_id, gene_data in phenotype_data["phenotypes"].items():
                if "drugRecommendations" in gene_data and isinstance(
                    gene_data["drugRecommendations"], list
                ):
                    for drug_rec in gene_data["drugRecommendations"]:
                        if not isinstance(drug_rec, dict):
                            continue

                        # Extract drug name
                        drug_name = "Unknown"
                        if "drug" in drug_rec:
                            if (
                                isinstance(drug_rec["drug"], dict)
                                and "name" in drug_rec["drug"]
                            ):
                                drug_name = drug_rec["drug"]["name"]
                            else:
                                drug_name = str(drug_rec["drug"])

                        # Create normalized drug recommendation
                        normalized_rec = {
                            "gene": gene_id,
                            "drug": drug_name,
                            "drugId": drug_rec.get("drugId", ""),
                            "guideline": drug_rec.get("guidelineName", ""),
                            "recommendation": drug_rec.get(
                                "recommendationText", "See report for details"
                            ),
                            "classification": drug_rec.get("strengthOfEvidence")
                            or drug_rec.get("classification", ""),
                        }

                        drug_recommendations.append(normalized_rec)
                        logger.info(
                            f"Added drug recommendation from phenotype.phenotypes data: {normalized_rec}"
                        )

        return drug_recommendations
    except Exception as e:
        logger.error(
            f"Error extracting drug recommendations from phenotype data: {str(e)}"
        )
        logger.error(traceback.format_exc())
        return []
