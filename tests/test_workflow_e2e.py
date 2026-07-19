"""
End-to-end tests for the complete workflow monitoring system.

This module contains comprehensive tests that demonstrate:
- Complete workflow lifecycle from creation to completion
- Real-time progress tracking and updates
- WebSocket communication and UI updates
- Error handling and recovery
- Integration between all components
"""

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.db import get_db
from app.main import app
from app.services.websocket_manager import ConnectionManager
from app.services.workflow_service import WorkflowService


class TestWorkflowEndToEnd:
    """End-to-end tests for the complete workflow monitoring system."""

    @pytest.fixture
    def client(self):
        """Create a test client."""
        return TestClient(app)

    @pytest.fixture
    def connection_manager(self):
        """Create a connection manager instance."""
        return ConnectionManager()

    @pytest.mark.xfail(
        reason="get_workflow_progress() derives progress_percentage from "
        "WorkflowProgressCalculator's fixed pipeline stage vocabulary "
        "(analysis/gatk/pypgx/pharmcat/report), not from completed_steps vs "
        "total_steps, so synthetic step names score 0%. Unifying the two "
        "progress models is a Wave 4 backlog item; see dev-notes/BACKLOG.md.",
        strict=True,
    )
    def test_complete_workflow_lifecycle(self, client):
        """Test complete workflow lifecycle from creation to completion."""
        # Step 1: Create a workflow
        workflow_data = {
            "name": "End-to-End Test Workflow",
            "description": "A comprehensive test workflow",
            "total_steps": 3,
            "created_by": "test_user",
        }

        create_response = client.post("/api/v1/workflows", json=workflow_data)
        assert create_response.status_code == 201
        workflow_id = create_response.json()["id"]

        # Step 2: Add workflow steps
        steps = [
            {
                "step_name": "data_preparation",
                "step_order": 1,
                "container_name": "prep_container",
            },
            {
                "step_name": "analysis",
                "step_order": 2,
                "container_name": "analysis_container",
            },
            {
                "step_name": "reporting",
                "step_order": 3,
                "container_name": "report_container",
            },
        ]

        for step_data in steps:
            step_response = client.post(
                f"/api/v1/workflows/{workflow_id}/steps", json=step_data
            )
            assert step_response.status_code == 201

        # Step 3: Start the workflow
        update_response = client.put(
            f"/api/v1/workflows/{workflow_id}", json={"status": "running"}
        )
        assert update_response.status_code == 200
        assert update_response.json()["status"] == "running"

        # Step 4: Update step statuses to simulate progress
        step_updates = [
            {"step_name": "data_preparation", "status": "running"},
            {
                "step_name": "data_preparation",
                "status": "completed",
                "output_data": {"files_processed": 10},
            },
            {"step_name": "analysis", "status": "running"},
            {
                "step_name": "analysis",
                "status": "completed",
                "output_data": {"variants_found": 150},
            },
            {"step_name": "reporting", "status": "running"},
            {
                "step_name": "reporting",
                "status": "completed",
                "output_data": {"report_generated": True},
            },
        ]

        for update_data in step_updates:
            # step_name identifies the step in the URL; WorkflowStepUpdate is
            # extra="forbid" and has no such field, so sending it in the body too
            # is a 422.
            body = {k: v for k, v in update_data.items() if k != "step_name"}
            step_response = client.put(
                f"/api/v1/workflows/{workflow_id}/steps/{update_data['step_name']}",
                json=body,
            )
            assert step_response.status_code == 200

        # Step 5: Complete the workflow
        final_update = client.put(
            f"/api/v1/workflows/{workflow_id}", json={"status": "completed"}
        )
        assert final_update.status_code == 200
        assert final_update.json()["status"] == "completed"

        # Step 6: Verify final state
        final_response = client.get(f"/api/v1/workflows/{workflow_id}")
        assert final_response.status_code == 200
        final_data = final_response.json()
        assert final_data["status"] == "completed"
        assert final_data["completed_steps"] == 3

        # Step 7: Verify progress
        progress_response = client.get(f"/api/v1/workflows/{workflow_id}/progress")
        assert progress_response.status_code == 200
        progress_data = progress_response.json()
        assert progress_data["progress_percentage"] == 100.0
        assert progress_data["status"] == "completed"

    def test_workflow_with_logging(self, client):
        """Test workflow with comprehensive logging."""
        # Create workflow
        workflow_data = {"name": "Logging Test Workflow", "total_steps": 2}
        create_response = client.post("/api/v1/workflows", json=workflow_data)
        workflow_id = create_response.json()["id"]

        # Add steps
        steps = [
            {"step_name": "step1", "step_order": 1},
            {"step_name": "step2", "step_order": 2},
        ]
        for step_data in steps:
            client.post(f"/api/v1/workflows/{workflow_id}/steps", json=step_data)

        # Add various log entries
        log_entries = [
            {"step_name": "step1", "log_level": "info", "message": "Starting step 1"},
            {
                "step_name": "step1",
                "log_level": "debug",
                "message": "Processing data",
                "metadata": {"count": 100},
            },
            {
                "step_name": "step1",
                "log_level": "warn",
                "message": "Warning: Low memory",
            },
            {"step_name": "step1", "log_level": "info", "message": "Step 1 completed"},
            {"step_name": "step2", "log_level": "info", "message": "Starting step 2"},
            {
                "step_name": "step2",
                "log_level": "error",
                "message": "Error occurred",
                "metadata": {"error_code": "E001"},
            },
            {"step_name": "step2", "log_level": "info", "message": "Step 2 completed"},
        ]

        for log_data in log_entries:
            log_response = client.post(
                f"/api/v1/workflows/{workflow_id}/logs", json=log_data
            )
            assert log_response.status_code == 201

        # Retrieve logs
        logs_response = client.get(f"/api/v1/workflows/{workflow_id}/logs")
        assert logs_response.status_code == 200
        logs_data = logs_response.json()
        # The service writes its own log entries (workflow creation, step
        # transitions) alongside the 7 posted here, so assert on content rather
        # than on a total that shifts whenever service-side logging changes.
        assert len(logs_data) >= 7
        posted = [entry["message"] for entry in logs_data]
        for expected in log_entries:
            assert expected["message"] in posted

        # Verify log content
        assert logs_data[0]["message"] == "Step 2 completed"  # Most recent first
        assert logs_data[1]["message"] == "Error occurred"
        assert logs_data[1]["log_level"] == "error"
        assert logs_data[1]["metadata"]["error_code"] == "E001"

    def test_workflow_error_handling(self, client):
        """Test workflow error handling and recovery."""
        # Create workflow
        workflow_data = {"name": "Error Test Workflow", "total_steps": 2}
        create_response = client.post("/api/v1/workflows", json=workflow_data)
        workflow_id = create_response.json()["id"]

        # Add steps
        steps = [
            {"step_name": "step1", "step_order": 1},
            {"step_name": "step2", "step_order": 2},
        ]
        for step_data in steps:
            client.post(f"/api/v1/workflows/{workflow_id}/steps", json=step_data)

        # Start workflow
        client.put(f"/api/v1/workflows/{workflow_id}", json={"status": "running"})

        # Complete first step
        client.put(
            f"/api/v1/workflows/{workflow_id}/steps/step1", json={"status": "completed"}
        )

        # Fail second step
        error_details = {"error_code": "E001", "error_message": "Processing failed"}
        fail_response = client.put(
            f"/api/v1/workflows/{workflow_id}/steps/step2",
            json={"status": "failed", "error_details": error_details},
        )
        assert fail_response.status_code == 200

        # Mark workflow as failed
        client.put(f"/api/v1/workflows/{workflow_id}", json={"status": "failed"})

        # Verify final state
        final_response = client.get(f"/api/v1/workflows/{workflow_id}")
        assert final_response.status_code == 200
        final_data = final_response.json()
        assert final_data["status"] == "failed"

        # Verify progress shows failure
        progress_response = client.get(f"/api/v1/workflows/{workflow_id}/progress")
        assert progress_response.status_code == 200
        progress_data = progress_response.json()
        assert progress_data["status"] == "failed"

    @pytest.mark.asyncio
    async def test_websocket_real_time_updates(self, connection_manager):
        """Test real-time WebSocket updates during workflow execution."""
        # Mock websocket connections
        mock_websocket1 = Mock()
        mock_websocket2 = Mock()
        mock_websocket1.accept = AsyncMock()
        mock_websocket2.accept = AsyncMock()
        mock_websocket1.send_text = AsyncMock()
        mock_websocket2.send_text = AsyncMock()

        workflow_id = str(uuid.uuid4())

        # Connect multiple clients
        connection_id1 = await connection_manager.connect(mock_websocket1, workflow_id)
        connection_id2 = await connection_manager.connect(mock_websocket2, workflow_id)

        # Simulate workflow progress updates
        progress_updates = [
            {"status": "running", "progress_percentage": 0.0, "current_step": "step1"},
            {"status": "running", "progress_percentage": 33.3, "current_step": "step1"},
            {"status": "running", "progress_percentage": 66.7, "current_step": "step2"},
            {"status": "completed", "progress_percentage": 100.0, "current_step": None},
        ]

        for update in progress_updates:
            await connection_manager.send_workflow_update(workflow_id, update)
            await asyncio.sleep(0.1)  # Small delay to simulate real-time updates

        # Verify all clients received all updates
        assert mock_websocket1.send_text.call_count == 4
        assert mock_websocket2.send_text.call_count == 4

        # Verify message content
        calls = mock_websocket1.send_text.call_args_list
        for i, call in enumerate(calls):
            message = json.loads(call[0][0])
            assert message["type"] == "workflow_update"
            assert message["workflow_id"] == workflow_id
            assert message["data"] == progress_updates[i]

    @pytest.mark.asyncio
    async def test_websocket_step_updates(self, connection_manager):
        """Test real-time step updates via WebSocket."""
        mock_websocket = Mock()
        mock_websocket.accept = AsyncMock()
        mock_websocket.send_text = AsyncMock()

        workflow_id = str(uuid.uuid4())
        connection_id = await connection_manager.connect(mock_websocket, workflow_id)

        # Simulate step updates
        step_updates = [
            {
                "step_name": "step1",
                "status": "running",
                "started_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "step_name": "step1",
                "status": "completed",
                "completed_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "step_name": "step2",
                "status": "running",
                "started_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "step_name": "step2",
                "status": "completed",
                "completed_at": datetime.now(timezone.utc).isoformat(),
            },
        ]

        for update in step_updates:
            await connection_manager.send_step_update(
                workflow_id, update["step_name"], update
            )
            await asyncio.sleep(0.1)

        # Verify all updates were sent
        assert mock_websocket.send_text.call_count == 4

        # Verify message structure
        calls = mock_websocket.send_text.call_args_list
        for i, call in enumerate(calls):
            message = json.loads(call[0][0])
            assert message["type"] == "workflow_update"
            assert message["data"]["type"] == "step_update"
            assert message["workflow_id"] == workflow_id
            assert message["data"]["step_name"] == step_updates[i]["step_name"]
            assert message["data"]["data"] == step_updates[i]

    @pytest.mark.asyncio
    async def test_websocket_log_streaming(self, connection_manager):
        """Test real-time log streaming via WebSocket."""
        mock_websocket = Mock()
        mock_websocket.accept = AsyncMock()
        mock_websocket.send_text = AsyncMock()

        workflow_id = str(uuid.uuid4())
        connection_id = await connection_manager.connect(mock_websocket, workflow_id)

        # Simulate log streaming
        log_entries = [
            {
                "step_name": "step1",
                "log_level": "info",
                "message": "Starting processing",
            },
            {
                "step_name": "step1",
                "log_level": "debug",
                "message": "Processing item 1 of 100",
            },
            {
                "step_name": "step1",
                "log_level": "debug",
                "message": "Processing item 50 of 100",
            },
            {
                "step_name": "step1",
                "log_level": "info",
                "message": "Processing completed",
            },
            {
                "step_name": "step2",
                "log_level": "warn",
                "message": "Warning: Low memory",
            },
            {
                "step_name": "step2",
                "log_level": "error",
                "message": "Error: Processing failed",
            },
        ]

        for log_entry in log_entries:
            await connection_manager.send_log_update(workflow_id, log_entry)
            await asyncio.sleep(0.1)

        # Verify all log entries were sent
        assert mock_websocket.send_text.call_count == 6

        # Verify message structure
        calls = mock_websocket.send_text.call_args_list
        for i, call in enumerate(calls):
            message = json.loads(call[0][0])
            assert message["type"] == "workflow_update"
            assert message["data"]["type"] == "log_update"
            assert message["workflow_id"] == workflow_id
            assert message["data"]["data"] == log_entries[i]

    def test_workflow_deletion_cascade(self, client):
        """Test that workflow deletion cascades to steps and logs."""
        # Create workflow with steps and logs
        workflow_data = {"name": "Cascade Test Workflow", "total_steps": 2}
        create_response = client.post("/api/v1/workflows", json=workflow_data)
        workflow_id = create_response.json()["id"]

        # Add steps
        steps = [
            {"step_name": "step1", "step_order": 1},
            {"step_name": "step2", "step_order": 2},
        ]
        for step_data in steps:
            client.post(f"/api/v1/workflows/{workflow_id}/steps", json=step_data)

        # Add logs
        log_entries = [
            {"step_name": "step1", "log_level": "info", "message": "Test log 1"},
            {"step_name": "step2", "log_level": "info", "message": "Test log 2"},
        ]
        for log_data in log_entries:
            client.post(f"/api/v1/workflows/{workflow_id}/logs", json=log_data)

        # Verify workflow exists
        get_response = client.get(f"/api/v1/workflows/{workflow_id}")
        assert get_response.status_code == 200

        # Verify steps exist
        steps_response = client.get(f"/api/v1/workflows/{workflow_id}/steps")
        assert steps_response.status_code == 200
        assert len(steps_response.json()) == 2

        # Verify logs exist
        logs_response = client.get(f"/api/v1/workflows/{workflow_id}/logs")
        assert logs_response.status_code == 200
        assert len(logs_response.json()) >= 2

        # Delete workflow
        delete_response = client.delete(f"/api/v1/workflows/{workflow_id}")
        assert delete_response.status_code == 204

        # Verify workflow is deleted
        get_response = client.get(f"/api/v1/workflows/{workflow_id}")
        assert get_response.status_code == 404

        # Verify steps are deleted (cascade)
        steps_response = client.get(f"/api/v1/workflows/{workflow_id}/steps")
        assert steps_response.status_code == 404

        # Verify logs are deleted (cascade)
        logs_response = client.get(f"/api/v1/workflows/{workflow_id}/logs")
        assert logs_response.status_code == 404

    def test_concurrent_workflow_operations(self, client):
        """Test concurrent operations on multiple workflows."""
        # Create multiple workflows
        workflow_ids = []
        for i in range(3):
            workflow_data = {
                "name": f"Concurrent Test Workflow {i+1}",
                "total_steps": 2,
            }
            create_response = client.post("/api/v1/workflows", json=workflow_data)
            workflow_ids.append(create_response.json()["id"])

        # Add steps to all workflows concurrently
        for workflow_id in workflow_ids:
            steps = [
                {"step_name": "step1", "step_order": 1},
                {"step_name": "step2", "step_order": 2},
            ]
            for step_data in steps:
                client.post(f"/api/v1/workflows/{workflow_id}/steps", json=step_data)

        # Start all workflows
        for workflow_id in workflow_ids:
            client.put(f"/api/v1/workflows/{workflow_id}", json={"status": "running"})

        # Complete steps in all workflows
        for workflow_id in workflow_ids:
            client.put(
                f"/api/v1/workflows/{workflow_id}/steps/step1",
                json={"status": "completed"},
            )
            client.put(
                f"/api/v1/workflows/{workflow_id}/steps/step2",
                json={"status": "completed"},
            )
            client.put(f"/api/v1/workflows/{workflow_id}", json={"status": "completed"})

        # Verify all workflows are completed
        for workflow_id in workflow_ids:
            response = client.get(f"/api/v1/workflows/{workflow_id}")
            assert response.status_code == 200
            assert response.json()["status"] == "completed"

    def test_workflow_metadata_handling(self, client):
        """Test workflow metadata handling and persistence."""
        # Create workflow with metadata
        workflow_data = {
            "name": "Metadata Test Workflow",
            "description": "A workflow with metadata",
            "metadata": {
                "priority": "high",
                "environment": "production",
                "tags": ["test", "metadata"],
                "config": {"timeout": 300, "retries": 3},
            },
        }

        create_response = client.post("/api/v1/workflows", json=workflow_data)
        assert create_response.status_code == 201
        workflow_id = create_response.json()["id"]

        # Verify metadata is stored
        get_response = client.get(f"/api/v1/workflows/{workflow_id}")
        assert get_response.status_code == 200
        stored_metadata = get_response.json()["metadata"]
        assert stored_metadata["priority"] == "high"
        assert stored_metadata["environment"] == "production"
        assert stored_metadata["tags"] == ["test", "metadata"]
        assert stored_metadata["config"]["timeout"] == 300
        assert stored_metadata["config"]["retries"] == 3

        # Update metadata
        update_data = {
            "metadata": {
                "priority": "low",
                "environment": "staging",
                "tags": ["test", "metadata", "updated"],
                "config": {"timeout": 600, "retries": 5},
            }
        }

        update_response = client.put(
            f"/api/v1/workflows/{workflow_id}", json=update_data
        )
        assert update_response.status_code == 200

        # Verify metadata is updated
        get_response = client.get(f"/api/v1/workflows/{workflow_id}")
        assert get_response.status_code == 200
        updated_metadata = get_response.json()["metadata"]
        assert updated_metadata["priority"] == "low"
        assert updated_metadata["environment"] == "staging"
        assert updated_metadata["tags"] == ["test", "metadata", "updated"]
        assert updated_metadata["config"]["timeout"] == 600
        assert updated_metadata["config"]["retries"] == 5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
