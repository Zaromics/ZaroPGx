"""
WebSocket Manager for real-time job updates.

This module provides WebSocket connection management for real-time
job progress updates and notifications.
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    Manages WebSocket connections for job monitoring.

    This class handles:
    - Connection registration and cleanup
    - Message broadcasting to specific jobs
    - Connection health monitoring
    - Message queuing for disconnected clients
    """

    def __init__(self):
        # Map job_id -> set of WebSocket connections
        self.job_connections: Dict[str, Set[WebSocket]] = {}
        # Map connection_id -> job_id for cleanup
        self.connection_jobs: Dict[str, str] = {}
        # Message queue for disconnected clients (optional feature)
        self.message_queues: Dict[str, list] = {}

    async def connect(self, websocket: WebSocket, job_id: str) -> str:
        """
        Accept a WebSocket connection for a specific job.

        Args:
            websocket: WebSocket connection
            job_id: Job ID to monitor

        Returns:
            Connection ID for tracking
        """
        await websocket.accept()

        # Generate connection ID. The uuid4 suffix is load-bearing: two clients
        # connecting to the same job within one timestamp tick used to receive
        # identical IDs, so the second registration overwrote the first in
        # connection_jobs and disconnecting either one unregistered both.
        connection_id = f"{job_id}_{uuid.uuid4()}"

        # Register connection
        if job_id not in self.job_connections:
            self.job_connections[job_id] = set()

        self.job_connections[job_id].add(websocket)
        self.connection_jobs[connection_id] = job_id

        logger.info(
            f"WebSocket connected for job {job_id} (connection: {connection_id})"
        )
        return connection_id

    def disconnect(self, websocket: WebSocket, connection_id: str):
        """
        Remove a WebSocket connection.

        Args:
            websocket: WebSocket connection to remove
            connection_id: Connection ID for cleanup
        """
        job_id = self.connection_jobs.get(connection_id)
        if job_id and job_id in self.job_connections:
            self.job_connections[job_id].discard(websocket)

            # Clean up empty job connection sets
            if not self.job_connections[job_id]:
                del self.job_connections[job_id]

        # Remove connection mapping
        if connection_id in self.connection_jobs:
            del self.connection_jobs[connection_id]

        # Clean up message queue
        if connection_id in self.message_queues:
            del self.message_queues[connection_id]

        logger.info(
            f"WebSocket disconnected for job {job_id} (connection: {connection_id})"
        )

    async def send_job_update(self, job_id: str, message: Dict[str, Any]):
        """
        Send an update to all connections monitoring a specific job.

        Args:
            job_id: Job ID to send update for
            message: Message data to send
        """
        if job_id not in self.job_connections:
            logger.warning(f"No WebSocket connections found for job {job_id}")
            return

        # Create message with timestamp
        full_message = {
            "type": "job_update",
            "job_id": job_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": message,
        }

        logger.info(
            f"Sending job update to {len(self.job_connections[job_id])} connections for job {job_id}"
        )

        # Send to all connections for this job
        disconnected_connections = set()
        for websocket in self.job_connections[job_id]:
            try:
                await websocket.send_text(json.dumps(full_message))
                logger.debug(f"Sent message to WebSocket for job {job_id}")
            except Exception as e:
                logger.warning(f"Failed to send message to WebSocket: {e}")
                disconnected_connections.add(websocket)

        # Clean up disconnected connections
        for websocket in disconnected_connections:
            self.job_connections[job_id].discard(websocket)

        # Clean up empty job connection sets
        if not self.job_connections[job_id]:
            del self.job_connections[job_id]

    async def send_step_update(
        self, job_id: str, step_name: str, message: Dict[str, Any]
    ):
        """
        Send a step-specific update to all connections monitoring a job.

        Args:
            job_id: Job ID
            step_name: Step name
            message: Step update data
        """
        step_message = {
            "type": "step_update",
            "job_id": job_id,
            "step_name": step_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": message,
        }

        await self.send_job_update(job_id, step_message)

    async def send_log_update(self, job_id: str, log_message: Dict[str, Any]):
        """
        Send a log update to all connections monitoring a job.

        Args:
            job_id: Job ID
            log_message: Log message data
        """
        log_update = {
            "type": "log_update",
            "job_id": job_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": log_message,
        }

        await self.send_job_update(job_id, log_update)

    async def send_error_notification(
        self, job_id: str, error_message: str, error_details: Dict[str, Any] = None
    ):
        """
        Send an error notification to all connections monitoring a job.

        Args:
            job_id: Job ID
            error_message: Error message
            error_details: Additional error details
        """
        error_notification = {
            "type": "error_notification",
            "job_id": job_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "error_message": error_message,
            "error_details": error_details or {},
        }

        await self.send_job_update(job_id, error_notification)

    async def send_heartbeat(self, job_id: str):
        """
        Send a heartbeat message to keep connections alive.

        Args:
            job_id: Job ID
        """
        heartbeat = {
            "type": "heartbeat",
            "job_id": job_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        await self.send_job_update(job_id, heartbeat)

    def get_connection_count(self, job_id: str) -> int:
        """
        Get the number of active connections for a job.

        Args:
            job_id: Job ID

        Returns:
            Number of active connections
        """
        return len(self.job_connections.get(job_id, set()))

    def get_total_connections(self) -> int:
        """
        Get the total number of active connections across all jobs.

        Returns:
            Total number of active connections
        """
        return sum(
            len(connections) for connections in self.job_connections.values()
        )

    async def broadcast_system_message(self, message: str, message_type: str = "info"):
        """
        Broadcast a system message to all connected clients.

        Args:
            message: Message to broadcast
            message_type: Type of message (info, warning, error)
        """
        system_message = {
            "type": "system_message",
            "message_type": message_type,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Send to all connections across all jobs
        for job_id, connections in self.job_connections.items():
            disconnected_connections = set()
            for websocket in connections:
                try:
                    await websocket.send_text(json.dumps(system_message))
                except Exception as e:
                    logger.warning(f"Failed to send system message to WebSocket: {e}")
                    disconnected_connections.add(websocket)

            # Clean up disconnected connections
            for websocket in disconnected_connections:
                connections.discard(websocket)

        # Clean up empty job connection sets
        self.job_connections = {
            job_id: connections
            for job_id, connections in self.job_connections.items()
            if connections
        }

    async def broadcast_cancellation(self, job_id: str) -> int:
        """
        Broadcast a cancellation message to all connections monitoring a job.

        This is a specialized method for job cancellation that sends
        a standardized cancellation message to all connected clients.

        Args:
            job_id: Job ID that was cancelled

        Returns:
            Number of connections that received the cancellation message
        """
        cancellation_message = {
            "type": "workflow_cancelled",
            "job_id": job_id,
            "message": "Job has been cancelled by user",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "cancelled",
        }

        if job_id not in self.job_connections:
            logger.debug(f"No connections found for cancelled job {job_id}")
            return 0

        connections = self.job_connections[job_id].copy()
        if not connections:
            logger.debug(f"No active connections for cancelled job {job_id}")
            return 0

        message_str = json.dumps(cancellation_message)
        sent_count = 0

        for websocket in connections:
            try:
                await websocket.send_text(message_str)
                sent_count += 1
            except Exception as e:
                logger.warning(
                    f"Failed to send cancellation message to job {job_id}: {e}"
                )
                # Remove the failed connection
                self.job_connections[job_id].discard(websocket)

        logger.info(
            f"Broadcasted cancellation to {sent_count} connections for job {job_id}"
        )
        return sent_count


# Global connection manager instance
connection_manager = ConnectionManager()
