"""415 — workflow-monitor refreshProgress must poll Job progress API."""

from pathlib import Path

MONITOR = Path("app/static/js/workflow-monitor.js")


def test_refresh_progress_uses_job_progress_url():
    text = MONITOR.read_text(encoding="utf-8")
    # Canonical catch-up poll (Job API; same id family as WS /api/v1/jobs/.../ws)
    assert "/api/v1/jobs/${this.workflowId}/progress" in text
    # Broken pre-415 path (upload router is /upload, not /api/v1/upload)
    assert "/api/v1/upload/status/" not in text


def test_refresh_progress_warns_on_non_ok():
    text = MONITOR.read_text(encoding="utf-8")
    # Ensure !response.ok is not a silent no-op
    assert "response.ok" in text
    assert "Progress refresh failed:" in text or "Progress refresh non-OK" in text
    # Prefer an explicit non-OK branch (else after if response.ok)
    start = text.index("async refreshProgress()")
    end = (
        text.index("\n    async ", start + 1)
        if "\n    async " in text[start + 1 :]
        else start + 800
    )
    block = text[start:end]
    assert "if (response.ok)" in block
    assert "else" in block or "response.status" in block
