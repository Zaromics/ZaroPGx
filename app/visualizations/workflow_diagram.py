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
    """Return the directory containing this file (app/visualizations)."""
    return Path(__file__).resolve().parent


def read_workflow_mermaid() -> str:
    """Read the Mermaid source for the workflow diagram from workflow.mmd in the same directory.
    
    Falls back to a minimal inline diagram if the file is not found.
    """
    current_dir = Path(__file__).resolve().parent
    mmd_path = current_dir / "workflow.mmd"
    logger.info(f"Looking for workflow.mmd at: {mmd_path}")
    logger.info(f"Current directory exists: {current_dir.exists()}")
    logger.info(f"workflow.mmd file exists: {mmd_path.exists()}")
    
    try:
        content = mmd_path.read_text(encoding="utf-8")
        logger.info(f"Successfully read workflow.mmd: {len(content)} chars")
        return content
    except FileNotFoundError:
        logger.warning("Mermaid source not found at %s; using fallback", mmd_path)
        fallback = "flowchart TD; A[ZaroPGx] --> B[Workflow]; B --> C[Reports]"
        logger.info(f"Using fallback diagram: {fallback}")
        return fallback
    except Exception as e:
        logger.error(f"Error reading workflow.mmd: {str(e)}")
        fallback = "flowchart TD; A[ZaroPGx] --> B[Workflow]; B --> C[Reports]"
        logger.info(f"Using fallback diagram due to error: {fallback}")
        return fallback


def try_read_static_asset(preferred: str = "svg") -> tuple[str, bytes] | None:
    """Try to read a pre-rendered static asset from the current directory.
    
    Returns tuple of (fmt, content_bytes) or None if not found.
    """
    current_dir = Path(__file__).resolve().parent
    candidates: list[tuple[str, Path]] = []
    if preferred == "svg":
        candidates.extend([
            ("svg", current_dir / "workflow.svg"),
            ("png", current_dir / "workflow.png"),
        ])
    else:
        candidates.extend([
            ("png", current_dir / "workflow.png"),
            ("svg", current_dir / "workflow.svg"),
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
    if not url.startswith(('http://', 'https://')):
        logger.error("Invalid Kroki URL format: %s (missing scheme)", url)
        raise ValueError(f"Invalid Kroki URL format: {url} (missing scheme)")
    
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
            "  VCF --> PYP[\"PyPGx\"]:::active",
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
    logger.info(f"[GRAPHVIZ] Rendering workflow diagram - format: {fmt}")
    logger.debug(f"[GRAPHVIZ] Workflow data: {workflow}")
    
    if Digraph is None:
        logger.error("[GRAPHVIZ] Graphviz library not available")
        raise RuntimeError("graphviz is not available")

    file_type = str(workflow.get("file_type", "vcf")).lower()
    extracted = str(workflow.get("extracted_file_type", "")).lower()
    used_gatk = bool(workflow.get("used_gatk", file_type in {"bam", "cram", "sam"}))
    used_pypgx = bool(workflow.get("used_pypgx", False))
    used_pharmcat = bool(workflow.get("used_pharmcat", True))
    exported_to_fhir = bool(workflow.get("exported_to_fhir", False))

    # Try two different approaches for PNG to ensure text renders
    attempts = []
    
    if fmt == "png":
        # Attempt 1: WeasyPrint-optimized settings with Arial (best compatibility)
        attempts.append({
            "name": "weasyprint_optimized",
            "graph_attrs": {"rankdir": "TB", "dpi": "96", "bgcolor": "white", "fontname": "Arial", "fontsize": "12"},
            "node_attrs": {"fontname": "Arial", "fontsize": "12", "shape": "box", "style": "rounded,filled", 
                          "fillcolor": "#f5f7fa", "color": "#2c3e50", "penwidth": "2", "fontcolor": "#2c3e50"},
            "edge_attrs": {"fontname": "Arial", "fontsize": "11", "color": "#2c3e50", "penwidth": "2", "fontcolor": "#2c3e50"}
        })
        
        # Attempt 2: System fonts with guaranteed text visibility
        attempts.append({
            "name": "system_default",
            "graph_attrs": {"rankdir": "TB", "dpi": "96", "bgcolor": "white", "fontname": "sans-serif"},
            "node_attrs": {"fontname": "sans-serif", "fontsize": "12", "shape": "box", "style": "rounded,filled", 
                          "fillcolor": "#f5f7fa", "color": "#2c3e50", "penwidth": "2", "fontcolor": "#2c3e50"},
            "edge_attrs": {"fontname": "sans-serif", "fontsize": "11", "color": "#2c3e50", "penwidth": "2", "fontcolor": "#2c3e50"}
        })
        
        # Attempt 3: Liberation Sans fallback with explicit text color
        attempts.append({
            "name": "liberation_sans",
            "graph_attrs": {"rankdir": "TB", "dpi": "96", "bgcolor": "white", "fontname": "Liberation Sans"},
            "node_attrs": {"fontname": "Liberation Sans", "fontsize": "12", "shape": "box", "style": "rounded,filled", 
                          "fillcolor": "#f5f7fa", "color": "#2c3e50", "penwidth": "2", "fontcolor": "#2c3e50"},
            "edge_attrs": {"fontname": "Liberation Sans", "fontsize": "11", "color": "#2c3e50", "penwidth": "2", "fontcolor": "#2c3e50"}
        })
    else:
        # SVG version optimized for WeasyPrint with guaranteed text rendering
        attempts.append({
            "name": "svg_weasyprint_text",
            "graph_attrs": {"rankdir": "TB", "dpi": "96", "bgcolor": "white", "fontname": "Arial", "fontsize": "12"},
            "node_attrs": {"fontname": "Arial", "fontsize": "12", "shape": "box", "style": "rounded,filled", 
                          "fillcolor": "#f5f7fa", "color": "#2c3e50", "penwidth": "2", "fontcolor": "#2c3e50"},
            "edge_attrs": {"fontname": "Arial", "fontsize": "11", "color": "#2c3e50", "penwidth": "2", "fontcolor": "#2c3e50"}
        })
        
        # SVG version with system fonts for better compatibility
        attempts.append({
            "name": "svg_system_fonts",
            "graph_attrs": {"rankdir": "TB", "dpi": "96", "bgcolor": "white", "fontname": "sans-serif", "fontsize": "12"},
            "node_attrs": {"fontname": "sans-serif", "fontsize": "12", "shape": "box", "style": "rounded,filled", 
                          "fillcolor": "#f5f7fa", "color": "#2c3e50", "penwidth": "2", "fontcolor": "#2c3e50"},
            "edge_attrs": {"fontname": "sans-serif", "fontsize": "11", "color": "#2c3e50", "penwidth": "2", "fontcolor": "#2c3e50"}
        })
        
        # Fallback SVG version with minimal font requirements
        attempts.append({
            "name": "svg_fallback",
            "graph_attrs": {"rankdir": "TB", "dpi": "96", "bgcolor": "white"},
            "node_attrs": {"fontsize": "12", "shape": "box", "style": "rounded,filled", 
                          "fillcolor": "#f5f7fa", "color": "#2c3e50", "fontcolor": "#2c3e50"},
            "edge_attrs": {"fontsize": "11", "color": "#2c3e50", "fontcolor": "#2c3e50"}
        })

    last_error = None
    for attempt in attempts:
        try:
            g = Digraph("workflow", format=fmt)
            g.attr(**attempt["graph_attrs"])
            g.node_attr.update(**attempt["node_attrs"])
            g.edge_attr.update(**attempt["edge_attrs"])
            
            logger.info(f"[GRAPHVIZ] Trying render with {attempt['name']} settings")
            logger.debug(f"[GRAPHVIZ] Graph attrs: {attempt['graph_attrs']}")
            logger.debug(f"[GRAPHVIZ] Node attrs: {attempt['node_attrs']}")
            
            result = _render_graphviz_diagram(g, file_type, extracted, used_gatk, used_pypgx, used_pharmcat, exported_to_fhir)
            if result and len(result) > 1000:  # Reasonable size check for a real image
                logger.info(f"[GRAPHVIZ] ✓ Successfully rendered with {attempt['name']} settings, size: {len(result)} bytes")
                
                # For PNG, try to debug if text is included by checking for common text patterns
                if fmt == "png":
                    # Save debug copy for inspection
                    try:
                        import tempfile
                        with tempfile.NamedTemporaryFile(suffix=f"_debug_{attempt['name']}.png", delete=False) as tmp:
                            tmp.write(result)
                            logger.info(f"[GRAPHVIZ] Debug PNG saved: {tmp.name}")
                    except Exception as debug_e:
                        logger.debug(f"[GRAPHVIZ] Could not save debug PNG: {debug_e}")
                
                return result
            else:
                logger.warning(f"[GRAPHVIZ] ✗ Render with {attempt['name']} produced small/empty result: {len(result) if result else 0} bytes")
        except Exception as e:
            logger.warning(f"Graphviz render failed with {attempt['name']}: {str(e)}")
            last_error = e
            continue
    
    # If all attempts failed, raise the last error
    if last_error:
        raise last_error
    else:
        raise RuntimeError("All Graphviz rendering attempts failed")


def _render_graphviz_diagram(g, file_type: str, extracted: str, used_gatk: bool, used_pypgx: bool, used_pharmcat: bool, exported_to_fhir: bool) -> bytes:
    """Helper function to build the actual Graphviz diagram structure."""
    logger.debug(f"[GRAPHVIZ] Building diagram structure - file_type: {file_type}, gatk: {used_gatk}, pypgx: {used_pypgx}")

    def n(name: str, label: str, active: bool = False, shape: str = "box"):
        node_kwargs = {}
        if active:
            node_kwargs = {"fillcolor": "#cfe8ff", "color": "#5b8def"}
        logger.debug(f"[GRAPHVIZ] Adding node: {name} = '{label}' (active: {active})")
        g.node(name, label=label, shape=shape, **node_kwargs)

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
        n("PYP", "PyPGx", active=True)
        e("VCF", "PYP", active=True)
        e("PYP", "VCF", active=True)

    n("PCAT", "PharmCAT", active=used_pharmcat)
    e("VCF", "PCAT", active=used_pharmcat)

    n("Outputs", "report.json | report.html | phenotype.json", active=True, shape="box")
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

    logger.debug(f"[GRAPHVIZ] Starting final pipe rendering for {g.format}")
    logger.debug(f"[GRAPHVIZ] Graphviz source:\n{g.source}")
    
    result = g.pipe()
    logger.debug(f"[GRAPHVIZ] Pipe completed successfully, result size: {len(result) if result else 0} bytes")
    return result


def render_kroki_mermaid_svg(workflow: Optional[Dict[str, Any]] = None) -> bytes:
    """Render workflow to SVG using Kroki Mermaid for comparison purposes.
    
    This function prioritizes the sophisticated Mermaid template from workflow.mmd
    for better quality diagrams, with workflow-specific diagrams as fallback.
    """
    try:
        # First try the sophisticated Mermaid template from workflow.mmd
        mermaid = read_workflow_mermaid()
        result = render_with_kroki(mermaid, fmt="svg")
        if result:
            logger.info("Generated sophisticated Kroki Mermaid SVG from workflow.mmd (size: %d bytes)", len(result))
            return result
    except Exception as e:
        logger.warning("Sophisticated Mermaid template failed (%s); trying workflow-specific", str(e))
    
    # Fallback to workflow-specific Mermaid if sophisticated template fails
    if workflow:
        try:
            mermaid = build_mermaid_from_workflow(workflow)
            result = render_with_kroki(mermaid, fmt="svg")
            if result:
                logger.info("Generated workflow-specific Kroki Mermaid SVG (size: %d bytes)", len(result))
                return result
            else:
                logger.warning("Workflow-specific Kroki Mermaid SVG generation returned empty result")
                return b""
        except Exception as e:
            logger.error("Workflow-specific Kroki Mermaid SVG generation failed: %s", str(e))
            return b""
    
    # If no workflow data and sophisticated template failed, return empty
    return b""


def render_workflow(fmt: Literal["svg", "png", "pdf"] = "svg", workflow: Optional[Dict[str, Any]] = None) -> bytes:
    """Render the workflow diagram in the requested format.

    For SVG, prioritize the sophisticated Mermaid template from workflow.mmd since it's more detailed.
    For PNG, prioritize Python PNG for guaranteed text rendering in PDFs.
    """
    logger.info(f"render_workflow called with fmt={fmt}, workflow={workflow is not None}")
    
    # For SVG, prioritize the sophisticated Mermaid template from workflow.mmd
    if fmt == "svg":
        logger.info("SVG format requested - prioritizing sophisticated Mermaid template from workflow.mmd")
        try:
            # First try the sophisticated Mermaid template from workflow.mmd
            mermaid = read_workflow_mermaid()
            logger.info(f"Read sophisticated Mermaid template: {len(mermaid)} chars")
            result = render_with_kroki(mermaid, fmt=fmt)
            if result:
                logger.info("✓ Using sophisticated Mermaid template from workflow.mmd (size: %d bytes)", len(result))
                return result
            else:
                logger.warning("Sophisticated Mermaid template returned empty result")
        except Exception as e:
            logger.warning("Sophisticated Mermaid template failed (%s); trying workflow-specific Mermaid", str(e))
        
        # If sophisticated template fails, try workflow-specific Mermaid
        if workflow:
            logger.info("Trying workflow-specific Mermaid as fallback")
            try:
                mermaid = build_mermaid_from_workflow(workflow)
                logger.info(f"Built workflow-specific Mermaid: {len(mermaid)} chars")
                result = render_with_kroki(mermaid, fmt=fmt)
                if result:
                    logger.info("✓ Using workflow-specific Mermaid (size: %d bytes)", len(result))
                    return result
                else:
                    logger.warning("Workflow-specific Mermaid returned empty result")
            except Exception as e:
                logger.warning("Workflow-specific Mermaid failed (%s); trying Graphviz", str(e))
        
        # Fallback to Graphviz for SVG
        if workflow:
            logger.info("Trying Graphviz SVG as final fallback")
            try:
                result = render_with_graphviz(workflow, fmt=fmt)
                if result:
                    logger.info("✓ Using Graphviz SVG fallback (size: %d bytes)", len(result))
                    return result
                else:
                    logger.warning("Graphviz SVG fallback returned empty result")
            except Exception as e:
                logger.warning("Graphviz SVG fallback failed (%s)", str(e))
    
    # For PNG format with workflow data, prioritize Python PNG for guaranteed text rendering
    elif workflow and fmt == "png":
        logger.info("PNG format requested with workflow data - prioritizing Python PNG")
        # Always use Python PNG for guaranteed text - WeasyPrint has font issues with Graphviz PNG
        try:
            python_png = render_simple_png_from_workflow(workflow)
            if python_png:
                logger.info("✓ Using Python/Pillow PNG for guaranteed text rendering (size: %d bytes)", len(python_png))
                return python_png
            else:
                logger.warning("Python PNG returned empty result")
        except Exception as e:
            logger.warning("Python PNG failed (%s); trying alternatives", str(e))
        
        # Try other methods only if Python PNG fails
        for method_name, method_func in [
            ("Sophisticated Mermaid", lambda: render_with_kroki(read_workflow_mermaid(), fmt=fmt)),
            ("Workflow-specific Mermaid", lambda: render_with_kroki(build_mermaid_from_workflow(workflow), fmt=fmt)),
            ("Graphviz", lambda: render_with_graphviz(workflow, fmt=fmt))
        ]:
            try:
                result = method_func()
                if result and len(result) > 1000:
                    logger.info("✓ Using %s PNG (size: %d bytes)", method_name, len(result))
                    return result
                else:
                    logger.warning("%s PNG returned empty or too small result", method_name)
            except Exception as e:
                logger.warning("%s PNG render failed (%s)", method_name, str(e))
                continue
            
    # For other formats or when no workflow data
    elif workflow:
        logger.info("Other format or no workflow data - trying Graphviz")
        try:
            if fmt in ("svg", "png"):
                return render_with_graphviz(workflow, fmt=fmt)
            # For PDF, render SVG and let the caller embed it
            return render_with_graphviz(workflow, fmt="svg")
        except Exception as e:
            logger.warning("Graphviz render failed (%s); trying Mermaid", str(e))

    # Try sophisticated Mermaid template as final fallback
    logger.info("Trying sophisticated Mermaid template as final fallback")
    try:
        mermaid = read_workflow_mermaid()
        logger.info(f"Read sophisticated Mermaid template for fallback: {len(mermaid)} chars")
        result = render_with_kroki(mermaid, fmt=fmt)
        if result:
            logger.info("✓ Using sophisticated Mermaid template as final fallback (size: %d bytes)", len(result))
            return result
        else:
            logger.warning("Sophisticated Mermaid template fallback returned empty result")
    except Exception as e:
        logger.warning("Sophisticated Mermaid template fallback failed (%s)", str(e))

    # Static asset fallback
    logger.info("Trying static asset fallback")
    static = try_read_static_asset(preferred=fmt)
    if static is not None:
        _static_fmt, content = static
        logger.info("✓ Using static asset fallback (size: %d bytes)", len(content))
        return content

    # Final fallback: pure-Python PNG if all else fails
    if workflow and fmt == "png":
        logger.info("Trying pure-Python PNG as final fallback")
        try:
            result = render_simple_png_from_workflow(workflow)
            if result:
                logger.info("✓ Using pure-Python PNG as final fallback (size: %d bytes)", len(result))
                return result
        except Exception as e:
            logger.warning("Pure-Python PNG final fallback failed (%s)", str(e))
    
    logger.error("All workflow diagram generation methods failed - returning empty result")
    return b""


def render_workflow_png_data_uri(workflow: Optional[Dict[str, Any]] = None) -> str:
    """Render workflow to PNG and return a data URI suitable for <img src> embedding."""
    png = render_workflow(fmt="png", workflow=workflow)
    if not png:
        return ""
    b64 = base64.b64encode(png).decode("ascii")
    return f"data:image/png;base64,{b64}"


def build_simple_html_from_workflow(workflow: Dict[str, Any]) -> str:
    """Build a simple HTML representation of the workflow diagram.
    
    This function creates a pure HTML/CSS workflow diagram that should render
    reliably in WeasyPrint without external dependencies.
    """
    try:
        # Extract workflow information
        file_type = workflow.get('file_type', 'unknown')
        used_gatk = workflow.get('used_gatk', False)
        used_pypgx = workflow.get('used_pypgx', False)
        used_pharmcat = workflow.get('used_pharmcat', False)
        exported_to_fhir = workflow.get('exported_to_fhir', False)
        
        # Build workflow steps
        steps = []
        
        # Always start with Upload
        steps.append('Upload')
        
        # Add detection step
        if file_type.lower() in ['vcf', 'vcf.gz']:
            steps.append('Detect (VCF)')
        elif file_type.lower() in ['bam', 'sam']:
            steps.append('Detect (BAM)')
        else:
            steps.append(f'Detect ({file_type.upper()})')
        
        # Add file type step
        steps.append(file_type.upper())
        
        # Add processing steps
        if used_gatk:
            steps.append('GATK')
        
        if used_pypgx:
            steps.append('PyPGx')
            
        if used_pharmcat:
            steps.append('PharmCAT')
            
        # Add final steps
        steps.append('Reports')
        
        if exported_to_fhir:
            steps.append('FHIR Export')
        
        # Create simple HTML with basic styling
        html_parts = []
        html_parts.append('<div style="text-align: center; padding: 20px; background: #f8f9fa; border: 1px solid #dee2e6; border-radius: 8px; margin: 10px 0;">')
        
        for i, step in enumerate(steps):
            # Add step
            html_parts.append(f'<span style="display: inline-block; padding: 12px 16px; border: 2px solid #2c3e50; border-radius: 8px; background: #ffffff; margin: 4px 6px; font-family: Arial, sans-serif; font-size: 14px; font-weight: bold; color: #2c3e50; text-align: center; min-width: 80px;">{step}</span>')
            
            # Add arrow (except after last step)
            if i < len(steps) - 1:
                html_parts.append('<span style="color: #2c3e50; font-size: 18px; font-weight: bold; margin: 0 8px; font-family: Arial, sans-serif;">→</span>')
        
        html_parts.append('</div>')
        
        return ''.join(html_parts)
        
    except Exception as e:
        logger.error(f"Error building HTML workflow: {str(e)}")
        # Return a simple fallback
        return '<div style="text-align: center; padding: 20px; color: #666;">Workflow diagram could not be generated</div>'


def render_simple_png_from_workflow(workflow: Optional[Dict[str, Any]], width: int = 1600, height: int = 300) -> bytes:
    """Pure-Python rasterizer: draw a breadcrumb of steps to a PNG via Pillow.

    This avoids external services and ensures we always have an image for PDF/HTML with guaranteed text rendering.
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
    
    # Try to load a better font for clearer text rendering
    font = None
    try:
        # Try to load a TrueType font for better rendering
        font_size = 18  # Larger font for better visibility
        for font_path in ["/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
                         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                         "/System/Library/Fonts/Arial.ttf",  # macOS
                         "C:/Windows/Fonts/arial.ttf"]:      # Windows
            try:
                font = ImageFont.truetype(font_path, font_size)
                break
            except (OSError, IOError):
                continue
    except Exception:
        pass
    
    if font is None:
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None  # type: ignore

    # Calculate positioning to center the workflow
    total_width = sum(max(140, len(s) * 12 + 30) for s in steps) + (len(steps) - 1) * 35
    start_x = max(10, (width - total_width) // 2)
    y = max(30, (height // 2) - 35)

    x = start_x
    for i, s in enumerate(steps):
        # Draw rounded rectangle with better styling
        char_w = 12  # Larger character estimate for bigger text
        box_w = max(140, char_w * len(s) + 30)
        box_h = 70  # Much taller boxes
        
        # Draw box with better colors for PDF visibility
        draw.rounded_rectangle([x, y, x + box_w, y + box_h], 
                             radius=8, 
                             outline=(44, 62, 80),   # Darker outline
                             fill=(245, 247, 250),   # Light background
                             width=2)
        
        # Draw text with better positioning and color
        text_x = x + (box_w - len(s) * char_w) // 2
        text_y = y + (box_h - 20) // 2
        draw.text((text_x, text_y), s, fill=(44, 62, 80), font=font)
        
        x += box_w + 35
        
        # Draw arrow between steps with larger size
        if i < len(steps) - 1:
            arrow_y = y + box_h // 2
            # Use larger arrow character and better positioning
            draw.text((x - 25, arrow_y - 12), "→", fill=(91, 141, 239), font=font)

    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def build_simple_text_workflow(workflow: Dict[str, Any]) -> str:
    """Build a simple text-based workflow diagram that renders reliably in WeasyPrint.
    
    This function creates a plain text representation using basic HTML elements
    that should work consistently across all WeasyPrint versions.
    """
    try:
        # Extract workflow information
        file_type = workflow.get('file_type', 'unknown')
        used_gatk = workflow.get('used_gatk', False)
        used_pypgx = workflow.get('used_pypgx', False)
        used_pharmcat = workflow.get('used_pharmcat', False)
        exported_to_fhir = workflow.get('exported_to_fhir', False)
        
        # Build workflow steps
        steps = []
        
        # Always start with Upload
        steps.append('Upload')
        
        # Add detection step
        if file_type.lower() in ['vcf', 'vcf.gz']:
            steps.append('Detect (VCF)')
        elif file_type.lower() in ['bam', 'sam']:
            steps.append('Detect (BAM)')
        else:
            steps.append(f'Detect ({file_type.upper()})')
        
        # Add file type step
        steps.append(file_type.upper())
        
        # Add processing steps
        if used_gatk:
            steps.append('GATK')
        
        if used_pypgx:
            steps.append('PyPGx')
            
        if used_pharmcat:
            steps.append('PharmCAT')
            
        # Add final steps
        steps.append('Reports')
        
        if exported_to_fhir:
            steps.append('FHIR Export')
        
        # Create simple text-based HTML using basic elements
        html_parts = []
        html_parts.append('<div class="workflow-text">')
        html_parts.append('<h3>Workflow Summary</h3>')
        
        for i, step in enumerate(steps):
            # Add step number and name
            html_parts.append(f'<p><strong>{i+1}.</strong> {step}</p>')
            
            # Add arrow (except after last step)
            if i < len(steps) - 1:
                html_parts.append('<p style="text-align: center; margin: 5px 0;">↓</p>')
        
        html_parts.append('</div>')
        
        return ''.join(html_parts)
        
    except Exception as e:
        logger.error(f"Error building text workflow: {str(e)}")
        # Return a simple fallback
        return '<div class="workflow-text"><p>Workflow diagram could not be generated</p></div>'


def build_plain_text_workflow(workflow: Dict[str, Any]) -> str:
    """Build a plain text workflow diagram that renders reliably in WeasyPrint.
    
    This function creates a plain text representation using only basic text elements
    that should work consistently across all WeasyPrint versions.
    """
    try:
        # Extract workflow information
        file_type = workflow.get('file_type', 'unknown')
        used_gatk = workflow.get('used_gatk', False)
        used_pypgx = workflow.get('used_pypgx', False)
        used_pharmcat = workflow.get('used_pharmcat', False)
        exported_to_fhir = workflow.get('exported_to_fhir', False)
        
        # Build workflow steps
        steps = []
        
        # Always start with Upload
        steps.append('Upload')
        
        # Add detection step
        if file_type.lower() in ['vcf', 'vcf.gz']:
            steps.append('Detect (VCF)')
        elif file_type.lower() in ['bam', 'sam']:
            steps.append('Detect (BAM)')
        else:
            steps.append(f'Detect ({file_type.upper()})')
        
        # Add file type step
        steps.append(file_type.upper())
        
        # Add processing steps
        if used_gatk:
            steps.append('GATK')
        
        if used_pypgx:
            steps.append('PyPGx')
            
        if used_pharmcat:
            steps.append('PharmCAT')
            
        # Add final steps
        steps.append('Reports')
        
        if exported_to_fhir:
            steps.append('FHIR Export')
        
        # Create plain text representation
        text_parts = []
        text_parts.append('WORKFLOW SUMMARY')
        text_parts.append('=' * 20)
        
        for i, step in enumerate(steps):
            # Add step number and name
            text_parts.append(f'{i+1}. {step}')
            
            # Add arrow (except after last step)
            if i < len(steps) - 1:
                text_parts.append('   ↓')
        
        return '\n'.join(text_parts)
        
    except Exception as e:
        logger.error(f"Error building plain text workflow: {str(e)}")
        # Return a simple fallback
        return 'Workflow diagram could not be generated'


def build_table_workflow(workflow: Dict[str, Any]) -> str:
    """Build a table-based workflow diagram that renders reliably in WeasyPrint.
    
    This function creates a table representation using basic HTML table elements
    that should work consistently across all WeasyPrint versions.
    """
    try:
        # Extract workflow information
        file_type = workflow.get('file_type', 'unknown')
        used_gatk = workflow.get('used_gatk', False)
        used_pypgx = workflow.get('used_pypgx', False)
        used_pharmcat = workflow.get('used_pharmcat', False)
        exported_to_fhir = workflow.get('exported_to_fhir', False)
        
        # Build workflow steps
        steps = []
        
        # Always start with Upload
        steps.append('Upload')
        
        # Add detection step
        if file_type.lower() in ['vcf', 'vcf.gz']:
            steps.append('Detect (VCF)')
        elif file_type.lower() in ['bam', 'sam']:
            steps.append('Detect (BAM)')
        else:
            steps.append(f'Detect ({file_type.upper()})')
        
        # Add file type step
        steps.append(file_type.upper())
        
        # Add processing steps
        if used_gatk:
            steps.append('GATK')
        
        if used_pypgx:
            steps.append('PyPGx')
            
        if used_pharmcat:
            steps.append('PharmCAT')
            
        # Add final steps
        steps.append('Reports')
        
        if exported_to_fhir:
            steps.append('FHIR Export')
        
        # Create table-based HTML
        html_parts = []
        html_parts.append('<table class="workflow-table" style="width: 100%; border-collapse: collapse; margin: 20px 0; font-family: Arial, sans-serif;">')
        html_parts.append('<thead>')
        html_parts.append('<tr>')
        html_parts.append('<th style="border: 1px solid #ddd; padding: 12px; text-align: center; background-color: #f8f9fa; font-weight: bold;">Step</th>')
        html_parts.append('<th style="border: 1px solid #ddd; padding: 12px; text-align: center; background-color: #f8f9fa; font-weight: bold;">Description</th>')
        html_parts.append('</tr>')
        html_parts.append('</thead>')
        html_parts.append('<tbody>')
        
        for i, step in enumerate(steps):
            html_parts.append('<tr>')
            html_parts.append(f'<td style="border: 1px solid #ddd; padding: 8px; text-align: center; font-weight: bold;">{i+1}</td>')
            html_parts.append(f'<td style="border: 1px solid #ddd; padding: 8px; text-align: center;">{step}</td>')
            html_parts.append('</tr>')
        
        html_parts.append('</tbody>')
        html_parts.append('</table>')
        
        return ''.join(html_parts)
        
    except Exception as e:
        logger.error(f"Error building table workflow: {str(e)}")
        # Return a simple fallback
        return '<p>Workflow diagram could not be generated</p>'


def build_simple_text_workflow_v2(workflow: Dict[str, Any]) -> str:
    """Build a simple text-based workflow diagram using minimal HTML.
    
    This function creates a very basic text representation using only
    the simplest HTML elements possible to avoid WeasyPrint rendering issues.
    """
    try:
        # Extract workflow information
        file_type = workflow.get('file_type', 'unknown')
        used_gatk = workflow.get('used_gatk', False)
        used_pypgx = workflow.get('used_pypgx', False)
        used_pharmcat = workflow.get('used_pharmcat', False)
        exported_to_fhir = workflow.get('exported_to_fhir', False)
        
        # Build workflow steps
        steps = []
        
        # Always start with Upload
        steps.append('Upload')
        
        # Add detection step
        if file_type.lower() in ['vcf', 'vcf.gz']:
            steps.append('Detect (VCF)')
        elif file_type.lower() in ['bam', 'sam']:
            steps.append('Detect (BAM)')
        else:
            steps.append(f'Detect ({file_type.upper()})')
        
        # Add file type step
        steps.append(file_type.upper())
        
        # Add processing steps
        if used_gatk:
            steps.append('GATK')
        
        if used_pypgx:
            steps.append('PyPGx')
            
        if used_pharmcat:
            steps.append('PharmCAT')
            
        # Add final steps
        steps.append('Reports')
        
        if exported_to_fhir:
            steps.append('FHIR Export')
        
        # Create simple text representation with minimal HTML
        html_parts = []
        html_parts.append('<div>')
        html_parts.append('<h3>Workflow Summary</h3>')
        
        for i, step in enumerate(steps):
            # Add step number and name
            html_parts.append(f'<p>{i+1}. {step}</p>')
            
            # Add arrow (except after last step)
            if i < len(steps) - 1:
                html_parts.append('<p>↓</p>')
        
        html_parts.append('</div>')
        
        return ''.join(html_parts)
        
    except Exception as e:
        logger.error(f"Error building simple text workflow v2: {str(e)}")
        # Return a simple fallback
        return '<p>Workflow diagram could not be generated</p>'


def build_plain_text_workflow_v2(workflow: Dict[str, Any]) -> str:
    """Build a completely plain text workflow diagram.
    
    This function creates a plain text representation using only
    basic text elements to avoid any WeasyPrint rendering issues.
    """
    try:
        # Extract workflow information
        file_type = workflow.get('file_type', 'unknown')
        used_gatk = workflow.get('used_gatk', False)
        used_pypgx = workflow.get('used_pypgx', False)
        used_pharmcat = workflow.get('used_pharmcat', False)
        exported_to_fhir = workflow.get('exported_to_fhir', False)
        
        # Build workflow steps
        steps = []
        
        # Always start with Upload
        steps.append('Upload')
        
        # Add detection step
        if file_type.lower() in ['vcf', 'vcf.gz']:
            steps.append('Detect (VCF)')
        elif file_type.lower() in ['bam', 'sam']:
            steps.append('Detect (BAM)')
        else:
            steps.append(f'Detect ({file_type.upper()})')
        
        # Add file type step
        steps.append(file_type.upper())
        
        # Add processing steps
        if used_gatk:
            steps.append('GATK')
        
        if used_pypgx:
            steps.append('PyPGx')
            
        if used_pharmcat:
            steps.append('PharmCAT')
            
        # Add final steps
        steps.append('Reports')
        
        if exported_to_fhir:
            steps.append('FHIR Export')
        
        # Create plain text representation
        text_parts = []
        text_parts.append('Workflow Summary')
        text_parts.append('')
        
        for i, step in enumerate(steps):
            # Add step number and name
            text_parts.append(f'{i+1}. {step}')
            
            # Add arrow (except after last step)
            if i < len(steps) - 1:
                text_parts.append('↓')
                text_parts.append('')
        
        # Join with newlines and wrap in a simple div
        plain_text = '\n'.join(text_parts)
        return f'<div style="font-family: monospace; white-space: pre; text-align: center; padding: 20px; background-color: #f8f9fa; border: 1px solid #dee2e6; border-radius: 5px;">{plain_text}</div>'
        
    except Exception as e:
        logger.error(f"Error building plain text workflow: {str(e)}")
        # Return a simple fallback
        return '<div style="font-family: monospace; padding: 20px; text-align: center;">Workflow diagram could not be generated</div>'



