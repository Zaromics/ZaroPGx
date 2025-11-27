"""
FHIR Export API Router

Provides endpoints for exporting pharmacogenomic reports in FHIR R4 format.
Follows the HL7 Genomics Reporting Implementation Guide for PGx reporting.

This module extends the existing report functionality without breaking changes.
Export is enabled by the FHIR_EXPORT_ENABLED environment variable (default: true).
"""

import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.db import get_db
from app.api.utils.security import get_optional_user
from app.services.fhir_export_service import FHIRExportService, FHIR_EXPORT_ENABLED

logger = logging.getLogger(__name__)

# Initialize router
router = APIRouter(
    prefix="/fhir",
    tags=["fhir-export"],
)


# Pydantic models for request/response validation
class PatientInfo(BaseModel):
    """Patient information for FHIR export."""
    id: Optional[str] = Field(None, description="Patient identifier")
    name: Optional[dict] = Field(None, description="Patient name (family, given)")
    gender: Optional[str] = Field(None, description="Patient gender")
    birthDate: Optional[str] = Field(None, description="Patient birth date (YYYY-MM-DD)")


class FHIRExportRequest(BaseModel):
    """Request model for FHIR export."""
    patient_info: Optional[PatientInfo] = Field(None, description="Optional patient information")
    output_format: str = Field("json", description="Output format: json or xml")
    include_recommendations: bool = Field(True, description="Include therapeutic implications and recommendations")


class FHIRExportResponse(BaseModel):
    """Response model for FHIR export."""
    success: bool
    format: Optional[str] = None
    filename: Optional[str] = None
    message: Optional[str] = None
    error: Optional[str] = None


@router.get("/status")
async def fhir_export_status(
    current_user: str = Depends(get_optional_user),
) -> dict:
    """
    Check if FHIR export functionality is enabled.
    
    Returns:
        Status of FHIR export feature
    """
    return {
        "enabled": FHIR_EXPORT_ENABLED,
        "message": "FHIR export is enabled" if FHIR_EXPORT_ENABLED else "FHIR export is disabled. Set FHIR_EXPORT_ENABLED=true to enable.",
        "supported_formats": ["json", "xml"] if FHIR_EXPORT_ENABLED else [],
        "implementation_guide": "HL7 Genomics Reporting Implementation Guide (FHIR R4)",
        "reference_url": "https://build.fhir.org/ig/HL7/genomics-reporting/pharmacogenomics.html",
    }


@router.get("/export/run/{run_id}")
async def export_run_to_fhir(
    run_id: str,
    output_format: str = Query("json", description="Output format: json or xml"),
    include_recommendations: bool = Query(True, description="Include drug recommendations"),
    db: Session = Depends(get_db),
    current_user: str = Depends(get_optional_user),
):
    """
    Export a PharmCAT run as a FHIR Bundle.
    
    This endpoint generates a downloadable FHIR R4-compliant file containing
    the pharmacogenomic report data following the HL7 Genomics Reporting IG.
    
    Args:
        run_id: PharmCAT run ID to export
        output_format: Output format (json or xml)
        include_recommendations: Whether to include therapeutic implications
        
    Returns:
        FHIR Bundle as JSON or XML file
    """
    if not FHIR_EXPORT_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="FHIR export is disabled. Set FHIR_EXPORT_ENABLED=true to enable.",
        )
    
    try:
        service = FHIRExportService(db)
        result = service.export_pgx_report(
            run_id=run_id,
            output_format=output_format,
            include_recommendations=include_recommendations,
        )
        
        if not result.get("success"):
            raise HTTPException(
                status_code=404,
                detail=result.get("error", "Failed to export FHIR report"),
            )
        
        # Determine content type
        content_type = "application/fhir+xml" if output_format.lower() == "xml" else "application/fhir+json"
        
        return Response(
            content=result["content"],
            media_type=content_type,
            headers={
                "Content-Disposition": f'attachment; filename="{result["filename"]}"',
            },
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error exporting FHIR report for run {run_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error exporting FHIR report: {str(e)}",
        )


@router.post("/export/run/{run_id}")
async def export_run_to_fhir_with_patient(
    run_id: str,
    request: FHIRExportRequest,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_optional_user),
):
    """
    Export a PharmCAT run as a FHIR Bundle with patient information.
    
    This endpoint allows specifying patient information to include in the
    FHIR Bundle export.
    
    Args:
        run_id: PharmCAT run ID to export
        request: Export request with patient info and options
        
    Returns:
        FHIR Bundle as JSON or XML file
    """
    if not FHIR_EXPORT_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="FHIR export is disabled. Set FHIR_EXPORT_ENABLED=true to enable.",
        )
    
    try:
        service = FHIRExportService(db)
        
        # Convert patient info to dict
        patient_info = None
        if request.patient_info:
            patient_info = request.patient_info.model_dump(exclude_none=True)
        
        result = service.export_pgx_report(
            run_id=run_id,
            patient_info=patient_info,
            output_format=request.output_format,
            include_recommendations=request.include_recommendations,
        )
        
        if not result.get("success"):
            raise HTTPException(
                status_code=404,
                detail=result.get("error", "Failed to export FHIR report"),
            )
        
        # Determine content type
        content_type = "application/fhir+xml" if request.output_format.lower() == "xml" else "application/fhir+json"
        
        return Response(
            content=result["content"],
            media_type=content_type,
            headers={
                "Content-Disposition": f'attachment; filename="{result["filename"]}"',
            },
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error exporting FHIR report for run {run_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error exporting FHIR report: {str(e)}",
        )


@router.get("/export/workflow/{workflow_id}")
async def export_workflow_to_fhir(
    workflow_id: str,
    output_format: str = Query("json", description="Output format: json or xml"),
    db: Session = Depends(get_db),
    current_user: str = Depends(get_optional_user),
):
    """
    Export a workflow's PharmCAT results as a FHIR Bundle.
    
    This endpoint retrieves PharmCAT data associated with a workflow and
    generates a FHIR R4-compliant export.
    
    Args:
        workflow_id: Workflow ID to export
        output_format: Output format (json or xml)
        
    Returns:
        FHIR Bundle as JSON or XML file
    """
    if not FHIR_EXPORT_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="FHIR export is disabled. Set FHIR_EXPORT_ENABLED=true to enable.",
        )
    
    try:
        service = FHIRExportService(db)
        result = service.export_workflow_to_fhir(
            workflow_id=workflow_id,
            output_format=output_format,
        )
        
        if not result.get("success"):
            raise HTTPException(
                status_code=404,
                detail=result.get("error", "Failed to export FHIR report"),
            )
        
        # Determine content type
        content_type = "application/fhir+xml" if output_format.lower() == "xml" else "application/fhir+json"
        
        return Response(
            content=result["content"],
            media_type=content_type,
            headers={
                "Content-Disposition": f'attachment; filename="{result["filename"]}"',
            },
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error exporting FHIR report for workflow {workflow_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error exporting FHIR report: {str(e)}",
        )


@router.get("/export/run/{run_id}/preview")
async def preview_fhir_export(
    run_id: str,
    output_format: str = Query("json", description="Output format: json or xml"),
    db: Session = Depends(get_db),
    current_user: str = Depends(get_optional_user),
):
    """
    Preview a FHIR export without downloading.
    
    Returns the FHIR Bundle in the response body for inspection.
    
    Args:
        run_id: PharmCAT run ID to preview
        output_format: Output format (json or xml)
        
    Returns:
        FHIR Bundle content and metadata
    """
    if not FHIR_EXPORT_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="FHIR export is disabled. Set FHIR_EXPORT_ENABLED=true to enable.",
        )
    
    try:
        service = FHIRExportService(db)
        result = service.export_pgx_report(
            run_id=run_id,
            output_format=output_format,
        )
        
        if not result.get("success"):
            raise HTTPException(
                status_code=404,
                detail=result.get("error", "Failed to generate FHIR preview"),
            )
        
        # For preview, return as JSON response with metadata
        return {
            "success": True,
            "format": result["format"],
            "filename": result["filename"],
            "content": result["content"] if output_format.lower() == "json" else None,
            "bundle": result.get("bundle") if output_format.lower() == "json" else None,
            "xml_preview": result["content"][:2000] + "..." if output_format.lower() == "xml" and len(result["content"]) > 2000 else result["content"] if output_format.lower() == "xml" else None,
            "resource_counts": _count_resources(result.get("bundle", {})) if result.get("bundle") else None,
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error previewing FHIR export for run {run_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error generating FHIR preview: {str(e)}",
        )


@router.get("/export/formats")
async def get_supported_formats(
    current_user: str = Depends(get_optional_user),
) -> dict:
    """
    Get information about supported FHIR export formats.
    
    Returns:
        Supported formats and their details
    """
    return {
        "formats": [
            {
                "id": "json",
                "name": "FHIR JSON",
                "content_type": "application/fhir+json",
                "description": "FHIR Bundle in JSON format (recommended for modern systems)",
                "extension": ".json",
            },
            {
                "id": "xml",
                "name": "FHIR XML",
                "content_type": "application/fhir+xml", 
                "description": "FHIR Bundle in XML format (CDA-compatible)",
                "extension": ".xml",
            },
        ],
        "implementation_guide": {
            "name": "HL7 Genomics Reporting Implementation Guide",
            "version": "STU 4",
            "fhir_version": "R4",
            "url": "https://build.fhir.org/ig/HL7/genomics-reporting/",
        },
        "profiles_used": [
            "genomic-report",
            "genotype",
            "therapeutic-implication",
            "medication-recommendation",
            "genomic-study",
        ],
    }


# ============================================================================
# File-saving endpoints - Save FHIR exports to reports directory
# ============================================================================

class FHIRSaveRequest(BaseModel):
    """Request model for saving FHIR export to reports directory."""
    patient_id: Optional[str] = Field(None, description="Patient ID for subdirectory (defaults to run_id)")
    patient_info: Optional[PatientInfo] = Field(None, description="Optional patient information")
    output_format: str = Field("json", description="Output format: json, xml, or both")
    include_recommendations: bool = Field(True, description="Include therapeutic implications")


class FHIRSaveResponse(BaseModel):
    """Response model for saved FHIR export."""
    success: bool
    files_saved: list = []
    report_directory: Optional[str] = None
    error: Optional[str] = None


@router.post("/save/run/{run_id}", response_model=FHIRSaveResponse)
async def save_fhir_export_for_run(
    run_id: str,
    request: FHIRSaveRequest,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_optional_user),
):
    """
    Export a PharmCAT run as FHIR and save to the reports directory.
    
    The FHIR export file is saved alongside other report outputs (PDF, HTML, etc.)
    in the patient/run subdirectory under /data/reports/.
    
    Args:
        run_id: PharmCAT run ID to export
        request: Save request with patient info and format options
        
    Returns:
        Information about saved files including paths and URLs
    """
    if not FHIR_EXPORT_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="FHIR export is disabled. Set FHIR_EXPORT_ENABLED=true to enable.",
        )
    
    try:
        service = FHIRExportService(db)
        
        # Convert patient info to dict
        patient_info = None
        if request.patient_info:
            patient_info = request.patient_info.model_dump(exclude_none=True)
        
        result = service.save_fhir_export(
            run_id=run_id,
            patient_id=request.patient_id,
            patient_info=patient_info,
            output_format=request.output_format,
            include_recommendations=request.include_recommendations,
        )
        
        if not result.get("success"):
            raise HTTPException(
                status_code=500,
                detail=result.get("error", "Failed to save FHIR export"),
            )
        
        return FHIRSaveResponse(
            success=True,
            files_saved=result.get("files_saved", []),
            report_directory=result.get("report_directory"),
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving FHIR export for run {run_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error saving FHIR export: {str(e)}",
        )


@router.post("/save/workflow/{workflow_id}", response_model=FHIRSaveResponse)
async def save_fhir_export_for_workflow(
    workflow_id: str,
    request: FHIRSaveRequest,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_optional_user),
):
    """
    Export a workflow's PharmCAT results as FHIR and save to reports directory.
    
    The FHIR export file is saved alongside other report outputs in the
    workflow's subdirectory under /data/reports/.
    
    Args:
        workflow_id: Workflow ID to export
        request: Save request with patient info and format options
        
    Returns:
        Information about saved files including paths and URLs
    """
    if not FHIR_EXPORT_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="FHIR export is disabled. Set FHIR_EXPORT_ENABLED=true to enable.",
        )
    
    try:
        service = FHIRExportService(db)
        
        # Convert patient info to dict
        patient_info = None
        if request.patient_info:
            patient_info = request.patient_info.model_dump(exclude_none=True)
        
        result = service.save_fhir_export_for_workflow(
            workflow_id=workflow_id,
            patient_id=request.patient_id,
            patient_info=patient_info,
            output_format=request.output_format,
        )
        
        if not result.get("success"):
            raise HTTPException(
                status_code=500,
                detail=result.get("error", "Failed to save FHIR export"),
            )
        
        return FHIRSaveResponse(
            success=True,
            files_saved=result.get("files_saved", []),
            report_directory=result.get("report_directory"),
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving FHIR export for workflow {workflow_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error saving FHIR export: {str(e)}",
        )


@router.get("/save/run/{run_id}/quick")
async def quick_save_fhir_export(
    run_id: str,
    output_format: str = Query("json", description="Output format: json, xml, or both"),
    patient_id: Optional[str] = Query(None, description="Patient ID for subdirectory"),
    db: Session = Depends(get_db),
    current_user: str = Depends(get_optional_user),
):
    """
    Quick save: Export and save FHIR with minimal options (GET request).
    
    Convenience endpoint for simple FHIR exports without patient info.
    Files are saved to /data/reports/{patient_id or run_id}/.
    
    Args:
        run_id: PharmCAT run ID to export
        output_format: json, xml, or both
        patient_id: Optional subdirectory name
        
    Returns:
        Information about saved files
    """
    if not FHIR_EXPORT_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="FHIR export is disabled. Set FHIR_EXPORT_ENABLED=true to enable.",
        )
    
    try:
        service = FHIRExportService(db)
        result = service.save_fhir_export(
            run_id=run_id,
            patient_id=patient_id,
            output_format=output_format,
        )
        
        if not result.get("success"):
            raise HTTPException(
                status_code=500,
                detail=result.get("error", "Failed to save FHIR export"),
            )
        
        return {
            "success": True,
            "message": f"FHIR export saved successfully",
            "files_saved": result.get("files_saved", []),
            "report_directory": result.get("report_directory"),
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in quick save FHIR export for run {run_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error saving FHIR export: {str(e)}",
        )


def _count_resources(bundle: dict) -> dict:
    """Count resources in a FHIR Bundle by type."""
    counts = {}
    entries = bundle.get("entry", [])
    for entry in entries:
        resource = entry.get("resource", {})
        resource_type = resource.get("resourceType", "Unknown")
        counts[resource_type] = counts.get(resource_type, 0) + 1
    return counts

