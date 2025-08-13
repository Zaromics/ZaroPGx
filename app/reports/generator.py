import os
import json
import logging
from datetime import datetime
import re
import base64
from typing import List, Dict, Any
import requests
from weasyprint import HTML, CSS
from jinja2 import Environment, FileSystemLoader, select_autoescape
from app.pharmcat.pharmcat_client import normalize_pharmcat_results
from app.visualizations.workflow_diagram import (
    render_workflow,
    render_workflow_png_data_uri,
    build_simple_html_from_workflow,
    render_simple_png_from_workflow,
)

# Version
# Do not hardcode; derive from pyproject when available
__version__ = "0.0.0"


def _read_version_from_pyproject() -> str:
    try:
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        pyproject_path = os.path.join(project_root, "pyproject.toml")
        if not os.path.exists(pyproject_path):
            return __version__
        with open(pyproject_path, "r", encoding="utf-8") as f:
            content = f.read()
        # Extract version from [project] section
        project_section_match = re.search(r"\[project\](.*?)\n\[", content, flags=re.DOTALL)
        section = project_section_match.group(1) if project_section_match else content
        version_match = re.search(r"^\s*version\s*=\s*\"([^\"]+)\"", section, flags=re.MULTILINE)
        return version_match.group(1).strip() if version_match else __version__
    except Exception:
        return __version__


def get_zaropgx_version() -> str:
    # Allow override via environment for reproducibility/testing
    env_version = os.getenv("ZAROPGX_VERSION")
    if env_version:
        return env_version
    return _read_version_from_pyproject()


def _read_author_from_pyproject() -> str:
    try:
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        pyproject_path = os.path.join(project_root, "pyproject.toml")
        if not os.path.exists(pyproject_path):
            return "Unknown Author"
        with open(pyproject_path, "r", encoding="utf-8") as f:
            content = f.read()
        authors_block_match = re.search(r"^\s*authors\s*=\s*\[(.*?)\]", content, flags=re.DOTALL | re.MULTILINE)
        block = authors_block_match.group(1) if authors_block_match else content
        name_match = re.search(r"name\s*=\s*\"([^\"]+)\"", block)
        if name_match:
            return name_match.group(1).strip()
        return "Unknown Author"
    except Exception:
        return "Unknown Author"


def get_author_name() -> str:
    env_author = os.getenv("AUTHOR_NAME")
    if env_author:
        return env_author
    return _read_author_from_pyproject()


def get_license_name() -> str:
    return "GNU Affero General Public License v3.0"


def get_license_url() -> str:
    return "https://www.gnu.org/licenses/agpl-3.0.html"


def get_source_url() -> str:
    return os.getenv("SOURCE_URL", "https://github.com/Zaroganos/ZaroPGx")


def _normalize_version_text(version_text: str) -> str:
    """Extract a clean, numeric version string from arbitrary text.

    Examples:
    - "The Genome Analysis Toolkit () v4.6.1.0" -> "4.6.1.0"
    - "v6.8.0" -> "6.8.0"
    - "3.0.0" -> "3.0.0"
    - "N/A" -> "N/A"
    """
    if not version_text:
        return "N/A"
    text = str(version_text).strip()
    # Find the first dotted numeric sequence (at least major.minor)
    match = re.search(r"\d+(?:\.\d+)+", text)
    return match.group(0) if match else text


def _load_versions_from_shared_dir() -> List[Dict[str, str]]:
    """Read version manifests from the shared /data/versions directory if present."""
    versions_dir = os.path.join("/data", "versions")
    items: List[Dict[str, str]] = []
    try:
        if not os.path.isdir(versions_dir):
            return items
        for fname in os.listdir(versions_dir):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(versions_dir, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    name = str(data.get("name", os.path.splitext(fname)[0])).strip()
                    version = str(data.get("version", "N/A")).strip()
                    # Normalize version display for consistency
                    items.append({"name": name, "version": _normalize_version_text(version)})
            except Exception:
                continue
    except Exception:
        return items
    return items


def _get_hapi_fhir_version() -> str:
    """Retrieve HAPI FHIR server version from its CapabilityStatement.

    Tries to fetch <FHIR_SERVER_URL>/metadata and parse software.version.
    Falls back to environment variable HAPI_FHIR_VERSION or "N/A" if not available.
    """
    # Prefer explicit env var override if present
    env_ver = os.getenv("HAPI_FHIR_VERSION")
    if env_ver:
        return _normalize_version_text(env_ver)

    server_url = os.environ.get("FHIR_SERVER_URL", "http://fhir-server:8080/fhir")
    metadata_url = server_url.rstrip("/") + "/metadata"
    try:
        headers = {"Accept": "application/fhir+json"}
        resp = requests.get(metadata_url, headers=headers, timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            software = data.get("software") or {}
            version = software.get("version") or ""
            if version:
                return _normalize_version_text(version)
    except Exception:
        pass
    return "N/A"


def build_platform_info() -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    items.append({"name": "ZaroPGx", "version": get_zaropgx_version()})
    # Merge from /data/versions and repo data/versions (for dev)
    merged: Dict[str, str] = {}
    for src in (_load_versions_from_shared_dir,):
        for it in src():
            name = it.get("name", "").strip()
            ver = _normalize_version_text(it.get("version", "N/A"))
            if name:
                merged.setdefault(name.lower(), ver)
    # Try repo data/versions as well
    try:
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        repo_versions_dir = os.path.join(project_root, "data", "versions")
        if os.path.isdir(repo_versions_dir):
            for fname in os.listdir(repo_versions_dir):
                if not fname.endswith(".json"):
                    continue
                fpath = os.path.join(repo_versions_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        name = str(data.get("name", os.path.splitext(fname)[0])).strip()
                        ver = _normalize_version_text(str(data.get("version", "N/A")).strip())
                        if name:
                            merged.setdefault(name.lower(), ver)
                except Exception:
                    continue
    except Exception:
        pass
    # Append merged entries
    for key, ver in merged.items():
        display = {"gatk": "GATK", "pharmcat": "PharmCAT", "pypgx": "PyPGx"}.get(key, key.capitalize())
        items.append({"name": display, "version": ver})
    # Add HAPI FHIR Server version
    # Only add if not already provided by a versions manifest
    lower_names = {i.get("name", "").lower() for i in items}
    if not any(name in lower_names for name in ("hapi fhir", "hapi fhir server", "fhir", "hapi")):
        items.append({"name": "HAPI FHIR Server", "version": _get_hapi_fhir_version()})

    # Ensure all version strings are normalized and not empty
    for item in items:
        item["version"] = _normalize_version_text(item.get("version", "N/A"))
    return items


def _versions_index() -> Dict[str, str]:
    idx: Dict[str, str] = {}
    for item in _load_versions_from_shared_dir():
        name = item.get("name", "").strip()
        ver = _normalize_version_text(item.get("version", "").strip())
        if name:
            idx[name.lower()] = ver
    # Include ZaroPGx and HAPI FHIR in the index for citation use if needed
    idx.setdefault("zaropgx", _normalize_version_text(get_zaropgx_version()))
    idx.setdefault("hapi fhir server", _normalize_version_text(_get_hapi_fhir_version()))
    return idx


def build_citations() -> List[Dict[str, str]]:
    """Build academically styled citations with versions (when available)."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    vmap = _versions_index()
    pypgx_ver = vmap.get("pypgx") or "N/A"
    pharmcat_ver = vmap.get("pharmcat") or "N/A"
    gatk_ver = vmap.get("gatk") or "N/A"

    citations: List[Dict[str, str]] = []
    citations.append({
        "name": "PyPGx",
        "text": f"PyPGx, version {pypgx_ver}. Available at: https://pypgx.readthedocs.io/ (accessed {today}). Citation guidance: https://github.com/sbslee/pypgx?tab=readme-ov-file#citation",
        "link": "https://github.com/sbslee/pypgx?tab=readme-ov-file#citation",
    })
    citations.append({
        "name": "PharmCAT",
        "text": f"PharmCAT (Pharmacogenomics Clinical Annotation Tool), version {pharmcat_ver}. Available at: https://github.com/PharmGKB/PharmCAT (accessed {today}).",
        "link": "https://github.com/PharmGKB/PharmCAT",
    })
    citations.append({
        "name": "GATK",
        "text": f"Genome Analysis Toolkit (GATK), version {gatk_ver}. Broad Institute. Available at: https://gatk.broadinstitute.org/ (accessed {today}).",
        "link": "https://gatk.broadinstitute.org/",
    })
    citations.append({
        "name": "CPIC",
        "text": f"Clinical Pharmacogenetics Implementation Consortium (CPIC). Available at: https://cpicpgx.org/ (accessed {today}).",
        "link": "https://cpicpgx.org/",
    })
    citations.append({
        "name": "PharmGKB",
        "text": f"Pharmacogenomics Knowledgebase (PharmGKB). Available at: https://www.pharmgkb.org/ (accessed {today}).",
        "link": "https://www.pharmgkb.org/",
    })
    return citations

# Report display configuration
# Controls which reports are included in the response and shown to users
REPORT_CONFIG = {
    # Our generated reports
    "show_pdf_report": True,          # Standard PDF report
    "show_html_report": True,         # Standard HTML report -- but isn't this just what is made into the PDF report with weasyprint?
    "show_interactive_report": True,  # Interactive HTML report with JavaScript visualizations

    # Visualization assets
    "write_workflow_svg": True,       # Write workflow.svg alongside report outputs
    "write_workflow_png": True,       # Also write workflow.png for robust PDF embedding

    # PharmCAT original reports
    "show_pharmcat_html_report": True,  # Original HTML report from PharmCAT
    "show_pharmcat_json_report": False, # Original JSON report from PharmCAT
    "show_pharmcat_tsv_report": False,  # Original TSV report from PharmCAT
}

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
CSS_FILE = os.path.join(TEMPLATE_DIR, "style.css")

# Initialize Jinja2 environment
env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))

# Custom exceptions
class ReportGenerationError(Exception):
    """Exception raised when report generation fails."""
    pass

def generate_pdf_report(
    patient_id: str,
    report_id: str,
    diplotypes: List[Dict[str, Any]],
    recommendations: List[Dict[str, Any]],
    report_path: str,
    workflow: Dict[str, Any] | None = None,
) -> str:
    """
    Generate a PDF pharmacogenomic report.
    
    Args:
        patient_id: Patient identifier
        report_id: Report identifier
        diplotypes: List of diplotype results
        recommendations: List of drug recommendations
        report_path: Path to save the PDF report
        
    Returns:
        Path to the generated PDF file
    """
    try:
        logger.info(f"Generating PDF report for patient {patient_id}, report {report_id}")
        
        # Ensure the directory exists
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        
        # Prepare the report data
        platform = build_platform_info()
        report_data = {
            "patient_id": patient_id,
            "report_id": report_id,
            "report_date": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
            "diplotypes": diplotypes,
            "recommendations": recommendations,
            "disclaimer": get_disclaimer(),
            "platform_info": platform,
            "citations": build_citations(),
            "author_name": get_author_name(),
            "license_name": get_license_name(),
            "license_url": get_license_url(),
            "source_url": get_source_url(),
        }
        
        # Load and render the HTML template
        template = env.get_template("report_template.html")
        # Try to embed a workflow diagram; some renderers struggle with inline SVG.
        # Provide both SVG (preferred) and a PNG data URI fallback.
        workflow_svg: str = ""
        workflow_png_data_uri: str = ""
        # Try SVG via Kroki/Graphviz
        try:
            svg_bytes = render_workflow(fmt="svg", workflow=workflow)
            workflow_svg = svg_bytes.decode("utf-8", errors="ignore")
        except Exception:
            workflow_svg = ""
        try:
            workflow_png_data_uri = render_workflow_png_data_uri(workflow=workflow)
        except Exception:
            workflow_png_data_uri = ""
        # If dynamic rendering failed, try to backport the saved PNG from the HTML report
        if not workflow_svg and not workflow_png_data_uri:
            try:
                # The PDF path lives in /data/reports/<report_id>/<report_id>_pgx_report.pdf
                # Reuse that directory to find the pre-rendered PNG
                report_dir = os.path.dirname(report_path)
                png_path = os.path.join(report_dir, f"{report_id}_workflow.png")
                if os.path.exists(png_path):
                    import base64
                    with open(png_path, "rb") as pf:
                        b64 = base64.b64encode(pf.read()).decode("ascii")
                        workflow_png_data_uri = f"data:image/png;base64,{b64}"
            except Exception:
                pass
        if not workflow_svg and not workflow_png_data_uri:
            # Try pure-Python PNG rasterizer
            try:
                png_bytes_alt = render_simple_png_from_workflow(workflow)
                if png_bytes_alt:
                    import base64
                    b64 = base64.b64encode(png_bytes_alt).decode("ascii")
                    workflow_png_data_uri = f"data:image/png;base64,{b64}"
            except Exception:
                pass
        # Always produce a simple HTML fallback so the block is never empty
        try:
            workflow_html_fallback = build_simple_html_from_workflow(workflow)
            if not workflow_html_fallback:
                workflow_html_fallback = "<em>Workflow: Upload → Detect → VCF → PharmCAT → Reports</em>"
        except Exception:
            workflow_html_fallback = "<em>Workflow: Upload → Detect → VCF → PharmCAT → Reports</em>"

        # If nothing rendered, force the fallback into the primary slot to guarantee display
        if not workflow_svg and not workflow_png_data_uri and workflow_html_fallback:
            workflow_svg = workflow_html_fallback

        html_content = template.render(
            **report_data,
            workflow_svg=workflow_svg,
            workflow_png_data_uri=workflow_png_data_uri,
            workflow_html_fallback=workflow_html_fallback,
        )
        
        try:
            # Generate PDF from HTML
            generate_pdf_from_html(html_content, report_path)
            logger.info(f"PDF report generated successfully: {report_path}")
            return report_path
        except Exception as pdf_error:
            # If PDF generation fails, create an HTML file as fallback
            logger.error(f"PDF generation failed, creating HTML fallback: {str(pdf_error)}")
            html_fallback_path = report_path.replace('.pdf', '.html')
            with open(html_fallback_path, 'w', encoding='utf-8') as f:
                f.write(html_content)
            logger.info(f"HTML fallback report generated: {html_fallback_path}")
            return html_fallback_path
    
    except Exception as e:
        logger.error(f"Error generating report: {str(e)}")
        raise

def generate_pdf_from_html(html_content: str, output_path: str) -> None:
    """
    Generate PDF from HTML content using WeasyPrint.
    
    Args:
        html_content: HTML content to convert
        output_path: Path to save the PDF
    """
    try:
        # Load CSS if it exists
        css = None
        if os.path.exists(CSS_FILE):
            css = CSS(filename=CSS_FILE)
        
        # Generate PDF - Using string_io instead of string to avoid passing positional arguments
        html = HTML(string=html_content)
        if css:
            html.write_pdf(target=output_path, stylesheets=[css])
        else:
            html.write_pdf(target=output_path)
    except Exception as e:
        logger.error(f"Error converting HTML to PDF: {str(e)}")
        # Create simple text file as fallback if PDF generation fails
        try:
            with open(output_path.replace('.pdf', '.txt'), 'w') as f:
                f.write(f"PDF GENERATION FAILED: {str(e)}\n\nRaw HTML content below:\n\n")
                f.write(html_content)
            logger.info(f"Created fallback text file at {output_path.replace('.pdf', '.txt')}")
        except:
            pass
        raise

def get_disclaimer() -> str:
    """
    Return the legal disclaimer for pharmacogenomic reports.
    """
    return """
    DISCLAIMER: This pharmacogenomic report is for informational purposes only. It is not intended
    to be used as a substitute for professional medical advice, diagnosis, or treatment. The content
    is based on guidelines from the Clinical Pharmacogenetics Implementation Consortium (CPIC) and
    may change as new research becomes available.

    Results pertain to the submitted sample and should be interpreted by qualified professionals in
    the appropriate clinical or research context. Decisions should not be made solely on the basis of
    this report without consultation with a qualified professional.
    """

def organize_gene_drug_recommendations(recommendations: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """
    Organize recommendations by gene and drug for better presentation.
    
    Args:
        recommendations: List of drug recommendations
        
    Returns:
        Dictionary organized by gene and drug
    """
    organized = {}
    
    for rec in recommendations:
        # Get gene(s) - could be under 'gene' or 'genes' in different formats
        gene = rec.get("gene") if rec.get("gene") else rec.get("genes", "Unknown")
        drug = rec.get("drug", "Unknown")
        
        # If genes is a comma-separated string, use the first gene
        if isinstance(gene, str) and "," in gene:
            gene = gene.split(",")[0].strip()
        
        if gene not in organized:
            organized[gene] = {}
            
        if drug not in organized[gene]:
            organized[gene][drug] = []
            
        organized[gene][drug].append(rec)
    
    return organized

def create_interactive_html_report(
    patient_id: str,
    report_id: str,
    diplotypes: List[Dict[str, Any]],
    recommendations: List[Dict[str, Any]],
    output_path: str,
    workflow: Dict[str, Any] | None = None
) -> str:
    """
    Create an interactive HTML report with JavaScript visualizations.
    
    Args:
        patient_id: Patient identifier
        report_id: Report identifier
        diplotypes: List of diplotype results
        recommendations: List of drug recommendations
        output_path: Path to save the HTML report
        
    Returns:
        Path to the generated HTML file
    """
    try:
        logger.info(f"Generating interactive HTML report for patient {patient_id}, report {report_id}")
        
        # Ensure the directory exists
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        # Map recommendations if they're in the new format
        template_recommendations = recommendations
        if recommendations and "genes" in recommendations[0] and "gene" not in recommendations[0]:
            template_recommendations = map_recommendations_for_template(recommendations)
        
        # Prepare workflow assets with robust fallbacks
        workflow_png_url = ""
        workflow_png_data_uri = ""
        workflow_svg_inline = ""
        workflow_html_fallback = ""
        report_dir = os.path.dirname(output_path)
        try:
            # Prefer a pre-rendered PNG served by the app
            png_path_local = os.path.join(report_dir, f"{report_id}_workflow.png")
            if os.path.exists(png_path_local):
                workflow_png_url = f"/reports/{report_id}/{report_id}_workflow.png"
        except Exception:
            workflow_png_url = ""
        if not workflow_png_url:
            # Try to render a PNG, save it, and expose as URL
            try:
                png_bytes = render_workflow(fmt="png", workflow=workflow)
                if png_bytes:
                    with open(os.path.join(report_dir, f"{report_id}_workflow.png"), "wb") as f_out:
                        f_out.write(png_bytes)
                    workflow_png_url = f"/reports/{report_id}/{report_id}_workflow.png"
            except Exception:
                workflow_png_url = ""
        if not workflow_png_url:
            # Fall back to data-URI PNG
            try:
                workflow_png_data_uri = render_workflow_png_data_uri(workflow=workflow)
            except Exception:
                workflow_png_data_uri = ""
        if not workflow_png_url and not workflow_png_data_uri:
            # Try pure-Python Pillow PNG
            try:
                from app.visualizations.workflow_diagram import render_simple_png_from_workflow
                png_bytes = render_simple_png_from_workflow(workflow)
                if png_bytes:
                    import base64
                    b64 = base64.b64encode(png_bytes).decode("ascii")
                    workflow_png_data_uri = f"data:image/png;base64,{b64}"
            except Exception:
                pass
        if not workflow_png_url and not workflow_png_data_uri:
            # Try inline SVG as a last renderer option
            try:
                svg_bytes = render_workflow(fmt="svg", workflow=workflow)
                workflow_svg_inline = svg_bytes.decode("utf-8", errors="ignore") if svg_bytes else ""
            except Exception:
                workflow_svg_inline = ""
        if not (workflow_png_url or workflow_png_data_uri or workflow_svg_inline):
            # Final: HTML breadcrumb fallback
            try:
                workflow_html_fallback = build_simple_html_from_workflow(workflow)
            except Exception:
                workflow_html_fallback = ""

        # Prepare the report data
        report_data = {
            "patient_id": patient_id,
            "report_id": report_id,
            "report_date": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
            "diplotypes": diplotypes,
            "recommendations": template_recommendations,
            "organized_recommendations": json.dumps(organize_gene_drug_recommendations(template_recommendations)),
            "disclaimer": get_disclaimer(),
            "workflow_png_url": workflow_png_url,
            "workflow_png_data_uri": workflow_png_data_uri,
            "workflow_svg": workflow_svg_inline,
            "workflow_html_fallback": workflow_html_fallback,
            "platform_info": build_platform_info(),
            "citations": build_citations(),
            "author_name": get_author_name(),
            "license_name": get_license_name(),
            "license_url": get_license_url(),
            "source_url": get_source_url(),
        }
        
        # Load and render the HTML template
        template = env.get_template("interactive_report.html")
        html_content = template.render(**report_data)
        
        # Write HTML to file
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        
        logger.info(f"Interactive HTML report generated successfully: {output_path}")
        return output_path
    except Exception as e:
        logger.error(f"Error generating interactive HTML report: {str(e)}")
        raise

def map_recommendations_for_template(drug_recommendations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Map drug recommendations from PharmCAT format to template-compatible format.
    
    Args:
        drug_recommendations: List of drug recommendations from normalized PharmCAT results
        
    Returns:
        Template-compatible recommendations
    """
    mapped_recommendations = []
    
    for rec in drug_recommendations:
        # Create a recommendation object with fields required by the template
        mapped_rec = {
            "drug": rec.get("drug", "Unknown"),
            "gene": rec.get("genes", ""),  # Use the genes field from our normalized format
            "recommendation": rec.get("recommendation", "See report for details"),
            "classification": rec.get("classification", "Unknown"),
            "literature_references": []  # Default empty list since templates check for this
        }
        
        # Add implications as references if available
        if "implications" in rec and rec["implications"]:
            if isinstance(rec["implications"], list):
                mapped_rec["literature_references"] = rec["implications"]
            elif isinstance(rec["implications"], str):
                # Split by semicolon if it's a string
                mapped_rec["literature_references"] = [imp.strip() for imp in rec["implications"].split(";") if imp.strip()]
        
        mapped_recommendations.append(mapped_rec)
    
    return mapped_recommendations

def generate_report(pharmcat_results: Dict[str, Any], output_dir: str, patient_info: Dict[str, Any] = None) -> Dict[str, str]:
    """
    Generate a report from PharmCAT results
    
    Args:
        pharmcat_results: Results from PharmCAT
        output_dir: Directory to write report files to
        patient_info: Optional patient information
        
    Returns:
        Dict containing file paths for all enabled reports
    """
    logger.info("Generating report from PharmCAT results")
    
    # Ensure normalized data is used consistently
    normalized_results = normalize_pharmcat_results(pharmcat_results)
    data = normalized_results["data"]
    
    # Map recommendations to template-compatible format
    template_recommendations = map_recommendations_for_template(data.get("drugRecommendations", []))
    
    # Prepare the template data
    # Build platform info once and include in both outputs
    platform = build_platform_info()

    template_data = {
        "patient": patient_info or {},
        "report_date": datetime.now().strftime("%Y-%m-%d"),
        "genes": data.get("genes", []),
        "diplotypes": data.get("genes", []),  # For compatibility with template
        "recommendations": template_recommendations,  # Use mapped recommendations
        "drug_recommendations": data.get("drugRecommendations", []),  # Keep original for reference
        "version": get_zaropgx_version(),
        "platform_info": platform,
        "citations": build_citations(),
        "author_name": get_author_name(),
        "license_name": get_license_name(),
        "license_url": get_license_url(),
        "source_url": get_source_url(),
    }
    
    # Get patient and report IDs
    patient_id = patient_info.get("id", "unknown") if patient_info else "unknown"
    # Use report_id from patient_info or generate one if not available
    report_id = patient_info.get("report_id", patient_id) if patient_info else patient_id
    
    # Create a report-specific directory using report_id as the directory name
    # This matches the approach in upload_router.py
    report_dir = os.path.join(output_dir, report_id)
    os.makedirs(report_dir, exist_ok=True)
    logger.info(f"Created report directory: {report_dir}")

    # Determine per-sample workflow for dynamic diagram from explicit flags or inference
    file_type = str(data.get("file_type", "vcf")).lower()
    used_gatk_flag = bool(data.get("used_gatk", False))
    used_pypgx_flag = bool(data.get("used_pypgx", False))
    # Infer usage conservatively: only show as used if we actually ran the step or if upstream recorded it
    inferred_gatk = used_gatk_flag or file_type in {"bam", "cram", "sam"} and bool(data.get("gatk_output_path"))
    inferred_pypgx = used_pypgx_flag and any(g for g in data.get("genes", []) if isinstance(g, dict) and g.get("gene", "").upper() == "CYP2D6")

    per_sample_workflow = {
        "file_type": file_type,
        "extracted_file_type": data.get("extracted_file_type"),
        "used_gatk": inferred_gatk,
        "used_pypgx": inferred_pypgx,
        "used_pharmcat": True,
        "exported_to_fhir": False,
    }

    # Optionally write workflow images alongside the report outputs
    workflow_svg_filename = f"{report_id}_workflow.svg"
    workflow_png_filename = f"{report_id}_workflow.png"
    try:
        if REPORT_CONFIG.get("write_workflow_svg", True):
            svg_bytes = render_workflow(fmt="svg", workflow=per_sample_workflow)
            if svg_bytes:
                with open(os.path.join(report_dir, workflow_svg_filename), "wb") as f_out:
                    f_out.write(svg_bytes)
            else:
                logger.warning("Workflow SVG empty; skipping file write")
    except Exception:
        logger.warning("Could not render workflow SVG; continuing without")
    try:
        if REPORT_CONFIG.get("write_workflow_png", False):
            png_bytes = render_workflow(fmt="png", workflow=per_sample_workflow)
            if not png_bytes:
                # Force pure-Python PNG fallback so a file is always present
                from app.visualizations.workflow_diagram import render_simple_png_from_workflow
                png_bytes = render_simple_png_from_workflow(per_sample_workflow)
            if png_bytes:
                with open(os.path.join(report_dir, workflow_png_filename), "wb") as f_out:
                    f_out.write(png_bytes)
            else:
                logger.warning("Workflow PNG still empty; skipping file write")
    except Exception as e:
        logger.warning(f"Could not render workflow PNG; continuing without ({str(e)})")
    
    # Create a unique filename based on report_id
    base_filename = f"{report_id}_pgx_report"
    
    try:
        # Generate both standard HTML report and interactive HTML report
        html_path = os.path.join(report_dir, f"{base_filename}.html")
        interactive_html_path = os.path.join(report_dir, f"{base_filename}_interactive.html")
        
        # Initialize the report paths dictionary that will be returned
        report_paths = {}
        
        # Generate standard HTML report if enabled
        if REPORT_CONFIG["show_html_report"]:
            env = Environment(
                loader=FileSystemLoader(os.path.join(os.path.dirname(__file__), "templates")),
                autoescape=select_autoescape(['html', 'xml'])
            )
            template = env.get_template("report_template.html")
            # Prepare workflow visuals for HTML, preferring pre-rendered files
            workflow_svg_in_html = ""
            workflow_png_data_uri_html = ""
            workflow_html_fallback_html = ""

            # Prefer previously written assets
            svg_path = os.path.join(report_dir, workflow_svg_filename)
            png_path = os.path.join(report_dir, workflow_png_filename)

            if os.path.exists(svg_path):
                try:
                    with open(svg_path, "r", encoding="utf-8") as f_svg:
                        workflow_svg_in_html = f_svg.read()
                except Exception:
                    workflow_svg_in_html = ""
            if not workflow_svg_in_html and os.path.exists(png_path):
                try:
                    with open(png_path, "rb") as f_png:
                        b64 = base64.b64encode(f_png.read()).decode("ascii")
                        workflow_png_data_uri_html = f"data:image/png;base64,{b64}"
                except Exception:
                    workflow_png_data_uri_html = ""

            # If no local assets available, try dynamic renderers and finally HTML fallback
            if not workflow_svg_in_html and not workflow_png_data_uri_html:
                try:
                    svg_bytes = render_workflow(fmt="svg", workflow=per_sample_workflow)
                    workflow_svg_in_html = svg_bytes.decode("utf-8", errors="ignore")
                except Exception:
                    workflow_svg_in_html = ""
                if not workflow_svg_in_html:
                    try:
                        workflow_png_data_uri_html = render_workflow_png_data_uri(workflow=per_sample_workflow)
                    except Exception:
                        workflow_png_data_uri_html = ""
                if not workflow_svg_in_html and not workflow_png_data_uri_html:
                    try:
                        workflow_html_fallback_html = build_simple_html_from_workflow(per_sample_workflow)
                    except Exception:
                        workflow_html_fallback_html = ""

            # Build debug veneer payload
            try:
                debug_info = {
                    "per_sample_workflow": per_sample_workflow,
                    "workflow_render": {
                        "pre_svg_path_exists": bool(os.path.exists(svg_path)),
                        "pre_png_path_exists": bool(os.path.exists(png_path)),
                        "used_inline_svg": bool(workflow_svg_in_html),
                        "used_png_data_uri": bool(workflow_png_data_uri_html),
                        "used_html_fallback": bool(workflow_html_fallback_html),
                    },
                    "platform_info": platform,
                    "footer_context": {
                        "author_name": template_data.get("author_name"),
                        "license_name": template_data.get("license_name"),
                        "license_url": template_data.get("license_url"),
                        "source_url": template_data.get("source_url"),
                    },
                }
                debug_json = json.dumps(debug_info, indent=2)
            except Exception:
                debug_json = "{}"

            html_content = template.render(
                **template_data,
                workflow_svg=workflow_svg_in_html,
                workflow_png_data_uri=workflow_png_data_uri_html,
                workflow_html_fallback=workflow_html_fallback_html,
                debug_json=debug_json,
            )
            
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html_content)
            logger.info(f"HTML report generated: {html_path}")
            
            # Add to report paths
            server_html_path = f"/reports/{report_id}/{base_filename}.html"
            report_paths["html_path"] = server_html_path
            normalized_results["data"]["html_report_url"] = server_html_path

            # Surface workflow asset URLs if present
            svg_path = os.path.join(report_dir, workflow_svg_filename)
            if os.path.exists(svg_path):
                report_paths["workflow_svg_path"] = f"/reports/{report_id}/{workflow_svg_filename}"
                normalized_results["data"]["workflow_svg_url"] = report_paths["workflow_svg_path"]
            png_path = os.path.join(report_dir, workflow_png_filename)
            if os.path.exists(png_path):
                report_paths["workflow_png_path"] = f"/reports/{report_id}/{workflow_png_filename}"
                normalized_results["data"]["workflow_png_url"] = report_paths["workflow_png_path"]
        
        # Generate interactive HTML report if enabled
        if REPORT_CONFIG["show_interactive_report"]:
            create_interactive_html_report(
                patient_id=patient_id,
                report_id=report_id,
                diplotypes=data.get("genes", []),
                recommendations=template_recommendations,
                output_path=interactive_html_path,
                workflow=per_sample_workflow
            )
            logger.info(f"Interactive HTML report generated: {interactive_html_path}")
            
            # Add to report paths
            server_interactive_html_path = f"/reports/{report_id}/{base_filename}_interactive.html"
            report_paths["interactive_html_path"] = server_interactive_html_path
            normalized_results["data"]["interactive_html_report_url"] = server_interactive_html_path
        
        # Generate PDF report from the HTML if enabled
        if REPORT_CONFIG["show_pdf_report"]:
            pdf_path = os.path.join(report_dir, f"{base_filename}.pdf")
            html = HTML(string=html_content if REPORT_CONFIG["show_html_report"] else template.render(**template_data))
            html.write_pdf(pdf_path)
            logger.info(f"PDF report generated: {pdf_path}")
            
            # Add to report paths
            server_pdf_path = f"/reports/{report_id}/{base_filename}.pdf"
            report_paths["pdf_path"] = server_pdf_path
            normalized_results["data"]["pdf_report_url"] = server_pdf_path
        
        # Include PharmCAT original reports if enabled
        # Check if pharmacat report files already exist in the report directory
        pharmcat_html_filename = f"{report_id}_pgx_pharmcat.html"
        pharmcat_html_path = os.path.join(report_dir, pharmcat_html_filename)
        
        # PharmCAT HTML report
        if REPORT_CONFIG["show_pharmcat_html_report"]:
            # Look for the original PharmCAT HTML report
            pharmcat_html_file = os.path.join(report_dir, f"{report_id}.report.html")
            if os.path.exists(pharmcat_html_file):
                # Copy it with our standardized naming if it doesn't already exist
                if not os.path.exists(pharmcat_html_path):
                    import shutil
                    shutil.copy(pharmcat_html_file, pharmcat_html_path)
                    logger.info(f"PharmCAT HTML report copied to: {pharmcat_html_path}")
            
            # Add to report paths if the file exists
            if os.path.exists(pharmcat_html_path):
                server_pharmcat_html_path = f"/reports/{report_id}/{pharmcat_html_filename}"
                report_paths["pharmcat_html_path"] = server_pharmcat_html_path
                normalized_results["data"]["pharmcat_html_report_url"] = server_pharmcat_html_path
            else:
                logger.warning("PharmCAT HTML report not found in report directory")
        
        # PharmCAT JSON report
        if REPORT_CONFIG["show_pharmcat_json_report"]:
            pharmcat_json_filename = f"{report_id}_pgx_pharmcat.json"
            pharmcat_json_path = os.path.join(report_dir, pharmcat_json_filename)
            pharmcat_json_file = os.path.join(report_dir, f"{report_id}.report.json")
            
            if os.path.exists(pharmcat_json_file):
                if not os.path.exists(pharmcat_json_path):
                    import shutil
                    shutil.copy(pharmcat_json_file, pharmcat_json_path)
                    logger.info(f"PharmCAT JSON report copied to: {pharmcat_json_path}")
            
            if os.path.exists(pharmcat_json_path):
                server_pharmcat_json_path = f"/reports/{report_id}/{pharmcat_json_filename}"
                report_paths["pharmcat_json_path"] = server_pharmcat_json_path
                normalized_results["data"]["pharmcat_json_report_url"] = server_pharmcat_json_path
        
        # PharmCAT TSV report
        if REPORT_CONFIG["show_pharmcat_tsv_report"]:
            pharmcat_tsv_filename = f"{report_id}_pgx_pharmcat.tsv"
            pharmcat_tsv_path = os.path.join(report_dir, pharmcat_tsv_filename)
            pharmcat_tsv_file = os.path.join(report_dir, f"{report_id}.report.tsv")
            
            if os.path.exists(pharmcat_tsv_file):
                if not os.path.exists(pharmcat_tsv_path):
                    import shutil
                    shutil.copy(pharmcat_tsv_file, pharmcat_tsv_path)
                    logger.info(f"PharmCAT TSV report copied to: {pharmcat_tsv_path}")
            
            if os.path.exists(pharmcat_tsv_path):
                server_pharmcat_tsv_path = f"/reports/{report_id}/{pharmcat_tsv_filename}"
                report_paths["pharmcat_tsv_path"] = server_pharmcat_tsv_path
                normalized_results["data"]["pharmcat_tsv_report_url"] = server_pharmcat_tsv_path
        
        # Add the normalized results to the report paths
        report_paths["normalized_results"] = normalized_results
        
        return report_paths
    
    except Exception as e:
        logger.error(f"Error generating report: {str(e)}")
        raise ReportGenerationError(f"Failed to generate report: {str(e)}") 