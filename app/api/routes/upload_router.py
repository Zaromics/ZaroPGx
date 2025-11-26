"""
Upload Router - Nextflow-Only Processing

This module handles genomic data uploads and processes them exclusively through Nextflow.
Legacy direct processing has been moved to legacy_processing.py as a backup.
"""

import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy.orm import Session

from app.api.db import SessionLocal, create_patient, get_db, register_genetic_data, save_genomic_header
from app.api.models import (
    FileAnalysis as PydanticFileAnalysis,
    FileType,
    LogLevel,
    StepStatus,
    UploadResponse,
    VCFHeaderInfo,
    WorkflowCreate,
    WorkflowInfo,
    WorkflowLogCreate,
    WorkflowStatus,
    WorkflowStepCreate,
    WorkflowStepUpdate,
    WorkflowUpdate,
)
from app.api.utils.file_processor import FileProcessor
from app.api.utils.header_inspector import inspect_header, extract_raw_header_text, filter_header_to_canonical_contigs
from app.reports.generator import create_interactive_html_report
from app.reports.pdf_generators import generate_pdf_report_dual_lane
from app.services.workflow_progress_calculator import WorkflowProgressCalculator
from app.services.workflow_service import WorkflowService
from app.visualizations.workflow_diagram import (
    render_kroki_mermaid_svg,
    render_simple_png_from_workflow,
    render_workflow,
    render_with_graphviz,
)
from ..utils.security import get_current_user, get_optional_user

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize router
router = APIRouter(
    prefix="/upload",
    tags=["upload"]
)

# Constants
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/data/uploads")
REPORTS_DIR = os.environ.get("REPORT_DIR", "/data/reports")

# Ensure upload directory exists
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Initialize file processor
file_processor = FileProcessor(temp_dir=UPLOAD_DIR)

# Environment variable helper function
def _env_flag(name: str, default: bool = False) -> bool:
    """Helper function to read boolean environment variables."""
    val = os.getenv(name)
    if val is None:
        return default
    return str(val).strip().lower() in {"1", "true", "yes", "on"}

# Report generation flags
INCLUDE_PHARMCAT_HTML = _env_flag("INCLUDE_PHARMCAT_HTML", True)
INCLUDE_PHARMCAT_JSON = _env_flag("INCLUDE_PHARMCAT_JSON", False)
INCLUDE_PHARMCAT_TSV = _env_flag("INCLUDE_PHARMCAT_TSV", False)

# Always use Nextflow for processing (legacy direct processing moved to legacy_processing.py)
USE_NEXTFLOW = True

# Log the configuration for debugging
logger.info(f"PharmCAT Report Configuration - HTML: {INCLUDE_PHARMCAT_HTML}, JSON: {INCLUDE_PHARMCAT_JSON}, TSV: {INCLUDE_PHARMCAT_TSV}")

# Progress calculation is now handled by WorkflowProgressCalculator

async def delayed_cleanup_on_cancellation(workflow_id: str, workflow_metadata: dict):
    """
    Perform delayed cleanup when app container detects cancellation.
    
    This function waits a short period to ensure any in-progress file operations
    complete, then cleans up the reports directory and other files.
    
    Args:
        workflow_id: The workflow ID that was cancelled
        workflow_metadata: Workflow metadata containing file paths
    """
    
    try:
        # Wait a short period to ensure any in-progress operations complete
        await asyncio.sleep(2.0)
        
        patient_id = workflow_metadata.get("patient_id")
        if not patient_id:
            logger.warning(f"No patient_id found in workflow metadata for delayed cleanup of {workflow_id}")
            return
        
        # Define cleanup paths
        cleanup_paths = [
            f"/data/reports/{patient_id}",  # Main output directory
            f"/data/temp/{patient_id}",     # Temporary files
            f"/data/uploads/{patient_id}",  # Uploaded files
            f"/data/results/{patient_id}",  # Results directory
        ]
        
        # Add any additional paths from metadata
        if "output_directory" in workflow_metadata:
            cleanup_paths.append(workflow_metadata["output_directory"])
        if "temp_directory" in workflow_metadata:
            cleanup_paths.append(workflow_metadata["temp_directory"])
        
        # Clean up each path
        for path_str in cleanup_paths:
            try:
                path = Path(path_str)
                if path.exists():
                    logger.info(f"Delayed cleanup: Removing directory {path}")
                    shutil.rmtree(path, ignore_errors=True)
                    logger.info(f"Delayed cleanup: Successfully removed {path}")
                else:
                    logger.debug(f"Delayed cleanup: Path does not exist, skipping {path}")
            except Exception as e:
                logger.warning(f"Delayed cleanup: Failed to remove {path_str}: {e}")
        
        logger.info(f"Delayed cleanup completed for cancelled workflow {workflow_id}")
        
    except Exception as e:
        logger.error(f"Error during delayed cleanup of workflow {workflow_id}: {e}")

async def handle_final_stages_progression(workflow_service: WorkflowService, workflow_id: str, outdir: str):
    """
    Handle the final stages of workflow progression after Nextflow completion.
    
    Args:
        workflow_service: Workflow service instance
        workflow_id: The workflow ID
        outdir: Output directory path
    """
    try:
        # Check for cancellation before starting
        workflow = workflow_service.get_workflow(workflow_id)
        if workflow and workflow.status == "cancelled":
            logger.info(f"Workflow {workflow_id} was cancelled before report generation")
            # Schedule delayed cleanup to ensure any partial files are removed
            task = asyncio.create_task(delayed_cleanup_on_cancellation(workflow_id, workflow.workflow_metadata))
            # Add a name for easier debugging
            task.set_name(f"delayed_cleanup_{workflow_id}")
            return     
        
        # Send initial progress update
        step_update = WorkflowStepUpdate(
            status=StepStatus.RUNNING,
            output_data={"progress_percent": 0}
        )
        workflow_service.update_workflow_step(workflow_id, "report_generation", step_update)
        
        log_data = WorkflowLogCreate(
            step_name="report_generation",
            log_level=LogLevel.INFO,
            message="Generating final reports from Nextflow output"
        )
        workflow_service.log_workflow_event(workflow_id, log_data)
        
        # Get workflow metadata to extract patient and data information
        workflow = workflow_service.get_workflow(workflow_id)
        if not workflow:
            raise RuntimeError(f"Workflow {workflow_id} not found")
        
        metadata = workflow.workflow_metadata or {}
        patient_id = metadata.get("patient_id")
        data_id = metadata.get("data_id")
        workflow_config = metadata.get("workflow", {})
        file_analysis = metadata.get("file_analysis", {})
        
        if not patient_id or not data_id:
            raise RuntimeError(f"Missing patient_id or data_id in workflow metadata")
        
        # Extract sample identifier from workflow metadata
        sample_identifier = None
        if "sample_identifier" in metadata:
            sample_identifier = metadata["sample_identifier"]
        
        # Use the outdir directly as the patient directory (it's already /data/reports/{patient_id})
        patient_dir = Path(outdir)
        patient_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Using patient directory: {patient_dir}")
        
        # Set up all output paths in the patient directory
        pdf_report_path = patient_dir / f"{patient_id}_pgx_report.pdf"
        interactive_html_path = patient_dir / f"{patient_id}_pgx_report_interactive.html"
        pharmcat_html_path = patient_dir / f"{patient_id}_pgx_pharmcat.html"
        pharmcat_json_path = patient_dir / f"{patient_id}_pgx_pharmcat.json"
        pharmcat_tsv_path = patient_dir / f"{patient_id}_pgx_pharmcat.tsv"
        
        # Check for existing PharmCAT outputs in the patient directory
        logger.info(f"Looking for PharmCAT files in: {patient_dir}")
        
        pharmcat_html_exists = pharmcat_html_path.exists()
        pharmcat_json_exists = pharmcat_json_path.exists()
        pharmcat_tsv_exists = pharmcat_tsv_path.exists()
        
        if patient_dir.exists():
            logger.info(f"Patient directory exists, contents: {list(patient_dir.glob('*'))}")
            logger.info(f"PharmCAT files exist - HTML: {pharmcat_html_exists}, JSON: {pharmcat_json_exists}, TSV: {pharmcat_tsv_exists}")
            
            # Log the actual files found for debugging
            pharmcat_pattern = f"{patient_id}_pgx_pharmcat.*"
            pharmcat_files = list(patient_dir.glob(pharmcat_pattern))
            logger.info(f"Found PharmCAT files: {pharmcat_files}")

        # Attempt reconciliation if some PharmCAT outputs are missing
        if not (pharmcat_html_exists and pharmcat_json_exists and pharmcat_tsv_exists):
            try:
                reports_root = patient_dir.parent
                # Try to derive sample base from any pharmcat pipeline log in this dir
                sample_base = None
                logs = list(patient_dir.glob("*_pharmcat_pipeline.log"))
                if logs:
                    sample_base = logs[0].name.replace("_pharmcat_pipeline.log", "")
                    logger.info(f"Derived sample_base from pipeline log: {sample_base}")
                # Candidate alternate directory names to search
                alt_dir_names = []
                if sample_identifier and str(sample_identifier).strip():
                    alt_dir_names.append(str(sample_identifier).strip())
                try:
                    if 'header_sample_identifier' in locals() and locals().get('header_sample_identifier'):
                        alt_dir_names.append(str(locals().get('header_sample_identifier')))
                except Exception:
                    pass
                if sample_base:
                    alt_dir_names.append(sample_base)
                # Deduplicate while preserving order
                seen = set()
                alt_dir_names = [x for x in alt_dir_names if not (x in seen or seen.add(x))]
                for alt_name in alt_dir_names:
                    alt_dir = reports_root / alt_name
                    if not alt_dir.exists():
                        continue
                    # Candidate source files (by patient_id and by alt_name)
                    src_candidates = [
                        (alt_dir / f"{patient_id}_pgx_pharmcat.html", pharmcat_html_path),
                        (alt_dir / f"{patient_id}_pgx_pharmcat.json", pharmcat_json_path),
                        (alt_dir / f"{patient_id}_pgx_pharmcat.tsv", pharmcat_tsv_path),
                        (alt_dir / f"{alt_name}_pgx_pharmcat.html", pharmcat_html_path),
                        (alt_dir / f"{alt_name}_pgx_pharmcat.json", pharmcat_json_path),
                        (alt_dir / f"{alt_name}_pgx_pharmcat.tsv", pharmcat_tsv_path),
                    ]
                    for src, dest in src_candidates:
                        try:
                            if src.exists() and not dest.exists():
                                shutil.copy2(src, dest)
                                logger.info(f"Reconciled PharmCAT output: {src} -> {dest}")
                        except Exception as e:
                            logger.warning(f"Failed to reconcile {src} -> {dest}: {e}")
                    # Refresh state
                    pharmcat_html_exists = pharmcat_html_path.exists()
                    pharmcat_json_exists = pharmcat_json_path.exists()
                    pharmcat_tsv_exists = pharmcat_tsv_path.exists()
                    if pharmcat_html_exists and pharmcat_json_exists:
                        break
            except Exception as e:
                logger.warning(f"PharmCAT output reconciliation encountered an error: {e}")
        
        # Try to load PharmCAT results from the Nextflow output
        pharmcat_data = {"genes": [], "drugRecommendations": []}
        diplotypes = []
        recommendations = []
        
        # Look for PharmCAT JSON results
        pharmcat_json_file = patient_dir / f"{patient_id}_pgx_pharmcat.json"
        pharmcat_run_id = None
        
        if pharmcat_json_file.exists():
            try:
                with open(pharmcat_json_file, 'r', encoding='utf-8') as f:
                    pharmcat_results = json.load(f)
                    if isinstance(pharmcat_results, dict):
                        # Load PharmCAT results into database
                        from app.pharmcat.pharmcat_parser import load_pharmcat_file
                        try:
                            # Pass the workflow_service's database session to ensure consistency
                            pharmcat_run_id = load_pharmcat_file(pharmcat_json_file, workflow_service.db)
                            
                            if pharmcat_run_id:
                                # Link PharmCAT run to workflow
                                workflow_service.link_pharmcat_run(workflow_id, pharmcat_run_id)
                                logger.info(f"Successfully linked PharmCAT run {pharmcat_run_id} to workflow {workflow_id}")
                            else:
                                logger.warning("load_pharmcat_file returned None - database insert may have failed")
                        except Exception as db_error:
                            # Check if it's a database constraint error
                            error_str = str(db_error).lower()
                            if "value too long" in error_str or "varchar" in error_str:
                                logger.error(f"Database constraint error: {db_error}")
                                logger.error("This usually means the database schema needs to be updated.")
                                logger.error("Run migration: db/init/migrations/01_fix_pharmcat_variant_allele_length.sql")
                            else:
                                logger.error(f"Failed to load PharmCAT data into database: {db_error}")
                            # Continue with file-based data fallback
                            pharmcat_run_id = None
                        
                        # PharmCAT JSON has genes directly, not in a "data" object
                        pharmcat_data = pharmcat_results
                        logger.info(f"Loaded PharmCAT results from {pharmcat_json_file}")
                    else:
                        logger.warning(f"PharmCAT JSON file has unexpected structure: {pharmcat_results}")
            except Exception as e:
                logger.error(f"Failed to load PharmCAT JSON results: {e}")
        
        # If JSON is missing or empty, try TSV fallback for simpler extraction
        if (not pharmcat_data.get("genes")):
            try:
                pharmcat_tsv_file = patient_dir / f"{patient_id}_pgx_pharmcat.tsv"
                if pharmcat_tsv_file.exists():
                    from app.reports.pharmcat_tsv_parser import parse_pharmcat_tsv
                    tsv_diplotypes, tsv_recs = parse_pharmcat_tsv(str(pharmcat_tsv_file))
                    if tsv_diplotypes:
                        # Build minimal pharmcat_data structure compatible with downstream formatting
                        pharmcat_data = {"genes": {"CPIC": {}}, "drugRecommendations": []}
                        for entry in tsv_diplotypes:
                            gene = entry.get("gene")
                            if not gene:
                                continue
                            gene_block = pharmcat_data["genes"]["CPIC"].setdefault(gene, {})
                            # Represent TSV-derived diplotype as recommendationDiplotypes shape minimally
                            gene_block.setdefault("recommendationDiplotypes", [])
                            gene_block["recommendationDiplotypes"].append({
                                "allele1": {"name": (entry.get("diplotype") or "").split("/")[0] or "Unknown"},
                                "allele2": {"name": (entry.get("diplotype") or "").split("/")[-1] or "Unknown"},
                                "phenotypes": [entry.get("phenotype") or "Unknown"],
                                "activityScore": entry.get("activity_score")
                            })
                        # Map recommendations if any
                        if tsv_recs:
                            for rec in tsv_recs:
                                pharmcat_data.setdefault("drugRecommendations", []).append({
                                    "drug": rec.get("drug"),
                                    "genes": rec.get("gene"),
                                    "recommendation": rec.get("recommendation"),
                                    "classification": rec.get("classification") or "Unknown"
                                })
                        logger.info(f"Loaded PharmCAT data via TSV fallback with {len(tsv_diplotypes)} diplotypes and {len(tsv_recs)} recommendations")
            except Exception as e:
                logger.warning(f"Failed TSV fallback for PharmCAT parsing: {e}")

        # Extract diplotypes from PharmCAT results
        # PharmCAT can have two formats:
        # 1. NESTED: genes -> {CPIC|DPWG} -> {gene_name} -> {sourceDiplotypes|recommendationDiplotypes}[]
        # 2. FLAT: genes -> {gene_name} -> {sourceDiplotypes|recommendationDiplotypes}[]
        diplotypes = []
        if "genes" in pharmcat_data:
            genes_data = pharmcat_data["genes"]
            
            # Detect format by checking if first key is a guideline source or a gene symbol
            is_nested_format = False
            if genes_data:
                first_key = next(iter(genes_data.keys()))
                if first_key in ["CPIC", "DPWG", "FDA"]:
                    is_nested_format = True
                    logger.info("Detected NESTED PharmCAT format for diplotype extraction")
                else:
                    logger.info("Detected FLAT PharmCAT format for diplotype extraction")
            
            def extract_diplotypes_from_gene(gene_name: str, gene_data: dict, guideline_source: str):
                """Helper to extract diplotypes from gene data."""
                extracted = []
                if not isinstance(gene_data, dict):
                    return extracted
                
                # Prioritize recommendationDiplotypes over sourceDiplotypes to avoid duplication
                diplotype_source = None
                if "recommendationDiplotypes" in gene_data and gene_data["recommendationDiplotypes"]:
                    diplotype_source = "recommendationDiplotypes"
                elif "sourceDiplotypes" in gene_data and gene_data["sourceDiplotypes"]:
                    diplotype_source = "sourceDiplotypes"
                
                if diplotype_source:
                    for diplotype in gene_data[diplotype_source]:
                        if isinstance(diplotype, dict):
                            allele1 = diplotype.get("allele1", {}) or {}
                            allele2 = diplotype.get("allele2", {}) or {}
                            diplotype_name = f"{allele1.get('name', 'Unknown')}/{allele2.get('name', 'Unknown')}"
                            
                            phenotypes = diplotype.get("phenotypes", [])
                            phenotype = phenotypes[0] if phenotypes else "Unknown"
                            
                            activity_score = diplotype.get("activityScore")
                            
                            extracted.append({
                                "gene": gene_name,
                                "diplotype": diplotype_name,
                                "phenotype": phenotype,
                                "activity_score": activity_score,
                                "guideline_source": guideline_source
                            })
                return extracted
            
            if is_nested_format:
                # Nested format: genes -> CPIC/DPWG -> gene_name -> data
                for guideline_source in ["CPIC", "DPWG"]:
                    if guideline_source in genes_data:
                        guideline_genes = genes_data[guideline_source]
                        for gene_name, gene_data in guideline_genes.items():
                            diplotypes.extend(extract_diplotypes_from_gene(gene_name, gene_data, guideline_source))
            else:
                # Flat format: genes -> gene_name -> data
                for gene_name, gene_data in genes_data.items():
                    # Get guideline source from gene data if available
                    guideline_source = gene_data.get("phenotypeSource", "CPIC") if isinstance(gene_data, dict) else "CPIC"
                    diplotypes.extend(extract_diplotypes_from_gene(gene_name, gene_data, guideline_source))
        
        logger.info(f"PharmCAT returned {len(diplotypes)} diplotypes")
        
        # Simple diplotype format conversion if needed
        formatted_diplotypes = []
        for gene in diplotypes:
            if isinstance(gene, dict):
                # Handle different possible data structures
                diplotype_obj = gene.get("diplotype", {})
                diplotype_name = diplotype_obj
                if isinstance(diplotype_obj, dict):
                    diplotype_name = diplotype_obj.get("name", "Unknown")
                
                phenotype_obj = gene.get("phenotype", {})
                phenotype_info = phenotype_obj
                if isinstance(phenotype_obj, dict):
                    phenotype_info = phenotype_obj.get("info", "Unknown")

                # Apply 'Possibly Wild Type' fallback when phenotype is blank/N-A and diplotype is *1/*1
                try:
                    dip_str = str(diplotype_name).strip() if diplotype_name is not None else ""
                    ph_str = str(phenotype_info).strip() if phenotype_info is not None else ""
                    if dip_str == "*1/*1" and (ph_str == "" or ph_str.lower() in {"n/a", "na"}):
                        phenotype_info = "Possibly Wild Type"
                except Exception:
                    pass
                
                # Determine tool source for this gene
                gene_name = gene.get("gene", "")
                file_type = workflow_config.get("file_type", "vcf")
                from app.reports.generator import determine_tool_source
                tool_source = determine_tool_source(gene_name, file_type, workflow_config)
                
                formatted_diplotypes.append({
                    "gene": gene_name,
                    "diplotype": diplotype_name,
                    "phenotype": phenotype_info,
                    "activity_score": diplotype_obj.get("activityScore") if isinstance(diplotype_obj, dict) else None,
                    "tool_source": tool_source
                })
        
        # Log the number of diplotypes found
        logger.info(f"Extracted {len(formatted_diplotypes)} formatted diplotypes")
        
        # Extract recommendations from PharmCAT results with deduplication
        # PharmCAT can have two formats (same as diplotypes):
        # 1. NESTED: genes -> {CPIC|DPWG} -> {gene_name} -> relatedDrugs
        # 2. FLAT: genes -> {gene_name} -> relatedDrugs
        drug_groups = {}  # Group by drug name to avoid duplicates
        
        def extract_drugs_from_gene(gene_name: str, gene_data: dict, guideline_source: str):
            """Helper to extract drug recommendations from gene data."""
            if not isinstance(gene_data, dict):
                return
            if "relatedDrugs" not in gene_data:
                return
            
            for drug in gene_data["relatedDrugs"]:
                if isinstance(drug, dict):
                    drug_name = drug.get("name", "Unknown")
                    
                    # Group by drug name to avoid duplicates
                    if drug_name not in drug_groups:
                        drug_groups[drug_name] = {
                            "drug": drug_name,
                            "genes": [],
                            "recommendations": []
                        }
                    
                    # Add gene if not already present
                    if gene_name not in drug_groups[drug_name]["genes"]:
                        drug_groups[drug_name]["genes"].append(gene_name)
                    
                    # Add recommendation
                    drug_groups[drug_name]["recommendations"].append({
                        "gene": gene_name,
                        "guideline": f"{guideline_source} Guideline for {gene_name} and {drug_name}",
                        "recommendation": f"See {guideline_source} guidelines for specific recommendations",
                        "classification": guideline_source,
                        "guideline_source": guideline_source
                    })
        
        if "genes" in pharmcat_data:
            genes_data = pharmcat_data["genes"]
            
            # Detect format (same logic as diplotypes)
            is_nested_format = False
            if genes_data:
                first_key = next(iter(genes_data.keys()))
                if first_key in ["CPIC", "DPWG", "FDA"]:
                    is_nested_format = True
            
            if is_nested_format:
                # Nested format: genes -> CPIC/DPWG -> gene_name -> data
                for guideline_source in ["CPIC", "DPWG"]:
                    if guideline_source in genes_data:
                        guideline_genes = genes_data[guideline_source]
                        for gene_name, gene_data in guideline_genes.items():
                            extract_drugs_from_gene(gene_name, gene_data, guideline_source)
            else:
                # Flat format: genes -> gene_name -> data
                for gene_name, gene_data in genes_data.items():
                    guideline_source = gene_data.get("phenotypeSource", "CPIC") if isinstance(gene_data, dict) else "CPIC"
                    extract_drugs_from_gene(gene_name, gene_data, guideline_source)
        
        # Convert grouped data to flattened format for compatibility
        formatted_recommendations = []
        for drug_name, drug_data in drug_groups.items():
            for rec in drug_data["recommendations"]:
                formatted_recommendations.append({
                    "gene": rec["gene"],
                    "drug": drug_name,
                    "guideline": rec["guideline"],
                    "recommendation": rec["recommendation"],
                    "classification": rec["classification"],
                    "guideline_source": rec["guideline_source"]
                })
        
        logger.info(f"PharmCAT returned {len(drug_groups)} unique drugs with {len(formatted_recommendations)} total recommendations")
        
        # Update progress: Diagram generation (35% of report generation)
        step_update = WorkflowStepUpdate(
            status=StepStatus.RUNNING,
            output_data={"progress_percent": 35}
        )
        workflow_service.update_workflow_step(workflow_id, "report_generation", step_update)

        # Generate workflow diagrams for this sample
        logger.info("=== WORKFLOW DIAGRAM GENERATION START ===")
        try:
            
            # Determine workflow configuration based on the data
            workflow_config_diagram = {
                "file_type": workflow_config.get("file_type", "vcf"),
                "used_gatk": workflow_config.get("needs_gatk", False),
                "used_hla": workflow_config.get("needs_hla", False),
                "used_pypgx": workflow_config.get("needs_pypgx", False),
                "used_pypgx_bam2vcf": workflow_config.get("needs_pypgx_bam2vcf", False),
                "used_pharmcat": True,
                "used_mtdna": workflow_config.get("needs_mtdna", False),
                "exported_to_fhir": False
            }
            
            logger.info(f"Workflow configuration: {workflow_config_diagram}")
            
            # Generate SVG workflow diagram (true Graphviz renderer for PDF-safe text)
            try:
                svg_bytes = render_with_graphviz(workflow_config_diagram, fmt="svg")
                if svg_bytes:
                    svg_path = patient_dir / f"{patient_id}_workflow.svg"
                    with open(svg_path, "wb") as f_out:
                        f_out.write(svg_bytes)
                    logger.info(f"✓ Graphviz Workflow SVG generated successfully: {svg_path} ({len(svg_bytes)} bytes)")
                else:
                    logger.warning("⚠ Graphviz Workflow SVG generation returned empty result")
            except Exception as e:
                logger.error(f"✗ Graphviz Workflow SVG generation failed: {str(e)}", exc_info=True)
            
            # Generate Kroki Mermaid SVG workflow diagram for comparison
            try:
                kroki_svg_bytes = render_kroki_mermaid_svg(workflow=workflow_config_diagram)
                if kroki_svg_bytes:
                    kroki_svg_path = patient_dir / f"{patient_id}_workflow_kroki_mermaid.svg"
                    with open(kroki_svg_path, "wb") as f_out:
                        f_out.write(kroki_svg_bytes)
                    logger.info(f"✓ Kroki Mermaid Workflow SVG generated successfully: {kroki_svg_path} ({len(kroki_svg_bytes)} bytes)")
                else:
                    logger.warning("⚠ Kroki Mermaid Workflow SVG generation returned empty result")
            except Exception as e:
                logger.error(f"✗ Kroki Mermaid Workflow SVG generation failed: {str(e)}", exc_info=True)
            
            # Generate PNG workflow diagram
            try:
                png_bytes = render_workflow(fmt="png", workflow=workflow_config_diagram)
                if not png_bytes:
                    # Force pure-Python PNG fallback so a file is always present
                    logger.info("PNG generation failed, trying Python fallback...")
                    png_bytes = render_simple_png_from_workflow(workflow_config_diagram)
                if png_bytes:
                    png_path = patient_dir / f"{patient_id}_workflow.png"
                    with open(png_path, "wb") as f_out:
                        f_out.write(png_bytes)
                    logger.info(f"✓ Workflow PNG generated successfully: {png_path} ({len(png_bytes)} bytes)")
                else:
                    logger.warning("⚠ Workflow PNG generation still failed after fallback")
            except Exception as e:
                logger.error(f"✗ Workflow PNG generation failed: {str(e)}", exc_info=True)
            
            logger.info(f"=== WORKFLOW DIAGRAM GENERATION END ===")
        except Exception as e:
            logger.error(f"✗ Workflow diagram generation failed: {str(e)}", exc_info=True)
            logger.info("Continuing without workflow diagrams...")
        
        # Determine effective Sample Identifier for reports
        header_sample_identifier_for_reports = locals().get('header_sample_identifier') or None
        effective_sample_identifier_reports = (
            (str(sample_identifier).strip() if (sample_identifier and str(sample_identifier).strip()) else None)
            or header_sample_identifier_for_reports
            or patient_id
        )

        # Generate reports using the main report generation function with database integration
        logger.info(f"Generating reports using main report generation function")
        
        # Get database session for report generation
        from app.api.db import get_db
        db_session = next(get_db())
        
        # Use the main report generation function with database integration
        from app.reports.generator import generate_report
        response_data = generate_report(
            pharmcat_results={"data": pharmcat_data},
            output_dir=str(patient_dir),
            patient_info={
                "id": patient_id,
                "report_id": data_id,
                "sample_identifier": effective_sample_identifier_reports
            },
            workflow_id=workflow_id,
            db_session=db_session
        )
        
        # Update progress: Reports generated (100% of report generation)
        step_update = WorkflowStepUpdate(
            status=StepStatus.RUNNING,
            output_data={"progress_percent": 100}
        )
        workflow_service.update_workflow_step(workflow_id, "report_generation", step_update)
        
        # Log report generation completion
        logger.info(f"Report generation completed for workflow {workflow_id}")
        logger.info(f"Generated reports: {[k for k, v in response_data.items() if v]}")
        
        # Add provisional flag if the workflow was marked as provisional
        is_provisional = workflow_config.get("is_provisional", False)
        
        # Add additional metadata to response_data from generate_report
        response_data["is_provisional"] = is_provisional
        response_data["job_directory"] = str(patient_dir)
        
        # Add PharmCAT run_id if available
        if pharmcat_run_id:
            response_data["pharmcat_run_id"] = pharmcat_run_id
        
        # Update workflow metadata with reports
        updated_metadata = metadata.copy()
        updated_metadata["reports"] = response_data
        
        # Update the workflow with the new metadata
        workflow_update = WorkflowUpdate(metadata=updated_metadata)
        workflow_service.update_workflow(workflow_id, workflow_update)
        
        # Complete the report generation step
        step_update = WorkflowStepUpdate(
            status=StepStatus.COMPLETED,
            output_data={"reports": response_data, "progress_percent": 100}
        )
        workflow_service.update_workflow_step(workflow_id, "report_generation", step_update)
        
        # Complete the workflow
        workflow_update = WorkflowUpdate(status=WorkflowStatus.COMPLETED)
        workflow_service.update_workflow(workflow_id, workflow_update)
        
        # Broadcast workflow completion with report URLs
        try:
            asyncio.create_task(workflow_service._broadcast_workflow_update(
                str(workflow_id),
                {
                    "workflow_id": str(workflow_id),
                    "status": "completed",
                    "progress_percentage": 100,
                    "current_step": "completed",
                    "message": "Processing complete! - All processing finished",
                    "pdf_report_url": response_data.get("pdf_path"),
                    "html_report_url": response_data.get("html_path"),
                    "interactive_html_report_url": response_data.get("interactive_html_path"),
                    "pharmcat_html_report_url": response_data.get("pharmcat_html_path"),
                    "pharmcat_json_report_url": response_data.get("pharmcat_json_path"),
                    "pharmcat_tsv_report_url": response_data.get("pharmcat_tsv_path")
                }
            ))
        except Exception as e:
            logger.error(f"Failed to broadcast workflow completion with reports: {e}")
        
        log_data = WorkflowLogCreate(
            step_name="workflow_completion",
            log_level=LogLevel.INFO,
            message="Workflow completed successfully with reports generated"
        )
        workflow_service.log_workflow_event(workflow_id, log_data)
        
        logger.info(f"Workflow {workflow_id} completed successfully with reports generated")
        logger.info(f"Generated reports: {list(response_data.keys())}")
        
    except Exception as e:
        logger.error(f"Error in final stages progression for workflow {workflow_id}: {e}")
        workflow_update = WorkflowUpdate(status=WorkflowStatus.FAILED)
        workflow_service.update_workflow(workflow_id, workflow_update)
        
        log_data = WorkflowLogCreate(
            step_name=None,
            log_level=LogLevel.ERROR,
            message=f"Error in final stages: {str(e)}"
        )
        workflow_service.log_workflow_event(workflow_id, log_data)

async def wait_for_nextflow_completion(workflow_service: WorkflowService, workflow_id: str, nextflow_url: str, job_key: str, outdir: str):
    """
    Wait for Nextflow job completion and coordinate with WorkflowProgressCalculator.
    
    This function monitors Nextflow execution and lets individual containers report
    their progress via WorkflowClient. The WorkflowProgressCalculator will handle
    progress calculation based on step status updates from the containers.
    
    Args:
        workflow_service: Workflow service instance
        workflow_id: The workflow ID
        nextflow_url: Nextflow runner URL
        job_key: Nextflow job key
        outdir: Output directory path
    """
    try:
        logger.info(f"Waiting for Nextflow completion for workflow {workflow_id}")
        
        # Log that Nextflow execution has started
        log_data = WorkflowLogCreate(
            step_name="nextflow_executor",
            log_level=LogLevel.INFO,
            message="Nextflow pipeline started - individual containers will report progress"
        )
        workflow_service.log_workflow_event(workflow_id, log_data)
        
        while True:
            try:
                # Check if workflow has been cancelled
                workflow = workflow_service.get_workflow(workflow_id)
                if workflow and workflow.status == "cancelled":
                    logger.info(f"Workflow {workflow_id} was cancelled, stopping Nextflow monitoring")
                    break
                
                # Check Nextflow job status
                response = requests.get(f"{nextflow_url}/status/{job_key}", timeout=30)
                if response.status_code == 200:
                    status_data = response.json()
                    
                    # Log Nextflow status for monitoring purposes
                    status = status_data.get("status", "unknown")
                    message = status_data.get("message", "Processing...")
                    
                    # Only log significant status changes to avoid spam
                    # Only log when status changes or when it's a final status
                    if status in ["completed", "failed", "cancelled"]:
                        log_data = WorkflowLogCreate(
                            step_name="nextflow_executor",
                            log_level=LogLevel.INFO,
                            message=f"Nextflow executor: {message}"
                        )
                        workflow_service.log_workflow_event(workflow_id, log_data)
                    
                    # Check if completed
                    if status_data.get("status") == "completed":
                        logger.info(f"Nextflow job {job_key} completed successfully")
                        
                        # Log that Nextflow execution completed
                        log_data = WorkflowLogCreate(
                            step_name="nextflow_executor",
                            log_level=LogLevel.INFO,
                            message="Nextflow pipeline completed - proceeding to report generation"
                        )
                        workflow_service.log_workflow_event(workflow_id, log_data)
                        
                        # Handle final stages (report generation)
                        await handle_final_stages_progression(workflow_service, workflow_id, outdir)
                        break
                    elif status_data.get("status") == "failed":
                        error_msg = status_data.get("error", "Nextflow job failed")
                        logger.error(f"Nextflow job {job_key} failed: {error_msg}")
                        
                        # Update workflow status to failed
                        workflow_update = WorkflowUpdate(status=WorkflowStatus.FAILED)
                        workflow_service.update_workflow(workflow_id, workflow_update)
                        
                        log_data = WorkflowLogCreate(
                            step_name=None,
                            log_level=LogLevel.ERROR,
                            message=f"Nextflow job failed: {error_msg}"
                        )
                        workflow_service.log_workflow_event(workflow_id, log_data)
                        break
                    elif status_data.get("status") == "cancelled":
                        logger.info(f"Nextflow job {job_key} was cancelled")
                        break
                
                # Wait before next check
                await asyncio.sleep(5)
                
            except requests.RequestException as e:
                logger.warning(f"Error checking Nextflow status: {e}")
                await asyncio.sleep(15)
                
    except Exception as e:
        logger.error(f"Error waiting for Nextflow completion: {e}")
        workflow_update = WorkflowUpdate(status=WorkflowStatus.FAILED)
        workflow_service.update_workflow(workflow_id, workflow_update)
        
        log_data = WorkflowLogCreate(
            step_name=None,
            log_level=LogLevel.ERROR,
            message=f"Error waiting for completion: {str(e)}"
        )
        workflow_service.log_workflow_event(workflow_id, log_data)

async def process_file_nextflow_background_with_db(file_path: str, patient_id: str, data_id: str, workflow: dict, sample_identifier: Optional[str] = None, workflow_id: Optional[str] = None):
    """
    WRAPPER FUNCTION: Creates database session and delegates to core implementation.
    
    This is the function that should be called from background tasks. It handles
    database session lifecycle management and delegates to the core implementation below.
    
    Args:
        file_path: Path to the uploaded file
        patient_id: Patient identifier
        data_id: Genetic data record ID
        workflow: Workflow configuration dictionary
        sample_identifier: Optional sample identifier
        workflow_id: Optional workflow ID for tracking
    """
    db = SessionLocal()
    try:
        await process_file_nextflow_background(file_path, patient_id, data_id, workflow, db, sample_identifier, workflow_id)
    finally:
        db.close()

async def process_file_nextflow_background(file_path: str, patient_id: str, data_id: str, workflow: dict, db: Session, sample_identifier: Optional[str] = None, workflow_id: Optional[str] = None):
    """
    CORE IMPLEMENTATION: Execute the PGx pipeline via the Nextflow runner service.
    
    This function contains the actual workflow logic and requires a database session
    to be passed in. It should NOT be called directly from background tasks - use
    process_file_nextflow_background_with_db() instead.
    
    Args:
        file_path: Path to the uploaded file
        patient_id: Patient identifier
        data_id: Genetic data record ID
        workflow: Workflow configuration dictionary
        db: Database session (must be provided)
        sample_identifier: Optional sample identifier
        workflow_id: Optional workflow ID for tracking
    """
    workflow_service = WorkflowService(db)
    
    try:
        # Get the workflow if workflow_id is provided
        if workflow_id:
            workflow_obj = workflow_service.get_workflow(workflow_id)
            if not workflow_obj:
                logger.error(f"Workflow {workflow_id} not found")
                return
            
            # Check for cancellation before starting
            if workflow_obj.status == "cancelled":
                logger.info(f"Workflow {workflow_id} was cancelled before processing started")
                # Schedule delayed cleanup to ensure any partial files are removed
                task = asyncio.create_task(delayed_cleanup_on_cancellation(workflow_id, workflow_obj.workflow_metadata))
                # Add a name for easier debugging
                task.set_name(f"delayed_cleanup_{workflow_id}")
                return
        else:
            logger.error("No workflow_id provided for background processing")
            return
        
        # Update header analysis step
        step_update = WorkflowStepUpdate(status=StepStatus.RUNNING)
        workflow_service.update_workflow_step(workflow_id, "header_analysis", step_update)
        
        # Inspect file header
        try:
            header_json = inspect_header(file_path)
            header_record_id = save_genomic_header(db, file_path, (workflow.get("file_type") or "UNKNOWN").upper(), header_json)

            # Persist filtered header text (canonical contigs only) into patient reports dir
            try:
                raw_header = extract_raw_header_text(file_path)
                if raw_header is not None:
                    filtered_header = filter_header_to_canonical_contigs(raw_header)
                    patient_dir = Path(os.getenv("REPORT_DIR", "/data/reports")) / str(patient_id)
                    patient_dir.mkdir(parents=True, exist_ok=True)
                    header_txt_path = patient_dir / f"{data_id}.header.txt"
                    with open(header_txt_path, "w", encoding="utf-8") as hf:
                        hf.write(filtered_header)
            except Exception as _header_txt_err:
                logger.debug(f"Header text write skipped due to error: {_header_txt_err}")

            # Derive Sample ID from header if available
            header_sample_identifier = None
            try:
                if isinstance(header_json, dict):
                    samples_list = header_json.get('samples') or []
                    if isinstance(samples_list, list) and samples_list:
                        first_sample = samples_list[0]
                        if isinstance(first_sample, str) and first_sample.strip():
                            header_sample_identifier = first_sample.strip()
            except Exception:
                header_sample_identifier = None
            
            # Complete header analysis step
            step_update = WorkflowStepUpdate(
                status=StepStatus.COMPLETED,
                output_data={"header_record_id": header_record_id}
            )
            workflow_service.update_workflow_step(workflow_id, "header_analysis", step_update)
            
            log_data = WorkflowLogCreate(
                step_name="header_analysis",
                log_level=LogLevel.INFO,
                message="Header analysis completed successfully"
            )
            workflow_service.log_workflow_event(workflow_id, log_data)
            
        except Exception as e:
            logger.error(f"Header analysis failed: {e}")
            step_update = WorkflowStepUpdate(
                status=StepStatus.FAILED,
                error_details={"error": str(e)}
            )
            workflow_service.update_workflow_step(workflow_id, "header_analysis", step_update)
            
            workflow_update = WorkflowUpdate(status=WorkflowStatus.FAILED)
            workflow_service.update_workflow(workflow_id, workflow_update)
            
            log_data = WorkflowLogCreate(
                step_name="header_analysis",
                log_level=LogLevel.ERROR,
                message=f"Header analysis failed: {str(e)}"
            )
            workflow_service.log_workflow_event(workflow_id, log_data)
            return
        
        # Submit to Nextflow
        nextflow_url = os.getenv("NEXTFLOW_RUNNER_URL", "http://nextflow:5055")
        
        try:
            # Determine input type and reference from workflow
            input_type = workflow.get("file_type", "vcf")
            
            # Get reference genome from workflow metadata (already set by file_processor)
            reference = workflow.get("reference", "hg38")
            
            # Determine skip flags based on workflow needs (after user overrides)
            skip_hla = "true" if not workflow.get("needs_hla", False) else "false"
            skip_pypgx = "true" if not workflow.get("needs_pypgx", False) else "false"
            skip_gatk = "true" if not workflow.get("needs_gatk", False) else "false"
            skip_report = "true" if not workflow.get("needs_report", True) else "false"
            
            # Debug logging for service states
            logger.info(f"User toggle states: optitype={workflow.get('optitype_enabled')}, "
                       f"gatk={workflow.get('gatk_enabled')}, pypgx={workflow.get('pypgx_enabled')}, "
                       f"report={workflow.get('report_enabled')}")
            logger.info(f"Workflow needs (after user overrides): needs_hla={workflow.get('needs_hla')}, "
                       f"needs_gatk={workflow.get('needs_gatk')}, needs_pypgx={workflow.get('needs_pypgx')}, "
                       f"needs_report={workflow.get('needs_report')}")
            logger.info(f"Skip flags: skip_hla={skip_hla}, skip_pypgx={skip_pypgx}, "
                       f"skip_gatk={skip_gatk}, skip_report={skip_report}")
            
            # Prepare Nextflow payload matching NextflowRunRequest
            # Compute effective sample identifier precedence:
            # 1) User-entered sample_identifier  2) Header-derived sample  3) None
            effective_sample_identifier = (
                (str(sample_identifier).strip() if (sample_identifier and str(sample_identifier).strip()) else None)
                or header_sample_identifier
            )

            payload = {
                "input": file_path,
                "input_type": input_type,
                "patient_id": patient_id,
                "report_id": patient_id,  # Use patient_id as report_id
                "reference": reference,
                "outdir": f"/data/reports/{patient_id}",
                "job_id": patient_id,
                "skip_hla": skip_hla,
                "skip_pypgx": skip_pypgx,
                "skip_gatk": skip_gatk,
                "skip_report": skip_report,
                "workflow_id": workflow_id,
                "sample_identifier": effective_sample_identifier
            }
            
            # Submit job to Nextflow
            response = requests.post(f"{nextflow_url}/run", json=payload, timeout=30)
            if response.status_code != 200:
                raise RuntimeError(f"Nextflow submission failed: {response.text}")
            
            job_data = response.json()
            job_key = job_data.get("job_key")
            
            if not job_key:
                raise RuntimeError("No job key returned from Nextflow")
            
            logger.info(f"Submitted Nextflow job {job_key} for workflow {workflow_id}")
            
            # Wait for completion
            await wait_for_nextflow_completion(workflow_service, workflow_id, nextflow_url, job_key, job_data.get("outdir", f"/data/reports/{patient_id}"))
            
        except Exception as e:
            logger.error(f"Nextflow execution failed: {e}")
            workflow_update = WorkflowUpdate(status=WorkflowStatus.FAILED)
            workflow_service.update_workflow(workflow_id, workflow_update)
            
            log_data = WorkflowLogCreate(
                step_name=None,
                log_level=LogLevel.ERROR,
                message=f"Nextflow execution failed: {str(e)}"
            )
            workflow_service.log_workflow_event(workflow_id, log_data)
            return
            
    except Exception as e:
        logger.error(f"Error in Nextflow background processing: {e}")
        workflow_update = WorkflowUpdate(status=WorkflowStatus.FAILED)
        workflow_service.update_workflow(workflow_id, workflow_update)
        
        log_data = WorkflowLogCreate(
            step_name=None,
            log_level=LogLevel.ERROR,
            message=f"Background processing error: {str(e)}"
        )
        workflow_service.log_workflow_event(workflow_id, log_data)

@router.post("/genomic-data", response_model=UploadResponse)
async def upload_genomic_data(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    sample_identifier: Optional[str] = Form(None),
    reference_genome: Optional[str] = Form("hg38"),
    optitype_enabled: Optional[str] = Form(None),
    gatk_enabled: Optional[str] = Form(None),
    pypgx_enabled: Optional[str] = Form(None),
    report_enabled: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    """
    Upload genomic data files for pharmacogenomic analysis.
    
    This endpoint handles the upload of genomic data files (VCF, BAM, CRAM, SAM, FASTQ)
    and initiates the Nextflow-based processing pipeline.
    
    Supported file types:
    - VCF: Direct processing through PyPGx and PharmCAT. If GRCh37/hg19 reference genome is detected, bcftools liftover will be used to convert.
    - BAM/CRAM/SAM: BAM is processed by hlatyping then PyPGx, then PharmCAT. CRAM/SAM processed through GATK first for conversion to BAM.
    - FASTQ: Processed by hlatyping, then GATK, then PyPGx and PharmCAT
    - 23andMe/BED: Not yet supported, requires conversion to VCF (future implementation)
    
    The system automatically detects and uses index files (.bai, .crai, .csi, .tbi, .idx) when provided.
    Currently only hg38/GRCh38 reference genome is fully supported.
    """
    try:
        # Generate unique identifiers
        file_id = str(uuid.uuid4())
        patient_id = str(uuid.uuid4())
        
        # Process uploaded files
        result = await file_processor.process_files(
            files, 
            reference_genome, 
            optitype_enabled=optitype_enabled,
            gatk_enabled=gatk_enabled,
            pypgx_enabled=pypgx_enabled,
            report_enabled=report_enabled
        )
        
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        
        # Create patient record
        patient_identifier = sample_identifier if sample_identifier else patient_id
        actual_patient_id = create_patient(db, patient_identifier)
        
        # Register genetic data
        primary_file_path = result["file_paths"][0]
        file_analysis = result["file_analysis"]
        data_id = register_genetic_data(
            db, 
            actual_patient_id,  # Use the actual patient ID returned from create_patient
            file_analysis.file_type.value,  # file_type
            primary_file_path,  # file_path
            False  # is_supplementary (boolean)
        )
        
        # Create workflow
        workflow_service = WorkflowService(db)
        workflow = workflow_service.create_workflow(
            WorkflowCreate(
                name=f"Genomic Analysis - {sample_identifier or 'Unknown Sample'}",
                description=f"Pharmacogenomic analysis workflow for {file_analysis.file_type.value} file",
                total_steps=5,  # header_analysis, hla_typing, pypgx_analysis, pharmcat_analysis, report_generation
                metadata={
                    "patient_id": actual_patient_id,
                    "data_id": data_id,
                    "workflow_type": "genomic_analysis",
                    "file_paths": result["file_paths"],
                    "workflow": result["workflow"],
                    "file_analysis": {
                        "file_type": file_analysis.file_type.value,
                        "is_compressed": file_analysis.is_compressed,
                        "has_index": file_analysis.has_index,
                        "file_size": file_analysis.file_size,
                        "error": file_analysis.error,
                        "is_valid": file_analysis.is_valid,
                        "validation_errors": file_analysis.validation_errors,
                        "vcf_info": file_analysis.vcf_info.__dict__ if file_analysis.vcf_info else None
                    }
                }
            )
        )
        
        # Create workflow steps based on service toggle states
        step_order = 1
        
        # Add header analysis step (file upload progress is handled by frontend)
        workflow_service.add_workflow_step(
            workflow.id,
            WorkflowStepCreate(
                step_name="header_analysis",
                step_order=step_order,
                container_name="header_inspector"
            )
        )
        step_order += 1
        
        # Add HLA typing step only if workflow needs it AND user hasn't disabled it
        if result["workflow"].get("needs_hla", False):
            workflow_service.add_workflow_step(
                workflow.id,
                WorkflowStepCreate(
                    step_name="hla_typing",
                    step_order=step_order,
                    container_name="hlatyping"
                )
            )
            step_order += 1
        
        # Add PyPGx BAM→VCF conversion step only if workflow needs it AND user hasn't disabled it
        if result["workflow"].get("needs_pypgx_bam2vcf", False):
            workflow_service.add_workflow_step(
                workflow.id,
                WorkflowStepCreate(
                    step_name="pypgx_bam2vcf",
                    step_order=step_order,
                    container_name="pypgx"
                )
            )
            step_order += 1
        
        # Add PyPGx analysis step only if workflow needs it AND user hasn't disabled it
        if result["workflow"].get("needs_pypgx", False):
            workflow_service.add_workflow_step(
                workflow.id,
                WorkflowStepCreate(
                    step_name="pypgx_analysis",
                    step_order=step_order,
                    container_name="pypgx"
                )
            )
            step_order += 1
        
        # Always add PharmCAT analysis step (required for core functionality)
        workflow_service.add_workflow_step(
            workflow.id,
            WorkflowStepCreate(
                step_name="pharmcat_analysis",
                step_order=step_order,
                container_name="pharmcat"
            )
        )
        step_order += 1
        
        # Add report generation step only if workflow needs it AND user hasn't disabled it
        if result["workflow"].get("needs_report", True):  # Reports are available by default
            workflow_service.add_workflow_step(
                workflow.id,
                WorkflowStepCreate(
                    step_name="report_generation",
                    step_order=step_order,
                    container_name="report_generator"
                )
            )
            step_order += 1
        
        # Start the workflow
        workflow_service.update_workflow(
            workflow.id,
            WorkflowUpdate(status=WorkflowStatus.RUNNING)
        )
        
        # Schedule background processing: Always use Nextflow
        background_tasks.add_task(
            process_file_nextflow_background_with_db,
            primary_file_path,
            str(actual_patient_id),
            str(data_id),
            result["workflow"],
            str(sample_identifier).strip() if (sample_identifier and sample_identifier.strip()) else None,
            str(workflow.id)  # Pass workflow ID
        )
        
        # Convert dataclass to Pydantic model
        file_analysis = result["file_analysis"]
        vcf_info = None
        if file_analysis.vcf_info:
            vcf_info = VCFHeaderInfo(
                reference_genome=file_analysis.vcf_info.reference_genome,
                sequencing_platform=file_analysis.vcf_info.sequencing_platform,
                sequencing_profile=file_analysis.vcf_info.sequencing_profile,
                has_index=file_analysis.vcf_info.has_index,
                is_bgzipped=file_analysis.vcf_info.is_bgzipped,
                contigs=file_analysis.vcf_info.contigs,
                sample_count=file_analysis.vcf_info.sample_count,
                variant_count=file_analysis.vcf_info.variant_count
            )
        
        # Create workflow info (include recommendations/warnings for UI display)
        workflow_info = WorkflowInfo(
            workflow_type=result["workflow"]["workflow_type"],
            file_type=FileType(result["workflow"]["file_type"]),
            needs_alignment=result["workflow"].get("needs_alignment", False),
            needs_gatk=result["workflow"].get("needs_gatk", False),
            needs_pypgx=result["workflow"].get("needs_pypgx", False),
            needs_pharmcat=result["workflow"].get("needs_pharmcat", True),
            reference_genome=result["workflow"].get("reference", "hg38"),
            is_provisional=result["workflow"].get("is_provisional", False),
            recommendations=result["workflow"].get("recommendations", []),
            warnings=result["workflow"].get("warnings", [])
        )
        
        # Create response
        response = UploadResponse(
            file_id=str(data_id),  # Use data_id as file_id for backward compatibility
            job_id=str(workflow.id),  # Use workflow ID as job_id for backward compatibility
            file_type=result["workflow"]["file_type"],
            status="processing",
            message="Files uploaded successfully. Processing started.",
            analysis_info=PydanticFileAnalysis(
                file_type=FileType(result["workflow"]["file_type"]),
                is_compressed=file_analysis.is_compressed,
                has_index=file_analysis.has_index,
                file_size=file_analysis.file_size,
                vcf_info=vcf_info,
                is_valid=file_analysis.is_valid,
                validation_errors=file_analysis.validation_errors
            ),
            workflow=workflow_info
        )
        
        logger.info(f"Upload successful for patient {patient_id}, workflow {workflow.id}")
        return response
        
    except Exception as e:
        logger.error(f"Upload failed: {e}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

@router.get("/status/{job_id}")
async def get_upload_status(job_id: str, db: Session = Depends(get_db)):
    """
    Get the processing status of a job using the new monitoring system.
    This endpoint works with both job_id and workflow_id for backward compatibility.
    """
    try:
        workflow_service = WorkflowService(db)
        
        # Try to get workflow by ID
        workflow = workflow_service.get_workflow(job_id)
        if not workflow:
            raise HTTPException(status_code=404, detail="Workflow not found")
        
        # Get workflow steps
        steps = workflow_service.get_workflow_steps(job_id)
        
        # Convert steps to dictionary format for progress calculator
        steps_dict = [
            {
                "step_name": step.step_name,
                "status": step.status,  # status is already a string from database
                "step_order": step.step_order,
                "container_name": step.container_name,
                "output_data": step.output_data,  # Include output_data for container progress
                "metadata": step.metadata  # Include metadata for container progress
            }
            for step in steps
        ]
        
        # Get workflow metadata for configuration
        metadata = workflow.workflow_metadata or {}
        workflow_config = metadata.get("workflow", {})
        
        # Calculate progress using centralized calculator
        progress_calculator = WorkflowProgressCalculator()
        progress_info = progress_calculator.calculate_progress_from_steps(steps_dict, workflow_config, job_id)
        
        progress = progress_info.progress_percentage
        current_stage = progress_info.stage.value
        
        # Get workflow logs
        logs = workflow_service.get_workflow_logs(job_id)
        latest_message = progress_info.message
        
        # Extract report URLs from metadata for completed workflows
        report_urls = {}
        if workflow.status == "completed" and metadata.get("reports"):
            reports = metadata["reports"]
            logger.info(f"Found report data in workflow metadata: {list(reports.keys())}")
            
            # Extract all report URLs to top level for frontend compatibility
            if "pdf_report_url" in reports:
                report_urls["pdf_report_url"] = reports["pdf_report_url"]
            if "html_report_url" in reports:
                report_urls["html_report_url"] = reports["html_report_url"]
            if "interactive_html_report_url" in reports:
                report_urls["interactive_html_report_url"] = reports["interactive_html_report_url"]
            if "pharmcat_html_report_url" in reports:
                report_urls["pharmcat_html_report_url"] = reports["pharmcat_html_report_url"]
            if "pharmcat_json_report_url" in reports:
                report_urls["pharmcat_json_report_url"] = reports["pharmcat_json_report_url"]
            if "pharmcat_tsv_report_url" in reports:
                report_urls["pharmcat_tsv_report_url"] = reports["pharmcat_tsv_report_url"]
        
        # Create response
        response = {
            "job_id": job_id,
            "status": workflow.status,  # status is already a string from database
            "progress": progress,
            "message": latest_message,
            "current_stage": current_stage,
            "data": {
                "workflow_id": workflow.id,
                "patient_id": metadata.get("patient_id"),
                "data_id": workflow.data_id,
                "steps": [
                    {
                        "name": step.step_name,
                        "status": step.status,  # status is already a string from database
                        "order": step.step_order,
                        "container": step.container_name
                    }
                    for step in steps
                ]
            },
            **report_urls  # Include report URLs at top level
        }
        
        logger.info(f"Status response for job {job_id}: {response}")
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting status for job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting status: {str(e)}")

@router.post("/inspect-header")
async def inspect_file_header(
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """
    Inspect the header of a genomic file without processing the full analysis.
    This endpoint allows users to preview file header information before
    committing to the full upload and analysis process.
    """
    try:
        # Save uploaded file temporarily
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=f"_{file.filename}")
        try:
            content = await file.read()
            temp_file.write(content)
            temp_file.close()
            
            # Inspect header
            header_info = inspect_header(temp_file.name)

            # Derive workflow analysis using the same backend logic (no Nextflow)
            compat_workflow = {
                "recommendations": [],
                "warnings": [],
                "unsupported": False,
                "unsupported_reason": None
            }
            try:
                workflow_result = await file_processor.process_upload(str(temp_file.name))
                if workflow_result.get("status") == "success":
                    wf = workflow_result.get("workflow", {})
                    compat_workflow = {
                        "recommendations": wf.get("recommendations", []),
                        "warnings": wf.get("warnings", []),
                        "unsupported": wf.get("unsupported", False),
                        "unsupported_reason": wf.get("unsupported_reason")
                    }
            except Exception as e:
                logger.debug(f"Header inspect workflow derivation failed: {e}")

            return {
                "status": "success",
                "success": True,
                "filename": file.filename,
                "file_size": len(content),
                "header_info": header_info,
                "compat": {"workflow": compat_workflow}
            }
            
        finally:
            # Clean up temp file
            if os.path.exists(temp_file.name):
                os.unlink(temp_file.name)
                
    except Exception as e:
        logger.error(f"Header inspection failed: {e}")
        raise HTTPException(status_code=500, detail=f"Header inspection failed: {str(e)}")

@router.get("/reports/job/{job_id}")
async def get_report_urls(job_id: str, db: Session = Depends(get_db)):
    """
    Get the report URLs for a completed job.
    """
    try:
        workflow_service = WorkflowService(db)
        
        # First try to get workflow by ID (in case job_id is actually a workflow_id)
        workflow = workflow_service.get_workflow(job_id)
        
        # If not found by ID, try to find by name pattern
        if not workflow:
            # Look for workflow with name containing the job_id
            from sqlalchemy import and_
            from app.api.db import Workflow
            workflow = db.query(Workflow).filter(
                and_(
                    Workflow.name.contains(job_id),
                    Workflow.status == WorkflowStatus.COMPLETED
                )
            ).first()
        
        if not workflow:
            raise HTTPException(status_code=404, detail="Workflow not found")
        
        if workflow.status != WorkflowStatus.COMPLETED:
            raise HTTPException(status_code=400, detail="Workflow not completed")
        
        # Get report URLs from metadata
        metadata = workflow.workflow_metadata or {}
        reports = metadata.get("reports", {})
        
        # If no reports in metadata, try to construct URLs from patient_id
        if not reports:
            patient_id = metadata.get("patient_id")
            if patient_id:
                # Construct basic report URLs based on standard naming convention
                reports = {
                    "pdf_report_url": f"/reports/{patient_id}/{patient_id}_pgx_report.pdf",
                    "html_report_url": f"/reports/{patient_id}/{patient_id}_pgx_report_interactive.html"
                }
                
                # Check if PharmCAT reports exist and add them
                patient_dir = Path(REPORTS_DIR) / patient_id
                if patient_dir.exists():
                    pharmcat_html = patient_dir / f"{patient_id}_pgx_pharmcat.html"
                    pharmcat_json = patient_dir / f"{patient_id}_pgx_pharmcat.json"
                    pharmcat_tsv = patient_dir / f"{patient_id}_pgx_pharmcat.tsv"
                    
                    if pharmcat_html.exists():
                        reports["pharmcat_html_report_url"] = f"/reports/{patient_id}/{pharmcat_html.name}"
                    if pharmcat_json.exists():
                        reports["pharmcat_json_report_url"] = f"/reports/{patient_id}/{pharmcat_json.name}"
                    if pharmcat_tsv.exists():
                        reports["pharmcat_tsv_report_url"] = f"/reports/{patient_id}/{pharmcat_tsv.name}"
        
        return {
            "job_id": job_id,
            "workflow_id": str(workflow.id),
            "status": "completed",
            "reports": reports
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting report URLs for job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting report URLs: {str(e)}")

@router.get("/reports/download/{patient_id}")
async def download_all_reports(patient_id: str, current_user: str = Depends(get_optional_user)):
    """
    Download all reports for a patient as a ZIP file.
    """
    try:
        # Use the same path resolution as individual file serving
        from app.main import REPORTS_DIR
        reports_dir = REPORTS_DIR / patient_id
        
        # Security check: ensure the path is within the reports directory
        try:
            reports_dir = reports_dir.resolve()
            reports_base = REPORTS_DIR.resolve()
            if not str(reports_dir).startswith(str(reports_base)):
                raise HTTPException(status_code=403, detail="Access denied")
        except Exception:
            raise HTTPException(status_code=403, detail="Invalid file path")
        
        # Check if directory exists
        if not reports_dir.exists():
            raise HTTPException(status_code=404, detail="Reports directory not found")
        
        # Create ZIP file
        zip_buffer = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")

        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            files_found = list(reports_dir.rglob("*"))
            logger.info(f"ZIP Download - Found {len(files_found)} files/directories in {reports_dir}")

            for file_path in files_found:
                if file_path.is_file():
                    # Add file to ZIP with relative path
                    arcname = file_path.relative_to(reports_dir)
                    logger.info(f"ZIP Download - Adding file: {file_path.name}")
                    zip_file.write(file_path, arcname)

        zip_buffer.close()

        # Read ZIP file content
        with open(zip_buffer.name, 'rb') as f:
            zip_content = f.read()

        # Clean up
        os.unlink(zip_buffer.name)

        # Return ZIP file
        return Response(
            content=zip_content,
            media_type="application/zip",
            headers={
                "Content-Disposition": f"attachment; filename=reports_{patient_id}.zip"
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating ZIP file for patient {patient_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error creating ZIP file: {str(e)}")
        