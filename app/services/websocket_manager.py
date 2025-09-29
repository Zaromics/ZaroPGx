"""
WebSocket Manager for real-time workflow updates.

This module provides WebSocket connection management for real-time
workflow progress updates and notifications.
"""

from typing import Dict, Set, Any, Optional
from fastapi import WebSocket, WebSocketDisconnect
import json
import logging
import asyncio
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    Manages WebSocket connections for workflow monitoring.
    
    This class handles:
    - Connection registration and cleanup
    - Message broadcasting to specific workflows
    - Connection health monitoring
    - Message queuing for disconnected clients
    """
    
    def __init__(self):
        # Map workflow_id -> set of WebSocket connections
        self.workflow_connections: Dict[str, Set[WebSocket]] = {}
        # Map connection_id -> workflow_id for cleanup
        self.connection_workflows: Dict[str, str] = {}
        # Message queue for disconnected clients (optional feature)
        self.message_queues: Dict[str, list] = {}
    
    async def connect(self, websocket: WebSocket, workflow_id: str) -> str:
        """
        Accept a WebSocket connection for a specific workflow.
        
        Args:
            websocket: WebSocket connection
            workflow_id: Workflow ID to monitor
            
        Returns:
            Connection ID for tracking
        """
        await websocket.accept()
        
        # Generate connection ID
        connection_id = f"{workflow_id}_{datetime.now(timezone.utc).timestamp()}"
        
        # Register connection
        if workflow_id not in self.workflow_connections:
            self.workflow_connections[workflow_id] = set()
        
        self.workflow_connections[workflow_id].add(websocket)
        self.connection_workflows[connection_id] = workflow_id
        
        logger.info(f"WebSocket connected for workflow {workflow_id} (connection: {connection_id})")
        return connection_id
    
    def disconnect(self, websocket: WebSocket, connection_id: str):
        """
        Remove a WebSocket connection.
        
        Args:
            websocket: WebSocket connection to remove
            connection_id: Connection ID for cleanup
        """
        workflow_id = self.connection_workflows.get(connection_id)
        if workflow_id and workflow_id in self.workflow_connections:
            self.workflow_connections[workflow_id].discard(websocket)
            
            # Clean up empty workflow connection sets
            if not self.workflow_connections[workflow_id]:
                del self.workflow_connections[workflow_id]
        
        # Remove connection mapping
        if connection_id in self.connection_workflows:
            del self.connection_workflows[connection_id]
        
        # Clean up message queue
        if connection_id in self.message_queues:
            del self.message_queues[connection_id]
        
        logger.info(f"WebSocket disconnected for workflow {workflow_id} (connection: {connection_id})")
    
    async def send_workflow_update(self, workflow_id: str, message: Dict[str, Any]):
        """
        Send an update to all connections monitoring a specific workflow.
        
        Args:
            workflow_id: Workflow ID to send update for
            message: Message data to send
        """
        if workflow_id not in self.workflow_connections:
            logger.warning(f"No WebSocket connections found for workflow {workflow_id}")
            return
        
        # Create message with timestamp
        full_message = {
            "type": "workflow_update",
            "workflow_id": workflow_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": message
        }
        
        logger.info(f"Sending workflow update to {len(self.workflow_connections[workflow_id])} connections for workflow {workflow_id}")
        
        # Send to all connections for this workflow
        disconnected_connections = set()
        for websocket in self.workflow_connections[workflow_id]:
            try:
                await websocket.send_text(json.dumps(full_message))
                logger.debug(f"Sent message to WebSocket for workflow {workflow_id}")
            except Exception as e:
                logger.warning(f"Failed to send message to WebSocket: {e}")
                disconnected_connections.add(websocket)
        
        # Clean up disconnected connections
        for websocket in disconnected_connections:
            self.workflow_connections[workflow_id].discard(websocket)
        
        # Clean up empty workflow connection sets
        if not self.workflow_connections[workflow_id]:
            del self.workflow_connections[workflow_id]
    
    async def send_step_update(self, workflow_id: str, step_name: str, message: Dict[str, Any]):
        """
        Send a step-specific update to all connections monitoring a workflow.
        
        Args:
            workflow_id: Workflow ID
            step_name: Step name
            message: Step update data
        """
        step_message = {
            "type": "step_update",
            "workflow_id": workflow_id,
            "step_name": step_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": message
        }
        
        await self.send_workflow_update(workflow_id, step_message)
    
    async def send_log_update(self, workflow_id: str, log_message: Dict[str, Any]):
        """
        Send a log update to all connections monitoring a workflow.
        
        Args:
            workflow_id: Workflow ID
            log_message: Log message data
        """
        log_update = {
            "type": "log_update",
            "workflow_id": workflow_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": log_message
        }
        
        await self.send_workflow_update(workflow_id, log_update)
    
    async def send_error_notification(self, workflow_id: str, error_message: str, error_details: Dict[str, Any] = None):
        """
        Send an error notification to all connections monitoring a workflow.
        
        Args:
            workflow_id: Workflow ID
            error_message: Error message
            error_details: Additional error details
        """
        error_notification = {
            "type": "error_notification",
            "workflow_id": workflow_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "error_message": error_message,
            "error_details": error_details or {}
        }
        
        await self.send_workflow_update(workflow_id, error_notification)
    
    async def send_heartbeat(self, workflow_id: str):
        """
        Send a heartbeat message to keep connections alive.
        
        Args:
            workflow_id: Workflow ID
        """
        heartbeat = {
            "type": "heartbeat",
            "workflow_id": workflow_id,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        await self.send_workflow_update(workflow_id, heartbeat)
    
    def get_connection_count(self, workflow_id: str) -> int:
        """
        Get the number of active connections for a workflow.
        
        Args:
            workflow_id: Workflow ID
            
        Returns:
            Number of active connections
        """
        return len(self.workflow_connections.get(workflow_id, set()))
    
    def get_total_connections(self) -> int:
        """
        Get the total number of active connections across all workflows.
        
        Returns:
            Total number of active connections
        """
        return sum(len(connections) for connections in self.workflow_connections.values())
    
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
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        # Send to all connections across all workflows
        for workflow_id, connections in self.workflow_connections.items():
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
        
        # Clean up empty workflow connection sets
        self.workflow_connections = {
            workflow_id: connections 
            for workflow_id, connections in self.workflow_connections.items() 
            if connections
        }
    
    async def broadcast_cancellation(self, workflow_id: str) -> int:
        """
        Broadcast a cancellation message to all connections monitoring a workflow.
        
        This is a specialized method for workflow cancellation that sends
        a standardized cancellation message to all connected clients.
        
        Args:
            workflow_id: Workflow ID that was cancelled
            
        Returns:
            Number of connections that received the cancellation message
        """
        cancellation_message = {
            "type": "workflow_cancelled",
            "workflow_id": workflow_id,
            "message": "Workflow has been cancelled by user",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "cancelled"
        }
        
        if workflow_id not in self.workflow_connections:
            logger.debug(f"No connections found for cancelled workflow {workflow_id}")
            return 0
        
        connections = self.workflow_connections[workflow_id].copy()
        if not connections:
            logger.debug(f"No active connections for cancelled workflow {workflow_id}")
            return 0
        
        message_str = json.dumps(cancellation_message)
        sent_count = 0
        
        for websocket in connections:
            try:
                await websocket.send_text(message_str)
                sent_count += 1
            except Exception as e:
                logger.warning(f"Failed to send cancellation message to workflow {workflow_id}: {e}")
                # Remove the failed connection
                self.workflow_connections[workflow_id].discard(websocket)
        
        logger.info(f"Broadcasted cancellation to {sent_count} connections for workflow {workflow_id}")
        return sent_count


# Global connection manager instance
connection_manager = ConnectionManager()
