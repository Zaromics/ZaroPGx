"""`is_valid` and `validation_errors` are gone from the upload's analysis.

Nothing ever assigned either. `FileAnalysis` was constructed in two places and neither
passed them, so every upload answered `"is_valid": true, "validation_errors": null` --
including the analysis failure path, which sets `error`, types the file UNKNOWN (refused
at the door with its own message) and left `is_valid` at its `True` default. The page
rendered a "Validation Errors" alert from the second, which could never appear, and
interpolated each entry into HTML unescaped had one ever arrived.

A field that always says "valid" is worse than no field: a client that reads it is told
something nobody checked. The refusals that exist are 4xx responses and the standing
input warnings, and they are what a reader should look at.
"""

from __future__ import annotations

from pathlib import Path

from app.api.models import FileAnalysis as ResponseAnalysis
from app.api.utils.file_processor import FileAnalysis, FileType

REPO = Path(__file__).resolve().parents[1]
GONE = ("is_valid", "validation_errors")


def test_the_response_model_does_not_carry_them():
    assert not set(GONE) & set(ResponseAnalysis.model_fields)


def test_the_analysis_does_not_carry_them():
    analysis = FileAnalysis(
        file_type=FileType.UNKNOWN, is_compressed=False, has_index=False, error="x"
    )
    assert not any(hasattr(analysis, name) for name in GONE)


def test_the_upload_route_does_not_write_them():
    route = (REPO / "app" / "api" / "routes" / "upload_router.py").read_text("utf-8")
    assert not [name for name in GONE if name in route]


def test_the_page_has_no_alert_for_them():
    page = (REPO / "app" / "templates" / "index.html").read_text("utf-8")
    assert "validation_errors" not in page and "Validation Errors" not in page
