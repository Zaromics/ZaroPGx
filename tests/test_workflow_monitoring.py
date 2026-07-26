"""
Test suite for workflow monitoring system.

This module contains comprehensive tests for:
- WorkflowService business logic
- WebSocketManager connection handling
- WorkflowClient API communication
- Database models and relationships
- API endpoints and validation
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock

import pytest

# Import the modules to test
from app.api.db import Workflow, WorkflowLog, WorkflowStep
from app.api.models import (
    LogLevel,
    StepStatus,
    WorkflowCreate,
    WorkflowLogCreate,
    WorkflowStatus,
    WorkflowStepCreate,
    WorkflowStepUpdate,
    WorkflowUpdate,
)
from app.services.workflow_service import WorkflowService

# The db_session / client / workflow_service / connection_manager fixtures and
# the get_db override all live in tests/conftest.py. They used to be defined
# here at module scope, which installed a dependency override on the shared app
# singleton at import time that was never torn down -- it leaked into every
# other test module -- and created tables only inside db_session, so the API
# and end-to-end classes ran against a schema that did not exist.


class TestWorkflowService:
    """Test cases for WorkflowService."""

    def test_create_workflow_success(self, workflow_service):
        """Test successful workflow creation."""
        workflow_data = WorkflowCreate(
            name="Test Workflow",
            description="A test workflow",
            total_steps=3,
            created_by="test_user",
        )

        workflow = workflow_service.create_workflow(workflow_data)

        assert workflow is not None
        assert workflow.name == "Test Workflow"
        assert workflow.description == "A test workflow"
        assert workflow.status == "pending"
        assert workflow.total_steps == 3
        assert workflow.completed_steps == 0
        assert workflow.created_by == "test_user"
        assert workflow.id is not None

    def test_create_workflow_invalid_name(self, workflow_service):
        """Test workflow creation with invalid name."""
        workflow_data = WorkflowCreate(
            name="", description="A test workflow"  # Empty name should fail
        )

        with pytest.raises(ValueError, match="Workflow name is required"):
            workflow_service.create_workflow(workflow_data)

    def test_get_workflow_success(self, workflow_service):
        """Test successful workflow retrieval."""
        # Create a workflow first
        workflow_data = WorkflowCreate(
            name="Test Workflow", description="A test workflow"
        )
        created_workflow = workflow_service.create_workflow(workflow_data)

        # Retrieve it
        retrieved_workflow = workflow_service.get_workflow(created_workflow.id)

        assert retrieved_workflow is not None
        assert retrieved_workflow.id == created_workflow.id
        assert retrieved_workflow.name == "Test Workflow"

    def test_get_workflow_not_found(self, workflow_service):
        """Test workflow retrieval when workflow doesn't exist."""
        non_existent_id = uuid.uuid4()
        workflow = workflow_service.get_workflow(non_existent_id)

        assert workflow is None

    def test_update_workflow_success(self, workflow_service):
        """Test successful workflow update."""
        # Create a workflow first
        workflow_data = WorkflowCreate(
            name="Test Workflow", description="A test workflow"
        )
        created_workflow = workflow_service.create_workflow(workflow_data)

        # Update it
        update_data = WorkflowUpdate(
            name="Updated Workflow", status=WorkflowStatus.RUNNING
        )
        updated_workflow = workflow_service.update_workflow(
            created_workflow.id, update_data
        )

        assert updated_workflow is not None
        assert updated_workflow.name == "Updated Workflow"
        assert updated_workflow.status == "running"
        assert updated_workflow.started_at is not None

    def test_add_workflow_step_success(self, workflow_service):
        """Test successful workflow step addition."""
        # Create a workflow first
        workflow_data = WorkflowCreate(
            name="Test Workflow", description="A test workflow"
        )
        created_workflow = workflow_service.create_workflow(workflow_data)

        # Add a step
        step_data = WorkflowStepCreate(
            step_name="test_step", step_order=1, container_name="test_container"
        )
        step = workflow_service.add_workflow_step(created_workflow.id, step_data)

        assert step is not None
        assert step.step_name == "test_step"
        assert step.step_order == 1
        assert step.container_name == "test_container"
        assert step.workflow_id == created_workflow.id

    def test_update_workflow_step_success(self, workflow_service):
        """Test successful workflow step update."""
        # Create a workflow and step first
        workflow_data = WorkflowCreate(name="Test Workflow")
        created_workflow = workflow_service.create_workflow(workflow_data)

        step_data = WorkflowStepCreate(step_name="test_step", step_order=1)
        created_step = workflow_service.add_workflow_step(
            created_workflow.id, step_data
        )

        # Update the step
        update_data = WorkflowStepUpdate(status=StepStatus.RUNNING)
        updated_step = workflow_service.update_workflow_step(
            created_workflow.id, "test_step", update_data
        )

        assert updated_step is not None
        assert updated_step.status == "running"
        assert updated_step.started_at is not None

    @pytest.mark.xfail(
        reason="get_workflow_progress() derives progress_percentage from "
        "WorkflowProgressCalculator's fixed pipeline stage vocabulary "
        "(analysis/gatk/pypgx/pharmcat/report), not from completed_steps vs "
        "total_steps, so synthetic step names score 0%. Unifying the two "
        "progress models is a Wave 4 backlog item; see dev-notes/BACKLOG.md.",
        strict=True,
    )
    def test_get_workflow_progress_success(self, workflow_service):
        """Test successful workflow progress retrieval."""
        # Create a workflow with steps
        workflow_data = WorkflowCreate(name="Test Workflow", total_steps=2)
        created_workflow = workflow_service.create_workflow(workflow_data)

        # Add steps
        step1_data = WorkflowStepCreate(step_name="step1", step_order=1)
        step2_data = WorkflowStepCreate(step_name="step2", step_order=2)

        workflow_service.add_workflow_step(created_workflow.id, step1_data)
        workflow_service.add_workflow_step(created_workflow.id, step2_data)

        # Complete one step
        update_data = WorkflowStepUpdate(status=StepStatus.COMPLETED)
        workflow_service.update_workflow_step(created_workflow.id, "step1", update_data)

        # Get progress
        progress = workflow_service.get_workflow_progress(created_workflow.id)

        assert progress is not None
        assert progress.total_steps == 2
        assert progress.completed_steps == 1
        assert progress.progress_percentage == 50.0

    def test_log_workflow_event_success(self, workflow_service):
        """Test successful workflow event logging."""
        # Create a workflow first
        workflow_data = WorkflowCreate(name="Test Workflow")
        created_workflow = workflow_service.create_workflow(workflow_data)

        # Log an event
        log_data = WorkflowLogCreate(
            step_name="test_step",
            log_level=LogLevel.INFO,
            message="Test log message",
            metadata={"key": "value"},
        )
        log_entry = workflow_service.log_workflow_event(created_workflow.id, log_data)

        assert log_entry is not None
        assert log_entry.workflow_id == created_workflow.id
        assert log_entry.step_name == "test_step"
        assert log_entry.log_level == "info"
        assert log_entry.message == "Test log message"
        assert log_entry.log_metadata == {"key": "value"}


class TestConnectionManager:
    """Test cases for ConnectionManager."""

    def test_connect_success(self, connection_manager):
        """Test successful WebSocket connection."""
        mock_websocket = AsyncMock()

        workflow_id = str(uuid.uuid4())
        connection_id = asyncio.run(
            connection_manager.connect(mock_websocket, workflow_id)
        )

        assert connection_id is not None
        assert workflow_id in connection_manager.workflow_connections
        assert mock_websocket in connection_manager.workflow_connections[workflow_id]
        mock_websocket.accept.assert_called_once()

    def test_disconnect_success(self, connection_manager):
        """Test successful WebSocket disconnection."""
        mock_websocket = AsyncMock()
        workflow_id = str(uuid.uuid4())

        # Connect first
        connection_id = asyncio.run(
            connection_manager.connect(mock_websocket, workflow_id)
        )

        # Disconnect
        connection_manager.disconnect(mock_websocket, connection_id)

        assert workflow_id not in connection_manager.workflow_connections
        assert connection_id not in connection_manager.connection_workflows

    @pytest.mark.asyncio
    async def test_send_workflow_update_success(self, connection_manager):
        """Test successful workflow update sending."""
        mock_websocket = AsyncMock()

        workflow_id = str(uuid.uuid4())
        connection_id = await connection_manager.connect(mock_websocket, workflow_id)

        message = {"type": "test", "data": "test_data"}
        await connection_manager.send_workflow_update(workflow_id, message)

        mock_websocket.send_text.assert_called_once()
        call_args = mock_websocket.send_text.call_args[0][0]
        sent_message = json.loads(call_args)
        assert sent_message["type"] == "workflow_update"
        assert sent_message["workflow_id"] == workflow_id
        assert sent_message["data"] == message

    def test_get_connection_count(self, connection_manager):
        """Test connection count retrieval."""
        mock_websocket1 = AsyncMock()
        mock_websocket2 = AsyncMock()
        workflow_id = str(uuid.uuid4())

        # Connect two websockets
        asyncio.run(connection_manager.connect(mock_websocket1, workflow_id))
        asyncio.run(connection_manager.connect(mock_websocket2, workflow_id))

        count = connection_manager.get_connection_count(workflow_id)
        assert count == 2

        # Test non-existent workflow
        count = connection_manager.get_connection_count("non-existent")
        assert count == 0


class TestWorkflowAPI:
    """Test cases for workflow API endpoints."""

    def test_create_workflow_endpoint(self, client):
        """Test POST /api/v1/workflows endpoint."""
        workflow_data = {
            "name": "Test Workflow",
            "description": "A test workflow",
            "total_steps": 3,
            "created_by": "test_user",
        }

        response = client.post("/api/v1/workflows", json=workflow_data)

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Test Workflow"
        assert data["description"] == "A test workflow"
        assert data["status"] == "pending"
        assert data["total_steps"] == 3
        assert data["created_by"] == "test_user"
        assert "id" in data

    def test_get_workflow_endpoint(self, client):
        """Test GET /api/v1/workflows/{workflow_id} endpoint."""
        # Create a workflow first
        workflow_data = {"name": "Test Workflow", "description": "A test workflow"}
        create_response = client.post("/api/v1/workflows", json=workflow_data)
        workflow_id = create_response.json()["id"]

        # Get the workflow
        response = client.get(f"/api/v1/workflows/{workflow_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == workflow_id
        assert data["name"] == "Test Workflow"

    def test_get_workflow_not_found(self, client):
        """Test GET /api/v1/workflows/{workflow_id} with non-existent workflow."""
        non_existent_id = str(uuid.uuid4())
        response = client.get(f"/api/v1/workflows/{non_existent_id}")

        assert response.status_code == 404
        assert "Workflow not found" in response.json()["detail"]

    def test_update_workflow_endpoint(self, client):
        """Test PUT /api/v1/workflows/{workflow_id} endpoint."""
        # Create a workflow first
        workflow_data = {"name": "Test Workflow"}
        create_response = client.post("/api/v1/workflows", json=workflow_data)
        workflow_id = create_response.json()["id"]

        # Update the workflow
        update_data = {"name": "Updated Workflow", "status": "running"}
        response = client.put(f"/api/v1/workflows/{workflow_id}", json=update_data)

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Updated Workflow"
        assert data["status"] == "running"

    def test_add_workflow_step_endpoint(self, client):
        """Test POST /api/v1/workflows/{workflow_id}/steps endpoint."""
        # Create a workflow first
        workflow_data = {"name": "Test Workflow"}
        create_response = client.post("/api/v1/workflows", json=workflow_data)
        workflow_id = create_response.json()["id"]

        # Add a step
        step_data = {
            "step_name": "test_step",
            "step_order": 1,
            "container_name": "test_container",
        }
        response = client.post(f"/api/v1/workflows/{workflow_id}/steps", json=step_data)

        assert response.status_code == 201
        data = response.json()
        assert data["step_name"] == "test_step"
        assert data["step_order"] == 1
        assert data["container_name"] == "test_container"
        assert data["workflow_id"] == workflow_id

    def test_update_workflow_step_endpoint(self, client):
        """Test PUT /api/v1/workflows/{workflow_id}/steps/{step_name} endpoint."""
        # Create a workflow and step first
        workflow_data = {"name": "Test Workflow"}
        create_response = client.post("/api/v1/workflows", json=workflow_data)
        workflow_id = create_response.json()["id"]

        step_data = {"step_name": "test_step", "step_order": 1}
        client.post(f"/api/v1/workflows/{workflow_id}/steps", json=step_data)

        # Update the step
        update_data = {"status": "running"}
        response = client.put(
            f"/api/v1/workflows/{workflow_id}/steps/test_step", json=update_data
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "running"

    def test_get_workflow_progress_endpoint(self, client):
        """Test GET /api/v1/workflows/{workflow_id}/progress endpoint."""
        # Create a workflow first
        workflow_data = {"name": "Test Workflow", "total_steps": 2}
        create_response = client.post("/api/v1/workflows", json=workflow_data)
        workflow_id = create_response.json()["id"]

        # Get progress
        response = client.get(f"/api/v1/workflows/{workflow_id}/progress")

        assert response.status_code == 200
        data = response.json()
        assert data["workflow_id"] == workflow_id
        assert data["total_steps"] == 2
        assert data["completed_steps"] == 0
        assert data["progress_percentage"] == 0.0

    def test_log_workflow_event_endpoint(self, client):
        """Test POST /api/v1/workflows/{workflow_id}/logs endpoint."""
        # Create a workflow first
        workflow_data = {"name": "Test Workflow"}
        create_response = client.post("/api/v1/workflows", json=workflow_data)
        workflow_id = create_response.json()["id"]

        # Log an event
        log_data = {
            "step_name": "test_step",
            "log_level": "info",
            "message": "Test log message",
            "metadata": {"key": "value"},
        }
        response = client.post(f"/api/v1/workflows/{workflow_id}/logs", json=log_data)

        assert response.status_code == 201
        data = response.json()
        assert data["workflow_id"] == workflow_id
        assert data["step_name"] == "test_step"
        assert data["log_level"] == "info"
        assert data["message"] == "Test log message"
        assert data["metadata"] == {"key": "value"}

    def test_get_workflow_logs_endpoint(self, client):
        """Test GET /api/v1/workflows/{workflow_id}/logs endpoint."""
        # Create a workflow first
        workflow_data = {"name": "Test Workflow"}
        create_response = client.post("/api/v1/workflows", json=workflow_data)
        workflow_id = create_response.json()["id"]

        # Add some logs
        log_data = {
            "step_name": "test_step",
            "log_level": "info",
            "message": "Test log message",
        }
        client.post(f"/api/v1/workflows/{workflow_id}/logs", json=log_data)

        # Get logs
        response = client.get(f"/api/v1/workflows/{workflow_id}/logs")

        assert response.status_code == 200
        data = response.json()
        # create_workflow() also emits a "created successfully" log entry, so
        # assert on content rather than on the total row count.
        assert all(entry["workflow_id"] == workflow_id for entry in data)
        assert "Test log message" in [entry["message"] for entry in data]


class TestWorkflowModels:
    """Test cases for workflow models and validation."""

    def test_workflow_create_model(self):
        """Test WorkflowCreate model validation."""
        # Valid data
        workflow_data = WorkflowCreate(
            name="Test Workflow",
            description="A test workflow",
            total_steps=3,
            created_by="test_user",
        )
        assert workflow_data.name == "Test Workflow"
        assert workflow_data.description == "A test workflow"
        assert workflow_data.total_steps == 3
        assert workflow_data.created_by == "test_user"

    def test_workflow_step_create_model(self):
        """Test WorkflowStepCreate model validation."""
        step_data = WorkflowStepCreate(
            step_name="test_step",
            step_order=1,
            container_name="test_container",
            output_data={"key": "value"},
        )
        assert step_data.step_name == "test_step"
        assert step_data.step_order == 1
        assert step_data.container_name == "test_container"
        assert step_data.output_data == {"key": "value"}

    def test_workflow_log_create_model(self):
        """Test WorkflowLogCreate model validation."""
        log_data = WorkflowLogCreate(
            step_name="test_step",
            log_level=LogLevel.INFO,
            message="Test log message",
            metadata={"key": "value"},
        )
        assert log_data.step_name == "test_step"
        assert log_data.log_level == LogLevel.INFO
        assert log_data.message == "Test log message"
        assert log_data.metadata == {"key": "value"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
