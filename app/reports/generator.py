import base64
import csv
import dataclasses
import glob
import gzip
import html
import importlib.metadata
import json
import logging
import os
import re
import shutil
import struct
import uuid
import zlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Tuple

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

from app.core.version_manager import get_components, get_versions_dict
from app.mtdna.mt_rnr1 import (
    BASIS_INFERRED,
    BASIS_MEASURED,
    NO_CALL_COVERAGE_BELOW_FLOOR,
    NO_CALL_COVERAGE_UNKNOWN,
    NO_CALL_NO_CHRM_DATA,
    NO_CALL_NO_MITO_CONTIG,
    NO_CALL_NOT_CONSENTED,
    NO_CALL_REGION_NOT_COVERED,
    NO_CALL_UNRESOLVED_961_DELINS,
    REFERENCE,
)
from app.pharmcat.pharmcat_client import normalize_pharmcat_results
from app.pharmcat.report_json import (
    SUPPORTED_VERSION_SERIES,
    extract_matcher_metadata,
    parse_pharmcat_version,
)
from app.reports.alt_locus import (
    alt_locus_alert,
    alt_locus_diagram_line,
    alt_locus_flags,
    alt_realignment_diagram_line,
    annotate_alt_locus,
    realigned_alt_loci,
)
from app.reports.copy_number import (
    annotate_copy_number,
    copy_number_alert,
    genes_without_copy_number,
    why_not_assessed,
)
from app.reports.credits import CreditRun, ref_numbers, report_credits
from app.reports.evidence import classify_evidence
from app.reports.pdf_fit import fill_summary_page, fit_sections, mark_headings
from app.reports.pharmcat_tsv_parser import (
    executive_summary_call,
    parse_pharmcat_tsv,
    unresolved_summary_call,
)
from app.reports.provenance import (
    CALLED_BY_NO_CALL,
    resolve_called_by,
    resolve_guideline_source,
)
from app.services.pharmcat_data_service import PharmCATDataService
from app.utils.env import env_flag
from app.utils.literature import (
    format_literature_reference,
    format_literature_references,
)
from app.utils.pharmcat_assume_ref import (
    ALIGNMENT_INPUT_TYPES,
    EXPLICIT_CALL_INPUT_TYPES,
)
from app.visualizations.job_workflow import (
    RunFacts,
    build_job_graph,
    load_job_file_type,
    load_job_step_records,
    to_html,
    to_mermaid,
)
from app.visualizations.workflow_diagram import render_with_kroki

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
    - "The Genome Analysis Toolkit () v4.7.0.0" -> "4.7.0.0"
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


def _responsive_svg(svg_str: str) -> str:
    """Make Kroki's SVG responsive-friendly for HTML/PDF rendering.

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


# A run whose steps were not recorded gets no diagram rather than a generic one, which
# would describe some other run.
WORKFLOW_NOT_RECORDED_HTML = (
    '<p class="workflow-text">The steps of this run were not recorded.</p>'
)


def job_workflow_diagram(
    steps, file_type: Optional[str], facts: Optional[RunFacts] = None
) -> Dict[str, Any]:
    """The run's workflow diagram in each form the reports use.

    ``steps`` is the job's (step_name, status) rows (job_workflow.load_job_steps);
    ``facts``, what the run produced (workflow_run_facts).
    Kroki draws the graph as a PNG, which every report shows: WeasyPrint drops the
    node labels of Kroki's SVG, and the HTML reports' CSS restyled its text. The SVG
    is kept as a file beside the reports. ``pdf_style`` sizes the PNG on the PDF's
    diagram page; ``html`` is the same steps as text, for when Kroki cannot draw it.
    """
    diagram = {
        "recorded": bool(steps),
        "svg": "",
        "svg_bytes": b"",
        "png": b"",
        "png_data_uri": "",
        "pdf_style": "",
        "html": WORKFLOW_NOT_RECORDED_HTML,
    }
    if not steps:
        return diagram
    graph = build_job_graph(steps, file_type, facts)
    diagram["html"] = to_html(graph)
    source = to_mermaid(graph)
    try:
        svg_bytes = render_with_kroki(source, fmt="svg")
        diagram["svg_bytes"] = svg_bytes
        diagram["svg"] = _responsive_svg(svg_bytes.decode("utf-8", errors="ignore"))
    except Exception as e:
        logger.warning(f"Kroki could not draw the workflow diagram as SVG: {e}")
    try:
        png = render_with_kroki(source, fmt="png")
        diagram["png"] = png
        diagram["png_data_uri"] = "data:image/png;base64," + base64.b64encode(
            png
        ).decode("ascii")
        diagram["pdf_style"] = workflow_pdf_style(png)
    except Exception as e:
        logger.warning(f"Kroki could not draw the workflow diagram as PNG: {e}")
    return diagram


# The PDF's diagram page (report_template.html, @page workflow-page): A4 less its
# 5mm margins, 200 x 287mm. The diagram keeps to the 178mm text width of the other
# pages, starts under the page's title and stops above the page number.
_DIAGRAM_WIDTH_MM = 178.0
_DIAGRAM_TOP_MM = 26.0
_DIAGRAM_BOTTOM_MM = 11.0
_DIAGRAM_PAGE_HEIGHT_MM = 287.0


def workflow_pdf_style(png: bytes) -> str:
    """The diagram's size and place on its PDF page: as large as the page allows,
    in its own proportions, centred across the page under its title.

    Set on the image itself. Left to object-fit inside a fixed box, WeasyPrint
    placed a diagram wider than the box's proportions 130px to the left of it: its
    left side ran off the page and an empty strip was left on the right.
    """
    if len(png) < 24 or png[:8] != b"\x89PNG\r\n\x1a\n":
        return ""
    width_px, height_px = struct.unpack(">II", png[16:24])
    if not width_px or not height_px:
        return ""
    area_w = _DIAGRAM_WIDTH_MM
    area_h = _DIAGRAM_PAGE_HEIGHT_MM - _DIAGRAM_TOP_MM - _DIAGRAM_BOTTOM_MM
    scale = min(area_w / width_px, area_h / height_px)
    width, height = width_px * scale, height_px * scale
    top = _DIAGRAM_TOP_MM
    return (
        f"width: {width:.1f}mm; height: {height:.1f}mm; "
        f"margin: {top:.1f}mm auto 0 auto;"
    )


def _count(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def pypgx_gene_counts(
    output: Dict[str, Any],
) -> Optional[Tuple[int, int, int, int, int, int]]:
    """(genotyped, panel, with no reads, with no genotype in the VCF, failed, genotyped
    with positions read as reference) from the PyPGx step's output.

    The wrapper's step_gene_counts records these directly, with
    ``not_sequenced_genes`` and ``no_genotype_genes``. Steps recorded before it counted
    genes with no reads as successful and dropped them from ``total_genes`` (a sliced
    BAM read "66 of 1 genes genotyped"); the true counts follow from the same record:
    the genes it ran were ``total_genes`` and those that failed are ``failed_genes``.
    """
    done, of = _count(output.get("successful_genes")), _count(output.get("total_genes"))
    if done is None or not of:
        return None
    failed = _count(output.get("failed_genes")) or 0
    if "not_sequenced_genes" in output:
        no_reads = _count(output.get("not_sequenced_genes")) or 0
        no_genotype = _count(output.get("no_genotype_genes")) or 0
        partial = _count(output.get("partially_genotyped_genes")) or 0
        return done, of, no_reads, no_genotype, failed, partial
    panel = done + failed
    return of - failed, panel, max(panel - of, 0), 0, failed, 0


def workflow_run_facts(
    records: List[Dict[str, Any]],
    platform: List[Dict[str, str]],
    genes: List[Dict[str, Any]],
    mtdna_result: Optional[Dict[str, Any]],
    absent_to_ref: Optional[bool],
    drug_count: Optional[int],
    outputs: List[str],
    summary: Optional[List[Dict[str, Any]]] = None,
    input_build: Optional[str] = None,
) -> RunFacts:
    """What the run produced, for its workflow diagram: each tool's version (as
    the Platform table gives it), each step's time, and per step what it yielded.
    Only facts the run recorded; nothing is inferred.

    ``summary`` is the Executive Summary's rows (summary_rows), whose called genes
    PharmCAT's count repeats. ``outputs`` is lines naming what the run writes.
    ``input_build`` is the build the upload's header analysis detected, which is
    recorded for VCFs."""
    facts = RunFacts()
    facts.versions = {
        item["name"]: item["version"]
        for item in platform
        if item.get("version") and item["version"] not in ("not recorded", "N/A")
    }
    by_name = {r["name"]: r for r in records}
    facts.seconds = {
        r["name"]: r["seconds"] for r in records if r.get("seconds") is not None
    }
    called = {
        str(g.get("gene") or "").upper(): g
        for g in genes or []
        if gene_was_called(g.get("diplotype"))
    }
    panel = _summary_panel()

    header = (by_name.get("header_analysis") or {}).get("output") or {}
    if header.get("contigs_renamed"):
        # upload_router.use_ucsc_contig_names: the file named its chromosomes by
        # NCBI accession, and the pipeline read a copy under UCSC names.
        facts.details["header_analysis"] = ["chromosomes renamed from NCBI accessions"]

    lift = by_name.get("liftover")
    if lift and lift.get("status") == "completed":
        out = lift.get("output") or {}
        lifted, rejected = _count(out.get("n_lifted")), _count(out.get("n_rejected"))
        if lifted is not None:
            line = f"{lifted:,} variants lifted"
            if rejected:
                line += f", {rejected:,} could not be"
            facts.details["liftover"] = [line]
        source = str(out.get("source_build") or "").strip() or "GRCh37"
        target = str(out.get("target_build") or "").strip() or "GRCh38"
        facts.input_detail = f"{source}, lifted to {target}"
    elif input_build and not lift:
        facts.input_detail = str(input_build)

    positions = (by_name.get("pypgx_bam2vcf") or {}).get("output") or {}
    positions = positions.get("pharmcat_positions") or {}
    total = _count(positions.get("n_pharmcat_positions"))
    if total:
        genotyped = (_count(positions.get("n_reference")) or 0) + (
            _count(positions.get("n_variant")) or 0
        )
        no_reads = _count(positions.get("n_no_reads")) or 0
        unsure = _count(positions.get("n_uncertain")) or 0
        line = f"PharmCAT's {total:,} positions: {genotyped:,} called"
        if no_reads:
            line += f", {no_reads:,} no reads"
        if unsure:
            line += f", {unsure:,} uncertain"
        facts.details["pypgx_bam2vcf"] = [line]

    pypgx = (by_name.get("pypgx_analysis") or {}).get("output") or {}
    counts = pypgx_gene_counts(pypgx)
    if counts:
        done, of, no_reads, no_genotype, failed, partial = counts
        line = f"{done} of {of} genes genotyped"
        if partial:
            line += f" ({partial} with positions read as reference)"
        if no_reads:
            line += f", {no_reads} with no reads"
        if no_genotype:
            line += f", {no_genotype} with no genotype in the VCF"
        if failed:
            line += f", {failed} failed"
        facts.details["pypgx_analysis"] = [line]
    facts.produced["cyp2d6"] = "CYP2D6" in called
    facts.produced["other_genes"] = any(
        name not in panel and g.get("called_by") == "P" for name, g in called.items()
    )

    hla = [name for name in ("HLA-A", "HLA-B", "HLA-C") if name in called]
    facts.produced["hla"] = bool({"HLA-A", "HLA-B"} & set(hla))
    facts.produced["hla_c"] = "HLA-C" in hla
    hla_step = by_name.get("hla_typing") or {}
    if hla_step.get("status") == "completed":
        facts.details["hla_typing"] = [
            ", ".join(hla) + " typed" if hla else "no HLA allele called"
        ]
    elif (hla_step.get("output") or {}).get("reason") == "hla_reads_not_located":
        # zarohla could not find the HLA reads and would not convert the whole file
        # to look: nothing was typed, which "no HLA allele called" would hide.
        facts.details["hla_typing"] = ["HLA reads not located: HLA-A, -B, -C not typed"]

    mtdna_ran = (by_name.get("mtdna_analysis") or {}).get("status") == "completed"
    if mtdna_result is not None and mtdna_ran:
        haplogroup = mtdna_result.get("haplogroup")
        mt_rnr1 = mtdna_result.get("mt_rnr1")
        facts.produced["haplogroup"] = bool(haplogroup)
        facts.produced["mt_rnr1"] = bool(mt_rnr1)
        if mtdna_result.get("mt_rnr1_no_call_reason") == NO_CALL_NO_CHRM_DATA:
            line = "no calls on chrM: no haplogroup, no MT-RNR1"
        elif mtdna_result.get("mt_rnr1_no_call_reason") == NO_CALL_NO_MITO_CONTIG:
            line = "no chrM in the alignment: no haplogroup, no MT-RNR1"
        else:
            line = f"haplogroup {haplogroup}" if haplogroup else "no haplogroup"
            line += f" · MT-RNR1 {mt_rnr1}" if mt_rnr1 else " · MT-RNR1 not called"
        facts.details["mtdna_analysis"] = [line]

    if summary:
        done = sum(1 for row in summary if row.get("called"))
        facts.details["pharmcat_analysis"] = [f"{done} of {len(summary)} genes called"]
        if drug_count:
            facts.details["pharmcat_analysis"].append(
                f"{drug_count} drugs with guidance"
            )
        if absent_to_ref:
            facts.details["pharmcat_analysis"].append(
                "positions absent from the file read as reference"
            )

    # Kept apart from the positions line above: a fact about the reads, not a count.
    bam2vcf_output = (by_name.get("pypgx_bam2vcf") or {}).get("output")
    for alt_line in (
        alt_realignment_diagram_line(bam2vcf_output),
        alt_locus_diagram_line(alt_locus_flags(bam2vcf_output)),
    ):
        if alt_line:
            facts.details.setdefault("pypgx_bam2vcf", []).append(alt_line)

    if outputs:
        facts.details["report_generation"] = list(outputs)
    return facts


def report_branding_context() -> Dict[str, Any]:
    """The footer/branding keys every renderer of these templates must supply.

    Four separate dicts feed report_template.html and interactive_report.html,
    and each listed these keys by hand. One of them -- the dict that rebinds
    `template_data` further down and is the one actually rendered into the PDF --
    listed author_name, license_name, license_url and source_url but not
    current_year, so the PDF footer read "(c) 2024- Iliya Yaroshevskiy" while the
    other three lanes were fine.

    That is why this has been "fixed" repeatedly without staying fixed: the fix
    kept landing on whichever dict someone happened to open, and three of the four
    do not render that page. Returning the set from one place removes the choice.
    Missing a key here is a KeyError at the call site, not a blank in a footer.
    """
    return {
        "author_name": get_author_name(),
        "license_name": get_license_name(),
        "license_url": get_license_url(),
        "source_url": get_source_url(),
        "current_year": datetime.now().year,
    }


def app_components() -> Dict[str, str]:
    """Versions of the libraries this app itself runs for a report."""
    found = {}
    for name in ("weasyprint", "pysam"):
        try:
            found[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            continue
    return found


def credit_run_for_job(
    records: Optional[List[Dict[str, Any]]],
    file_type: Optional[str],
    pharmcat_data_version: Optional[str] = None,
    kroki: bool = True,
) -> CreditRun:
    """The facts about this job that decide what its report credits.

    ``records`` are its step rows (job_workflow.load_job_step_records); none means
    they could not be read, and the report then credits everything that could
    have run, rather than leave out something that did.
    """
    completed = (
        frozenset(r["name"] for r in records if r.get("status") == "completed")
        if records
        else None
    )
    # Not a step of its own: pypgx_bam2vcf aligned some reads again (BWA-MEM via
    # GATK; app/reports/alt_locus.py), which the credits name as if it were one.
    bam2vcf = next(
        (r.get("output") for r in records or () if r["name"] == "pypgx_bam2vcf"),
        None,
    )
    if (
        completed is not None
        and isinstance(bam2vcf, dict)
        and realigned_alt_loci(bam2vcf)
    ):
        completed = completed | {"alt_realignment"}
    pypgx = next(
        (r.get("output") for r in records or () if r["name"] == "pypgx_analysis"),
        None,
    )
    return CreditRun(
        steps=completed,
        file_type=file_type,
        pypgx=pypgx if isinstance(pypgx, dict) else None,
        pdf=bool(REPORT_CONFIG.get("write_pdf", True)),
        interactive=bool(REPORT_CONFIG.get("write_interactive_html", True)),
        kroki=kroki,
        pharmcat_data_version=pharmcat_data_version,
    )


def build_report_credits(run: Optional[CreditRun] = None) -> Dict[str, Any]:
    """What this run's report credits (app/reports/credits.py), at the versions
    each service recorded (data/versions/about.md)."""
    components = dict(get_components())
    components["app"] = app_components()
    return report_credits(
        run or CreditRun(),
        get_versions_dict(),
        components,
        get_zaropgx_version(),
    )


def platform_from_credits(credits: Dict[str, Any]) -> List[Dict[str, str]]:
    """The software a run used and its versions, as the workflow diagram labels
    its steps with them."""
    return [
        {"name": row["name"], "version": row["version"]}
        for row in credits.get("software", [])
    ]


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


def display_sample_identifier(*candidates: Any) -> Optional[str]:
    """The first candidate that names a sample: non-blank and not a UUID.

    The ids the pipeline mints (patient, job, PharmCAT run title) are UUIDs, and a
    UUID under "Sample ID" was always one of those, never a sample's name.
    """
    for value in candidates:
        text = str(value or "").strip()
        if text and not _is_uuid_like(text):
            return text
    return None


def entered_sample_identifier(
    job_metadata: Optional[Dict[str, Any]], patient_info: Optional[Dict[str, Any]]
) -> Optional[str]:
    """The sample identifier the reports print, or None if there is none.

    The one entered at upload, else the one read from the file's header; the
    upload keeps both in the job's metadata, and upload_router also passes its
    pick as ``patient_info["sample_identifier"]`` (falling back to the patient
    id, which is not a sample identifier). Neither ever reached a report:
    generate_report took the PharmCAT run's title first -- the patient UUID --
    and swapped any UUID-like value for the job id read off the PharmCAT file
    names, so every report's "Sample ID" was the job id.
    """
    meta = job_metadata or {}
    info = patient_info or {}
    for value in (
        meta.get("sample_identifier"),
        meta.get("header_sample_identifier"),
        info.get("sample_identifier"),
    ):
        text = str(value or "").strip()
        if text and text != str(info.get("id") or ""):
            return text
    return None


# Read PharmCAT report configuration from environment variables
INCLUDE_PHARMCAT_HTML = env_flag("INCLUDE_PHARMCAT_HTML", True)
INCLUDE_PHARMCAT_JSON = env_flag("INCLUDE_PHARMCAT_JSON", False)
INCLUDE_PHARMCAT_TSV = env_flag("INCLUDE_PHARMCAT_TSV", False)
EXECSUM_USE_TSV = env_flag("EXECSUM_USE_TSV", False)


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
#   VCF, BCF, 23andMe,  -> "Possibly Wild Type". A variant-only file is silent
#   AncestryDNA            about positions it does not list, so the absence of a
#                          variant call is not evidence of the reference allele.
#   BAM/CRAM/SAM/FASTQ, -> "Likely Wild Type". PharmCAT was given an explicit call
#   gVCF                   at every one of its positions (from the reads, or the
#                          gVCF's reference blocks), so the absence of a variant
#                          is positive evidence.
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
_VARIANT_ONLY_FILE_TYPES = frozenset(
    {"vcf", "vcf.gz", "vcf.bgz", "bcf", "23andme", "ancestry"}
)
# The inputs main.nf genotypes at every PharmCAT position (pharmcat_assume_ref).
_EXPLICIT_CALL_FILE_TYPES = EXPLICIT_CALL_INPUT_TYPES | {"fq"}


def wild_type_phenotype(
    diplotype: Any, phenotype: Any, file_type: Any
) -> Optional[str]:
    """Return the wild-type label for a row, or ``None`` to leave it alone.

    ``None`` means "not a wild-type row" *and* "file type cannot support the
    claim" -- callers must not substitute a default label for it.
    """
    diplotype_str = str(diplotype or "").strip()
    if not _is_reference_diplotype(diplotype_str):
        return None

    phenotype_str = str(phenotype or "").strip()
    if phenotype_str and phenotype_str.lower() not in _ABSENT_PHENOTYPES:
        return None

    normalized_file_type = str(file_type or "").strip().lower()
    if normalized_file_type in _VARIANT_ONLY_FILE_TYPES:
        return WILD_TYPE_VARIANT_ONLY_LABEL
    if normalized_file_type in _EXPLICIT_CALL_FILE_TYPES:
        return WILD_TYPE_ALIGNED_READS_LABEL
    return None


# PharmCAT 3.4 names a one-position gene's reference allele by its position and
# base: VKORC1 "rs9923231 reference (C)", F5 "rs6025 reference (C)". Two of them are
# a reference call as much as CYP4F2's *1/*1 is. Reports before October 2026 printed
# these as Reference/Reference and labelled them; with the names, VKORC1, F2 and F5
# fell to "N/A" while CYP4F2 beside them kept its label.
_NAMED_REFERENCE_ALLELE = re.compile(r"rs\d+ reference \([ACGT]+\)", re.IGNORECASE)


def _is_reference_diplotype(diplotype: str) -> bool:
    if diplotype.upper() in _REFERENCE_DIPLOTYPES:
        return True
    alleles = [allele.strip() for allele in diplotype.split("/")]
    return len(alleles) == 2 and all(
        _NAMED_REFERENCE_ALLELE.fullmatch(allele) for allele in alleles
    )


# The Phenotype for a call whose calling tool defines no phenotype for the gene, as
# PharmCAT's own report prints it. Not "Indeterminate": CPIC reserves that for a
# diplotype with uncertain- or unknown-function alleles in a gene that does have
# phenotype rules. Not "Unknown": nothing about this sample is unknown.
NO_PHENOTYPE_STANDARD = "N/A"

# The genes PharmCAT 3.4 calls without a phenotype: their guidance keys on the
# genotype itself (CPIC warfarin on CYP4F2 and VKORC1, peginterferon on IFNL3). Its
# example TSV and 30x NA12878's leave exactly these blank, apart from DPYD's
# unphased source call there, whose lookup call has a phenotype. (2.x also left
# NAT2 blank; 3.x gives it one.) F2 and F5 too: PharmCAT calls them under
# unannotatedGeneCalls, phenotype "n/a", each allele's function saying what the
# call means ("Factor V Leiden negative").
PHARMCAT_GENES_WITHOUT_PHENOTYPE = frozenset({"CYP4F2", "F2", "F5", "IFNL3", "VKORC1"})


def defines_no_phenotype(row: Dict[str, Any]) -> bool:
    """Whether the tool that called this row's gene defines no phenotype for it:
    PyPGx, for a gene its gene table gives no PhenotypeMethod; OptiType, for the HLA
    genes PharmCAT has no row for (HLA-C); PharmCAT, for
    PHARMCAT_GENES_WITHOUT_PHENOTYPE. A blank phenotype anywhere else is missing
    data, not "N/A". PyPGx's "Indeterminate" genotype is no call at all."""
    if not gene_was_called(row.get("diplotype")):
        return False
    if str(row.get("diplotype") or "").strip().lower() == "indeterminate":
        return False
    if row.get("pyPgxOnly"):
        return not row.get("pypgx_phenotype_method")
    if row.get("optitypeOnly"):
        return True
    return (
        str(row.get("gene") or "").strip().upper() in PHARMCAT_GENES_WITHOUT_PHENOTYPE
    )


# PyPGx's copy-number codes (pypgx/api/data/cnv-table.csv), by family, longest
# prefix first. The code itself is always shown beside the description: PyPGx
# documents what each numbered variant is, gene by gene.
_COPY_NUMBER_FAMILIES = (
    ("ParalogPartialDel", "partial deletion of the paralog"),
    ("ParalogWholeDel", "whole deletion of the paralog"),
    ("ParalogWholeDup", "duplication of the paralog"),
    ("NoncodingDel", "deletion of non-coding sequence"),
    ("NoncodingDup", "duplication of non-coding sequence"),
    ("WholeMultip", "whole-gene multiplication (two or more extra copies)"),
    ("WholeDup", "whole-gene duplication"),
    ("WholeDel", "whole-gene deletion"),
    ("PartialDel", "partial-gene deletion"),
    ("PartialDup", "partial-gene duplication"),
    ("Hybrid", "hybrid gene"),
    ("Tandem", "tandem arrangement (see diplotype)"),
    ("Unknown", "unclassified copy-number pattern"),
)
# Families whose event sits on one chromosome unless the code ends in "Hom".
_ONE_CHROMOSOME_FAMILIES = frozenset(
    {
        "NoncodingDel",
        "NoncodingDup",
        "WholeDup",
        "WholeDel",
        "PartialDel",
        "PartialDup",
        "Hybrid",
    }
)
# G6PD is on chromosome X: there PyPGx's copy-number call is the X count.
_X_CHROMOSOME_CALLS = {"Female": "two X chromosomes", "Male": "one X chromosome"}


def copy_number_summary(code: Optional[str]) -> Optional[str]:
    """A plain description of a PyPGx copy-number code, or None for none."""
    text = str(code or "").strip()
    if not text:
        return None
    parts = []
    for part in text.split("+"):
        if part == "Normal":
            parts.append("normal copy number")
            continue
        if part in _X_CHROMOSOME_CALLS:
            parts.append(_X_CHROMOSOME_CALLS[part])
            continue
        family = next((f for f in _COPY_NUMBER_FAMILIES if part.startswith(f[0])), None)
        if family is None:
            parts.append("structural variant")
        elif part.endswith("Hom"):
            parts.append(f"{family[1]} on both chromosomes")
        elif family[0] in _ONE_CHROMOSOME_FAMILIES:
            parts.append(f"{family[1]} on one chromosome")
        else:
            parts.append(family[1])
    return " + ".join(parts)


def _same_diplotype(a: Any, b: Any) -> bool:
    """Same call, ignoring the spacing PharmCAT and PyPGx write tandems with
    ("*68 + *4" against "*68+*4")."""
    left, right = "".join(str(a or "").split()), "".join(str(b or "").split())
    return bool(left) and left == right


def optitype_diplotype(call: Any) -> str:
    """OptiType's pair as the report writes HLA calls. zarohla returns
    "C*01:02,C*07:01"; main.nf (OptiTypeHLAFromBAM) hands PharmCAT "*01:02/*07:01",
    which is how PharmCAT's HLA-A and HLA-B rows read."""
    alleles = [a.strip() for a in str(call or "").split(",") if a.strip()]
    return "/".join("*" + a.split("*", 1)[1] if "*" in a else a for a in alleles)


def merge_optitype_calls(genes: List[Dict[str, Any]], report_dir: str) -> int:
    """Put the run's OptiType calls (hla_result.json, zarohla's /call-hla response)
    into the report's gene rows; returns how many rows it added.

    OptiType's calls reached the report only through PharmCAT, as outside calls.
    PharmCAT has no HLA-C, so HLA-C was typed and then dropped: every report listed
    it as no call. And PharmCAT's HLA-A and HLA-B rows record only that the call came
    from outside, so they read "producing tool not recorded" though this run's
    OptiType made them. As for PyPGx's CYP2D6, a PharmCAT outside call that matches
    OptiType's is OptiType's; one that does not (a manual outside call) is left alone.
    """
    try:
        with open(
            os.path.join(report_dir, "hla_result.json"), "r", encoding="utf-8"
        ) as fh:
            results = (json.load(fh) or {}).get("results") or {}
    except (OSError, ValueError, AttributeError):
        return 0
    if not isinstance(results, dict):
        return 0
    rows = {
        str(g.get("gene") or g.get("name") or "").strip().upper(): g
        for g in genes
        if isinstance(g, dict)
    }
    added = 0
    for gene, call in results.items():
        gene = str(gene).strip()
        diplotype = optitype_diplotype(call)
        if not gene.upper().startswith("HLA-") or not diplotype:
            continue
        row = rows.get(gene.upper())
        if row is None:
            row = {
                "gene": gene,
                "diplotype": diplotype,
                "phenotype": None,
                "activity_score": None,
                "tool_source": "OptiType",
                # OptiType types alleles; it assigns no phenotype.
                "optitypeOnly": True,
            }
            genes.append(row)
            rows[gene.upper()] = row
            added += 1
        elif str(row.get("call_source") or "").upper() == "OUTSIDE" and _same_diplotype(
            row.get("diplotype"), diplotype
        ):
            row["tool_source"] = "OptiType"
    return added


def credit_mtdna_call(genes: List[Dict[str, Any]], report_dir: str) -> bool:
    """Credit PharmCAT's MT-RNR1 row to mtDNA-Server 2 when it is the run's own call.

    MtdnaCall (main.nf) hands PharmCAT mtdna_result.json's ``mt_rnr1`` as an outside
    call, and PharmCAT records only that the call came from outside: the gene table
    read "producing tool not recorded" for a call this run made. A row that does not
    match (a manual outside call) is left alone, as for OptiType's and PyPGx's.
    """
    try:
        with open(
            os.path.join(report_dir, "mtdna_result.json"), "r", encoding="utf-8"
        ) as fh:
            call = (json.load(fh) or {}).get("mt_rnr1")
    except (OSError, ValueError, AttributeError):
        return False
    for row in genes:
        if (
            isinstance(row, dict)
            and str(row.get("gene") or row.get("name") or "").strip().upper()
            == "MT-RNR1"
            and str(row.get("call_source") or "").upper() == "OUTSIDE"
            and _same_diplotype(row.get("diplotype"), call)
        ):
            row["tool_source"] = "mtDNA-Server-2"
            return True
    return False


def pypgx_record(details: Dict[str, Any]) -> Dict[str, Any]:
    """What PyPGx reported for a gene beyond its genotype (docker/pypgx's
    parse_pypgx_results): candidate alleles, alternative phasings, the copy-number
    call, the variants behind each allele, and its plots."""
    record: Dict[str, Any] = {}
    for key in (
        "haplotype1",
        "haplotype2",
        "alternative_phase",
        "supporting_variants",
        "plots",
    ):
        if details.get(key):
            record[key] = details[key]
    if details.get("copy_number"):
        record["copy_number"] = details["copy_number"]
        record["copy_number_summary"] = copy_number_summary(details["copy_number"])
    return record


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


# Upstream's own floor (docker/mtdna-server-2/app.py's MIN_MEAN_COVERAGE,
# nextflow.config params.min_mean_coverage upstream). Repeated here only for
# the no-call explanation below -- not read back from the sidecar, which
# never echoes the threshold it used, only the coverage it measured.
_MTDNA_MIN_MEAN_COVERAGE = 50


def mtdna_report_context(
    report_dir: str, genes: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """The mtDNA report section's context, from the run's own mtdna_result.json.

    MtdnaCall (pipelines/pgx/main.nf) publishDir-copies the sidecar's own
    /call-mtdna response, unmodified, into report_dir as mtdna_result.json.
    Its presence is what "mtDNA actually ran" means for this report: Task
    11's toggle can be on with no completed run yet (params.skip_mtdna
    still defaults true -- see tests/test_mtdna_citation_honesty.py), but
    this file only exists once a call has finished, successfully or as an
    explicit no-call.

    "used": False is what makes both templates render nothing at all --
    a job that never touched the mtDNA toggle must look exactly as it did
    before this feature existed, not show an empty mtDNA panel.

    When MT-RNR1 comes back empty, "no_call_reason" names the concrete cause
    rather than leaving a blank row for the reader to guess at -- the thing
    this whole effort exists to remove. The cause is read directly off
    "mt_rnr1_no_call_reason", a reason code the sidecar itself decides
    (docker/mtdna-server-2/app.py's _call_from_alignment and _call_from_vcf,
    via app.mtdna.mt_rnr1.resolve_mt_rnr1_call -- the one place the
    "empty match -> Reference" promotion happens, and the only place that
    knows which of several distinct reasons actually applied):
      - VCF input, no chrM data at all: the VCF has no chrM record (a chrM
        contig header alone does not count), so an absent MT-RNR1 record is
        indistinguishable from "never sequenced" -- e.g. an ordinary
        nuclear-only PGx VCF (NO_CALL_NO_CHRM_DATA), with the option on or
        off;
      - VCF input, chrM data present: the job did not set
        pharmcat_absent_to_ref, so an absent record stayed ambiguous between
        "reference here" and "never covered here" (NO_CALL_NOT_CONSENTED);
      - either input: an unresolved deletion/delins sits on top of position
        961 and matched no named allele -- could be the real, but
        deliberately unmatchable, m.961T>del+Cn (NO_CALL_UNRESOLVED_961_DELINS);
      - alignment input (BAM/CRAM/FASTQ): mean coverage across MT-RNR1 fell
        below the MIN_MEAN_COVERAGE floor (NO_CALL_COVERAGE_BELOW_FLOOR) or
        could not be computed at all (NO_CALL_COVERAGE_UNKNOWN);
      - VCF input, chrM data present: mitochondrial data is present but
        nothing established that MT-RNR1 itself was interrogated -- an empty
        match with no in-gene variant, or one with no clean haplogroup to
        back it (NO_CALL_REGION_NOT_COVERED). Distinct from
        NO_CALL_NO_CHRM_DATA (no chrM data at all): the two suggest different
        remedies to the reader.
    A result produced before this reason code existed, or carrying an
    unrecognised one, falls back to a generic explanation rather than
    re-deriving the reason from indirect signals (e.g. "mean_coverage" being
    a key at all) -- that re-derivation is exactly the bug this replaced:
    it could not tell "no chrM data" apart from "consent not given".

    When "mt_rnr1" resolved to Reference, "evidence_basis" (set by the
    sidecar's resolve_mt_rnr1_call, via app/mtdna/mt_rnr1.py's
    BASIS_MEASURED / BASIS_INFERRED) says how: a real depth measurement over
    the gene, or Tier C's in-gene-variant-plus-clean-haplogroup inference.
    That distinction becomes "call_basis_text" here -- a named allele gets
    no such line, since the variant is its own evidence.

    Never raises: a report with a malformed or unreadable mtdna_result.json
    renders no mtDNA section, the same as a run that never called it.
    """
    result_path = os.path.join(report_dir, "mtdna_result.json")
    if not os.path.exists(result_path):
        return {"used": False}
    try:
        with open(result_path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except Exception as exc:
        logger.warning(f"Could not read mtdna_result.json: {exc}")
        return {"used": False}

    mt_rnr1 = raw.get("mt_rnr1")
    no_call_reason = None
    if not mt_rnr1:
        reason_code = raw.get("mt_rnr1_no_call_reason")
        coverage = raw.get("mean_coverage")
        if reason_code == NO_CALL_NO_CHRM_DATA:
            # The sidecar's test is whether the file has any chrM record; its
            # header may still declare chrM (a provider's export can keep the
            # contig line and drop the calls).
            no_call_reason = (
                "No MT-RNR1 variant was detected, and this VCF has no calls on "
                "the mitochondrial chromosome (chrM), so the absence could not "
                "be confirmed as Reference, and no haplogroup was assigned. A "
                "VCF that keeps its chrM calls, or the BAM or CRAM, gives both."
            )
        elif reason_code == NO_CALL_NO_MITO_CONTIG:
            no_call_reason = (
                "MT-RNR1 was not called: this alignment's header names no "
                "mitochondrial chromosome (chrM, MT or M), so its mitochondrial "
                "reads, if it has any, could not be read, and no haplogroup was "
                "assigned."
            )
        elif reason_code == NO_CALL_NOT_CONSENTED:
            no_call_reason = (
                "No MT-RNR1 variant was detected, and the run did not assume "
                'the reference allele where the file has no record ("Assume '
                'absent sites = reference", under PharmCAT on the upload page, '
                "was off), so an absent record could not be resolved to "
                "Reference."
            )
        elif reason_code == NO_CALL_UNRESOLVED_961_DELINS:
            no_call_reason = (
                "No MT-RNR1 variant was detected as a named allele, but "
                "this sample carries an unresolved deletion or insertion "
                "at position 961 that cannot be confidently distinguished "
                "from m.961T>del+Cn, so it was not called Reference."
            )
        elif reason_code == NO_CALL_COVERAGE_BELOW_FLOOR and coverage is not None:
            no_call_reason = (
                "No MT-RNR1 variant was detected, and mean coverage across "
                f"the gene ({coverage:.1f}x) was below the "
                f"{_MTDNA_MIN_MEAN_COVERAGE}x floor needed to call Reference "
                "from absence."
            )
        elif reason_code == NO_CALL_COVERAGE_UNKNOWN:
            no_call_reason = (
                "No MT-RNR1 variant was detected, and mean coverage across "
                "the gene could not be determined, so the absence could not "
                "be confirmed as Reference."
            )
        elif reason_code == NO_CALL_REGION_NOT_COVERED:
            no_call_reason = (
                "No call — mitochondrial data is present, but MT-RNR1 "
                "coverage could not be established from this file."
            )
        else:
            # Defensive fallback only -- a result missing this reason code
            # (older format) or carrying one this report does not recognise.
            # Never re-derive the reason from indirect signals like whether
            # "mean_coverage" is a key: that inference is what conflated
            # "no chrM data" with "consent not given" in the first place.
            no_call_reason = (
                "No MT-RNR1 variant was detected, and it could not be "
                "confirmed as Reference."
            )

    # How a Reference call was established -- only meaningful alongside
    # Reference itself. A named allele needs no basis line (the variant is
    # its own evidence), and a no-call has nothing to describe.
    call_basis_text = None
    if mt_rnr1 == REFERENCE:
        basis = raw.get("evidence_basis")
        if basis == BASIS_MEASURED:
            coverage = raw.get("mean_coverage")
            if coverage is not None:
                call_basis_text = (
                    "Reference — mitochondrial coverage measured at "
                    f"{coverage:.0f}x across MT-RNR1."
                )
        elif basis == BASIS_INFERRED:
            call_basis_text = (
                "Reference — inferred: mitochondrial variants were called "
                "within MT-RNR1 and no expected haplogroup marker was "
                "missing."
            )

    phenotype = None
    for gene in genes or []:
        if str(gene.get("gene", "")).strip().upper() == "MT-RNR1":
            phenotype = gene.get("phenotype")
            break

    return {
        "used": True,
        "haplogroup": raw.get("haplogroup"),
        "haplogroup_quality": raw.get("haplogroup_quality"),
        "mt_rnr1": mt_rnr1,
        "phenotype": phenotype,
        "no_call_reason": no_call_reason,
        "call_basis_text": call_basis_text,
        "all_matches": raw.get("mt_rnr1_all_matches") or [],
    }


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


# Which colour a phenotype's badge gets. No standards body assigns colours to
# phenotypes (CPIC standardised the terms, Caudle et al. 2017, not their colours;
# PharmCAT's report colours none). The badges colour what the phenotype says
# about the gene's function or the risk screened, against what is typical: they
# do not follow CPIC's EHR priority, which ranks the need to act and so differs
# by gene (a CYP3A5 normal metabolizer is CPIC "Abnormal/Priority", needing more
# tacrolimus than the standard dose set on the more common poor metabolizers; a
# UGT1A1 intermediate is "Normal/Routine"). Whether to act is the drug
# recommendations' to say. Four colours:
#
# - green, normal: Normal Metabolizer or Function, G6PD Normal, an HLA risk allele
#   negative, normal risk of aminoglycoside-induced hearing loss;
# - amber, reduced: intermediate metabolism, decreased function, G6PD Variable;
# - red, absent or at risk: poor metabolism or function, G6PD Deficient, an
#   HLA risk allele positive, Malignant Hyperthermia Susceptibility (PharmCAT's word;
#   "Susceptible" matched only the spelling CPIC's tables do not use), increased risk
#   of aminoglycoside-induced hearing loss;
# - blue: rapid metabolism and increased function, which is not "better" (a CYP2D6
#   ultrarapid metabolizer is at risk on codeine).
#
# Uncertain and indeterminate results, an unresolved "A or B", and anything else
# keep the neutral grey. The label is always printed: colour is never the only cue.
# The CSS classes are colour tiers; their names predate the non-metabolizer terms.
_PHENOTYPE_BADGE_CLASSES = tuple(
    (re.compile(pattern, re.IGNORECASE), css_class)
    for pattern, css_class in (
        (r"\bLikely Wild Type\b", "likely-wild-type"),
        (r"\bPossibly Wild Type\b", "possibly-wild-type"),
        (r"\sor\s", ""),
        (r"\buncertain\b|\bindeterminate\b", ""),
        (r"\bpositive\b", "poor-metabolizer"),
        (r"\bnegative\b", "normal-metabolizer"),
        (r"\bsusceptib(?:le|ility)\b", "poor-metabolizer"),
        (r"\bincreased risk\b", "poor-metabolizer"),
        (r"\bnormal risk\b", "normal-metabolizer"),
        (r"\bdeficient\b", "poor-metabolizer"),
        (r"\bvariable\b", "intermediate-metabolizer"),
        (r"\bnormal\b", "normal-metabolizer"),
        (r"\bpoor\b", "poor-metabolizer"),
        (r"\bintermediate\b", "intermediate-metabolizer"),
        (r"\bdecreased function\b", "decreased-function"),
        (r"\b(?:ultra)?rapid\b", "rapid-metabolizer"),
        (r"\bincreased function\b", "increased-function"),
    )
)


def phenotype_badge_class(phenotype) -> str:
    """The colour class of a phenotype badge, "" for the neutral default."""
    text = str(phenotype or "")
    for pattern, css_class in _PHENOTYPE_BADGE_CLASSES:
        if pattern.search(text):
            return css_class
    return ""


# An HLA allele and what was screened for it: "*57:01 positive".
_ALLELE_RESULT = re.compile(r"(\*\d+:\d+) (positive|negative)\b")


def allele_results_unbroken(phenotype) -> str:
    """The phenotype with each HLA allele kept on one line with its result.

    HLA-B states a result for each of three alleles, more than one line of the
    Executive Summary holds, and its pill broke between "*58:01" and "negative". A
    no-break space between each allele and its result moves the break to a comma.
    """
    return _ALLELE_RESULT.sub("\\1\u00a0\\2", str(phenotype or ""))


def register_report_template_helpers(environment) -> None:
    """Register everything report_template.html needs to compile.

    Jinja resolves filters and tests at *compile* time, so an Environment missing
    one does not render a slightly worse report -- ``get_template()`` raises
    TemplateAssertionError and the caller silently falls back to a stub page. That
    has now happened twice, once per helper, because three separate Environments
    render this template and each had to remember to register them by hand
    (generator.py's two, plus the WeasyPrint lane in pdf_generators.py).

    One function, called by all three, so adding a helper cannot half-land again.
    """
    environment.filters["activity_score_num"] = activity_score_num
    environment.filters["phenotype_badge_class"] = phenotype_badge_class
    environment.filters["allele_results_unbroken"] = allele_results_unbroken
    environment.filters["literature_references"] = format_literature_references
    environment.filters["ref_numbers"] = ref_numbers
    environment.tests["a_call"] = gene_was_called
    environment.tests["in_summary_panel"] = in_summary_panel
    environment.globals["summary_rows"] = summary_rows
    environment.globals["inline_script"] = inline_script


# The interactive report's scripts travel inside the file. It is downloaded (the
# results page's ZIP) and opened offline, where neither a CDN nor this app's
# /static is reachable: from jsDelivr and d3js.org every opened report also told
# two third parties that someone was reading one, and pgx-report.js, linked as
# /static/js/pgx-report.js, did not load at all outside the app.
_STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
_INLINE_SCRIPTS = {
    "chart.js": "vendor/chartjs/chart.umd.min.js",
    "d3": "vendor/d3/d3.min.js",
    "pgx-report.js": "js/pgx-report.js",
}
# Their licences, which ask for the copyright and permission notice in every copy:
# the downloaded report is one, and the minified files carry only a one-line
# banner.
_INLINE_SCRIPT_LICENCES = {
    "chart.js": "vendor/chartjs/LICENSE.md",
    "d3": "vendor/d3/LICENSE",
}
_SCRIPT_END_TAG = re.compile(r"</(script)", re.IGNORECASE)
_inline_script_cache: Dict[str, str] = {}


def inline_script(name: str) -> str:
    """The source of one of _INLINE_SCRIPTS, safe to place in a <script> element.

    A literal ``</script`` inside the source would end the element early; written
    ``<\\/script`` it means the same in any JavaScript string or regex, the only
    places it can occur. None of the three files contains one today.
    """
    if name not in _inline_script_cache:
        path = os.path.join(_STATIC_DIR, _INLINE_SCRIPTS[name])
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        licence = _INLINE_SCRIPT_LICENCES.get(name)
        if licence:
            with open(os.path.join(_STATIC_DIR, licence), encoding="utf-8") as fh:
                # "*/" would close the comment early.
                text = fh.read().strip().replace("*/", "* /")
            source = f"/*! {_INLINE_SCRIPTS[name]}\n{text}\n*/\n{source}"
        _inline_script_cache[name] = _SCRIPT_END_TAG.sub(r"<\\/\1", source)
    return _inline_script_cache[name]


_SUMMARY_PANEL: Optional[frozenset] = None


def in_summary_panel(gene) -> bool:
    """Whether the Executive Summary covers this gene: PharmCAT's panel, the genes
    its guidelines use (config/genes.json ``sets.pharmcat_all``).

    The summary is one page. From PharmCAT's TSV it only ever held these genes;
    without the TSV it took every gene the run reported, so a BAM with PyPGx's
    whole record ran to 91 rows over four pages. The others are in Genetic
    Results. Without the config, every gene is covered, as before.
    """
    panel = _summary_panel()
    return not panel or str(gene or "").strip().upper() in panel


def _summary_panel() -> frozenset:
    global _SUMMARY_PANEL
    if _SUMMARY_PANEL is None:
        try:
            root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            with open(
                os.path.join(root, "config", "genes.json"), encoding="utf-8"
            ) as fh:
                panel = (json.load(fh).get("sets") or {}).get("pharmcat_all") or []
            _SUMMARY_PANEL = frozenset(str(g).strip().upper() for g in panel)
        except Exception as e:
            logger.warning(f"Executive Summary panel unavailable: {e}")
            _SUMMARY_PANEL = frozenset()
    return _SUMMARY_PANEL


# PharmCAT's own label for a diplotype of its reference alleles. Its TSV prints the
# alleles' names instead, and CFTR's reference allele is named "ivacaftor
# non-responsive CFTR sequence": the summary's longest cell by far, twice over.
_PHARMCAT_REFERENCE_LABEL = re.compile(r"Reference(?:/Reference)?")


def summary_rows(tsv_rows, diplotypes) -> List[Dict[str, Any]]:
    """The Executive Summary's rows: one for each gene of PharmCAT's panel, in
    order, so every report lists the same genes.

    A gene PharmCAT's TSV calls is shown from the TSV, as before. The TSV leaves
    out the genes PharmCAT takes only as outside calls -- HLA-A, HLA-B and
    MT-RNR1 -- so a called one was neither in the table nor among the genes not
    called: the summary dropped it without a word. It is shown from the gene list
    now. A gene neither source calls is a row that says so. Without the panel
    config, the genes the run called, as before.
    """
    tsv = {
        str(row.get("gene") or "").strip().upper(): row
        for row in tsv_rows or []
        if in_summary_panel(row.get("gene"))
    }
    genes = {
        str(row.get("gene") or "").strip().upper(): row
        for row in diplotypes or []
        if in_summary_panel(row.get("gene"))
    }
    panel = sorted(_summary_panel()) or sorted(
        set(tsv)
        | {g for g, row in genes.items() if gene_was_called(row.get("diplotype"))}
    )
    rows = []
    for gene in panel:
        if gene in tsv:
            row = tsv[gene]
            diplotype = (
                row.get("diplotype_display") or row.get("rec_lookup_diplotype") or ""
            )
            # Where PharmCAT labels the call "Reference/Reference" and the TSV
            # spells out the reference allele's name, the summary shows PharmCAT's
            # label, and its notes give the name.
            label = str((genes.get(gene) or {}).get("diplotype") or "")
            reference_name = None
            if (
                _PHARMCAT_REFERENCE_LABEL.fullmatch(label)
                and diplotype
                and diplotype != label
                and not row.get("diplotype_candidates")
            ):
                names = {part.strip() for part in diplotype.split("/")}
                if len(names) == 1:
                    reference_name = names.pop()
                    diplotype = label
            rows.append(
                {
                    "gene": row.get("gene") or gene,
                    "called": True,
                    "diplotype": diplotype,
                    "phenotype": row.get("rec_lookup_phenotype") or "",
                    "activity_score": row.get("rec_lookup_activity_score"),
                    "diplotype_candidates": row.get("diplotype_candidates"),
                    "reference_name": reference_name,
                }
            )
        elif gene in genes and gene_was_called(genes[gene].get("diplotype")):
            row = genes[gene]
            rows.append(
                {
                    "gene": row.get("gene") or gene,
                    "called": True,
                    "diplotype": row.get("diplotype_display")
                    or row.get("diplotype")
                    or "",
                    "phenotype": row.get("phenotype") or "Unknown",
                    "activity_score": row.get("activity_score"),
                    "diplotype_candidates": row.get("diplotype_candidates"),
                    "reference_name": None,
                }
            )
        else:
            rows.append(
                {
                    "gene": (genes.get(gene) or {}).get("gene") or gene,
                    "called": False,
                    "diplotype": "",
                    "phenotype": "",
                    "activity_score": None,
                    "diplotype_candidates": None,
                    "reference_name": None,
                }
            )
    return rows


def gene_was_called(diplotype) -> bool:
    """True when the run actually produced a diplotype for this gene.

    Uncalled genes come back as "Unknown" or "Unknown/Unknown" depending on which
    tool reported them, so this matches the prefix rather than either literal.
    Registered as a Jinja *test* so the templates can split the gene list with
    selectattr/rejectattr -- the environment has no `do` extension, and computing
    the split in Python would mean threading two more keys through the three
    places that build a template context.
    """
    text = str(diplotype or "").strip().lower()
    return bool(text) and not text.startswith("unknown")


_VARIANT_ONLY_INPUT_TYPES = frozenset({"vcf", "bcf"})


# An alignment run's per-position statuses for PharmCAT's positions: the pypgx
# wrapper's genotype_pharmcat_positions writes them, main.nf's PyPGxBam2Vcf publishes
# them beside PharmCAT's VCF in the report directory.
POSITION_STATUS_FILE = os.path.join("pharmcat", "pharmcat_position_status.tsv")

# Why a position PharmCAT lists as missing was not genotyped for it, from the
# alignment's per-position statuses: {bucket: (label, meaning)}, in the order the
# alert gives them. A status the file holds but the alert does not name (a 0/0
# record PharmCAT discarded would be "reference") is "other"; a position missing
# from the file is "unlisted", so each line's reasons add up to its count.
_MISSING_REASONS = {
    "no_reads": (
        "no reads",
        "no read of mapping quality 1 or more reached the position",
    ),
    "uncertain": (
        "uncertain",
        "reads did, but did not settle the genotype (see Methodology)",
    ),
    "variant": (
        "variant",
        "the reads showed a non-reference allele that PharmCAT did not take at its "
        "position (a call starting at another position, or a record it discarded)",
    ),
    "other": (
        "other",
        "another status, given per position in pharmcat_position_status.tsv",
    ),
    "unlisted": (
        "not in this run's position list",
        "the alignment was genotyped from a different copy of PharmCAT's positions "
        "than PharmCAT used (restage reference/pharmcat/pharmcat_positions.vcf after "
        "a PharmCAT upgrade)",
    ),
}
_NAMED_STATUSES = frozenset({"no_reads", "uncertain", "variant"})


def read_position_statuses(report_dir: str) -> Dict[str, str]:
    """{"chrom:pos": status} from the run's POSITION_STATUS_FILE, {} without one.

    Only an alignment run has the file. A missing or unreadable one costs the
    partial-coverage alert its reasons, never the alert.
    """
    path = os.path.join(report_dir or "", POSITION_STATUS_FILE)
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            return {
                f"{row['chrom']}:{row['position']}": row["status"]
                for row in csv.DictReader(fh, delimiter="\t")
                if row.get("chrom") and row.get("position") and row.get("status")
            }
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, KeyError, csv.Error) as exc:
        logger.warning(f"Could not read PharmCAT's position statuses at {path}: {exc}")
        return {}


def partial_coverage_alert(
    genes,
    input_type: Optional[str] = None,
    statuses: Optional[Mapping[str, str]] = None,
    max_listed: int = 8,
) -> Optional[str]:
    """One alert, a line per gene PharmCAT matched without some of its positions.

    PharmCAT reads a position it has no genotype for as missing (its report lists it
    with a null call, see pharmcat_parser.position_coverage) and matches the gene on
    the rest: an allele defined only at missing positions is not assessed
    (``uncalledHaplotypes``), and one defined at others too is matched on those
    alone. Before aligned inputs were genotyped at every PharmCAT position this was
    always empty for them, because --missing-to-ref filled the gaps with reference;
    now a panel that misses 5 of NUDT15's 20 positions reports *1/*1 with *4, *15,
    *20 and *21 unassessed, and the reader is owed that, and how much was missing.

    Each line: how many of the gene's positions were missing, why when the run kept
    per-position statuses (an alignment, read_position_statuses), and the alleles
    this leaves unassessed or partly assessed. A gene with every position read gets
    no line. A gene with no call gets its count alone, since that may be why; for a
    plain VCF those are left to "Genes examined without a call", which explains a
    variants-only file there, and would otherwise be most of PharmCAT's panel.

    For a plain VCF the missing positions are not "unread", they are simply not
    listed: a variants-only file says nothing about the rest, and the wording says so.
    """
    kind = str(input_type or "").strip().lower()
    variant_only = kind in _VARIANT_ONLY_INPUT_TYPES
    if variant_only:
        verb, intro = "not in the VCF", "The VCF has no genotype at"
    elif kind in ALIGNMENT_INPUT_TYPES:
        verb, intro = "not read", "The alignment gave no genotype at"
    else:
        verb, intro = "without a genotype", "The data gave no genotype at"
    # Read statuses describe an alignment's reads; a VCF's missing positions are
    # simply not listed in it.
    statuses = {} if variant_only else (statuses or {})

    def listed(items):
        shown = ", ".join(html.escape(i) for i in items[:max_listed])
        if len(items) > max_listed:
            shown += f" and {len(items) - max_listed} more"
        return shown

    lines, used = [], set()
    for gene in sorted(genes or [], key=lambda g: str(g.get("gene") or "")):
        missing = [
            m for m in gene.get("missing_positions") or [] if isinstance(m, dict)
        ]
        uncalled = [str(h) for h in (gene.get("uncalled_haplotypes") or []) if h]
        called = gene_was_called(gene.get("diplotype"))
        if not missing and not (called and uncalled):
            continue
        if not called and variant_only:
            continue
        parts = []
        if missing:
            total = gene.get("n_positions")
            known = isinstance(total, int) and total >= len(missing)
            noun = (
                "position" if (total if known else len(missing)) == 1 else "positions"
            )
            count = f"{len(missing)} of {total}" if known else str(len(missing))
            text = f"{count} {noun} {verb}"
            if statuses:
                tally: Dict[str, int] = {}
                for m in missing:
                    status = statuses.get(str(m.get("position")))
                    if status is None:
                        bucket = "unlisted"
                    else:
                        bucket = status if status in _NAMED_STATUSES else "other"
                    tally[bucket] = tally.get(bucket, 0) + 1
                reasons = [
                    f"{label} {tally[key]}"
                    for key, (label, _) in _MISSING_REASONS.items()
                    if key in tally
                ]
                text += f" ({', '.join(reasons)})"
                used.update(tally)
            parts.append(text)
        if not called:
            parts.append("no call")
        else:
            if uncalled:
                parts.append(f"alleles not assessable: {listed(uncalled)}")
            partly = list(
                dict.fromkeys(
                    str(a)
                    for m in missing
                    for a in m.get("alleles") or []
                    if a and str(a) not in uncalled
                )
            )
            if partly:
                parts.append(
                    f"assessed on their other positions only: {listed(partly)}"
                )
        lines.append(
            f"<li><strong>{html.escape(str(gene.get('gene')))}</strong>: "
            + "; ".join(parts)
            + "</li>"
        )
    if not lines:
        return None
    note = (
        f"<p>⚠️ Positions {verb}: {intro} some of the positions that define these "
        "genes' alleles, and PharmCAT matched each gene on the rest. An allele defined "
        "only there could not be assessed; one defined at other positions too was "
        "assessed on those alone."
    )
    # The reasons this alert gives, explained once; constants, so nothing to escape.
    legend = [
        f"<em>{label}</em>: {meaning}"
        for key, (label, meaning) in _MISSING_REASONS.items()
        if key in used
    ]
    if legend:
        note += " In brackets, " + "; ".join(legend) + "."
    return note + "</p><ul>" + "".join(lines) + "</ul>"


# PharmCAT's preprocessor drops a record whose REF does not match PharmCAT's own
# before the matcher sees it, and says so only in the pipeline log (measured with
# PharmCAT 3.4.0):
#   * WARNING: "chr1:97450065 REF=TGG ALT=T" does not match PharmCAT expectation of
#     REF at "chr1:97450065 REF=TG ALT=T"
# The position then reads as missing -- or, when the run's assume-reference setting is
# on (plain VCF/BCF only), as reference: that record became `TG|TG`. Either way the data
# had something there that the call did not use.
_PREPROCESSOR_REF_MISMATCH = re.compile(
    r'"(\S+):(\d+) REF=(\S+) ALT=\S*" does not match PharmCAT expectation of REF at '
    r'"\S+ REF=(\S+) ALT=\S*"'
)


def preprocessor_ref_mismatches(log_text: str) -> List[Tuple[str, int, str, str]]:
    """(chrom, pos, REF in the data, REF PharmCAT expects) for each dropped record."""
    seen, found = set(), []
    for m in _PREPROCESSOR_REF_MISMATCH.finditer(log_text or ""):
        key = (m.group(1), int(m.group(2)), m.group(3), m.group(4))
        if key not in seen:
            seen.add(key)
            found.append(key)
    return found


def add_preprocessor_drops(genes, report_dir: str) -> None:
    """Add the preprocessor's dropped records to each report gene's unread_variants.

    Reads the run's own artifacts in report_dir: the PharmCAT pipeline log for the
    drops, and PharmCAT's report JSON (``*_pgx_pharmcat.json``) for which gene each
    position belongs to and what PharmCAT finally used there. A position the matcher
    already flagged is left as the matcher described it. Missing files mean nothing
    is added; the report must not fail over a supplementary alert.
    """
    try:
        logs = sorted(glob.glob(os.path.join(report_dir, "*_pharmcat_pipeline.log")))
        reports = sorted(glob.glob(os.path.join(report_dir, "*_pgx_pharmcat.json")))
        if not logs or not reports:
            return
        with open(logs[-1], encoding="utf-8", errors="replace") as fh:
            drops = preprocessor_ref_mismatches(fh.read())
        if not drops:
            return
        with open(reports[-1], encoding="utf-8") as fh:
            pharmcat_genes = (json.load(fh) or {}).get("genes") or {}
    except (OSError, ValueError) as exc:
        logger.warning(f"Could not read PharmCAT's preprocessor drops: {exc}")
        return

    by_symbol = {str(g.get("gene")): g for g in genes or [] if isinstance(g, dict)}
    for chrom, pos, data_ref, expected_ref in drops:
        for symbol, gene_json in pharmcat_genes.items():
            variant = next(
                (
                    v
                    for v in (gene_json or {}).get("variants") or []
                    if isinstance(v, dict)
                    and v.get("chromosome") == chrom
                    and v.get("position") == pos
                ),
                None,
            )
            gene = by_symbol.get(str(symbol))
            if variant is None or gene is None:
                continue
            unread = gene.setdefault("unread_variants", [])
            label = f"{chrom}:{pos}"
            if any(u.get("position") == label for u in unread):
                continue
            unread.append(
                {
                    "position": label,
                    "rsid": variant.get("dbSnpId"),
                    "call": variant.get("call"),
                    "reason": (
                        "assumed_reference" if variant.get("call") else "discarded"
                    ),
                    "detail": f"REF {data_ref} where PharmCAT expects {expected_ref}",
                }
            )


def unread_variants_alert(genes, max_listed: int = 8) -> Optional[str]:
    """One alert naming the positions a CALLED gene's call did not use as found.

    PharmCAT matches an allele it does not define as reference, and reads a position
    the data writes differently from its definition as missing, and says so only in its
    own report (see pharmcat_parser.unread_variants). Either way the call printed here
    is not what the data at that position showed, and the reader is owed that.
    """
    undocumented, discarded, assumed = [], [], []
    for gene in sorted(genes or [], key=lambda g: str(g.get("gene") or "")):
        if not gene_was_called(gene.get("diplotype")):
            continue
        for v in gene.get("unread_variants") or []:
            label = f"{gene.get('gene')} {v.get('rsid') or v.get('position')}"
            if v.get("reason") == "undocumented":
                call = v.get("call")
                undocumented.append(f"{label} ({call})" if call else label)
            elif v.get("reason") in ("discarded", "assumed_reference"):
                detail = v.get("detail")
                item = f"{label} ({detail})" if detail else label
                (assumed if v["reason"] == "assumed_reference" else discarded).append(
                    item
                )

    def listed(items):
        shown = "; ".join(html.escape(i) for i in items[:max_listed])
        if len(items) > max_listed:
            shown += f" and {len(items) - max_listed} more"
        return shown

    parts = []
    if undocumented:
        parts.append(
            f"Allele PharmCAT does not define, matched as reference: {listed(undocumented)}"
        )
    if discarded:
        parts.append(
            f"Record PharmCAT could not read, taken as missing: {listed(discarded)}"
        )
    if assumed:
        parts.append(
            "Record PharmCAT could not read, taken as reference by the "
            f"assume-reference setting: {listed(assumed)}"
        )
    if not parts:
        return None
    return (
        "<p>⚠️ Positions PharmCAT did not use as found. " + ". ".join(parts) + ".</p>"
    )


register_report_template_helpers(env)


# Custom exceptions
class ReportGenerationError(Exception):
    """Exception raised when report generation fails."""

    pass


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
        document, _ = render_pdf_document(html_content)
        document.write_pdf(target=output_path, zoom=1.0)
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


def render_pdf_document(html_content: str):
    """Lay the report out as its PDF prints, with each section that must hold to
    a page fitted to it (app/reports/pdf_fit.py).

    Returns the WeasyPrint Document and the tightening step used per section.
    """
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
        /* The workflow diagram's page and size are the template's print rules and
           the image's own style (workflow_pdf_style). The rules here capped it at
           280px with !important, which, in a user stylesheet, outranked both. */
        .section { 
            page-break-inside: avoid; 
            break-inside: avoid; 
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

    html = HTML(string=mark_headings(html_content))
    font_config = FontConfiguration() if FontConfiguration else None

    # The stylesheet of the last layout: the one fit_sections returns.
    last_css = [""]

    def render(fit_css: str):
        last_css[0] = fit_css
        sheets = list(stylesheets)
        if fit_css:
            sheets.append(
                CSS(string=fit_css, font_config=font_config)
                if font_config
                else CSS(string=fit_css)
            )
        if font_config:
            return html.render(stylesheets=sheets, font_config=font_config)
        return html.render(stylesheets=sheets)

    document, levels = fit_sections(render)
    # The summary fitted, its leftover height shared out between its rows, so it
    # fills its page: one more layout.
    document = fill_summary_page(render, document, last_css[0])
    return document, levels


def unverified_pharmcat_version_alert(pharmcat_results: Any) -> Optional[str]:
    """Alert text when the run's PharmCAT version is one nobody has verified.

    265's version gate warns and never blocks: hard-failing on an upstream point
    release would turn a harmless bump into a clinical outage after the expensive
    preprocessing already succeeded, and the structure checks are what carry the
    teeth. But that warning reached the log only, so the operator knew and the
    clinician reading the report did not -- and "these calls came out of a
    PharmCAT release this pipeline has never been checked against" is exactly the
    kind of qualification the Alerts and Warnings page exists for.

    Returns ``None`` when there is nothing to say. In particular, a payload with
    **no** ``pharmcatVersion`` key at all is silent rather than alarming: that is
    the shape of the TSV fallback and of already-normalised data, neither of
    which is making a claim about a PharmCAT release, and alerting there would
    put the banner on every TSV-rescued run. Only a version that is present and
    unverified is reported, which is the case the gate's warning is about.

    HTML, because the template renders each warning with ``|safe`` and the
    existing warnings are HTML fragments. Every interpolated value is escaped.
    """
    payload = pharmcat_results
    if isinstance(payload, Mapping) and isinstance(payload.get("data"), Mapping):
        payload = payload["data"]
    if not isinstance(payload, Mapping) or "pharmcatVersion" not in payload:
        return None

    raw = payload["pharmcatVersion"]
    parsed = parse_pharmcat_version(raw) if isinstance(raw, str) else None
    if parsed is not None and (parsed[0], parsed[1]) in SUPPORTED_VERSION_SERIES:
        return None

    known = html.escape(
        ", ".join(
            f"{major}.{minor}" for major, minor in sorted(SUPPORTED_VERSION_SERIES)
        )
    )
    # `pharmcatVersion: null` and `pharmcatVersion: ""` are "the field is there
    # and says nothing", which is a different sentence from "it says 9.9.1".
    # Interpolating them naively printed the literal "None" -- str(None) is
    # truthy, so an `or` fallback never fired -- and told the reader the release
    # was called None.
    shown = "" if raw is None else str(raw).strip()
    if shown:
        claim = (
            f"These results were produced by PharmCAT "
            f"<strong>{html.escape(shown)}</strong>, a release this pipeline has "
            f"not been verified against (verified series: {known})."
        )
    else:
        claim = (
            f"The PharmCAT output does not say which release produced these "
            f"results, so they cannot be attributed to a version this pipeline "
            f"has been verified against (verified series: {known})."
        )
    return (
        "<p><strong>Unverified PharmCAT version</strong></p>"
        f"<p>{claim} The structural checks on the PharmCAT output passed, so the "
        f"calls below are readable and are reported as PharmCAT made them; but "
        f"they have not been confirmed to match this pipeline's expectations for "
        f"a known release, and should be treated as unverified until they are.</p>"
    )


def pharmcat_tsv_rescue_alert(schema_gate_reason: Optional[str]) -> str:
    """Alert text for a report the PharmCAT TSV rescued from a rejected JSON.

    When 265's structure gate refuses ``report.json``, the report lane's one
    honest way out is PharmCAT's tab-delimited output for the same run -- a
    separate file read by a separate parser
    (``app/reports/pharmcat_tsv_parser.py``). That rescue used to leave a
    ``logger.warning`` and nothing else, so the clinician holding the report had
    no way to know it had been built from the fallback artifact rather than from
    PharmCAT's structured output. Same problem the unverified-version banner
    solved, same channel: ``workflow_warnings``, rendered by both templates in
    "Alerts and Warnings".

    The third paragraph is not padding. ``probe_matcher_metadata`` globs
    ``*_pgx_pharmcat.json`` first, so the genome build and Named Allele Matcher
    version printed on the report are still read out of the *rejected* file. A
    banner claiming the rejected file contributed nothing would be false, and
    falsely reassuring about the two fields a reader is most likely to check.

    HTML, because the templates render each warning with ``|safe`` and the
    existing warnings are HTML fragments. ``schema_gate_reason`` is built from
    key names lifted out of the payload, so it is escaped.
    """
    reason = "" if schema_gate_reason is None else str(schema_gate_reason).strip()
    if reason:
        refusal = (
            "PharmCAT's structured output (<strong>report.json</strong>) failed this "
            "pipeline's structure check and was not used. The check reported: "
            f"<em>{html.escape(reason)}</em>"
        )
    else:
        refusal = (
            "PharmCAT's structured output (<strong>report.json</strong>) failed this "
            "pipeline's structure check and was not used; the check recorded no "
            "further detail"
        )
    return (
        "<p><strong>Report built from PharmCAT's TSV output</strong></p>"
        f"<p>{refusal}. The genotypes and drug recommendations below were read "
        "instead from PharmCAT's tab-delimited (TSV) output for the same run -- "
        "a separate file, parsed by separate code -- so the results below are "
        "not taken from the file that was refused.</p>"
        "<p>The run provenance shown elsewhere in this report -- genome build "
        "and Named Allele Matcher version -- is the exception: those fields are "
        "still read from the refused file, which is the only artifact that "
        "carries them. The structure check's verdict does not cover them, so it "
        "neither confirms nor condemns them; treat them as unverified. The TSV "
        "also carries less detail than the structured output, so this report may "
        "be less complete than a report from a run that passed the check.</p>"
    )


def get_disclaimer() -> str:
    """
    Return the legal disclaimer for pharmacogenomic reports.
    """
    return """
    DISCLAIMER: This pharmacogenomic report is for informational purposes only. ZaroPGx is an independently 
    developed hobby software. It is not intended for use as a substitute for professional medical advice, 
    diagnosis, or treatment. The content herein is based on guidelines from the Clinical Pharmacogenetics 
    Implementation Consortium (CPIC), ClinPGx (formerly the Pharmacogenomics Knowledgebase, PharmGKB), the U.S. Food and Drug 
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


# The Export tab's files: each job's outputs that another program can take in. Listed
# by what is on disk once every other output is written, as links relative to the
# interactive report, which sits in the same directory -- so they work when the job's
# directory is opened from the downloaded ZIP as well as through the app.
_EXPORT_FILES = (
    ("{report_id}_pgx_fhir_report.json", "FHIR R4 bundle", "JSON"),
    ("{report_id}_pgx_fhir_report.xml", "FHIR R4 bundle", "XML"),
    ("{report_id}_pgx_pharmcat.json", "PharmCAT report", "JSON"),
    ("{report_id}_pgx_pharmcat.tsv", "PharmCAT report", "TSV"),
    ("{report_id}_pgx_pharmcat.html", "PharmCAT report", "HTML"),
    ("{report_id}_pgx_report.pdf", "This report", "PDF"),
)


def export_downloads(report_dir: str, report_id: str) -> List[Dict[str, str]]:
    """The Export tab's entries: the files in _EXPORT_FILES that exist in report_dir."""
    downloads = []
    for pattern, label, fmt in _EXPORT_FILES:
        filename = pattern.format(report_id=report_id)
        if os.path.isfile(os.path.join(report_dir, filename)):
            downloads.append({"label": label, "format": fmt, "href": filename})
    return downloads


def create_interactive_html_report(
    patient_id: str,
    report_id: str,
    diplotypes: List[Dict[str, Any]],
    recommendations: List[Dict[str, Any]],
    output_path: str,
    workflow: Dict[str, Any] | None = None,
    credits: Optional[Dict[str, Any]] = None,
    sample_identifier: str | None = None,
    workflow_warnings: List[str] | None = None,
    pharmcat_assume_ref_methodology: str | None = None,
    liftover_provenance: str | None = None,
    gvcf_provenance: str | None = None,
    missing_positions_not_assumed: bool = False,
    downloads: Optional[List[Dict[str, str]]] = None,
    workflow_diagram: Optional[Dict[str, Any]] = None,
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

        # The run's diagram, from generate_report's job_workflow_diagram: inline, so
        # the report needs neither the app nor a network to show it.
        diagram = workflow_diagram or job_workflow_diagram([], None)
        workflow_png_url = ""
        workflow_kroki_svg_inline = ""
        workflow_png_data_uri = diagram["png_data_uri"]
        workflow_html_fallback = (
            ""
            if (workflow_kroki_svg_inline or workflow_png_data_uri)
            else diagram["html"]
        )
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

        # 159: run-derived provenance. `report_dir` is os.path.dirname(output_path)
        # -- the same directory the other two lanes probe -- so the interactive
        # report resolves the identical three facts from the identical file. Without
        # this the print report carried provenance and the interactive one silently
        # did not, for the same run.
        matcher_meta = probe_matcher_metadata(report_dir, str(report_id))

        # Task 12: the mtDNA section's context, resolved the same way as the
        # provenance facts above -- from a file this same report_dir already
        # holds, not from a value threaded in through the caller.
        mtdna_context = mtdna_report_context(report_dir, diplotypes)

        # Prepare the report data
        report_data = {
            "patient_id": patient_id,
            "report_id": report_id,
            "sample_identifier": sample_identifier,
            "report_date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "diplotypes": diplotypes,
            "recommendations": template_recommendations,
            "organized_recommendations": json.dumps(
                organize_gene_drug_recommendations(template_recommendations)
            ),
            "mtdna": mtdna_context,
            "disclaimer": get_disclaimer(),
            "workflow_png_url": workflow_png_url,
            "workflow_png_data_uri": workflow_png_data_uri,
            "workflow_html_fallback": workflow_html_fallback,
            "workflow_kroki_svg": workflow_kroki_svg_inline,  # Added for interactive report
            # Without the job's credits, everything that could have run.
            "credits": credits or build_report_credits(),
            **report_branding_context(),
            "header_text": header_text,
            # Add workflow warnings/alerts for report display
            "workflow_warnings": workflow_warnings or [],
            "pharmcat_assume_ref_methodology": pharmcat_assume_ref_methodology,
            "gvcf_provenance": gvcf_provenance,
            "liftover_provenance": liftover_provenance,
            "missing_positions_not_assumed": missing_positions_not_assumed,
            # 159: run-derived provenance (each rendered only if resolved)
            "genome_build": matcher_meta["genome_build"],
            "named_allele_matcher_version": matcher_meta[
                "named_allele_matcher_version"
            ],
            "pharmcat_data_version": matcher_meta["data_version"],
        }

        report_data["display_sample_id"] = sample_identifier
        report_data["downloads"] = downloads or []

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
                elif defines_no_phenotype(row) and (
                    str(row.get("phenotype") or "").strip().lower()
                    in _ABSENT_PHENOTYPES | {""}
                ):
                    # A call the calling tool assigns no phenotype to: PharmCAT
                    # for VKORC1, IFNL3 and CYP4F2, PyPGx for most genes outside
                    # PharmCAT's. PharmCAT's own report prints "N/A" there. This
                    # one printed a blank in the PDF, "Unknown" in the interactive
                    # report, and "Unknown" again for every PyPGx-only gene.
                    row["phenotype"] = NO_PHENOTYPE_STANDARD
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
                "recommendation_genes": drug_data.get("recommendation_genes") or [gene],
                "recommendation": drug_data.get(
                    "recommendation", "See report for details"
                ),
                "classification": drug_data.get("classification", "Unknown"),
                "guideline": drug_data.get("guideline", ""),
                "guideline_source": drug_data.get("guideline_source", ""),
                "guideline_url": drug_data.get("guideline_url", ""),
                "literature_references": drug_data.get("literature_references", []),
                "prescribing_changes": drug_data.get("prescribing_changes", []),
                "change_genes": drug_data.get("change_genes", []),
                "conditions": drug_data.get("conditions", []),
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
                # Every gene the recommendation rests on, in PharmCAT's order.
                # "gene" is each lane's own pick: the JSON lane's is the first, the
                # database lane's the parser's (isoflurane: RYR1, or CACNA1S).
                "recommendation_genes": rec.get("recommendation_genes") or [gene],
                "recommendation": recommendation_text,
                "classification": classification,
                "evidence_rank": tier.rank,
                "evidence_class": tier.css_class,
                "guideline": guideline,
                "guideline_source": guideline_source,
                "guideline_url": rec.get("guideline_url", ""),
                "literature_references": rec.get("literature_references", []),
                "prescribing_changes": rec.get("prescribing_changes", []),
                "change_genes": rec.get("change_genes", []),
                "conditions": rec.get("conditions", []),
            }

            # Add drug-level metadata if available
            if drug_data.get("pharmgkb_id"):
                mapped_rec["pharmgkb_id"] = drug_data["pharmgkb_id"]
            if drug_data.get("called_by"):
                mapped_rec["called_by"] = drug_data["called_by"]

            mapped_recommendations.append(mapped_rec)

    label_conditional_recommendations(mapped_recommendations)
    return mapped_recommendations


def _shown(diplotypes: List[str], limit: int = 3) -> str:
    if len(diplotypes) <= limit:
        return ", ".join(diplotypes)
    return f"{', '.join(diplotypes[:limit])} or {len(diplotypes) - limit} more"


def label_conditional_recommendations(recommendations: List[Dict[str, Any]]) -> None:
    """Set "applies_if" where a drug's guidance depends on which candidate is true.

    An unphased call can leave several diplotypes, and PharmCAT then gives one
    recommendation per phenotype they could have: CPIC simvastatin for SLCO1B1
    "Decreased Function" (*1/*15, *1/*45, *1/*47) and another for "Poor
    Function". Listed together they read as two pieces of advice for one patient.

    A gene is in question for a drug's guideline when its annotations were matched
    on more than one set of candidates. Each block the report prints -- one
    guideline's text, as the template groups it -- is labelled with the candidates
    of those genes it was matched on, unless it covers all of them (FDA's
    simvastatin text is the same for both: it holds whatever the truth). Genes
    matched one way only are left out: DPWG writes one annotation per gene for
    azathioprine (TPMT, NUDT15), and both hold at once.
    """

    def key(condition):
        gene, phenotype, diplotypes = condition
        return (gene, phenotype, tuple(diplotypes))

    options: Dict[Tuple[str, str], Dict[str, set]] = {}
    by_block: Dict[Tuple[str, str, str, str], List[Dict[str, Any]]] = {}
    for rec in recommendations:
        conditions = [key(c) for c in rec.get("conditions") or []]
        if not conditions:
            continue
        guideline = (rec.get("drug"), rec.get("guideline_source") or "")
        for gene, phenotype, diplotypes in conditions:
            options.setdefault(guideline, {}).setdefault(gene, set()).add(
                (phenotype, diplotypes)
            )
        block = guideline + (
            rec.get("recommendation") or "",
            rec.get("classification") or "",
        )
        by_block.setdefault(block, []).append(rec)

    def in_question(rec, guideline):
        return tuple(
            key(c)
            for c in rec.get("conditions") or []
            if len(options[guideline][c[0]]) > 1
        )

    for (drug, source, _, _), recs in by_block.items():
        guideline = (drug, source)
        mine = {in_question(r, guideline) for r in recs} - {()}
        if not mine:
            continue
        genes = {tuple(c[0] for c in conditions) for conditions in mine}
        everything = {
            in_question(r, guideline)
            for r in recommendations
            if (r.get("drug"), r.get("guideline_source") or "") == guideline
            and r.get("conditions")
        }
        everything = {c for c in everything if tuple(g for g, _, _ in c) in genes}
        if mine == everything:
            continue
        for rec in recs:
            rec["applies_if"] = _applies_if(sorted(mine))


def _applies_if(
    condition_sets: List[Tuple[Tuple[str, str, Tuple[str, ...]], ...]],
) -> str:
    """The "Applies if" text for the condition sets one block of guidance was
    matched on: "SLCO1B1 Decreased Function (*1/*15, *1/*45)". Per gene when exact (any mix of the
    genes' options matches); otherwise each set in turn, so that SLCO1B1 normal
    with CYP2C9 intermediate, or SLCO1B1 decreased with CYP2C9 normal, does not
    read as also covering SLCO1B1 normal with CYP2C9 normal."""

    def part(gene, phenotypes, diplotypes):
        return f"{gene} {' or '.join(phenotypes) or 'call'} ({_shown(diplotypes)})"

    options: Dict[str, List[Tuple[str, Tuple[str, ...]]]] = {}
    for conditions in condition_sets:
        for gene, phenotype, diplotypes in conditions:
            if (phenotype, diplotypes) not in options.setdefault(gene, []):
                options[gene].append((phenotype, diplotypes))
    combinations = 1
    for choices in options.values():
        combinations *= len(choices)
    if combinations == len(condition_sets):
        parts = []
        for gene, choices in options.items():
            phenotypes = [p for p, _ in choices if p]
            labels = [d for _, ds in choices for d in ds]
            parts.append(part(gene, list(dict.fromkeys(phenotypes)), labels))
        return " and ".join(parts)
    return "; or ".join(
        " and ".join(part(g, [p] if p else [], list(ds)) for g, p, ds in conditions)
        for conditions in condition_sets
    )


# native_report_paths' keys (generate_report's report_paths keys) -> the keys its
# template data and the web page read the same links under.
NATIVE_REPORT_URL_KEYS = {
    "pharmcat_html_path": "pharmcat_html_report_url",
    "pharmcat_json_path": "pharmcat_json_report_url",
    "pharmcat_tsv_path": "pharmcat_tsv_report_url",
    "mtdna_report_path": "mtdna_report_url",
    "mtdna_haplogroups_path": "mtdna_haplogroups_url",
    "mtdna_vcf_path": "mtdna_vcf_url",
}


def native_report_paths(
    report_dir: str, report_id: str, reports_url_prefix: str
) -> Dict[str, str]:
    """Served paths of the reports the pipeline publishes itself, by report_paths key.

    PharmCAT's own HTML, JSON and TSV, each as INCLUDE_PHARMCAT_* allows, and
    mtDNA-Server 2's files: all already in report_dir when the report step runs.
    Only a file that exists gets a key, so the page never offers a dead link, and
    an mtDNA file only when it holds a result (mtdna_report_paths).

    generate_report adds these to the reports it builds. A job whose custom report
    was turned off gets these alone (upload_router), which is what the page's
    Report toggle promises: "only PharmCAT's native report is provided".
    """
    paths: Dict[str, str] = {}

    # PharmCAT HTML report
    if REPORT_CONFIG["show_pharmcat_html_report"]:
        logger.info(
            "Processing PharmCAT HTML report (enabled via INCLUDE_PHARMCAT_HTML)"
        )
        pharmcat_html_filename = f"{report_id}_pgx_pharmcat.html"
        if os.path.exists(os.path.join(report_dir, pharmcat_html_filename)):
            paths["pharmcat_html_path"] = (
                f"{reports_url_prefix}/{pharmcat_html_filename}"
            )
            logger.info(
                f"PharmCAT HTML report URL added: {paths['pharmcat_html_path']}"
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
            paths["pharmcat_json_path"] = (
                f"{reports_url_prefix}/{pharmcat_json_filename}"
            )
            logger.info(
                f"PharmCAT JSON report URL added: {paths['pharmcat_json_path']}"
            )
        else:
            logger.warning("PharmCAT JSON report not found in report directory")
    else:
        logger.debug(
            "PharmCAT JSON report processing disabled via INCLUDE_PHARMCAT_JSON environment variable"
        )

    # PharmCAT TSV report
    if REPORT_CONFIG["show_pharmcat_tsv_report"]:
        logger.info("Processing PharmCAT TSV report (enabled via INCLUDE_PHARMCAT_TSV)")
        pharmcat_tsv_filename = f"{report_id}_pgx_pharmcat.tsv"
        pharmcat_tsv_path = os.path.join(report_dir, pharmcat_tsv_filename)
        pharmcat_tsv_file = os.path.join(report_dir, f"{report_id}.report.tsv")

        if os.path.exists(pharmcat_tsv_file):
            if not os.path.exists(pharmcat_tsv_path):
                shutil.copy(pharmcat_tsv_file, pharmcat_tsv_path)
                logger.info(f"PharmCAT TSV report copied to: {pharmcat_tsv_path}")

        if os.path.exists(pharmcat_tsv_path):
            paths["pharmcat_tsv_path"] = f"{reports_url_prefix}/{pharmcat_tsv_filename}"
            logger.info(f"PharmCAT TSV report URL added: {paths['pharmcat_tsv_path']}")
        else:
            logger.warning("PharmCAT TSV report not found in report directory")
    else:
        logger.debug(
            "PharmCAT TSV report processing disabled via INCLUDE_PHARMCAT_TSV environment variable"
        )

    paths.update(mtdna_report_paths(report_dir, reports_url_prefix))
    return paths


def _mtdna_result_calls_something(path: str) -> bool:
    """True when mtdna_result.json names a haplogroup, an MT-RNR1 result or variants."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            result = json.load(fh)
    except (OSError, ValueError) as exc:
        logger.warning(f"Could not read {path}: {exc}")
        return False
    return isinstance(result, dict) and any(
        result.get(key) for key in ("haplogroup", "mt_rnr1", "variants")
    )


def _vcf_has_records(path: str) -> bool:
    """True when the bgzipped VCF at `path` holds a record, not only its header."""
    try:
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
            return any(line.strip() and not line.startswith("#") for line in fh)
    except (OSError, EOFError, zlib.error) as exc:
        logger.warning(f"Could not read {path}: {exc}")
        return False


def mtdna_report_paths(report_dir: str, reports_url_prefix: str) -> Dict[str, str]:
    """Served paths of mtDNA-Server 2's files that hold a result, by report_paths key.

    MtdnaCall (pipelines/pgx/main.nf) publishDir-copies these into report_dir
    under fixed names, with no report_id prefix, unlike PharmCAT's files.

    A file that exists is not enough. An upload with no mitochondrial data still
    leaves mtdna_result.json, a no-call with no haplogroup (mt_rnr1_no_call_reason
    "no_chrm_data" for a VCF, "no_mito_contig" or no reads for an alignment), and a
    VCF upload leaves a chrM VCF that is only a header. Linked as "View Haplogroup
    Report" and "Download chrM VCF" (index.html), they offered a haplogroup report
    without a haplogroup and a VCF without a variant (PharmCAT's example VCF,
    2026-10-20). So:
      - mtdna_result.json is linked when it names a haplogroup, an MT-RNR1 result
        or variants;
      - mtdna_variants.vcf.gz when it holds a record;
      - mtdna_report.html when it is not empty. It exists only for an alignment:
        upstream's report needs coverage and contamination metrics a VCF cannot
        give (mtdna-server-2's report_unavailable_reason).
    The report's mtDNA section says why there is no result (mtdna_report_context).

    native_report_paths adds these; GET /upload/reports/job/{job_id}'s fallback for
    a job without stored reports uses them too.
    """
    checks = (
        (
            "mtdna_report_path",
            "mtdna_report.html",
            "mtDNA-Server 2 report",
            lambda path: os.path.getsize(path) > 0,
        ),
        (
            "mtdna_haplogroups_path",
            "mtdna_result.json",
            "mtDNA haplogroup result",
            _mtdna_result_calls_something,
        ),
        ("mtdna_vcf_path", "mtdna_variants.vcf.gz", "mtDNA chrM VCF", _vcf_has_records),
    )
    paths: Dict[str, str] = {}
    for key, name, what, has_content in checks:
        path = os.path.join(report_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            linked = has_content(path)
        except Exception as exc:
            # Deciding a link must not fail the job (the final stage marks it
            # failed on any error here): a file that cannot be read is not linked.
            logger.warning(f"{what} not linked: could not read {name}: {exc}")
            continue
        if linked:
            paths[key] = f"{reports_url_prefix}/{name}"
            logger.info(f"{what} URL added: {paths[key]}")
        else:
            logger.info(f"{what} not linked: {name} has no result in it")
    return paths


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
    job_metadata: Dict[str, Any] = {}
    workflow_warnings = []  # Initialize warnings list
    pharmcat_assume_ref_methodology = None
    liftover_provenance = None
    # What PharmCAT ran with for positions absent from the file (the workflow
    # diagram says so); None when the job's settings could not be read.
    run_absent_to_ref = None
    gvcf_provenance = None
    # Why a variant-only file left genes uncalled, when the job's flags say so.
    missing_positions_not_assumed = False
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
                from app.api.db import Job, JobStep
                from app.utils.gvcf_provenance import gvcf_provenance_paragraph
                from app.utils.liftover_provenance import (
                    liftover_provenance_sentence,
                )
                from app.utils.pharmcat_assume_ref import (
                    assume_ref_alert,
                    explicit_calls_paragraph,
                    methodology_assume_ref_paragraph,
                    pharmcat_flags_for_input,
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
                job_metadata = meta
                workflow_config = (
                    meta.get("workflow", {})
                    if isinstance(meta.get("workflow"), dict)
                    else {}
                )
                # Drop the pre-flight advisories. A warning tagged
                # class='preflight' by file_processor is guidance for someone
                # still choosing a file -- "consider uploading a BAM instead",
                # "this will take a while", "liftover will drop variants". None of
                # it is actionable in a finished report, and the last one is
                # actively contradicted a page later by the liftover step's real
                # counts. Everything unmarked is a standing caveat that still
                # qualifies these results, and stays.
                workflow_warnings = [
                    w
                    for w in (workflow_config.get("warnings", []) or [])
                    if "class='preflight'" not in str(w)
                    and 'class="preflight"' not in str(w)
                ]
                logger.info(
                    f"Retrieved {len(workflow_warnings)} workflow warnings from job metadata"
                )
                # From what PharmCAT reported, not from the plan: which calls rest on
                # a gene only partly covered by the data, and -- for an alignment,
                # from its per-position statuses -- why those positions were missing.
                coverage_alert = partial_coverage_alert(
                    (data or {}).get("genes"),
                    (meta.get("file_analysis") or {}).get("file_type"),
                    read_position_statuses(output_dir),
                )
                if coverage_alert:
                    workflow_warnings.append(coverage_alert)
                add_preprocessor_drops((data or {}).get("genes"), output_dir)
                unread_alert = unread_variants_alert((data or {}).get("genes"))
                if unread_alert:
                    workflow_warnings.append(unread_alert)
                # Read once, used twice: the assume-ref paragraph below and the gVCF
                # paragraph further down describe the two ends of the same question
                # and must not be told different things about the same run.
                #
                # The metadata holds what the run ASKED for. What PharmCAT ran with
                # depends on the input too: main.nf switches both flags off for an
                # input it genotyped at every PharmCAT position (an alignment, a gVCF),
                # and pharmcat_flags_for_input is that rule's report-side copy. Using
                # the requested pair here would print "--missing-to-ref was used"
                # under a run where it was not.
                requested_absent = bool(meta.get("pharmcat_absent_to_ref"))
                requested_unspecified = bool(meta.get("pharmcat_unspecified_to_ref"))
                input_type = (meta.get("file_analysis") or {}).get("file_type")
                absent_to_ref, unspecified_to_ref = pharmcat_flags_for_input(
                    input_type, requested_absent, requested_unspecified
                )
                run_absent_to_ref = absent_to_ref
                # A variant-only VCF has no record where the sample matches the
                # reference; without --missing-to-ref PharmCAT cannot call a gene
                # whose defining positions are all absent from it.
                missing_positions_not_assumed = (
                    str(input_type or "").strip().lower() in _VARIANT_ONLY_INPUT_TYPES
                    and not absent_to_ref
                )
                pharmcat_assume_ref_methodology = methodology_assume_ref_paragraph(
                    absent_to_ref,
                    unspecified_to_ref,
                )
                # Its caution is an alert, from the same two flags.
                ref_alert = assume_ref_alert(absent_to_ref, unspecified_to_ref)
                if ref_alert:
                    workflow_warnings.append(ref_alert)
                if pharmcat_assume_ref_methodology is None:
                    # An alignment's PharmCAT positions: counts from the step row,
                    # what happened rather than what was planned.
                    bam2vcf_step = (
                        db_session.query(JobStep)
                        .filter(
                            JobStep.job_id == job_uuid,
                            JobStep.step_name == "pypgx_bam2vcf",
                        )
                        .first()
                    )
                    bam2vcf_output = (
                        (bam2vcf_step.output_data or {})
                        if bam2vcf_step is not None
                        else {}
                    )
                    pharmcat_assume_ref_methodology = explicit_calls_paragraph(
                        input_type,
                        requested_absent,
                        requested_unspecified,
                        bam2vcf_output.get("pharmcat_positions"),
                    )

                # A lifted run reports coordinates the uploaded file never had, so
                # the report has to say so. Read from the liftover JobStep, not the
                # upload-time needs_liftover flag: the flag is an intention, the row
                # is what happened, and only the row carries the counts (gatk-api
                # writes them as output_data when the step completes). Absent row =
                # no lift ran, which is the ordinary GRCh38 case.
                liftover_step = (
                    db_session.query(JobStep)
                    .filter(
                        JobStep.job_id == job_uuid,
                        JobStep.step_name == "liftover",
                    )
                    .first()
                )
                liftover_provenance = liftover_provenance_sentence(
                    liftover_step.output_data if liftover_step is not None else None
                )

                # Same contract as the liftover row above, for the same reason: a
                # genotyped gVCF's reference calls are the one thing about this run
                # the reader cannot infer from the results, and only the step row
                # carries how much of PharmCAT's position list the file covered
                # (gatk-api's /gvcf-to-vcf writes it as output_data on completion).
                # Absent row = no gVCF genotyping ran, which is every other input.
                gvcf_step = (
                    db_session.query(JobStep)
                    .filter(
                        JobStep.job_id == job_uuid,
                        JobStep.step_name == "gvcf_to_vcf",
                    )
                    .first()
                )
                # The flags are handed over, not assumed -- the EFFECTIVE pair from
                # above, which is off for a gVCF: main.nf never lets them turn the
                # positions this paragraph counts as uncovered into reference calls.
                gvcf_provenance = gvcf_provenance_paragraph(
                    gvcf_step.output_data if gvcf_step is not None else None,
                    absent_to_ref=absent_to_ref,
                    unspecified_to_ref=unspecified_to_ref,
                )
            except Exception as e:
                # Say so in the report, do not just blank the fields.
                #
                # Everything this block reads is a claim about the run that the
                # reader CANNOT infer from the results: that --absent-to-ref was
                # used and can over-call normal phenotypes, that the file was
                # lifted from GRCh37, how much of PharmCAT's position list a
                # genotyped gVCF actually covered, and the workflow's own
                # warnings. Resetting all four to empty renders a report that is
                # byte-indistinguishable from a run where none of them applied --
                # an absent measurement presented as a normal result, which is the
                # one outcome this pipeline refuses everywhere else. The log line
                # was the only trace, and nobody reads a report's logs.
                #
                # The warning goes in workflow_warnings because that is the
                # channel both templates already render (see this module's
                # docstring); it deliberately does not name the exception, which
                # would mean nothing to a clinician reading the PDF.
                logger.warning(f"Failed to retrieve job report metadata: {e}")
                workflow_warnings = [
                    "<p>⚠️ This run's provenance could not be read back from the "
                    "database, so this report omits statements it would normally "
                    "make about how the analysis was configured — including "
                    "whether uncalled positions were assumed to be reference, and "
                    "whether the file was converted from another genome build. "
                    "Treat the absence of those statements as unknown, not as "
                    "'did not apply'.</p>"
                ]
                pharmcat_assume_ref_methodology = None
                liftover_provenance = None
                gvcf_provenance = None

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

    # The wild-type labels key on the input's type (wild_type_phenotype). Neither
    # lane knows it -- the database lane says "vcf" -- so every BAM, CRAM and FASTQ
    # report gave its reference calls the variant-only label. The job's metadata
    # records it, whichever lane the data came from.
    input_file_type = (job_metadata.get("file_analysis") or {}).get("file_type")
    if input_file_type:
        data["file_type"] = str(input_file_type).lower()

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
                    # Source columns, or the lookup call when the source is not
                    # one diplotype (unphased DPYD); then wild type logic.
                    diplotype_str, phenotype_str, activity = executive_summary_call(row)
                    if diplotype_str.strip().lower() in ("", "no call") or not (
                        gene_was_called(diplotype_str)
                    ):
                        # Not a call: named with the genes examined without one.
                        continue
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
                    elif defines_no_phenotype(
                        {"gene": row.get("gene"), "diplotype": diplotype_str}
                    ) and (
                        str(phenotype_str or "").strip().lower()
                        in _ABSENT_PHENOTYPES | {""}
                    ):
                        # PharmCAT's TSV: the same "N/A" as the gene table.
                        phenotype_str = NO_PHENOTYPE_STANDARD

                    execsum_rows_from_tsv.append(
                        {
                            "gene": row.get("gene", ""),
                            "rec_lookup_diplotype": diplotype_str,  # Use fallback value
                            "rec_lookup_phenotype": phenotype_str,
                            "rec_lookup_activity_score": activity,
                            **{
                                key: value
                                for key, value in (
                                    unresolved_summary_call(row) or {}
                                ).items()
                                if key in ("diplotype_display", "diplotype_candidates")
                            },
                        }
                    )
    except Exception as _e_exec:
        logger.warning(f"Executive Summary TSV parse skipped: {_e_exec}")

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

    # What this report credits: the tools and data this job used, at the versions
    # that ran (app/reports/credits.py). Built once for every output; `platform`
    # is its software, which the workflow diagram labels its steps with.
    workflow_records = load_job_step_records(db_session, job_id)
    credit_run = credit_run_for_job(
        workflow_records,
        load_job_file_type(db_session, job_id) or file_type,
        matcher_meta["data_version"],
    )
    credits = build_report_credits(credit_run)
    platform = platform_from_credits(credits)

    # 265: put an unverified PharmCAT version in front of the reader, not just in
    # the log. A new list rather than an append: `workflow_warnings` above may be
    # the very list object inside the Job row's JSON metadata, and mutating that
    # in place edits a loaded ORM attribute for no reason.
    _version_alert = unverified_pharmcat_version_alert(pharmcat_results)
    if _version_alert:
        logger.warning(
            "PharmCAT version is not a verified series; adding a report alert"
        )
        workflow_warnings = list(workflow_warnings) + [_version_alert]

    # Genes whose reads mostly have mapping quality 0 (app/reports/alt_locus.py),
    # from the counts the alignment's pypgx_bam2vcf step recorded. Same new-list rule.
    bam2vcf_output = next(
        (r["output"] for r in workflow_records if r["name"] == "pypgx_bam2vcf"), None
    )
    alt_locus = alt_locus_flags(bam2vcf_output)
    _alt_locus_alert = alt_locus_alert(bam2vcf_output)
    if _alt_locus_alert:
        workflow_warnings = list(workflow_warnings) + [_alt_locus_alert]

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
        "credits": credits,
        **report_branding_context(),
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
        "missing_positions_not_assumed": missing_positions_not_assumed,
        # Add workflow warnings/alerts for report display
        "workflow_warnings": workflow_warnings,
        "pharmcat_assume_ref_methodology": pharmcat_assume_ref_methodology,
        "gvcf_provenance": gvcf_provenance,
        "liftover_provenance": liftover_provenance,
        # 159: run-derived provenance (each rendered only if resolved)
        "genome_build": matcher_meta["genome_build"],
        "named_allele_matcher_version": matcher_meta["named_allele_matcher_version"],
        "pharmcat_data_version": matcher_meta["data_version"],
    }
    # "Sample ID" in every report: the identifier entered at upload (or read from
    # the file header), else a name the run recorded, else none -- the templates
    # then say "Not provided" and the job id stands on its own Report ID line.
    # It used to fall back to the patient or job UUID, which named no sample.
    workflow_meta = (
        data.get("workflow") if isinstance(data.get("workflow"), dict) else {}
    )
    entered_sample = entered_sample_identifier(
        job_metadata, patient_info
    ) or display_sample_identifier(
        data.get("sample_identifier"),
        workflow_meta.get("display_sample_id"),
        data.get("displayId"),
        (patient_info or {}).get("display_sample_id"),
    )
    template_data["sample_identifier"] = entered_sample
    template_data["display_sample_id"] = entered_sample

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

    # Task 12: the mtDNA section's context, from this run's own
    # mtdna_result.json if MtdnaCall produced one. Set here too, not only on
    # the template_data the write_html branch below rebuilds from scratch --
    # this dict is what any caller reading generate_report's own template_data
    # sees, independent of that branch running.
    template_data["mtdna"] = mtdna_report_context(report_dir, data.get("genes", []))

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
        # If not embedded, look for the PyPGx results file in the report directory and
        # pick the newest by mtime. Two producers write two names: the sidecar wrapper
        # writes "<job>_pypgx_results.json" (plural), while the Nextflow lane emits
        # "pypgx_result.json" (singular). Both carry the same {"results": {...}} shape,
        # so match either - otherwise a Nextflow run leaves the report with used_pypgx
        # false and no PyPGx genes.
        if not pypgx_results:
            pypgx_json_candidates = glob.glob(
                os.path.join(report_dir, "*_pypgx_results.json")
            ) + glob.glob(os.path.join(report_dir, "pypgx_result.json"))
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
            # Every row of a gene: PharmCAT 2.x's JSON has one per guideline source
            # (CPIC, DPWG). Keyed to the last alone, only the DPWG row was PyPGx's,
            # and the dedupe kept the CPIC row, its call letter X.
            rows_by_gene: Dict[str, List[Dict[str, Any]]] = {}
            for g in data.get("genes", []):
                if isinstance(g, dict):
                    rows_by_gene.setdefault(
                        (g.get("gene") or g.get("name") or "").strip().upper(), []
                    ).append(g)
            for gene_key, gene_res in results_obj.items():
                try:
                    if not isinstance(gene_res, dict) or not gene_res.get("success"):
                        continue
                    gene_name = str(gene_key).strip()
                    details = gene_res.get("details") or {}
                    record = pypgx_record(details)
                    if gene_name.upper() in existing_genes:
                        # A PharmCAT row whose call PyPGx supplied -- an outside
                        # call with PyPGx's diplotype, as for CYP2D6 -- is PyPGx's
                        # call: it carries PyPGx's evidence and Call letter. A row
                        # PharmCAT called itself, or an outside call PyPGx did not
                        # make (the manual override file), does not.
                        for row in rows_by_gene.get(gene_name.upper(), []):
                            source = str(row.get("call_source") or "").upper()
                            if source == "OUTSIDE" and _same_diplotype(
                                row.get("diplotype"), gene_res.get("diplotype")
                            ):
                                row["tool_source"] = "PyPGx"
                                if record:
                                    row["pypgx"] = record
                                if details.get("positions_note"):
                                    row["positions_note"] = str(
                                        details["positions_note"]
                                    )
                            elif (
                                not gene_res.get("diplotype")
                                and details.get("note")
                                and not gene_was_called(row.get("diplotype"))
                            ):
                                # A gene nobody called, which PharmCAT takes from
                                # PyPGx alone (CYP2D6): PyPGx's reason is the reason.
                                row["no_call_note"] = str(details["note"])
                        continue
                    diplotype = gene_res.get("diplotype")
                    # PyPGx's own phenotype, only where it has a rule for the gene;
                    # elsewhere its "Indeterminate" means no rule exists.
                    phenotype = (
                        details.get("phenotype")
                        if details.get("phenotype_method")
                        else None
                    )
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
                        "phenotype": phenotype,
                        "activity_score": activity_score,
                        "tool_source": "PyPGx",
                        "pyPgxOnly": True,
                        # None where PyPGx has no phenotype rule for the gene.
                        "pypgx_phenotype_method": details.get("phenotype_method"),
                    }
                    if record:
                        gene_entry["pypgx"] = record
                    # Why PyPGx made no call, where it says: no reads over the gene,
                    # no genotype in the VCF (docker/pypgx's no_reads_note and
                    # no_genotype_note), or no small-variant alleles to call.
                    if not diplotype and details.get("note"):
                        gene_entry["no_call_note"] = str(details["note"])
                    # How many of the positions behind a call PyPGx read as the
                    # reference with nothing in the file to say so (docker/pypgx's
                    # positions_read_as_reference_note).
                    if diplotype and details.get("positions_note"):
                        gene_entry["positions_note"] = str(details["positions_note"])
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

    except Exception as e:
        logger.warning(f"PyPGx enrichment step failed: {e}", exc_info=True)

    optitype_added = merge_optitype_calls(data.setdefault("genes", []), report_dir)
    if optitype_added:
        logger.info(f"Added {optitype_added} OptiType-only HLA gene entries")
    credit_mtdna_call(data["genes"], report_dir)

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
    # The note beside each flagged gene's call; the alert above says why.
    annotate_alt_locus(data.get("genes", []), alt_locus)
    # Genes with SV-defined alleles that PyPGx called without copy number
    # (app/reports/copy_number.py): a note beside each call and one alert, from
    # PyPGx's own record, so a whole-genome run, where every such gene has a
    # copy-number call, gets neither. Same new-list rule for the alert. The input's
    # type as the job recorded it, not file_type: that defaults to "vcf", and a
    # report rebuilt without the job (/reprocess-report) would call a BAM a VCF.
    copy_number_why = why_not_assessed(
        load_job_file_type(db_session, job_id) or input_file_type, bam2vcf_output
    )
    annotate_copy_number(
        data.get("genes", []),
        genes_without_copy_number(pypgx_results),
        copy_number_why,
    )
    _copy_number_alert = copy_number_alert(
        [
            row["gene"]
            for row in data.get("genes", [])
            if isinstance(row, dict)
            and row.get("copy_number_not_assessed")
            and gene_was_called(row.get("diplotype"))
        ],
        copy_number_why,
    )
    if _copy_number_alert:
        workflow_warnings = list(workflow_warnings) + [_copy_number_alert]
        template_data["workflow_warnings"] = workflow_warnings

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

    # The run's own workflow diagram, drawn once from its job_steps rows and used by
    # every output below: the PDF's PNG, the HTML reports' SVG, the files beside them.
    workflow_svg_filename = f"{report_id}_workflow.svg"
    workflow_png_filename = f"{report_id}_workflow.png"
    try:
        with open(
            os.path.join(report_dir, "mtdna_result.json"), "r", encoding="utf-8"
        ) as fh:
            workflow_mtdna = json.load(fh)
    except (OSError, ValueError):
        workflow_mtdna = None
    # What this run is set to write: the reports on one line, the FHIR export on
    # its own. The diagram is drawn before, and into, the reports, so it says what
    # they are, not that each was written.
    workflow_reports = [
        label
        for label, enabled in (
            ("PDF", REPORT_CONFIG.get("write_pdf")),
            ("HTML", REPORT_CONFIG.get("write_html")),
            ("interactive HTML", REPORT_CONFIG.get("write_interactive_html")),
        )
        if enabled
    ]
    workflow_outputs = [", ".join(workflow_reports)] if workflow_reports else []
    # The export needs the job's database rows (see its block below).
    if fhir_export_enabled() and db_session is not None:
        workflow_outputs.append("FHIR R4 bundle")
    # Counted as the Drugs section lists them: one per drug name.
    workflow_drugs = {
        r.get("drug") for r in template_recommendations if isinstance(r, dict)
    } - {None, ""}
    workflow_diagram = job_workflow_diagram(
        [(r["name"], r["status"]) for r in workflow_records],
        load_job_file_type(db_session, job_id) or file_type,
        workflow_run_facts(
            workflow_records,
            platform,
            data.get("genes", []),
            workflow_mtdna,
            run_absent_to_ref,
            len(workflow_drugs),
            workflow_outputs,
            summary_rows(
                (
                    execsum_rows_from_tsv
                    if (EXECSUM_USE_TSV and execsum_rows_from_tsv)
                    else None
                ),
                data.get("genes", []),
            ),
            ((job_metadata.get("file_analysis") or {}).get("vcf_info") or {}).get(
                "reference_genome"
            ),
        ),
    )
    if not (workflow_diagram["png"] or workflow_diagram["svg_bytes"]):
        # Kroki did not draw it (off, or failed): the report shows the steps as
        # text and does not credit Kroki or Mermaid.
        credits = build_report_credits(dataclasses.replace(credit_run, kroki=False))
        template_data["credits"] = credits
    for filename, content, enabled in (
        (
            workflow_svg_filename,
            workflow_diagram["svg_bytes"],
            REPORT_CONFIG.get("write_workflow_svg", True),
        ),
        (
            workflow_png_filename,
            workflow_diagram["png"],
            REPORT_CONFIG.get("write_workflow_png", True),
        ),
    ):
        if enabled and content:
            try:
                with open(os.path.join(report_dir, filename), "wb") as f_out:
                    f_out.write(content)
            except OSError as e:
                logger.warning(f"Could not write {filename}: {e}")

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

            # The run's diagram (job_workflow_diagram above): its PNG, or the same
            # steps as text when Kroki could not draw it.
            workflow_kroki_svg_in_html = ""
            workflow_png_data_uri_html = workflow_diagram["png_data_uri"]
            workflow_html_fallback_html = (
                "" if workflow_png_data_uri_html else workflow_diagram["html"]
            )

            # Log what we have for the template
            logger.info(f"Workflow content prepared for HTML template:")
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
                # Task 12: rebuilt here too, not inherited from the earlier
                # template_data above -- this dict rebinds template_data and is
                # the one actually rendered into the HTML and PDF (see
                # report_branding_context's docstring for why that distinction
                # is load-bearing: a key set only on the first dict silently
                # never reaches the page).
                "mtdna": mtdna_report_context(report_dir, data.get("genes", [])),
                "disclaimer": get_disclaimer(),
                "credits": credits,
                **report_branding_context(),
                "workflow": per_sample_workflow,
                "workflow_diagrams": True,
                "header_text": header_text,
                # Pass TSV-driven Executive Summary rows if enabled and available
                "execsum_from_tsv": (
                    execsum_rows_from_tsv
                    if (EXECSUM_USE_TSV and execsum_rows_from_tsv)
                    else None
                ),
                "missing_positions_not_assumed": missing_positions_not_assumed,
                # Add workflow warnings/alerts for report display
                "workflow_warnings": workflow_warnings,
                "pharmcat_assume_ref_methodology": pharmcat_assume_ref_methodology,
                "gvcf_provenance": gvcf_provenance,
                "liftover_provenance": liftover_provenance,
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
            # The shown sample identifier, as for template_data above.
            template_data["sample_identifier"] = entered_sample
            template_data["display_sample_id"] = entered_sample

            # What the HTML report is rendered from, for troubleshooting: logged at
            # DEBUG. It used to be printed at the foot of every HTML report (a
            # "debug veneer"), handing readers internal ids and Python reprs of the
            # patient's first diplotype and recommendation.
            try:
                debug_info = {
                    "workflow_config": per_sample_workflow,
                    "workflow_assets": {
                        "workflow_steps_recorded": workflow_diagram["recorded"],
                        "used_kroki_svg": bool(workflow_kroki_svg_in_html),
                        "used_png_data_uri": bool(workflow_png_data_uri_html),
                        "used_html_fallback": bool(workflow_html_fallback_html),
                    },
                    "platform_info": platform,
                    # current_year included deliberately. This diagnostic exists
                    # to show what the footer was rendered from, and it omitted
                    # the one key that was actually missing -- so the tool built
                    # to debug the footer could not have revealed the "(c) 2024-"
                    # bug it was looking at.
                    "footer_context": {
                        "author_name": template_data.get("author_name"),
                        "license_name": template_data.get("license_name"),
                        "license_url": template_data.get("license_url"),
                        "source_url": template_data.get("source_url"),
                        "current_year": template_data.get("current_year"),
                    },
                    "template_data_keys": list(template_data.keys()),
                    "template_data_sample": {
                        k: str(v)[:100] + "..." if len(str(v)) > 100 else str(v)
                        for k, v in list(template_data.items())[:5]
                    },
                }
                logger.debug(
                    "HTML report context: %s",
                    json.dumps(debug_info, indent=2, default=str),
                )
            except Exception:
                logger.debug("HTML report context could not be serialised")

            # Render the HTML template with error handling
            try:
                logger.info("Rendering HTML template...")
                html_content = template.render(
                    **template_data,
                    workflow_kroki_svg=workflow_kroki_svg_in_html,
                    workflow_png_data_uri=workflow_png_data_uri_html,
                    workflow_html_fallback=workflow_html_fallback_html,
                )
                logger.info(
                    f"✓ Template rendered successfully, HTML content length: {len(html_content)}"
                )
            except Exception as e:
                logger.error(f"✗ Template rendering failed: {str(e)}", exc_info=True)
                # Try to provide more context about what might be missing
                logger.error(f"Template data keys: {list(template_data.keys())}")
                logger.error(
                    f"Workflow content lengths - Kroki SVG: {len(workflow_kroki_svg_in_html)}, PNG: {len(workflow_png_data_uri_html)}, HTML: {len(workflow_html_fallback_html)}"
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
                    f"✓ Workflow SVG URL added: {report_paths['workflow_svg_path']}"
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

        # Generate PDF report using configured engine
        if REPORT_CONFIG["write_pdf"]:
            pdf_path = os.path.join(report_dir, f"{base_filename}.pdf")

            logger.info(f"=== PDF GENERATION START (Engine: {PDF_ENGINE}) ===")
            logger.info(f"Workflow data: {per_sample_workflow}")

            # The run's diagram as a PNG: WeasyPrint draws Kroki's SVG text badly. Without
            # one, the same steps as text.
            pdf_png_data_uri = workflow_diagram["png_data_uri"]
            workflow_kroki_svg_for_pdf = ""
            # For the ReportLab lane (pdf_generators), which renders from template_data.
            template_data["job_workflow_diagram"] = {
                "png_data_uri": workflow_diagram["png_data_uri"],
                "pdf_style": workflow_diagram["pdf_style"],
                "html": workflow_diagram["html"],
            }

            # Render the HTML template with workflow diagram
            # Prefer PNG for reliable PDF rendering; SVG as fallback only
            pdf_html_content = template.render(
                **template_data,
                workflow_png_data_uri=pdf_png_data_uri,
                workflow_kroki_svg=workflow_kroki_svg_for_pdf,
                workflow_png_file_url="",
                workflow_png_style=workflow_diagram["pdf_style"],
                workflow_html_fallback=(
                    "" if pdf_png_data_uri else workflow_diagram["html"]
                ),
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

        # The reports the pipeline published itself: PharmCAT's own and mtDNA-Server 2's.
        native_paths = native_report_paths(report_dir, report_id, reports_url_prefix)
        report_paths.update(native_paths)
        data.update({NATIVE_REPORT_URL_KEYS[k]: v for k, v in native_paths.items()})

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

        # Generate interactive HTML report if enabled. Last, so its Export tab can
        # list the PDF, PharmCAT's files and the FHIR bundle that exist by now.
        if REPORT_CONFIG["write_interactive_html"]:
            create_interactive_html_report(
                patient_id=patient_id,
                report_id=report_id,
                sample_identifier=entered_sample,
                diplotypes=data.get("genes", []),
                recommendations=template_recommendations,
                output_path=interactive_html_path,
                workflow=per_sample_workflow,
                credits=credits,
                workflow_warnings=workflow_warnings,
                pharmcat_assume_ref_methodology=pharmcat_assume_ref_methodology,
                liftover_provenance=liftover_provenance,
                gvcf_provenance=gvcf_provenance,
                missing_positions_not_assumed=missing_positions_not_assumed,
                downloads=export_downloads(report_dir, report_id),
                workflow_diagram=workflow_diagram,
            )
            logger.info(f"Interactive HTML report generated: {interactive_html_path}")

            # Add to report paths
            server_interactive_html_path = (
                f"{reports_url_prefix}/{base_filename}_interactive.html"
            )
            report_paths["interactive_html_path"] = server_interactive_html_path
            data["interactive_html_report_url"] = server_interactive_html_path

        # Add the processed data to the report paths for reference
        report_paths["processed_data"] = data

        logger.info(f"Report generation completed successfully")
        logger.info(f"Generated {len(report_paths)} report paths")
        logger.info(f"Report paths keys: {list(report_paths.keys())}")

        return report_paths

    except Exception as e:
        logger.error(f"Error generating report: {str(e)}")
        raise ReportGenerationError(f"Failed to generate report: {str(e)}")
