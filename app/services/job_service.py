"""
Job Service for centralized job management and orchestration.

This service provides a centralized interface for:
- Creating and managing jobs
- Tracking job step execution
- Managing job progress and status
- Logging job events
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Union

from sqlalchemy import and_, desc, or_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.api.db import Job, JobLog, JobStep
from app.api.models import (
    JobCreate,
    JobLogCreate,
    JobLogResponse,
    JobProgressResponse,
    JobResponse,
    JobStatus,
    JobStepCreate,
    JobStepResponse,
    JobStepUpdate,
    JobUpdate,
    LogLevel,
    StepStatus,
    WorkflowOptions,
)
from app.services.cleanup_service import cleanup_service
from app.services.pharmcat_data_service import PharmCATDataService
from app.services.websocket_manager import connection_manager
from app.services.workflow_progress_calculator import WorkflowProgressCalculator
from app.services.workflow_registry import (
    build_snapshot,
    get_recipe,
    resolve_steps,
)

logger = logging.getLogger(__name__)


def _json_safe(value: Any) -> Any:
    """Recursively coerce a value into something the JSON metadata column accepts.

    ``jobs.job_metadata`` is a ``Column(JSON)`` serialized with ``json.dumps``, which
    raises ``TypeError: Object of type datetime is not JSON serializable`` on a
    datetime/date. Report payloads stored under ``metadata["reports"]`` (PharmCAT/PyPGx
    processed data) can carry those, so normalize datetimes to ISO strings and recurse
    through dicts/lists before the value reaches the column.
    """
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "isoformat") and not isinstance(value, (str, bytes)):
        # date / time objects
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


# Strong refs for fire-and-forget broadcast tasks (loop only keeps weak refs).
_background_tasks: set[asyncio.Task] = set()
# Main app loop — used when sync JobService methods run off the event-loop thread.
_main_loop: Optional[asyncio.AbstractEventLoop] = None


def remember_event_loop(loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
    """Record the running asyncio loop for thread-safe broadcast scheduling."""
    global _main_loop
    if loop is None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
    _main_loop = loop


def _on_background_task_done(task: asyncio.Task) -> None:
    _background_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "Background workflow broadcast task failed: %s",
            exc,
            exc_info=exc,
        )


def schedule_coroutine(coro) -> None:
    """
    Schedule a coroutine from sync or async context.

    Holds a strong Task reference so the event loop cannot GC mid-flight, and
    logs exceptions from done callbacks. When called from a worker thread with
    no running loop, uses run_coroutine_threadsafe against the remembered main loop.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        remember_event_loop(loop)
        task = loop.create_task(coro)
        _background_tasks.add(task)
        task.add_done_callback(_on_background_task_done)
        return

    if _main_loop is not None and _main_loop.is_running():
        future = asyncio.run_coroutine_threadsafe(coro, _main_loop)

        def _on_future_done(fut: asyncio.Future) -> None:
            try:
                fut.result()
            except Exception as e:
                logger.error(
                    "Background workflow broadcast failed (thread-safe): %s",
                    e,
                    exc_info=True,
                )

        future.add_done_callback(_on_future_done)
        return

    logger.error(
        "Cannot schedule workflow broadcast: no running event loop "
        "(call remember_event_loop at app startup)"
    )
    coro.close()


class JobService:
    """
    Service for managing workflows and their execution.

    This service provides comprehensive workflow management including:
    - Job lifecycle management
    - Step orchestration and tracking
    - Progress calculation and monitoring
    - Error handling and retry logic
    - Integration with existing job monitoring
    """

    def __init__(self, db: Session):
        self.db = db

    async def _broadcast_job_update(self, job_id: str, message: Dict[str, Any]):
        """Broadcast workflow update to WebSocket connections."""
        try:
            logger.info(f"Broadcasting workflow update for {job_id}: {message}")
            await connection_manager.send_job_update(str(job_id), message)
            logger.info(f"Successfully broadcasted workflow update for {job_id}")
        except Exception as e:
            logger.error(f"Failed to broadcast workflow update: {e}")

    async def _broadcast_step_update(
        self, job_id: str, step_name: str, message: Dict[str, Any]
    ):
        """Broadcast step update to WebSocket connections."""
        try:
            logger.info(f"Broadcasting step update for {job_id}/{step_name}: {message}")
            await connection_manager.send_step_update(str(job_id), step_name, message)
            logger.info(
                f"Successfully broadcasted step update for {job_id}/{step_name}"
            )
        except Exception as e:
            logger.error(f"Failed to broadcast step update: {e}")

    async def _broadcast_log_update(self, job_id: str, log_message: Dict[str, Any]):
        """Broadcast log update to WebSocket connections."""
        try:
            logger.info(f"Broadcasting log update for {job_id}: {log_message}")
            await connection_manager.send_log_update(str(job_id), log_message)
            logger.info(f"Successfully broadcasted log update for {job_id}")
        except Exception as e:
            logger.error(f"Failed to broadcast log update: {e}")

    def create_job(self, job_data: JobCreate) -> Job:
        """
        Create a new job.

        Args:
            job_data: Job creation data

        Returns:
            Created Job object

        Raises:
            ValueError: If invalid parameters are provided
            RuntimeError: If database operation fails
        """
        try:
            if not job_data.name or not job_data.name.strip():
                raise ValueError("Job name is required")
            if not job_data.workflow_type or not job_data.workflow_type.strip():
                raise ValueError("workflow_type is required")
            if get_recipe(job_data.workflow_type) is None:
                raise ValueError(f"Unknown workflow_type: {job_data.workflow_type}")

            options = job_data.options or WorkflowOptions()
            resolved = resolve_steps(job_data.workflow_type, options)
            snapshot = build_snapshot(job_data.workflow_type, options, resolved)

            metadata = dict(job_data.metadata or {})
            metadata["workflow_type"] = job_data.workflow_type
            workflow_meta = options.model_dump()
            # Keep file_type for diagram/progress consumers (not a WorkflowOptions field)
            fa = metadata.get("file_analysis") or {}
            if fa.get("file_type"):
                workflow_meta["file_type"] = fa["file_type"]
            metadata["workflow"] = workflow_meta

            job = Job(
                name=job_data.name.strip(),
                description=job_data.description,
                status=JobStatus.PENDING,
                total_steps=len(resolved),
                completed_steps=0,
                job_metadata=_json_safe(metadata),
                created_by=job_data.created_by,
                workflow_type=job_data.workflow_type,
                workflow_snapshot=snapshot,
            )
            self.db.add(job)
            self.db.flush()

            self.mint_steps_from_recipe(
                job.id,
                job_data.workflow_type,
                options,
                commit=False,
                resolved=resolved,
            )

            self.db.commit()
            self.db.refresh(job)

            self._log_job_event(
                job.id,
                LogLevel.INFO,
                f"Job '{job.name}' created successfully",
                {"job_id": str(job.id)},
            )

            logger.info(f"Created workflow {job.id}: {job.name}")
            return job

        except (ValueError, RuntimeError):
            self.db.rollback()
            raise
        except SQLAlchemyError as e:
            self.db.rollback()
            logger.error(f"Database error creating workflow: {str(e)}")
            raise RuntimeError(f"Database operation failed: {str(e)}")
        except Exception as e:
            self.db.rollback()
            logger.error(f"Unexpected error creating workflow: {str(e)}")
            raise RuntimeError(f"Failed to create workflow: {str(e)}")

    def mint_steps_from_recipe(
        self,
        job_id: Union[str, uuid.UUID],
        workflow_type: str,
        options: WorkflowOptions,
        commit: bool = True,
        resolved: Optional[List] = None,
    ) -> List[JobStep]:
        """Mint JobStep rows from a workflow recipe. Default commits; use commit=False
        when the caller owns the surrounding transaction (e.g. create_job).
        Pass resolved steps to avoid a second resolve_steps call."""
        steps = (
            resolved if resolved is not None else resolve_steps(workflow_type, options)
        )
        minted: List[JobStep] = []
        for step in steps:
            created = self.add_job_step(
                job_id,
                JobStepCreate(
                    step_name=step.step_name,
                    step_order=step.step_order,
                    container_name=step.container_name,
                ),
                commit=commit,
            )
            if created is None:
                raise ValueError(f"Job not found: {job_id}")
            minted.append(created)
        return minted

    def get_job(self, job_id: Union[str, uuid.UUID]) -> Optional[Job]:
        """
        Get workflow by ID, re-reading the row rather than trusting the session.

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

            # populate_existing() is load-bearing. SessionLocal sets
            # expire_on_commit=False, so an instance this session already loaded is
            # never expired; a plain query would fetch the row and then *discard* it
            # in favour of the identity-mapped instance. Long-lived background
            # sessions -- the Nextflow poll loop re-reads the job every 5s for the
            # life of a run -- would therefore never observe a write made by another
            # session, so a mid-run cancellation was invisible and the monitor
            # generated reports for a cancelled job.
            #
            # populate_existing() refreshes the *same* Python object in place rather
            # than returning a new one, so callers holding a reference across calls
            # keep working (and get fresh values for free). It does overwrite
            # unflushed local changes, and autoflush is off, so any caller that
            # mutates a Job must flush before reading it back -- see
            # _update_job_progress, which flushes for exactly this reason.
            return (
                self.db.query(Job).filter(Job.id == job_id).populate_existing().first()
            )

        except (ValueError, RuntimeError):
            raise
        except SQLAlchemyError as e:
            logger.error(f"Database error getting workflow: {str(e)}")
            raise RuntimeError(f"Database operation failed: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error getting workflow: {str(e)}")
            raise RuntimeError(f"Failed to get workflow: {str(e)}")

    def update_job(
        self, job_id: Union[str, uuid.UUID], update_data: JobUpdate
    ) -> Optional[Job]:
        """
        Update job.

        Args:
            job_id: Job ID to update
            update_data: Update data

        Returns:
            Updated Job object or None if not found

        Raises:
            ValueError: If invalid parameters
            RuntimeError: If database operation fails
        """
        try:
            # Convert job_id to UUID if needed
            if isinstance(job_id, str):
                try:
                    job_id = uuid.UUID(job_id)
                except ValueError:
                    raise ValueError(f"Invalid job_id format: {job_id}")

            # Get the workflow
            job = self.db.query(Job).filter(Job.id == job_id).first()
            if not job:
                return None

            # Update fields
            if update_data.name is not None:
                job.name = update_data.name.strip()
            if update_data.description is not None:
                job.description = update_data.description
            if update_data.status is not None:
                job.status = update_data.status
            if update_data.total_steps is not None:
                job.total_steps = update_data.total_steps
            if update_data.completed_steps is not None:
                job.completed_steps = update_data.completed_steps
            if update_data.metadata is not None:
                job.job_metadata = _json_safe(update_data.metadata)

            # Update timing fields based on status
            if update_data.status == JobStatus.RUNNING and not job.started_at:
                job.started_at = datetime.now(timezone.utc)
            elif update_data.status in [
                JobStatus.COMPLETED,
                JobStatus.FAILED,
                JobStatus.CANCELLED,
            ]:
                job.completed_at = datetime.now(timezone.utc)

            self.db.commit()
            self.db.refresh(job)

            # Log workflow update
            self._log_job_event(
                job.id,
                LogLevel.INFO,
                f"Job updated: {update_data.status if update_data.status else 'fields updated'}",
                {
                    "updated_fields": [
                        k
                        for k, v in update_data.model_dump(exclude_unset=True).items()
                        if v is not None
                    ]
                },
            )

            # Get proper progress calculation using WorkflowProgressCalculator
            progress_response = self.get_job_progress(job.id)

            # Broadcast workflow update via WebSocket
            try:
                schedule_coroutine(
                    self._broadcast_job_update(
                        str(job.id),
                        {
                            "job_id": str(job.id),
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
                                else "Processing..."
                            ),
                            "started_at": (
                                job.started_at.isoformat() if job.started_at else None
                            ),
                            "completed_at": (
                                job.completed_at.isoformat()
                                if job.completed_at
                                else None
                            ),
                        },
                    )
                )
            except Exception as e:
                logger.error(f"Failed to schedule workflow update broadcast: {e}")

            logger.info(f"Updated workflow {job.id}")
            return job

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

    def add_job_step(
        self,
        job_id: Union[str, uuid.UUID],
        step_data: JobStepCreate,
        commit: bool = True,
    ) -> Optional[JobStep]:
        """
        Add a step to a job.

        Args:
            job_id: Job ID
            step_data: Step creation data
            commit: If False, flush only so the caller can commit atomically

        Returns:
            Created JobStep object or None if workflow not found

        Raises:
            ValueError: If invalid parameters
            RuntimeError: If database operation fails
        """
        try:
            # Convert job_id to UUID if needed
            if isinstance(job_id, str):
                try:
                    job_id = uuid.UUID(job_id)
                except ValueError:
                    raise ValueError(f"Invalid job_id format: {job_id}")

            # Get the workflow
            job = self.db.query(Job).filter(Job.id == job_id).first()
            if not job:
                return None

            # Create the step
            step = JobStep(
                job_id=job_id,
                step_name=step_data.step_name,
                step_order=step_data.step_order,
                container_name=step_data.container_name,
                output_data=step_data.output_data,
            )

            self.db.add(step)
            if commit:
                self.db.commit()
                self.db.refresh(step)
                self._log_job_event(
                    job_id,
                    LogLevel.INFO,
                    f"Step '{step_data.step_name}' added to workflow",
                    {"step_id": str(step.id), "step_order": step_data.step_order},
                )
            else:
                self.db.flush()

            logger.info(f"Added step {step.id} to workflow {job_id}")
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

    def update_job_step(
        self,
        job_id: Union[str, uuid.UUID],
        step_name: str,
        update_data: JobStepUpdate,
    ) -> Optional[JobStep]:
        """
        Update a workflow step.

        Args:
            job_id: Job ID
            step_name: Step name to update
            update_data: Update data

        Returns:
            Updated JobStep object or None if not found

        Raises:
            ValueError: If invalid parameters
            RuntimeError: If database operation fails
        """
        try:
            # Convert job_id to UUID if needed
            if isinstance(job_id, str):
                try:
                    job_id = uuid.UUID(job_id)
                except ValueError:
                    raise ValueError(f"Invalid job_id format: {job_id}")

            # Get the step
            step = (
                self.db.query(JobStep)
                .filter(
                    and_(
                        JobStep.job_id == job_id,
                        JobStep.step_name == step_name,
                    )
                )
                .first()
            )

            if not step:
                return None

            # Update fields
            if update_data.status is not None:
                step.status = update_data.status
            if update_data.container_name is not None:
                step.container_name = update_data.container_name
            if update_data.output_data is not None:
                step.output_data = _json_safe(update_data.output_data)
            if update_data.error_details is not None:
                step.error_details = _json_safe(update_data.error_details)
            if update_data.retry_count is not None:
                step.retry_count = update_data.retry_count

            # Log message if provided
            if update_data.message is not None:
                self._log_job_event(
                    job_id,
                    "info",
                    update_data.message,
                    {"step_status": step.status, "step_name": step_name},
                )

            # Update timing fields based on status
            if update_data.status == StepStatus.RUNNING and not step.started_at:
                step.started_at = datetime.now(timezone.utc)
            elif update_data.status in [
                StepStatus.COMPLETED,
                StepStatus.FAILED,
                StepStatus.SKIPPED,
            ]:
                step.completed_at = datetime.now(timezone.utc)
                if step.started_at:
                    # started_at is always written as aware UTC, but it comes back
                    # naive from backends without timezone support (SQLite), and
                    # subtracting mixed awareness raises TypeError. Normalise both
                    # ends rather than assume the backend.
                    started_at = step.started_at
                    if started_at.tzinfo is None:
                        started_at = started_at.replace(tzinfo=timezone.utc)
                    step.duration_seconds = int(
                        (step.completed_at - started_at).total_seconds()
                    )

            self.db.commit()
            self.db.refresh(step)

            # Log step update
            self._log_job_event(
                job_id,
                LogLevel.INFO,
                f"Step '{step_name}' updated: {update_data.status if update_data.status else 'fields updated'}",
                {
                    "step_id": str(step.id),
                    "step_status": (
                        update_data.status if update_data.status else step.status
                    ),
                },
            )

            # Update workflow progress if step completed
            if update_data.status == StepStatus.COMPLETED:
                self._update_job_progress(job_id)

            # Broadcast step update via WebSocket
            try:
                # Schedule the broadcast task for execution
                schedule_coroutine(
                    self._broadcast_step_update(
                        str(job_id),
                        step_name,
                        {
                            "step_name": step_name,
                            "status": step.status,
                            "container_name": step.container_name,
                            "started_at": (
                                step.started_at.isoformat() if step.started_at else None
                            ),
                            "completed_at": (
                                step.completed_at.isoformat()
                                if step.completed_at
                                else None
                            ),
                            "duration_seconds": step.duration_seconds,
                            "output_data": step.output_data,
                            "error_details": step.error_details,
                            "retry_count": step.retry_count,
                        },
                    )
                )
            except Exception as e:
                logger.error(f"Failed to schedule step update broadcast: {e}")

            # Also broadcast workflow progress update for any step status change
            try:
                # Get updated progress information
                progress_response = self.get_job_progress(job_id)
                if progress_response:
                    # Get workflow object for additional data. Via get_job() so the
                    # status/step counts pushed to the browser are the committed ones
                    # and not whatever this session happened to load earlier.
                    job = self.get_job(job_id)
                    if job:
                        # Schedule workflow progress broadcast
                        schedule_coroutine(
                            self._broadcast_job_update(
                                str(job_id),
                                {
                                    "job_id": str(job_id),
                                    "status": job.status,
                                    "total_steps": job.total_steps,
                                    "completed_steps": job.completed_steps,
                                    "progress_percentage": progress_response.progress_percentage,
                                    "current_step": progress_response.current_step,
                                    "message": progress_response.message,
                                    "started_at": (
                                        job.started_at.isoformat()
                                        if job.started_at
                                        else None
                                    ),
                                    "completed_at": (
                                        job.completed_at.isoformat()
                                        if job.completed_at
                                        else None
                                    ),
                                },
                            )
                        )
            except Exception as e:
                logger.error(f"Failed to schedule workflow progress broadcast: {e}")

            logger.info(f"Updated step {step.id} in workflow {job_id}")
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

    def get_job_progress(
        self, job_id: Union[str, uuid.UUID]
    ) -> Optional[JobProgressResponse]:
        """
        Get workflow progress information.

        Args:
            job_id: Job ID

        Returns:
            JobProgressResponse or None if workflow not found

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

            # Get the workflow. populate_existing() for the same reason as get_job,
            # and selectinload(Job.steps) because populate_existing() alone is NOT
            # enough for the collection this method reads below.
            #
            # populate_existing() only *expires* job.steps. The reload that follows
            # is an ordinary lazy load, which is served from the identity map, so any
            # JobStep instance the caller still holds a reference to comes back with
            # its stale column values. (It looks like it works when nothing holds the
            # steps: expiring the collection drops the last strong reference, the weak
            # identity map lets them be collected, and the reload builds fresh objects.
            # Hold them -- as anything walking job.steps across a poll does -- and the
            # statuses freeze.) populate_existing() *does* propagate into eager
            # loaders, so naming the relationship refreshes the held instances in
            # place. Step rows are written by the container services on their own
            # request-scoped sessions, so this is the whole point of the method.
            job = (
                self.db.query(Job)
                .filter(Job.id == job_id)
                .options(selectinload(Job.steps))
                .populate_existing()
                .first()
            )
            if not job:
                return None

            # Convert steps to dictionary format for progress calculator
            steps_dict = [
                {
                    "step_name": step.step_name,
                    "status": step.status,  # status is already a string from database
                    "step_order": step.step_order,
                    "container_name": step.container_name,
                    "output_data": step.output_data,  # Include output_data for container progress
                    # NOTE: there used to be a "metadata": step.metadata entry here.
                    # JobStep has no metadata column, so that expression returned
                    # SQLAlchemy's MetaData object; WorkflowProgressCalculator gates on
                    # isinstance(metadata, dict) and so silently skipped it every time.
                    # Container-reported per-step progress has therefore never been
                    # picked up from this path — containers must use output_data.
                }
                for step in job.steps
            ]

            # Get workflow metadata for configuration
            workflow_config = (
                job.job_metadata.get("workflow", {}) if job.job_metadata else {}
            )

            # Calculate progress using centralized calculator
            progress_calculator = WorkflowProgressCalculator()
            progress_info = progress_calculator.calculate_progress_from_steps(
                steps_dict, workflow_config, str(job.id)
            )

            # Calculate estimated completion
            estimated_completion = None
            if job.started_at and job.status == JobStatus.RUNNING:
                # Simple estimation based on current progress
                if progress_info.progress_percentage > 0:
                    # started_at is always written as aware UTC, but comes back naive
                    # from backends without timezone support (SQLite), and subtracting
                    # mixed awareness raises TypeError -- which this method wraps into
                    # RuntimeError, propagating out of _update_job_progress and taking
                    # the whole update_job_step call down with it. Normalise rather
                    # than assume the backend, exactly as update_job_step does.
                    started_at = job.started_at
                    if started_at.tzinfo is None:
                        started_at = started_at.replace(tzinfo=timezone.utc)
                    elapsed = datetime.now(timezone.utc) - started_at
                    estimated_total = elapsed / (
                        progress_info.progress_percentage / 100
                    )
                    estimated_completion = started_at + estimated_total

            return JobProgressResponse(
                job_id=str(job.id),
                status=JobStatus(job.status),
                total_steps=job.total_steps or 0,
                completed_steps=job.completed_steps or 0,
                progress_percentage=round(progress_info.progress_percentage, 2),
                current_step=progress_info.current_step_name or progress_info.stage,
                estimated_completion=estimated_completion,
                message=progress_info.message,
            )

        except (ValueError, RuntimeError):
            raise
        except SQLAlchemyError as e:
            logger.error(f"Database error getting workflow progress: {str(e)}")
            raise RuntimeError(f"Database operation failed: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error getting workflow progress: {str(e)}")
            raise RuntimeError(f"Failed to get workflow progress: {str(e)}")

    def log_job_event(
        self, job_id: Union[str, uuid.UUID], log_data: JobLogCreate
    ) -> JobLog:
        """
        Log a workflow event.

        Args:
            job_id: Job ID
            log_data: Log data

        Returns:
            Created JobLog object

        Raises:
            ValueError: If invalid parameters
            RuntimeError: If database operation fails
        """
        try:
            # Convert job_id to UUID if needed
            if isinstance(job_id, str):
                try:
                    job_id = uuid.UUID(job_id)
                except ValueError:
                    raise ValueError(f"Invalid job_id format: {job_id}")

            # Create the log entry
            log_entry = JobLog(
                job_id=job_id,
                step_name=log_data.step_name,
                log_level=log_data.log_level,
                message=log_data.message,
                log_metadata=_json_safe(log_data.metadata),
            )

            self.db.add(log_entry)
            self.db.commit()
            self.db.refresh(log_entry)

            # Broadcast log update via WebSocket
            try:
                # Schedule the broadcast task for execution
                schedule_coroutine(
                    self._broadcast_log_update(
                        str(job_id),
                        {
                            "step_name": log_entry.step_name,
                            "log_level": log_entry.log_level,
                            "message": log_entry.message,
                            "metadata": log_entry.log_metadata,
                            "timestamp": log_entry.timestamp.isoformat(),
                        },
                    )
                )
            except Exception as e:
                logger.error(f"Failed to schedule log update broadcast: {e}")

            logger.info(
                f"Logged event for workflow {job_id}: {log_data.log_level} - {log_data.message}"
            )
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

    def get_job_logs(
        self, job_id: Union[str, uuid.UUID], limit: int = 100
    ) -> List[JobLog]:
        """
        Get workflow logs.

        Args:
            job_id: Job ID
            limit: Maximum number of logs to return

        Returns:
            List of JobLog objects

        Raises:
            ValueError: If invalid parameters
            RuntimeError: If database operation fails
        """
        try:
            # Convert job_id to UUID if needed
            if isinstance(job_id, str):
                try:
                    job_id = uuid.UUID(job_id)
                except ValueError:
                    raise ValueError(f"Invalid job_id format: {job_id}")

            return (
                self.db.query(JobLog)
                .filter(JobLog.job_id == job_id)
                # id breaks ties: several entries are routinely written inside one
                # timestamp tick (a step transition logs alongside a posted entry),
                # and ordering by timestamp alone leaves their relative order to the
                # database. id is a monotonic integer, so this is insertion order.
                .order_by(desc(JobLog.timestamp), desc(JobLog.id))
                .limit(limit)
                .all()
            )

        except (ValueError, RuntimeError):
            raise
        except SQLAlchemyError as e:
            logger.error(f"Database error getting workflow logs: {str(e)}")
            raise RuntimeError(f"Database operation failed: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error getting workflow logs: {str(e)}")
            raise RuntimeError(f"Failed to get workflow logs: {str(e)}")

    def _update_job_progress(self, job_id: uuid.UUID) -> None:
        """Update workflow progress based on completed steps."""
        try:
            job = (
                self.db.query(Job).filter(Job.id == job_id).populate_existing().first()
            )
            if not job:
                return

            # Count completed steps
            completed_steps = (
                self.db.query(JobStep)
                .filter(
                    and_(
                        JobStep.job_id == job_id,
                        JobStep.status == StepStatus.COMPLETED,
                    )
                )
                .count()
            )

            # Update workflow
            job.completed_steps = completed_steps

            # Flush before the read below. get_job_progress() re-reads the row with
            # populate_existing(), and autoflush is off, so the pending
            # completed_steps write would be silently overwritten by the old value
            # and the commit at the end of this method would emit no UPDATE for it.
            # Flushing puts the new value into the transaction first, so the re-read
            # returns it.
            self.db.flush()

            # Get progress information to check if workflow should be completed
            progress_response = self.get_job_progress(job_id)

            # Check if workflow should be completed based on progress percentage
            if progress_response and progress_response.progress_percentage >= 100:
                job.status = JobStatus.COMPLETED
                job.completed_at = datetime.now(timezone.utc)

                # Log workflow completion
                self._log_job_event(
                    job_id,
                    LogLevel.INFO,
                    "Job completed successfully with reports generated",
                    {
                        "completed_steps": completed_steps,
                        "total_steps": job.total_steps,
                    },
                )

                # Perform centralized cleanup of temporary files
                try:
                    # Extract patient_id from workflow metadata if available
                    patient_id = None
                    if hasattr(job, "job_metadata") and job.job_metadata:
                        patient_id = job.job_metadata.get("patient_id")

                    # Clean up workflow-specific temporary files
                    cleanup_result = cleanup_service.cleanup_job_files(
                        job_id=str(job_id), patient_id=patient_id
                    )

                    # Log cleanup results
                    if cleanup_result.get("success", False):
                        logger.info(
                            f"Job cleanup completed for {job_id}: "
                            f"{cleanup_result['total_items_cleaned']} items, "
                            f"{cleanup_result['total_size_cleaned']} bytes cleaned"
                        )
                    else:
                        logger.warning(
                            f"Job cleanup had issues for {job_id}: "
                            f"{len(cleanup_result.get('failed_paths', []))} failed paths"
                        )

                except Exception as e:
                    logger.error(
                        f"Failed to cleanup temporary files for workflow {job_id}: {e}"
                    )

                # Broadcast final workflow completion update
                try:
                    schedule_coroutine(
                        self._broadcast_job_update(
                            str(job_id),
                            {
                                "job_id": str(job_id),
                                "status": job.status,
                                "total_steps": job.total_steps,
                                "completed_steps": job.completed_steps,
                                "progress_percentage": 100,
                                "current_step": "completed",
                                "message": "Processing complete! - All processing finished",
                                "started_at": (
                                    job.started_at.isoformat()
                                    if job.started_at
                                    else None
                                ),
                                "completed_at": (
                                    job.completed_at.isoformat()
                                    if job.completed_at
                                    else None
                                ),
                            },
                        )
                    )
                except Exception as e:
                    logger.error(f"Failed to broadcast workflow completion: {e}")

            self.db.commit()

        except SQLAlchemyError as e:
            self.db.rollback()
            logger.error(f"Failed to update workflow progress: {str(e)}")

    def _log_job_event(
        self,
        job_id: uuid.UUID,
        level: str,
        message: str,
        metadata: Dict[str, Any] = None,
    ) -> None:
        """Log a workflow event (internal method)."""
        try:
            log_entry = JobLog(
                job_id=job_id,
                log_level=level,
                message=message,
                # NOT `metadata=` — that is SQLAlchemy's MetaData class attribute on
                # every declarative model, so the constructor silently shadows it with
                # an instance attribute and the payload never reaches the column.
                log_metadata=_json_safe(metadata or {}),
            )
            self.db.add(log_entry)
            self.db.commit()
        except SQLAlchemyError as e:
            logger.error(f"Failed to log workflow event: {str(e)}")
            # Don't fail the main operation if logging fails

    def get_job_steps(self, job_id: Union[str, uuid.UUID]) -> List[JobStepResponse]:
        """
        Get all steps for a job.

        Args:
            job_id: ID of the workflow

        Returns:
            List of workflow step responses
        """
        try:
            job_id = uuid.UUID(str(job_id))

            # populate_existing(): container services write these rows from their
            # own sessions, and a session that already holds the JobStep instances
            # (having walked job.steps) would otherwise be handed its cached copies
            # and report step statuses frozen at first load.
            steps = (
                self.db.query(JobStep)
                .filter(JobStep.job_id == job_id)
                .order_by(JobStep.step_order)
                .populate_existing()
                .all()
            )

            return [JobStepResponse.model_validate(step) for step in steps]

        except Exception as e:
            logger.error(f"Failed to get workflow steps: {str(e)}")
            return []

    def link_pharmcat_run(self, job_id: str, pharmcat_run_id: str) -> bool:
        """
        Link a PharmCAT run to a job.

        Args:
            job_id: Job ID
            pharmcat_run_id: PharmCAT run ID

        Returns:
            True if successful, False otherwise
        """
        try:
            job_id = uuid.UUID(str(job_id))
            # populate_existing(): this is a read-modify-write of job_metadata, so it
            # has to start from the row as it stands now. Reading a stale copy and
            # writing the merged dict back would drop every key another session has
            # added since -- including the "cancelled" flag the cancel endpoint
            # writes -- because the whole dict is replaced, not patched.
            job = (
                self.db.query(Job).filter(Job.id == job_id).populate_existing().first()
            )

            if not job:
                logger.error(f"Job {job_id} not found")
                return False

            # Update workflow metadata with PharmCAT run ID.
            # dict() is load-bearing: jobs.job_metadata is a plain Column(JSON)
            # with no MutableDict, so mutating the attached dict in place and
            # assigning the *same object* back leaves the attribute history
            # empty -- SQLAlchemy emits no UPDATE and the link is silently lost
            # the moment the session is expired or a later request reloads the
            # row. create_job always seeds job_metadata, so this is never the
            # accidentally-safe empty-dict case. Same pattern as job_router.py.
            metadata = dict(job.job_metadata or {})
            metadata["pharmcat_run_id"] = pharmcat_run_id
            metadata["pharmcat_linked_at"] = datetime.now(timezone.utc).isoformat()

            job.job_metadata = metadata
            self.db.commit()

            logger.info(
                f"Successfully linked PharmCAT run {pharmcat_run_id} to workflow {job_id}"
            )
            return True

        except Exception as e:
            logger.error(f"Error linking PharmCAT run to workflow: {e}")
            self.db.rollback()
            return False

    def append_workflow_warning(
        self, job_id: Union[str, uuid.UUID], warning: str
    ) -> bool:
        """Add one alert to ``job_metadata['workflow']['warnings']`` and commit.

        This is the channel the report templates read: ``generate_report`` lifts
        that list off the Job row and hands it to both templates' "Alerts and
        Warnings" section. Anything a later stage learns that the reader needs to
        know -- the PharmCAT TSV having rescued the run, say -- reaches the page
        through here; the upload path seeds the same list.

        The caller's ordering obligation: ``generate_report`` reads the row with
        a plain query, no ``populate_existing()``, so it only observes this write
        on a session opened afterwards. Commit before that session exists, not
        merely before the call (tests/test_generator_job_metadata_read.py).

        Idempotent on the exact text: final-stage progression can be driven more
        than once for a job, and the reader should not get the same banner twice.

        Returns True when the warning is stored (or was already stored).
        """
        try:
            job_uuid = uuid.UUID(str(job_id))
            # populate_existing(): read-modify-write of job_metadata, and the
            # session that gets here has usually loaded this Job already. Same
            # reasoning as link_pharmcat_run.
            job = (
                self.db.query(Job)
                .filter(Job.id == job_uuid)
                .populate_existing()
                .first()
            )

            if not job:
                logger.error(f"Job {job_id} not found; workflow warning not stored")
                return False

            # New dicts at every level, not in-place mutation: job_metadata is a
            # plain Column(JSON) with no MutableDict, so assigning the same
            # object back leaves the attribute history empty and SQLAlchemy
            # emits no UPDATE. Same trap as link_pharmcat_run.
            metadata = dict(job.job_metadata or {})
            workflow = dict(metadata.get("workflow") or {})
            warnings = list(workflow.get("warnings") or [])
            if warning in warnings:
                return True
            warnings.append(warning)
            workflow["warnings"] = warnings
            metadata["workflow"] = workflow

            job.job_metadata = metadata
            self.db.commit()
            return True

        except Exception as e:
            logger.error(f"Error appending workflow warning to job {job_id}: {e}")
            self.db.rollback()
            return False

    def get_pharmcat_run_id(self, job_id: str) -> Optional[str]:
        """
        Get the PharmCAT run ID for a job.

        Args:
            job_id: Job ID

        Returns:
            PharmCAT run ID if found, None otherwise
        """
        try:
            job_id = uuid.UUID(str(job_id))
            # populate_existing(): the link is written by whichever session finished
            # the PharmCAT stage, which is not necessarily the session asking here.
            job = (
                self.db.query(Job).filter(Job.id == job_id).populate_existing().first()
            )

            if not job:
                return None

            metadata = job.job_metadata or {}
            return metadata.get("pharmcat_run_id")

        except Exception as e:
            logger.error(f"Error getting PharmCAT run ID for workflow {job_id}: {e}")
            return None

    def get_pharmcat_data(self, job_id: str) -> Optional[Dict[str, Any]]:
        """
        Get PharmCAT data for a job.

        Args:
            job_id: Job ID

        Returns:
            Dict containing normalized PharmCAT data, or None if not found
        """
        try:
            pharmcat_service = PharmCATDataService(self.db)
            return pharmcat_service.get_pharmcat_data_for_workflow(job_id)
        except Exception as e:
            logger.error(f"Error getting PharmCAT data for workflow {job_id}: {e}")
            return None
