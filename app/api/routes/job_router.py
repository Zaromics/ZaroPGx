"""
Job API Router

This module provides REST API endpoints for job management including:
- Job CRUD operations
- Step management
- Progress monitoring
- Logging and debugging
"""

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import List, Optional

import requests
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from sqlalchemy.orm import Session

from app.api.db import get_db
from app.api.models import (
    JobCreate,
    JobLogCreate,
    JobLogResponse,
    JobProgressResponse,
    JobResponse,
    JobStepCreate,
    JobStepResponse,
    JobStepUpdate,
    JobUpdate,
)
from app.services.websocket_manager import connection_manager
from app.services.job_service import JobService

logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


@router.post("/", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(job_data: JobCreate, db: Session = Depends(get_db)):
    """
    Create a new job.

    This endpoint creates a new job with the specified configuration.
    The job will be in 'pending' status until steps are added and execution begins.
    """
    try:
        job_service = JobService(db)
        job = job_service.create_job(job_data)

        return JobResponse(
            id=str(job.id),
            name=job.name,
            description=job.description,
            status=job.status,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
            total_steps=job.total_steps,
            completed_steps=job.completed_steps,
            metadata=job.job_metadata,
            created_by=job.created_by,
        )

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating workflow: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create workflow",
        )


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: str, db: Session = Depends(get_db)):
    """
    Get workflow by ID.

    Returns the complete workflow information including status, progress, and metadata.
    """
    try:
        job_service = JobService(db)
        job = job_service.get_job(job_id)

        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
            )

        return JobResponse(
            id=str(job.id),
            name=job.name,
            description=job.description,
            status=job.status,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
            total_steps=job.total_steps,
            completed_steps=job.completed_steps,
            metadata=job.job_metadata,
            created_by=job.created_by,
        )

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting workflow: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get workflow",
        )


@router.put("/{job_id}", response_model=JobResponse)
async def update_job(
    job_id: str, update_data: JobUpdate, db: Session = Depends(get_db)
):
    """
    Update job.

    Updates workflow fields including status, progress, and metadata.
    """
    try:
        job_service = JobService(db)
        job = job_service.update_job(job_id, update_data)

        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
            )

        return JobResponse(
            id=str(job.id),
            name=job.name,
            description=job.description,
            status=job.status,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
            total_steps=job.total_steps,
            completed_steps=job.completed_steps,
            metadata=job.job_metadata,
            created_by=job.created_by,
        )

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating workflow: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update workflow",
        )


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(job_id: str, db: Session = Depends(get_db)):
    """
    Delete job.

    Permanently deletes the workflow and all associated steps and logs.
    """
    try:
        job_service = JobService(db)
        job = job_service.get_job(job_id)

        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
            )

        # Delete workflow (cascade will handle steps and logs)
        db.delete(job)
        db.commit()

        logger.info(f"Deleted job {job_id}")

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Error deleting workflow: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete workflow",
        )


@router.post(
    "/{job_id}/steps",
    response_model=JobStepResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_job_step(
    job_id: str, step_data: JobStepCreate, db: Session = Depends(get_db)
):
    """
    Add a step to a job.

    Adds a new step to the specified workflow with the given configuration.
    """
    try:
        job_service = JobService(db)
        step = job_service.add_job_step(job_id, step_data)

        if not step:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
            )

        return JobStepResponse(
            id=str(step.id),
            job_id=str(step.job_id),
            step_name=step.step_name,
            step_order=step.step_order,
            status=step.status,
            container_name=step.container_name,
            started_at=step.started_at,
            completed_at=step.completed_at,
            duration_seconds=step.duration_seconds,
            output_data=step.output_data,
            error_details=step.error_details,
            retry_count=step.retry_count,
        )

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Error adding workflow step: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to add workflow step",
        )


@router.get("/{job_id}/steps", response_model=List[JobStepResponse])
async def get_job_steps(job_id: str, db: Session = Depends(get_db)):
    """
    Get all steps for a workflow, ordered by step_order.
    """
    try:
        job_service = JobService(db)

        # Check the workflow itself exists first: get_job_steps() returns an
        # empty list both for "no such workflow" and "no steps yet", and callers
        # need to tell those apart.
        if not job_service.get_job(job_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
            )

        return job_service.get_job_steps(job_id)

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting workflow steps: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get workflow steps",
        )


@router.put("/{job_id}/steps/{step_name}", response_model=JobStepResponse)
async def update_job_step(
    job_id: str,
    step_name: str,
    update_data: JobStepUpdate,
    db: Session = Depends(get_db),
):
    """
    Update a workflow step.

    Updates the status and other properties of a specific workflow step.
    """
    try:
        job_service = JobService(db)
        step = job_service.update_job_step(
            job_id, step_name, update_data
        )

        if not step:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Job step not found"
            )

        return JobStepResponse(
            id=str(step.id),
            job_id=str(step.job_id),
            step_name=step.step_name,
            step_order=step.step_order,
            status=step.status,
            container_name=step.container_name,
            started_at=step.started_at,
            completed_at=step.completed_at,
            duration_seconds=step.duration_seconds,
            output_data=step.output_data,
            error_details=step.error_details,
            retry_count=step.retry_count,
        )

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating workflow step: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update workflow step",
        )


@router.get("/{job_id}/progress", response_model=JobProgressResponse)
async def get_job_progress(job_id: str, db: Session = Depends(get_db)):
    """
    Get workflow progress.

    Returns detailed progress information including completion percentage,
    current step, and estimated completion time.
    """
    try:
        job_service = JobService(db)
        progress = job_service.get_job_progress(job_id)

        if not progress:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
            )

        return progress

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting workflow progress: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get workflow progress",
        )


@router.post(
    "/{job_id}/logs",
    response_model=JobLogResponse,
    status_code=status.HTTP_201_CREATED,
)
async def log_job_event(
    job_id: str, log_data: JobLogCreate, db: Session = Depends(get_db)
):
    """
    Log a workflow event.

    Adds a log entry to the workflow for debugging and monitoring purposes.
    """
    try:
        job_service = JobService(db)
        log_entry = job_service.log_job_event(job_id, log_data)

        return JobLogResponse(
            id=log_entry.id,
            job_id=str(log_entry.job_id),
            step_name=log_entry.step_name,
            log_level=log_entry.log_level,
            message=log_entry.message,
            metadata=log_entry.log_metadata,
            timestamp=log_entry.timestamp,
        )

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Error logging workflow event: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to log workflow event",
        )


@router.get("/{job_id}/logs", response_model=List[JobLogResponse])
async def get_job_logs(
    job_id: str, limit: int = 100, db: Session = Depends(get_db)
):
    """
    Get workflow logs.

    Retrieves the log entries for a workflow, ordered by timestamp (newest first).
    """
    try:
        job_service = JobService(db)

        # Same contract as GET /{job_id}/steps: an unknown workflow is a 404,
        # not an empty list. get_job_logs() cannot distinguish "no such
        # workflow" from "no logs yet".
        if not job_service.get_job(job_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
            )

        logs = job_service.get_job_logs(job_id, limit)

        return [
            JobLogResponse(
                id=log.id,
                job_id=str(log.job_id),
                step_name=log.step_name,
                log_level=log.log_level,
                message=log.message,
                metadata=log.log_metadata,
                timestamp=log.timestamp,
            )
            for log in logs
        ]

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting workflow logs: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get workflow logs",
        )


@router.websocket("/{job_id}/ws")
async def websocket_endpoint(websocket: WebSocket, job_id: str):
    """
    WebSocket endpoint for real-time workflow updates.

    This endpoint provides real-time updates for workflow progress,
    step status changes, and log messages.
    """
    connection_id = None
    try:
        # Validate job_id format
        try:
            uuid.UUID(job_id)
        except ValueError:
            await websocket.close(code=4000, reason="Invalid workflow ID format")
            return

        # Connect to the workflow
        connection_id = await connection_manager.connect(websocket, job_id)

        # Send initial workflow status
        try:
            db = next(get_db())
            logger.info(f"Database connection established for workflow {job_id}")

            job_service = JobService(db)
            job = job_service.get_job(job_id)

            if job:
                logger.info(
                    f"Job found: {job.name} (status: {job.status})"
                )

                # Get proper progress calculation using WorkflowProgressCalculator
                progress_response = job_service.get_job_progress(job_id)

                initial_message = {
                    "job_id": str(job.id),
                    "name": job.name,
                    "status": job.status,
                    "total_steps": job.total_steps,
                    "completed_steps": job.completed_steps,
                    "progress_percentage": (
                        progress_response.progress_percentage
                        if progress_response
                        else 0
                    ),
                    "current_step": (
                        progress_response.current_step
                        if progress_response
                        else "unknown"
                    ),
                    "message": (
                        progress_response.message
                        if progress_response
                        else "Starting workflow"
                    ),
                    "created_at": job.created_at.isoformat(),
                    "started_at": (
                        job.started_at.isoformat() if job.started_at else None
                    ),
                    "completed_at": (
                        job.completed_at.isoformat()
                        if job.completed_at
                        else None
                    ),
                }

                await websocket.send_text(
                    json.dumps({"type": "initial_status", "data": initial_message})
                )
                logger.info(f"Initial status sent for workflow {job_id}")
            else:
                logger.warning(f"Job not found: {job_id}")
                await websocket.send_text(
                    json.dumps({"type": "error", "message": "Job not found"})
                )
                await websocket.close(code=4004, reason="Job not found")
                return

        except Exception as e:
            logger.error(
                f"Error in WebSocket endpoint for workflow {job_id}: {str(e)}"
            )
            await websocket.send_text(
                json.dumps(
                    {"type": "error", "message": f"Internal server error: {str(e)}"}
                )
            )
            await websocket.close(code=4000, reason="Internal server error")
            return
        finally:
            if "db" in locals():
                db.close()

        # Keep connection alive and handle incoming messages
        while True:
            try:
                # Use asyncio.wait_for to make receive_text non-blocking
                # This allows us to handle both incoming messages and timeouts
                try:
                    data = await asyncio.wait_for(
                        websocket.receive_text(), timeout=30.0
                    )
                    message = json.loads(data)

                    # Handle client messages
                    if message.get("type") == "ping":
                        await websocket.send_text(
                            json.dumps(
                                {"type": "pong", "timestamp": message.get("timestamp")}
                            )
                        )
                    elif message.get("type") == "subscribe":
                        # Client can subscribe to specific events
                        logger.info(f"Client subscribed to workflow {job_id}")

                except asyncio.TimeoutError:
                    # Send heartbeat to keep connection alive
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "heartbeat",
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            }
                        )
                    )
                    continue

            except WebSocketDisconnect:
                logger.info(f"WebSocket disconnected for workflow {job_id}")
                break
            except json.JSONDecodeError:
                logger.warning(
                    f"Invalid JSON received from WebSocket for workflow {job_id}"
                )
                continue
            except Exception as e:
                logger.error(
                    f"Error handling WebSocket message for workflow {job_id}: {e}"
                )
                continue

    except Exception as e:
        logger.error(f"WebSocket error for workflow {job_id}: {e}")
        try:
            await websocket.close(code=1011, reason="Internal server error")
        except Exception:
            logger.debug(
                "Failed closing WebSocket after error for workflow %s",
                job_id,
                exc_info=True,
            )
    finally:
        # Clean up connection
        if connection_id:
            connection_manager.disconnect(websocket, connection_id)


async def cancel_nextflow_job(job_id: str, job_metadata: dict):
    """
    Cancel a running Nextflow job by calling the Nextflow runner API.

    Args:
        job_id: The workflow ID to cancel
        job_metadata: Job metadata containing job information
    """
    try:
        # Get Nextflow runner URL
        nextflow_url = os.getenv("NEXTFLOW_RUNNER_URL", "http://nextflow:5055")

        # Extract job information from metadata
        patient_id = job_metadata.get("patient_id")
        data_id = job_metadata.get("data_id")

        if not patient_id:
            logger.warning(
                f"No patient_id found in workflow metadata for {job_id}"
            )
            return

        # Construct job key (same format as used in Nextflow runner)
        job_key = f"{patient_id}_{data_id or patient_id}"

        # Call Nextflow cancel endpoint
        cancel_url = f"{nextflow_url}/cancel/{job_key}"
        logger.info(f"Cancelling Nextflow job {job_key} at {cancel_url}")

        response = requests.post(cancel_url, timeout=10)

        if response.status_code == 200:
            logger.info(f"Successfully cancelled Nextflow job {job_key}")
        elif response.status_code == 404:
            logger.info(
                f"Nextflow job {job_key} not found (may have already completed)"
            )
        else:
            logger.warning(
                f"Failed to cancel Nextflow job {job_key}: {response.status_code} - {response.text}"
            )

    except Exception as e:
        logger.error(f"Error cancelling Nextflow job for workflow {job_id}: {e}")
        raise


async def cancel_container_jobs(job_id: str, job_metadata: dict):
    """
    Cancel running jobs in all container services using a standardized cancel endpoint.

    All containers should implement: POST /cancel with job_id in the payload.
    This is much simpler than trying multiple endpoint patterns.

    Args:
        job_id: The workflow ID to cancel
        job_metadata: Job metadata containing job information
    """
    patient_id = job_metadata.get("patient_id")
    if not patient_id:
        logger.warning(f"No patient_id found in workflow metadata for {job_id}")
        return

    # List of container services with standardized cancel endpoint
    containers = [
        {"name": "gatk-api", "url": "http://gatk-api:5000"},
        {"name": "zarohla", "url": "http://zarohla:5000"},
        {"name": "pypgx", "url": "http://pypgx:5000"},
        {"name": "pharmcat", "url": "http://pharmcat:5000"},
    ]

    # Cancel jobs in each container using standardized endpoint
    for container in containers:
        try:
            await cancel_container_job(container, patient_id, job_id)
        except Exception as e:
            logger.warning(f"Failed to cancel job in {container['name']}: {e}")


async def cancel_container_job(container: dict, patient_id: str, job_id: str):
    """
    Cancel a job in a specific container service using standardized endpoint.

    All containers should implement: POST /cancel
    Payload: {"job_id": "...", "patient_id": "...", "action": "cancel"}

    Args:
        container: Container configuration dict with name, url
        patient_id: Patient ID to cancel jobs for
        job_id: Job ID for logging
    """
    try:
        cancel_url = f"{container['url']}/cancel"
        logger.info(f"Cancelling job in {container['name']} at {cancel_url}")

        payload = {
            "job_id": job_id,
            "workflow_id": job_id,  # dual-key for one-release transition
            "patient_id": patient_id,
            "action": "cancel",
        }

        response = requests.post(cancel_url, json=payload, timeout=30)

        if response.status_code == 200:
            logger.info(f"Successfully cancelled job in {container['name']}")
        elif response.status_code == 404:
            logger.info(
                f"No running job found in {container['name']} for workflow {job_id}"
            )
        else:
            logger.warning(
                f"Cancel request to {container['name']} returned {response.status_code}: {response.text}"
            )

    except requests.exceptions.Timeout as e:
        logger.warning(f"Timeout cancelling job in {container['name']} (30s): {e}")
    except requests.exceptions.ConnectionError as e:
        logger.warning(f"Connection error cancelling job in {container['name']}: {e}")
    except requests.exceptions.RequestException as e:
        logger.warning(f"Request error cancelling job in {container['name']}: {e}")
    except Exception as e:
        logger.error(f"Unexpected error cancelling job in {container['name']}: {e}")


@router.post("/{job_id}/cancel", response_model=JobResponse)
async def cancel_job(job_id: str, db: Session = Depends(get_db)):
    """
    Cancel a running job.

    This endpoint cancels a workflow that is currently running or pending.
    The workflow status will be updated to 'cancelled' and all running steps will be stopped.
    """
    try:
        job_service = JobService(db)

        # Get the workflow
        job = job_service.get_job(job_id)
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
            )

        # Check if workflow can be cancelled
        if job.status in ["completed", "failed", "cancelled"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Job cannot be cancelled. Current status: {job.status}",
            )

        # Cancel processes FIRST, then update database status
        # This prevents race conditions where new processes start after status update

        # Prepare cancellation metadata
        from app.api.models import JobStatus

        cancellation_metadata = (
            job.job_metadata.copy() if job.job_metadata else {}
        )
        cancellation_metadata["cancelled"] = True
        cancellation_metadata["cancelled_at"] = datetime.now(timezone.utc).isoformat()

        # STEP 1: Immediately stop all running processes
        logger.info(f"Stopping all processes for workflow {job_id}")

        # Cancel Nextflow job (orchestrator) first
        try:
            await cancel_nextflow_job(job_id, job.job_metadata)
            logger.info(
                f"Successfully cancelled Nextflow job for workflow {job_id}"
            )
        except Exception as e:
            logger.warning(
                f"Failed to cancel Nextflow job for workflow {job_id}: {e}"
            )

        # Cancel individual container jobs
        try:
            await cancel_container_jobs(job_id, job.job_metadata)
            logger.info(
                f"Successfully cancelled container jobs for workflow {job_id}"
            )
        except Exception as e:
            logger.warning(
                f"Failed to cancel some container jobs for workflow {job_id}: {e}"
            )

        # Note: File cleanup is handled by individual containers when they detect cancellation
        # The app container will perform delayed cleanup to ensure any in-progress operations complete

        # STEP 2: Update database status AFTER processes are stopped
        # This ensures no new processes can start (they check DB status)
        logger.info(f"Updating database status to cancelled for workflow {job_id}")

        job_update = JobUpdate(
            status=JobStatus.CANCELLED, metadata=cancellation_metadata
        )
        updated_job = job_service.update_job(
            job_id, job_update
        )

        if not updated_workflow:
            # Even if DB update fails, processes are already stopped
            logger.error(
                f"Failed to update database status for workflow {job_id}, but processes are stopped"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update workflow status, but processes have been stopped",
            )

        # Log the cancellation
        log_data = JobLogCreate(
            step_name=None,
            log_level="info",
            message="Job cancelled by user - stopping all running processes",
        )
        job_service.log_job_event(job_id, log_data)

        # Broadcast cancellation via WebSocket to all connected clients
        try:
            from app.services.websocket_manager import connection_manager

            await connection_manager.broadcast_cancellation(job_id)
        except Exception as e:
            logger.warning(f"Failed to broadcast cancellation via WebSocket: {e}")

        logger.info(f"Job {job_id} cancelled successfully")

        return JobResponse(
            id=str(updated_job.id),
            name=updated_job.name,
            description=updated_job.description,
            status=updated_job.status,
            total_steps=updated_job.total_steps,
            completed_steps=updated_job.completed_steps,
            created_at=updated_job.created_at,
            started_at=updated_job.started_at,
            completed_at=updated_job.completed_at,
            metadata=updated_job.job_metadata,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cancelling workflow {job_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to cancel workflow: {str(e)}",
        )
