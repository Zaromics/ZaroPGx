"""
Workflow API Client Library

This module provides a client library for containers to communicate with the
workflow monitoring system. It supports both Python and shell script usage.
"""

import httpx
import asyncio
import json
import logging
import os
from typing import Dict, Any, Optional, Union
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class WorkflowClient:
    """
    Client for communicating with the workflow monitoring API.
    
    This client provides methods for:
    - Updating workflow step status
    - Logging workflow events
    - Reporting progress and errors
    - Retry logic and error handling
    """
    
    def __init__(self, base_url: str = None, workflow_id: str = None, step_name: str = None):
        """
        Initialize the workflow client.
        
        Args:
            base_url: Base URL for the workflow API (defaults to environment variable)
            workflow_id: Workflow ID to track (defaults to environment variable)
            step_name: Step name for this client (defaults to environment variable)
        """
        self.base_url = base_url or os.getenv("WORKFLOW_API_BASE", "http://app:8000/api/v1")
        self.workflow_id = workflow_id or os.getenv("WORKFLOW_ID")
        self.step_name = step_name or os.getenv("STEP_NAME")

        if not self.workflow_id:
            raise ValueError("Workflow ID must be provided either as parameter or WORKFLOW_ID environment variable")

        if not self.step_name:
            # Try to infer step name from process name or use a default
            self.step_name = os.getenv("NXF_PROCESS_NAME", "unknown_step")
            logger.warning(f"STEP_NAME not provided, using inferred step name: {self.step_name}")
        
        self.client = httpx.AsyncClient(timeout=30.0)
        self.retry_count = 3
        self.retry_delay = 1.0
    
    async def __aenter__(self):
        """Async context manager entry."""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.client.aclose()
    
    async def _make_request(self, method: str, endpoint: str, data: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Make an HTTP request with retry logic.
        
        Args:
            method: HTTP method
            endpoint: API endpoint
            data: Request data
            
        Returns:
            Response data
            
        Raises:
            Exception: If request fails after retries
        """
        url = f"{self.base_url}{endpoint}"
        
        for attempt in range(self.retry_count):
            try:
                if method.upper() == "GET":
                    response = await self.client.get(url)
                elif method.upper() == "POST":
                    response = await self.client.post(url, json=data)
                elif method.upper() == "PUT":
                    response = await self.client.put(url, json=data)
                elif method.upper() == "DELETE":
                    response = await self.client.delete(url)
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")
                
                response.raise_for_status()
                return response.json() if response.content else {}
                
            except httpx.HTTPError as e:
                logger.warning(f"HTTP request failed (attempt {attempt + 1}/{self.retry_count}): {e}")
                if attempt == self.retry_count - 1:
                    raise Exception(f"Failed to make request after {self.retry_count} attempts: {e}")
                
                # Exponential backoff
                await asyncio.sleep(self.retry_delay * (2 ** attempt))
                
            except Exception as e:
                logger.error(f"Unexpected error making request: {e}")
                if attempt == self.retry_count - 1:
                    raise
                
                await asyncio.sleep(self.retry_delay * (2 ** attempt))
    
    async def update_step_status(self, status: str, message: str = None, 
                               output_data: Dict[str, Any] = None, 
                               error_details: Dict[str, Any] = None) -> bool:
        """
        Update the status of a workflow step.
        
        Args:
            status: New step status (pending, running, completed, failed, skipped)
            message: Status message
            output_data: Step output data
            error_details: Error details if step failed
            
        Returns:
            True if successful, False otherwise
        """
        try:
            data = {
                "status": status,
                "output_data": output_data or {},
                "error_details": error_details or {}
            }
            
            if message:
                data["message"] = message
            
            await self._make_request(
                "PUT",
                f"/workflows/{self.workflow_id}/steps/{self.step_name}",
                data
            )
            
            logger.info(f"Updated step {self.step_name} status to {status}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to update step status: {e}")
            return False
    
    async def log_event(self, level: str, message: str, metadata: Dict[str, Any] = None) -> bool:
        """
        Log an event for the workflow.
        
        Args:
            level: Log level (debug, info, warn, error)
            message: Log message
            metadata: Additional metadata
            
        Returns:
            True if successful, False otherwise
        """
        try:
            data = {
                "step_name": self.step_name,
                "log_level": level,
                "message": message,
                "metadata": metadata or {}
            }
            
            await self._make_request(
                "POST",
                f"/workflows/{self.workflow_id}/logs",
                data
            )
            
            logger.info(f"Logged {level} event: {message}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to log event: {e}")
            return False
    
    async def start_step(self, message: str = None) -> bool:
        """
        Mark a step as started.
        
        Args:
            message: Optional start message
            
        Returns:
            True if successful, False otherwise
        """
        return await self.update_step_status(
            "running",
            message or f"Step {self.step_name} started"
        )
    
    async def complete_step(self, message: str = None, output_data: Dict[str, Any] = None) -> bool:
        """
        Mark a step as completed.
        
        Args:
            message: Optional completion message
            output_data: Step output data
            
        Returns:
            True if successful, False otherwise
        """
        return await self.update_step_status(
            "completed",
            message or f"Step {self.step_name} completed",
            output_data
        )
    
    async def fail_step(self, error_message: str, error_details: Dict[str, Any] = None) -> bool:
        """
        Mark a step as failed.
        
        Args:
            error_message: Error message
            error_details: Additional error details
            
        Returns:
            True if successful, False otherwise
        """
        return await self.update_step_status(
            "failed",
            error_message,
            error_details=error_details
        )
    
    async def skip_step(self, reason: str = None) -> bool:
        """
        Mark a step as skipped.
        
        Args:
            reason: Reason for skipping
            
        Returns:
            True if successful, False otherwise
        """
        return await self.update_step_status(
            "skipped",
            reason or f"Step {self.step_name} skipped"
        )
    
    async def log_progress(self, message: str, metadata: Dict[str, Any] = None) -> bool:
        """
        Log progress information.
        
        Args:
            message: Progress message
            metadata: Additional metadata
            
        Returns:
            True if successful, False otherwise
        """
        return await self.log_event("info", message, metadata)
    
    async def log_warning(self, message: str, metadata: Dict[str, Any] = None) -> bool:
        """
        Log a warning.
        
        Args:
            message: Warning message
            metadata: Additional metadata
            
        Returns:
            True if successful, False otherwise
        """
        return await self.log_event("warn", message, metadata)
    
    async def log_error(self, message: str, metadata: Dict[str, Any] = None) -> bool:
        """
        Log an error.
        
        Args:
            message: Error message
            metadata: Additional metadata
            
        Returns:
            True if successful, False otherwise
        """
        return await self.log_event("error", message, metadata)
    
    async def log_debug(self, message: str, metadata: Dict[str, Any] = None) -> bool:
        """
        Log debug information.
        
        Args:
            message: Debug message
            metadata: Additional metadata
            
        Returns:
            True if successful, False otherwise
        """
        return await self.log_event("debug", message, metadata)
    
    async def get_workflow_status(self) -> Optional[Dict[str, Any]]:
        """
        Get current workflow status.
        
        Returns:
            Workflow status data or None if failed
        """
        try:
            return await self._make_request("GET", f"/workflows/{self.workflow_id}")
        except Exception as e:
            logger.error(f"Failed to get workflow status: {e}")
            return None
    
    async def get_workflow_progress(self) -> Optional[Dict[str, Any]]:
        """
        Get workflow progress information.
        
        Returns:
            Progress data or None if failed
        """
        try:
            return await self._make_request("GET", f"/workflows/{self.workflow_id}/progress")
        except Exception as e:
            logger.error(f"Failed to get workflow progress: {e}")
            return None
    
    async def is_workflow_cancelled(self) -> bool:
        """
        Check if the workflow has been cancelled.
        
        This method should be called before starting any processing to ensure
        the workflow hasn't been cancelled by the user.
        
        Uses HTTP API to communicate with the main app.
        
        Returns:
            True if workflow is cancelled, False otherwise
        """
        try:
            # Use HTTP API to check workflow status
            status_data = await self.get_workflow_status()
            if status_data:
                workflow_status = status_data.get("status", "").lower()
                return workflow_status == "cancelled"
            return False
        except Exception as e:
            logger.warning(f"Could not check workflow cancellation status: {e}")
            # Fail safe - don't block processing if we can't check status
            return False
    


# Convenience functions for non-async usage
def create_workflow_client(workflow_id: str = None, step_name: str = None) -> WorkflowClient:
    """
    Create a workflow client (synchronous wrapper).
    
    Args:
        workflow_id: Workflow ID
        step_name: Step name
        
    Returns:
        WorkflowClient instance
    """
    return WorkflowClient(workflow_id=workflow_id, step_name=step_name)


def run_async(coro):
    """
    Run an async coroutine in a new event loop.
    
    Args:
        coro: Async coroutine to run
        
    Returns:
        Coroutine result
    """
    try:
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(coro)
    except RuntimeError:
        # No event loop running, create a new one
        return asyncio.run(coro)


# Synchronous wrapper functions
def update_step_status_sync(workflow_id: str, step_name: str, status: str, 
                           message: str = None, output_data: Dict[str, Any] = None,
                           error_details: Dict[str, Any] = None) -> bool:
    """Synchronous wrapper for update_step_status."""
    async def _update():
        async with WorkflowClient(workflow_id=workflow_id, step_name=step_name) as client:
            return await client.update_step_status(status, message, output_data, error_details)
    
    return run_async(_update())


def log_event_sync(workflow_id: str, step_name: str, level: str, 
                  message: str, metadata: Dict[str, Any] = None) -> bool:
    """Synchronous wrapper for log_event."""
    async def _log():
        async with WorkflowClient(workflow_id=workflow_id, step_name=step_name) as client:
            return await client.log_event(level, message, metadata)
    
    return run_async(_log())


def start_step_sync(workflow_id: str, step_name: str, message: str = None) -> bool:
    """Synchronous wrapper for start_step."""
    return update_step_status_sync(workflow_id, step_name, "running", message)


def complete_step_sync(workflow_id: str, step_name: str, message: str = None, 
                      output_data: Dict[str, Any] = None) -> bool:
    """Synchronous wrapper for complete_step."""
    return update_step_status_sync(workflow_id, step_name, "completed", message, output_data)


def fail_step_sync(workflow_id: str, step_name: str, error_message: str, 
                  error_details: Dict[str, Any] = None) -> bool:
    """Synchronous wrapper for fail_step."""
    return update_step_status_sync(workflow_id, step_name, "failed", error_message, 
                                 error_details=error_details)


def skip_step_sync(workflow_id: str, step_name: str, reason: str = None) -> bool:
    """Synchronous wrapper for skip_step."""
    return update_step_status_sync(workflow_id, step_name, "skipped", reason)
