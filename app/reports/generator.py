import base64
import glob
import json
import logging
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

# Optional import: WeasyPrint needs native Pango/GObject libraries that are present in the
# container but not on a bare host. Importing it at module scope makes `import app.main`
# (and therefore the whole test suite) fail outside Docker. Guard it the way pysam is
# guarded in app/api/utils/file_processor.py; generate_pdf_from_html already branches on
# FontConfiguration being falsy.
try:
    from weasyprint import CSS, HTML
    from weasyprint.text.fonts import FontConfiguration  # type: ignore

    _HAS_WEASYPRINT = True
except Exception as _weasyprint_import_error:  # optional dependency at runtime
    CSS = None  # type: ignore
    HTML = None  # type: ignore
    FontConfiguration = None  # type: ignore
    _HAS_WEASYPRINT = False
    # Loud on purpose: inside the container this import is expected to succeed, and a silent
    # failure would just downgrade every PDF to ReportLab with nobody noticing.
    logging.getLogger(__name__).warning(
        "WeasyPrint unavailable (%s); PDF generation will fall back to ReportLab. "
        "Inside the container this indicates missing Pango/GObject libraries.",
        _weasyprint_import_error,
    )

from app.core.version_manager import get_all_versions, get_versions_dict
from app.pharmcat.pharmcat_client import normalize_pharmcat_results
from app.pharmcat.report_json import extract_matcher_metadata
from app.reports.evidence import classify_evidence
from app.reports.pharmcat_tsv_parser import (
    parse_pharmcat_tsv,
    prefer_source_over_lookup,
)
from app.reports.provenance import (
    CALLED_BY_NO_CALL,
    resolve_called_by,
    resolve_guideline_source,
)
from app.reports.pypgx_pipeline_parser import parse_gene_pipeline
from app.services.pharmcat_data_service import PharmCATDataService
from app.visualizations.workflow_diagram import (
    build_simple_html_from_workflow,
    render_kroki_mermaid_svg,
    render_simple_png_from_workflow,
    render_with_graphviz,
    render_workflow,
    render_workflow_png_data_uri,
)

# Keep below import commented out; this prevents circular import
# from app.reports.pdf_generators import generate_pdf_report_dual_lane

# FHIR Export - imported lazily to avoid circular imports; see fhir_export_enabled()
# below and the FHIRExportService import inside generate_report. There is no
# FHIR_EXPORT_ENABLED constant to import anywhere: the flag is resolved per call.


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
        project_section_match = re.search(
            r"\[project\](.*?)\n\[", content, flags=re.DOTALL
        )
        section = project_section_match.group(1) if project_section_match else content
        version_match = re.search(
            r"^\s*version\s*=\s*\"([^\"]+)\"", section, flags=re.MULTILINE
        )
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
        authors_block_match = re.search(
            r"^\s*authors\s*=\s*\[(.*?)\]", content, flags=re.DOTALL | re.MULTILINE
        )
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
    return os.getenv("SOURCE_URL", "https://github.com/Zaromics/ZaroPGx")


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
            svg_str = svg_str.replace(
                "<svg", '<svg preserveAspectRatio="xMidYMid meet"', 1
            )

        # Ensure text elements have proper styling and presentation attributes
        # Fix any style fill:none on text/tspan
        svg_str = re.sub(
            r'(<text[^>]*style=["\"][^"\"]*)fill\s*:\s*none\s*;?',
            r"\1fill:#000000;",
            svg_str,
        )
        svg_str = re.sub(
            r'(<tspan[^>]*style=["\"][^"\"]*)fill\s*:\s*none\s*;?',
            r"\1fill:#000000;",
            svg_str,
        )
        # Add presentation attributes to text/tspan for WeasyPrint
        svg_str = re.sub(
            r"<text([^>]*?)>",
            r'<text\1 fill="#000000" font-family="Arial, sans-serif" font-size="12px">',
            svg_str,
        )
        svg_str = re.sub(
            r"<tspan([^>]*?)>",
            r'<tspan\1 fill="#000000" font-family="Arial, sans-serif" font-size="12px">',
            svg_str,
        )

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
            items.append(
                {
                    "name": name,
                    "version": _normalize_version_text(version),
                    "source": source,
                }
            )

    # Ensure all version strings are normalized
    for item in items:
        item["version"] = _normalize_version_text(item.get("version", "N/A"))

    return items


def _versions_index() -> Dict[str, str]:
    """Get versions index using centralized version management."""
    return get_versions_dict()


# TODO: integrate zotero bridge
def build_citations() -> List[Dict[str, str]]:
    """Build academically styled citations with versions (when available)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    vmap = _versions_index()
    pypgx_ver = vmap.get("pypgx") or "0.26.0"
    pharmcat_ver = vmap.get("pharmcat") or "3.4.0"
    gatk_ver = vmap.get("gatk") or "4.6.2.0"
    zarohla_ver = vmap.get("zarohla") or "1.5.0"
    mtdna_server_2_ver = vmap.get("mtdna-server-2") or "2.1.16"

    citations: List[Dict[str, str]] = []
    citations.append(
        {
            "name": "PyPGx",
            "text": f"PyPGx, version {pypgx_ver}. Available at: https://pypgx.readthedocs.io/en/latest/index.html (accessed {today}).",
            "repo": "https://github.com/sbslee/pypgx",
        }
    )
    citations.append(
        {
            "name": "PharmCAT",
            "text": f"Pharmacogenomics Clinical Annotation Tool (PharmCAT), version {pharmcat_ver}. Available at: https://pharmcat.clinpgx.org/ (accessed {today}).",
            "repo": "https://github.com/PharmGKB/PharmCAT",
        }
    )
    citations.append(
        {
            "name": "GATK",
            "text": f"Genome Analysis Toolkit (GATK), version {gatk_ver}. Broad Institute. Available at: https://gatk.broadinstitute.org/ (accessed {today}).",
            "repo": "https://github.com/broadinstitute/gatk",
        }
    )
    citations.append(
        {
            "name": "CPIC",
            "text": f"Clinical Pharmacogenetics Implementation Consortium (CPIC). Available at: https://www.clinpgx.org/ (accessed {today}).",
            "repo": "https://github.com/cpicpgx",
        }
    )
    citations.append(
        {
            "name": "DWPG",
            "text": f"Dutch Pharmacogenomics Working Group. Royal Dutch Pharmacist's Association (KNMP). Available at: https://www.knmp.nl/dossiers/farmacogenetica/pharmacogenetics (accessed {today}).",
        }
    )
    citations.append(
        {
            "name": "FDA",
            "text": f"U.S. Food and Drug Administration (FDA) Pharmacogenetic Associations. Available at: https://www.fda.gov/medical-devices/precision-medicine/table-pharmacogenetic-associations (accessed {today}).",
        }
    )
    citations.append(
        {
            "name": "mtDNA-server-2",
            "text": f"mtDNA-server-2-based mitochondrial typing. Available at: https://mitoverse.readthedocs.io/mtdna-server/mtdna-server/ (accessed {today}).",
            "repo": "https://github.com/genepi/mtdna-server-2",
        }
    )
    citations.append(
        {
            "name": "PharmGKB",
            "text": f"Pharmacogenomics Knowledgebase (PharmGKB). Available at: https://www.pharmgkb.org/ (accessed {today}).",
            "repo": "https://github.com/PharmGKB",
        }
    )
    citations.append(
        {
            "name": "ZaroHLA",
            "text": f"OptiType-based HLA typing with ZaroHLA, version {zarohla_ver}. Available at: https://github.com/FRED-2/OptiType (accessed {today}).",
            "repo": "https://github.com/ZaroHLA",
        }
    )
    return citations


# Report display configuration
# Controls which reports are included in the response and shown to users
#
# Configure logging first
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# PDF Generation Configuration via environment variables:
# - PDF_ENGINE: 'weasyprint' (default) or 'reportlab'
# - PDF_FALLBACK: 'true' or 'false' (default: true)
PDF_ENGINE = os.environ.get("PDF_ENGINE", "weasyprint").lower()
PDF_FALLBACK = os.environ.get("PDF_FALLBACK", "true").lower() == "true"

# Validate PDF engine setting
if PDF_ENGINE not in ["weasyprint", "reportlab"]:
    logger.warning(f"Invalid PDF_ENGINE '{PDF_ENGINE}', defaulting to 'weasyprint'")
    PDF_ENGINE = "weasyprint"

logger.info(
    f"PDF Generation Configuration: Engine={PDF_ENGINE}, Fallback={PDF_FALLBACK}"
)


def _is_uuid_like(s: str) -> bool:
    """Return True if *s* parses as a UUID (used to detect placeholder sample IDs)."""
    try:
        uuid.UUID(str(s))
        return True
    except Exception:
        return False


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


# FHIR Export - automatically generate FHIR R4 exports during report generation.
#
# Deliberately NOT a module-level constant. This module is imported from
# app/main.py's import block (app/main.py:76), which runs *before* that file's
# load_dotenv(), so a constant here would snapshot a pre-.env environment while
# app/main.py -- which decides whether to mount /fhir/* -- reads a post-.env one:
# two readers, two answers, and report generation could skip the export for a run
# whose /fhir/* endpoints are live. That is the exact failure mode
# app/utils/outside_calls_override.py documents as its reason to resolve on
# demand, and the one tests/test_fhir_export_flag.py pins.
#
# fhir_export_service is imported lazily for the same circular-import reason the
# FHIRExportService import inside generate_report gives.
def fhir_export_enabled() -> bool:
    """Resolve FHIR_EXPORT_ENABLED per call, through the one shared parser."""
    from app.services.fhir_export_service import fhir_export_enabled as _resolve

    return _resolve()


def lookup_pharmcat_run_id(db_session, job_id) -> Optional[str]:
    """Read the PharmCAT run id the upload path linked to this job.

    ``JobService.link_pharmcat_run`` writes ``pharmcat_run_id`` into
    ``jobs.job_metadata`` as soon as ``load_pharmcat_file`` returns, so by report
    time it is there for every run that produced parsable PharmCAT output.

    The FHIR export lane read it as ``Workflow.workflow_metadata`` --  the names
    ``db/init/migrations/04_rename_workflows_to_jobs.sql`` retired. ``Workflow``
    does not exist in ``app.api.db``, so the read raised ``ImportError`` on every
    call and the run id was unconditionally ``None``. That is not cosmetic:
    ``FHIRExportService`` uses it for the Bundle identifier and the export
    filename, both of which silently degraded to the job id.

    Returns ``None`` -- never raises -- when there is no session, no job, no
    metadata, or no link yet; the caller treats that as "fall back to job id".
    """
    if db_session is None or not job_id:
        return None
    try:
        import uuid as uuid_module

        from app.api.db import Job

        job_uuid = uuid_module.UUID(str(job_id))
        job_row = db_session.query(Job).filter(Job.id == job_uuid).first()
        metadata = getattr(job_row, "job_metadata", None) if job_row else None
        if isinstance(metadata, dict):
            run_id = metadata.get("pharmcat_run_id")
            return str(run_id) if run_id else None
    except Exception as e:
        logger.warning(f"Could not read PharmCAT run_id from job metadata: {e}")
    return None


# Log the configuration for debugging
logger.info(
    f"PharmCAT Report Configuration - HTML: {INCLUDE_PHARMCAT_HTML}, JSON: {INCLUDE_PHARMCAT_JSON}, TSV: {INCLUDE_PHARMCAT_TSV}"
)
logger.info(f"Executive Summary Configuration - Use TSV: {EXECSUM_USE_TSV}")

# Report configuration dictionary
REPORT_CONFIG = {
    # Core report settings
    "write_pdf": True,  # Generate PDF report
    "write_html": True,  # Generate HTML report
    "write_interactive_html": True,  # Generate interactive HTML report
    "write_json": True,  # Generate JSON export
    "write_tsv": True,  # Generate TSV export
    # Visualization assets
    "write_workflow_svg": True,  # Write workflow.svg alongside report outputs
    "write_workflow_png": True,  # Also write workflow.png for robust PDF embedding
    # PharmCAT original reports - now controlled by environment variables
    "show_pharmcat_html_report": INCLUDE_PHARMCAT_HTML,  # Original HTML report from PharmCAT
    "show_pharmcat_json_report": INCLUDE_PHARMCAT_JSON,  # Original JSON report from PharmCAT
    "show_pharmcat_tsv_report": INCLUDE_PHARMCAT_TSV,  # Original TSV report from PharmCAT
    # FHIR Export is intentionally absent from this dict: REPORT_CONFIG is built at
    # import time, so storing the flag here would re-freeze exactly what
    # fhir_export_enabled() exists to avoid. generate_report calls the resolver.
}

# Configure WeasyPrint logging for debugging text rendering issues
weasyprint_logger = logging.getLogger("weasyprint")
weasyprint_logger.setLevel(logging.DEBUG)
if not weasyprint_logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[WEASYPRINT] %(levelname)s: %(message)s"))
    weasyprint_logger.addHandler(handler)

# Constants
TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
CSS_FILE = os.path.join(TEMPLATE_DIR, "style.css")


# Wild-type labelling (BACKLOG 235).
#
# PharmCAT reports a reference diplotype with no phenotype when it found nothing
# to call. What that *means* depends on what the pipeline was given, so the report
# says two different things:
#
#   VCF                 -> "Possibly Wild Type". A variant-only file is silent
#                          about positions it does not list, so the absence of a
#                          variant call is not evidence of the reference allele.
#   BAM/CRAM/SAM/FASTQ  -> "Likely Wild Type". Aligned reads cover the locus, so
#                          the absence of a variant is positive evidence.
#   anything else       -> no label at all. An unknown provenance cannot support
#                          either claim, and inventing one would be fabrication.
#
# This lived inline in three places (the TSV Executive Summary in the PDF lane,
# _build_canonical_diplotypes, and the TSV Executive Summary in the HTML lane) and
# had no test anywhere. The copies agreed on every input reachable today, but only
# because each caller happened to lower-case file_type first: the HTML-lane copy
# compared it raw, so a single upstream change to that normalisation would have
# made one lane stop labelling while the other two carried on.
WILD_TYPE_VARIANT_ONLY_LABEL = "Possibly Wild Type"
WILD_TYPE_ALIGNED_READS_LABEL = "Likely Wild Type"

# Reference calls, in the spellings PharmCAT and the TSV parser actually emit.
_REFERENCE_DIPLOTYPES = frozenset(
    {"*1/*1", "REFERENCE/REFERENCE", "*1 / *1", "REFERENCE / REFERENCE"}
)
# Spellings that mean "no phenotype reported", not a phenotype named "Unknown".
_ABSENT_PHENOTYPES = frozenset({"n/a", "na", "unknown", "none", "-", "."})
_VARIANT_ONLY_FILE_TYPES = frozenset({"vcf", "vcf.gz", "vcf.bgz"})
_ALIGNED_READ_FILE_TYPES = frozenset({"bam", "fastq", "fq", "cram", "sam"})


def wild_type_phenotype(
    diplotype: Any, phenotype: Any, file_type: Any
) -> Optional[str]:
    """Return the wild-type label for a row, or ``None`` to leave it alone.

    ``None`` means "not a wild-type row" *and* "file type cannot support the
    claim" -- callers must not substitute a default label for it.
    """
    diplotype_str = str(diplotype or "").strip()
    if diplotype_str.upper() not in _REFERENCE_DIPLOTYPES:
        return None

    phenotype_str = str(phenotype or "").strip()
    if phenotype_str and phenotype_str.lower() not in _ABSENT_PHENOTYPES:
        return None

    normalized_file_type = str(file_type or "").strip().lower()
    if normalized_file_type in _VARIANT_ONLY_FILE_TYPES:
        return WILD_TYPE_VARIANT_ONLY_LABEL
    if normalized_file_type in _ALIGNED_READ_FILE_TYPES:
        return WILD_TYPE_ALIGNED_READS_LABEL
    return None


def activity_score_num(value: Any) -> Optional[float]:
    """Coerce activity scores to float for templates (PyPGx may leave strings)."""
    if value is None or value is False:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text.lower() in {"n/a", "na", "none", "null", "unknown", "-"}:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


_EMPTY_MATCHER_METADATA: Dict[str, Optional[str]] = {
    "genome_build": None,
    "named_allele_matcher_version": None,
    "data_version": None,
}


# The filenames production actually writes into a report directory.
#
# PharmCAT's *native* output is named ``<base>.report.json``, but that name never
# reaches a report directory: the PharmCAT service discovers it inside its own
# temp workdir and copies it out under three renamed forms -- deliberately, "to
# avoid colliding with our own JSON export" (docker/pharmcat/pharmcat.py:877,
# :913, :930). ``upload_router.py:255`` reconciles the canonical one. Globbing
# for ``*.report.json`` here matched nothing on any real run.
_MATCHER_METADATA_FILE_SUFFIXES = (
    "_pgx_pharmcat.json",  # canonical PharmCAT JSON copy (pharmcat.py:877)
    "_raw_report.json",  # verbatim raw copy (pharmcat.py:913)
    "_pgx_report.json",  # standard copy (pharmcat.py:930)
)


def probe_matcher_metadata(report_dir: str, report_id: str) -> Dict[str, Optional[str]]:
    """Read run-derived provenance from the run's PharmCAT JSON output (159).

    ``report_dir`` must be the directory the artifacts actually land in -- i.e.
    the already-nested ``/data/reports/{patient_id}/{job_id}`` path that both
    callers pass as ``output_dir`` (``upload_router.py:589``, ``main.py:1579``)
    and that ``generate_report`` assigns straight to ``report_dir``. Do not
    rebuild it from parts.

    Tries ``{report_id}<suffix>`` for each known suffix, then falls back to a
    newest-first glob per suffix, mirroring the existing filename probe at
    :1150 (artifact basenames are not always the report id -- the PharmCAT
    service names them from its own ``name_base``).

    Never raises. A run with no PharmCAT JSON simply renders no provenance
    sentences.

    Note this cannot reuse ``generate_report``'s later copy of the JSON: that
    copy happens well after template data is assembled.
    """
    candidates = [
        os.path.join(report_dir, f"{report_id}{suffix}")
        for suffix in _MATCHER_METADATA_FILE_SUFFIXES
    ]
    for suffix in _MATCHER_METADATA_FILE_SUFFIXES:
        try:
            others = glob.glob(os.path.join(report_dir, f"*{suffix}"))
            others.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            candidates.extend(others)
        except Exception as e:
            logger.debug("Swallowed exception: %s", e, exc_info=True)

    seen = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        try:
            if not os.path.exists(path):
                continue
            with open(path, "r", encoding="utf-8") as fh:
                meta = extract_matcher_metadata(json.load(fh))
            # A file that parses but carries no provenance must not shadow a
            # later candidate that does.
            if any(value is not None for value in meta.values()):
                return meta
        except Exception as e:
            logger.debug("Swallowed exception: %s", e, exc_info=True)

    return dict(_EMPTY_MATCHER_METADATA)


# Initialize Jinja2 environment.
#
# autoescape is not optional here. The interactive report writes free text into
# HTML attributes -- data-recommendation="{{ rec.recommendation }}" among them --
# and PharmCAT's own guideline prose contains markup: <h4 id="other-considerations">
# appears in 45 of the 151 data-recommendation attributes of a real run. Without
# escaping, the browser terminates the attribute at that first inner quote and the
# rest of the recommendation leaks out as stray attributes on the div, so
# pgx-report.js reads truncated dosing text. Gene names, diplotypes, sample
# identifiers and filenames all reach the page the same way, and some of them come
# from an uploaded file.
#
# The one value that is genuinely markup is the drug recommendation body, which
# the templates now mark |safe explicitly. It is the only field carrying tags or
# entities across all 24 runs under data/reports.
env = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    autoescape=select_autoescape(["html", "xml"]),
)
env.filters["activity_score_num"] = activity_score_num


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
        logger.info(
            f"Generating PDF report for patient {patient_id}, report {report_id}"
        )

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
                tsv_candidates.append(
                    os.path.join(report_dir_probe, f"{patient_id}_pgx_pharmcat.tsv")
                )
                tsv_candidates.append(
                    os.path.join(report_dir_probe, f"{patient_id}.report.tsv")
                )
                try:
                    tsv_candidates.extend(
                        glob.glob(os.path.join(report_dir_probe, "*_pgx_pharmcat.tsv"))
                    )
                    any_pharmcat = glob.glob(
                        os.path.join(report_dir_probe, "*.pharmcat.tsv")
                    )
                    if any_pharmcat:
                        any_pharmcat.sort(
                            key=lambda p: os.path.getmtime(p), reverse=True
                        )
                        tsv_candidates.extend(any_pharmcat)
                except Exception as e:
                    logger.debug("Swallowed exception: %s", e, exc_info=True)
                # Also probe parent directory of the report directory (the patient-level dir)
                try:
                    parent_dir = os.path.dirname(report_dir_probe)
                    tsv_candidates.append(
                        os.path.join(parent_dir, f"{patient_id}_pgx_pharmcat.tsv")
                    )
                    tsv_candidates.append(
                        os.path.join(parent_dir, f"{patient_id}.report.tsv")
                    )
                    tsv_candidates.extend(
                        glob.glob(os.path.join(parent_dir, "*_pgx_pharmcat.tsv"))
                    )
                    any_pharmcat_parent = glob.glob(
                        os.path.join(parent_dir, "*.pharmcat.tsv")
                    )
                    if any_pharmcat_parent:
                        any_pharmcat_parent.sort(
                            key=lambda p: os.path.getmtime(p), reverse=True
                        )
                        tsv_candidates.extend(any_pharmcat_parent)
                except Exception as e:
                    logger.debug("Swallowed exception: %s", e, exc_info=True)
                tsv_path = next((p for p in tsv_candidates if os.path.exists(p)), None)
                if tsv_path:
                    _diplos, _ = parse_pharmcat_tsv(tsv_path)
                    try:
                        logger.info(
                            f"Executive Summary TSV selected (PDF path): {tsv_path}"
                        )
                    except Exception as e:
                        logger.debug("Swallowed exception: %s", e, exc_info=True)
                    # Determine file type from workflow for wild type labeling
                    inferred_file_type = "vcf"
                    if workflow and isinstance(workflow, dict):
                        inferred_file_type = str(
                            workflow.get("file_type", "vcf")
                        ).lower()

                    for row in _diplos:
                        # Get phenotype, applying wild type logic if needed
                        # Prefer source; fallback to recommendation lookup
                        rec_lookup_dip = (row.get("rec_lookup_diplotype") or "").strip()
                        source_dip = (row.get("diplotype") or "").strip()
                        diplotype_str = prefer_source_over_lookup(
                            source_dip, rec_lookup_dip
                        )
                        phenotype_str = prefer_source_over_lookup(
                            str(row.get("phenotype") or ""),
                            str(row.get("rec_lookup_phenotype") or ""),
                        )
                        wild_type_label = wild_type_phenotype(
                            diplotype_str, phenotype_str, inferred_file_type
                        )
                        if wild_type_label:
                            phenotype_str = wild_type_label
                            logger.debug(
                                f"TSV PDF: Assigned '{wild_type_label}' to {row.get('gene')}"
                            )

                        execsum_rows_from_tsv.append(
                            {
                                "gene": row.get("gene", ""),
                                "rec_lookup_diplotype": diplotype_str,  # Use fallback value
                                "rec_lookup_phenotype": phenotype_str,
                                "rec_lookup_activity_score": (
                                    row.get("activity_score")
                                    if row.get("activity_score") not in (None, "")
                                    else row.get("rec_lookup_activity_score")
                                ),
                            }
                        )
            except Exception:
                execsum_rows_from_tsv = []

        # 159: run-derived provenance. Probed unconditionally -- EXECSUM_USE_TSV
        # defaults to false and the provenance block renders on both branches.
        matcher_meta = probe_matcher_metadata(
            os.path.dirname(report_path), str(report_id)
        )

        report_data = {
            "patient_id": patient_id,
            "report_id": report_id,
            "sample_identifier": sample_identifier if sample_identifier else patient_id,
            # New: explicit display sample id for templates that prefer it (NEEDS FIXING)
            "display_sample_id": sample_identifier if sample_identifier else patient_id,
            "report_date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "diplotypes": diplotypes,
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
            "execsum_from_tsv": (
                execsum_rows_from_tsv
                if (EXECSUM_USE_TSV and execsum_rows_from_tsv)
                else None
            ),
            # 159: run-derived provenance (each rendered only if resolved)
            "genome_build": matcher_meta["genome_build"],
            "named_allele_matcher_version": matcher_meta[
                "named_allele_matcher_version"
            ],
            "pharmcat_data_version": matcher_meta["data_version"],
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
            svg_path = os.path.join(report_dir, f"{report_id}_workflow.svg")

            if os.path.exists(svg_path):
                # Read SVG content directly for PDF embedding
                with open(svg_path, "r", encoding="utf-8") as f_svg:
                    svg_content = f_svg.read()
                    workflow_svg = f'<div class="workflow-figure">{svg_content}</div>'
                    logger.info(
                        f"✓ Using existing SVG workflow for PDF: {len(svg_content)} chars"
                    )
            else:
                # If SVG doesn't exist, generate it first
                logger.info("Generating SVG workflow for PDF...")
                svg_bytes = render_workflow(fmt="svg", workflow=workflow)
                if svg_bytes:
                    svg_content = svg_bytes.decode("utf-8")
                    workflow_svg = f'<div class="workflow-figure">{svg_content}</div>'
                    logger.info(
                        f"✓ Generated SVG workflow for PDF: {len(svg_content)} chars"
                    )

                    # Also save the SVG file for future use
                    with open(svg_path, "w", encoding="utf-8") as f_svg:
                        f_svg.write(svg_content)
                    logger.info(f"✓ Saved SVG workflow to: {svg_path}")
                else:
                    logger.warning(
                        "✗ SVG workflow generation failed, using fallback text"
                    )
                    workflow_svg = (
                        "<em>Workflow: Upload → Detect → VCF → PharmCAT → Reports</em>"
                    )
        except Exception as e:
            logger.error(f"✗ SVG workflow approach failed: {str(e)}", exc_info=True)
            workflow_svg = (
                "<em>Workflow: Upload → Detect → VCF → PharmCAT → Reports</em>"
            )
        logger.info(
            f"Final PDF workflow - HTML: {len(workflow_svg) if workflow_svg else 0}, PNG: {len(workflow_png_data_uri) if workflow_png_data_uri else 0}"
        )
        logger.info("=== PDF WORKFLOW DIAGRAM DEBUG END ===")

        # Ensure we always have a workflow display
        if not workflow_svg:
            workflow_svg = (
                "<em>Workflow: Upload → Detect → VCF → PharmCAT → Reports</em>"
            )

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
            logger.error(
                f"PDF generation failed, creating HTML fallback: {str(pdf_error)}"
            )
            html_fallback_path = report_path.replace(".pdf", ".html")
            with open(html_fallback_path, "w", encoding="utf-8") as f:
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
    if not _HAS_WEASYPRINT:
        raise ImportError(
            "WeasyPrint is not available in this environment; "
            "set PDF_ENGINE=reportlab or run inside the app container."
        )
    try:
        # Load CSS if it exists and configure fonts
        stylesheets = []
        # Add project stylesheet if present
        if os.path.exists(CSS_FILE):
            if FontConfiguration:
                stylesheets.append(
                    CSS(filename=CSS_FILE, font_config=FontConfiguration())
                )
            else:
                stylesheets.append(CSS(filename=CSS_FILE))
        # Enhanced print-safe defaults via inline CSS for better workflow diagram handling
        # Focus on PNG image rendering which works reliably in WeasyPrint
        pdf_css = """
            @page {
                size: A4;
                margin: 16mm;
                @bottom-right {
                    content: "Page " counter(page) " of " counter(pages);
                    font-size: 10px;
                    color: #888;
                }
            }
            @page header-page {
                size: A4;
                margin: 8mm;
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
            /* Apply narrow margins to header section */
            .header-section {
                page: header-page;
            }
        """
        if FontConfiguration:
            stylesheets.append(CSS(string=pdf_css, font_config=FontConfiguration()))
        else:
            stylesheets.append(CSS(string=pdf_css))

        # Generate PDF
        html = HTML(string=html_content)
        if FontConfiguration:
            html.write_pdf(
                target=output_path,
                stylesheets=stylesheets,
                font_config=FontConfiguration(),
                zoom=1.0,
            )
        else:
            html.write_pdf(target=output_path, stylesheets=stylesheets, zoom=1.0)
    except Exception as e:
        logger.error(f"Error converting HTML to PDF: {str(e)}")
        # Create simple text file as fallback if PDF generation fails
        try:
            with open(output_path.replace(".pdf", ".txt"), "w") as f:
                f.write(
                    f"PDF GENERATION FAILED: {str(e)}\n\nRaw HTML content below:\n\n"
                )
                f.write(html_content)
            logger.info(
                f"Created fallback text file at {output_path.replace('.pdf', '.txt')}"
            )
        except OSError as fallback_err:
            logger.debug(
                "Failed to write PDF fallback text file: %s",
                fallback_err,
                exc_info=True,
            )
        raise


def get_disclaimer() -> str:
    """
    Return the legal disclaimer for pharmacogenomic reports.
    """
    return """
    DISCLAIMER: This pharmacogenomic report is for informational purposes only. ZaroPGx is an independently 
    developed hobby software. It is not intended for use as a substitute for professional medical advice, 
    diagnosis, or treatment. The content herein is based on guidelines from the Clinical Pharmacogenetics 
    Implementation Consortium (CPIC), the Pharmacogenomics Knowledgebase (PharmGKB), the U.S. Food and Drug 
    Administration (FDA), and the Dutch Pharmacogenetics Working Group (DPWG). The content may change as new 
    research becomes available. The content may vary depending on the sequencing or genotyping method used, 
    the quality and composition of the genomic data submitted, and the versions of the constituent software.

    Results should be considered in an educational context only. 
    Should the need for qualified interpretation arise, this report by its nature can only serve as a prompt 
    for professional investigation by a qualified physician or medical genetics practitioner.
    Responsible use of this report in such a context entails verification and validation of the findings by an  
    appropriately accredited sequencing or genotyping laboratory followed by interpretation and consultation 
    with an appropriately credentialed professional. 

    Please do not make any changes to your lifestyle or treatment regimen solely on the basis of this report.
    First, consult your trusted physician and verify the findings in the appropriate clinical manner.
    
    If you choose to ignore this guidance, be aware that you do so at your own risk.
    ZaroPGx is provided with absolutely no warranty, and the author(s) are not liable for any consequences of its use.

    This tool is made with care, standing on the shoulders of myriad underlying free software, in the hope that 
    it is useful to you. Thank you for using it, and thank you for supporting free and open source software.

    """


def organize_gene_drug_recommendations(
    recommendations: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
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
    sample_identifier: str | None = None,
    workflow_warnings: List[str] | None = None,
    pharmcat_assume_ref_methodology: str | None = None,
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
    """
    try:
        logger.info(
            f"Generating interactive HTML report for patient {patient_id}, report {report_id}"
        )

        # Ensure the directory exists
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # Map recommendations if they're in the new format
        template_recommendations = recommendations
        if (
            recommendations
            and "genes" in recommendations[0]
            and "gene" not in recommendations[0]
        ):
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
            header_txt_candidates.append(
                os.path.join(patient_dir, f"{report_id}.header.txt")
            )
            header_txt_candidates.append(
                os.path.join(patient_dir, f"{patient_id}.header.txt")
            )
            # Add all *.header.txt
            try:
                header_txt_candidates.extend(
                    glob.glob(os.path.join(patient_dir, "*.header.txt"))
                )
            except Exception as e:
                logger.debug("Swallowed exception: %s", e, exc_info=True)
            # Filter to existing and pick newest by mtime
            existing = [p for p in header_txt_candidates if os.path.exists(p)]
            if existing:
                selected = max(existing, key=lambda p: os.path.getmtime(p))
                with open(selected, "r", encoding="utf-8", errors="ignore") as hf:
                    header_text = hf.read()
                logger.info(
                    f"Loaded genomic header text for interactive report from {selected} ({len(header_text)} chars)"
                )
        except Exception as _e:
            logger.debug(f"Interactive header text load skipped: {_e}")

        # Generate Kroki Mermaid SVG for comparison
        try:
            kroki_svg_bytes = render_kroki_mermaid_svg(workflow=workflow)
            if kroki_svg_bytes:
                kroki_svg_text = kroki_svg_bytes.decode("utf-8", errors="ignore")
                workflow_kroki_svg_inline = _sanitize_graphviz_svg(kroki_svg_text)
                logger.info(
                    f"✓ Generated Kroki Mermaid SVG for interactive report: {len(workflow_kroki_svg_inline)} chars"
                )
            else:
                logger.warning(
                    "⚠ Kroki Mermaid SVG generation returned empty result for interactive report"
                )
        except Exception as e:
            logger.error(
                f"✗ Kroki Mermaid SVG generation failed for interactive report: {str(e)}"
            )
            workflow_kroki_svg_inline = ""

        try:
            # Prefer a pre-rendered PNG served by the app (nested patient/job layout)
            png_path_local = os.path.join(report_dir, f"{report_id}_workflow.png")
            if os.path.exists(png_path_local):
                workflow_png_url = (
                    f"/reports/{patient_id}/{report_id}/{report_id}_workflow.png"
                )
        except Exception:
            workflow_png_url = ""
        if not workflow_png_url:
            # Try to render a PNG, save it, and expose as URL
            try:
                png_bytes = render_workflow(fmt="png", workflow=workflow)
                if png_bytes:
                    with open(
                        os.path.join(report_dir, f"{report_id}_workflow.png"), "wb"
                    ) as f_out:
                        f_out.write(png_bytes)
                    workflow_png_url = (
                        f"/reports/{patient_id}/{report_id}/{report_id}_workflow.png"
                    )
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
            except Exception as e:
                logger.debug("Swallowed exception: %s", e, exc_info=True)
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

        # 159: run-derived provenance. `report_dir` is os.path.dirname(output_path)
        # -- the same directory the other two lanes probe -- so the interactive
        # report resolves the identical three facts from the identical file. Without
        # this the print report carried provenance and the interactive one silently
        # did not, for the same run.
        matcher_meta = probe_matcher_metadata(report_dir, str(report_id))

        # Prepare the report data
        report_data = {
            "patient_id": patient_id,
            "report_id": report_id,
            "sample_identifier": sample_identifier if sample_identifier else patient_id,
            "report_date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "diplotypes": diplotypes,
            "recommendations": template_recommendations,
            "organized_recommendations": json.dumps(
                organize_gene_drug_recommendations(template_recommendations)
            ),
            "disclaimer": get_disclaimer(),
            "workflow_png_url": workflow_png_url,
            "workflow_png_data_uri": workflow_png_data_uri,
            "workflow_svg": workflow_svg_inline,
            "workflow_html_fallback": workflow_html_fallback,
            "workflow_kroki_svg": workflow_kroki_svg_inline,  # Added for interactive report
            "platform_info": build_platform_info(),
            "citations": build_citations(),
            "author_name": get_author_name(),
            "license_name": get_license_name(),
            "license_url": get_license_url(),
            "source_url": get_source_url(),
            "current_year": datetime.now().year,
            "header_text": header_text,
            # Add workflow warnings/alerts for report display
            "workflow_warnings": workflow_warnings or [],
            "pharmcat_assume_ref_methodology": pharmcat_assume_ref_methodology,
            # 159: run-derived provenance (each rendered only if resolved)
            "genome_build": matcher_meta["genome_build"],
            "named_allele_matcher_version": matcher_meta[
                "named_allele_matcher_version"
            ],
            "pharmcat_data_version": matcher_meta["data_version"],
        }

        # Compute unified display sample id for Interactive; if it's UUID-like, derive from PharmCAT filenames
        display_sample = report_data.get("sample_identifier") or report_data.get(
            "patient_id"
        )
        if (not display_sample) or _is_uuid_like(display_sample):
            try:
                candidates = []
                for name in os.listdir(patient_dir):
                    if (
                        name.endswith("_pgx_pharmcat.html")
                        or name.endswith("_pgx_pharmcat.json")
                        or name.endswith("_pgx_pharmcat.tsv")
                    ):
                        base = name.split("_pgx_pharmcat")[0]
                        if (
                            base
                            and base != report_data.get("patient_id")
                            and base not in candidates
                        ):
                            candidates.append(base)
                if candidates:
                    display_sample = candidates[0]
                    logger.info(
                        f"Derived display_sample_id from PharmCAT filenames: {display_sample}"
                    )
            except Exception as e:
                logger.debug(
                    "Interactive display_sample_id derivation failed: %s",
                    e,
                    exc_info=True,
                )
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


def _choose_better_gene_entry(
    existing: Dict[str, Any], candidate: Dict[str, Any]
) -> Dict[str, Any]:
    """Heuristic to pick a better gene entry when duplicates are present.

    Priority:
    - Prefer entries with a PharmCAT call
    - Else prefer non-unknown phenotype over unknown
    - Otherwise keep existing

    This is a display tiebreak between two candidate rows, not a provenance
    claim -- the letter that reaches the report comes from
    ``resolve_called_by`` (BACKLOG 28 + 216).
    """
    try:
        # Normalize sources. ``called_by`` first: the resolver has already run
        # on every file-lane row by the time dedupe fires, so it is the one
        # field that reflects what the run recorded.
        existing_source = (
            (
                existing.get("called_by")
                or existing.get("tool_source")
                or existing.get("source")
                or ""
            )
            .strip()
            .upper()
        )
        candidate_source = (
            (
                candidate.get("called_by")
                or candidate.get("tool_source")
                or candidate.get("source")
                or ""
            )
            .strip()
            .upper()
        )
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
        gene_name = (
            (
                candidate.get("gene")
                or existing.get("gene")
                or candidate.get("name")
                or existing.get("name")
                or ""
            )
            .strip()
            .upper()
        )

        # Special precedence -- a TIEBREAK between two candidate rows only.
        # This never produces the letter that reaches the report; provenance
        # comes from resolve_called_by (BACKLOG 28 + 216).
        # - HLA-A/B/C → prefer O (OptiType)
        # - MT-RNR1   → prefer M (mtDNA-server-2)
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
        if _is_unknown_phenotype(
            existing.get("phenotype")
        ) and not _is_unknown_phenotype(candidate.get("phenotype")):
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
            # Normalize fields and ensure source fields exist
            row = dict(best_by_gene[key])
            row["gene"] = name
            try:
                # Report what the run recorded. Assigned unconditionally: the
                # old `if not row.get(...)` guards existed so the DB lane's
                # value survived, but both lanes now produce the same letters
                # from the same resolver, so re-resolving is idempotent -- and
                # the guard would have re-admitted stale values (28 + 216).
                provenance = resolve_called_by(row)
                row["called_by"] = provenance.letter
                row["called_by_label"] = provenance.label
                guideline_letter = resolve_guideline_source(row)
                if guideline_letter:
                    row["guideline_source"] = guideline_letter
                else:
                    row.pop("guideline_source", None)
                row.pop("report_data_from", None)

                # Assign wild type phenotype labels based on file type
                wild_type_label = wild_type_phenotype(
                    row.get("diplotype"), row.get("phenotype"), file_type
                )
                if wild_type_label:
                    row["phenotype"] = wild_type_label
                    logger.debug(
                        f"Assigned '{wild_type_label}' to {row.get('gene')} "
                        f"(diplotype: {row.get('diplotype')}, file_type: {file_type})"
                    )
            except Exception as e:
                logger.debug("Swallowed exception: %s", e, exc_info=True)
            canonical_rows.append(row)
        else:
            # Placeholder row for a gene with no result. The run recorded no
            # call, so it has no caller and no guideline attribution either --
            # a guessed letter here was pure fabrication (28 + 216).
            canonical_rows.append(
                {
                    "gene": name,
                    "diplotype": "",
                    "phenotype": "",
                    "activity_score": None,
                    "called_by": CALLED_BY_NO_CALL,
                    "called_by_label": "No call made for this gene",
                }
            )

    return canonical_rows


def map_recommendations_for_template(
    drug_recommendations: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Map drug recommendations from PharmCAT format to template-compatible format.
    Groups recommendations by drug name to avoid duplicates in the final output.

    Args:
        drug_recommendations: List of drug recommendations from normalized PharmCAT results
        Can be either grouped structure: [{"drug": "warfarin", "genes": ["CYP2C9"], "recommendations": [...]}]
        Or flattened structure: [{"drug": "warfarin", "gene": "CYP2C9", "recommendation": "..."}]

    Returns:
        Template-compatible recommendations, grouped by drug to avoid duplicates
    """
    # First, group all recommendations by drug name to handle duplicates
    drug_groups = {}

    for drug_data in drug_recommendations:
        drug_name = drug_data.get("drug", "Unknown")
        genes = drug_data.get("genes", [])
        recommendations = drug_data.get("recommendations", [])

        # If this is the new grouped structure, process it
        if isinstance(genes, list) and isinstance(recommendations, list):
            if drug_name not in drug_groups:
                drug_groups[drug_name] = {
                    "drug": drug_name,
                    "genes": [],
                    "recommendations": [],
                    "pharmgkb_id": drug_data.get("pharmgkb_id"),
                    "called_by": drug_data.get("called_by"),
                }

            # Add genes and recommendations
            for gene in genes:
                if gene not in drug_groups[drug_name]["genes"]:
                    drug_groups[drug_name]["genes"].append(gene)

            drug_groups[drug_name]["recommendations"].extend(recommendations)
        else:
            # Legacy flattened format - group by drug name
            gene = drug_data.get("gene", "Unknown")

            if drug_name not in drug_groups:
                drug_groups[drug_name] = {
                    "drug": drug_name,
                    "genes": [],
                    "recommendations": [],
                    "pharmgkb_id": drug_data.get("pharmgkb_id"),
                    "called_by": drug_data.get("called_by"),
                }

            # Add gene if not already present
            if gene not in drug_groups[drug_name]["genes"]:
                drug_groups[drug_name]["genes"].append(gene)

            # Create recommendation entry
            recommendation_entry = {
                "gene": gene,
                "recommendation": drug_data.get(
                    "recommendation", "See report for details"
                ),
                "classification": drug_data.get("classification", "Unknown"),
                "guideline": drug_data.get("guideline", ""),
                "guideline_source": drug_data.get("guideline_source", ""),
                "literature_references": drug_data.get("literature_references", []),
            }

            drug_groups[drug_name]["recommendations"].append(recommendation_entry)

    # Convert grouped data to flattened format for template compatibility
    mapped_recommendations = []
    for drug_name, drug_data in drug_groups.items():
        # Create one entry per gene-guideline combination, but keep them grouped by drug
        for rec in drug_data["recommendations"]:
            gene = rec.get("gene", "Unknown")
            guideline = rec.get("guideline", "")
            guideline_source = rec.get("guideline_source", "")
            recommendation_text = rec.get("recommendation", "See report for details")
            classification = rec.get("classification", "Unknown")

            tier = classify_evidence(classification)

            mapped_rec = {
                "drug": drug_name,
                "gene": gene,
                "genes": gene,  # For backward compatibility
                "recommendation": recommendation_text,
                "classification": classification,
                "evidence_rank": tier.rank,
                "evidence_class": tier.css_class,
                "guideline": guideline,
                "guideline_source": guideline_source,
                "literature_references": rec.get("literature_references", []),
            }

            # Add drug-level metadata if available
            if drug_data.get("pharmgkb_id"):
                mapped_rec["pharmgkb_id"] = drug_data["pharmgkb_id"]
            if drug_data.get("called_by"):
                mapped_rec["called_by"] = drug_data["called_by"]

            mapped_recommendations.append(mapped_rec)

    return mapped_recommendations


def generate_report(
    pharmcat_results: Dict[str, Any],
    output_dir: str,
    patient_info: Dict[str, Any] = None,
    job_id: str = None,
    db_session=None,
) -> Dict[str, str]:
    """
    Generate a report from PharmCAT results

    Args:
        pharmcat_results: Results from PharmCAT (already normalized from main.py)
        output_dir: Directory to write report files to
        patient_info: Optional patient information
        job_id: Optional job ID (also used as display report_id)
        db_session: Optional database session for database queries

    Returns:
        Dict containing file paths for all enabled reports
    """
    logger.info("Generating report from PharmCAT results")
    try:
        logger.info(f"Input pharmcat_results keys: {list(pharmcat_results.keys())}")
    except Exception:
        logger.info("Input pharmcat_results has no keys()")
    logger.info(f"Input patient_info: {patient_info}")
    logger.info(f"Job ID: {job_id}")

    # Try to get PharmCAT data from database first if job_id and db_session are provided
    data = None
    workflow_warnings = []  # Initialize warnings list
    pharmcat_assume_ref_methodology = None
    if job_id and db_session:
        try:
            logger.info(
                f"Attempting to get PharmCAT data from database for job {job_id}"
            )
            logger.info(f"Database session type: {type(db_session)}")
            pharmcat_service = PharmCATDataService(db_session)
            data = pharmcat_service.get_pharmcat_data_for_workflow(job_id)
            if data:
                logger.info(
                    f"Successfully retrieved PharmCAT data from database: {len(data.get('genes', []))} genes, {len(data.get('drugRecommendations', []))} recommendations"
                )
                logger.info(f"Database data keys: {list(data.keys())}")

            # Prefer Job.job_metadata (upload writes assume-ref flags here)
            try:
                from app.api.db import Job
                from app.utils.pharmcat_assume_ref import (
                    methodology_assume_ref_paragraph,
                )

                # No populate_existing() here, unlike the six JobService reads.
                # SessionLocal sets expire_on_commit=False, so a plain query hands
                # back an identity-mapped instance -- but only if this session
                # already loaded the row. It has not: the sole caller that supplies
                # db_session opens a dedicated `SessionLocal()` in a try/finally on
                # the lines around the call (upload_router.py), deliberately *not*
                # reusing the long-lived session it already holds, i.e. a brand-new
                # session with an empty identity map; and app/main.py's reprocessing
                # path passes no session at all so this branch never runs there.
                # Every key read below is committed at upload time, long before
                # report generation starts, so there is no window to be stale in.
                #
                # The safety is the caller's freshness, not the statement's, and it
                # is invisible from here -- tests/test_generator_job_metadata_read.py
                # pins both shapes. Hand this function a session that already loaded
                # the Job (a poll loop's, a reused request-scoped one) and the GRCh37
                # provisional alert vanishes from the report with no error anywhere;
                # populate_existing() becomes required that day.
                job_uuid = uuid.UUID(str(job_id))
                job_row = db_session.query(Job).filter(Job.id == job_uuid).first()
                meta = (job_row.job_metadata or {}) if job_row is not None else {}
                workflow_config = (
                    meta.get("workflow", {})
                    if isinstance(meta.get("workflow"), dict)
                    else {}
                )
                workflow_warnings = workflow_config.get("warnings", []) or []
                logger.info(
                    f"Retrieved {len(workflow_warnings)} workflow warnings from job metadata"
                )
                pharmcat_assume_ref_methodology = methodology_assume_ref_paragraph(
                    bool(meta.get("pharmcat_absent_to_ref")),
                    bool(meta.get("pharmcat_unspecified_to_ref")),
                )
            except Exception as e:
                logger.warning(f"Failed to retrieve job report metadata: {e}")
                workflow_warnings = []
                pharmcat_assume_ref_methodology = None

            if not data:
                logger.warning(
                    "No PharmCAT data found in database, falling back to file-based data"
                )
        except Exception as e:
            logger.error(
                f"Failed to get PharmCAT data from database: {e}", exc_info=True
            )
            logger.warning("Falling back to file-based data")

    # Fallback to file-based data if database data is not available
    if not data:
        # Check if the data is already in the expected format (normalized)
        if "data" in pharmcat_results and isinstance(pharmcat_results["data"], dict):
            raw_data = pharmcat_results["data"]

            # Check if this is raw PharmCAT JSON (has 'drugs' key) or normalized data (has 'drugRecommendations' key)
            if "drugRecommendations" in raw_data:
                # This is already normalized data
                data = raw_data
                logger.info("Using pre-normalized data from main.py")
                logger.info(f"Data keys: {list(data.keys())}")
            elif "drugs" in raw_data:
                # This is raw PharmCAT JSON, need to normalize it
                logger.info(
                    "Detected raw PharmCAT JSON, normalizing with new grouped architecture"
                )
                try:
                    normalized_results = normalize_pharmcat_results(raw_data)
                    data = normalized_results["data"]
                    logger.info(
                        f"Successfully normalized raw PharmCAT JSON: {len(data.get('drugRecommendations', []))} drug recommendations"
                    )
                except Exception as e:
                    logger.error(f"Failed to normalize raw PharmCAT JSON: {str(e)}")
                    # Create minimal data structure to prevent crashes
                    data = {
                        "genes": [],
                        "drugRecommendations": [],
                        "file_type": "unknown",
                    }
            else:
                # Unknown data structure
                logger.warning(
                    f"Unknown data structure in 'data' key: {list(raw_data.keys())}"
                )
                data = raw_data
        else:
            # Fallback: try to normalize if the data structure is unexpected
            logger.warning("Unexpected data structure, attempting normalization")
            logger.warning(
                f"Expected 'data' key not found in: {list(pharmcat_results.keys())}"
            )
            try:
                normalized_results = normalize_pharmcat_results(pharmcat_results)
                data = normalized_results["data"]
                logger.info("Successfully normalized data")
            except Exception as e:
                logger.error(f"Failed to normalize data: {str(e)}")
                # Create minimal data structure to prevent crashes
                data = {"genes": [], "drugRecommendations": [], "file_type": "unknown"}

    # Map recommendations to template-compatible format
    raw_recs = data.get("drugRecommendations", [])
    logger.info(f"DEBUG - Raw drugRecommendations count: {len(raw_recs)}")
    if raw_recs:
        logger.info(f"DEBUG - First raw recommendation sample: {raw_recs[0]}")

    template_recommendations = map_recommendations_for_template(raw_recs)
    logger.info(f"Mapped {len(template_recommendations)} recommendations for template")
    if template_recommendations:
        logger.info(
            f"DEBUG - First mapped recommendation sample: {template_recommendations[0]}"
        )

    # Add tool source information to diplotypes
    file_type = str(data.get("file_type", "vcf")).lower()
    workflow_config = data.get("workflow", {})

    # Process diplotypes to add source information
    enhanced_diplotypes = []
    for diplotype in data.get("genes", []):
        if isinstance(diplotype, dict):
            gene_name = diplotype.get("gene", "")
            if gene_name:
                # Report what the run recorded (BACKLOG 28 + 216).
                provenance = resolve_called_by(diplotype)
                diplotype["called_by"] = provenance.letter
                diplotype["called_by_label"] = provenance.label
                guideline_letter = resolve_guideline_source(diplotype)
                if guideline_letter:
                    diplotype["guideline_source"] = guideline_letter
                else:
                    diplotype.pop("guideline_source", None)
                diplotype.pop("report_data_from", None)
                enhanced_diplotypes.append(diplotype)
            else:
                enhanced_diplotypes.append(diplotype)
        else:
            enhanced_diplotypes.append(diplotype)

    # Update the data with enhanced diplotypes
    data["genes"] = enhanced_diplotypes
    logger.info(
        f"Enhanced {len(enhanced_diplotypes)} diplotypes with tool source information"
    )

    # Optionally prepare Executive Summary rows from TSV
    execsum_rows_from_tsv = []
    try:
        if EXECSUM_USE_TSV:
            # Determine report directory for patient
            pid_for_dir = (
                patient_info.get("id", "unknown") if patient_info else "unknown"
            )
            report_dir_probe = os.path.join(output_dir, pid_for_dir)
            # Candidate TSV names:
            # 1) Standardized copy: {patient_id}_pgx_pharmcat.tsv
            # 2) Original PharmCAT: {patient_id}.report.tsv
            # 3) Any *_pgx_pharmcat.tsv
            # Also probe the base output_dir (some workflows place TSVs at the first level)
            tsv_candidates = []
            tsv_candidates.append(
                os.path.join(report_dir_probe, f"{pid_for_dir}_pgx_pharmcat.tsv")
            )
            tsv_candidates.append(
                os.path.join(report_dir_probe, f"{pid_for_dir}.report.tsv")
            )
            try:
                tsv_candidates.extend(
                    glob.glob(os.path.join(report_dir_probe, "*_pgx_pharmcat.tsv"))
                )
            except Exception as e:
                logger.debug("Swallowed exception: %s", e, exc_info=True)
            try:
                any_report_tsv = glob.glob(
                    os.path.join(report_dir_probe, "*.pharmcat.tsv")
                )
                # Prefer newest pharmcat.tsv if multiple
                if any_report_tsv:
                    any_report_tsv.sort(key=lambda p: os.path.getmtime(p), reverse=True)
                    tsv_candidates.extend(any_report_tsv)
            except Exception as e:
                logger.debug("Swallowed exception: %s", e, exc_info=True)
            # Base directory candidates (where reports often land before subdir creation)
            try:
                tsv_candidates.append(
                    os.path.join(output_dir, f"{pid_for_dir}_pgx_pharmcat.tsv")
                )
                tsv_candidates.append(
                    os.path.join(output_dir, f"{pid_for_dir}.report.tsv")
                )
                tsv_candidates.extend(
                    glob.glob(os.path.join(output_dir, "*_pgx_pharmcat.tsv"))
                )
                any_report_tsv_base = glob.glob(
                    os.path.join(output_dir, "*.pharmcat.tsv")
                )
                if any_report_tsv_base:
                    any_report_tsv_base.sort(
                        key=lambda p: os.path.getmtime(p), reverse=True
                    )
                    tsv_candidates.extend(any_report_tsv_base)
            except Exception as e:
                logger.debug("Swallowed exception: %s", e, exc_info=True)
            # Pick first existing in order
            tsv_path = next((p for p in tsv_candidates if os.path.exists(p)), None)
            if tsv_path:
                diplos, _recs = parse_pharmcat_tsv(tsv_path)
                try:
                    logger.info(f"Executive Summary TSV selected: {tsv_path}")
                except Exception as e:
                    logger.debug("Swallowed exception: %s", e, exc_info=True)
                for row in diplos:
                    # Get phenotype, applying wild type logic if needed
                    # Prefer source; fallback to recommendation lookup
                    rec_lookup_dip = (row.get("rec_lookup_diplotype") or "").strip()
                    source_dip = (row.get("diplotype") or "").strip()
                    diplotype_str = prefer_source_over_lookup(
                        source_dip, rec_lookup_dip
                    )
                    phenotype_str = prefer_source_over_lookup(
                        str(row.get("phenotype") or ""),
                        str(row.get("rec_lookup_phenotype") or ""),
                    )
                    # Assign wild type phenotype if conditions are met
                    # (file_type already determined above)
                    wild_type_label = wild_type_phenotype(
                        diplotype_str, phenotype_str, file_type
                    )
                    if wild_type_label:
                        phenotype_str = wild_type_label
                        logger.debug(
                            f"TSV HTML: Assigned '{wild_type_label}' to {row.get('gene')}"
                        )

                    execsum_rows_from_tsv.append(
                        {
                            "gene": row.get("gene", ""),
                            "rec_lookup_diplotype": diplotype_str,  # Use fallback value
                            "rec_lookup_phenotype": phenotype_str,
                            "rec_lookup_activity_score": (
                                row.get("activity_score")
                                if row.get("activity_score") not in (None, "")
                                else row.get("rec_lookup_activity_score")
                            ),
                        }
                    )
    except Exception as _e_exec:
        logger.warning(f"Executive Summary TSV parse skipped: {_e_exec}")

    # Prepare the template data
    # Build platform info once and include in both outputs
    platform = build_platform_info()
    logger.info(f"Built platform info with {len(platform)} items")

    # 159: run-derived provenance. `output_dir` IS the report directory -- both
    # call sites pass an already-nested /data/reports/{patient_id}/{job_id} path
    # (upload_router.py:589, main.py:1579) and `report_dir = output_dir` below.
    # Joining a patient id onto it adds a level that does not exist.
    # `_report_id_for_meta` mirrors the `report_id` computation below verbatim.
    _report_id_for_meta = (
        str(job_id)
        if job_id
        else (patient_info.get("id", "unknown") if patient_info else "unknown")
    )
    matcher_meta = probe_matcher_metadata(output_dir, _report_id_for_meta)

    template_data = {
        "patient": patient_info or {},
        "patient_id": patient_info.get("id", "unknown") if patient_info else "unknown",
        "report_id": (
            str(job_id)
            if job_id
            else (patient_info.get("id", "unknown") if patient_info else "unknown")
        ),
        "report_date": datetime.now().strftime("%Y-%m-%d"),
        "genes": data.get("genes", []),
        "diplotypes": data.get("genes", []),  # For compatibility with template
        "recommendations": template_recommendations,  # Use mapped recommendations
        "drug_recommendations": data.get(
            "drugRecommendations", []
        ),  # Keep original for reference
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
            "file_type": data.get("file_type", "vcf"),
        },
        "workflow": {
            "file_type": data.get("file_type", "vcf"),
            "used_gatk": data.get("used_gatk", False),
            "used_pypgx": data.get("used_pypgx", False),
            "used_pharmcat": True,
        },
        # Inject optional TSV-driven Executive Summary rows for template use
        "execsum_from_tsv": (
            execsum_rows_from_tsv
            if (EXECSUM_USE_TSV and execsum_rows_from_tsv)
            else None
        ),
        # Add workflow warnings/alerts for report display
        "workflow_warnings": workflow_warnings,
        "pharmcat_assume_ref_methodology": pharmcat_assume_ref_methodology,
        # 159: run-derived provenance (each rendered only if resolved)
        "genome_build": matcher_meta["genome_build"],
        "named_allele_matcher_version": matcher_meta["named_allele_matcher_version"],
        "pharmcat_data_version": matcher_meta["data_version"],
    }
    # Inject unified display sample id sourced from workflow metadata or persisted field
    try:
        workflow_meta = (
            data.get("workflow") if isinstance(data.get("workflow"), dict) else {}
        )
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
    except Exception as e:
        logger.debug("Swallowed exception: %s", e, exc_info=True)

    logger.info(f"Template data prepared with {len(template_data)} fields")
    logger.info(f"Template data keys: {list(template_data.keys())}")
    logger.info(f"Genes count: {len(template_data['genes'])}")
    logger.info(f"Recommendations count: {len(template_data['recommendations'])}")

    # Get patient and report IDs
    patient_id = patient_info.get("id", "unknown") if patient_info else "unknown"
    data_id = (
        str(patient_info.get("data_id"))
        if patient_info and patient_info.get("data_id")
        else None
    )
    # Display report_id = job_id (137c)
    report_id = str(job_id) if job_id else patient_id

    # Use output_dir directly as the report directory
    # The upload_router.py already passes the nested /data/reports/{patient_id}/{job_id} path
    report_dir = output_dir
    os.makedirs(report_dir, exist_ok=True)
    logger.info(f"Using report directory: {report_dir}")

    # Server URL prefix under nested patient/job layout
    reports_url_prefix = f"/reports/{patient_id}/{report_id}"

    # Attempt to load genomic header text saved earlier in the pipeline
    header_text: str = ""
    try:
        # We expect a file named {data_id}.header.txt written in the nested report directory
        data_id_for_header = data_id or patient_id
        header_txt_candidates = []
        # Primary expected file
        header_txt_candidates.append(
            os.path.join(report_dir, f"{data_id_for_header}.header.txt")
        )
        # Also consider any *.header.txt in the directory if the exact data_id is not known
        try:
            for p in glob.glob(os.path.join(report_dir, "*.header.txt")):
                if p not in header_txt_candidates:
                    header_txt_candidates.append(p)
        except Exception as e:
            logger.debug("Swallowed exception: %s", e, exc_info=True)
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
            logger.info(
                f"Loaded genomic header text from {selected} ({len(header_text)} chars)"
            )
        else:
            logger.info("No genomic header text file found for this report")
    except Exception as _e:
        logger.warning(f"Failed to load genomic header text: {_e}")

    # Now that we know the report directory, try to load PyPGx results and augment data['genes']
    try:
        # Prefer PyPGx results coming directly from workflow (if upstream provided it)
        pypgx_results = None
        if isinstance(data, dict) and "pypgx_results" in data:
            pypgx_results = data.get("pypgx_results")
        # If not embedded, look for *_pypgx_results.json in the report directory; pick the newest by mtime
        if not pypgx_results:
            pypgx_json_candidates = glob.glob(
                os.path.join(report_dir, "*_pypgx_results.json")
            )
            if pypgx_json_candidates:
                latest_path = max(
                    pypgx_json_candidates, key=lambda p: os.path.getmtime(p)
                )
                logger.info(f"Found PyPGx results JSON for enrichment: {latest_path}")
                with open(latest_path, "r", encoding="utf-8") as f:
                    pypgx_results = json.load(f)
        if pypgx_results:
            logger.info("Proceeding to merge PyPGx results into report data...")
            results_obj = (
                pypgx_results.get("results", {})
                if isinstance(pypgx_results, dict)
                else {}
            )
            existing_genes = {
                (g.get("gene") or g.get("name") or "").strip().upper()
                for g in data.get("genes", [])
                if isinstance(g, dict)
            }
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
                    # `a or b` would swallow a PyPGx activity score of 0 -- falsy,
                    # so it falls through to the alternate key and ends up None,
                    # blanking the cell before any template sees it. 0 is the Poor
                    # Metabolizer end of the scale; pick by presence, not truth.
                    activity_score = details.get("activity_score")
                    if activity_score is None:
                        activity_score = details.get("activityScore")
                    gene_entry = {
                        "gene": gene_name,
                        # Align with normalized PharmCAT structure minimally
                        "diplotype": diplotype if diplotype else "Unknown",
                        "phenotype": phenotype if phenotype else "Unknown",
                        "activity_score": activity_score,
                        "tool_source": "PyPGx",
                        "pyPgxOnly": True,
                    }
                    # activity_score is already included in the gene_entry above
                    data.setdefault("genes", []).append(gene_entry)
                    existing_genes.add(gene_name.upper())
                    added_count += 1
                except Exception as ie:
                    logger.warning(f"Failed to map PyPGx gene {gene_key}: {ie}")
            if added_count:
                data["used_pypgx"] = True
                logger.info(
                    f"Augmented report with {added_count} PyPGx-only gene entries (total genes now={len(data.get('genes', []))})"
                )
        else:
            logger.info("No PyPGx results available for enrichment (embedded or file)")

        # Deep enrichment from per-gene pipeline artifacts if available
        try:
            # Locate any per-gene pipelines under a pypgx_* directory in this report_dir
            pipelines_root_candidates = glob.glob(os.path.join(report_dir, "pypgx_*"))
            if pipelines_root_candidates:
                pipelines_root = pipelines_root_candidates[0]
                pipeline_dirs = [
                    os.path.join(pipelines_root, d)
                    for d in os.listdir(pipelines_root)
                    if d.endswith("-pipeline")
                    and os.path.isdir(os.path.join(pipelines_root, d))
                ]
                if pipeline_dirs:
                    logger.info(
                        f"Found {len(pipeline_dirs)} PyPGx per-gene pipelines for enrichment"
                    )
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
                            # Both halves were truthiness tests, and both were wrong
                            # for a score of 0: a parsed 0 never filled an empty
                            # target, and a stored 0 counted as missing and got
                            # overwritten. Presence is numeric here as everywhere
                            # else -- which also stops "N/A"/"Unknown" from being
                            # treated as a score worth keeping or copying.
                            if activity_score_num(
                                target.get("activity_score")
                            ) is None and (
                                activity_score_num(parsed.get("activity_score"))
                                is not None
                            ):
                                target["activity_score"] = parsed["activity_score"]
                            if parsed.get("call_confidence") and not target.get(
                                "call_confidence"
                            ):
                                target["call_confidence"] = parsed["call_confidence"]
                            # Evidence
                            if parsed.get("evidence"):
                                target.setdefault("evidence", {})
                                if parsed["evidence"].get("alleles"):
                                    target["evidence"]["alleles"] = parsed["evidence"][
                                        "alleles"
                                    ]
                                if parsed["evidence"].get("variants"):
                                    target["evidence"]["variants"] = parsed["evidence"][
                                        "variants"
                                    ]
                            if parsed.get("phased") is True:
                                target["phased"] = True
                            if parsed.get("copy_number") and not target.get(
                                "copy_number"
                            ):
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
                            "pyPgxOnly": True,
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
                    logger.info(
                        f"Enriched PyPGx details for {enriched_count} genes from per-gene pipelines"
                    )
        except Exception as enrich_e:
            logger.warning(
                f"PyPGx per-gene enrichment skipped due to error: {enrich_e}"
            )
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
    inferred_gatk = (
        used_gatk_flag
        or file_type in {"bam", "cram", "sam"}
        and bool(data.get("gatk_output_path"))
    )
    inferred_pypgx = used_pypgx_flag and any(
        g
        for g in data.get("genes", [])
        if isinstance(g, dict) and g.get("gene", "").upper() == "CYP2D6"
    )

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
    logger.info(
        f"Drug recommendations count: {len(data.get('drugRecommendations', []))}"
    )

    # Optionally write workflow images alongside the report outputs
    # Use patient_id for filenames to match the directory structure
    workflow_svg_filename = f"{report_id}_workflow.svg"
    workflow_png_filename = f"{report_id}_workflow.png"

    logger.info(f"=== WORKFLOW DIAGRAM GENERATION START ===")
    logger.info(f"Workflow configuration: {per_sample_workflow}")

    try:
        if REPORT_CONFIG.get("write_workflow_svg", True):
            logger.info(
                "Generating Graphviz SVG workflow diagram (true Graphviz renderer)..."
            )
            svg_bytes = render_with_graphviz(per_sample_workflow, fmt="svg")
            if svg_bytes:
                svg_path = os.path.join(report_dir, workflow_svg_filename)
                with open(svg_path, "wb") as f_out:
                    f_out.write(svg_bytes)
                logger.info(
                    f"✓ Graphviz Workflow SVG generated successfully: {svg_path} ({len(svg_bytes)} bytes)"
                )
            else:
                logger.warning(
                    "⚠ Graphviz Workflow SVG generation returned empty result"
                )
    except Exception as e:
        logger.error(
            f"✗ Graphviz Workflow SVG generation failed: {str(e)}", exc_info=True
        )

    # Generate Kroki Mermaid SVG for comparison
    try:
        if REPORT_CONFIG.get("write_workflow_svg", True):
            logger.info(
                "Generating Kroki Mermaid SVG workflow diagram for comparison..."
            )
            kroki_svg_bytes = render_kroki_mermaid_svg(workflow=per_sample_workflow)
            if kroki_svg_bytes:
                kroki_svg_filename = f"{report_id}_workflow_kroki_mermaid.svg"
                kroki_svg_path = os.path.join(report_dir, kroki_svg_filename)
                with open(kroki_svg_path, "wb") as f_out:
                    f_out.write(kroki_svg_bytes)
                logger.info(
                    f"✓ Kroki Mermaid Workflow SVG generated successfully: {kroki_svg_path} ({len(kroki_svg_bytes)} bytes)"
                )
            else:
                logger.warning(
                    "⚠ Kroki Mermaid Workflow SVG generation returned empty result"
                )
    except Exception as e:
        logger.error(
            f"✗ Kroki Mermaid Workflow SVG generation failed: {str(e)}", exc_info=True
        )

    try:
        # Always generate PNG for reliable PDF embedding (default True)
        if REPORT_CONFIG.get("write_workflow_png", True):
            logger.info("Generating PNG workflow diagram...")
            png_bytes = None

            # Try Kroki Mermaid first (best quality, same as HTML report)
            try:
                from app.visualizations.workflow_diagram import (
                    read_workflow_mermaid,
                    render_with_kroki,
                )

                logger.info("Attempting PNG generation via Kroki Mermaid...")
                mermaid_src = read_workflow_mermaid()
                png_bytes = render_with_kroki(mermaid_src, fmt="png")
                if png_bytes:
                    logger.info(
                        f"✓ PNG generated from Kroki Mermaid: {len(png_bytes)} bytes"
                    )
            except Exception as e:
                logger.warning(f"Kroki Mermaid PNG generation failed: {e}")

            # Fallback to Graphviz PNG if Kroki failed
            if not png_bytes:
                try:
                    logger.info("Attempting PNG generation via Graphviz...")
                    png_bytes = render_workflow(fmt="png", workflow=per_sample_workflow)
                    if png_bytes:
                        logger.info(
                            f"✓ PNG generated from Graphviz: {len(png_bytes)} bytes"
                        )
                except Exception as e:
                    logger.warning(f"Graphviz PNG generation failed: {e}")

            # Final fallback to pure-Python text-based PNG
            if not png_bytes:
                logger.info("Both renderers failed, using pure-Python fallback...")
                png_bytes = render_simple_png_from_workflow(per_sample_workflow)
                if png_bytes:
                    logger.info(
                        f"✓ PNG generated from Python fallback: {len(png_bytes)} bytes"
                    )

            if png_bytes:
                png_path = os.path.join(report_dir, workflow_png_filename)
                with open(png_path, "wb") as f_out:
                    f_out.write(png_bytes)
                logger.info(
                    f"✓ Workflow PNG saved successfully: {png_path} ({len(png_bytes)} bytes)"
                )
            else:
                logger.warning("⚠ All PNG generation methods failed")
        else:
            logger.info("PNG workflow generation disabled in config")
    except Exception as e:
        logger.error(f"✗ Workflow PNG generation failed: {str(e)}", exc_info=True)

    logger.info(f"=== WORKFLOW DIAGRAM GENERATION END ===")

    # Create a unique filename based on patient_id
    base_filename = f"{report_id}_pgx_report"

    try:
        # Generate both standard HTML report and interactive HTML report
        html_path = os.path.join(report_dir, f"{base_filename}.html")
        interactive_html_path = os.path.join(
            report_dir, f"{base_filename}_interactive.html"
        )

        # Initialize the report paths dictionary that will be returned
        report_paths = {}

        # Generate standard HTML report if enabled
        if REPORT_CONFIG["write_html"]:
            logger.info("=== HTML REPORT GENERATION START ===")
            logger.info("Loading HTML template...")
            # The module-level `env` -- same loader, same filter, same autoescape.
            # This used to build a third Environment inline, which is how the three
            # renderers of these two templates drifted apart on autoescape in the
            # first place.
            template = env.get_template("report_template.html")
            logger.info("HTML template loaded successfully")

            # Prepare workflow visuals for HTML, preferring pre-rendered files
            workflow_svg_in_html = ""
            workflow_kroki_svg_in_html = ""
            workflow_png_data_uri_html = ""
            workflow_html_fallback_html = ""

            # Prefer previously written assets
            svg_path = os.path.join(report_dir, workflow_svg_filename)
            kroki_svg_filename = f"{report_id}_workflow_kroki_mermaid.svg"
            kroki_svg_path = os.path.join(report_dir, kroki_svg_filename)
            png_path = os.path.join(report_dir, workflow_png_filename)

            logger.info(f"Checking for pre-generated workflow files...")
            logger.info(
                f"Graphviz SVG path: {svg_path} (exists: {os.path.exists(svg_path)})"
            )
            logger.info(
                f"Kroki Mermaid SVG path: {kroki_svg_path} (exists: {os.path.exists(kroki_svg_path)})"
            )
            logger.info(f"PNG path: {png_path} (exists: {os.path.exists(png_path)})")

            if os.path.exists(svg_path):
                try:
                    with open(svg_path, "r", encoding="utf-8") as f_svg:
                        workflow_svg_in_html = f_svg.read()
                    logger.info(
                        f"✓ Loaded pre-generated Graphviz SVG workflow: {len(workflow_svg_in_html)} chars"
                    )
                except Exception as e:
                    logger.error(
                        f"✗ Failed to read pre-generated Graphviz SVG: {str(e)}"
                    )
                    workflow_svg_in_html = ""

            # Load Kroki Mermaid SVG for comparison
            if os.path.exists(kroki_svg_path):
                try:
                    with open(kroki_svg_path, "r", encoding="utf-8") as f_kroki_svg:
                        workflow_kroki_svg_in_html = f_kroki_svg.read()
                    logger.info(
                        f"✓ Loaded pre-generated Kroki Mermaid SVG workflow: {len(workflow_kroki_svg_in_html)} chars"
                    )
                except Exception as e:
                    logger.error(
                        f"✗ Failed to read pre-generated Kroki Mermaid SVG: {str(e)}"
                    )
                    workflow_kroki_svg_in_html = ""

            if not workflow_svg_in_html and os.path.exists(png_path):
                try:
                    with open(png_path, "rb") as f_png:
                        b64 = base64.b64encode(f_png.read()).decode("ascii")
                        workflow_png_data_uri_html = f"data:image/png;base64,{b64}"
                    logger.info(
                        f"✓ Loaded pre-generated PNG workflow: {len(workflow_png_data_uri_html)} chars"
                    )
                except Exception as e:
                    logger.error(f"✗ Failed to read pre-generated PNG: {str(e)}")
                    workflow_png_data_uri_html = ""

            # If no local assets available, try dynamic renderers and finally HTML fallback
            if not workflow_svg_in_html and not workflow_png_data_uri_html:
                logger.info(
                    "No pre-generated workflow files found, trying dynamic generation..."
                )
                try:
                    logger.info("Attempting SVG generation...")
                    svg_bytes = render_workflow(fmt="svg", workflow=per_sample_workflow)
                    svg_text = svg_bytes.decode("utf-8", errors="ignore")
                    workflow_svg_in_html = _sanitize_graphviz_svg(svg_text)
                    logger.info(
                        f"✓ Generated SVG workflow dynamically: {len(workflow_svg_in_html)} chars"
                    )
                except Exception as e:
                    logger.error(f"✗ Dynamic SVG generation failed: {str(e)}")
                    workflow_svg_in_html = ""

                if not workflow_svg_in_html:
                    try:
                        logger.info("Attempting PNG data URI generation...")
                        workflow_png_data_uri_html = render_workflow_png_data_uri(
                            workflow=per_sample_workflow
                        )
                        logger.info(
                            f"✓ Generated PNG data URI: {len(workflow_png_data_uri_html)} chars"
                        )
                    except Exception as e:
                        logger.error(f"✗ PNG data URI generation failed: {str(e)}")
                        workflow_png_data_uri_html = ""

                if not workflow_svg_in_html and not workflow_png_data_uri_html:
                    try:
                        logger.info("Attempting HTML fallback generation...")
                        workflow_html_fallback_html = build_simple_html_from_workflow(
                            per_sample_workflow
                        )
                        logger.info(
                            f"✓ Generated HTML fallback: {len(workflow_html_fallback_html)} chars"
                        )
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
                "report_date": datetime.now(timezone.utc).strftime(
                    "%Y-%m-%d %H:%M:%S UTC"
                ),
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
                "execsum_from_tsv": (
                    execsum_rows_from_tsv
                    if (EXECSUM_USE_TSV and execsum_rows_from_tsv)
                    else None
                ),
                # Add workflow warnings/alerts for report display
                "workflow_warnings": workflow_warnings,
                "pharmcat_assume_ref_methodology": pharmcat_assume_ref_methodology,
                # 159: run-derived provenance (each rendered only if resolved).
                # This dict rebinds `template_data` and is the one actually fed
                # to report_template.html for both the HTML render below and the
                # PDF render further down -- it must carry the keys too.
                "genome_build": matcher_meta["genome_build"],
                "named_allele_matcher_version": matcher_meta[
                    "named_allele_matcher_version"
                ],
                "pharmcat_data_version": matcher_meta["data_version"],
            }
            try:
                logger.info(
                    f"Executive Summary rows (TSV): {len(execsum_rows_from_tsv) if execsum_rows_from_tsv else 0}; Using TSV: {EXECSUM_USE_TSV}"
                )
            except Exception as e:
                logger.debug(
                    "Executive Summary row count log failed: %s", e, exc_info=True
                )
            # Inject display sample id for WeasyPrint HTML template
            try:
                display_sample = (
                    data.get("sample_identifier") or data.get("displayId") or patient_id
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
                            if (
                                name.endswith("_pgx_pharmcat.html")
                                or name.endswith("_pgx_pharmcat.json")
                                or name.endswith("_pgx_pharmcat.tsv")
                            ):
                                base = name.split("_pgx_pharmcat")[0]
                                if (
                                    base
                                    and base != patient_id
                                    and base not in candidates
                                ):
                                    candidates.append(base)
                        if candidates:
                            derived = candidates[0]
                            template_data["sample_identifier"] = derived
                            template_data["display_sample_id"] = derived
                            logger.info(
                                f"Derived display_sample_id from PharmCAT files: {derived}"
                            )
                    except Exception as e:
                        logger.debug(
                            f"Display sample derivation from files failed: {e}",
                            exc_info=True,
                        )
            except Exception as e:
                logger.debug("Display sample id injection failed: %s", e, exc_info=True)

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
                    "template_data_sample": {
                        k: str(v)[:100] + "..." if len(str(v)) > 100 else str(v)
                        for k, v in list(template_data.items())[:5]
                    },
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
                logger.info(
                    f"✓ Template rendered successfully, HTML content length: {len(html_content)}"
                )
            except Exception as e:
                logger.error(f"✗ Template rendering failed: {str(e)}", exc_info=True)
                # Try to provide more context about what might be missing
                logger.error(f"Template data keys: {list(template_data.keys())}")
                logger.error(
                    f"Workflow content lengths - Graphviz SVG: {len(workflow_svg_in_html)}, Kroki SVG: {len(workflow_kroki_svg_in_html)}, PNG: {len(workflow_png_data_uri_html)}, HTML: {len(workflow_html_fallback_html)}"
                )
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
            server_html_path = f"{reports_url_prefix}/{base_filename}.html"
            report_paths["html_path"] = server_html_path
            # Store the HTML report URL in the data structure for later use
            data["html_report_url"] = server_html_path

            # Surface workflow asset URLs if present
            svg_path = os.path.join(report_dir, workflow_svg_filename)
            if os.path.exists(svg_path):
                report_paths["workflow_svg_path"] = (
                    f"{reports_url_prefix}/{workflow_svg_filename}"
                )
                data["workflow_svg_url"] = report_paths["workflow_svg_path"]
                logger.info(
                    f"✓ Workflow Graphviz SVG URL added: {report_paths['workflow_svg_path']}"
                )

            kroki_svg_path = os.path.join(report_dir, kroki_svg_filename)
            if os.path.exists(kroki_svg_path):
                report_paths["workflow_kroki_svg_path"] = (
                    f"{reports_url_prefix}/{kroki_svg_filename}"
                )
                data["workflow_kroki_svg_url"] = report_paths["workflow_kroki_svg_path"]
                logger.info(
                    f"✓ Workflow Kroki Mermaid SVG URL added: {report_paths['workflow_kroki_svg_path']}"
                )

            png_path = os.path.join(report_dir, workflow_png_filename)
            if os.path.exists(png_path):
                report_paths["workflow_png_path"] = (
                    f"{reports_url_prefix}/{workflow_png_filename}"
                )
                data["workflow_png_url"] = report_paths["workflow_png_path"]
                logger.info(
                    f"✓ Workflow PNG URL added: {report_paths['workflow_png_path']}"
                )

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
                workflow=per_sample_workflow,
                workflow_warnings=workflow_warnings,
                pharmcat_assume_ref_methodology=pharmcat_assume_ref_methodology,
            )
            logger.info(f"Interactive HTML report generated: {interactive_html_path}")

            # Add to report paths
            server_interactive_html_path = (
                f"{reports_url_prefix}/{base_filename}_interactive.html"
            )
            report_paths["interactive_html_path"] = server_interactive_html_path
            data["interactive_html_report_url"] = server_interactive_html_path

        # Generate PDF report using configured engine
        if REPORT_CONFIG["write_pdf"]:
            pdf_path = os.path.join(report_dir, f"{base_filename}.pdf")

            logger.info(f"=== PDF GENERATION START (Engine: {PDF_ENGINE}) ===")
            logger.info(f"Workflow data: {per_sample_workflow}")

            # For PDF, prefer PNG over SVG to avoid WeasyPrint text rendering issues
            # PNG ensures the diagram is reliably visible with proper scaling
            pdf_png_data_uri = ""
            workflow_kroki_svg_for_pdf = ""
            workflow_graphviz_svg_for_pdf = ""

            try:
                png_path = os.path.join(report_dir, f"{report_id}_workflow.png")

                # First, try to load pre-rendered PNG
                if os.path.exists(png_path):
                    try:
                        with open(png_path, "rb") as f_png:
                            png_bytes = f_png.read()
                            pdf_png_data_uri = f"data:image/png;base64,{base64.b64encode(png_bytes).decode()}"
                        logger.info(
                            f"✓ Loaded pre-rendered PNG for PDF: {len(png_bytes)} bytes"
                        )
                    except Exception as e:
                        logger.warning(f"Failed reading pre-rendered PNG for PDF: {e}")

                # If no PNG exists, generate one from Kroki Mermaid (preferred) or Graphviz
                if not pdf_png_data_uri:
                    try:
                        # Try Kroki Mermaid → PNG first (best quality)
                        logger.info("Generating PNG from Kroki Mermaid for PDF...")
                        from app.visualizations.workflow_diagram import (
                            read_workflow_mermaid,
                            render_with_kroki,
                        )

                        mermaid_src = read_workflow_mermaid()
                        png_bytes = render_with_kroki(mermaid_src, fmt="png")
                        if png_bytes:
                            pdf_png_data_uri = f"data:image/png;base64,{base64.b64encode(png_bytes).decode()}"
                            # Save for future use
                            with open(png_path, "wb") as f_out:
                                f_out.write(png_bytes)
                            logger.info(
                                f"✓ Generated PNG from Kroki Mermaid for PDF: {len(png_bytes)} bytes"
                            )
                    except Exception as e:
                        logger.warning(f"Kroki Mermaid → PNG generation failed: {e}")

                    # If Kroki failed, try Graphviz → PNG
                    if not pdf_png_data_uri:
                        try:
                            logger.info("Generating PNG from Graphviz for PDF...")
                            png_bytes = render_workflow(
                                fmt="png", workflow=per_sample_workflow
                            )
                            if png_bytes:
                                pdf_png_data_uri = f"data:image/png;base64,{base64.b64encode(png_bytes).decode()}"
                                # Save for future use
                                with open(png_path, "wb") as f_out:
                                    f_out.write(png_bytes)
                                logger.info(
                                    f"✓ Generated PNG from Graphviz for PDF: {len(png_bytes)} bytes"
                                )
                        except Exception as e:
                            logger.warning(f"Graphviz → PNG generation failed: {e}")

                # If PNG generation failed completely, fall back to SVG (last resort)
                if not pdf_png_data_uri:
                    logger.warning(
                        "⚠ PNG generation failed, falling back to SVG for PDF (may have rendering issues)"
                    )
                    svg_kroki_path = os.path.join(
                        report_dir, f"{report_id}_workflow_kroki_mermaid.svg"
                    )
                    svg_graphviz_path = os.path.join(
                        report_dir, f"{patient_id}_workflow.svg"
                    )

                    # Try Kroki SVG
                    if os.path.exists(svg_kroki_path):
                        try:
                            with open(svg_kroki_path, "r", encoding="utf-8") as f_svg:
                                workflow_kroki_svg_for_pdf = f_svg.read()
                            logger.info(
                                f"✓ Loaded Kroki Mermaid SVG as fallback for PDF: {len(workflow_kroki_svg_for_pdf)} chars"
                            )
                        except Exception as e:
                            logger.warning(f"Failed reading Kroki SVG fallback: {e}")

                    # Try Graphviz SVG
                    if not workflow_kroki_svg_for_pdf and os.path.exists(
                        svg_graphviz_path
                    ):
                        try:
                            with open(
                                svg_graphviz_path, "r", encoding="utf-8"
                            ) as f_svg:
                                workflow_graphviz_svg_for_pdf = f_svg.read()
                            logger.info(
                                f"✓ Loaded Graphviz SVG as fallback for PDF: {len(workflow_graphviz_svg_for_pdf)} chars"
                            )
                        except Exception as e:
                            logger.warning(f"Failed reading Graphviz SVG fallback: {e}")

            except Exception as e:
                logger.warning(
                    f"⚠ PDF workflow rendering block failed, template will use text fallback: {e}"
                )

            # Render the HTML template with workflow diagram
            # Prefer PNG for reliable PDF rendering; SVG as fallback only
            pdf_html_content = template.render(
                **template_data,
                workflow_png_data_uri=pdf_png_data_uri,
                workflow_kroki_svg=workflow_kroki_svg_for_pdf,
                workflow_svg=workflow_graphviz_svg_for_pdf,
                workflow_png_file_url="",
                workflow_html_fallback="",
            )

            # Add the PDF HTML content to template_data for PDF generators
            template_data["template_html"] = pdf_html_content

            # Generate PDF using configured engine
            pdf_generated = False

            if PDF_ENGINE == "weasyprint":
                try:
                    generate_pdf_from_html(pdf_html_content, pdf_path)
                    logger.info(
                        f"✓ PDF report generated successfully using WeasyPrint: {pdf_path}"
                    )
                    pdf_generated = True
                except Exception as e:
                    logger.error(f"✗ WeasyPrint PDF generation failed: {str(e)}")
                    if PDF_FALLBACK:
                        logger.info("🔄 Attempting ReportLab fallback...")
                        try:
                            # Lazy import avoids circular import with pdf_generators
                            from app.reports.pdf_generators import (
                                generate_pdf_report_dual_lane,
                            )

                            result = generate_pdf_report_dual_lane(
                                template_data=template_data,
                                output_path=pdf_path,
                                workflow_diagram=per_sample_workflow,
                            )
                            if result["success"]:
                                logger.info(
                                    f"✓ PDF generated successfully using ReportLab fallback: {result['generator_used']}"
                                )
                                pdf_generated = True
                            else:
                                logger.error(
                                    f"✗ ReportLab fallback also failed: {result['error']}"
                                )
                        except Exception as fallback_error:
                            logger.error(
                                f"✗ ReportLab fallback failed: {str(fallback_error)}"
                            )

            elif PDF_ENGINE == "reportlab":
                try:
                    # Lazy import avoids circular import with pdf_generators
                    from app.reports.pdf_generators import generate_pdf_report_dual_lane

                    result = generate_pdf_report_dual_lane(
                        template_data=template_data,
                        output_path=pdf_path,
                        workflow_diagram=per_sample_workflow,
                    )
                    if result["success"]:
                        logger.info(
                            f"✓ PDF report generated successfully using ReportLab: {result['generator_used']}"
                        )
                        pdf_generated = True
                    else:
                        logger.error(
                            f"✗ ReportLab PDF generation failed: {result['error']}"
                        )
                        if PDF_FALLBACK:
                            logger.info("🔄 Attempting WeasyPrint fallback...")
                            try:
                                generate_pdf_from_html(pdf_html_content, pdf_path)
                                logger.info(
                                    f"✓ PDF generated successfully using WeasyPrint fallback"
                                )
                                pdf_generated = True
                            except Exception as fallback_error:
                                logger.error(
                                    f"✗ WeasyPrint fallback failed: {str(fallback_error)}"
                                )
                except Exception as e:
                    logger.error(f"✗ ReportLab PDF generation failed: {str(e)}")
                    if PDF_FALLBACK:
                        logger.info("🔄 Attempting WeasyPrint fallback...")
                        try:
                            generate_pdf_from_html(pdf_html_content, pdf_path)
                            logger.info(
                                f"✓ PDF generated successfully using WeasyPrint fallback"
                            )
                            pdf_generated = True
                        except Exception as fallback_error:
                            logger.error(
                                f"✗ WeasyPrint fallback failed: {str(fallback_error)}"
                            )

            # Add to report paths if PDF was generated
            if pdf_generated:
                server_pdf_path = f"{reports_url_prefix}/{base_filename}.pdf"
                report_paths["pdf_path"] = server_pdf_path
                data["pdf_report_url"] = server_pdf_path
            else:
                # Create a simple text file as fallback
                try:
                    txt_path = pdf_path.replace(".pdf", ".txt")
                    with open(txt_path, "w", encoding="utf-8") as f:
                        f.write(f"PDF GENERATION FAILED: All engines failed\n\n")
                        f.write("Report content would be here.\n")
                        f.write("Please check the HTML report instead.\n")
                    logger.info(f"Created fallback text file: {txt_path}")
                except Exception as txt_error:
                    logger.error(
                        f"Failed to create fallback text file: {str(txt_error)}"
                    )

            logger.info(f"=== PDF GENERATION END (Engine: {PDF_ENGINE}) ===")

        # Include PharmCAT original reports if enabled
        # Check if pharmacat report files already exist in the report directory
        pharmcat_html_filename = f"{report_id}_pgx_pharmcat.html"
        pharmcat_html_path = os.path.join(report_dir, pharmcat_html_filename)

        # PharmCAT HTML report
        if REPORT_CONFIG["show_pharmcat_html_report"]:
            logger.info(
                "Processing PharmCAT HTML report (enabled via INCLUDE_PHARMCAT_HTML)"
            )
            # Look for the original PharmCAT HTML report
            pharmcat_html_file = os.path.join(
                report_dir, f"{report_id}_pgx_pharmcat.html"
            )
            if os.path.exists(pharmcat_html_file):
                # Copy it with our standardized naming if it doesn't already exist
                if not os.path.exists(pharmcat_html_path):
                    shutil.copy(pharmcat_html_file, pharmcat_html_path)
                    logger.info(f"PharmCAT HTML report copied to: {pharmcat_html_path}")

            # Add to report paths if the file exists
            if os.path.exists(pharmcat_html_path):
                server_pharmcat_html_path = (
                    f"{reports_url_prefix}/{pharmcat_html_filename}"
                )
                report_paths["pharmcat_html_path"] = server_pharmcat_html_path
                data["pharmcat_html_report_url"] = server_pharmcat_html_path
                logger.info(
                    f"PharmCAT HTML report URL added: {server_pharmcat_html_path}"
                )
            else:
                logger.warning("PharmCAT HTML report not found in report directory")
        else:
            logger.debug(
                "PharmCAT HTML report processing disabled via INCLUDE_PHARMCAT_HTML environment variable"
            )

        # PharmCAT JSON report
        if REPORT_CONFIG["show_pharmcat_json_report"]:
            logger.info(
                "Processing PharmCAT JSON report (enabled via INCLUDE_PHARMCAT_JSON)"
            )
            pharmcat_json_filename = f"{report_id}_pgx_pharmcat.json"
            pharmcat_json_path = os.path.join(report_dir, pharmcat_json_filename)
            pharmcat_json_file = os.path.join(report_dir, f"{report_id}.report.json")

            if os.path.exists(pharmcat_json_file):
                if not os.path.exists(pharmcat_json_path):
                    shutil.copy(pharmcat_json_file, pharmcat_json_path)
                    logger.info(f"PharmCAT JSON report copied to: {pharmcat_json_path}")

            if os.path.exists(pharmcat_json_path):
                server_pharmcat_json_path = (
                    f"{reports_url_prefix}/{pharmcat_json_filename}"
                )
                report_paths["pharmcat_json_path"] = server_pharmcat_json_path
                data["pharmcat_json_report_url"] = server_pharmcat_json_path
                logger.info(
                    f"PharmCAT JSON report URL added: {server_pharmcat_json_path}"
                )
            else:
                logger.warning("PharmCAT JSON report not found in report directory")
        else:
            logger.debug(
                "PharmCAT JSON report processing disabled via INCLUDE_PHARMCAT_JSON environment variable"
            )

        # PharmCAT TSV report
        if REPORT_CONFIG["show_pharmcat_tsv_report"]:
            logger.info(
                "Processing PharmCAT TSV report (enabled via INCLUDE_PHARMCAT_TSV)"
            )
            pharmcat_tsv_filename = f"{report_id}_pgx_pharmcat.tsv"
            pharmcat_tsv_path = os.path.join(report_dir, pharmcat_tsv_filename)
            pharmcat_tsv_file = os.path.join(report_dir, f"{report_id}.report.tsv")

            if os.path.exists(pharmcat_tsv_file):
                if not os.path.exists(pharmcat_tsv_path):
                    shutil.copy(pharmcat_tsv_file, pharmcat_tsv_path)
                    logger.info(f"PharmCAT TSV report copied to: {pharmcat_tsv_path}")

            if os.path.exists(pharmcat_tsv_path):
                server_pharmcat_tsv_path = (
                    f"{reports_url_prefix}/{pharmcat_tsv_filename}"
                )
                report_paths["pharmcat_tsv_path"] = server_pharmcat_tsv_path
                data["pharmcat_tsv_report_url"] = server_pharmcat_tsv_path
                logger.info(
                    f"PharmCAT TSV report URL added: {server_pharmcat_tsv_path}"
                )
            else:
                logger.warning("PharmCAT TSV report not found in report directory")
        else:
            logger.debug(
                "PharmCAT TSV report processing disabled via INCLUDE_PHARMCAT_TSV environment variable"
            )

        # FHIR Export - Generate FHIR R4 compliant exports if enabled.
        # Resolved here, per report, not read out of an import-time snapshot.
        if fhir_export_enabled():
            logger.info("=== FHIR EXPORT GENERATION START ===")
            try:
                # Import lazily to avoid circular imports
                from app.services.fhir_export_service import FHIRExportService

                # We need a database session for the FHIR export service
                if db_session:
                    fhir_service = FHIRExportService(db_session)

                    # The PharmCAT run id the upload path linked to this job.
                    pharmcat_run_id = lookup_pharmcat_run_id(db_session, job_id)
                    if pharmcat_run_id:
                        logger.info(
                            f"Found PharmCAT run_id in job metadata: {pharmcat_run_id}"
                        )

                    if not pharmcat_run_id:
                        logger.info(
                            "No explicit PharmCAT run_id found; using workflow data for FHIR export"
                        )

                    # Prepare patient info for FHIR export
                    fhir_patient_info = None
                    if patient_info:
                        fhir_patient_info = {
                            "id": patient_info.get("id", patient_id),
                            "name": patient_info.get("name"),
                            "gender": patient_info.get("gender"),
                            "birthDate": patient_info.get("birthDate"),
                        }

                    # Generate both JSON and XML FHIR exports directly from normalized data
                    fhir_result = fhir_service.save_fhir_export(
                        run_id=pharmcat_run_id,
                        patient_id=patient_id,
                        patient_info=fhir_patient_info,
                        output_format="both",  # Generate both JSON and XML
                        include_recommendations=True,
                        pharmcat_data=data,
                        workflow_id=job_id,
                    )

                    if fhir_result.get("success"):
                        files_saved = fhir_result.get("files_saved", [])
                        for file_info in files_saved:
                            fmt = file_info.get("format", "")
                            url = file_info.get("url", "")
                            if fmt == "json":
                                report_paths["fhir_json_path"] = url
                                data["fhir_json_url"] = url
                                logger.info(f"✓ FHIR JSON export generated: {url}")
                            elif fmt == "xml":
                                report_paths["fhir_xml_path"] = url
                                data["fhir_xml_url"] = url
                                logger.info(f"✓ FHIR XML export generated: {url}")

                        # Mark workflow as having FHIR export
                        data["exported_to_fhir"] = True
                    else:
                        logger.warning(
                            f"FHIR export failed: {fhir_result.get('error', 'Unknown error')}"
                        )
                else:
                    logger.warning(
                        "No database session available - skipping FHIR export"
                    )

            except ImportError as e:
                logger.warning(f"FHIR export service not available: {e}")
            except Exception as e:
                logger.error(f"FHIR export generation failed: {e}", exc_info=True)
                # Don't fail the entire report generation if FHIR export fails

            logger.info("=== FHIR EXPORT GENERATION END ===")
        else:
            logger.debug(
                "FHIR export disabled via FHIR_EXPORT_ENABLED environment variable"
            )

        # Add the processed data to the report paths for reference
        report_paths["processed_data"] = data

        logger.info(f"Report generation completed successfully")
        logger.info(f"Generated {len(report_paths)} report paths")
        logger.info(f"Report paths keys: {list(report_paths.keys())}")

        return report_paths

    except Exception as e:
        logger.error(f"Error generating report: {str(e)}")
        raise ReportGenerationError(f"Failed to generate report: {str(e)}")
