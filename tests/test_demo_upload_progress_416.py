"""416 — demo VCF path must start upload progress via uploadProgressManager."""

from pathlib import Path

INDEX = Path("app/templates/index.html")


def test_demo_path_uses_upload_progress_manager():
    text = INDEX.read_text(encoding="utf-8")
    assert "window.uploadProgressManager" in text
    assert "startProgressTracking" in text
    assert (
        "typeof window.uploadProgressManager.startProgressTracking === 'function'"
        in text
    )
    # Broken pre-416 guard (class is lexical, never on window)
    assert "window.GenomeDownloadProgress.startProgressTracking" not in text
