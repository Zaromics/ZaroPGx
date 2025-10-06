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
        logger.info(f"Generating {report_type} report for patient {patient_id}, file {file_id}")
        
        # Get patient allele data from database
        # This would be implemented with db queries
        # Mock data for now
        diplotypes = [
            {"gene": "CYP2D6", "diplotype": "*1/*4", "phenotype": "Mock Data"},
            {"gene": "CYP2C19", "diplotype": "*1/*1", "phenotype": "Mock Data"},
            {"gene": "SLCO1B1", "diplotype": "rs4149056 TC", "phenotype": "Mock Data"}
        ]
        
        # Get drug recommendations based on diplotypes
        recommendations = []
        for diplotype in diplotypes:
            gene = diplotype["gene"]
            # Get drugs that have guidelines for this gene
            # In a real implementation, this would check the specific allele combination
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
        # Get patient allele data from database
        # This would be implemented with db queries
        # Mock data for now
        diplotypes = [
            {"gene": "CYP2D6", "diplotype": "*1/*4", "phenotype": "Intermediate Metabolizer"},
            {"gene": "CYP2C19", "diplotype": "*1/*1", "phenotype": "Normal Metabolizer"},
            {"gene": "SLCO1B1", "diplotype": "rs4149056 TC", "phenotype": "Intermediate Function"}
        ]
        
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