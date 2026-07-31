"""Regression: Kroki must not block forever / must honor enable+timeout env."""

import os
from unittest.mock import MagicMock, patch

import pytest

from app.visualizations import workflow_diagram as wd


@pytest.fixture(autouse=True)
def _clear_kroki_env(monkeypatch):
    monkeypatch.delenv("KROKI_ENABLED", raising=False)
    monkeypatch.delenv("KROKI_TIMEOUT", raising=False)
    monkeypatch.delenv("KROKI_URL", raising=False)


def test_render_with_kroki_disabled_raises_without_http(monkeypatch):
    monkeypatch.setenv("KROKI_ENABLED", "false")
    with patch.object(wd.requests, "post") as post:
        with pytest.raises(RuntimeError, match="Kroki disabled"):
            wd.render_with_kroki("flowchart TD\n  A-->B", fmt="svg")
        post.assert_not_called()


def test_render_with_kroki_uses_env_timeout(monkeypatch):
    monkeypatch.setenv("KROKI_ENABLED", "true")
    monkeypatch.setenv("KROKI_TIMEOUT", "4.5")
    monkeypatch.setenv("KROKI_URL", "http://kroki.test:8000")

    mock_resp = MagicMock()
    mock_resp.content = b"<svg/>"
    mock_resp.raise_for_status = MagicMock()

    with patch.object(wd.requests, "post", return_value=mock_resp) as post:
        out = wd.render_with_kroki("flowchart TD\n  A-->B", fmt="svg")
        assert out == b"<svg/>"
        assert post.call_args.kwargs["timeout"] == 4.5


def test_handle_final_stages_runs_sync_body_in_thread(monkeypatch):
    """Async wrapper must offload blocking work via asyncio.to_thread."""
    import asyncio

    import app.api.routes.upload_router as ur

    called = {}

    def fake_sync(workflow_id, outdir):
        called["args"] = (workflow_id, outdir)

    async def fake_to_thread(func, *args):
        called["func"] = func
        return func(*args)

    monkeypatch.setattr(ur, "_handle_final_stages_progression_sync", fake_sync)
    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

    asyncio.run(ur.handle_final_stages_progression("svc", "wid", "/out"))
    assert called["func"] is fake_sync
    assert called["args"] == ("wid", "/out")
