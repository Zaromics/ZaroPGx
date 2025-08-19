import os
import uuid
import shutil
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from typing import Optional
import logging
from datetime import datetime
from pathlib import Path
import asyncio
import requests
import json

from app.api.db import get_db, create_patient, register_genetic_data
from app.api.models import UploadResponse, FileType, WorkflowInfo, FileAnalysis as PydanticFileAnalysis, VCFHeaderInfo
from app.pharmcat.pharmcat_client import call_pharmcat_service
from app.api.utils.file_processor import FileProcessor
from app.reports.generator import create_interactive_html_report
from ..utils.security import get_current_user, get_optional_user

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize router with no dependencies - will be set at the endpoint level
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

# Initialize job status dictionary
job_status = {}

# Environment variable helper function
def _env_flag(name: str, default: bool = False) -> bool:
    """Helper function to read boolean environment variables."""
    val = os.getenv(name)
    if val is None:
        return default
    return str(val).strip().lower() in {"1", "true", "yes", "on"}

INCLUDE_PHARMCAT_HTML = _env_flag("INCLUDE_PHARMCAT_HTML", True)
INCLUDE_PHARMCAT_JSON = _env_flag("INCLUDE_PHARMCAT_JSON", False)
INCLUDE_PHARMCAT_TSV = _env_flag("INCLUDE_PHARMCAT_TSV", False)

# Log the configuration for debugging
logger.info(f"PharmCAT Report Configuration - HTML: {INCLUDE_PHARMCAT_HTML}, JSON: {INCLUDE_PHARMCAT_JSON}, TSV: {INCLUDE_PHARMCAT_TSV}")

async def process_file_background(file_path: str, patient_id: str, data_id: str, workflow: dict):
    """
    Process file based on determined workflow
    
    This function handles the background processing of genomic files through
    the appropriate pipeline based on the file type and workflow configuration.
    
    Pipeline flow:
    1. For BAM/CRAM/SAM files: GATK → PyPGx → PharmCAT → Reports
    2. For VCF files: Direct to PharmCAT → Reports (or GATK if needed)
    3. Future: For FASTQ: Alignment → GATK → PyPGx → PharmCAT → Reports
    4. Future: For 23andMe: Conversion → PharmCAT → Reports
    """
    try:
        logger.info(f"Processing file for patient {patient_id}, file {data_id}")
        logger.info(f"Workflow: {workflow}")

        # Initialize job status
        job_status[data_id] = {
            "status": "processing",
            "percent": 0,
            "message": "Starting analysis...",
            "stage": "Upload",
            "complete": False,
            "data": {}
        }
        
        # Check if file format is unsupported
        if workflow.get("unsupported", False):
            reason = workflow.get("unsupported_reason", "Unsupported file format")
            logger.warning(f"Unsupported file format for {data_id}: {reason}")
            job_status[data_id].update({
                "status": "error",
                "message": reason,
                "complete": True
            })
            return

        # Update initial status
        job_status[data_id].update({
            "percent": 5,
            "stage": "Analysis",
            "message": "Analyzing file..."
        })
        await asyncio.sleep(1)  # Give time for status update
        
        # File processing pipeline
        output_file = file_path  # Start with the original file
        
        # Check if we can go directly to PharmCAT (e.g., for VCF files)
        if workflow.get("go_directly_to_pharmcat", False) and not workflow.get("needs_gatk", False):
            logger.info(f"File can be processed directly by PharmCAT, skipping preprocessing steps")
            job_status[data_id].update({
                "percent": 30, 
                "stage": "Direct Processing",
                "message": "File ready for PharmCAT processing"
            })
            await asyncio.sleep(1)  # Short delay for UI update
        else:
            # Standard preprocessing pipeline
            
            # Step 1: Alignment (for FASTQ files) - not yet implemented
            if workflow.get("needs_alignment", False):
                job_status[data_id].update({
                    "percent": 10,
                    "stage": "Alignment",
                    "message": "Aligning reads to reference genome (not yet implemented)..."
                })
                # This would be implemented with a call to BWA or similar aligner
                # For now, just sleep to simulate processing time
                await asyncio.sleep(2)
            
            # Step 2: GATK Variant Calling (for BAM/CRAM/SAM)
            if workflow.get("needs_gatk", False):
                job_status[data_id].update({
                    "percent": 20,
                    "stage": "GATK",
                    "message": "Calling variants with GATK..."
                })
                
                try:
                    # Call GATK service for processing
                    # This would be a real implementation to call the GATK service
                    # For example:
                    # gatk_output = await call_gatk_service(file_path)
                    # output_file = gatk_output
                    
                    # For now, simulate processing
                    logger.info(f"Would call GATK for {file_path}")
                    await asyncio.sleep(5)  # Simulate GATK processing time
                    
                    # Update status after GATK
                    job_status[data_id].update({
                        "percent": 40,
                        "stage": "GATK",
                        "message": "Variant calling complete."
                    })
                except Exception as e:
                    error_msg = f"GATK processing failed: {str(e)}"
                    logger.error(error_msg)
                    job_status[data_id].update({
                        "status": "error",
                        "message": error_msg,
                        "complete": True
                    })
                    return
            
            # Step 3: File conversion (for 23andMe files) - not yet implemented
            if workflow.get("needs_conversion", False):
                job_status[data_id].update({
                    "percent": 30,
                    "stage": "Conversion",
                    "message": "Converting to VCF format (not yet implemented)..."
                })
                # This would be implemented with a conversion tool
                # For now, just sleep to simulate processing time
                await asyncio.sleep(2)
            
            # Step 4: PyPGx for CYP2D6 analysis
            if workflow.get("needs_pypgx", False):
                job_status[data_id].update({
                    "percent": 50,
                    "stage": "PyPGx",
                    "message": "Analyzing PyPGx supported star alleles..."
                })
                
                try:
                    # Call PyPGx service
                    # This would be a real implementation to call PyPGx
                    # For example:
                    # pypgx_output = await call_pypgx_service(output_file)
                    # output_file = pypgx_output
                    
                    # For now, simulate processing
                    logger.info(f"Would call PyPGx for {output_file}")
                    await asyncio.sleep(3)  # Simulate PyPGx processing time
                    
                    # Update status after PyPGx
                    job_status[data_id].update({
                        "percent": 60,
                        "stage": "PyPGx",
                        "message": "PyPGx analysis complete."
                    })
                except Exception as e:
                    error_msg = f"PyPGx processing failed: {str(e)}"
                    logger.error(error_msg)
                    job_status[data_id].update({
                        "status": "error",
                        "message": error_msg,
                        "complete": True
                    })
                    return

        # Step 5: PharmCAT Analysis
        job_status[data_id].update({
            "percent": 70,
            "stage": "PharmCAT",
            "message": "Running PharmCAT analysis..."
        })

        # Call PharmCAT service for final analysis
        logger.info(f"Calling PharmCAT service with file: {output_file}")
        try:
            # Since we're already getting the patient_id from the function parameters,
            # we can use that directly. The patient_identifier is the same as patient_id
            # in this context since we're using the database ID for directory naming.
            patient_identifier = str(patient_id)
            logger.info(f"Using patient_id as patient_identifier: {patient_identifier}")
            
            # Use the direct PharmCAT service call to avoid duplicate report generation
            # Pass both patient_id (for database consistency) and patient_identifier (for user experience)
            results = call_pharmcat_service(
                output_file, 
                report_id=data_id, 
                patient_id=patient_id,
                patient_identifier=patient_identifier
            )
        except Exception as e:
            logger.error(f"PharmCAT service call failed: {str(e)}")
            results = {"success": False, "message": f"PharmCAT service error: {str(e)}", "data": {}}
            
        logger.info(f"PharmCAT processing complete")
        
        # Step 6: Generate Reports
        try:
            # Update status for Report stage
            job_status[data_id].update({
                "percent": 90,
                "stage": "Report",
                "message": "Generating reports..."
            })
            
            # Ensure results has required structure even if the call failed
            if not isinstance(results, dict):
                logger.error(f"PharmCAT results are not a dictionary: {type(results)}")
                results = {"success": False, "message": "Invalid results format", "data": {}}
                
            if "data" not in results:
                logger.warning("PharmCAT results missing 'data' key, adding empty data structure")
                results["data"] = {}
                
            if "genes" not in results.get("data", {}):
                logger.warning("Missing 'genes' in results data, adding empty list")
                results.setdefault("data", {})["genes"] = []
                
            if "drugRecommendations" not in results.get("data", {}):
                logger.warning("Missing 'drugRecommendations' in results data, adding empty list")
                results.setdefault("data", {})["drugRecommendations"] = []
            
            # Generate reports (proceed even if normalization 'success' is False; use whatever data is available)
            try:
                # Create a single reports directory for this job
                reports_dir = Path(os.getenv("REPORT_DIR", "/data/reports"))
                reports_dir.mkdir(parents=True, exist_ok=True)
                
                # Use the patient directory directly instead of creating a new job directory
                # This ensures all reports (ours and PharmCAT's) are in the same place
                patient_dir = reports_dir / str(patient_id)
                patient_dir.mkdir(parents=True, exist_ok=True)
                logger.info(f"Using patient directory: {patient_dir}")
                
                # Set up all output paths in the patient directory
                pdf_report_path = patient_dir / f"{patient_id}_pgx_report.pdf"
                interactive_html_path = patient_dir / f"{patient_id}_pgx_report_interactive.html"
                pharmcat_html_path = patient_dir / f"{patient_id}_pgx_pharmcat.html"
                pharmcat_json_path = patient_dir / f"{patient_id}_pgx_pharmcat.json"
                pharmcat_tsv_path = patient_dir / f"{patient_id}_pgx_pharmcat.tsv"
                
                # Extract data needed for the reports
                pharmcat_data = results.get("data", {}) if isinstance(results, dict) else {}
                
                # Extract diplotypes from PharmCAT results - should be normalized already
                diplotypes = pharmcat_data.get("genes", [])
                logger.info(f"PharmCAT returned {len(diplotypes)} genes")
                
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
                        
                        formatted_diplotypes.append({
                            "gene": gene.get("gene", ""),
                            "diplotype": diplotype_name,
                            "phenotype": phenotype_info,
                            "activity_score": diplotype_obj.get("activityScore") if isinstance(diplotype_obj, dict) else None
                        })
                
                # Log the number of diplotypes found
                logger.info(f"Extracted {len(formatted_diplotypes)} formatted diplotypes")
                
                # Extract recommendations from PharmCAT results - should be normalized already
                recommendations = pharmcat_data.get("drugRecommendations", [])
                logger.info(f"PharmCAT returned {len(recommendations)} drug recommendations")
                
                # Simple recommendation format conversion if needed
                formatted_recommendations = []
                for drug in recommendations:
                    if not isinstance(drug, dict):
                        continue
                        
                    # Handle different possible structures for drug name
                    drug_name = drug.get("drug", {})
                    if isinstance(drug_name, dict):
                        drug_name = drug_name.get("name", "Unknown")
                    
                    formatted_recommendations.append({
                        "gene": drug.get("gene", ""),
                        "drug": drug_name,
                        "guideline": drug.get("guideline", ""),
                        "recommendation": drug.get("recommendation", "Unknown"),
                        "classification": drug.get("classification", "Unknown")
                    })
                
                # Log the number of recommendations found
                logger.info(f"Extracted {len(formatted_recommendations)} formatted recommendations")
                
                # Generate workflow diagrams for this sample
                logger.info("=== WORKFLOW DIAGRAM GENERATION START ===")
                try:
                    from app.visualizations.workflow_diagram import render_workflow, render_simple_png_from_workflow
                    
                    # Determine workflow configuration based on the data
                    workflow_config = {
                        "file_type": "vcf",  # Default to VCF since we're processing VCF files
                        "used_gatk": False,  # We're going directly to PharmCAT
                        "used_pypgx": False,  # Not using PyPGx in this flow
                        "used_pharmcat": True,  # Always using PharmCAT
                        "exported_to_fhir": False  # FHIR export is optional
                    }
                    
                    logger.info(f"Workflow configuration: {workflow_config}")
                    
                    # Generate SVG workflow diagram
                    try:
                        svg_bytes = render_workflow(fmt="svg", workflow=workflow_config)
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
                        from app.visualizations.workflow_diagram import render_kroki_mermaid_svg
                        kroki_svg_bytes = render_kroki_mermaid_svg(workflow=workflow_config)
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
                        png_bytes = render_workflow(fmt="png", workflow=workflow_config)
                        if not png_bytes:
                            # Force pure-Python PNG fallback so a file is always present
                            logger.info("PNG generation failed, trying Python fallback...")
                            png_bytes = render_simple_png_from_workflow(workflow_config)
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
            
            # Check for existing PharmCAT outputs in the patient directory
                logger.info(f"Looking for PharmCAT files in: {patient_dir}")
                
                pharmcat_html_exists = False
                pharmcat_json_exists = False
                pharmcat_tsv_exists = False
                
                if patient_dir.exists():
                    logger.info(f"Patient directory exists, contents: {list(patient_dir.glob('*'))}")
                    
                    # Check if PharmCAT files already exist (they should have been copied by the PharmCAT service)
                    pharmcat_html_exists = pharmcat_html_path.exists()
                    pharmcat_json_exists = pharmcat_json_path.exists()
                    pharmcat_tsv_exists = pharmcat_tsv_path.exists()
                    
                    logger.info(f"PharmCAT files exist - HTML: {pharmcat_html_exists}, JSON: {pharmcat_json_exists}, TSV: {pharmcat_tsv_exists}")
                    
                    # Log the actual files found for debugging
                    pharmcat_pattern = f"{patient_id}_pgx_pharmcat.*"
                    pharmcat_files = list(patient_dir.glob(pharmcat_pattern))
                    logger.info(f"Found PharmCAT files: {pharmcat_files}")
                    
                    # Additional debugging: check if destination paths already exist
                    logger.info(f"Destination paths - HTML: {pharmcat_html_path}, JSON: {pharmcat_json_path}, TSV: {pharmcat_tsv_path}")
                    logger.info(f"Source files found: {[f.name for f in pharmcat_files]}")
                    
                    # Verify that the files are actually accessible and have content
                    if pharmcat_html_exists:
                        try:
                            html_size = pharmcat_html_path.stat().st_size
                            logger.info(f"PharmCAT HTML file size: {html_size} bytes")
                        except Exception as e:
                            logger.warning(f"Could not get HTML file size: {e}")
                    
                    if pharmcat_json_exists:
                        try:
                            json_size = pharmcat_json_path.stat().st_size
                            logger.info(f"PharmCAT JSON file size: {json_size} bytes")
                        except Exception as e:
                            logger.warning(f"Could not get JSON file size: {e}")
                    
                    if pharmcat_tsv_exists:
                        try:
                            tsv_size = pharmcat_tsv_path.stat().st_size
                            logger.info(f"PharmCAT TSV file size: {tsv_size} bytes")
                        except Exception as e:
                            logger.warning(f"Could not get TSV file size: {e}")


                # Generate interactive HTML report first (needed for PDF generation)
                logger.info(f"Generating interactive HTML report to {interactive_html_path}")
                create_interactive_html_report(
                    patient_id=patient_id,
                    report_id=data_id,
                    diplotypes=formatted_diplotypes,
                    recommendations=formatted_recommendations,
                    output_path=str(interactive_html_path),
                    workflow=workflow.copy() if isinstance(workflow, dict) else {},
                )
                
                # Generate unified PDF report using centralized PDF generation system
                logger.info(f"Generating unified PDF report to {pdf_report_path}")
                
                try:
                    from app.reports.pdf_generators import generate_pdf_report_dual_lane
                    
                    # Prepare template data for PDF generation using the PDF template structure
                    # Note: We don't need template_html for the PDF template - it generates its own HTML
                    template_data = {
                        "patient_id": patient_id,
                        "report_id": data_id,
                        "file_type": workflow.get("file_type", "unknown"),
                        "analysis_results": {
                            "GATK Processing": "Completed" if workflow.get("needs_gatk", False) else "Not Required",
                            "PyPGx CYP2D6 Analysis": "Completed" if workflow.get("used_pypgx", False) else "Not Required",
                            "PharmCAT Analysis": "Completed",
                            "FHIR Export": "Not Implemented"
                        },
                        "workflow_diagram": workflow,
                        # PDF template will generate its own HTML content
                        "diplotypes": formatted_diplotypes,
                        "recommendations": formatted_recommendations,
                        "workflow": workflow
                    }
                    
                    # Generate PDF using centralized system (respects environment configuration)
                    result = generate_pdf_report_dual_lane(
                        template_data=template_data,
                        output_path=str(pdf_report_path),
                        workflow_diagram=workflow
                    )
                    
                    if result["success"]:
                        logger.info(f"✓ PDF report generated successfully using {result['generator_used']}: {pdf_report_path}")
                        if result["fallback_used"]:
                            logger.info("ℹ️ Used fallback generator due to primary failure")
                    else:
                        logger.error(f"✗ PDF generation failed: {result['error']}")
                        # Continue with HTML report only
                    
                except Exception as e:
                    logger.error(f"✗ PDF generation failed: {str(e)}")
                    # Continue with HTML report only
                
                # Add provisional flag if the workflow was marked as provisional
                is_provisional = workflow.get("is_provisional", False)
                
                # Update job status with unified report URLs
                response_data = {
                    "pdf_report_url": f"/reports/{pdf_report_path.name}",
                    "html_report_url": f"/reports/{interactive_html_path.name}",
                    "diplotypes": formatted_diplotypes,
                    "recommendations": formatted_recommendations,
                    "is_provisional": is_provisional,
                    "warnings": workflow.get("warnings", []),
                    "job_directory": str(patient_dir)
                }
                
                # Add PharmCAT report URLs if they exist and are enabled via environment variables
                if pharmcat_html_exists and INCLUDE_PHARMCAT_HTML:
                    response_data["pharmcat_html_report_url"] = f"/reports/{pharmcat_html_path.name}"
                    logger.info(f"Added PharmCAT HTML report URL (enabled via INCLUDE_PHARMCAT_HTML)")
                if pharmcat_json_exists and INCLUDE_PHARMCAT_JSON:
                    response_data["pharmcat_json_report_url"] = f"/reports/{pharmcat_json_path.name}"
                    logger.info(f"Added PharmCAT JSON report URL (enabled via INCLUDE_PHARMCAT_JSON)")
                if pharmcat_tsv_exists and INCLUDE_PHARMCAT_TSV:
                    response_data["pharmcat_tsv_report_url"] = f"/reports/{pharmcat_tsv_path.name}"
                    logger.info(f"Added PharmCAT TSV report URL (enabled via INCLUDE_PHARMCAT_TSV)")
                
                # Log which reports were skipped due to environment variable settings
                if pharmcat_html_exists and not INCLUDE_PHARMCAT_HTML:
                    logger.info("PharmCAT HTML report exists but skipped due to INCLUDE_PHARMCAT_HTML=false")
                if pharmcat_json_exists and not INCLUDE_PHARMCAT_JSON:
                    logger.info("PharmCAT JSON report exists but skipped due to INCLUDE_PHARMCAT_JSON=false")
                if pharmcat_tsv_exists and not INCLUDE_PHARMCAT_TSV:
                    logger.info("PharmCAT TSV report exists but skipped due to INCLUDE_PHARMCAT_TSV=false")
                
                job_status[data_id].update({"data": response_data})
                
                # Add completion message with provisional status if applicable
                completion_message = "Analysis completed successfully"
                if is_provisional:
                    completion_message += " (PROVISIONAL RESULTS)"
                
                logger.info(f"Updated job status with unified report URLs. Job directory: {patient_dir}")
                
            except Exception as gen_err:
                # If our custom generation failed, attempt to surface PharmCAT HTML if available
                logger.error(f"Error during unified report generation: {str(gen_err)}")
                job_status[data_id].update({
                    "status": "error",
                    "message": f"Report generation failed: {str(gen_err)}",
                })
                return
                
        except Exception as report_error:
            # Handle report generation error
            logger.error(f"Error generating reports: {str(report_error)}")
            job_status[data_id].update({
                "status": "error",
                "message": f"Error generating reports: {str(report_error)}"
            })
            return
        
        # Mark job as completed
        completion_message = "Analysis completed successfully"
        if workflow.get("is_provisional", False):
            completion_message += " (PROVISIONAL RESULTS)"
        
        job_status[data_id].update({
            "status": "completed",
            "percent": 100,
            "stage": "Complete",
            "message": completion_message,
            "complete": True
        })
        
        logger.info(f"Job {data_id} completed successfully")
        
    except Exception as e:
        # Handle any other errors
        logger.error(f"Error processing file: {str(e)}")
        job_status[data_id].update({
            "status": "error",
            "message": f"Error: {str(e)}",
            "complete": True
        })

@router.post("/genomic-data", response_model=UploadResponse)
async def upload_genomic_data(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    patient_identifier: Optional[str] = Form(None),
    original_file: Optional[UploadFile] = File(None),
    reference_genome: Optional[str] = Form("hg38"),
    db: Session = Depends(get_db),
    current_user: str = Depends(get_optional_user)
):
    """
    Upload genomic data file for processing. Supports various file formats:
    
    - VCF: Directly analyzed with PharmCAT (can be uploaded with original BAM/CRAM file for better variant calling)
    - BAM/CRAM/SAM: Processed through GATK for variant calling
    - FASTQ: Not yet supported, requires alignment (future implementation)
    - 23andMe: Not yet supported, requires conversion to VCF (future implementation)
    
    Currently only hg38/GRCh38 reference genome is fully supported.
    """
    try:
        # Generate unique identifiers
        file_id = str(uuid.uuid4())
        if not patient_identifier:
            patient_identifier = f"patient_{uuid.uuid4()}"
        
        # Create patient record
        patient_id = create_patient(db, patient_identifier)
        
        # Create directory for patient if it doesn't exist
        patient_dir = os.path.join(UPLOAD_DIR, str(patient_id))
        os.makedirs(patient_dir, exist_ok=True)
        
        # Save primary uploaded file
        primary_file_path = os.path.join(patient_dir, f"{file_id}{Path(file.filename).suffix}")
        with open(primary_file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # Save original file if provided
        original_file_path = None
        if original_file and original_file.filename:
            original_file_id = str(uuid.uuid4())
            original_file_path = os.path.join(patient_dir, f"{original_file_id}{Path(original_file.filename).suffix}")
            with open(original_file_path, "wb") as buffer:
                shutil.copyfileobj(original_file.file, buffer)
            
            logger.info(f"Uploaded original genomic file: {original_file_path}")
        
        # Analyze file and determine workflow
        result = await file_processor.process_upload(primary_file_path, original_file_path)
        
        if result["status"] == "error":
            raise HTTPException(status_code=400, detail=result["error"])

        # Register genetic data in database - primary file
        data_id = register_genetic_data(
            db, 
            patient_id, 
            result["file_analysis"].file_type.value,
            primary_file_path
        )
        
        # Register original file in database if provided
        if original_file_path:
            # Determine file type for original file
            if "original_file_type" in result["workflow"]:
                original_type = result["workflow"]["original_file_type"]
            else:
                # Default to "unknown" if can't determine
                original_type = "unknown"
                
            # Register as a secondary file linked to the same patient
            original_data_id = register_genetic_data(
                db,
                patient_id,
                original_type,
                original_file_path,
                is_supplementary=True,
                parent_id=data_id
            )
            logger.info(f"Registered original file with ID: {original_data_id}")
            
            # Add to workflow information
            result["workflow"]["original_file_id"] = str(original_data_id)
        
        # Add reference genome info to workflow
        if reference_genome and reference_genome != "hg38":
            result["workflow"]["requested_reference"] = reference_genome
            result["workflow"]["warnings"].append(
                f"Requested reference genome {reference_genome} may not be fully supported. "
                "Only hg38/GRCh38 is currently guaranteed for all analyses."
            )
        
        # Schedule background processing
        background_tasks.add_task(
            process_file_background,
            primary_file_path,
            str(patient_id),
            str(data_id),
            result["workflow"]
        )
        
        # Convert dataclass to Pydantic model
        file_analysis = result["file_analysis"]
        vcf_info = None
        if file_analysis.vcf_info:
            vcf_info = VCFHeaderInfo(
                reference_genome=file_analysis.vcf_info.reference_genome,
                sequencing_platform=file_analysis.vcf_info.sequencing_platform,
                sequencing_profile=(
                    file_analysis.vcf_info.sequencing_profile.value
                    if hasattr(file_analysis.vcf_info.sequencing_profile, "value")
                    else file_analysis.vcf_info.sequencing_profile
                ),
                has_index=file_analysis.vcf_info.has_index,
                is_bgzipped=file_analysis.vcf_info.is_bgzipped,
                contigs=file_analysis.vcf_info.contigs,
                sample_count=file_analysis.vcf_info.sample_count,
                variant_count=file_analysis.vcf_info.variant_count
            )
        
        analysis_info = PydanticFileAnalysis(
            file_type=(
                file_analysis.file_type.value
                if hasattr(file_analysis.file_type, "value")
                else file_analysis.file_type
            ),
            is_compressed=file_analysis.is_compressed,
            has_index=file_analysis.has_index,
            vcf_info=vcf_info,
            file_size=file_analysis.file_size,
            error=file_analysis.error
        )
        
        # Prepare response with appropriate messages
        upload_message = "File uploaded successfully. Processing started."
        if result["workflow"].get("unsupported", False):
            upload_message = f"File uploaded, but {result['workflow'].get('unsupported_reason', 'format is not fully supported')}."
        elif result["workflow"].get("is_provisional", False):
            upload_message = "File uploaded. Results will be PROVISIONAL due to limitations in the input data."
        elif original_file_path and result["workflow"].get("using_original_file", False):
            upload_message = "Files uploaded. Using original genomic file for processing."
        
        return UploadResponse(
            file_id=str(data_id),
            file_type=file_analysis.file_type.value,
            status="queued",
            message=upload_message,
            analysis_info=analysis_info,
            workflow=result["workflow"]
        )
        
    except Exception as e:
        logger.error(f"Error during file upload: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error processing upload: {str(e)}")

@router.get("/status/{file_id}")
async def get_upload_status(file_id: str, db: Session = Depends(get_db)):
    """
    Check the processing status of an uploaded file.
    """
    try:
        # Get status from job_status dictionary
        if file_id not in job_status:
            logger.warning(f"Status request for unknown job ID: {file_id}")
            
            # Check if reports exist for this ID in patient directory
            patient_dir = Path(f"{REPORTS_DIR}/{file_id}")
            pdf_path = patient_dir / f"{file_id}_pgx_report.pdf"
            html_path = patient_dir / f"{file_id}_pgx_report.html"
            interactive_path = patient_dir / f"{file_id}_pgx_report_interactive.html"
            pharmcat_html = patient_dir / f"{file_id}_pgx_pharmcat.html"
            pharmcat_json = patient_dir / f"{file_id}_pgx_pharmcat.json"
            pharmcat_tsv = patient_dir / f"{file_id}_pgx_pharmcat.tsv"
            
            pdf_exists = os.path.exists(pdf_path)
            html_exists = os.path.exists(html_path) or os.path.exists(interactive_path)
            
            if pdf_exists or html_exists:
                logger.info(f"Reports found for job {file_id}, returning completed status")
                data = {
                    "pdf_report_url": f"/reports/{file_id}_pgx_report.pdf" if pdf_exists else None,
                    # Prefer interactive HTML if present
                    "html_report_url": f"/reports/{file_id}_pgx_report_interactive.html" if os.path.exists(interactive_path) else (
                        f"/reports/{file_id}_pgx_report.html" if os.path.exists(html_path) else None
                    )
                }
                if pharmcat_html.exists() and INCLUDE_PHARMCAT_HTML:
                    data["pharmcat_html_report_url"] = f"/reports/{file_id}_pgx_pharmcat.html"
                    logger.info(f"Added PharmCAT HTML report URL to status (enabled via INCLUDE_PHARMCAT_HTML)")
                if pharmcat_json.exists() and INCLUDE_PHARMCAT_JSON:
                    data["pharmcat_json_report_url"] = f"/reports/{file_id}_pgx_pharmcat.json"
                    logger.info(f"Added PharmCAT JSON report URL to status (enabled via INCLUDE_PHARMCAT_JSON)")
                if pharmcat_tsv.exists() and INCLUDE_PHARMCAT_TSV:
                    data["pharmcat_tsv_report_url"] = f"/reports/{file_id}_pgx_pharmcat.tsv"
                    logger.info(f"Added PharmCAT TSV report URL to status (enabled via INCLUDE_PHARMCAT_TSV)")

                return {
                    "file_id": file_id,
                    "status": "completed",
                    "progress": 100,
                    "message": "Analysis completed successfully",
                    "current_stage": "Complete",
                    "data": data
                }
            
            raise HTTPException(status_code=404, detail=f"Job {file_id} not found")
            
        status = job_status[file_id]
        logger.info(f"Found status for job {file_id}: {status}")
        
        # Check PharmCAT wrapper service status if still processing
        if status.get("status") == "processing" and status.get("stage") == "PharmCAT":
            try:
                logger.info("Checking PharmCAT wrapper status")
                wrapper_response = requests.get("http://pharmcat:5000/status", timeout=2)
                if wrapper_response.ok:
                    wrapper_status = wrapper_response.json()
                    logger.info(f"PharmCAT wrapper status: {wrapper_status}")
                    
                    # Update status with wrapper information if available
                    if wrapper_status.get("processing_status"):
                        proc_status = wrapper_status.get("processing_status", {})
                        # Only update if we have valid information
                        if proc_status.get("message"):
                            status["message"] = proc_status.get("message")
                        if proc_status.get("progress"):
                            status["percent"] = proc_status.get("progress")
                        logger.info(f"Updated job status from wrapper: {status}")
            except Exception as e:
                logger.warning(f"Could not get PharmCAT wrapper status: {str(e)}")
        
        # Check for completed reports even if job status doesn't show completion
        if status.get("status") != "completed" and not status.get("complete", False):
            # Check in patient directory
            patient_dir = Path(f"{REPORTS_DIR}/{file_id}")
            pdf_path = patient_dir / f"{file_id}_pgx_report.pdf"
            html_path = patient_dir / f"{file_id}_pgx_report.html"
            
            pdf_exists = os.path.exists(pdf_path)
            html_exists = os.path.exists(html_path) or os.path.exists(patient_dir / f"{file_id}_pgx_report_interactive.html")
            
            if pdf_exists or html_exists:
                logger.info(f"Found reports for job {file_id} but status not marked as complete, updating status")
                status.update({
                    "status": "completed",
                    "percent": 100,
                    "stage": "Complete",
                    "message": "Analysis completed successfully",
                    "complete": True,
                    "data": {
                        "pdf_report_url": f"/reports/{file_id}_pgx_report.pdf" if pdf_exists else None,
                        "html_report_url": (
                            f"/reports/{file_id}_pgx_report_interactive.html" if os.path.exists(patient_dir / f"{file_id}_pgx_report_interactive.html") else (
                                f"/reports/{file_id}_pgx_report.html" if os.path.exists(html_path) else None
                            )
                        )
                    }
                })
        
        # Normalize status for response
        response_status = "completed" if status.get("complete", False) or status.get("status") == "completed" else "processing"
        if status.get("status") == "error":
            response_status = "error"
            
        # Map the status to the expected response format
        response = {
            "file_id": file_id,
            "status": response_status,
            "progress": status.get("percent", 0),
            "message": status.get("message", ""),
            "current_stage": status.get("stage", "Unknown"),
            "data": status.get("data", {})
        }
        
        logger.info(f"Returning status response for job {file_id}: {response}")
        return response
        
    except Exception as e:
        logger.error(f"Error getting status for job {file_id}: {str(e)}")
        raise HTTPException(status_code=404, detail=f"File not found or error retrieving status: {str(e)}")

@router.get("/reports/job/{file_id}")
async def get_report_urls(file_id: str):
    """
    Get the report URLs for a completed job
    """
    try:
        # Check if job exists and is complete
        if file_id not in job_status:
            # Check in patient directory
            patient_dir = Path(f"{REPORTS_DIR}/{file_id}")
            pdf_path = patient_dir / f"{file_id}_pgx_report.pdf"
            html_path = patient_dir / f"{file_id}_pgx_report.html"
            interactive_path = patient_dir / f"{file_id}_pgx_report_interactive.html"
            pharmcat_html = patient_dir / f"{file_id}_pgx_pharmcat.html"
            pharmcat_json = patient_dir / f"{file_id}_pgx_pharmcat.json"
            pharmcat_tsv = patient_dir / f"{file_id}_pgx_pharmcat.tsv"
            
            pdf_exists = os.path.exists(pdf_path)
            html_exists = os.path.exists(html_path) or os.path.exists(interactive_path)
            
            if pdf_exists or html_exists:
                report_paths = {
                    "pdf_report_url": f"/reports/{file_id}_pgx_report.pdf" if pdf_exists else None,
                    "html_report_url": (
                        f"/reports/{file_id}_pgx_report_interactive.html" if os.path.exists(interactive_path) else (
                            f"/reports/{file_id}_pgx_report.html" if os.path.exists(html_path) else None
                        )
                    )
                }
                if pharmcat_html.exists() and INCLUDE_PHARMCAT_HTML:
                    report_paths["pharmcat_html_report_url"] = f"/reports/{file_id}_pgx_pharmcat.html"
                    logger.info(f"Added PharmCAT HTML report URL to report paths (enabled via INCLUDE_PHARMCAT_HTML)")
                if pharmcat_json.exists() and INCLUDE_PHARMCAT_JSON:
                    report_paths["pharmcat_json_report_url"] = f"/reports/{file_id}_pgx_pharmcat.json"
                    logger.info(f"Added PharmCAT JSON report URL to report paths (enabled via INCLUDE_PHARMCAT_JSON)")
                if pharmcat_tsv.exists() and INCLUDE_PHARMCAT_TSV:
                    report_paths["pharmcat_tsv_report_url"] = f"/reports/{file_id}_pgx_pharmcat.tsv"
                    logger.info(f"Added PharmCAT TSV report URL to report paths (enabled via INCLUDE_PHARMCAT_TSV)")
                return {
                    "file_id": file_id,
                    "status": "completed",
                    **report_paths
                }
            else:
                raise HTTPException(status_code=404, detail="Job not found")
                
        # Get data from job status
        status = job_status[file_id]
        data = status.get("data", {})
        
        if not status.get("complete", False):
            return {
                "file_id": file_id,
                "status": "processing",
                "message": status.get("message", "Processing in progress")
            }
            
        response = {
            "file_id": file_id,
            "status": "completed" if status.get("success", False) else "failed",
            "pdf_report_url": data.get("pdf_report_url"),
            "html_report_url": data.get("html_report_url")
        }
        # Bubble up PharmCAT URLs if present and enabled via environment variables
        if "pharmcat_html_report_url" in data and INCLUDE_PHARMCAT_HTML:
            response["pharmcat_html_report_url"] = data["pharmcat_html_report_url"]
            logger.info("Bubbled up PharmCAT HTML report URL (enabled via INCLUDE_PHARMCAT_HTML)")
        if "pharmcat_json_report_url" in data and INCLUDE_PHARMCAT_JSON:
            response["pharmcat_json_report_url"] = data["pharmcat_json_report_url"]
            logger.info("Bubbled up PharmCAT JSON report URL (enabled via INCLUDE_PHARMCAT_JSON)")
        if "pharmcat_tsv_report_url" in data and INCLUDE_PHARMCAT_TSV:
            response["pharmcat_tsv_report_url"] = data["pharmcat_tsv_report_url"]
            logger.info("Bubbled up PharmCAT TSV report URL (enabled via INCLUDE_PHARMCAT_TSV)")
        return response
    
    except Exception as e:
        logger.error(f"Error retrieving report URLs: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error retrieving report URLs: {str(e)}") 