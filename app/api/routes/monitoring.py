"""
Monitoring router for job status
"""
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from sqlalchemy.orm import Session

from app.api.db import get_db
from app.services.job_status_service import JobStatusService
from app.services.workflow_service import WorkflowService
from app.api.models import (
    JobCreate, JobUpdate, JobResponse, JobStageResponse, JobEventResponse,
    WorkflowProgressResponse
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/monitoring", tags=["monitoring"])


@router.post("/jobs", response_model=JobResponse, status_code=http_status.HTTP_201_CREATED)
async def create_job(
    job_data: JobCreate,
    db: Session = Depends(get_db)
):
    """Create a new job"""
    try:
        job_service = JobStatusService(db)
        job = job_service.create_job(
            patient_id=job_data.patient_id,
            file_id=job_data.file_id,
            initial_stage=job_data.initial_stage.value,
            metadata=job_data.job_metadata
        )
        return JobResponse.model_validate(job)
    except ValueError as e:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid request: {str(e)}"
        )
    except RuntimeError as e:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create job: {str(e)}"
        )


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: str,
    db: Session = Depends(get_db)
):
    """Get a job by ID"""
    try:
        job_service = JobStatusService(db)
        job_status = job_service.get_job_status(job_id)
        
        if not job_status:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=f"Job {job_id} not found"
            )
        
        return JobResponse.model_validate(job_status)
    except ValueError as e:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid request: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error getting job {job_id}: {str(e)}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )


@router.put("/jobs/{job_id}", response_model=JobResponse)
async def update_job(
    job_id: str,
    update_data: JobUpdate,
    db: Session = Depends(get_db)
):
    """Update a job"""
    try:
        job_service = JobStatusService(db)
        
        # Update stage if provided
        if update_data.stage:
            job_service.update_job_stage(job_id, update_data.stage.value)
        
        # Update progress if provided
        if update_data.progress is not None:
            job_service.update_job_progress(job_id, update_data.progress)
        
        # Update message if provided
        if update_data.message:
            job_service.update_job_message(job_id, update_data.message)
        
        # Update error message if provided
        if update_data.error_message:
            job_service.update_job_error(job_id, update_data.error_message)
        
        # Get updated job
        job_status = job_service.get_job_status(job_id)
        if not job_status:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=f"Job {job_id} not found"
            )
        
        return JobResponse.model_validate(job_status)
    except ValueError as e:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid request: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error updating job {job_id}: {str(e)}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )



@router.get("/jobs", response_model=List[JobResponse])
async def list_jobs(
    status: Optional[str] = Query(None, description="Filter by status"),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db)
):
    """List jobs with optional status filtering"""
    try:
        job_service = JobStatusService(db)
        
        if status:
            jobs = job_service.get_jobs_by_status(status, limit=limit)
        else:
            # For now, get pending jobs as default
            jobs = job_service.get_jobs_by_status("pending", limit=limit)
        
        return [JobResponse.model_validate(job) for job in jobs]
    except Exception as e:
        logger.error(f"Unexpected error listing jobs: {str(e)}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )


@router.get("/jobs/status/{status}", response_model=List[JobResponse])
async def get_jobs_by_status(
    status: str,
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db)
):
    """Get jobs by status"""
    try:
        job_service = JobStatusService(db)
        jobs = job_service.get_jobs_by_status(status, limit=limit)
        
        return [JobResponse.model_validate(job) for job in jobs]
    except Exception as e:
        logger.error(f"Unexpected error getting jobs by status {status}: {str(e)}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )


@router.delete("/jobs/{job_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_job(
    job_id: str,
    db: Session = Depends(get_db)
):
    """Delete a job"""
    try:
        job_service = JobStatusService(db)
        success = job_service.delete_job(job_id)
        
        if not success:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=f"Job {job_id} not found"
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error deleting job {job_id}: {str(e)}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )


@router.get("/progress/{workflow_id}", response_model=WorkflowProgressResponse)
async def get_workflow_progress(
    workflow_id: str,
    db: Session = Depends(get_db)
):
    """Get workflow progress information"""
    try:
        workflow_service = WorkflowService(db)
        progress = workflow_service.get_workflow_progress(workflow_id)
        
        if not progress:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=f"Workflow {workflow_id} not found"
            )
        
        return progress
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error getting workflow progress {workflow_id}: {str(e)}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get workflow progress: {str(e)}"
        )
