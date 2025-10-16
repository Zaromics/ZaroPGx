import os
import uuid
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from typing import List, Optional
import logging
from datetime import datetime, timezone
import json

from app.api.db import get_db, register_report, get_guidelines_for_gene_drug
from app.api.models import ReportRequest, ReportResponse, DrugRecommendation
from app.reports.fhir_client import FhirClient
from ..utils.security import get_current_user, get_optional_user

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize router
router = APIRouter(
    prefix="/reports",
    tags=["reports"]
)

# Constants
REPORT_DIR = os.environ.get("REPORT_DIR", "/data/reports")
# Ensure reports directory exists
os.makedirs(REPORT_DIR, exist_ok=True)

# Background task to generate report
def generate_report_background(patient_id: str, file_id: str, report_type: str, report_id: str, db: Session):
    try:
        logger.info(f"🚀 Starting report generation for patient {patient_id}, file {file_id}")
        logger.info(f"📋 Report type: {report_type}, Report ID: {report_id}")
        
        logger.info(f"Generating {report_type} report for patient {patient_id}, file {file_id}")

        # Try multiple path resolution strategies
        pharmcat_data_dir = None
        
        # Strategy 1: Use environment variable if set
        if os.getenv("PHARMCAT_DATA_DIR"):
            pharmcat_data_dir = os.getenv("PHARMCAT_DATA_DIR")
        
        # Strategy 2: Look relative to current working directory
        if not pharmcat_data_dir or not os.path.exists(pharmcat_data_dir):
            pharmcat_data_dir = os.path.join(os.getcwd(), "data", "pharmcat_final_results")
        
        # Strategy 3: Look relative to this file
        if not os.path.exists(pharmcat_data_dir):
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
            pharmcat_data_dir = os.path.join(project_root, "data", "pharmcat_final_results")
        
        # Strategy 4
        if not os.path.exists(pharmcat_data_dir):
            docker_paths = [
                "/data/reports"
            ]
            for path in docker_paths:
                if os.path.exists(path):
                    pharmcat_data_dir = path
                    break
        
        logger.info(f"Using PharmCAT data directory: {pharmcat_data_dir}")
        logger.info(f"Directory exists: {os.path.exists(pharmcat_data_dir)}")
        
        if os.path.exists(pharmcat_data_dir):
            files = os.listdir(pharmcat_data_dir)
            logger.info(f"Files in PharmCAT directory: {files}")
        
        # Check if PharmCAT results exist for this patient/sample
        sample_files = [
            f"{patient_id}.mm2.sortdup.bqsr.hc.report.json",
            f"{patient_id}.mm2.sortdup.bqsr.hc.report.tsv",
            f"{patient_id}.mm2.sortdup.bqsr.hc.phenotype.json"
        ]
        
        pharmcat_json_path = None
        pharmcat_tsv_path = None
        
        for filename in sample_files:
            file_path = os.path.join(pharmcat_data_dir, filename)
            if os.path.exists(file_path):
                if filename.endswith('.json') and 'report' in filename:
                    pharmcat_json_path = file_path
                elif filename.endswith('.tsv'):
                    pharmcat_tsv_path = file_path
                logger.info(f"Found PharmCAT file: {filename}")
        
        if not pharmcat_json_path or not pharmcat_tsv_path:
            logger.warning(f"No PharmCAT results found for patient {patient_id}.")
            # Do not fall back to mock data if PharmCAT results are not found
            diplotypes = []
        else:
            # Load PharmCAT data
            logger.info("Loading PharmCAT results")
            import pandas as pd
            
            # Load TSV data
            df = pd.read_csv(pharmcat_tsv_path, sep='\t', skiprows=1)
            
            # Convert to diplotypes format
            diplotypes = []
            for _, row in df.iterrows():
                if pd.notna(row['Gene']) and pd.notna(row['Source Diplotype']):
                    diplotypes.append({
                        "gene": row['Gene'],
                        "diplotype": row['Source Diplotype'],
                        "phenotype": row['Phenotype'] if pd.notna(row['Phenotype']) else "Unknown",
                        "activity_score": row['Activity Score'] if pd.notna(row['Activity Score']) else None
                    })
            
            logger.info(f"Loaded {len(diplotypes)} real pharmacogenomic findings")
        
        # Get drug recommendations based on diplotypes
        recommendations = []
        for diplotype in diplotypes:
            gene = diplotype["gene"]
            # Get drugs that have guidelines for this gene
            drug_guidelines = get_guidelines_for_gene_drug(db, gene, None)
            for guideline in drug_guidelines:
                recommendations.append(
                    DrugRecommendation(
                        drug=guideline.drug,
                        gene=gene,
                        guideline=f"CPIC Guideline for {gene} and {guideline.drug}",
                        recommendation=guideline.recommendation,
                        classification="Strong",
                        literature_references=["PMID:12345678"]
                    )
                )
        
        # Generate PDF report using dual-lane system
        report_path = os.path.join(REPORT_DIR, f"{report_id}.pdf")
        from app.reports.pdf_generators import generate_pdf_report_dual_lane
        
        # Prepare template data for dual-lane PDF generation
        template_data = {
            "patient_id": patient_id,
            "report_id": report_id,
            "diplotypes": diplotypes,
            "recommendations": recommendations,
        }
        
        # Use dual-lane PDF generation system
        preferred_generator = os.environ.get("PDF_ENGINE", "weasyprint").lower()
        if preferred_generator not in ["weasyprint", "reportlab"]:
            preferred_generator = "weasyprint"  # Default fallback
            
        result = generate_pdf_report_dual_lane(
            template_data=template_data,
            output_path=report_path,
            workflow_diagram=None,  # No workflow diagram for this simple report
            preferred_generator=preferred_generator  # Use configured engine preference
        )
        
        if result["success"]:
            logger.info(f"✓ PDF generated successfully using {result['generator_used']}")
            if result["fallback_used"]:
                logger.info("⚠ Fallback generator was used")
        else:
            logger.error(f"✗ Dual-lane PDF generation failed: {result['error']}")
            raise Exception(f"PDF generation failed: {result['error']}")
        
        # Register report in database
        register_report(db, patient_id, report_type, report_path)
        
        logger.info(f"Report generation complete: {report_path}")
    except Exception as e:
        logger.error(f"Error generating report: {str(e)}")
        # Update status in database to failed

@router.post("/generate", response_model=ReportResponse)
async def generate_report(
    request: ReportRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_optional_user)
):
    """
    Generate a pharmacogenomic report for a patient based on their genetic data.
    """
    try:
        # Check if patient and file exist
        # This would be implemented with db queries
        # For now, assume they exist
        
        # Generate report ID
        report_id = str(uuid.uuid4())
        
        # Schedule background task to generate report
        background_tasks.add_task(
            generate_report_background,
            request.patient_id,
            request.file_id,
            request.report_type,
            report_id,
            db
        )
        
        # Return response
        return ReportResponse(
            report_id=report_id,
            patient_id=request.patient_id,
            created_at=datetime.now(timezone.utc),
            report_url=f"/reports/{report_id}/download",
            report_type=request.report_type
        )
    except Exception as e:
        logger.error(f"Error initiating report generation: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error generating report: {str(e)}")

@router.get("/{report_id}/status")
async def get_report_status(
    report_id: str, 
    db: Session = Depends(get_db),
    current_user: str = Depends(get_optional_user)
):
    """
    Check the status of a report generation request.
    """
    try:
        # Query report status from database
        # This would be implemented with a db query
        # For now, return mock data
        return {
            "report_id": report_id,
            "status": "completed",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "download_url": f"/reports/{report_id}/download"
        }
    except Exception as e:
        logger.error(f"Error getting report status: {str(e)}")
        raise HTTPException(status_code=404, detail="Report not found")

@router.get("/{report_id}/download")
async def download_report(
    report_id: str, 
    db: Session = Depends(get_db),
    current_user: str = Depends(get_optional_user)
):
    """
    Download a generated pharmacogenomic report.
    """
    try:
        # Get report path from database
        # This would be implemented with a db query
        # For now, use a fixed path
        report_path = os.path.join(REPORT_DIR, f"{report_id}.pdf")
        
        # Check if report exists
        if not os.path.exists(report_path):
            raise HTTPException(status_code=404, detail="Report not found or still processing")
            
        # In a real implementation, this would return the file
        # For now, return a mock response
        return {
            "file_url": f"/static/reports/{report_id}.pdf",
            "content_type": "application/pdf",
            "filename": f"pgx_report_{report_id}.pdf"
        }
    except Exception as e:
        logger.error(f"Error downloading report: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error downloading report: {str(e)}")

@router.get("/recommendations/{patient_id}", response_model=List[DrugRecommendation])
async def get_drug_recommendations(
    patient_id: str, 
    drug: Optional[str] = None, 
    db: Session = Depends(get_db),
    current_user: str = Depends(get_optional_user)
):
    """
    Get drug recommendations for a patient based on their genetic profile.
    Optionally filter by specific drug.
    """
    try:
        # Load PharmCAT results from the data directory
        # Try multiple path resolution strategies
        pharmcat_data_dir = None
        
        # Strategy 1: Use environment variable if set
        if os.getenv("PHARMCAT_DATA_DIR"):
            pharmcat_data_dir = os.getenv("PHARMCAT_DATA_DIR")
        
        # Strategy 2: Look relative to current working directory
        if not pharmcat_data_dir or not os.path.exists(pharmcat_data_dir):
            pharmcat_data_dir = os.path.join(os.getcwd(), "data", "pharmcat_final_results")
        
        # Strategy 3: Look relative to this file (for development)
        if not os.path.exists(pharmcat_data_dir):
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
            pharmcat_data_dir = os.path.join(project_root, "data", "pharmcat_final_results")
        
        # Strategy 4: Look in common path
        if not os.path.exists(pharmcat_data_dir):
            docker_paths = [
                "/data/reports"
            ]
            for path in docker_paths:
                if os.path.exists(path):
                    pharmcat_data_dir = path
                    break
        
        logger.info(f"Using PharmCAT data directory for recommendations: {pharmcat_data_dir}")
        logger.info(f"Directory exists: {os.path.exists(pharmcat_data_dir)}")
        
        # Check if PharmCAT results exist for this patient/sample
        sample_files = [
            f"{patient_id}.mm2.sortdup.bqsr.hc.report.json",
            f"{patient_id}.mm2.sortdup.bqsr.hc.report.tsv",
            f"{patient_id}.mm2.sortdup.bqsr.hc.phenotype.json"
        ]
        
        pharmcat_tsv_path = None
        
        for filename in sample_files:
            file_path = os.path.join(pharmcat_data_dir, filename)
            if os.path.exists(file_path) and filename.endswith('.tsv'):
                pharmcat_tsv_path = file_path
                logger.info(f"Found PharmCAT TSV file: {filename}")
                break
        
        if not pharmcat_tsv_path:
            logger.warning(f"No PharmCAT results found for patient {patient_id}.")
            # Do not fall back to mock data if PharmCAT results are not found
            diplotypes = []
        else:
            # Load PharmCAT data
            logger.info("Loading real PharmCAT results for drug recommendations")
            import pandas as pd
            
            # Load TSV data
            df = pd.read_csv(pharmcat_tsv_path, sep='\t', skiprows=1)
            
            # Convert to diplotypes format
            diplotypes = []
            for _, row in df.iterrows():
                if pd.notna(row['Gene']) and pd.notna(row['Source Diplotype']):
                    diplotypes.append({
                        "gene": row['Gene'],
                        "diplotype": row['Source Diplotype'],
                        "phenotype": row['Phenotype'] if pd.notna(row['Phenotype']) else "Unknown",
                        "activity_score": row['Activity Score'] if pd.notna(row['Activity Score']) else None
                    })
            
            logger.info(f"Loaded {len(diplotypes)} real pharmacogenomic findings for recommendations")
        
        # Get drug recommendations based on diplotypes
        recommendations = []
        for diplotype in diplotypes:
            gene = diplotype["gene"]
            # In a real implementation, this would check the specific allele combination
            if drug:
                drug_guidelines = get_guidelines_for_gene_drug(db, gene, drug)
            else:
                drug_guidelines = get_guidelines_for_gene_drug(db, gene, None)
                
            for guideline in drug_guidelines:
                recommendations.append(
                    DrugRecommendation(
                        drug=guideline.drug,
                        gene=gene,
                        guideline=f"CPIC Guideline for {gene} and {guideline.drug}",
                        recommendation=guideline.recommendation,
                        classification="Strong",
                        literature_references=["PMID:12345678"]
                    )
                )
        
        return recommendations
    except Exception as e:
        logger.error(f"Error getting drug recommendations: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error getting recommendations: {str(e)}")

@router.post("/{report_id}/export-to-fhir")
async def export_report_to_fhir(
    report_id: str,
    target_fhir_url: Optional[str] = None,
    patient_info: Optional[dict] = None,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_optional_user)
):
    """
    Export a pharmacogenomic report to a FHIR server.
    
    Args:
        report_id: ID of the report to export
        target_fhir_url: Optional URL of the target FHIR server
        patient_info: Optional additional patient information
    
    Returns:
        Export status and details
    """
    try:
        logger.info(f"Exporting report {report_id} to FHIR server")
        
        # In a real implementation, we would get this from the database
        # For now, use placeholder data based on report_id
        
        # Initialize the FHIR client
        fhir_client = FhirClient(target_fhir_url)
        
        # Check FHIR server connection
        if not fhir_client.check_server_connection():
            logger.error("Cannot connect to FHIR server")
            raise HTTPException(status_code=503, detail="Cannot connect to FHIR server")
        
        # Get report data
        # In a real implementation, this would come from the database
        report_path = os.path.join(REPORT_DIR, f"{report_id}.pdf")
        html_path = os.path.join(REPORT_DIR, f"{report_id}.html")
        
        # Ensure report exists
        if not os.path.exists(report_path) and not os.path.exists(html_path):
            raise HTTPException(status_code=404, detail="Report not found")
            
        # For demonstration purposes, use mock data
        # In a real implementation, this would be retrieved from the database
        patient_id = patient_info.get("id", "unknown") if patient_info else "demo-patient"
        
        # Get diplotypes and recommendations (mock data for now)
        diplotypes = [
            {"gene": "CYP2D6", "diplotype": "*1/*4", "phenotype": "Intermediate Metabolizer"},
            {"gene": "CYP2C19", "diplotype": "*1/*1", "phenotype": "Normal Metabolizer"},
            {"gene": "SLCO1B1", "diplotype": "rs4149056 TC", "phenotype": "Intermediate Function"}
        ]
        
        recommendations = []
        for diplotype in diplotypes:
            gene = diplotype["gene"]
            drug_guidelines = get_guidelines_for_gene_drug(db, gene, None)
            for guideline in drug_guidelines:
                recommendations.append({
                    "drug": guideline.drug,
                    "gene": gene,
                    "guideline": f"CPIC Guideline for {gene} and {guideline.drug}",
                    "recommendation": guideline.recommendation,
                    "classification": "Strong",
                    "literature_references": ["PMID:12345678"]
                })
        
        # Prepare patient information
        if not patient_info:
            patient_info = {
                "id": patient_id,
                "name": {
                    "family": "Demo",
                    "given": ["Patient"]
                },
                "gender": "unknown"
            }
        
        # Create absolute URLs for the reports
        base_url = "http://localhost:8765"  # This should be configured from environment
        pdf_url = f"{base_url}/reports/{report_id}/download?format=pdf"
        html_url = f"{base_url}/reports/{report_id}/download?format=html"
        
        # Export to FHIR
        result = fhir_client.export_pgx_report_to_fhir(
            patient_info=patient_info,
            report_id=report_id,
            diplotypes=diplotypes,
            recommendations=recommendations,
            report_pdf_url=pdf_url,
            report_html_url=html_url,
            target_fhir_server=target_fhir_url
        )
        
        if result.get("status") == "error":
            logger.error(f"Error exporting to FHIR: {result.get('error')}")
            raise HTTPException(status_code=500, detail=f"Error exporting to FHIR: {result.get('error')}")
            
        return result
    except Exception as e:
        logger.error(f"Error exporting report to FHIR: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error exporting report to FHIR: {str(e)}") 