"""
Workflow Service for centralized workflow management and orchestration.

This service provides a centralized interface for:
- Creating and managing workflows
- Tracking workflow step execution
- Managing workflow progress and status
- Logging workflow events
- Integration with existing JobStatusService
"""

from typing import Dict, List, Optional, Any, Union
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, desc
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from app.api.db import Workflow, WorkflowStep, WorkflowLog
from app.api.models import (
    WorkflowStatus, StepStatus, LogLevel,
    WorkflowCreate, WorkflowUpdate, WorkflowResponse,
    WorkflowStepCreate, WorkflowStepUpdate, WorkflowStepResponse,
    WorkflowLogCreate, WorkflowLogResponse, WorkflowProgressResponse
)
from app.services.workflow_progress_calculator import WorkflowProgressCalculator
from app.services.websocket_manager import connection_manager
from app.services.cleanup_service import cleanup_service
from app.services.pharmcat_data_service import PharmCATDataService
from datetime import datetime, timezone, timedelta
import uuid
import logging
import asyncio

logger = logging.getLogger(__name__)


class WorkflowService:
    """
    Service for managing workflows and their execution.
    
    This service provides comprehensive workflow management including:
    - Workflow lifecycle management
    - Step orchestration and tracking
    - Progress calculation and monitoring
    - Error handling and retry logic
    - Integration with existing job monitoring
    """
    
    def __init__(self, db: Session):
        self.db = db
    
    async def _broadcast_workflow_update(self, workflow_id: str, message: Dict[str, Any]):
        """Broadcast workflow update to WebSocket connections."""
        try:
            logger.info(f"Broadcasting workflow update for {workflow_id}: {message}")
            await connection_manager.send_workflow_update(str(workflow_id), message)
            logger.info(f"Successfully broadcasted workflow update for {workflow_id}")
        except Exception as e:
            logger.error(f"Failed to broadcast workflow update: {e}")
    
    async def _broadcast_step_update(self, workflow_id: str, step_name: str, message: Dict[str, Any]):
        """Broadcast step update to WebSocket connections."""
        try:
            logger.info(f"Broadcasting step update for {workflow_id}/{step_name}: {message}")
            await connection_manager.send_step_update(str(workflow_id), step_name, message)
            logger.info(f"Successfully broadcasted step update for {workflow_id}/{step_name}")
        except Exception as e:
            logger.error(f"Failed to broadcast step update: {e}")
    
    async def _broadcast_log_update(self, workflow_id: str, log_message: Dict[str, Any]):
        """Broadcast log update to WebSocket connections."""
        try:
            logger.info(f"Broadcasting log update for {workflow_id}: {log_message}")
            await connection_manager.send_log_update(str(workflow_id), log_message)
            logger.info(f"Successfully broadcasted log update for {workflow_id}")
        except Exception as e:
            logger.error(f"Failed to broadcast log update: {e}")
    
    def create_workflow(self, workflow_data: WorkflowCreate) -> Workflow:
        """
        Create a new workflow.
        
        Args:
            workflow_data: Workflow creation data
            
        Returns:
            Created Workflow object
            
        Raises:
            ValueError: If invalid parameters are provided
            RuntimeError: If database operation fails
        """
        try:
            # Input validation
            if not workflow_data.name or not workflow_data.name.strip():
                raise ValueError("Workflow name is required")
            
            # Create the workflow
            workflow = Workflow(
                name=workflow_data.name.strip(),
                description=workflow_data.description,
                status=WorkflowStatus.PENDING,
                total_steps=workflow_data.total_steps,
                completed_steps=0,
                workflow_metadata=workflow_data.metadata,
                created_by=workflow_data.created_by
            )

            self.db.add(workflow)
            self.db.commit()
            self.db.refresh(workflow)
            
            # Log workflow creation
            self._log_workflow_event(
                workflow.id,
                LogLevel.INFO,
                f"Workflow '{workflow.name}' created successfully",
                {"workflow_id": str(workflow.id)}
            )
            
            logger.info(f"Created workflow {workflow.id}: {workflow.name}")
            return workflow
            
        except (ValueError, RuntimeError):
            raise
        except SQLAlchemyError as e:
            self.db.rollback()
            logger.error(f"Database error creating workflow: {str(e)}")
            raise RuntimeError(f"Database operation failed: {str(e)}")
        except Exception as e:
            self.db.rollback()
            logger.error(f"Unexpected error creating workflow: {str(e)}")
            raise RuntimeError(f"Failed to create workflow: {str(e)}")
    
    def get_workflow(self, workflow_id: Union[str, uuid.UUID]) -> Optional[Workflow]:
        """
        Get workflow by ID.
        
        Args:
            workflow_id: Workflow ID to retrieve
            
        Returns:
            Workflow object or None if not found
            
        Raises:
            ValueError: If invalid workflow_id format
            RuntimeError: If database operation fails
        """
        try:
            # Convert workflow_id to UUID if needed
            if isinstance(workflow_id, str):
                try:
                    workflow_id = uuid.UUID(workflow_id)
                except ValueError:
                    raise ValueError(f"Invalid workflow_id format: {workflow_id}")
            
            return self.db.query(Workflow).filter(Workflow.id == workflow_id).first()
            
        except (ValueError, RuntimeError):
            raise
        except SQLAlchemyError as e:
            logger.error(f"Database error getting workflow: {str(e)}")
            raise RuntimeError(f"Database operation failed: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error getting workflow: {str(e)}")
            raise RuntimeError(f"Failed to get workflow: {str(e)}")
    
    def update_workflow(self, workflow_id: Union[str, uuid.UUID], 
                       update_data: WorkflowUpdate) -> Optional[Workflow]:
        """
        Update workflow.
        
        Args:
            workflow_id: Workflow ID to update
            update_data: Update data
            
        Returns:
            Updated Workflow object or None if not found
            
        Raises:
            ValueError: If invalid parameters
            RuntimeError: If database operation fails
        """
        try:
            # Convert workflow_id to UUID if needed
            if isinstance(workflow_id, str):
                try:
                    workflow_id = uuid.UUID(workflow_id)
                except ValueError:
                    raise ValueError(f"Invalid workflow_id format: {workflow_id}")
            
            # Get the workflow
            workflow = self.db.query(Workflow).filter(Workflow.id == workflow_id).first()
            if not workflow:
                return None
            
            # Update fields
            if update_data.name is not None:
                workflow.name = update_data.name.strip()
            if update_data.description is not None:
                workflow.description = update_data.description
            if update_data.status is not None:
                workflow.status = update_data.status
            if update_data.total_steps is not None:
                workflow.total_steps = update_data.total_steps
            if update_data.completed_steps is not None:
                workflow.completed_steps = update_data.completed_steps
            if update_data.metadata is not None:
                workflow.workflow_metadata = update_data.metadata
            
            # Update timing fields based on status
            if update_data.status == WorkflowStatus.RUNNING and not workflow.started_at:
                workflow.started_at = datetime.now(timezone.utc)
            elif update_data.status in [WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED]:
                workflow.completed_at = datetime.now(timezone.utc)
            
            self.db.commit()
            self.db.refresh(workflow)
            
            # Log workflow update
            self._log_workflow_event(
                workflow.id,
                LogLevel.INFO,
                f"Workflow updated: {update_data.status if update_data.status else 'fields updated'}",
                {"updated_fields": [k for k, v in update_data.dict(exclude_unset=True).items() if v is not None]}
            )
            
            # Get proper progress calculation using WorkflowProgressCalculator
            progress_response = self.get_workflow_progress(workflow.id)
            
            # Broadcast workflow update via WebSocket
            try:
                # Schedule the broadcast task for execution
                asyncio.create_task(self._broadcast_workflow_update(
                    str(workflow.id),
                    {
                        "workflow_id": str(workflow.id),
                        "status": workflow.status,
                        "total_steps": workflow.total_steps,
                        "completed_steps": workflow.completed_steps,
                        "progress_percentage": progress_response.progress_percentage if progress_response else 0,
                        "current_step": progress_response.current_step if progress_response else "unknown",
                        "message": progress_response.message if progress_response else "Processing...",
                        "started_at": workflow.started_at.isoformat() if workflow.started_at else None,
                        "completed_at": workflow.completed_at.isoformat() if workflow.completed_at else None
                    }
                ))
            except Exception as e:
                logger.error(f"Failed to schedule workflow update broadcast: {e}")
            
            logger.info(f"Updated workflow {workflow.id}")
            return workflow
            
        except (ValueError, RuntimeError):
            raise
        except SQLAlchemyError as e:
            self.db.rollback()
            logger.error(f"Database error updating workflow: {str(e)}")
            raise RuntimeError(f"Database operation failed: {str(e)}")
        except Exception as e:
            self.db.rollback()
            logger.error(f"Unexpected error updating workflow: {str(e)}")
            raise RuntimeError(f"Failed to update workflow: {str(e)}")
    
    def add_workflow_step(self, workflow_id: Union[str, uuid.UUID], 
                         step_data: WorkflowStepCreate) -> Optional[WorkflowStep]:
        """
        Add a step to a workflow.
        
        Args:
            workflow_id: Workflow ID
            step_data: Step creation data
            
        Returns:
            Created WorkflowStep object or None if workflow not found
            
        Raises:
            ValueError: If invalid parameters
            RuntimeError: If database operation fails
        """
        try:
            # Convert workflow_id to UUID if needed
            if isinstance(workflow_id, str):
                try:
                    workflow_id = uuid.UUID(workflow_id)
                except ValueError:
                    raise ValueError(f"Invalid workflow_id format: {workflow_id}")
            
            # Get the workflow
            workflow = self.db.query(Workflow).filter(Workflow.id == workflow_id).first()
            if not workflow:
                return None
            
            # Create the step
            step = WorkflowStep(
                workflow_id=workflow_id,
                step_name=step_data.step_name,
                step_order=step_data.step_order,
                container_name=step_data.container_name,
                output_data=step_data.output_data
            )
            
            self.db.add(step)
            self.db.commit()
            self.db.refresh(step)
            
            # Log step creation
            self._log_workflow_event(
                workflow_id,
                LogLevel.INFO,
                f"Step '{step_data.step_name}' added to workflow",
                {"step_id": str(step.id), "step_order": step_data.step_order}
            )
            
            logger.info(f"Added step {step.id} to workflow {workflow_id}")
            return step
            
        except (ValueError, RuntimeError):
            raise
        except SQLAlchemyError as e:
            self.db.rollback()
            logger.error(f"Database error adding workflow step: {str(e)}")
            raise RuntimeError(f"Database operation failed: {str(e)}")
        except Exception as e:
            self.db.rollback()
            logger.error(f"Unexpected error adding workflow step: {str(e)}")
            raise RuntimeError(f"Failed to add workflow step: {str(e)}")
    
    def update_workflow_step(self, workflow_id: Union[str, uuid.UUID], 
                            step_name: str, update_data: WorkflowStepUpdate) -> Optional[WorkflowStep]:
        """
        Update a workflow step.
        
        Args:
            workflow_id: Workflow ID
            step_name: Step name to update
            update_data: Update data
            
        Returns:
            Updated WorkflowStep object or None if not found
            
        Raises:
            ValueError: If invalid parameters
            RuntimeError: If database operation fails
        """
        try:
            # Convert workflow_id to UUID if needed
            if isinstance(workflow_id, str):
                try:
                    workflow_id = uuid.UUID(workflow_id)
                except ValueError:
                    raise ValueError(f"Invalid workflow_id format: {workflow_id}")
            
            # Get the step
            step = self.db.query(WorkflowStep).filter(
                and_(
                    WorkflowStep.workflow_id == workflow_id,
                    WorkflowStep.step_name == step_name
                )
            ).first()
            
            if not step:
                return None
            
            # Update fields
            if update_data.status is not None:
                step.status = update_data.status
            if update_data.container_name is not None:
                step.container_name = update_data.container_name
            if update_data.output_data is not None:
                step.output_data = update_data.output_data
            if update_data.error_details is not None:
                step.error_details = update_data.error_details
            if update_data.retry_count is not None:
                step.retry_count = update_data.retry_count
            
            # Log message if provided
            if update_data.message is not None:
                self._log_workflow_event(
                    workflow_id,
                    "info",
                    update_data.message,
                    {"step_status": step.status, "step_name": step_name}
                )
            
            # Update timing fields based on status
            if update_data.status == StepStatus.RUNNING and not step.started_at:
                step.started_at = datetime.now(timezone.utc)
            elif update_data.status in [StepStatus.COMPLETED, StepStatus.FAILED, StepStatus.SKIPPED]:
                step.completed_at = datetime.now(timezone.utc)
                if step.started_at:
                    step.duration_seconds = int((step.completed_at - step.started_at).total_seconds())
            
            self.db.commit()
            self.db.refresh(step)
            
            # Log step update
            self._log_workflow_event(
                workflow_id,
                LogLevel.INFO,
                f"Step '{step_name}' updated: {update_data.status if update_data.status else 'fields updated'}",
                {"step_id": str(step.id), "step_status": update_data.status if update_data.status else step.status}
            )
            
            # Update workflow progress if step completed
            if update_data.status == StepStatus.COMPLETED:
                self._update_workflow_progress(workflow_id)
            
            # Broadcast step update via WebSocket
            try:
                # Schedule the broadcast task for execution
                asyncio.create_task(self._broadcast_step_update(
                    str(workflow_id),
                    step_name,
                    {
                        "step_name": step_name,
                        "status": step.status,
                        "container_name": step.container_name,
                        "started_at": step.started_at.isoformat() if step.started_at else None,
                        "completed_at": step.completed_at.isoformat() if step.completed_at else None,
                        "duration_seconds": step.duration_seconds,
                        "output_data": step.output_data,
                        "error_details": step.error_details,
                        "retry_count": step.retry_count
                    }
                ))
            except Exception as e:
                logger.error(f"Failed to schedule step update broadcast: {e}")
            
            # Also broadcast workflow progress update for any step status change
            try:
                # Get updated progress information
                progress_response = self.get_workflow_progress(workflow_id)
                if progress_response:
                    # Get workflow object for additional data
                    workflow = self.db.query(Workflow).filter(Workflow.id == workflow_id).first()
                    if workflow:
                        # Schedule workflow progress broadcast
                        asyncio.create_task(self._broadcast_workflow_update(
                            str(workflow_id),
                            {
                                "workflow_id": str(workflow_id),
                                "status": workflow.status,
                                "total_steps": workflow.total_steps,
                                "completed_steps": workflow.completed_steps,
                                "progress_percentage": progress_response.progress_percentage,
                                "current_step": progress_response.current_step,
                                "message": progress_response.message,
                                "started_at": workflow.started_at.isoformat() if workflow.started_at else None,
                                "completed_at": workflow.completed_at.isoformat() if workflow.completed_at else None
                            }
                        ))
            except Exception as e:
                logger.error(f"Failed to schedule workflow progress broadcast: {e}")
            
            logger.info(f"Updated step {step.id} in workflow {workflow_id}")
            return step
            
        except (ValueError, RuntimeError):
            raise
        except SQLAlchemyError as e:
            self.db.rollback()
            logger.error(f"Database error updating workflow step: {str(e)}")
            raise RuntimeError(f"Database operation failed: {str(e)}")
        except Exception as e:
            self.db.rollback()
            logger.error(f"Unexpected error updating workflow step: {str(e)}")
            raise RuntimeError(f"Failed to update workflow step: {str(e)}")
    
    def get_workflow_progress(self, workflow_id: Union[str, uuid.UUID]) -> Optional[WorkflowProgressResponse]:
        """
        Get workflow progress information.
        
        Args:
            workflow_id: Workflow ID
            
        Returns:
            WorkflowProgressResponse or None if workflow not found
            
        Raises:
            ValueError: If invalid workflow_id format
            RuntimeError: If database operation fails
        """
        try:
            # Convert workflow_id to UUID if needed
            if isinstance(workflow_id, str):
                try:
                    workflow_id = uuid.UUID(workflow_id)
                except ValueError:
                    raise ValueError(f"Invalid workflow_id format: {workflow_id}")
            
            # Get the workflow
            workflow = self.db.query(Workflow).filter(Workflow.id == workflow_id).first()
            if not workflow:
                return None
            
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
                for step in workflow.steps
            ]
            
            # Get workflow metadata for configuration
            workflow_config = workflow.workflow_metadata.get("workflow", {}) if workflow.workflow_metadata else {}
            
            # Calculate progress using centralized calculator
            progress_calculator = WorkflowProgressCalculator()
            progress_info = progress_calculator.calculate_progress_from_steps(steps_dict, workflow_config, str(workflow.id))
            
            # Calculate estimated completion
            estimated_completion = None
            if workflow.started_at and workflow.status == WorkflowStatus.RUNNING:
                # Simple estimation based on current progress
                if progress_info.progress_percentage > 0:
                    elapsed = datetime.now(timezone.utc) - workflow.started_at
                    estimated_total = elapsed / (progress_info.progress_percentage / 100)
                    estimated_completion = workflow.started_at + estimated_total
            
            return WorkflowProgressResponse(
                workflow_id=str(workflow.id),
                status=WorkflowStatus(workflow.status),
                total_steps=workflow.total_steps or 0,
                completed_steps=workflow.completed_steps or 0,
                progress_percentage=round(progress_info.progress_percentage, 2),
                current_step=progress_info.current_step_name or progress_info.stage,
                estimated_completion=estimated_completion,
                message=progress_info.message
            )
            
        except (ValueError, RuntimeError):
            raise
        except SQLAlchemyError as e:
            logger.error(f"Database error getting workflow progress: {str(e)}")
            raise RuntimeError(f"Database operation failed: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error getting workflow progress: {str(e)}")
            raise RuntimeError(f"Failed to get workflow progress: {str(e)}")
    
    def log_workflow_event(self, workflow_id: Union[str, uuid.UUID], 
                          log_data: WorkflowLogCreate) -> WorkflowLog:
        """
        Log a workflow event.
        
        Args:
            workflow_id: Workflow ID
            log_data: Log data
            
        Returns:
            Created WorkflowLog object
            
        Raises:
            ValueError: If invalid parameters
            RuntimeError: If database operation fails
        """
        try:
            # Convert workflow_id to UUID if needed
            if isinstance(workflow_id, str):
                try:
                    workflow_id = uuid.UUID(workflow_id)
                except ValueError:
                    raise ValueError(f"Invalid workflow_id format: {workflow_id}")
            
            # Create the log entry
            log_entry = WorkflowLog(
                workflow_id=workflow_id,
                step_name=log_data.step_name,
                log_level=log_data.log_level,
                message=log_data.message,
                log_metadata=log_data.metadata
            )
            
            self.db.add(log_entry)
            self.db.commit()
            self.db.refresh(log_entry)
            
            # Broadcast log update via WebSocket
            try:
                # Schedule the broadcast task for execution
                asyncio.create_task(self._broadcast_log_update(
                    str(workflow_id),
                    {
                        "step_name": log_entry.step_name,
                        "log_level": log_entry.log_level,
                        "message": log_entry.message,
                        "metadata": log_entry.log_metadata,
                        "timestamp": log_entry.timestamp.isoformat()
                    }
                ))
            except Exception as e:
                logger.error(f"Failed to schedule log update broadcast: {e}")
            
            logger.info(f"Logged event for workflow {workflow_id}: {log_data.log_level} - {log_data.message}")
            return log_entry
            
        except (ValueError, RuntimeError):
            raise
        except SQLAlchemyError as e:
            self.db.rollback()
            logger.error(f"Database error logging workflow event: {str(e)}")
            raise RuntimeError(f"Database operation failed: {str(e)}")
        except Exception as e:
            self.db.rollback()
            logger.error(f"Unexpected error logging workflow event: {str(e)}")
            raise RuntimeError(f"Failed to log workflow event: {str(e)}")
    
    def get_workflow_logs(self, workflow_id: Union[str, uuid.UUID], 
                         limit: int = 100) -> List[WorkflowLog]:
        """
        Get workflow logs.
        
        Args:
            workflow_id: Workflow ID
            limit: Maximum number of logs to return
            
        Returns:
            List of WorkflowLog objects
            
        Raises:
            ValueError: If invalid parameters
            RuntimeError: If database operation fails
        """
        try:
            # Convert workflow_id to UUID if needed
            if isinstance(workflow_id, str):
                try:
                    workflow_id = uuid.UUID(workflow_id)
                except ValueError:
                    raise ValueError(f"Invalid workflow_id format: {workflow_id}")
            
            return self.db.query(WorkflowLog).filter(
                WorkflowLog.workflow_id == workflow_id
            ).order_by(desc(WorkflowLog.timestamp)).limit(limit).all()
            
        except (ValueError, RuntimeError):
            raise
        except SQLAlchemyError as e:
            logger.error(f"Database error getting workflow logs: {str(e)}")
            raise RuntimeError(f"Database operation failed: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error getting workflow logs: {str(e)}")
            raise RuntimeError(f"Failed to get workflow logs: {str(e)}")
    
    def _update_workflow_progress(self, workflow_id: uuid.UUID) -> None:
        """Update workflow progress based on completed steps."""
        try:
            workflow = self.db.query(Workflow).filter(Workflow.id == workflow_id).first()
            if not workflow:
                return
            
            # Count completed steps
            completed_steps = self.db.query(WorkflowStep).filter(
                and_(
                    WorkflowStep.workflow_id == workflow_id,
                    WorkflowStep.status == StepStatus.COMPLETED
                )
            ).count()
            
            # Update workflow
            workflow.completed_steps = completed_steps
            
            # Get progress information to check if workflow should be completed
            progress_response = self.get_workflow_progress(workflow_id)
            
            # Check if workflow should be completed based on progress percentage
            if progress_response and progress_response.progress_percentage >= 100:
                workflow.status = WorkflowStatus.COMPLETED
                workflow.completed_at = datetime.now(timezone.utc)
                
                # Log workflow completion
                self._log_workflow_event(
                    workflow_id,
                    LogLevel.INFO,
                    "Workflow completed successfully with reports generated",
                    {"completed_steps": completed_steps, "total_steps": workflow.total_steps}
                )
                
                # Perform centralized cleanup of temporary files
                try:
                    # Extract patient_id from workflow metadata if available
                    patient_id = None
                    if hasattr(workflow, 'workflow_metadata') and workflow.workflow_metadata:
                        patient_id = workflow.workflow_metadata.get('patient_id')
                    
                    # Clean up workflow-specific temporary files
                    cleanup_result = cleanup_service.cleanup_workflow_files(
                        workflow_id=str(workflow_id),
                        patient_id=patient_id
                    )
                    
                    # Log cleanup results
                    if cleanup_result.get('success', False):
                        logger.info(f"Workflow cleanup completed for {workflow_id}: "
                                   f"{cleanup_result['total_items_cleaned']} items, "
                                   f"{cleanup_result['total_size_cleaned']} bytes cleaned")
                    else:
                        logger.warning(f"Workflow cleanup had issues for {workflow_id}: "
                                     f"{len(cleanup_result.get('failed_paths', []))} failed paths")
                        
                except Exception as e:
                    logger.error(f"Failed to cleanup temporary files for workflow {workflow_id}: {e}")
                
                # Broadcast final workflow completion update
                try:
                    asyncio.create_task(self._broadcast_workflow_update(
                        str(workflow_id),
                        {
                            "workflow_id": str(workflow_id),
                            "status": workflow.status,
                            "total_steps": workflow.total_steps,
                            "completed_steps": workflow.completed_steps,
                            "progress_percentage": 100,
                            "current_step": "completed",
                            "message": "Processing complete! - All processing finished",
                            "started_at": workflow.started_at.isoformat() if workflow.started_at else None,
                            "completed_at": workflow.completed_at.isoformat() if workflow.completed_at else None
                        }
                    ))
                except Exception as e:
                    logger.error(f"Failed to broadcast workflow completion: {e}")
            
            self.db.commit()
            
        except SQLAlchemyError as e:
            self.db.rollback()
            logger.error(f"Failed to update workflow progress: {str(e)}")
    
    def _log_workflow_event(self, workflow_id: uuid.UUID, level: str, 
                           message: str, metadata: Dict[str, Any] = None) -> None:
        """Log a workflow event (internal method)."""
        try:
            log_entry = WorkflowLog(
                workflow_id=workflow_id,
                log_level=level,
                message=message,
                metadata=metadata or {}
            )
            self.db.add(log_entry)
            self.db.commit()
        except SQLAlchemyError as e:
            logger.error(f"Failed to log workflow event: {str(e)}")
            # Don't fail the main operation if logging fails
    
    
    def get_workflow_steps(self, workflow_id: Union[str, uuid.UUID]) -> List[WorkflowStepResponse]:
        """
        Get all steps for a workflow.
        
        Args:
            workflow_id: ID of the workflow
            
        Returns:
            List of workflow step responses
        """
        try:
            workflow_id = uuid.UUID(str(workflow_id))
            
            steps = self.db.query(WorkflowStep).filter(
                WorkflowStep.workflow_id == workflow_id
            ).order_by(WorkflowStep.step_order).all()
            
            return [WorkflowStepResponse.model_validate(step) for step in steps]
            
        except Exception as e:
            logger.error(f"Failed to get workflow steps: {str(e)}")
            return []
    
    def link_pharmcat_run(self, workflow_id: str, pharmcat_run_id: str) -> bool:
        """
        Link a PharmCAT run to a workflow.
        
        Args:
            workflow_id: Workflow ID
            pharmcat_run_id: PharmCAT run ID
            
        Returns:
            True if successful, False otherwise
        """
        try:
            workflow_id = uuid.UUID(str(workflow_id))
            workflow = self.db.query(Workflow).filter(Workflow.id == workflow_id).first()
            
            if not workflow:
                logger.error(f"Workflow {workflow_id} not found")
                return False
            
            # Update workflow metadata with PharmCAT run ID
            metadata = workflow.workflow_metadata or {}
            metadata['pharmcat_run_id'] = pharmcat_run_id
            metadata['pharmcat_linked_at'] = datetime.now(timezone.utc).isoformat()
            
            workflow.workflow_metadata = metadata
            self.db.commit()
            
            logger.info(f"Successfully linked PharmCAT run {pharmcat_run_id} to workflow {workflow_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error linking PharmCAT run to workflow: {e}")
            self.db.rollback()
            return False
    
    def get_pharmcat_run_id(self, workflow_id: str) -> Optional[str]:
        """
        Get the PharmCAT run ID for a workflow.
        
        Args:
            workflow_id: Workflow ID
            
        Returns:
            PharmCAT run ID if found, None otherwise
        """
        try:
            workflow_id = uuid.UUID(str(workflow_id))
            workflow = self.db.query(Workflow).filter(Workflow.id == workflow_id).first()
            
            if not workflow:
                return None
            
            metadata = workflow.workflow_metadata or {}
            return metadata.get('pharmcat_run_id')
            
        except Exception as e:
            logger.error(f"Error getting PharmCAT run ID for workflow {workflow_id}: {e}")
            return None
    
    def get_pharmcat_data(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        """
        Get PharmCAT data for a workflow.
        
        Args:
            workflow_id: Workflow ID
            
        Returns:
            Dict containing normalized PharmCAT data, or None if not found
        """
        try:
            pharmcat_service = PharmCATDataService(self.db)
            return pharmcat_service.get_pharmcat_data_for_workflow(workflow_id)
        except Exception as e:
            logger.error(f"Error getting PharmCAT data for workflow {workflow_id}: {e}")
            return None
    