"""
PharmCAT API Router for ZaroPGx
Provides REST API endpoints for PharmCAT data parsing and querying
"""

import logging
from typing import List, Optional, Dict, Any
from pathlib import Path
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.api.db import get_db
from app.pharmcat.pharmcat_parser import PharmCATParser, load_pharmcat_file, get_pharmcat_summary
from app.services.pharmcat_data_service import PharmCATDataService

# Configure logging
logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="/api/pharmcat", tags=["pharmcat"])

# ============================================================================
# Pydantic Models for API
# ============================================================================

class PharmCATLoadResponse(BaseModel):
    """Response model for PharmCAT file loading"""
    run_id: str = Field(..., description="Unique identifier for the PharmCAT run")
    message: str = Field(..., description="Status message")
    total_genes: int = Field(..., description="Total number of genes analyzed")
    total_diplotypes: int = Field(..., description="Total number of diplotypes found")
    actionable_findings: int = Field(..., description="Number of actionable findings")
    warning_messages: int = Field(..., description="Number of warning messages")


class GeneSummary(BaseModel):
    """Model for gene summary information"""
    gene_symbol: str
    call_source: Optional[str] = None
    phenotype_source: Optional[str] = None
    chromosome: Optional[str] = None
    phased: Optional[bool] = None


class DiplotypeInfo(BaseModel):
    """Model for diplotype information"""
    gene_symbol: str
    diplotype_label: Optional[str] = None
    allele1_name: Optional[str] = None
    allele1_function: Optional[str] = None
    allele2_name: Optional[str] = None
    allele2_function: Optional[str] = None
    activity_score: Optional[float] = None
    phenotype: Optional[str] = None


class DrugInfo(BaseModel):
    """Model for drug information"""
    drug_name: str
    drug_id: Optional[str] = None


class MessageInfo(BaseModel):
    """Model for message information"""
    gene_symbol: Optional[str] = None
    rule_name: Optional[str] = None
    exception_type: Optional[str] = None
    message: Optional[str] = None


class ActionableFinding(BaseModel):
    """Model for actionable findings"""
    gene_symbol: str
    diplotype_label: Optional[str] = None
    phenotype: Optional[str] = None
    activity_score: Optional[float] = None
    allele1_name: Optional[str] = None
    allele2_name: Optional[str] = None


class PharmCATSummary(BaseModel):
    """Model for comprehensive PharmCAT summary"""
    run_id: str
    total_genes: int
    total_diplotypes: int
    actionable_findings: int
    total_messages: int
    genes: List[GeneSummary]
    actionable_findings_list: List[ActionableFinding]
    warning_messages: List[MessageInfo]


# ============================================================================
# API Endpoints
# ============================================================================

@router.post("/load", response_model=PharmCATLoadResponse)
async def load_pharmcat_file_endpoint(
    file: UploadFile = File(..., description="PharmCAT JSON file"),
    db: Session = Depends(get_db)
):
    """
    Load a PharmCAT JSON file into the database
    
    This endpoint accepts a PharmCAT JSON file upload and parses it into
    the database for querying and analysis.
    """
    try:
        # Validate file type
        if not file.filename.endswith('.json'):
            raise HTTPException(status_code=400, detail="File must be a JSON file")
        
        # Read file content
        content = await file.read()
        
        # Parse JSON
        import json
        try:
            data = json.loads(content.decode('utf-8'))
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"Invalid JSON file: {e}")
        
        # Load into database
        with PharmCATParser(db) as parser:
            run_id = parser.parse_and_load(data)
            
            # Get summary for response
            summary = get_pharmcat_summary(run_id, db)
            
            return PharmCATLoadResponse(
                run_id=run_id,
                message="PharmCAT file loaded successfully",
                total_genes=summary['total_genes'],
                total_diplotypes=summary['total_diplotypes'],
                actionable_findings=summary['actionable_findings'],
                warning_messages=len(summary['warning_messages'])
            )
    
    except Exception as e:
        logger.error(f"Error loading PharmCAT file: {e}")
        raise HTTPException(status_code=500, detail=f"Error loading file: {str(e)}")


@router.get("/workflow/{workflow_id}/summary", response_model=PharmCATSummary)
async def get_pharmcat_summary_by_workflow(
    workflow_id: str,
    db: Session = Depends(get_db)
):
    """
    Get PharmCAT summary for a specific workflow
    
    This endpoint retrieves PharmCAT summary data using the workflow ID,
    which is linked to the PharmCAT run in the database.
    """
    try:
        pharmcat_service = PharmCATDataService(db)
        summary = pharmcat_service.get_workflow_pharmcat_summary(workflow_id)
        
        if not summary:
            raise HTTPException(status_code=404, detail="No PharmCAT data found for this workflow")
        
        return PharmCATSummary(
            run_id=summary.get('run_id', ''),
            pharmcat_version=summary.get('pharmcat_version', 'Unknown'),
            total_genes=summary.get('total_genes', 0),
            total_diplotypes=summary.get('total_diplotypes', 0),
            actionable_findings=summary.get('actionable_findings', []),
            warning_messages=summary.get('warning_messages', [])
        )
    
    except Exception as e:
        logger.error(f"Error getting PharmCAT summary for workflow {workflow_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting summary: {str(e)}")


@router.get("/workflow/{workflow_id}/data")
async def get_pharmcat_data_by_workflow(
    workflow_id: str,
    db: Session = Depends(get_db)
):
    """
    Get complete PharmCAT data for a specific workflow
    
    This endpoint retrieves all PharmCAT data (genes, diplotypes, recommendations, etc.)
    using the workflow ID, which is linked to the PharmCAT run in the database.
    """
    try:
        pharmcat_service = PharmCATDataService(db)
        data = pharmcat_service.get_pharmcat_data_for_workflow(workflow_id)
        
        if not data:
            raise HTTPException(status_code=404, detail="No PharmCAT data found for this workflow")
        
        return data
    
    except Exception as e:
        logger.error(f"Error getting PharmCAT data for workflow {workflow_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting data: {str(e)}")


@router.get("/summary/{run_id}", response_model=PharmCATSummary)
async def get_pharmcat_summary_endpoint(
    run_id: str,
    db: Session = Depends(get_db)
):
    """
    Get comprehensive summary of a PharmCAT run
    
    Returns detailed information about genes, diplotypes, actionable findings,
    and messages for a specific PharmCAT run.
    """
    try:
        with PharmCATParser(db) as parser:
            summary = get_pharmcat_summary(run_id, db)
            
            # Convert to response model
            return PharmCATSummary(
                run_id=summary['run_id'],
                total_genes=summary['total_genes'],
                total_diplotypes=summary['total_diplotypes'],
                actionable_findings=summary['actionable_findings'],
                total_messages=summary['total_messages'],
                genes=[GeneSummary(**gene) for gene in summary['genes']],
                actionable_findings_list=[ActionableFinding(**finding) for finding in summary['actionable_findings']],
                warning_messages=[MessageInfo(**msg) for msg in summary['warning_messages']]
            )
    
    except Exception as e:
        logger.error(f"Error getting PharmCAT summary for {run_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting summary: {str(e)}")


@router.get("/genes/{run_id}", response_model=List[GeneSummary])
async def get_genes(
    run_id: str,
    db: Session = Depends(get_db)
):
    """
    Get gene summary for a PharmCAT run
    
    Returns information about all genes analyzed in the specified run.
    """
    try:
        with PharmCATParser(db) as parser:
            genes = parser.get_gene_summary(run_id)
            return [GeneSummary(**gene) for gene in genes]
    
    except Exception as e:
        logger.error(f"Error getting genes for {run_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting genes: {str(e)}")


@router.get("/diplotypes/{run_id}", response_model=List[DiplotypeInfo])
async def get_diplotypes(
    run_id: str,
    gene_symbol: Optional[str] = Query(None, description="Filter by gene symbol"),
    db: Session = Depends(get_db)
):
    """
    Get diplotype information for a PharmCAT run
    
    Returns diplotype calls and phenotypes for all genes or a specific gene.
    """
    try:
        with PharmCATParser(db) as parser:
            diplotypes = parser.get_diplotypes(run_id, gene_symbol)
            return [DiplotypeInfo(**diplotype) for diplotype in diplotypes]
    
    except Exception as e:
        logger.error(f"Error getting diplotypes for {run_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting diplotypes: {str(e)}")


@router.get("/drugs/{run_id}", response_model=List[DrugInfo])
async def get_drugs_by_gene(
    run_id: str,
    gene_symbol: str = Query(..., description="Gene symbol to get drugs for"),
    db: Session = Depends(get_db)
):
    """
    Get drugs related to a specific gene for a PharmCAT run
    
    Returns all drugs that are affected by the specified gene.
    """
    try:
        with PharmCATParser(db) as parser:
            drugs = parser.get_drugs_by_gene(run_id, gene_symbol)
            return [DrugInfo(**drug) for drug in drugs]
    
    except Exception as e:
        logger.error(f"Error getting drugs for {run_id}, gene {gene_symbol}: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting drugs: {str(e)}")


@router.get("/messages/{run_id}", response_model=List[MessageInfo])
async def get_messages(
    run_id: str,
    gene_symbol: Optional[str] = Query(None, description="Filter by gene symbol"),
    db: Session = Depends(get_db)
):
    """
    Get messages and warnings for a PharmCAT run
    
    Returns all messages, warnings, and errors from the PharmCAT analysis.
    """
    try:
        with PharmCATParser(db) as parser:
            messages = parser.get_messages(run_id, gene_symbol)
            return [MessageInfo(**message) for message in messages]
    
    except Exception as e:
        logger.error(f"Error getting messages for {run_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting messages: {str(e)}")


@router.get("/actionable/{run_id}", response_model=List[ActionableFinding])
async def get_actionable_findings(
    run_id: str,
    db: Session = Depends(get_db)
):
    """
    Get actionable findings for a PharmCAT run
    
    Returns only the pharmacogenomic findings that require clinical attention
    (excludes normal metabolizers and uncertain susceptibility).
    """
    try:
        with PharmCATParser(db) as parser:
            findings = parser.get_actionable_findings(run_id)
            return [ActionableFinding(**finding) for finding in findings]
    
    except Exception as e:
        logger.error(f"Error getting actionable findings for {run_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting actionable findings: {str(e)}")


@router.get("/runs", response_model=List[Dict[str, Any]])
async def list_pharmcat_runs(
    limit: int = Query(10, ge=1, le=100, description="Maximum number of runs to return"),
    offset: int = Query(0, ge=0, description="Number of runs to skip"),
    db: Session = Depends(get_db)
):
    """
    List all PharmCAT runs in the database
    
    Returns a paginated list of all PharmCAT runs with basic metadata.
    """
    try:
        from app.pharmcat.pharmcat_parser import PharmCATResult
        
        # Query runs with pagination
        runs = db.query(PharmCATResult).offset(offset).limit(limit).all()
        
        return [
            {
                'run_id': run.run_id,
                'run_timestamp': run.run_timestamp,
                'pharmcat_version': run.pharmcat_version,
                'data_version': run.data_version,
                'loaded_at': run.loaded_at
            }
            for run in runs
        ]
    
    except Exception as e:
        logger.error(f"Error listing PharmCAT runs: {e}")
        raise HTTPException(status_code=500, detail=f"Error listing runs: {str(e)}")


@router.delete("/runs/{run_id}")
async def delete_pharmcat_run(
    run_id: str,
    db: Session = Depends(get_db)
):
    """
    Delete a PharmCAT run and all associated data
    
    Permanently removes the specified PharmCAT run and all related data
    from the database.
    """
    try:
        from app.pharmcat.pharmcat_parser import PharmCATResult
        
        # Find the run
        run = db.query(PharmCATResult).filter(PharmCATResult.run_id == run_id).first()
        if not run:
            raise HTTPException(status_code=404, detail=f"PharmCAT run {run_id} not found")
        
        # Delete the run (cascade will handle related data)
        db.delete(run)
        db.commit()
        
        return {"message": f"PharmCAT run {run_id} deleted successfully"}
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting PharmCAT run {run_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error deleting run: {str(e)}")


# ============================================================================
# Health Check Endpoint
# ============================================================================

@router.get("/health")
async def health_check():
    """Health check endpoint for PharmCAT API"""
    return {
        "status": "healthy",
        "service": "pharmcat-api",
        "version": "1.0.0"
    }
