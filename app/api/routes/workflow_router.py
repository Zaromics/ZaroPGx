"""
Workflow API Router

This module provides REST API endpoints for workflow management including:
- Workflow CRUD operations
- Step management
- Progress monitoring
- Logging and debugging
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from app.api.db import get_db
from app.services.workflow_service import WorkflowService
from app.services.websocket_manager import connection_manager
from app.api.models import (
    WorkflowCreate, WorkflowUpdate, WorkflowResponse,
    WorkflowStepCreate, WorkflowStepUpdate, WorkflowStepResponse,
    WorkflowLogCreate, WorkflowLogResponse, WorkflowProgressResponse
)
import uuid
import logging
import json
import asyncio
import requests
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="/api/v1/workflows", tags=["workflows"])


@router.post("/", response_model=WorkflowResponse, status_code=status.HTTP_201_CREATED)
async def create_workflow(
    workflow_data: WorkflowCreate,
    db: Session = Depends(get_db)
):
    """
    Create a new workflow.
    
    This endpoint creates a new workflow with the specified configuration.
    The workflow will be in 'pending' status until steps are added and execution begins.
    """
    try:
        workflow_service = WorkflowService(db)
        workflow = workflow_service.create_workflow(workflow_data)
        
        return WorkflowResponse(
            id=str(workflow.id),
            name=workflow.name,
            description=workflow.description,
            status=workflow.status,
            created_at=workflow.created_at,
            started_at=workflow.started_at,
            completed_at=workflow.completed_at,
            total_steps=workflow.total_steps,
            completed_steps=workflow.completed_steps,
            metadata=workflow.workflow_metadata,
            created_by=workflow.created_by
        )
        
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating workflow: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create workflow")


@router.get("/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(
    workflow_id: str,
    db: Session = Depends(get_db)
):
    """
    Get workflow by ID.
    
    Returns the complete workflow information including status, progress, and metadata.
    """
    try:
        workflow_service = WorkflowService(db)
        workflow = workflow_service.get_workflow(workflow_id)
        
        if not workflow:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")
        
        return WorkflowResponse(
            id=str(workflow.id),
            name=workflow.name,
            description=workflow.description,
            status=workflow.status,
            created_at=workflow.created_at,
            started_at=workflow.started_at,
            completed_at=workflow.completed_at,
            total_steps=workflow.total_steps,
            completed_steps=workflow.completed_steps,
            metadata=workflow.workflow_metadata,
            created_by=workflow.created_by
        )
        
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting workflow: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get workflow")


@router.put("/{workflow_id}", response_model=WorkflowResponse)
async def update_workflow(
    workflow_id: str,
    update_data: WorkflowUpdate,
    db: Session = Depends(get_db)
):
    """
    Update workflow.
    
    Updates workflow fields including status, progress, and metadata.
    """
    try:
        workflow_service = WorkflowService(db)
        workflow = workflow_service.update_workflow(workflow_id, update_data)
        
        if not workflow:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")
        
        return WorkflowResponse(
            id=str(workflow.id),
            name=workflow.name,
            description=workflow.description,
            status=workflow.status,
            created_at=workflow.created_at,
            started_at=workflow.started_at,
            completed_at=workflow.completed_at,
            total_steps=workflow.total_steps,
            completed_steps=workflow.completed_steps,
            metadata=workflow.workflow_metadata,
            created_by=workflow.created_by
        )
        
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating workflow: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update workflow")


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(
    workflow_id: str,
    db: Session = Depends(get_db)
):
    """
    Delete workflow.
    
    Permanently deletes the workflow and all associated steps and logs.
    """
    try:
        workflow_service = WorkflowService(db)
        workflow = workflow_service.get_workflow(workflow_id)
        
        if not workflow:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")
        
        # Delete workflow (cascade will handle steps and logs)
        db.delete(workflow)
        db.commit()
        
        logger.info(f"Deleted workflow {workflow_id}")
        
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Error deleting workflow: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to delete workflow")


@router.post("/{workflow_id}/steps", response_model=WorkflowStepResponse, status_code=status.HTTP_201_CREATED)
async def add_workflow_step(
    workflow_id: str,
    step_data: WorkflowStepCreate,
    db: Session = Depends(get_db)
):
    """
    Add a step to a workflow.
    
    Adds a new step to the specified workflow with the given configuration.
    """
    try:
        workflow_service = WorkflowService(db)
        step = workflow_service.add_workflow_step(workflow_id, step_data)
        
        if not step:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")
        
        return WorkflowStepResponse(
            id=str(step.id),
            workflow_id=str(step.workflow_id),
            step_name=step.step_name,
            step_order=step.step_order,
            status=step.status,
            container_name=step.container_name,
            started_at=step.started_at,
            completed_at=step.completed_at,
            duration_seconds=step.duration_seconds,
            output_data=step.output_data,
            error_details=step.error_details,
            retry_count=step.retry_count
        )
        
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Error adding workflow step: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to add workflow step")


@router.put("/{workflow_id}/steps/{step_name}", response_model=WorkflowStepResponse)
async def update_workflow_step(
    workflow_id: str,
    step_name: str,
    update_data: WorkflowStepUpdate,
    db: Session = Depends(get_db)
):
    """
    Update a workflow step.
    
    Updates the status and other properties of a specific workflow step.
    """
    try:
        workflow_service = WorkflowService(db)
        step = workflow_service.update_workflow_step(workflow_id, step_name, update_data)
        
        if not step:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow step not found")
        
        return WorkflowStepResponse(
            id=str(step.id),
            workflow_id=str(step.workflow_id),
            step_name=step.step_name,
            step_order=step.step_order,
            status=step.status,
            container_name=step.container_name,
            started_at=step.started_at,
            completed_at=step.completed_at,
            duration_seconds=step.duration_seconds,
            output_data=step.output_data,
            error_details=step.error_details,
            retry_count=step.retry_count
        )
        
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating workflow step: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update workflow step")


@router.get("/{workflow_id}/progress", response_model=WorkflowProgressResponse)
async def get_workflow_progress(
    workflow_id: str,
    db: Session = Depends(get_db)
):
    """
    Get workflow progress.
    
    Returns detailed progress information including completion percentage,
    current step, and estimated completion time.
    """
    try:
        workflow_service = WorkflowService(db)
        progress = workflow_service.get_workflow_progress(workflow_id)
        
        if not progress:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")
        
        return progress
        
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting workflow progress: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get workflow progress")


@router.post("/{workflow_id}/logs", response_model=WorkflowLogResponse, status_code=status.HTTP_201_CREATED)
async def log_workflow_event(
    workflow_id: str,
    log_data: WorkflowLogCreate,
    db: Session = Depends(get_db)
):
    """
    Log a workflow event.
    
    Adds a log entry to the workflow for debugging and monitoring purposes.
    """
    try:
        workflow_service = WorkflowService(db)
        log_entry = workflow_service.log_workflow_event(workflow_id, log_data)
        
        return WorkflowLogResponse(
            id=log_entry.id,
            workflow_id=str(log_entry.workflow_id),
            step_name=log_entry.step_name,
            log_level=log_entry.log_level,
            message=log_entry.message,
            metadata=log_entry.log_metadata,
            timestamp=log_entry.timestamp
        )
        
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Error logging workflow event: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to log workflow event")


@router.get("/{workflow_id}/logs", response_model=List[WorkflowLogResponse])
async def get_workflow_logs(
    workflow_id: str,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """
    Get workflow logs.
    
    Retrieves the log entries for a workflow, ordered by timestamp (newest first).
    """
    try:
        workflow_service = WorkflowService(db)
        logs = workflow_service.get_workflow_logs(workflow_id, limit)
        
        return [
            WorkflowLogResponse(
                id=log.id,
                workflow_id=str(log.workflow_id),
                step_name=log.step_name,
                log_level=log.log_level,
                message=log.message,
                metadata=log.log_metadata,
                timestamp=log.timestamp
            )
            for log in logs
        ]
        
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting workflow logs: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get workflow logs")


@router.websocket("/{workflow_id}/ws")
async def websocket_endpoint(websocket: WebSocket, workflow_id: str):
    """
    WebSocket endpoint for real-time workflow updates.
    
    This endpoint provides real-time updates for workflow progress,
    step status changes, and log messages.
    """
    connection_id = None
    try:
        # Validate workflow_id format
        try:
            uuid.UUID(workflow_id)
        except ValueError:
            await websocket.close(code=4000, reason="Invalid workflow ID format")
            return
        
        # Connect to the workflow
        connection_id = await connection_manager.connect(websocket, workflow_id)
        
        # Send initial workflow status
        try:
            db = next(get_db())
            logger.info(f"Database connection established for workflow {workflow_id}")
            
            workflow_service = WorkflowService(db)
            workflow = workflow_service.get_workflow(workflow_id)
            
            if workflow:
                logger.info(f"Workflow found: {workflow.name} (status: {workflow.status})")
                
                # Get proper progress calculation using WorkflowProgressCalculator
                progress_response = workflow_service.get_workflow_progress(workflow_id)
                
                initial_message = {
                    "workflow_id": str(workflow.id),
                    "name": workflow.name,
                    "status": workflow.status,
                    "total_steps": workflow.total_steps,
                    "completed_steps": workflow.completed_steps,
                    "progress_percentage": progress_response.progress_percentage if progress_response else 0,
                    "current_step": progress_response.current_step if progress_response else "unknown",
                    "message": progress_response.message if progress_response else "Starting workflow",
                    "created_at": workflow.created_at.isoformat(),
                    "started_at": workflow.started_at.isoformat() if workflow.started_at else None,
                    "completed_at": workflow.completed_at.isoformat() if workflow.completed_at else None
                }
                
                await websocket.send_text(json.dumps({
                    "type": "initial_status",
                    "data": initial_message
                }))
                logger.info(f"Initial status sent for workflow {workflow_id}")
            else:
                logger.warning(f"Workflow not found: {workflow_id}")
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "message": "Workflow not found"
                }))
                await websocket.close(code=4004, reason="Workflow not found")
                return
                
        except Exception as e:
            logger.error(f"Error in WebSocket endpoint for workflow {workflow_id}: {str(e)}")
            await websocket.send_text(json.dumps({
                "type": "error",
                "message": f"Internal server error: {str(e)}"
            }))
            await websocket.close(code=4000, reason="Internal server error")
            return
        finally:
            if 'db' in locals():
                db.close()
        
        # Keep connection alive and handle incoming messages
        while True:
            try:
                # Use asyncio.wait_for to make receive_text non-blocking
                # This allows us to handle both incoming messages and timeouts
                try:
                    data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                    message = json.loads(data)
                    
                    # Handle client messages
                    if message.get("type") == "ping":
                        await websocket.send_text(json.dumps({
                            "type": "pong",
                            "timestamp": message.get("timestamp")
                        }))
                    elif message.get("type") == "subscribe":
                        # Client can subscribe to specific events
                        logger.info(f"Client subscribed to workflow {workflow_id}")
                        
                except asyncio.TimeoutError:
                    # Send heartbeat to keep connection alive
                    await websocket.send_text(json.dumps({
                        "type": "heartbeat",
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }))
                    continue
                    
            except WebSocketDisconnect:
                logger.info(f"WebSocket disconnected for workflow {workflow_id}")
                break
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON received from WebSocket for workflow {workflow_id}")
                continue
            except Exception as e:
                logger.error(f"Error handling WebSocket message for workflow {workflow_id}: {e}")
                continue
                
    except Exception as e:
        logger.error(f"WebSocket error for workflow {workflow_id}: {e}")
        try:
            await websocket.close(code=1011, reason="Internal server error")
        except:
            pass
    finally:
        # Clean up connection
        if connection_id:
            connection_manager.disconnect(websocket, connection_id)


async def cancel_nextflow_job(workflow_id: str, workflow_metadata: dict):
    """
    Cancel a running Nextflow job by calling the Nextflow runner API.
    
    Args:
        workflow_id: The workflow ID to cancel
        workflow_metadata: Workflow metadata containing job information
    """
    try:
        # Get Nextflow runner URL
        nextflow_url = os.getenv("NEXTFLOW_RUNNER_URL", "http://nextflow:5055")
        
        # Extract job information from metadata
        patient_id = workflow_metadata.get("patient_id")
        data_id = workflow_metadata.get("data_id")
        
        if not patient_id:
            logger.warning(f"No patient_id found in workflow metadata for {workflow_id}")
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
            logger.info(f"Nextflow job {job_key} not found (may have already completed)")
        else:
            logger.warning(f"Failed to cancel Nextflow job {job_key}: {response.status_code} - {response.text}")
            
    except Exception as e:
        logger.error(f"Error cancelling Nextflow job for workflow {workflow_id}: {e}")
        raise


async def cancel_container_jobs(workflow_id: str, workflow_metadata: dict):
    """
    Cancel running jobs in all container services using a standardized cancel endpoint.
    
    All containers should implement: POST /cancel with workflow_id in the payload.
    This is much simpler than trying multiple endpoint patterns.
    
    Args:
        workflow_id: The workflow ID to cancel
        workflow_metadata: Workflow metadata containing job information
    """
    patient_id = workflow_metadata.get("patient_id")
    if not patient_id:
        logger.warning(f"No patient_id found in workflow metadata for {workflow_id}")
        return
    
    # List of container services with standardized cancel endpoint
    containers = [
        {"name": "gatk-api", "url": "http://gatk-api:5000"},
        {"name": "hlatyping", "url": "http://hlatyping:5000"},
        {"name": "pypgx", "url": "http://pypgx:5000"},
        {"name": "pharmcat", "url": "http://pharmcat:5000"}
    ]
    
    # Cancel jobs in each container using standardized endpoint
    for container in containers:
        try:
            await cancel_container_job(container, patient_id, workflow_id)
        except Exception as e:
            logger.warning(f"Failed to cancel job in {container['name']}: {e}")


async def cancel_container_job(container: dict, patient_id: str, workflow_id: str):
    """
    Cancel a job in a specific container service using standardized endpoint.
    
    All containers should implement: POST /cancel
    Payload: {"workflow_id": "...", "patient_id": "...", "action": "cancel"}
    
    Args:
        container: Container configuration dict with name, url
        patient_id: Patient ID to cancel jobs for
        workflow_id: Workflow ID for logging
    """
    try:
        cancel_url = f"{container['url']}/cancel"
        logger.info(f"Cancelling job in {container['name']} at {cancel_url}")
        
        payload = {
            "workflow_id": workflow_id,
            "patient_id": patient_id,
            "action": "cancel"
        }
        
        response = requests.post(cancel_url, json=payload, timeout=30)
        
        if response.status_code == 200:
            logger.info(f"Successfully cancelled job in {container['name']}")
        elif response.status_code == 404:
            logger.info(f"No running job found in {container['name']} for workflow {workflow_id}")
        else:
            logger.warning(f"Cancel request to {container['name']} returned {response.status_code}: {response.text}")
            
    except requests.exceptions.Timeout as e:
        logger.warning(f"Timeout cancelling job in {container['name']} (30s): {e}")
    except requests.exceptions.ConnectionError as e:
        logger.warning(f"Connection error cancelling job in {container['name']}: {e}")
    except requests.exceptions.RequestException as e:
        logger.warning(f"Request error cancelling job in {container['name']}: {e}")
    except Exception as e:
        logger.error(f"Unexpected error cancelling job in {container['name']}: {e}")


@router.post("/{workflow_id}/cancel", response_model=WorkflowResponse)
async def cancel_workflow(
    workflow_id: str,
    db: Session = Depends(get_db)
):
    """
    Cancel a running workflow.
    
    This endpoint cancels a workflow that is currently running or pending.
    The workflow status will be updated to 'cancelled' and all running steps will be stopped.
    """
    try:
        workflow_service = WorkflowService(db)
        
        # Get the workflow
        workflow = workflow_service.get_workflow(workflow_id)
        if not workflow:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workflow not found"
            )
        
        # Check if workflow can be cancelled
        if workflow.status in ["completed", "failed", "cancelled"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Workflow cannot be cancelled. Current status: {workflow.status}"
            )
        
        # Cancel processes FIRST, then update database status
        # This prevents race conditions where new processes start after status update
        
        # Prepare cancellation metadata
        from app.api.models import WorkflowStatus
        cancellation_metadata = workflow.workflow_metadata.copy() if workflow.workflow_metadata else {}
        cancellation_metadata["cancelled"] = True
        cancellation_metadata["cancelled_at"] = datetime.now(timezone.utc).isoformat()
        
        # STEP 1: Immediately stop all running processes
        logger.info(f"Stopping all processes for workflow {workflow_id}")
        
        # Cancel Nextflow job (orchestrator) first
        try:
            await cancel_nextflow_job(workflow_id, workflow.workflow_metadata)
            logger.info(f"Successfully cancelled Nextflow job for workflow {workflow_id}")
        except Exception as e:
            logger.warning(f"Failed to cancel Nextflow job for workflow {workflow_id}: {e}")
        
        # Cancel individual container jobs
        try:
            await cancel_container_jobs(workflow_id, workflow.workflow_metadata)
            logger.info(f"Successfully cancelled container jobs for workflow {workflow_id}")
        except Exception as e:
            logger.warning(f"Failed to cancel some container jobs for workflow {workflow_id}: {e}")
        
        # Note: File cleanup is handled by individual containers when they detect cancellation
        # The app container will perform delayed cleanup to ensure any in-progress operations complete
        
        # STEP 2: Update database status AFTER processes are stopped
        # This ensures no new processes can start (they check DB status)
        logger.info(f"Updating database status to cancelled for workflow {workflow_id}")
        
        workflow_update = WorkflowUpdate(
            status=WorkflowStatus.CANCELLED,
            metadata=cancellation_metadata
        )
        updated_workflow = workflow_service.update_workflow(workflow_id, workflow_update)
        
        if not updated_workflow:
            # Even if DB update fails, processes are already stopped
            logger.error(f"Failed to update database status for workflow {workflow_id}, but processes are stopped")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update workflow status, but processes have been stopped"
            )
        
        # Log the cancellation
        log_data = WorkflowLogCreate(
            step_name=None,
            log_level="info",
            message="Workflow cancelled by user - stopping all running processes"
        )
        workflow_service.log_workflow_event(workflow_id, log_data)
        
        # Broadcast cancellation via WebSocket to all connected clients
        try:
            from app.services.websocket_manager import connection_manager
            await connection_manager.broadcast_cancellation(workflow_id)
        except Exception as e:
            logger.warning(f"Failed to broadcast cancellation via WebSocket: {e}")
        
        logger.info(f"Workflow {workflow_id} cancelled successfully")
        
        return WorkflowResponse(
            id=str(updated_workflow.id),
            name=updated_workflow.name,
            description=updated_workflow.description,
            status=updated_workflow.status,
            total_steps=updated_workflow.total_steps,
            completed_steps=updated_workflow.completed_steps,
            created_at=updated_workflow.created_at,
            started_at=updated_workflow.started_at,
            completed_at=updated_workflow.completed_at,
            metadata=updated_workflow.workflow_metadata
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cancelling workflow {workflow_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to cancel workflow: {str(e)}"
        )
