import os
import json
import logging
import shutil
import uuid
from datetime import datetime
import re
import base64
from typing import List, Dict, Any
import glob

from weasyprint import HTML, CSS
try:
    # WeasyPrint >= 53
    from weasyprint.text.fonts import FontConfiguration  # type: ignore
except Exception:
    try:
        # WeasyPrint <= 52.x
        from weasyprint.fonts import FontConfiguration  # type: ignore
    except Exception:
        FontConfiguration = None  # type: ignore

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.pharmcat.pharmcat_client import normalize_pharmcat_results
from app.visualizations.workflow_diagram import (
    render_workflow,
    render_workflow_png_data_uri,
    build_simple_html_from_workflow,
    render_simple_png_from_workflow,
    render_kroki_mermaid_svg,
    render_with_graphviz,
)

from app.core.version_manager import get_all_versions, get_versions_dict

# Import for pharmcat TSV parsing (used in multiple functions)
from app.reports.pharmcat_tsv_parser import parse_pharmcat_tsv
from app.reports.pdf_generators import generate_pdf_report_dual_lane
from app.reports.pypgx_pipeline_parser import parse_gene_pipeline

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
            return "Zaromics Initiative"
        with open(pyproject_path, "r", encoding="utf-8") as f:
            content = f.read()
        authors_block_match = re.search(r"^\s*authors\s*=\s*\[(.*?)\]", content, flags=re.DOTALL | re.MULTILINE)
        block = authors_block_match.group(1) if authors_block_match else content
        name_match = re.search(r"name\s*=\s*\"([^\"]+)\"", block)
        if name_match:
            return name_match.group(1).strip()
        return "Zaromics Initiative"
    except Exception:
        return "Zaromics Initiative"


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
    - "The Genome Analysis Toolkit () v4.6.2.0" -> "4.6.2.0"
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


def _sanitize_graphviz_svg(svg_str: str) -> str:
    """Make Graphviz SVG responsive-friendly for HTML/PDF rendering.

    - Remove absolute width/height attributes
    - Ensure preserveAspectRatio is set to keep centering
    - Ensure text elements are visible and properly styled
    """
    try:
        # Remove width/height attributes on root <svg>
        svg_str = re.sub(r'(<svg[^>]*?)\s+width="[^"]+"', r"\1", svg_str, count=1)
        svg_str = re.sub(r'(<svg[^>]*?)\s+height="[^"]+"', r"\1", svg_str, count=1)
        
        # Add preserveAspectRatio if missing
        if "preserveAspectRatio" not in svg_str[:200]:
            svg_str = svg_str.replace("<svg", "<svg preserveAspectRatio=\"xMidYMid meet\"", 1)
        
        # Ensure text elements have proper styling and presentation attributes
        # Fix any style fill:none on text/tspan
        svg_str = re.sub(r'(<text[^>]*style=["\"][^"\"]*)fill\s*:\s*none\s*;?', r'\1fill:#000000;', svg_str)
        svg_str = re.sub(r'(<tspan[^>]*style=["\"][^"\"]*)fill\s*:\s*none\s*;?', r'\1fill:#000000;', svg_str)
        # Add presentation attributes to text/tspan for WeasyPrint
        svg_str = re.sub(r'<text([^>]*?)>', r'<text\1 fill="#000000" font-family="Arial, sans-serif" font-size="12px">', svg_str)
        svg_str = re.sub(r'<tspan([^>]*?)>', r'<tspan\1 fill="#000000" font-family="Arial, sans-serif" font-size="12px">', svg_str)
        
        return svg_str
    except Exception:
        return svg_str


def build_platform_info() -> List[Dict[str, str]]:
    """Build platform information using centralized version management."""
    items = []
    
    # Add ZaroPGx version
    items.append({"name": "ZaroPGx", "version": get_zaropgx_version()})
    
    # Get all versions from centralized manager
    all_versions = get_all_versions()
    
    # Add service versions, avoiding duplicates
    seen_names = {"zaropgx"}
    for version_info in all_versions:
        name = version_info.get("name", "").strip()
        version = version_info.get("version", "N/A").strip()
        source = version_info.get("source", "unknown")
        
        if name and name.lower() not in seen_names:
            seen_names.add(name.lower())
            items.append({
                "name": name,
                "version": _normalize_version_text(version),
                "source": source
            })
    
    # Ensure all version strings are normalized
    for item in items:
        item["version"] = _normalize_version_text(item.get("version", "N/A"))
    
    return items


def _versions_index() -> Dict[str, str]:
    """Get versions index using centralized version management."""
    return get_versions_dict()


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
        "text": f"PyPGx, version {pypgx_ver}. Available at: https://pypgx.readthedocs.io/en/latest/index.html (accessed {today}).",
    })
    citations.append({
        "name": "PharmCAT",
        "text": f"Pharmacogenomics Clinical Annotation Tool (PharmCAT), version {pharmcat_ver}. Available at: https://github.com/PharmGKB/PharmCAT (accessed {today}).",
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
        "name": "DWPG",
        "text": f"Dutch Pharmacogenomics Working Group. Available at: https://www.dwpg.nl/ (accessed {today}).",
        "link": "https://www.dwpg.nl/",
    })
    citations.append({
        "name": "FDA",
        "text": f"Food and Drug Administration PGx Guidelines. Available at: https://www.fda.gov/ (accessed {today}).",
        "link": "https://www.fda.gov/",
    })
    citations.append({
        "name": "MitoZ",
        "text": f"MitoZ-based mitochondrial typing. Available at: https://mitoz.readthedocs.io/en/stable/ (accessed {today}).",
        "link": "https://mitoz.readthedocs.io/en/stable/",
    })
    citations.append({
        "name": "PharmGKB",
        "text": f"Pharmacogenomics Knowledgebase (PharmGKB). Available at: https://www.pharmgkb.org/ (accessed {today}).",
        "link": "https://www.pharmgkb.org/",
    })
    citations.append({
        "name": "nf-core/hlatyping",
        "text": f"OptiType-based HLA typing using nf-core/hlatyping. Available at: https://nf-co.re/hlatyping (accessed {today}).",
        "link": "https://nf-co.re/hlatyping",
    })
    return citations

# Report display configuration
# Controls which reports are included in the response and shown to users
# 
# Configure logging first
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# PDF Generation Configuration
# Can be configured via environment variables:
# - PDF_ENGINE: 'weasyprint' (default) or 'reportlab'
# - PDF_FALLBACK: 'true' or 'false' (default: true)
PDF_ENGINE = os.environ.get("PDF_ENGINE", "weasyprint").lower()
PDF_FALLBACK = os.environ.get("PDF_FALLBACK", "true").lower() == "true"

# Validate PDF engine setting
if PDF_ENGINE not in ["weasyprint", "reportlab"]:
    logger.warning(f"Invalid PDF_ENGINE '{PDF_ENGINE}', defaulting to 'weasyprint'")
    PDF_ENGINE = "weasyprint"

logger.info(f"PDF Generation Configuration: Engine={PDF_ENGINE}, Fallback={PDF_FALLBACK}")

# Environment variable helper function
def _env_flag(name: str, default: bool = False) -> bool:
    """Helper function to read boolean environment variables."""
    val = os.getenv(name)
    if val is None:
        return default
    return str(val).strip().lower() in {"1", "true", "yes", "on"}

# Read PharmCAT report configuration from environment variables
INCLUDE_PHARMCAT_HTML = _env_flag("INCLUDE_PHARMCAT_HTML", True)
INCLUDE_PHARMCAT_JSON = _env_flag("INCLUDE_PHARMCAT_JSON", False)
INCLUDE_PHARMCAT_TSV = _env_flag("INCLUDE_PHARMCAT_TSV", False)
EXECSUM_USE_TSV = _env_flag("EXECSUM_USE_TSV", False)

# Log the configuration for debugging
logger.info(f"PharmCAT Report Configuration - HTML: {INCLUDE_PHARMCAT_HTML}, JSON: {INCLUDE_PHARMCAT_JSON}, TSV: {INCLUDE_PHARMCAT_TSV}")
logger.info(f"Executive Summary Configuration - Use TSV: {EXECSUM_USE_TSV}")

# Report configuration dictionary
REPORT_CONFIG = {
    # Core report settings
    "write_pdf": True,                # Generate PDF report
    "write_html": True,               # Generate HTML report
    "write_interactive_html": True,   # Generate interactive HTML report
    "write_json": True,               # Generate JSON export
    "write_tsv": True,                # Generate TSV export
    
    # Visualization assets
    "write_workflow_svg": True,       # Write workflow.svg alongside report outputs
    "write_workflow_png": True,       # Also write workflow.png for robust PDF embedding

    # PharmCAT original reports - now controlled by environment variables
    "show_pharmcat_html_report": INCLUDE_PHARMCAT_HTML,  # Original HTML report from PharmCAT
    "show_pharmcat_json_report": INCLUDE_PHARMCAT_JSON,  # Original JSON report from PharmCAT
    "show_pharmcat_tsv_report": INCLUDE_PHARMCAT_TSV,   # Original TSV report from PharmCAT
}

# Configure WeasyPrint logging for debugging text rendering issues
weasyprint_logger = logging.getLogger('weasyprint')
weasyprint_logger.setLevel(logging.DEBUG)
if not weasyprint_logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('[WEASYPRINT] %(levelname)s: %(message)s'))
    weasyprint_logger.addHandler(handler)

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
    sample_identifier: str | None = None,
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
        # Optionally prepare TSV exec summary rows when enabled
        execsum_rows_from_tsv: list[dict] = []
        try:
            from app.reports.generator import EXECSUM_USE_TSV  # type: ignore
        except Exception:
            EXECSUM_USE_TSV = False  # type: ignore
        if EXECSUM_USE_TSV:
            try:
                report_dir_probe = os.path.dirname(report_path)
                tsv_candidates = []
                # Local report directory
                tsv_candidates.append(os.path.join(report_dir_probe, f"{patient_id}_pgx_pharmcat.tsv"))
                tsv_candidates.append(os.path.join(report_dir_probe, f"{patient_id}.report.tsv"))
                try:
                    tsv_candidates.extend(glob.glob(os.path.join(report_dir_probe, "*_pgx_pharmcat.tsv")))
                    any_pharmcat = glob.glob(os.path.join(report_dir_probe, "*.pharmcat.tsv"))
                    if any_pharmcat:
                        any_pharmcat.sort(key=lambda p: os.path.getmtime(p), reverse=True)
                        tsv_candidates.extend(any_pharmcat)
                except Exception:
                    pass
                # Also probe parent directory of the report directory (the patient-level dir)
                try:
                    parent_dir = os.path.dirname(report_dir_probe)
                    tsv_candidates.append(os.path.join(parent_dir, f"{patient_id}_pgx_pharmcat.tsv"))
                    tsv_candidates.append(os.path.join(parent_dir, f"{patient_id}.report.tsv"))
                    tsv_candidates.extend(glob.glob(os.path.join(parent_dir, "*_pgx_pharmcat.tsv")))
                    any_pharmcat_parent = glob.glob(os.path.join(parent_dir, "*.pharmcat.tsv"))
                    if any_pharmcat_parent:
                        any_pharmcat_parent.sort(key=lambda p: os.path.getmtime(p), reverse=True)
                        tsv_candidates.extend(any_pharmcat_parent)
                except Exception:
                    pass
                tsv_path = next((p for p in tsv_candidates if os.path.exists(p)), None)
                if tsv_path:
                    _diplos, _ = parse_pharmcat_tsv(tsv_path)
                    try:
                        logger.info(f"Executive Summary TSV selected (PDF path): {tsv_path}")
                    except Exception:
                        pass
                    for row in _diplos:
                        execsum_rows_from_tsv.append({
                            "gene": row.get("gene", ""),
                            "rec_lookup_diplotype": row.get("rec_lookup_diplotype", ""),
                            "rec_lookup_phenotype": row.get("rec_lookup_phenotype", row.get("phenotype", "")),
                            "rec_lookup_activity_score": row.get("rec_lookup_activity_score"),
                        })
            except Exception:
                execsum_rows_from_tsv = []

        report_data = {
            "patient_id": patient_id,
            "report_id": report_id,
            "sample_identifier": sample_identifier if sample_identifier else patient_id,
            # New: explicit display sample id for templates that prefer it
            "display_sample_id": sample_identifier if sample_identifier else patient_id,
            "report_date": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
            "diplotypes": [
                {
                    **d,
                    "phenotype": (
                        "Possibly Wild Type"
                        if str(d.get("diplotype") or "").strip() in {"*1/*1", "Reference/Reference"} and (
                            str(d.get("phenotype") or "").strip() == "" or str(d.get("phenotype") or "").strip().lower() in {"n/a", "na"}
                        )
                        else d.get("phenotype")
                    )
                }
                if isinstance(d, dict) else d for d in diplotypes
            ],
            "recommendations": recommendations,
            "disclaimer": get_disclaimer(),
            "platform_info": platform,
            "citations": build_citations(),
            "author_name": get_author_name(),
            "license_name": get_license_name(),
            "license_url": get_license_url(),
            "source_url": get_source_url(),
            "header_text": "",
            # Pass TSV-driven Executive Summary rows if enabled and available
            "execsum_from_tsv": execsum_rows_from_tsv if (EXECSUM_USE_TSV and execsum_rows_from_tsv) else None,
        }
        
        # Load and render the HTML template
        template = env.get_template("report_template.html")
        # Try to embed a workflow diagram; some renderers struggle with inline SVG.
        # Provide both SVG (preferred) and a PNG data URI fallback.
        workflow_svg: str = ""
        workflow_png_data_uri: str = ""
        # DEBUG: Test all workflow diagram generation methods with WeasyPrint 66.0
        logger.info("=== PDF WORKFLOW DIAGRAM DEBUG START ===")
        logger.info(f"Workflow data: {workflow}")
        
        workflow_svg = ""
        workflow_png_data_uri = ""
        workflow_html_fallback = ""
        
        # For PDF generation, use SVG workflow diagrams to preserve text content
        # SVG preserves text as searchable/selectable content, while PNG is just raster pixels
        try:
            # First, try to get the SVG workflow image that was already generated
            report_dir = os.path.dirname(report_path)
            # Use patient_id for workflow filenames to match the new directory structure
            svg_path = os.path.join(report_dir, f"{patient_id}_workflow.svg")
            
            if os.path.exists(svg_path):
                # Read SVG content directly for PDF embedding
                with open(svg_path, "r", encoding="utf-8") as f_svg:
                    svg_content = f_svg.read()
                    workflow_svg = f'<div class="workflow-figure">{svg_content}</div>'
                    logger.info(f"✓ Using existing SVG workflow for PDF: {len(svg_content)} chars")
            else:
                # If SVG doesn't exist, generate it first
                logger.info("Generating SVG workflow for PDF...")
                svg_bytes = render_workflow(fmt="svg", workflow=workflow)
                if svg_bytes:
                    svg_content = svg_bytes.decode('utf-8')
                    workflow_svg = f'<div class="workflow-figure">{svg_content}</div>'
                    logger.info(f"✓ Generated SVG workflow for PDF: {len(svg_content)} chars")
                    
                    # Also save the SVG file for future use
                    with open(svg_path, "w", encoding="utf-8") as f_svg:
                        f_svg.write(svg_content)
                    logger.info(f"✓ Saved SVG workflow to: {svg_path}")
                else:
                    logger.warning("✗ SVG workflow generation failed, using fallback text")
                    workflow_svg = "<em>Workflow: Upload → Detect → VCF → PharmCAT → Reports</em>"
        except Exception as e:
            logger.error(f"✗ SVG workflow approach failed: {str(e)}", exc_info=True)
            workflow_svg = "<em>Workflow: Upload → Detect → VCF → PharmCAT → Reports</em>"
        logger.info(f"Final PDF workflow - HTML: {len(workflow_svg) if workflow_svg else 0}, PNG: {len(workflow_png_data_uri) if workflow_png_data_uri else 0}")
        logger.info("=== PDF WORKFLOW DIAGRAM DEBUG END ===")
        
        # Ensure we always have a workflow display
        if not workflow_svg:
            workflow_svg = "<em>Workflow: Upload → Detect → VCF → PharmCAT → Reports</em>"

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
        # Load CSS if it exists and configure fonts
        stylesheets = []
        # Add project stylesheet if present
        if os.path.exists(CSS_FILE):
            if FontConfiguration:
                stylesheets.append(CSS(filename=CSS_FILE, font_config=FontConfiguration()))
            else:
                stylesheets.append(CSS(filename=CSS_FILE))
        # Enhanced print-safe defaults via inline CSS for better workflow diagram handling
        # Focus on PNG image rendering which works reliably in WeasyPrint
        pdf_css = '''
            @page {
                size: A4;
                margin: 16mm;
                @bottom-right {
                    content: "Page " counter(page) " of " counter(pages);
                    font-size: 10px;
                    color: #888;
                }
            }
            img, svg { max-width: 100%; height: auto; }
            .workflow-figure { 
                page-break-inside: avoid; 
                break-inside: avoid; 
                margin-bottom: 15px; 
                text-align: center;
                max-height: 300px;
                overflow: visible;
            }
            .workflow-figure img { 
                max-width: 100% !important; 
                max-height: 280px !important;
                height: auto !important; 
                display: block !important;
                margin: 0 auto !important;
                border: 1px solid #dee2e6 !important;
                border-radius: 5px !important;
            }
            .workflow-figure svg { 
                max-width: 100% !important; 
                max-height: 280px !important;
                height: auto !important; 
                display: block;
                margin: 0 auto;
            }
            .section { 
                page-break-inside: avoid; 
                break-inside: avoid; 
            }
            /* Ensure PNG workflow images render properly in PDF */
            .workflow-figure img[src*="data:image/png"] {
                max-width: 100% !important;
                max-height: 280px !important;
                height: auto !important;
                display: block !important;
                margin: 0 auto !important;
                page-break-inside: avoid !important;
                break-inside: avoid !important;
            }
        '''
        if FontConfiguration:
            stylesheets.append(CSS(string=pdf_css, font_config=FontConfiguration()))
        else:
            stylesheets.append(CSS(string=pdf_css))

        # Generate PDF
        html = HTML(string=html_content)
        if FontConfiguration:
            html.write_pdf(target=output_path, stylesheets=stylesheets, font_config=FontConfiguration(), zoom=1.0)
        else:
            html.write_pdf(target=output_path, stylesheets=stylesheets, zoom=1.0)
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
    DISCLAIMER: This pharmacogenomic report is for informational purposes only. ZaroPGx is an independently 
    developed hobby software. It is not intended to be used as a substitute for professional medical advice, 
    diagnosis, or treatment. The content herein is based on guidelines from the Clinical Pharmacogenetics 
    Implementation Consortium (CPIC), the Pharmacogenomics Knowledgebase (PharmGKB), the Food and Drug 
    Administration (FDA), and the Dutch Pharmacogenetics Working Group (DPWG). The content may change as new 
    research becomes available. The content may vary depending on the sequencing or genotyping method used, 
    the quality and composition of the genomic data submitted, and the revision of the constituent software.


    Results should be considered in an educational context only. 
    Should the need for clinical or research interpretation arise, the nature of this report is such that it may serve 
    only as a prompt for professional investigation by a qualified physician or medical genetics practitioner.
    Responsible use of this report in such a context entails verification and validation of the findings by an  
    appropriately accredited sequencing or genotyping laboratory followed by interpretation and consultation 
    with an appropriately credentialed professional. 
    

    Please do not make any changes to your lifestyle or treatment regimen solely on the basis of this report.
    First, consult your trusted physician and verify the findings in the appropriate clinical manner.
    

    ZaroPGx is provided with absolutely no warranty, and the author(s) are not liable for any consequences of its use.
    If you choose to ignore the guidance herein, please be aware that you do so at your own risk.


    ZaroPGx is made with care, standing on the shoulders of the myriad underlying free software, in the hope that 
    it is useful to you. Thank you for using it, and thank you for supporting free and open source software.

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
    workflow: Dict[str, Any] | None = None,
    sample_identifier: str | None = None
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
        workflow_kroki_svg_inline = ""
        workflow_html_fallback = ""
        report_dir = os.path.dirname(output_path)
        # Patient directory is the directory containing the report files
        patient_dir = report_dir

        # Attempt to load genomic header text saved earlier in the pipeline
        header_text: str = ""
        try:
            header_txt_candidates = []
            # Prefer explicit names
            header_txt_candidates.append(os.path.join(patient_dir, f"{report_id}.header.txt"))
            header_txt_candidates.append(os.path.join(patient_dir, f"{patient_id}.header.txt"))
            # Add all *.header.txt
            try:
                header_txt_candidates.extend(glob.glob(os.path.join(patient_dir, "*.header.txt")))
            except Exception:
                pass
            # Filter to existing and pick newest by mtime
            existing = [p for p in header_txt_candidates if os.path.exists(p)]
            if existing:
                selected = max(existing, key=lambda p: os.path.getmtime(p))
                with open(selected, "r", encoding="utf-8", errors="ignore") as hf:
                    header_text = hf.read()
                logger.info(f"Loaded genomic header text for interactive report from {selected} ({len(header_text)} chars)")
        except Exception as _e:
            logger.debug(f"Interactive header text load skipped: {_e}")
        
        # Generate Kroki Mermaid SVG for comparison
        try:
            kroki_svg_bytes = render_kroki_mermaid_svg(workflow=workflow)
            if kroki_svg_bytes:
                kroki_svg_text = kroki_svg_bytes.decode("utf-8", errors="ignore")
                workflow_kroki_svg_inline = _sanitize_graphviz_svg(kroki_svg_text)
                logger.info(f"✓ Generated Kroki Mermaid SVG for interactive report: {len(workflow_kroki_svg_inline)} chars")
            else:
                logger.warning("⚠ Kroki Mermaid SVG generation returned empty result for interactive report")
        except Exception as e:
            logger.error(f"✗ Kroki Mermaid SVG generation failed for interactive report: {str(e)}")
            workflow_kroki_svg_inline = ""
        
        try:
            # Prefer a pre-rendered PNG served by the app
            # Use patient_id for the workflow.png filename to match the directory structure
            png_path_local = os.path.join(report_dir, f"{patient_id}_workflow.png")
            if os.path.exists(png_path_local):
                workflow_png_url = f"/reports/{patient_id}/{patient_id}_workflow.png"
        except Exception:
            workflow_png_url = ""
        if not workflow_png_url:
            # Try to render a PNG, save it, and expose as URL
            try:
                png_bytes = render_workflow(fmt="png", workflow=workflow)
                if png_bytes:
                    with open(os.path.join(report_dir, f"{patient_id}_workflow.png"), "wb") as f_out:
                        f_out.write(png_bytes)
                    workflow_png_url = f"/reports/{patient_id}/{patient_id}_workflow.png"
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
                png_bytes = render_simple_png_from_workflow(workflow)
                if png_bytes:
                    b64 = base64.b64encode(png_bytes).decode("ascii")
                    workflow_png_data_uri = f"data:image/png;base64,{b64}"
            except Exception:
                pass
        if not workflow_png_url and not workflow_png_data_uri:
            # Try inline SVG as a last renderer option
            try:
                svg_bytes = render_workflow(fmt="svg", workflow=workflow)
                if svg_bytes:
                    svg_text = svg_bytes.decode("utf-8", errors="ignore")
                    workflow_svg_inline = _sanitize_graphviz_svg(svg_text)
                else:
                    workflow_svg_inline = ""
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
            "sample_identifier": sample_identifier if sample_identifier else patient_id,
            "report_date": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
            "diplotypes": [
                {
                    **d,
                    "phenotype": (
                        "Possibly Wild Type"
                        if str(d.get("diplotype") or "").strip() in {"*1/*1", "Reference/Reference"} and (
                            str(d.get("phenotype") or "").strip() == "" or str(d.get("phenotype") or "").strip().lower() in {"n/a", "na"}
                        )
                        else d.get("phenotype")
                    )
                }
                if isinstance(d, dict) else d for d in diplotypes
            ],
            "recommendations": template_recommendations,
            "organized_recommendations": json.dumps(organize_gene_drug_recommendations(template_recommendations)),
            "disclaimer": get_disclaimer(),
            "workflow_png_url": workflow_png_url,
            "workflow_png_data_uri": workflow_png_data_uri,
            "workflow_svg": workflow_svg_inline,
            "workflow_html_fallback": workflow_html_fallback,
            "workflow_kroki_svg": workflow_kroki_svg_inline, # Added for interactive report
            "platform_info": build_platform_info(),
            "citations": build_citations(),
            "author_name": get_author_name(),
            "license_name": get_license_name(),
            "license_url": get_license_url(),
            "source_url": get_source_url(),
            "current_year": datetime.now().year,
            "header_text": header_text,
        }
        
        # Compute unified display sample id for Interactive; if it's UUID-like, derive from PharmCAT filenames
        def _is_uuid_like(s: str) -> bool:
            try:
                uuid.UUID(str(s))
                return True
            except Exception:
                return False

        display_sample = report_data.get("sample_identifier") or report_data.get("patient_id")
        if (not display_sample) or _is_uuid_like(display_sample):
            try:
                candidates = []
                for name in os.listdir(patient_dir):
                    if name.endswith("_pgx_pharmcat.html") or name.endswith("_pgx_pharmcat.json") or name.endswith("_pgx_pharmcat.tsv"):
                        base = name.split("_pgx_pharmcat")[0]
                        if base and base != report_data.get("patient_id") and base not in candidates:
                            candidates.append(base)
                if candidates:
                    display_sample = candidates[0]
            except Exception:
                pass
        report_data["display_sample_id"] = display_sample

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

def determine_tool_source(gene_name: str, file_type: str, workflow_config: Dict[str, Any] = None) -> str:
    """
    Determine which tool was used to call a specific gene based on the logic:
    - HLA genes (HLA-A, HLA-B, HLA-C) → O (OptiType)
    - MT-RNR1 → M (MitoZ)
    - PyPGx-only genes → P (PyPGx)
    - Overlapping genes: VCF input → C (PharmCAT), others → P (PyPGx)
    - Environment variable overrides can force PyPGx preference
    
    Args:
        gene_name: Name of the gene
        file_type: Type of input file (vcf, bam, cram, sam)
        workflow_config: Workflow configuration dict
        
    Returns:
        Single letter code: C (PharmCAT), P (PyPGx), O (OptiType), M (MitoZ)
    """
    gene_name = gene_name.upper().strip()
    
    # HLA genes → OptiType
    if gene_name in ["HLA-A", "HLA-B", "HLA-C"]:
        return "O"
    
    # MT-RNR1 → MitoZ
    if gene_name == "MT-RNR1":
        return "M"
    
    # Load gene categories from config
    try:
        config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config", "genes.json")
        with open(config_path, 'r') as f:
            genes_config = json.load(f)
        
        # Get gene sets
        pharmcat_can_call = set(genes_config.get("sets", {}).get("pharmcat_can_call", []))
        pypgx_minus_pharmcat = set(genes_config.get("sets", {}).get("pypgx_minus_pharmcat", []))
        pharmcat_outside_callers = set(genes_config.get("sets", {}).get("pharmcat_outside_callers", []))
        
    except Exception as e:
        # If config can't be loaded, log the error and use empty sets
        logger.warning(f"Failed to load genes.json config: {e}. Tool source determination may be limited.")
        pharmcat_can_call = set()
        pypgx_minus_pharmcat = set()
        pharmcat_outside_callers = set()
    
    # PyPGx-only genes → PyPGx
    if gene_name in pypgx_minus_pharmcat:
        return "P"
    
    # PharmCAT outside callers (handled by PyPGx/OptiType/MitoZ, not PharmCAT directly)
    if gene_name in pharmcat_outside_callers:
        # These are handled by outside tools, not PharmCAT directly
        # CYP2D6 is handled by PyPGx, HLA genes by OptiType, MT-RNR1 by MitoZ
        if gene_name == "CYP2D6":
            return "P"  # CYP2D6 is handled by PyPGx
        # HLA genes and MT-RNR1 are already handled above
    
    # Overlapping genes (both PharmCAT and PyPGx can call)
    if gene_name in pharmcat_can_call:
        # Check for environment variable override
        pypgx_preferred = os.environ.get("PYPGX_PREFERRED", "false").lower() in {"true", "1", "yes"}
        pharmcat_preferred = os.environ.get("PHARMCAT_PREFERRED", "false").lower() in {"true", "1", "yes"}
        
        if pypgx_preferred:
            return "P"
        elif pharmcat_preferred:
            return "C"
        else:
            # Default logic: VCF input → PharmCAT, others → PyPGx
            if file_type.lower() in ["vcf"]:
                return "C"
            else:
                return "P"
    
    # Default fallback to PyPGx for unknown genes
    return "P"


def _load_all_gene_names() -> List[str]:
    """Load the full, canonical list of supported genes from config/genes.json.

    Preference order:
    1) sets.all (explicit curated list)
    2) top-level genes array objects' names
    Returns alphabetical unique list.
    """
    try:
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        config_path = os.path.join(project_root, "config", "genes.json")
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        names: List[str] = []
        sets_all = (cfg.get("sets", {}) or {}).get("all")
        if isinstance(sets_all, list) and sets_all:
            names = [str(x).strip() for x in sets_all if str(x).strip()]
        if not names:
            # Fallback to top-level genes array
            top_genes = cfg.get("genes", []) or []
            if isinstance(top_genes, list):
                for g in top_genes:
                    if isinstance(g, dict) and g.get("name"):
                        names.append(str(g.get("name")).strip())
        # Ensure unique and sorted
        uniq = sorted({n for n in names if n})
        return uniq
    except Exception as e:
        logger.warning(f"Failed to load canonical gene list from genes.json: {e}")
        return []


def _is_unknown_phenotype(text: Any) -> bool:
    t = str(text or "").strip().lower()
    return t in {"", "unknown", "n/a", "na"}


def _choose_better_gene_entry(existing: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    """Heuristic to pick a better gene entry when duplicates are present.

    Priority:
    - Prefer entries with tool_source == 'C' (PharmCAT)
    - Else prefer non-unknown phenotype over unknown
    - Otherwise keep existing
    """
    try:
        # Normalize sources
        existing_source = (existing.get("tool_source") or existing.get("source") or "").strip().upper()
        candidate_source = (candidate.get("tool_source") or candidate.get("source") or "").strip().upper()
        if candidate_source == "PHARMCAT":
            candidate_source = "C"
        if existing_source == "PHARMCAT":
            existing_source = "C"
        if candidate_source == "PYPgx".upper():
            candidate_source = "P"
        if existing_source == "PYPgx".upper():
            existing_source = "P"
        if candidate_source == "OPTITYPE":
            candidate_source = "O"
        if existing_source == "OPTITYPE":
            existing_source = "O"

        # Identify gene name for special precedence cases
        gene_name = (candidate.get("gene") or existing.get("gene") or candidate.get("name") or existing.get("name") or "").strip().upper()

        # Special precedence:
        # - HLA-A/B/C → prefer O (OptiType)
        # - MT-RNR1   → prefer M (MitoZ)
        # - CYP2D6    → prefer P (PyPGx)
        if gene_name in {"HLA-A", "HLA-B", "HLA-C"}:
            if candidate_source == "O" and existing_source != "O":
                return candidate
            if existing_source == "O" and candidate_source != "O":
                return existing
        if gene_name == "MT-RNR1":
            if candidate_source == "M" and existing_source != "M":
                return candidate
            if existing_source == "M" and candidate_source != "M":
                return existing
        if gene_name == "CYP2D6":
            if candidate_source == "P" and existing_source != "P":
                return candidate
            if existing_source == "P" and candidate_source != "P":
                return existing

        # Prefer PharmCAT
        if candidate_source == "C" and existing_source != "C":
            return candidate
        if existing_source == "C" and candidate_source != "C":
            return existing

        # Prefer non-unknown phenotype
        if _is_unknown_phenotype(existing.get("phenotype")) and not _is_unknown_phenotype(candidate.get("phenotype")):
            return candidate

        return existing
    except Exception:
        return existing


def _build_canonical_diplotypes(
    raw_gene_entries: List[Dict[str, Any]],
    file_type: str,
    workflow_config: Dict[str, Any] | None,
) -> List[Dict[str, Any]]:
    """Build an alphabetical, canonical list of gene entries for all supported genes.

    - Deduplicates incoming entries by gene name with sane precedence
    - Ensures every supported gene appears exactly once
    - Fills placeholders for genes with no data
    """
    # Index best entry per gene
    best_by_gene: Dict[str, Dict[str, Any]] = {}
    for entry in raw_gene_entries or []:
        if not isinstance(entry, dict):
            continue
        gene_name = (entry.get("gene") or entry.get("name") or "").strip()
        if not gene_name:
            continue
        key = gene_name.upper()
        if key in best_by_gene:
            best_by_gene[key] = _choose_better_gene_entry(best_by_gene[key], entry)
        else:
            best_by_gene[key] = entry

    # Load canonical list and assemble rows
    canonical_names = _load_all_gene_names()
    if not canonical_names:
        # Fallback to whatever we have, alphabetized and deduplicated
        canonical_names = sorted(best_by_gene.keys())

    canonical_rows: List[Dict[str, Any]] = []
    for name in sorted(canonical_names, key=lambda s: s.upper()):
        key = name.upper()
        if key in best_by_gene:
            # Normalize fields and ensure tool_source exists
            row = dict(best_by_gene[key])
            row["gene"] = name
            try:
                if not row.get("tool_source"):
                    row["tool_source"] = determine_tool_source(name, file_type, workflow_config)
            except Exception:
                pass
            canonical_rows.append(row)
        else:
            # Placeholder row for genes without results
            canonical_rows.append({
                "gene": name,
                "diplotype": "",
                "phenotype": "",
                "activity_score": None,
                "tool_source": determine_tool_source(name, file_type, workflow_config or {}),
            })

    return canonical_rows

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
        pharmcat_results: Results from PharmCAT (already normalized from main.py)
        output_dir: Directory to write report files to
        patient_info: Optional patient information
        
    Returns:
        Dict containing file paths for all enabled reports
    """
    logger.info("Generating report from PharmCAT results")
    try:
        logger.info(f"Input pharmcat_results keys: {list(pharmcat_results.keys())}")
    except Exception:
        logger.info("Input pharmcat_results has no keys()")
    logger.info(f"Input patient_info: {patient_info}")
    
    # The data coming from main.py is already normalized, so we don't need to normalize again
    # Check if the data is already in the expected format
    if "data" in pharmcat_results and isinstance(pharmcat_results["data"], dict):
        # Data is already in the expected format from main.py
        data = pharmcat_results["data"]
        logger.info("Using pre-normalized data from main.py")
        logger.info(f"Data keys: {list(data.keys())}")
    else:
        # Fallback: try to normalize if the data structure is unexpected
        logger.warning("Unexpected data structure, attempting normalization")
        logger.warning(f"Expected 'data' key not found in: {list(pharmcat_results.keys())}")
        try:
            normalized_results = normalize_pharmcat_results(pharmcat_results)
            data = normalized_results["data"]
            logger.info("Successfully normalized data")
        except Exception as e:
            logger.error(f"Failed to normalize data: {str(e)}")
            # Create minimal data structure to prevent crashes
            data = {
                "genes": [],
                "drugRecommendations": [],
                "file_type": "unknown"
            }
    
    # Enrich with PyPGx-only genes (not present in PharmCAT) if available
    try:
        # Determine report directory based on provided output_dir and patient id (if known later)
        # We'll append later once we compute report_dir, but we can attempt a global pattern too
        # Prefer per-patient directory lookup once we know patient_id (set below), so we postpone
        pass
    except Exception:
        pass

    # Map recommendations to template-compatible format
    template_recommendations = map_recommendations_for_template(data.get("drugRecommendations", []))
    logger.info(f"Mapped {len(template_recommendations)} recommendations for template")
    
    # Add tool source information to diplotypes
    file_type = str(data.get("file_type", "vcf")).lower()
    workflow_config = data.get("workflow", {})
    
    # Process diplotypes to add tool source
    enhanced_diplotypes = []
    for diplotype in data.get("genes", []):
        if isinstance(diplotype, dict):
            gene_name = diplotype.get("gene", "")
            if gene_name:
                tool_source = determine_tool_source(gene_name, file_type, workflow_config)
                diplotype["tool_source"] = tool_source
                enhanced_diplotypes.append(diplotype)
            else:
                enhanced_diplotypes.append(diplotype)
        else:
            enhanced_diplotypes.append(diplotype)
    
    # Update the data with enhanced diplotypes
    data["genes"] = enhanced_diplotypes
    logger.info(f"Enhanced {len(enhanced_diplotypes)} diplotypes with tool source information")
    
    # Optionally prepare Executive Summary rows from TSV
    execsum_rows_from_tsv = []
    try:
        if EXECSUM_USE_TSV:
            # Determine report directory for patient
            pid_for_dir = (patient_info.get("id", "unknown") if patient_info else "unknown")
            report_dir_probe = os.path.join(output_dir, pid_for_dir)
            # Candidate TSV names:
            # 1) Standardized copy: {patient_id}_pgx_pharmcat.tsv
            # 2) Original PharmCAT: {patient_id}.report.tsv
            # 3) Any *_pgx_pharmcat.tsv
            # Also probe the base output_dir (some workflows place TSVs at the first level)
            tsv_candidates = []
            tsv_candidates.append(os.path.join(report_dir_probe, f"{pid_for_dir}_pgx_pharmcat.tsv"))
            tsv_candidates.append(os.path.join(report_dir_probe, f"{pid_for_dir}.report.tsv"))
            try:
                tsv_candidates.extend(glob.glob(os.path.join(report_dir_probe, "*_pgx_pharmcat.tsv")))
            except Exception:
                pass
            try:
                any_report_tsv = glob.glob(os.path.join(report_dir_probe, "*.pharmcat.tsv"))
                # Prefer newest pharmcat.tsv if multiple
                if any_report_tsv:
                    any_report_tsv.sort(key=lambda p: os.path.getmtime(p), reverse=True)
                    tsv_candidates.extend(any_report_tsv)
            except Exception:
                pass
            # Base directory candidates (where reports often land before subdir creation)
            try:
                tsv_candidates.append(os.path.join(output_dir, f"{pid_for_dir}_pgx_pharmcat.tsv"))
                tsv_candidates.append(os.path.join(output_dir, f"{pid_for_dir}.report.tsv"))
                tsv_candidates.extend(glob.glob(os.path.join(output_dir, "*_pgx_pharmcat.tsv")))
                any_report_tsv_base = glob.glob(os.path.join(output_dir, "*.pharmcat.tsv"))
                if any_report_tsv_base:
                    any_report_tsv_base.sort(key=lambda p: os.path.getmtime(p), reverse=True)
                    tsv_candidates.extend(any_report_tsv_base)
            except Exception:
                pass
            # Pick first existing in order
            tsv_path = next((p for p in tsv_candidates if os.path.exists(p)), None)
            if tsv_path:
                diplos, _recs = parse_pharmcat_tsv(tsv_path)
                try:
                    logger.info(f"Executive Summary TSV selected: {tsv_path}")
                except Exception:
                    pass
                for row in diplos:
                    execsum_rows_from_tsv.append({
                        "gene": row.get("gene", ""),
                        "rec_lookup_diplotype": row.get("rec_lookup_diplotype", ""),
                        "rec_lookup_phenotype": row.get("rec_lookup_phenotype", row.get("phenotype", "")),
                        "rec_lookup_activity_score": row.get("rec_lookup_activity_score"),
                    })
    except Exception as _e_exec:
        logger.warning(f"Executive Summary TSV parse skipped: {_e_exec}")

    # Prepare the template data
    # Build platform info once and include in both outputs
    platform = build_platform_info()
    logger.info(f"Built platform info with {len(platform)} items")

    template_data = {
        "patient": patient_info or {},
        "patient_id": patient_info.get("id", "unknown") if patient_info else "unknown",
        "report_id": patient_info.get("report_id", "unknown") if patient_info else "unknown",
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
        "current_year": datetime.now().year,
        "disclaimer": get_disclaimer(),  # Add missing disclaimer variable
        # Add missing fields that PDF generators expect
        "sample_id": patient_info.get("id", "unknown") if patient_info else "unknown",
        "file_type": data.get("file_type", "vcf"),
        "analysis_results": {
            "genes_found": len(data.get("genes", [])),
            "recommendations_found": len(data.get("drugRecommendations", [])),
            "file_type": data.get("file_type", "vcf")
        },
        "workflow": {
            "file_type": data.get("file_type", "vcf"),
            "used_gatk": data.get("used_gatk", False),
            "used_pypgx": data.get("used_pypgx", False),
            "used_pharmcat": True
        },
        # Inject optional TSV-driven Executive Summary rows for template use
        "execsum_from_tsv": execsum_rows_from_tsv if (EXECSUM_USE_TSV and execsum_rows_from_tsv) else None,
    }
    # Inject unified display sample id sourced from workflow metadata or persisted field
    try:
        workflow_meta = data.get("workflow") if isinstance(data.get("workflow"), dict) else {}
        display_sample = (
            data.get("sample_identifier")
            or workflow_meta.get("display_sample_id")
            or data.get("displayId")
            or (patient_info.get("display_sample_id") if patient_info else None)
            or (patient_info.get("id") if patient_info else None)
        )
        if display_sample:
            template_data["sample_identifier"] = display_sample
            template_data["display_sample_id"] = display_sample
    except Exception:
        pass
    
    logger.info(f"Template data prepared with {len(template_data)} fields")
    logger.info(f"Template data keys: {list(template_data.keys())}")
    logger.info(f"Genes count: {len(template_data['genes'])}")
    logger.info(f"Recommendations count: {len(template_data['recommendations'])}")
    
    # Get patient and report IDs
    patient_id = patient_info.get("id", "unknown") if patient_info else "unknown"
    # Use report_id from patient_info or generate one if not available
    report_id = patient_info.get("report_id", patient_id) if patient_info else patient_id
    
    # Create a report-specific directory using patient_id as the directory name
    # This matches the new approach in upload_router.py where all reports go to patient directories
    report_dir = os.path.join(output_dir, patient_id)
    os.makedirs(report_dir, exist_ok=True)
    logger.info(f"Created report directory: {report_dir}")

    # Attempt to load genomic header text saved earlier in the pipeline
    header_text: str = ""
    try:
        # We expect a file named {data_id}.header.txt written in the patient report directory
        if patient_info and patient_info.get("report_id"):
            data_id_for_header = str(patient_info.get("report_id"))
        else:
            # Fallback: try patient_id as report_id
            data_id_for_header = patient_id
        header_txt_candidates = []
        # Primary expected file
        header_txt_candidates.append(os.path.join(report_dir, f"{data_id_for_header}.header.txt"))
        # Also consider any *.header.txt in the directory if the exact data_id is not known
        try:
            for p in glob.glob(os.path.join(report_dir, "*.header.txt")):
                if p not in header_txt_candidates:
                    header_txt_candidates.append(p)
        except Exception:
            pass
        # Pick the first existing candidate (prefer exact match if present)
        selected = None
        for cand in header_txt_candidates:
            if os.path.exists(cand):
                selected = cand
                # Prefer exact match; break on first existing when in listed order
                break
        if not selected:
            # As a final fallback, try to locate any header txt in parent output_dir/patient_id
            alt = os.path.join(output_dir, patient_id, f"{patient_id}.header.txt")
            if os.path.exists(alt):
                selected = alt
        if selected:
            with open(selected, "r", encoding="utf-8", errors="ignore") as hf:
                header_text = hf.read()
            logger.info(f"Loaded genomic header text from {selected} ({len(header_text)} chars)")
        else:
            logger.info("No genomic header text file found for this report")
    except Exception as _e:
        logger.warning(f"Failed to load genomic header text: {_e}")

    # Now that we know the report directory, try to load PyPGx results and augment data['genes']
    try:
        # Prefer PyPGx results coming directly from workflow (if upstream provided it)
        pypgx_results = None
        if isinstance(data, dict) and 'pypgx_results' in data:
            pypgx_results = data.get('pypgx_results')
        # If not embedded, look for *_pypgx_results.json in the report directory; pick the newest by mtime
        if not pypgx_results:
            pypgx_json_candidates = glob.glob(os.path.join(report_dir, "*_pypgx_results.json"))
            if pypgx_json_candidates:
                latest_path = max(pypgx_json_candidates, key=lambda p: os.path.getmtime(p))
                logger.info(f"Found PyPGx results JSON for enrichment: {latest_path}")
                with open(latest_path, "r", encoding="utf-8") as f:
                    pypgx_results = json.load(f)
        if pypgx_results:
            logger.info("Proceeding to merge PyPGx results into report data...")
            results_obj = pypgx_results.get("results", {}) if isinstance(pypgx_results, dict) else {}
            existing_genes = { (g.get("gene") or g.get("name") or "").strip().upper() for g in data.get("genes", []) if isinstance(g, dict) }
            added_count = 0
            for gene_key, gene_res in results_obj.items():
                try:
                    if not isinstance(gene_res, dict) or not gene_res.get("success"):
                        continue
                    gene_name = str(gene_key).strip()
                    if gene_name.upper() in existing_genes:
                        continue
                    diplotype = gene_res.get("diplotype")
                    details = gene_res.get("details") or {}
                    phenotype = details.get("phenotype") or details.get("Phenotype")
                    activity_score = details.get("activity_score") or details.get("activityScore")
                    gene_entry = {
                        "gene": gene_name,
                        # Align with normalized PharmCAT structure minimally
                        "diplotype": diplotype if diplotype else "Unknown",
                        "phenotype": phenotype if phenotype else "Unknown",
                        "activity_score": activity_score,
                        "source": "PyPGx",
                        "pyPgxOnly": True
                    }
                    # activity_score is already included in the gene_entry above
                    data.setdefault("genes", []).append(gene_entry)
                    existing_genes.add(gene_name.upper())
                    added_count += 1
                except Exception as ie:
                    logger.warning(f"Failed to map PyPGx gene {gene_key}: {ie}")
            if added_count:
                data["used_pypgx"] = True
                logger.info(f"Augmented report with {added_count} PyPGx-only gene entries (total genes now={len(data.get('genes', []))})")
        else:
            logger.info("No PyPGx results available for enrichment (embedded or file)")

        # Deep enrichment from per-gene pipeline artifacts if available
        try:
            # Locate any per-gene pipelines under a pypgx_* directory in this report_dir
            pipelines_root_candidates = glob.glob(os.path.join(report_dir, "pypgx_*"))
            if pipelines_root_candidates:
                pipelines_root = pipelines_root_candidates[0]
                pipeline_dirs = [os.path.join(pipelines_root, d) for d in os.listdir(pipelines_root) if d.endswith("-pipeline") and os.path.isdir(os.path.join(pipelines_root, d))]
                if pipeline_dirs:
                    logger.info(f"Found {len(pipeline_dirs)} PyPGx per-gene pipelines for enrichment")
                # Build index for quick update
                gene_index = {}
                for idx, g in enumerate(data.get("genes", [])):
                    if isinstance(g, dict):
                        key = (g.get("gene") or g.get("name") or "").strip().upper()
                        if key:
                            gene_index[key] = idx
                enriched_count = 0
                for pdir in pipeline_dirs:
                    gene_name = os.path.basename(pdir).replace("-pipeline", "")
                    parsed = parse_gene_pipeline(pdir, gene_name)
                    key = gene_name.strip().upper()
                    # Merge into existing entry if present, else append as PyPGx-only
                    if key in gene_index:
                        target = data["genes"][gene_index[key]]
                        if isinstance(target, dict):
                            # Only fill fields that are missing; attach evidence regardless
                            if not target.get("diplotype") and parsed.get("diplotype"):
                                target["diplotype"] = parsed["diplotype"]
                            if not target.get("phenotype") and parsed.get("phenotype"):
                                target["phenotype"] = parsed["phenotype"]
                            if not target.get("activity_score") and parsed.get("activity_score"):
                                target["activity_score"] = parsed["activity_score"]
                            if parsed.get("call_confidence") and not target.get("call_confidence"):
                                target["call_confidence"] = parsed["call_confidence"]
                            # Evidence
                            if parsed.get("evidence"):
                                target.setdefault("evidence", {})
                                if parsed["evidence"].get("alleles"):
                                    target["evidence"]["alleles"] = parsed["evidence"]["alleles"]
                                if parsed["evidence"].get("variants"):
                                    target["evidence"]["variants"] = parsed["evidence"]["variants"]
                            if parsed.get("phased") is True:
                                target["phased"] = True
                            if parsed.get("copy_number") and not target.get("copy_number"):
                                target["copy_number"] = parsed["copy_number"]
                            # Always set tool source if not present
                            if not target.get("tool_source"):
                                target["tool_source"] = "PyPGx"
                            enriched_count += 1
                    else:
                        # Append new PyPGx-only gene
                        entry = {
                            "gene": gene_name,
                            "diplotype": parsed.get("diplotype") or "Unknown",
                            "phenotype": parsed.get("phenotype") or "Unknown",
                            "activity_score": parsed.get("activity_score"),
                            "tool_source": "PyPGx",
                            "pyPgxOnly": True
                        }
                        if parsed.get("call_confidence"):
                            entry["call_confidence"] = parsed["call_confidence"]
                        if parsed.get("evidence"):
                            entry["evidence"] = parsed["evidence"]
                        if parsed.get("phased") is True:
                            entry["phased"] = True
                        if parsed.get("copy_number"):
                            entry["copy_number"] = parsed["copy_number"]
                        data.setdefault("genes", []).append(entry)
                        enriched_count += 1
                if enriched_count:
                    data["used_pypgx"] = True
                    logger.info(f"Enriched PyPGx details for {enriched_count} genes from per-gene pipelines")
        except Exception as enrich_e:
            logger.warning(f"PyPGx per-gene enrichment skipped due to error: {enrich_e}")
    except Exception as e:
        logger.warning(f"PyPGx enrichment step failed: {e}")

    # After all enrichment steps, build the canonical, alphabetical full gene list
    try:
        canonical_diplotypes = _build_canonical_diplotypes(
            raw_gene_entries=data.get("genes", []),
            file_type=file_type,
            workflow_config=workflow_config,
        )
        data["genes"] = canonical_diplotypes
        logger.info(
            f"Canonical diplotypes prepared: total={len(canonical_diplotypes)} (alphabetical, deduplicated, full complement)"
        )
    except Exception as canon_e:
        logger.warning(f"Failed to build canonical diplotypes: {canon_e}")

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
    
    logger.info(f"Generated workflow configuration: {per_sample_workflow}")
    logger.info(f"Data keys available: {list(data.keys())}")
    logger.info(f"Genes count: {len(data.get('genes', []))}")
    logger.info(f"Drug recommendations count: {len(data.get('drugRecommendations', []))}")

    # Optionally write workflow images alongside the report outputs
    # Use patient_id for filenames to match the directory structure
    workflow_svg_filename = f"{patient_id}_workflow.svg"
    workflow_png_filename = f"{patient_id}_workflow.png"
    
    logger.info(f"=== WORKFLOW DIAGRAM GENERATION START ===")
    logger.info(f"Workflow configuration: {per_sample_workflow}")
    
    try:
        if REPORT_CONFIG.get("write_workflow_svg", True):
            logger.info("Generating Graphviz SVG workflow diagram (true Graphviz renderer)...")
            svg_bytes = render_with_graphviz(per_sample_workflow, fmt="svg")
            if svg_bytes:
                svg_path = os.path.join(report_dir, workflow_svg_filename)
                with open(svg_path, "wb") as f_out:
                    f_out.write(svg_bytes)
                logger.info(f"✓ Graphviz Workflow SVG generated successfully: {svg_path} ({len(svg_bytes)} bytes)")
            else:
                logger.warning("⚠ Graphviz Workflow SVG generation returned empty result")
    except Exception as e:
        logger.error(f"✗ Graphviz Workflow SVG generation failed: {str(e)}", exc_info=True)
    
    # Generate Kroki Mermaid SVG for comparison
    try:
        if REPORT_CONFIG.get("write_workflow_svg", True):
            logger.info("Generating Kroki Mermaid SVG workflow diagram for comparison...")
            kroki_svg_bytes = render_kroki_mermaid_svg(workflow=per_sample_workflow)
            if kroki_svg_bytes:
                kroki_svg_filename = f"{patient_id}_workflow_kroki_mermaid.svg"
                kroki_svg_path = os.path.join(report_dir, kroki_svg_filename)
                with open(kroki_svg_path, "wb") as f_out:
                    f_out.write(kroki_svg_bytes)
                logger.info(f"✓ Kroki Mermaid Workflow SVG generated successfully: {kroki_svg_path} ({len(kroki_svg_bytes)} bytes)")
            else:
                logger.warning("⚠ Kroki Mermaid Workflow SVG generation returned empty result")
    except Exception as e:
        logger.error(f"✗ Kroki Mermaid Workflow SVG generation failed: {str(e)}", exc_info=True)
    
    try:
        if REPORT_CONFIG.get("write_workflow_png", False):
            logger.info("Generating PNG workflow diagram...")
            png_bytes = render_workflow(fmt="png", workflow=per_sample_workflow)
            if not png_bytes:
                # Force pure-Python PNG fallback so a file is always present
                logger.info("PNG generation failed, trying Python fallback...")
                png_bytes = render_simple_png_from_workflow(per_sample_workflow)
            if png_bytes:
                png_path = os.path.join(report_dir, workflow_png_filename)
                with open(png_path, "wb") as f_out:
                    f_out.write(png_bytes)
                logger.info(f"✓ Workflow PNG generated successfully: {png_path} ({len(png_bytes)} bytes)")
            else:
                logger.warning("⚠ Workflow PNG generation still failed after fallback")
        else:
            logger.info("PNG workflow generation disabled in config")
    except Exception as e:
        logger.error(f"✗ Workflow PNG generation failed: {str(e)}", exc_info=True)
    
    logger.info(f"=== WORKFLOW DIAGRAM GENERATION END ===")
    
    # Create a unique filename based on patient_id
    base_filename = f"{patient_id}_pgx_report"
    
    try:
        # Generate both standard HTML report and interactive HTML report
        html_path = os.path.join(report_dir, f"{base_filename}.html")
        interactive_html_path = os.path.join(report_dir, f"{base_filename}_interactive.html")
        
        # Initialize the report paths dictionary that will be returned
        report_paths = {}
        
        # Generate standard HTML report if enabled
        if REPORT_CONFIG["write_html"]:
            logger.info("=== HTML REPORT GENERATION START ===")
            logger.info("Loading HTML template...")
            env = Environment(
                loader=FileSystemLoader(os.path.join(os.path.dirname(__file__), "templates")),
                autoescape=select_autoescape(['html', 'xml'])
            )
            template = env.get_template("report_template.html")
            logger.info("HTML template loaded successfully")
            
            # Prepare workflow visuals for HTML, preferring pre-rendered files
            workflow_svg_in_html = ""
            workflow_kroki_svg_in_html = ""
            workflow_png_data_uri_html = ""
            workflow_html_fallback_html = ""

            # Prefer previously written assets
            svg_path = os.path.join(report_dir, workflow_svg_filename)
            kroki_svg_filename = f"{patient_id}_workflow_kroki_mermaid.svg"
            kroki_svg_path = os.path.join(report_dir, kroki_svg_filename)
            png_path = os.path.join(report_dir, workflow_png_filename)

            logger.info(f"Checking for pre-generated workflow files...")
            logger.info(f"Graphviz SVG path: {svg_path} (exists: {os.path.exists(svg_path)})")
            logger.info(f"Kroki Mermaid SVG path: {kroki_svg_path} (exists: {os.path.exists(kroki_svg_path)})")
            logger.info(f"PNG path: {png_path} (exists: {os.path.exists(png_path)})")

            if os.path.exists(svg_path):
                try:
                    with open(svg_path, "r", encoding="utf-8") as f_svg:
                        workflow_svg_in_html = f_svg.read()
                    logger.info(f"✓ Loaded pre-generated Graphviz SVG workflow: {len(workflow_svg_in_html)} chars")
                except Exception as e:
                    logger.error(f"✗ Failed to read pre-generated Graphviz SVG: {str(e)}")
                    workflow_svg_in_html = ""
            
            # Load Kroki Mermaid SVG for comparison
            if os.path.exists(kroki_svg_path):
                try:
                    with open(kroki_svg_path, "r", encoding="utf-8") as f_kroki_svg:
                        workflow_kroki_svg_in_html = f_kroki_svg.read()
                    logger.info(f"✓ Loaded pre-generated Kroki Mermaid SVG workflow: {len(workflow_kroki_svg_in_html)} chars")
                except Exception as e:
                    logger.error(f"✗ Failed to read pre-generated Kroki Mermaid SVG: {str(e)}")
                    workflow_kroki_svg_in_html = ""
                    
            if not workflow_svg_in_html and os.path.exists(png_path):
                try:
                    with open(png_path, "rb") as f_png:
                        b64 = base64.b64encode(f_png.read()).decode("ascii")
                        workflow_png_data_uri_html = f"data:image/png;base64,{b64}"
                    logger.info(f"✓ Loaded pre-generated PNG workflow: {len(workflow_png_data_uri_html)} chars")
                except Exception as e:
                    logger.error(f"✗ Failed to read pre-generated PNG: {str(e)}")
                    workflow_png_data_uri_html = ""

            # If no local assets available, try dynamic renderers and finally HTML fallback
            if not workflow_svg_in_html and not workflow_png_data_uri_html:
                logger.info("No pre-generated workflow files found, trying dynamic generation...")
                try:
                    logger.info("Attempting SVG generation...")
                    svg_bytes = render_workflow(fmt="svg", workflow=per_sample_workflow)
                    svg_text = svg_bytes.decode("utf-8", errors="ignore")
                    workflow_svg_in_html = _sanitize_graphviz_svg(svg_text)
                    logger.info(f"✓ Generated SVG workflow dynamically: {len(workflow_svg_in_html)} chars")
                except Exception as e:
                    logger.error(f"✗ Dynamic SVG generation failed: {str(e)}")
                    workflow_svg_in_html = ""
                    
                if not workflow_svg_in_html:
                    try:
                        logger.info("Attempting PNG data URI generation...")
                        workflow_png_data_uri_html = render_workflow_png_data_uri(workflow=per_sample_workflow)
                        logger.info(f"✓ Generated PNG data URI: {len(workflow_png_data_uri_html)} chars")
                    except Exception as e:
                        logger.error(f"✗ PNG data URI generation failed: {str(e)}")
                        workflow_png_data_uri_html = ""
                        
                if not workflow_svg_in_html and not workflow_png_data_uri_html:
                    try:
                        logger.info("Attempting HTML fallback generation...")
                        workflow_html_fallback_html = build_simple_html_from_workflow(per_sample_workflow)
                        logger.info(f"✓ Generated HTML fallback: {len(workflow_html_fallback_html)} chars")
                    except Exception as e:
                        logger.error(f"✗ HTML fallback generation failed: {str(e)}")
                        workflow_html_fallback_html = ""

            # Log what we have for the template
            logger.info(f"Workflow content prepared for HTML template:")
            logger.info(f"  Graphviz SVG: {len(workflow_svg_in_html)} chars")
            logger.info(f"  Kroki Mermaid SVG: {len(workflow_kroki_svg_in_html)} chars")
            logger.info(f"  PNG data URI: {len(workflow_png_data_uri_html)} chars")
            logger.info(f"  HTML fallback: {len(workflow_html_fallback_html)} chars")

            # Prepare template data
            template_data = {
                "patient_id": patient_id,
                "report_id": report_id,
                "report_date": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
                "diplotypes": data.get("genes", []),
                "recommendations": template_recommendations,
                "disclaimer": get_disclaimer(),
                "platform_info": platform,
                "citations": build_citations(),
                "author_name": get_author_name(),
                "license_name": get_license_name(),
                "license_url": get_license_url(),
                "source_url": get_source_url(),
                "workflow": per_sample_workflow,
                "workflow_diagrams": True,
                "header_text": header_text,
                # Pass TSV-driven Executive Summary rows if enabled and available
                "execsum_from_tsv": execsum_rows_from_tsv if (EXECSUM_USE_TSV and execsum_rows_from_tsv) else None,
            }
            try:
                logger.info(f"Executive Summary rows (TSV): {len(execsum_rows_from_tsv) if execsum_rows_from_tsv else 0}; Using TSV: {EXECSUM_USE_TSV}")
            except Exception:
                pass
            # Inject display sample id for WeasyPrint HTML template
            try:
                display_sample = (
                    data.get("sample_identifier")
                    or data.get("displayId")
                    or patient_id
                )
                if display_sample:
                    template_data["sample_identifier"] = display_sample
                    template_data["display_sample_id"] = display_sample
                # If still UUID-like, try to derive from PharmCAT files in report_dir
                if (not display_sample) or _is_uuid_like(display_sample):
                    try:
                        # Look for any *_pgx_pharmcat.* files and extract base
                        candidates = []
                        for name in os.listdir(report_dir):
                            if name.endswith("_pgx_pharmcat.html") or name.endswith("_pgx_pharmcat.json") or name.endswith("_pgx_pharmcat.tsv"):
                                base = name.split("_pgx_pharmcat")[0]
                                if base and base != patient_id and base not in candidates:
                                    candidates.append(base)
                        if candidates:
                            derived = candidates[0]
                            template_data["sample_identifier"] = derived
                            template_data["display_sample_id"] = derived
                            logger.info(f"Derived display_sample_id from PharmCAT files: {derived}")
                    except Exception as e:
                        logger.debug(f"Display sample derivation from files failed: {e}")
            except Exception:
                pass

            # Add debug information for troubleshooting
            try:
                debug_info = {
                    "workflow_config": per_sample_workflow,
                    "workflow_assets": {
                        "svg_path_exists": bool(os.path.exists(svg_path)),
                        "kroki_svg_path_exists": bool(os.path.exists(kroki_svg_path)),
                        "pre_png_path_exists": bool(os.path.exists(png_path)),
                        "used_inline_svg": bool(workflow_svg_in_html),
                        "used_kroki_svg": bool(workflow_kroki_svg_in_html),
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
                    "template_data_keys": list(template_data.keys()),
                    "template_data_sample": {k: str(v)[:100] + "..." if len(str(v)) > 100 else str(v) for k, v in list(template_data.items())[:5]},
                }
                debug_json = json.dumps(debug_info, indent=2)
            except Exception:
                debug_json = "{}"

            # Render the HTML template with error handling
            try:
                logger.info("Rendering HTML template...")
                html_content = template.render(
                    **template_data,
                    workflow_svg=workflow_svg_in_html,
                    workflow_kroki_svg=workflow_kroki_svg_in_html,
                    workflow_png_data_uri=workflow_png_data_uri_html,
                    workflow_html_fallback=workflow_html_fallback_html,
                    debug_json=debug_json,
                )
                logger.info(f"✓ Template rendered successfully, HTML content length: {len(html_content)}")
            except Exception as e:
                logger.error(f"✗ Template rendering failed: {str(e)}", exc_info=True)
                # Try to provide more context about what might be missing
                logger.error(f"Template data keys: {list(template_data.keys())}")
                logger.error(f"Workflow content lengths - Graphviz SVG: {len(workflow_svg_in_html)}, Kroki SVG: {len(workflow_kroki_svg_in_html)}, PNG: {len(workflow_png_data_uri_html)}, HTML: {len(workflow_html_fallback_html)}")
                raise
            
            logger.info(f"Template data keys used: {list(template_data.keys())}")
            
            # Add the rendered HTML content to template_data for PDF generation
            template_data["template_html"] = html_content
            
            try:
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(html_content)
                logger.info(f"✓ HTML report written to file: {html_path}")
            except Exception as e:
                logger.error(f"✗ Failed to write HTML report to file: {str(e)}")
                raise
            
            # Add to report paths
            server_html_path = f"/reports/{patient_id}/{base_filename}.html"
            report_paths["html_path"] = server_html_path
            # Store the HTML report URL in the data structure for later use
            data["html_report_url"] = server_html_path

            # Surface workflow asset URLs if present
            svg_path = os.path.join(report_dir, workflow_svg_filename)
            if os.path.exists(svg_path):
                report_paths["workflow_svg_path"] = f"/reports/{patient_id}/{workflow_svg_filename}"
                data["workflow_svg_url"] = report_paths["workflow_svg_path"]
                logger.info(f"✓ Workflow Graphviz SVG URL added: {report_paths['workflow_svg_path']}")
            
            kroki_svg_path = os.path.join(report_dir, kroki_svg_filename)
            if os.path.exists(kroki_svg_path):
                report_paths["workflow_kroki_svg_path"] = f"/reports/{patient_id}/{kroki_svg_filename}"
                data["workflow_kroki_svg_url"] = report_paths["workflow_kroki_svg_path"]
                logger.info(f"✓ Workflow Kroki Mermaid SVG URL added: {report_paths['workflow_kroki_svg_path']}")
            
            png_path = os.path.join(report_dir, workflow_png_filename)
            if os.path.exists(png_path):
                report_paths["workflow_png_path"] = f"/reports/{patient_id}/{workflow_png_filename}"
                data["workflow_png_url"] = report_paths["workflow_png_path"]
                logger.info(f"✓ Workflow PNG URL added: {report_paths['workflow_png_path']}")
            
            logger.info(f"=== HTML REPORT GENERATION COMPLETE ===")
        else:
            logger.info("HTML report generation disabled in config")
        
        # Generate interactive HTML report if enabled
        if REPORT_CONFIG["write_interactive_html"]:
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
            server_interactive_html_path = f"/reports/{patient_id}/{base_filename}_interactive.html"
            report_paths["interactive_html_path"] = server_interactive_html_path
            data["interactive_html_report_url"] = server_interactive_html_path
        
        # Generate PDF report using configured engine
        if REPORT_CONFIG["write_pdf"]:
            pdf_path = os.path.join(report_dir, f"{base_filename}.pdf")
            
            logger.info(f"=== PDF GENERATION START (Engine: {PDF_ENGINE}) ===")
            logger.info(f"Workflow data: {per_sample_workflow}")
            
            # Prefer embedding pre-rendered SVG (Kroki Mermaid first, then Graphviz), fallback to PNG
            workflow_svg_for_pdf = ""
            pdf_png_data_uri = ""
            
            try:
                # Prefer Graphviz SVG for PDFs (WeasyPrint-friendly), then Kroki, then PNG
                svg_graphviz_path = os.path.join(report_dir, f"{patient_id}_workflow.svg")
                svg_kroki_path = os.path.join(report_dir, f"{patient_id}_workflow_kroki_mermaid.svg")
                chosen_svg_content = ""
                if os.path.exists(svg_graphviz_path):
                    try:
                        with open(svg_graphviz_path, "r", encoding="utf-8") as f_svg:
                            chosen_svg_content = f_svg.read()
                        logger.info(f"✓ Using pre-rendered Graphviz SVG for PDF: {len(chosen_svg_content)} chars")
                    except Exception as e:
                        logger.warning(f"Failed reading Graphviz SVG for PDF: {e}")
                if not chosen_svg_content and os.path.exists(svg_kroki_path):
                    try:
                        with open(svg_kroki_path, "r", encoding="utf-8") as f_svg:
                            chosen_svg_content = f_svg.read()
                        logger.info(f"✓ Using pre-rendered Kroki Mermaid SVG for PDF: {len(chosen_svg_content)} chars")
                    except Exception as e:
                        logger.warning(f"Failed reading Kroki SVG for PDF: {e}")
                if not chosen_svg_content:
                    try:
                        # Try generating a fresh SVG via Mermaid first for better text
                        svg_bytes = render_workflow(fmt="svg", workflow=per_sample_workflow)
                        if svg_bytes:
                            chosen_svg_content = svg_bytes.decode("utf-8", errors="ignore")
                            # Save for future use
                            with open(svg_graphviz_path, "w", encoding="utf-8") as f_out:
                                f_out.write(chosen_svg_content)
                            logger.info(f"✓ Generated SVG for PDF dynamically: {len(chosen_svg_content)} chars")
                    except Exception as e:
                        logger.warning(f"Dynamic SVG generation failed for PDF: {e}")
                if chosen_svg_content:
                    workflow_svg_for_pdf = f'<div class="workflow-figure">{chosen_svg_content}</div>'
                else:
                    # Fallback to PNG as a last resort
                    try:
                        png_bytes = render_workflow(fmt="png", workflow=per_sample_workflow)
                        if png_bytes:
                            pdf_png_data_uri = f"data:image/png;base64,{base64.b64encode(png_bytes).decode()}"
                            workflow_svg_for_pdf = f'<div class="workflow-figure"><img src="{pdf_png_data_uri}" alt="Workflow Diagram" style="max-width: 100%; height: auto; display: block; margin: 0 auto;" /></div>'
                            logger.info(f"✓ Generated PNG workflow for PDF (fallback): {len(png_bytes)} bytes")
                        else:
                            workflow_svg_for_pdf = "<em>Workflow: Upload → Detect → VCF → PharmCAT → Reports</em>"
                            logger.info("ℹ Using text-based workflow description for PDF")
                    except Exception as e:
                        workflow_svg_for_pdf = "<em>Workflow: Upload → Detect → VCF → PharmCAT → Reports</em>"
                        logger.warning(f"⚠ Workflow diagram PNG fallback failed, using text fallback: {e}")
            except Exception as e:
                workflow_svg_for_pdf = "<em>Workflow: Upload → Detect → VCF → PharmCAT → Reports</em>"
                logger.warning(f"⚠ PDF workflow rendering block failed, using text fallback: {e}")
            
            # Render the HTML template with workflow diagram
            pdf_html_content = template.render(
                **template_data,
                workflow_svg=workflow_svg_for_pdf,
                workflow_png_file_url="",
                workflow_png_data_uri=pdf_png_data_uri,
                workflow_html_fallback="",
            )
            
            # Add the PDF HTML content to template_data for PDF generators
            template_data["template_html"] = pdf_html_content
            
            # Generate PDF using configured engine
            pdf_generated = False
            
            if PDF_ENGINE == "weasyprint":
                try:
                    generate_pdf_from_html(pdf_html_content, pdf_path)
                    logger.info(f"✓ PDF report generated successfully using WeasyPrint: {pdf_path}")
                    pdf_generated = True
                except Exception as e:
                    logger.error(f"✗ WeasyPrint PDF generation failed: {str(e)}")
                    if PDF_FALLBACK:
                        logger.info("🔄 Attempting ReportLab fallback...")
                        try:
                            result = generate_pdf_report_dual_lane(
                                template_data=template_data,
                                output_path=pdf_path,
                                workflow_diagram=per_sample_workflow
                            )
                            if result["success"]:
                                logger.info(f"✓ PDF generated successfully using ReportLab fallback: {result['generator_used']}")
                                pdf_generated = True
                            else:
                                logger.error(f"✗ ReportLab fallback also failed: {result['error']}")
                        except Exception as fallback_error:
                            logger.error(f"✗ ReportLab fallback failed: {str(fallback_error)}")
            
            elif PDF_ENGINE == "reportlab":
                try:
                    result = generate_pdf_report_dual_lane(
                        template_data=template_data,
                        output_path=pdf_path,
                        workflow_diagram=per_sample_workflow
                    )
                    if result["success"]:
                        logger.info(f"✓ PDF report generated successfully using ReportLab: {result['generator_used']}")
                        pdf_generated = True
                    else:
                        logger.error(f"✗ ReportLab PDF generation failed: {result['error']}")
                        if PDF_FALLBACK:
                            logger.info("🔄 Attempting WeasyPrint fallback...")
                            try:
                                generate_pdf_from_html(pdf_html_content, pdf_path)
                                logger.info(f"✓ PDF generated successfully using WeasyPrint fallback")
                                pdf_generated = True
                            except Exception as fallback_error:
                                logger.error(f"✗ WeasyPrint fallback failed: {str(fallback_error)}")
                except Exception as e:
                    logger.error(f"✗ ReportLab PDF generation failed: {str(e)}")
                    if PDF_FALLBACK:
                        logger.info("🔄 Attempting WeasyPrint fallback...")
                        try:
                            generate_pdf_from_html(pdf_html_content, pdf_path)
                            logger.info(f"✓ PDF generated successfully using WeasyPrint fallback")
                            pdf_generated = True
                        except Exception as fallback_error:
                            logger.error(f"✗ WeasyPrint fallback failed: {str(fallback_error)}")
            
            # Add to report paths if PDF was generated
            if pdf_generated:
                server_pdf_path = f"/reports/{patient_id}/{base_filename}.pdf"
                report_paths["pdf_path"] = server_pdf_path
                data["pdf_report_url"] = server_pdf_path
            else:
                # Create a simple text file as fallback
                try:
                    txt_path = pdf_path.replace('.pdf', '.txt')
                    with open(txt_path, 'w', encoding='utf-8') as f:
                        f.write(f"PDF GENERATION FAILED: All engines failed\n\n")
                        f.write("Report content would be here.\n")
                        f.write("Please check the HTML report instead.\n")
                    logger.info(f"Created fallback text file: {txt_path}")
                except Exception as txt_error:
                    logger.error(f"Failed to create fallback text file: {str(txt_error)}")
            
            logger.info(f"=== PDF GENERATION END (Engine: {PDF_ENGINE}) ===")
        
        # Include PharmCAT original reports if enabled
        # Check if pharmacat report files already exist in the report directory
        pharmcat_html_filename = f"{patient_id}_pgx_pharmcat.html"
        pharmcat_html_path = os.path.join(report_dir, pharmcat_html_filename)
        
        # PharmCAT HTML report
        if REPORT_CONFIG["show_pharmcat_html_report"]:
            logger.info("Processing PharmCAT HTML report (enabled via INCLUDE_PHARMCAT_HTML)")
            # Look for the original PharmCAT HTML report
            pharmcat_html_file = os.path.join(report_dir, f"{patient_id}.report.html")
            if os.path.exists(pharmcat_html_file):
                # Copy it with our standardized naming if it doesn't already exist
                if not os.path.exists(pharmcat_html_path):
                    shutil.copy(pharmcat_html_file, pharmcat_html_path)
                    logger.info(f"PharmCAT HTML report copied to: {pharmcat_html_path}")
            
            # Add to report paths if the file exists
            if os.path.exists(pharmcat_html_path):
                server_pharmcat_html_path = f"/reports/{patient_id}/{pharmcat_html_filename}"
                report_paths["pharmcat_html_path"] = server_pharmcat_html_path
                data["pharmcat_html_report_url"] = server_pharmcat_html_path
                logger.info(f"PharmCAT HTML report URL added: {server_pharmcat_html_path}")
            else:
                logger.warning("PharmCAT HTML report not found in report directory")
        else:
            logger.info("PharmCAT HTML report processing disabled via INCLUDE_PHARMCAT_HTML environment variable")
        
        # PharmCAT JSON report
        if REPORT_CONFIG["show_pharmcat_json_report"]:
            logger.info("Processing PharmCAT JSON report (enabled via INCLUDE_PHARMCAT_JSON)")
            pharmcat_json_filename = f"{patient_id}_pgx_pharmcat.json"
            pharmcat_json_path = os.path.join(report_dir, pharmcat_json_filename)
            pharmcat_json_file = os.path.join(report_dir, f"{patient_id}.report.json")
            
            if os.path.exists(pharmcat_json_file):
                if not os.path.exists(pharmcat_json_path):
                    shutil.copy(pharmcat_json_file, pharmcat_json_path)
                    logger.info(f"PharmCAT JSON report copied to: {pharmcat_json_path}")
            
            if os.path.exists(pharmcat_json_path):
                server_pharmcat_json_path = f"/reports/{patient_id}/{pharmcat_json_filename}"
                report_paths["pharmcat_json_path"] = server_pharmcat_json_path
                data["pharmcat_json_report_url"] = server_pharmcat_json_path
                logger.info(f"PharmCAT JSON report URL added: {server_pharmcat_json_path}")
            else:
                logger.warning("PharmCAT JSON report not found in report directory")
        else:
            logger.info("PharmCAT JSON report processing disabled via INCLUDE_PHARMCAT_JSON environment variable")
        
        # PharmCAT TSV report
        if REPORT_CONFIG["show_pharmcat_tsv_report"]:
            logger.info("Processing PharmCAT TSV report (enabled via INCLUDE_PHARMCAT_TSV)")
            pharmcat_tsv_filename = f"{patient_id}_pgx_pharmcat.tsv"
            pharmcat_tsv_path = os.path.join(report_dir, pharmcat_tsv_filename)
            pharmcat_tsv_file = os.path.join(report_dir, f"{patient_id}.report.tsv")
            
            if os.path.exists(pharmcat_tsv_file):
                if not os.path.exists(pharmcat_tsv_path):
                    shutil.copy(pharmcat_tsv_file, pharmcat_tsv_path)
                    logger.info(f"PharmCAT TSV report copied to: {pharmcat_tsv_path}")
            
            if os.path.exists(pharmcat_tsv_path):
                server_pharmcat_tsv_path = f"/reports/{patient_id}/{pharmcat_tsv_filename}"
                report_paths["pharmcat_tsv_path"] = server_pharmcat_tsv_path
                data["pharmcat_tsv_report_url"] = server_pharmcat_tsv_path
                logger.info(f"PharmCAT TSV report URL added: {server_pharmcat_tsv_path}")
            else:
                logger.warning("PharmCAT TSV report not found in report directory")
        else:
            logger.info("PharmCAT TSV report processing disabled via INCLUDE_PHARMCAT_TSV environment variable")
        
        # Add the processed data to the report paths for reference
        report_paths["processed_data"] = data
        
        logger.info(f"Report generation completed successfully")
        logger.info(f"Generated {len(report_paths)} report paths")
        logger.info(f"Report paths keys: {list(report_paths.keys())}")
        
        return report_paths
    
    except Exception as e:
        logger.error(f"Error generating report: {str(e)}")
        raise ReportGenerationError(f"Failed to generate report: {str(e)}") 