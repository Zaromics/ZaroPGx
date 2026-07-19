"""
Integration tests for WebSocket functionality.

This module contains tests for:
- WebSocket connection establishment
- Real-time message handling
- Connection management
- Error handling and reconnection
"""

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.websockets import WebSocket

from app.main import app
from app.services.websocket_manager import ConnectionManager


class TestWebSocketIntegration:
    """Integration tests for WebSocket functionality."""

    @pytest.fixture
    def client(self):
        """Create a test client."""
        return TestClient(app)

    @pytest.fixture
    def connection_manager(self):
        """Create a connection manager instance."""
        return ConnectionManager()

    @pytest.mark.asyncio
    async def test_websocket_connection_establishment(self, connection_manager):
        """Test WebSocket connection establishment."""
        mock_websocket = Mock()
        mock_websocket.accept = AsyncMock()

        workflow_id = str(uuid.uuid4())
        connection_id = await connection_manager.connect(mock_websocket, workflow_id)

        assert connection_id is not None
        assert workflow_id in connection_manager.workflow_connections
        assert mock_websocket in connection_manager.workflow_connections[workflow_id]
        mock_websocket.accept.assert_called_once()

    @pytest.mark.asyncio
    async def test_websocket_message_handling(self, connection_manager):
        """Test WebSocket message handling."""
        mock_websocket = Mock()
        mock_websocket.accept = AsyncMock()
        mock_websocket.send_text = AsyncMock()

        workflow_id = str(uuid.uuid4())
        connection_id = await connection_manager.connect(mock_websocket, workflow_id)

        # Send a workflow update
        message = {
            "type": "workflow_update",
            "data": {"status": "running", "progress_percentage": 50.0},
        }

        await connection_manager.send_workflow_update(workflow_id, message["data"])

        # Verify message was sent
        mock_websocket.send_text.assert_called_once()
        call_args = mock_websocket.send_text.call_args[0][0]
        sent_message = json.loads(call_args)

        assert sent_message["type"] == "workflow_update"
        assert sent_message["workflow_id"] == workflow_id
        assert sent_message["data"] == message["data"]
        assert "timestamp" in sent_message

    @pytest.mark.asyncio
    async def test_websocket_step_update(self, connection_manager):
        """Test WebSocket step update handling."""
        mock_websocket = Mock()
        mock_websocket.accept = AsyncMock()
        mock_websocket.send_text = AsyncMock()

        workflow_id = str(uuid.uuid4())
        connection_id = await connection_manager.connect(mock_websocket, workflow_id)

        # Send a step update
        step_message = {"step_name": "test_step", "status": "running", "progress": 25.0}

        await connection_manager.send_step_update(
            workflow_id, "test_step", step_message
        )

        # Verify message was sent
        mock_websocket.send_text.assert_called_once()
        call_args = mock_websocket.send_text.call_args[0][0]
        sent_message = json.loads(call_args)

        # send_step_update delegates to send_workflow_update, which wraps the
        # step message in a "workflow_update" envelope; the browser client
        # unwraps it at workflow-monitor.js:260-263.
        assert sent_message["type"] == "workflow_update"
        assert sent_message["workflow_id"] == workflow_id
        inner = sent_message["data"]
        assert inner["type"] == "step_update"
        assert inner["step_name"] == "test_step"
        assert inner["data"] == step_message

    @pytest.mark.asyncio
    async def test_websocket_log_update(self, connection_manager):
        """Test WebSocket log update handling."""
        mock_websocket = Mock()
        mock_websocket.accept = AsyncMock()
        mock_websocket.send_text = AsyncMock()

        workflow_id = str(uuid.uuid4())
        connection_id = await connection_manager.connect(mock_websocket, workflow_id)

        # Send a log update
        log_message = {
            "step_name": "test_step",
            "log_level": "info",
            "message": "Test log message",
        }

        await connection_manager.send_log_update(workflow_id, log_message)

        # Verify message was sent
        mock_websocket.send_text.assert_called_once()
        call_args = mock_websocket.send_text.call_args[0][0]
        sent_message = json.loads(call_args)

        assert sent_message["type"] == "workflow_update"
        assert sent_message["workflow_id"] == workflow_id
        inner = sent_message["data"]
        assert inner["type"] == "log_update"
        assert inner["data"] == log_message

    @pytest.mark.asyncio
    async def test_websocket_error_notification(self, connection_manager):
        """Test WebSocket error notification handling."""
        mock_websocket = Mock()
        mock_websocket.accept = AsyncMock()
        mock_websocket.send_text = AsyncMock()

        workflow_id = str(uuid.uuid4())
        connection_id = await connection_manager.connect(mock_websocket, workflow_id)

        # Send an error notification
        error_message = "Test error message"
        error_details = {"error_code": "TEST_ERROR"}

        await connection_manager.send_error_notification(
            workflow_id, error_message, error_details
        )

        # Verify message was sent
        mock_websocket.send_text.assert_called_once()
        call_args = mock_websocket.send_text.call_args[0][0]
        sent_message = json.loads(call_args)

        assert sent_message["type"] == "workflow_update"
        assert sent_message["workflow_id"] == workflow_id
        inner = sent_message["data"]
        assert inner["type"] == "error_notification"
        assert inner["error_message"] == error_message
        assert inner["error_details"] == error_details

    @pytest.mark.asyncio
    async def test_websocket_heartbeat(self, connection_manager):
        """Test WebSocket heartbeat handling."""
        mock_websocket = Mock()
        mock_websocket.accept = AsyncMock()
        mock_websocket.send_text = AsyncMock()

        workflow_id = str(uuid.uuid4())
        connection_id = await connection_manager.connect(mock_websocket, workflow_id)

        # Send a heartbeat
        await connection_manager.send_heartbeat(workflow_id)

        # Verify message was sent
        mock_websocket.send_text.assert_called_once()
        call_args = mock_websocket.send_text.call_args[0][0]
        sent_message = json.loads(call_args)

        assert sent_message["type"] == "workflow_update"
        assert sent_message["workflow_id"] == workflow_id
        assert sent_message["data"]["type"] == "heartbeat"
        assert "timestamp" in sent_message

    @pytest.mark.asyncio
    async def test_websocket_disconnect(self, connection_manager):
        """Test WebSocket disconnection handling."""
        mock_websocket = Mock()
        mock_websocket.accept = AsyncMock()

        workflow_id = str(uuid.uuid4())
        connection_id = await connection_manager.connect(mock_websocket, workflow_id)

        # Verify connection exists
        assert workflow_id in connection_manager.workflow_connections
        assert connection_id in connection_manager.connection_workflows

        # Disconnect
        connection_manager.disconnect(mock_websocket, connection_id)

        # Verify connection is removed
        assert workflow_id not in connection_manager.workflow_connections
        assert connection_id not in connection_manager.connection_workflows

    @pytest.mark.asyncio
    async def test_websocket_multiple_connections(self, connection_manager):
        """Test multiple WebSocket connections for the same workflow."""
        mock_websocket1 = Mock()
        mock_websocket2 = Mock()
        mock_websocket1.accept = AsyncMock()
        mock_websocket2.accept = AsyncMock()
        mock_websocket1.send_text = AsyncMock()
        mock_websocket2.send_text = AsyncMock()

        workflow_id = str(uuid.uuid4())

        # Connect two websockets to the same workflow
        connection_id1 = await connection_manager.connect(mock_websocket1, workflow_id)
        connection_id2 = await connection_manager.connect(mock_websocket2, workflow_id)

        # Verify both connections exist
        assert connection_id1 != connection_id2
        assert len(connection_manager.workflow_connections[workflow_id]) == 2

        # Send a message to all connections
        message = {"type": "test", "data": "test_data"}
        await connection_manager.send_workflow_update(workflow_id, message)

        # Verify both websockets received the message
        assert mock_websocket1.send_text.call_count == 1
        assert mock_websocket2.send_text.call_count == 1

    @pytest.mark.asyncio
    async def test_websocket_connection_failure_handling(self, connection_manager):
        """Test WebSocket connection failure handling."""
        mock_websocket = Mock()
        mock_websocket.accept = AsyncMock(side_effect=Exception("Connection failed"))

        workflow_id = str(uuid.uuid4())

        # Attempt to connect (should handle the exception)
        with pytest.raises(Exception):
            await connection_manager.connect(mock_websocket, workflow_id)

    @pytest.mark.asyncio
    async def test_websocket_send_failure_handling(self, connection_manager):
        """Test WebSocket send failure handling."""
        mock_websocket = Mock()
        mock_websocket.accept = AsyncMock()
        mock_websocket.send_text = AsyncMock(side_effect=Exception("Send failed"))

        workflow_id = str(uuid.uuid4())
        connection_id = await connection_manager.connect(mock_websocket, workflow_id)

        # Send a message (should handle the exception gracefully)
        message = {"type": "test", "data": "test_data"}
        await connection_manager.send_workflow_update(workflow_id, message)

        # Verify the connection is cleaned up after send failure
        assert workflow_id not in connection_manager.workflow_connections

    def test_connection_count_tracking(self, connection_manager):
        """Test connection count tracking."""
        mock_websocket1 = Mock()
        mock_websocket2 = Mock()
        mock_websocket3 = Mock()
        mock_websocket1.accept = AsyncMock()
        mock_websocket2.accept = AsyncMock()
        mock_websocket3.accept = AsyncMock()

        workflow_id1 = str(uuid.uuid4())
        workflow_id2 = str(uuid.uuid4())

        # Connect websockets
        asyncio.run(connection_manager.connect(mock_websocket1, workflow_id1))
        asyncio.run(connection_manager.connect(mock_websocket2, workflow_id1))
        asyncio.run(connection_manager.connect(mock_websocket3, workflow_id2))

        # Test connection counts
        assert connection_manager.get_connection_count(workflow_id1) == 2
        assert connection_manager.get_connection_count(workflow_id2) == 1
        assert connection_manager.get_connection_count("non-existent") == 0
        assert connection_manager.get_total_connections() == 3

    @pytest.mark.asyncio
    async def test_system_message_broadcast(self, connection_manager):
        """Test system message broadcast to all connections."""
        mock_websocket1 = Mock()
        mock_websocket2 = Mock()
        mock_websocket1.accept = AsyncMock()
        mock_websocket2.accept = AsyncMock()
        mock_websocket1.send_text = AsyncMock()
        mock_websocket2.send_text = AsyncMock()

        workflow_id1 = str(uuid.uuid4())
        workflow_id2 = str(uuid.uuid4())

        # Connect websockets to different workflows
        await connection_manager.connect(mock_websocket1, workflow_id1)
        await connection_manager.connect(mock_websocket2, workflow_id2)

        # Broadcast system message
        await connection_manager.broadcast_system_message(
            "System maintenance in 5 minutes", "warning"
        )

        # Verify both websockets received the message
        assert mock_websocket1.send_text.call_count == 1
        assert mock_websocket2.send_text.call_count == 1

        # Verify message content
        call_args1 = mock_websocket1.send_text.call_args[0][0]
        sent_message1 = json.loads(call_args1)
        assert sent_message1["type"] == "system_message"
        assert sent_message1["message"] == "System maintenance in 5 minutes"
        assert sent_message1["message_type"] == "warning"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
