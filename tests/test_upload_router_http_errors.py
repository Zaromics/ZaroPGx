"""Regression: POST /upload/genomic-data could never answer 400.

The handler body ends in a bare ``except Exception as e: raise HTTPException(500, ...)``.
``HTTPException`` is an ``Exception``, so the deliberate
``raise HTTPException(400, detail=result["error"])`` a few lines above — the one that
reports "VCF must contain exactly one sample", "No files provided", and every other
input-validation failure ``FileProcessor`` produces — was caught by that handler and
re-emitted as a 500. Clients saw a server fault for their own bad input, and the real
detail was buried behind "Upload failed: 400: ...".

The structural test below checks the whole module rather than this one handler: any
``try`` that re-wraps ``Exception`` as an ``HTTPException`` must let a real
``HTTPException`` through first.
"""

import ast
from pathlib import Path

import pytest

UPLOAD_ROUTER = (
    Path(__file__).resolve().parents[1] / "app" / "api" / "routes" / "upload_router.py"
)


def _handler_catches_http_exception(handler: ast.ExceptHandler) -> bool:
    names = handler.type
    if names is None:
        return False
    candidates = names.elts if isinstance(names, ast.Tuple) else [names]
    return any(
        isinstance(c, ast.Name) and c.id == "HTTPException" for c in candidates
    ) or any(
        isinstance(c, ast.Attribute) and c.attr == "HTTPException" for c in candidates
    )


def _handler_catches_bare_exception(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True  # bare `except:`
    return isinstance(handler.type, ast.Name) and handler.type.id in {
        "Exception",
        "BaseException",
    }


def _handler_raises_http_exception(handler: ast.ExceptHandler) -> bool:
    for node in ast.walk(handler):
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue
        exc = node.exc
        if isinstance(exc, ast.Call):
            exc = exc.func
        if isinstance(exc, ast.Name) and exc.id == "HTTPException":
            return True
        if isinstance(exc, ast.Attribute) and exc.attr == "HTTPException":
            return True
    return False


def _unguarded_rewraps(source: str):
    """``try`` blocks that turn any Exception into an HTTPException without re-raising."""
    offenders = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Try):
            continue
        for index, handler in enumerate(node.handlers):
            if not _handler_catches_bare_exception(handler):
                continue
            if not _handler_raises_http_exception(handler):
                continue
            if any(
                _handler_catches_http_exception(earlier)
                for earlier in node.handlers[:index]
            ):
                continue
            offenders.append(handler.lineno)
    return offenders


def test_no_handler_masks_an_http_exception_as_a_server_error():
    """A deliberate 4xx must not be swallowed by the catch-all below it."""
    offenders = _unguarded_rewraps(UPLOAD_ROUTER.read_text(encoding="utf-8"))
    assert not offenders, (
        "these `except Exception` handlers re-wrap HTTPException as a new "
        f"HTTPException with no `except HTTPException: raise` above them: {offenders}"
    )


@pytest.fixture
def rejecting_processor(monkeypatch):
    """Make FileProcessor reject the upload the way it does for bad input."""
    from app.api.routes import upload_router

    async def _reject(files, reference_genome, **kwargs):
        for f in files:  # drain, as the real implementation does
            await f.read()
        return {
            "success": False,
            "error": "VCF must contain exactly one sample; found 2.",
        }

    monkeypatch.setattr(upload_router.file_processor, "process_files", _reject)


def test_rejected_upload_answers_400_with_the_processor_reason(
    client, rejecting_processor
):
    resp = client.post(
        "/upload/genomic-data",
        files={"files": ("two_samples.vcf", b"##fileformat=VCFv4.2\n", "text/plain")},
        data={"reference_genome": "hg38"},
    )

    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "VCF must contain exactly one sample; found 2."
