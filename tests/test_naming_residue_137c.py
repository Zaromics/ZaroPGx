"""137c naming residue contracts — hard-cut file_id→data_id, cleanup, WS."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def test_upload_response_models_use_data_id_not_file_id():
    text = Path("app/api/models.py").read_text(encoding="utf-8")
    # Upload / status surfaces
    assert "class UploadResponse" in text
    # Naive but effective: UploadResponse block must contain data_id and not file_id
    start = text.index("class UploadResponse")
    end = text.index("\nclass ", start + 1)
    block = text[start:end]
    assert "data_id:" in block
    assert "file_id:" not in block

    start = text.index("class ProcessingStatus")
    end = text.index("\nclass ", start + 1)
    block = text[start:end]
    assert "data_id:" in block
    assert "file_id:" not in block


def test_main_status_path_uses_data_id():
    text = Path("app/main.py").read_text(encoding="utf-8")
    assert '@app.get("/status/{data_id}")' in text
    assert '@app.get("/status/{file_id}")' not in text


def test_cleanup_path_is_job_not_workflow():
    text = Path("app/main.py").read_text(encoding="utf-8")
    assert '@app.post("/api/cleanup/job/{job_id}")' in text
    assert '/api/cleanup/workflow/' not in text


def test_websocket_envelope_type_is_job_update():
    text = Path("app/services/websocket_manager.py").read_text(encoding="utf-8")
    assert '"type": "job_update"' in text
    assert '"type": "workflow_update"' not in text
    assert "async def send_job_update" in text
