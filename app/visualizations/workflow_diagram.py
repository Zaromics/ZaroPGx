from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional, Dict, Any
import logging
import requests
import base64
from contextlib import suppress

try:
    from graphviz import Digraph  # type: ignore
except Exception:  # pragma: no cover
    Digraph = None  # type: ignore

try:
    from PIL import Image, ImageDraw, ImageFont  # type: ignore
except Exception:  # pragma: no cover
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore
    ImageFont = None  # type: ignore


logger = logging.getLogger(__name__)


def get_repo_root() -> Path:
    """Return the repository root directory (two levels up from this file)."""
    return Path(__file__).resolve().parents[2]


def read_workflow_mermaid() -> str:
    """Read the Mermaid source for the workflow diagram from visualizations/workflow.mmd.

    Falls back to a minimal inline diagram if the file is not found.
    """
    visualizations_dir = get_repo_root() / "visualizations"
    mmd_path = visualizations_dir / "workflow.mmd"
    try:
        return mmd_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("Mermaid source not found at %s; using fallback", mmd_path)
        return "flowchart TD; A[ZaroPGx] --> B[Workflow]; B --> C[Reports]"


def try_read_static_asset(preferred: str = "svg") -> tuple[str, bytes] | None:
    """Try to read a pre-rendered static asset from visualizations/.

    Returns tuple of (fmt, content_bytes) or None if not found.
    """
    root = get_repo_root() / "visualizations"
    candidates: list[tuple[str, Path]] = []
    if preferred == "svg":
        candidates.extend([
            ("svg", root / "workflow.svg"),
            ("png", root / "workflow.png"),
        ])
    else:
        candidates.extend([
            ("png", root / "workflow.png"),
            ("svg", root / "workflow.svg"),
        ])
    for fmt, p in candidates:
        if p.exists():
            try:
                return fmt, p.read_bytes()
            except Exception:
                continue
    return None


def render_with_kroki(
    mermaid_source: str,
    fmt: Literal["svg", "png", "pdf"] = "svg",
    kroki_url: str | None = None,
) -> bytes:
    """Render Mermaid diagram via Kroki.

    Args:
        mermaid_source: Mermaid DSL string
        fmt: output format ('svg', 'png', or 'pdf')
        kroki_url: base URL of Kroki service; defaults to env KROKI_URL or https://kroki.io

    Returns:
        bytes of rendered image
    """
    base = kroki_url or os.environ.get("KROKI_URL", "https://kroki.io")
    url = f"{base.rstrip('/')}/mermaid/{fmt}"
    headers = {"Content-Type": "text/plain; charset=utf-8"}
    logger.info("Rendering Mermaid via Kroki: %s", url)
    resp = requests.post(url, data=mermaid_source.encode("utf-8"), headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.content


def build_mermaid_from_workflow(workflow: Dict[str, Any]) -> str:
    """Build a Mermaid flowchart for a specific sample workflow.

    Expected keys in workflow (all optional; sensible defaults applied):
      - file_type: "vcf" | "bam" | "cram" | "sam" | "zip"
      - extracted_file_type: e.g., "vcf" (if zip)
      - used_gatk: bool
      - used_pypgx: bool
      - used_pharmcat: bool (default True)
      - exported_to_fhir: bool
    """
    file_type = str(workflow.get("file_type", "vcf")).lower()
    extracted = str(workflow.get("extracted_file_type", "")).lower()
    used_gatk = bool(workflow.get("used_gatk", file_type in {"bam", "cram", "sam"}))
    used_pypgx = bool(workflow.get("used_pypgx", False))
    used_pharmcat = bool(workflow.get("used_pharmcat", True))
    exported_to_fhir = bool(workflow.get("exported_to_fhir", False))

    # Helper to mark active path
    def act(label: str) -> str:
        return f"{label}:::active"

    m = [
        "flowchart TD",
        "classDef active fill:#cfe8ff,stroke:#5b8def,stroke-width:2px;",
        "classDef norm fill:#f5f7fa,stroke:#b5bdc9,stroke-width:1px;",
        "classDef svc fill:#f8f1ff,stroke:#9b59b6,stroke-width:1px;",
        "classDef io fill:#fff7e6,stroke:#f39c12,stroke-width:1px;",
        "",
        "subgraph Client[\"Client/UI\"]",
        "  U[User]",
        "  U --> Upload[\"Upload file\"]",
        "end",
        "",
        "subgraph FastAPI[\"FastAPI App\"]",
        "  Upload --> SaveTmp[/Save to /tmp and /data/uploads/]",
        "  SaveTmp --> Detect[Detect file type]",
    ]

    if file_type == "zip":
        m += [
            act("Detect"),
            "  Detect --> Extract[Extract ZIP]:::active",
            "  Extract --> Detect2[Detect extracted file type]:::active",
        ]
        file_type = extracted or "vcf"
    else:
        m += ["  Detect:::active"]

    if file_type in {"bam", "cram", "sam"}:
        if used_gatk:
            m += [
                "  Detect --> GATK[\"GATK variant calling\"]:::active",
                "  GATK --> VCF[VCF]:::active",
            ]
        else:
            m += [
                "  Detect --> GATK[\"GATK variant calling (skipped)\"]",
                "  GATK --> VCF[VCF]",
            ]
    else:
        m += [
            "  Detect --> VCF[VCF]:::active",
        ]

    if used_pypgx:
        m += [
            "  VCF --> PYP[\"PyPGx (CYP2D6)\"]:::active",
            "  PYP --> VCF",
        ]

    if used_pharmcat:
        m += [
            "  VCF --> PCAT[\"PharmCAT\"]:::active",
            "  PCAT --> Outputs[\"report.json<br/>report.html<br/>phenotype.json\"]:::io",
        ]
    else:
        m += [
            "  VCF --> PCAT[\"PharmCAT (skipped)\"]",
        ]

    m += [
        "  Outputs --> Generate[\"Generate reports\"]:::active",
        "  Generate --> ReportsDir[/Write to /data/reports/:report_id/]:::io",
        "  ReportsDir --> Serve[\"Serve at /reports/*\"]",
        "end",
        "",
        "subgraph Optional[\"FHIR Export (optional)\"]",
    ]
    if exported_to_fhir:
        m += [
            "  Generate -.-> FhirRoute[\"POST /reports/:report_id/export-to-fhir\"]:::active",
            "  FhirRoute --> DiagnosticReport[(DiagnosticReport)]:::active",
        ]
    else:
        m += [
            "  Generate -.-> FhirRoute[\"POST /reports/:report_id/export-to-fhir\"]",
        ]
    m += ["end"]

    return "\n".join(m)


def render_with_graphviz(workflow: Dict[str, Any], fmt: Literal["svg", "png"] = "svg") -> bytes:
    """Local fallback renderer using Graphviz to avoid external dependencies."""
    if Digraph is None:
        raise RuntimeError("graphviz is not available")

    file_type = str(workflow.get("file_type", "vcf")).lower()
    extracted = str(workflow.get("extracted_file_type", "")).lower()
    used_gatk = bool(workflow.get("used_gatk", file_type in {"bam", "cram", "sam"}))
    used_pypgx = bool(workflow.get("used_pypgx", False))
    used_pharmcat = bool(workflow.get("used_pharmcat", True))
    exported_to_fhir = bool(workflow.get("exported_to_fhir", False))

    g = Digraph("workflow", format=fmt)
    g.attr(rankdir="TB", fontsize="10")

    def n(name: str, label: str, active: bool = False, shape: str = "box"):
        style = "filled" if active else "rounded"
        fillcolor = "#cfe8ff" if active else "#f5f7fa"
        color = "#5b8def" if active else "#b5bdc9"
        g.node(name, label=label, shape=shape, style=style, fillcolor=fillcolor, color=color)

    def e(a: str, b: str, label: str = "", active: bool = False, style: str = "solid"):
        color = "#5b8def" if active else "#b5bdc9"
        g.edge(a, b, label=label, color=color, style=style)

    # Client
    n("U", "User")
    n("Upload", "Upload file", active=True)
    e("U", "Upload", active=True)

    # FastAPI
    n("SaveTmp", "Save to /tmp and /data/uploads/", active=True, shape="folder")
    e("Upload", "SaveTmp", active=True)
    n("Detect", "Detect file type", active=True)
    e("SaveTmp", "Detect", active=True)

    if file_type == "zip":
        n("Extract", "Extract ZIP", active=True)
        e("Detect", "Extract", active=True)
        n("Detect2", "Detect extracted type", active=True)
        e("Extract", "Detect2", active=True)
        file_type = extracted or "vcf"

    n("VCF", "VCF", active=True)
    if file_type in {"bam", "cram", "sam"}:
        n("GATK", "GATK variant calling", active=used_gatk)
        e("Detect", "GATK", active=used_gatk)
        e("GATK", "VCF", active=used_gatk)
    else:
        e("Detect", "VCF", active=True)

    if used_pypgx:
        n("PYP", "PyPGx (CYP2D6)", active=True)
        e("VCF", "PYP", active=True)
        e("PYP", "VCF", active=True)

    n("PCAT", "PharmCAT", active=used_pharmcat)
    e("VCF", "PCAT", active=used_pharmcat)

    n("Outputs", "report.json|report.html|phenotype.json", active=True, shape="component")
    e("PCAT", "Outputs", active=True)
    n("Generate", "Generate reports", active=True)
    e("Outputs", "Generate", active=True)
    n("ReportsDir", "Write to /data/reports/:report_id/", active=True, shape="folder")
    e("Generate", "ReportsDir", active=True)

    if exported_to_fhir:
        n("FhirRoute", "POST /reports/:report_id/export-to-fhir", active=True)
        e("Generate", "FhirRoute", style="dashed", active=True)
        n("Diag", "DiagnosticReport", active=True, shape="ellipse")
        e("FhirRoute", "Diag", active=True)
    else:
        with suppress(Exception):
            n("FhirRoute", "POST /reports/:report_id/export-to-fhir")
            e("Generate", "FhirRoute", style="dashed")

    return g.pipe()


def render_workflow(fmt: Literal["svg", "png", "pdf"] = "svg", workflow: Optional[Dict[str, Any]] = None) -> bytes:
    """Convenience wrapper to render the workflow diagram in the requested format.

    Tries Kroki first, then falls back to a checked-in static asset if available.
    """
    mermaid = build_mermaid_from_workflow(workflow) if workflow else read_workflow_mermaid()
    # First try Kroki
    try:
        return render_with_kroki(mermaid, fmt=fmt)
    except Exception as e:
        logger.warning("Kroki render failed (%s); trying Graphviz", str(e))
        # Try local Graphviz
        if workflow:
            try:
                if fmt in ("svg", "png"):
                    return render_with_graphviz(workflow, fmt=fmt)
                # If PDF requested, produce SVG and let caller embed it
                return render_with_graphviz(workflow, fmt="svg")
            except Exception as e2:
                logger.warning("Graphviz render failed (%s); trying static asset", str(e2))
        # Static asset fallback
        static = try_read_static_asset(preferred=fmt)
        if static is not None:
            _static_fmt, content = static
            return content
        return b""

def render_workflow_png_data_uri(workflow: Optional[Dict[str, Any]] = None) -> str:
    """Render workflow to PNG and return a data URI suitable for <img src> embedding."""
    png = render_workflow(fmt="png", workflow=workflow)
    if not png:
        return ""
    b64 = base64.b64encode(png).decode("ascii")
    return f"data:image/png;base64,{b64}"


def build_simple_html_from_workflow(workflow: Optional[Dict[str, Any]]) -> str:
    """Very simple HTML fallback rendering when all image renderers fail."""
    if workflow is None:
        workflow = {}
    file_type = str(workflow.get("file_type", "vcf")).upper()
    used_gatk = bool(workflow.get("used_gatk", file_type in {"BAM", "CRAM", "SAM"}))
    used_pypgx = bool(workflow.get("used_pypgx", False))
    exported_to_fhir = bool(workflow.get("exported_to_fhir", False))

    steps = ["Upload", "Detect ({})".format(file_type)]
    if file_type in {"BAM", "CRAM", "SAM"}:
        steps.append("GATK" + (" ✔" if used_gatk else " (skipped)"))
    steps.append("VCF")
    if used_pypgx:
        steps.append("PyPGx")
    steps.append("PharmCAT")
    steps.append("Reports")
    if exported_to_fhir:
        steps.append("FHIR Export")

    parts = []
    for i, s in enumerate(steps):
        parts.append(f"<span style='display:inline-block;padding:6px 10px;border:1px solid #b5bdc9;border-radius:6px;background:#f5f7fa;margin:2px 4px'>{s}</span>")
        if i < len(steps) - 1:
            parts.append("<span style='color:#5b8def'>&nbsp;→&nbsp;</span>")
    return "".join(parts)


def render_simple_png_from_workflow(workflow: Optional[Dict[str, Any]], width: int = 1400, height: int = 200) -> bytes:
    """Pure-Python rasterizer: draw a breadcrumb of steps to a PNG via Pillow.

    This avoids external services and ensures we always have an image for PDF/HTML.
    """
    if Image is None or ImageDraw is None:
        return b""
    if workflow is None:
        workflow = {}
    file_type = str(workflow.get("file_type", "vcf")).upper()
    used_gatk = bool(workflow.get("used_gatk", file_type in {"BAM", "CRAM", "SAM"}))
    used_pypgx = bool(workflow.get("used_pypgx", False))
    exported_to_fhir = bool(workflow.get("exported_to_fhir", False))

    steps = ["Upload", f"Detect ({file_type})"]
    if file_type in {"BAM", "CRAM", "SAM"}:
        steps.append("GATK" + (" ✔" if used_gatk else " (skipped)"))
    steps.append("VCF")
    if used_pypgx:
        steps.append("PyPGx")
    steps.append("PharmCAT")
    steps.append("Reports")
    if exported_to_fhir:
        steps.append("FHIR Export")

    img = Image.new("RGB", (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None  # type: ignore

    x = 10
    y = max(10, (height // 2) - 15)
    for i, s in enumerate(steps):
        # Draw rounded rectangle
        # Estimate width by text length
        char_w = 8
        box_w = max(120, char_w * len(s) + 20)
        box_h = 30
        draw.rounded_rectangle([x, y, x + box_w, y + box_h], radius=8, outline=(91, 141, 239), fill=(245, 247, 250), width=2)
        # Draw text
        draw.text((x + 10, y + 8), s, fill=(0, 0, 0), font=font)
        x += box_w + 20
        if i < len(steps) - 1:
            draw.text((x - 12, y + 8), "→", fill=(91, 141, 239), font=font)

    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()



