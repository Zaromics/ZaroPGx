"""
Job Status Service for centralized job monitoring and status management.

This service replaces the in-memory job_status dictionary with a database-backed
system that provides persistence, audit trails, and better error handling.
"""

from typing import Dict, Optional, List, Any, Union
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, desc
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from app.api.db import Job, JobStage, JobEvent, JobDependency
from app.api.models import JobStatus, JobStage as JobStageEnum, JobStageStatus, JobEventType
from datetime import datetime, timezone, timedelta
import uuid
import logging

logger = logging.getLogger(__name__)


class JobStatusService:
    """
    Service for managing job status and progress tracking.
    
    This service provides a centralized interface for:
    - Creating and updating job status
    - Tracking job stage transitions
    - Logging job events
    - Querying job history and status
    """
    
    def __init__(self, db: Session):
        self.db = db
    
    def create_job(self, patient_id: Optional[Union[str, uuid.UUID]] = None, 
                   file_id: Optional[Union[str, uuid.UUID]] = None, 
                   initial_stage: Union[str, JobStageEnum] = JobStageEnum.UPLOAD,
                   metadata: Optional[Dict[str, Any]] = None) -> Job:
        """
        Create a new job with initial status.
        
        Args:
            patient_id: Optional patient ID for the job (UUID or string)
            file_id: Optional file ID for the job (UUID or string)
            initial_stage: Starting stage for the job
            metadata: Optional metadata for the job
            
        Returns:
            Created Job object
            
        Raises:
            ValueError: If invalid parameters are provided
            RuntimeError: If database operation fails
        """
        try:
            # Input validation
            if metadata is not None and not isinstance(metadata, dict):
                raise ValueError("Metadata must be a dictionary")
            
            # Convert string IDs to UUIDs if needed
            if patient_id and isinstance(patient_id, str):
                try:
                    patient_id = uuid.UUID(patient_id)
                except ValueError:
                    raise ValueError(f"Invalid patient_id format: {patient_id}")
                    
            if file_id and isinstance(file_id, str):
                try:
                    file_id = uuid.UUID(file_id)
                except ValueError:
                    raise ValueError(f"Invalid file_id format: {file_id}")
            
            # Validate that IDs are UUIDs
            if patient_id and not isinstance(patient_id, uuid.UUID):
                raise ValueError(f"patient_id must be a UUID, got {type(patient_id)}")
                
            if file_id and not isinstance(file_id, uuid.UUID):
                raise ValueError(f"file_id must be a UUID, got {type(file_id)}")
            
            # Convert stage to string if it's an enum
            if isinstance(initial_stage, JobStageEnum):
                initial_stage = initial_stage.value
            elif not isinstance(initial_stage, str):
                raise ValueError(f"Invalid stage type: {type(initial_stage)}")
            
            # Validate stage value
            valid_stages = [stage.value for stage in JobStageEnum]
            if initial_stage not in valid_stages:
                raise ValueError(f"Invalid stage: {initial_stage}. Must be one of: {valid_stages}")
            
            # Create the job
            job = Job(
                job_id=uuid.uuid4(),
                patient_id=patient_id,
                file_id=file_id,
                status=JobStatus.PENDING.value,
                stage=initial_stage,  # initial_stage is already converted to string above
                progress=0,
                message="Job created",
                job_metadata=metadata or {},
                started_at=datetime.now(timezone.utc)
            )
            
            self.db.add(job)
            self.db.commit()
            self.db.refresh(job)
            
            # Create initial stage record
            self._create_stage_record(job.job_id, initial_stage, JobStageStatus.STARTED.value)
            
            # Log job creation event
            self._log_event(job.job_id, JobEventType.INFO.value, "Job created successfully", metadata)
            
            logger.info(f"Created job {job.job_id} for stage {initial_stage}")
            return job
            
        except (ValueError, RuntimeError):
            # Re-raise validation errors
            raise
        except SQLAlchemyError as e:
            self.db.rollback()
            logger.error(f"Database error creating job: {str(e)}")
            raise RuntimeError(f"Database operation failed: {str(e)}")
        except Exception as e:
            self.db.rollback()
            logger.error(f"Unexpected error creating job: {str(e)}")
            raise RuntimeError(f"Failed to create job: {str(e)}")
    
    def update_job_progress(self, job_id: Union[str, uuid.UUID], stage: Union[str, JobStageEnum], 
                           progress: int, message: str, metadata: Optional[Dict[str, Any]] = None) -> Job:
        """
        Update job progress and create stage record if stage changed.
        
        Args:
            job_id: Job ID to update
            stage: New stage for the job
            progress: Progress percentage (0-100)
            message: Status message
            metadata: Optional metadata for the update
            
        Returns:
            Updated Job object
            
        Raises:
            ValueError: If job not found or invalid parameters
            RuntimeError: If database operation fails
        """
        try:
            # Input validation
            if not isinstance(progress, int) or not 0 <= progress <= 100:
                raise ValueError("Progress must be an integer between 0 and 100")
            
            if not isinstance(message, str):
                raise ValueError("Message must be a string")
                
            if metadata is not None and not isinstance(metadata, dict):
                raise ValueError("Metadata must be a dictionary")
            
            # Convert job_id to UUID if needed
            if isinstance(job_id, str):
                try:
                    job_id = uuid.UUID(job_id)
                except ValueError:
                    raise ValueError(f"Invalid job_id format: {job_id}")
            
            # Convert stage to string if it's an enum
            if isinstance(stage, JobStageEnum):
                stage = stage.stage
            elif not isinstance(stage, str):
                raise ValueError(f"Invalid stage type: {type(stage)}")
            
            # Validate stage value
            valid_stages = [stage.value for stage in JobStageEnum]
            if stage not in valid_stages:
                raise ValueError(f"Invalid stage: {stage}. Must be one of: {valid_stages}")
            
            # Get the job
            job = self.db.query(Job).filter(Job.job_id == job_id).first()
            if not job:
                raise ValueError(f"Job {job_id} not found")
            
            # FORWARD-ONLY PROGRESS RULE: Progress can never go backward
            # Only allow progress to increase or stay the same
            if progress < job.progress:
                logger.warning(f"Progress update rejected for job {job_id}: attempted to decrease from {job.progress}% to {progress}%. Progress can only increase or remain the same.")
                # Return the job without updating progress
                return job
            
            # Update job status
            job.stage = stage
            job.progress = progress
            job.message = message
            if metadata is not None:
                job.job_metadata = metadata
            job.updated_at = datetime.now(timezone.utc)
            job.status = JobStatus.PROCESSING.value
            
            # Complete previous stage if different
            if job.stages and job.stages[-1].stage != stage:
                self._complete_stage(job.stages[-1].stage_id, JobStageStatus.COMPLETED.value)
                self._create_stage_record(job_id, stage, JobStageStatus.STARTED.value, metadata)
            # Update progress for current stage
            elif job.stages:
                current_stage = job.stages[-1]
                current_stage.progress = progress
                current_stage.message = message
                if metadata is not None:
                    current_stage.stage_metadata = metadata
            
            self.db.commit()
            self.db.refresh(job)
            
            # Log progress update event
            self._log_event(job_id, JobEventType.INFO.value, f"Progress updated: {stage} - {progress}%", metadata)
            
            logger.info(f"Updated job {job_id}: {stage} - {progress}% - {message}")
            return job
            
        except (ValueError, RuntimeError):
            # Re-raise validation errors
            raise
        except SQLAlchemyError as e:
            self.db.rollback()
            logger.error(f"Database error updating job progress: {str(e)}")
            raise RuntimeError(f"Database operation failed: {str(e)}")
        except Exception as e:
            self.db.rollback()
            logger.error(f"Unexpected error updating job progress: {str(e)}")
            raise RuntimeError(f"Failed to update job progress: {str(e)}")
    
    def complete_job(self, job_id: Union[str, uuid.UUID], success: bool = True, 
                     final_message: str = "Job completed successfully",
                     metadata: Optional[Dict[str, Any]] = None) -> Job:
        """
        Mark job as completed.
        
        Args:
            job_id: Job ID to complete
            success: Whether the job completed successfully
            final_message: Final status message
            metadata: Optional metadata for completion
            
        Returns:
            Completed Job object
            
        Raises:
            ValueError: If job not found
            RuntimeError: If database operation fails
        """
        try:
            # Input validation
            if not isinstance(success, bool):
                raise ValueError("Success must be a boolean")
                
            if not isinstance(final_message, str):
                raise ValueError("Final message must be a string")
                
            if metadata is not None and not isinstance(metadata, dict):
                raise ValueError("Metadata must be a dictionary")
            
            # Convert job_id to UUID if needed
            if isinstance(job_id, str):
                try:
                    job_id = uuid.UUID(job_id)
                except ValueError:
                    raise ValueError(f"Invalid job_id format: {job_id}")
            
            # Get the job
            job = self.db.query(Job).filter(Job.job_id == job_id).first()
            if not job:
                raise ValueError(f"Job {job_id} not found")
            
            # Update job status
            job.status = JobStatus.COMPLETED.value if success else JobStatus.FAILED.value
            job.progress = 100
            job.message = final_message
            job.completed_at = datetime.now(timezone.utc)
            job.updated_at = datetime.now(timezone.utc)
            
            if metadata is not None:
                job.job_metadata = metadata
            
            # Complete current stage
            if job.stages:
                self._complete_stage(job.stages[-1].stage_id, JobStageStatus.COMPLETED.value)
            
            self.db.commit()
            self.db.refresh(job)
            
            # Log completion event
            event_type = JobEventType.INFO.value if success else JobEventType.ERROR.value
            self._log_event(job_id, event_type, final_message, metadata)
            
            logger.info(f"Completed job {job_id}: {'SUCCESS' if success else 'FAILED'}")
            return job
            
        except (ValueError, RuntimeError):
            # Re-raise validation errors
            raise
        except SQLAlchemyError as e:
            self.db.rollback()
            logger.error(f"Database error completing job: {str(e)}")
            raise RuntimeError(f"Database operation failed: {str(e)}")
        except Exception as e:
            self.db.rollback()
            logger.error(f"Unexpected error completing job: {str(e)}")
            raise RuntimeError(f"Failed to complete job: {str(e)}")
    
    def fail_job(self, job_id: Union[str, uuid.UUID], error_message: str, 
                  stage: Optional[Union[str, JobStageEnum]] = None,
                  metadata: Optional[Dict[str, Any]] = None) -> Job:
        """
        Mark job as failed.
        
        Args:
            job_id: Job ID to mark as failed
            error_message: Error message describing the failure
            stage: Optional stage where failure occurred
            metadata: Optional metadata for the failure
            
        Returns:
            Failed Job object
            
        Raises:
            ValueError: If job not found
            RuntimeError: If database operation fails
        """
        try:
            # Input validation
            if not isinstance(error_message, str):
                raise ValueError("Error message must be a string")
                
            if metadata is not None and not isinstance(metadata, dict):
                raise ValueError("Metadata must be a dictionary")
            
            # Convert job_id to UUID if needed
            if isinstance(job_id, str):
                try:
                    job_id = uuid.UUID(job_id)
                except ValueError:
                    raise ValueError(f"Invalid job_id format: {job_id}")
            
            # Convert stage to string if it's an enum
            if stage and isinstance(stage, JobStageEnum):
                stage = stage.value
            elif stage and not isinstance(stage, str):
                raise ValueError(f"Invalid stage type: {type(stage)}")
            
            # Validate stage value if provided
            if stage:
                valid_stages = [stage.value for stage in JobStageEnum]
                if stage not in valid_stages:
                    raise ValueError(f"Invalid stage: {stage}. Must be one of: {valid_stages}")
            
            # Get the job
            job = self.db.query(Job).filter(Job.job_id == job_id).first()
            if not job:
                raise ValueError(f"Job {job_id} not found")
            
            # Update job status
            job.status = JobStatus.FAILED.value
            job.error_message = error_message
            if stage:
                job.stage = stage
            job.updated_at = datetime.now(timezone.utc)
            
            if metadata is not None:
                job.job_metadata = metadata
            
            # Complete current stage as failed
            if job.stages:
                self._complete_stage(job.stages[-1].stage_id, JobStageStatus.FAILED.value)
            
            self.db.commit()
            self.db.refresh(job)
            
            # Log failure event
            self._log_event(job_id, JobEventType.ERROR.value, f"Job failed: {error_message}", metadata)
            
            logger.error(f"Failed job {job_id}: {error_message}")
            return job
            
        except (ValueError, RuntimeError):
            # Re-raise validation errors
            raise
        except SQLAlchemyError as e:
            self.db.rollback()
            logger.error(f"Database error failing job: {str(e)}")
            raise RuntimeError(f"Database operation failed: {str(e)}")
        except Exception as e:
            self.db.rollback()
            logger.error(f"Unexpected error failing job: {str(e)}")
            raise RuntimeError(f"Failed to mark job as failed: {str(e)}")
    
    def get_job_status(self, job_id: Union[str, uuid.UUID]) -> Optional[Dict[str, Any]]:
        """
        Get current job status for API response.
        
        Args:
            job_id: Job ID to get status for (UUID or string)
            
        Returns:
            Job status dictionary or None if not found
            
        Raises:
            ValueError: If invalid job_id format
            RuntimeError: If database operation fails
        """
        try:
            # Validate that job_id is a UUID or string
            if not isinstance(job_id, (str, uuid.UUID)):
                raise ValueError(f"job_id must be a UUID or string, got {type(job_id)}")
            
            # Convert job_id to UUID if needed
            if isinstance(job_id, str):
                try:
                    job_id = uuid.UUID(job_id)
                except ValueError:
                    raise ValueError(f"Invalid job_id format: {job_id}")
            
            # Get the job with stages
            job = self.db.query(Job).filter(Job.job_id == job_id).first()
            if not job:
                return None
            
            return {
                "job_id": str(job.job_id),
                "patient_id": str(job.patient_id) if job.patient_id else None,
                "file_id": str(job.file_id) if job.file_id else None,
                "status": job.status,
                "stage": job.stage,
                "progress": job.progress,
                "message": job.message,
                "error_message": job.error_message,
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "updated_at": job.updated_at.isoformat() if job.updated_at else None,
                "completed_at": job.completed_at.isoformat() if job.completed_at else None,
                "job_metadata": job.job_metadata or {}
            }
            
        except (ValueError, RuntimeError):
            # Re-raise validation errors
            raise
        except SQLAlchemyError as e:
            logger.error(f"Database error getting job status: {str(e)}")
            raise RuntimeError(f"Database operation failed: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error getting job status: {str(e)}")
            raise RuntimeError(f"Failed to get job status: {str(e)}")
    
    def get_job_by_id(self, job_id: Union[str, uuid.UUID]) -> Optional[Job]:
        """
        Get job object by ID.
        
        Args:
            job_id: Job ID to retrieve
            
        Returns:
            Job object or None if not found
            
        Raises:
            ValueError: If invalid job_id format
            RuntimeError: If database operation fails
        """
        try:
            # Convert job_id to UUID if needed
            if isinstance(job_id, str):
                try:
                    job_id = uuid.UUID(job_id)
                except ValueError:
                    raise ValueError(f"Invalid job_id format: {job_id}")
            
            return self.db.query(Job).filter(Job.job_id == job_id).first()
            
        except (ValueError, RuntimeError):
            # Re-raise validation errors
            raise
        except SQLAlchemyError as e:
            logger.error(f"Database error getting job by ID: {str(e)}")
            raise RuntimeError(f"Database operation failed: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error getting job by ID: {str(e)}")
            raise RuntimeError(f"Failed to get job by ID: {str(e)}")
    
    def get_jobs_by_status(self, status: Union[str, JobStatus], limit: int = 100) -> List[Job]:
        """
        Get jobs by status.
        
        Args:
            status: Status to filter by
            limit: Maximum number of jobs to return
            
        Returns:
            List of jobs with the specified status
            
        Raises:
            ValueError: If invalid parameters
            RuntimeError: If database operation fails
        """
        try:
            # Input validation
            if not isinstance(limit, int) or limit <= 0:
                raise ValueError("Limit must be a positive integer")
            
            # Convert status to string if it's an enum
            if isinstance(status, JobStatus):
                status = status.value
            elif not isinstance(status, str):
                raise ValueError(f"Invalid status type: {type(status)}")
            
            # Validate status value
            valid_statuses = [status.value for status in JobStatus]
            if status not in valid_statuses:
                raise ValueError(f"Invalid status: {status}. Must be one of: {valid_statuses}")
            
            return self.db.query(Job).filter(
                Job.status == status
            ).order_by(desc(Job.updated_at)).limit(limit).all()
            
        except (ValueError, RuntimeError):
            # Re-raise validation errors
            raise
        except SQLAlchemyError as e:
            logger.error(f"Database error getting jobs by status: {str(e)}")
            raise RuntimeError(f"Database operation failed: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error getting jobs by status: {str(e)}")
            raise RuntimeError(f"Failed to get jobs by status: {str(e)}")
    
    def get_jobs_by_patient(self, patient_id: Union[str, uuid.UUID], limit: int = 100) -> List[Job]:
        """
        Get jobs for a specific patient.
        
        Args:
            patient_id: Patient ID to filter by (UUID or string)
            limit: Maximum number of jobs to return
            
        Returns:
            List of jobs for the patient
            
        Raises:
            ValueError: If invalid parameters
            RuntimeError: If database operation fails
        """
        try:
            # Input validation
            if not isinstance(limit, int) or limit <= 0:
                raise ValueError("Limit must be a positive integer")
            
            # Validate that patient_id is a UUID or string
            if not isinstance(patient_id, (str, uuid.UUID)):
                raise ValueError(f"patient_id must be a UUID or string, got {type(patient_id)}")
            
            # Convert patient_id to UUID if needed
            if isinstance(patient_id, str):
                try:
                    patient_id = uuid.UUID(patient_id)
                except ValueError:
                    raise ValueError(f"Invalid patient_id format: {patient_id}")
            
            return self.db.query(Job).filter(
                Job.patient_id == patient_id
            ).order_by(desc(Job.created_at)).limit(limit).all()
            
        except (ValueError, RuntimeError):
            # Re-raise validation errors
            raise
        except SQLAlchemyError as e:
            logger.error(f"Database error getting jobs by patient: {str(e)}")
            raise RuntimeError(f"Database operation failed: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error getting jobs by patient: {str(e)}")
            raise RuntimeError(f"Failed to get jobs by patient: {str(e)}")
    
    def cleanup_old_jobs(self, days_old: int = 30) -> int:
        """
        Clean up old completed/failed jobs.
        
        Args:
            days_old: Age in days for cleanup threshold
            
        Returns:
            Number of jobs deleted
            
        Raises:
            ValueError: If invalid parameters
            RuntimeError: If database operation fails
        """
        try:
            # Input validation
            if not isinstance(days_old, int) or days_old <= 0:
                raise ValueError("Days old must be a positive integer")
            
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_old)
            
            # Get jobs to delete
            jobs_to_delete = self.db.query(Job).filter(
                and_(
                    Job.status.in_([JobStatus.COMPLETED.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value]),
                    Job.updated_at < cutoff_date
                )
            ).all()
            
            deleted_count = len(jobs_to_delete)
            
            # Delete the jobs (cascade will handle related records)
            for job in jobs_to_delete:
                self.db.delete(job)
            
            self.db.commit()
            
            logger.info(f"Cleaned up {deleted_count} old jobs")
            return deleted_count
            
        except (ValueError, RuntimeError):
            # Re-raise validation errors
            raise
        except SQLAlchemyError as e:
            self.db.rollback()
            logger.error(f"Database error cleaning up old jobs: {str(e)}")
            raise RuntimeError(f"Database operation failed: {str(e)}")
        except Exception as e:
            self.db.rollback()
            logger.error(f"Unexpected error cleaning up old jobs: {str(e)}")
            raise RuntimeError(f"Failed to cleanup old jobs: {str(e)}")
    
    def _create_stage_record(self, job_id: uuid.UUID, stage: str, status: str, 
                             metadata: Optional[Dict[str, Any]] = None) -> JobStage:
        """Create a new stage record."""
        try:
            stage_record = JobStage(
                job_id=job_id,
                stage=stage,
                status=status,
                progress=0,
                message="Stage started",
                stage_metadata=metadata or {}
            )
            self.db.add(stage_record)
            self.db.commit()
            self.db.refresh(stage_record)
            return stage_record
        except SQLAlchemyError as e:
            self.db.rollback()
            logger.error(f"Failed to create stage record: {str(e)}")
            raise RuntimeError(f"Failed to create stage record: {str(e)}")
    
    def _complete_stage(self, stage_id: int, status: str) -> None:
        """Mark a stage as completed."""
        try:
            stage = self.db.query(JobStage).filter(JobStage.stage_id == stage_id).first()
            if stage:
                stage.status = status
                stage.completed_at = datetime.now(timezone.utc)
                if stage.started_at:
                    stage.duration_ms = int((stage.completed_at - stage.started_at).total_seconds() * 1000)
                self.db.commit()
        except SQLAlchemyError as e:
            self.db.rollback()
            logger.error(f"Failed to complete stage: {str(e)}")
            raise RuntimeError(f"Failed to complete stage: {str(e)}")
    
    def _log_event(self, job_id: uuid.UUID, event_type: str, message: str, 
                   metadata: Optional[Dict[str, Any]] = None) -> None:
        """Log a job event."""
        try:
            event = JobEvent(
                job_id=job_id,
                event_type=event_type,
                message=message,
                event_metadata=metadata or {}
            )
            self.db.add(event)
            self.db.commit()
        except SQLAlchemyError as e:
            logger.error(f"Failed to log job event: {str(e)}")
            # Don't fail the main operation if event logging fails
            self.db.rollback()
