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
from app.api.models import (
    JobCreate, JobUpdate, JobResponse, JobStageResponse, JobEventResponse
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


@router.get("/progress/{identifier}")
async def get_progress_stream(
    identifier: str,
    db: Session = Depends(get_db)
):
    """
    Stream job progress as Server-Sent Events
    
    The identifier can be either a job_id or file_id.
    If it's a file_id, we'll lookup the corresponding job_id.
    """
    from fastapi.responses import StreamingResponse
    from app.services.job_status_service import JobStatusService
    import asyncio
    import json
    import time
    
    # Try to get job by identifier (could be job_id or file_id)
    job_service = JobStatusService(db)
    job = job_service.get_job_status(identifier)
    
    actual_job_id = identifier
    
    # If not found by identifier, try to find by file_id
    if not job:
        # Query the database to find job by file_id
        from sqlalchemy import text
        result = db.execute(
            text("SELECT job_id FROM job_monitoring.jobs WHERE file_id = :file_id"),
            {"file_id": identifier}
        ).scalar()
        
        if result:
            actual_job_id = str(result)
            job = job_service.get_job_status(actual_job_id)
    
    async def progress_event_generator():
        """Generate progress events for the job"""
        last_progress = None
        keepalive_count = 0
        
        while True:
            try:
                # Get current job status
                current_job = job_service.get_job_status(actual_job_id)
                
                if current_job:
                    progress_data = {
                        "job_id": actual_job_id,
                        "status": current_job["status"],
                        "stage": current_job["stage"],
                        "progress": current_job["progress"],
                        "message": current_job["message"] or "Processing...",
                        "timestamp": current_job["updated_at"] if current_job.get("updated_at") else None,
                        "job_metadata": current_job.get("job_metadata", {})
                    }
                    
                    # Only send update if progress has changed
                    if progress_data != last_progress:
                        yield f"data: {json.dumps(progress_data)}\n\n"
                        last_progress = progress_data
                    
                    # Check if job is complete
                    if current_job["status"] in ["completed", "failed", "cancelled"]:
                        # Send final update with success flag and report URLs
                        final_data = {
                            **progress_data, 
                            "complete": True,
                            "success": current_job["status"] == "completed",
                            "data": {
                                "job_id": actual_job_id,
                                "file_id": current_job.get("file_id"),
                                "patient_id": current_job.get("patient_id"),
                                "pdf_report_url": f"/reports/{current_job.get('patient_id')}/{current_job.get('patient_id')}_pgx_report.pdf",
                                "html_report_url": f"/reports/{current_job.get('patient_id')}/{current_job.get('patient_id')}_pgx_report_interactive.html"
                            }
                        }
                        yield f"data: {json.dumps(final_data)}\n\n"
                        break
                        
                else:
                    # Job not found - send error and break
                    error_data = {
                        "error": f"Job {identifier} not found",
                        "reconnect": False
                    }
                    yield f"data: {json.dumps(error_data)}\n\n"
                    break
                
                # Send keepalive every 30 seconds
                keepalive_count += 1
                if keepalive_count % 30 == 0:
                    keepalive_data = {"keepalive": True, "timestamp": time.time()}
                    yield f"data: {json.dumps(keepalive_data)}\n\n"
                
                # Wait before next check
                await asyncio.sleep(1)
                
            except Exception as e:
                logger.error(f"Error in progress stream for {identifier}: {str(e)}")
                error_data = {
                    "error": f"Stream error: {str(e)}",
                    "reconnect": True
                }
                yield f"data: {json.dumps(error_data)}\n\n"
                break
    
    return StreamingResponse(
        progress_event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Content-Type": "text/event-stream",
        }
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
