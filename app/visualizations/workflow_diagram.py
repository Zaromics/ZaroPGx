"""Kroki, the stack's diagram renderer: Mermaid source in, SVG or PNG out.

The reports' workflow diagram is built per job in job_workflow.py and drawn here. The
flag-driven builders, Graphviz and Pillow renderers and static workflow.mmd that used
to live in this module drew a generic pipeline rather than the run's.
"""

from __future__ import annotations

import logging
import os
from typing import Literal

import requests

from app.utils.env import env_flag

logger = logging.getLogger(__name__)


def _kroki_timeout_seconds() -> float:
    """HTTP timeout for Kroki posts. Override with KROKI_TIMEOUT (seconds)."""
    raw = (os.environ.get("KROKI_TIMEOUT") or "30").strip()
    try:
        timeout = float(raw)
    except ValueError:
        logger.warning("Invalid KROKI_TIMEOUT=%r; using 30s", raw)
        return 30.0
    return max(0.5, timeout)


def render_with_kroki(
    mermaid_source: str,
    fmt: Literal["svg", "png", "pdf"] = "svg",
    kroki_url: str | None = None,
) -> bytes:
    """Render Mermaid diagram via Kroki.

    Args:
        mermaid_source: Mermaid DSL string
        fmt: output format ('svg', 'png', or 'pdf')
        kroki_url: base URL of Kroki service; defaults to env KROKI_URL, then the local
            http://localhost:8001 (never the public kroki.io: the diagram describes a run)

    Returns:
        bytes of rendered image

    Raises:
        RuntimeError: when KROKI_ENABLED is false (callers should fall back)
        requests exceptions / HTTP errors from Kroki
    """
    if not env_flag("KROKI_ENABLED", True):
        raise RuntimeError("Kroki disabled via KROKI_ENABLED")

    # Get base URL, handling empty strings properly
    env_kroki_url = os.environ.get("KROKI_URL", "").strip()
    if kroki_url:
        base = kroki_url
        logger.debug("Using provided kroki_url parameter: %s", base)
    elif env_kroki_url:
        base = env_kroki_url
        logger.debug("Using KROKI_URL environment variable: %s", base)
    else:
        base = "http://localhost:8001"
        logger.debug("Using default local Kroki URL: %s", base)

    url = f"{base.rstrip('/')}/mermaid/{fmt}"

    # Validate URL format
    if not url.startswith(("http://", "https://")):
        logger.error("Invalid Kroki URL format: %s (missing scheme)", url)
        raise ValueError(f"Invalid Kroki URL format: {url} (missing scheme)")

    # Ensure Mermaid init sets a universally compatible font
    def _ensure_font_init(src: str) -> str:
        try:
            init_present = "%%{init:" in src
            if init_present:
                return src
            # Force universal font and disable HTML labels (avoid <foreignObject> which WeasyPrint can't render)
            header = "%%{init: { 'themeVariables': { 'fontFamily': 'sans-serif' }, 'flowchart': { 'htmlLabels': false } }}%%"
            return f"{header}\n{src}"
        except Exception:
            return src

    prepared_source = _ensure_font_init(mermaid_source)

    headers = {"Content-Type": "text/plain; charset=utf-8"}
    timeout = _kroki_timeout_seconds()
    logger.info("Rendering Mermaid via Kroki: %s (timeout=%ss)", url, timeout)
    resp = requests.post(
        url, data=prepared_source.encode("utf-8"), headers=headers, timeout=timeout
    )
    resp.raise_for_status()
    return resp.content
