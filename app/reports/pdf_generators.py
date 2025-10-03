#!/usr/bin/env python3
"""
Environment-Configured Dual-Lane PDF Generation System

This module provides an abstracted interface for PDF generation with two backends:
- Primary engine: Configured via PDF_ENGINE environment variable (weasyprint or reportlab)
- Fallback engine: Automatically selected based on PDF_FALLBACK environment variable

Both backends implement the same interface, making them interchangeable.
The system respects environment configuration for engine priority and fallback behavior.
"""

# Standard library imports
import base64
import logging
import os
import re
import tempfile
from abc import ABC, abstractmethod
from datetime import datetime
from io import BytesIO
from typing import Dict, Any, Optional, Union

# Third-party imports
from jinja2 import Environment, FileSystemLoader, Template
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer
)
from weasyprint import CSS, HTML
from weasyprint.text.fonts import FontConfiguration

# Local imports
from app.reports.generator import (
    build_citations,
    build_platform_info,
    get_author_name,
    get_disclaimer,
    get_license_name,
    get_license_url,
    get_source_url,
    _sanitize_graphviz_svg
)
from app.visualizations.workflow_diagram import read_workflow_mermaid, render_with_kroki
from pathlib import Path

logger = logging.getLogger(__name__)

class PDFGenerator(ABC):
    """Abstract base class for PDF generators."""
    
    @abstractmethod
    def generate_pdf(self, 
                    template_data: Dict[str, Any], 
                    output_path: str,
                    workflow_diagram: Optional[bytes] = None) -> bool:
        """
        Generate a PDF report.
        
        Args:
            template_data: Data to render in the template
            output_path: Path where PDF should be saved
            workflow_diagram: Optional PNG bytes for workflow diagram
            
        Returns:
            bool: True if successful, False otherwise
        """
        pass
    
    @abstractmethod
    def get_supported_features(self) -> Dict[str, bool]:
        """Return supported features of this generator."""
        pass

class ReportLabGenerator(PDFGenerator):
    """
    ReportLab-based PDF generator.
    
    Features:
    - Excellent text rendering (no missing text issues)
    - Searchable PDFs
    - Professional layout control
    - Native Python implementation
    - No external dependencies
    """
    
    def __init__(self):
        self.name = "ReportLab"
        self.version = "4.0+"
        
    def generate_pdf(self, 
                    template_data: Dict[str, Any], 
                    output_path: str,
                    workflow_diagram: Optional[bytes] = None) -> bool:
        """Generate PDF using ReportLab."""
        try:
            logger.info(f"🎯 Using {self.name} for PDF generation")
            
            # Create PDF document
            doc = SimpleDocTemplate(
                output_path,
                pagesize=A4,
                rightMargin=18*mm,
                leftMargin=18*mm,
                topMargin=18*mm,
                bottomMargin=18*mm
            )
            
            # Build story (content)
            story = []
            styles = getSampleStyleSheet()
            
            # Custom styles
            title_style = ParagraphStyle(
                'CustomTitle',
                parent=styles['Heading1'],
                fontSize=18,
                spaceAfter=12,
                alignment=TA_CENTER,
                textColor=colors.darkblue
            )
            
            heading_style = ParagraphStyle(
                'CustomHeading',
                parent=styles['Heading2'],
                fontSize=14,
                spaceAfter=8,
                textColor=colors.darkblue
            )
            
            normal_style = ParagraphStyle(
                'CustomNormal',
                parent=styles['Normal'],
                fontSize=11,
                spaceAfter=6
            )
            mono_style = ParagraphStyle(
                'Mono',
                parent=styles['Code'],
                fontName='Courier',
                fontSize=9.5,
                leading=11.5,
                spaceAfter=6
            )
            
            # Compute display sample once and reuse
            display_sample = (
                template_data.get('display_sample_id')
                or template_data.get('sample_identifier')
                or template_data.get('patient_id')
                or template_data.get('sample_id')
            )

            # Title
            title = f"Pharmacogenomic Report - Sample {display_sample}" if display_sample else "Pharmacogenomic Report"
            story.append(Paragraph(title, title_style))
            story.append(Spacer(1, 12))

            # Sample Information
            if display_sample:
                story.append(Paragraph("Sample Information", heading_style))
                story.append(Paragraph(f"<b>Sample ID:</b> {display_sample}", normal_style))
                if 'report_id' in template_data:
                    story.append(Paragraph(f"<b>Report ID:</b> {template_data['report_id']}", normal_style))
                if 'file_type' in template_data:
                    story.append(Paragraph(f"<b>File Type:</b> {template_data['file_type']}", normal_style))
                story.append(Spacer(1, 12))
            
            # Add timestamp if available
            current_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            story.append(Paragraph(f"<b>Report Generated:</b> {current_time}", normal_style))
            story.append(Spacer(1, 12))
            
            # Add workflow information if available
            if 'workflow' in template_data and template_data['workflow']:
                story.append(Paragraph("Workflow Information", heading_style))
                workflow = template_data['workflow']
                if isinstance(workflow, dict):
                    if 'file_type' in workflow:
                        story.append(Paragraph(f"<b>File Type:</b> {workflow['file_type']}", normal_style))
                    if 'used_gatk' in workflow:
                        story.append(Paragraph(f"<b>GATK Used:</b> {'Yes' if workflow['used_gatk'] else 'No'}", normal_style))
                    if 'used_pypgx' in workflow:
                        story.append(Paragraph(f"<b>PyPGx Used:</b> {'Yes' if workflow['used_pypgx'] else 'No'}", normal_style))
                    if 'used_pharmcat' in workflow:
                        story.append(Paragraph(f"<b>PharmCAT Used:</b> {'Yes' if workflow['used_pharmcat'] else 'No'}", normal_style))
                story.append(Spacer(1, 12))
            
            # Workflow Diagram
            story.append(Paragraph("Analysis Workflow", heading_style))
            story.append(Spacer(1, 6))
            
            # Check if we have workflow content in template data or workflow_diagram parameter
            workflow_text_extracted = False
            
            # First, try to extract from workflow_diagram parameter
            if workflow_diagram and isinstance(workflow_diagram, bytes):
                try:
                    # Check if it's SVG content (text-based)
                    try:
                        svg_content = workflow_diagram.decode('utf-8')
                        if '<svg' in svg_content or '<text' in svg_content:
                            # Extract text content from SVG for ReportLab
                            text_matches = re.findall(r'<text[^>]*>(.*?)</text>', svg_content, re.DOTALL)
                            if text_matches:
                                # Create a text-based workflow representation
                                workflow_text = "Workflow Steps:\n"
                                for i, text in enumerate(text_matches, 1):
                                    workflow_text += f"{i}. {text.strip()}\n"
                                story.append(Paragraph(workflow_text, normal_style))
                                logger.info(f"✓ Workflow text extracted from SVG bytes using {self.name}")
                                workflow_text_extracted = True
                        else:
                            # Not SVG content, might be PNG or other binary
                            logger.info(f"Workflow diagram is binary data (likely PNG), using fallback text")
                    except UnicodeDecodeError:
                        # Binary data (PNG, etc.) - can't extract text
                        logger.info(f"Workflow diagram is binary data (likely PNG), using fallback text")
                except Exception as e:
                    logger.warning(f"Failed to process workflow_diagram: {e}")
            
            # If no text extracted from workflow_diagram, try template_html
            if not workflow_text_extracted and 'template_html' in template_data:
                # Try to extract workflow SVG content from the template HTML
                workflow_match = re.search(r'<div class="workflow-figure">(.*?)</div>', template_data['template_html'], re.DOTALL)
                if workflow_match:
                    workflow_svg_content = workflow_match.group(1)
                    # Extract text content from SVG for ReportLab
                    text_matches = re.findall(r'<text[^>]*>(.*?)</text>', workflow_svg_content, re.DOTALL)
                    if text_matches:
                        # Create a text-based workflow representation
                        workflow_text = "Workflow Steps:\n"
                        for i, text in enumerate(text_matches, 1):
                            workflow_text += f"{i}. {text.strip()}\n"
                        story.append(Paragraph(workflow_text, normal_style))
                        logger.info(f"✓ Workflow text extracted from template HTML using {self.name}")
                        workflow_text_extracted = True
                    else:
                        # Try to extract from SVG content more broadly
                        svg_text_matches = re.findall(r'<svg[^>]*>(.*?)</svg>', workflow_svg_content, re.DOTALL)
                        if svg_text_matches:
                            # Look for any text-like content in the SVG
                            content = svg_text_matches[0]
                            # Extract any text-like content
                            text_content = re.findall(r'>([^<]+)<', content)
                            if text_content:
                                workflow_text = "Workflow Steps:\n"
                                for i, text in enumerate(text_content, 1):
                                    if text.strip() and len(text.strip()) > 1:  # Filter out very short text
                                        workflow_text += f"{i}. {text.strip()}\n"
                                story.append(Paragraph(workflow_text, normal_style))
                                logger.info(f"✓ Workflow text extracted from SVG content using {self.name}")
                                workflow_text_extracted = True
                        else:
                            # Fallback to basic workflow text
                            story.append(Paragraph("Workflow: Upload → Detect → VCF → PharmCAT → Reports", normal_style))
                else:
                    story.append(Paragraph("Workflow: Upload → Detect → VCF → PharmCAT → Reports", normal_style))
            
            # If still no text extracted, use fallback
            if not workflow_text_extracted:
                # Create a more visually appealing workflow representation
                story.append(Paragraph("Analysis Workflow", heading_style))
                story.append(Spacer(1, 6))
                
                # Create a structured workflow representation
                workflow_steps = [
                    ("1. Upload", "Sample file uploaded for analysis"),
                    ("2. Detect", "File type detected and processed"),
                    ("3. VCF", "Variant Call Format generation"),
                    ("4. PharmCAT", "Pharmacogenomic annotation"),
                    ("5. Reports", "Comprehensive report generation")
                ]
                
                for step, description in workflow_steps:
                    step_text = f"<b>{step}:</b> {description}"
                    story.append(Paragraph(step_text, normal_style))
                    story.append(Spacer(1, 3))
                
                logger.info(f"✓ Using enhanced workflow representation in {self.name}")
            else:
                # Add a visual separator after extracted workflow text
                story.append(Spacer(1, 6))
                story.append(Paragraph("<i>Note: Workflow diagram text extracted from SVG content</i>", normal_style))
            
            story.append(Spacer(1, 12))
            
            # Analysis Results - Use the actual template data instead of just basic results
            if 'analysis_results' in template_data:
                story.append(Paragraph("Analysis Results", heading_style))
                results = template_data['analysis_results']
                if isinstance(results, dict):
                    for key, value in results.items():
                        if value:
                            story.append(Paragraph(f"<b>{key}:</b> {value}", normal_style))
                story.append(Spacer(1, 12))
            
            # Handle diplotypes data specifically
            if 'diplotypes' in template_data and template_data['diplotypes']:
                story.append(Paragraph("Gene Diplotypes", heading_style))
                story.append(Spacer(1, 6))
                
                diplotypes = template_data['diplotypes']
                if isinstance(diplotypes, list):
                    for diplotype in diplotypes:
                        if isinstance(diplotype, dict):
                            # Handle different diplotype formats
                            if 'gene' in diplotype:
                                gene_name = diplotype.get('gene', 'Unknown')
                                diplotype_value = diplotype.get('diplotype', 'Unknown')
                                phenotype = diplotype.get('phenotype', 'Unknown')
                                activity_score = diplotype.get('activity_score', 'Unknown')
                                
                                diplotype_text = f"<b>{gene_name}:</b> {diplotype_value}"
                                if phenotype != 'Unknown':
                                    diplotype_text += f" (Phenotype: {phenotype})"
                                if activity_score != 'Unknown' and activity_score and str(activity_score).strip():
                                    diplotype_text += f" (Activity Score: {activity_score})"
                                
                                story.append(Paragraph(diplotype_text, normal_style))
                                story.append(Spacer(1, 3))
                            elif 'name' in diplotype:
                                # Alternative format (legacy support)
                                story.append(Paragraph(f"<b>{diplotype.get('name', 'Unknown')}:</b> {diplotype.get('value', 'Unknown')}", normal_style))
                                story.append(Spacer(1, 3))
                            else:
                                # Generic format
                                story.append(Paragraph(f"• {str(diplotype)}", normal_style))
                                story.append(Spacer(1, 3))
                        else:
                            story.append(Paragraph(f"• {str(diplotype)}", normal_style))
                            story.append(Spacer(1, 3))
                story.append(Spacer(1, 12))
            
            # Handle recommendations data specifically
            if 'recommendations' in template_data and template_data['recommendations']:
                story.append(Paragraph("Drug Recommendations", heading_style))
                story.append(Spacer(1, 6))
                
                recommendations = template_data['recommendations']
                if isinstance(recommendations, list):
                    for recommendation in recommendations:
                        if isinstance(recommendation, dict):
                            # Handle different recommendation formats
                            if 'drug' in recommendation:
                                drug_name = recommendation.get('drug', 'Unknown')
                                recommendation_text = recommendation.get('recommendation', 'See report for details')
                                guidelines = recommendation.get('guidelines', [])
                                
                                rec_text = f"<b>{drug_name}:</b> {recommendation_text}"
                                if guidelines:
                                    rec_text += f" (Guidelines: {', '.join(guidelines)})"
                                
                                story.append(Paragraph(rec_text, normal_style))
                            else:
                                # Generic format
                                story.append(Paragraph(f"• {str(recommendation)}", normal_style))
                        else:
                            story.append(Paragraph(f"• {str(recommendation)}", normal_style))
                story.append(Spacer(1, 12))
            
            # Add all other template data that contains the actual pharmacogenomic information
            # This ensures we get the full report content, not just basic info
            for key, value in template_data.items():
                if key not in ['sample_id', 'file_type', 'analysis_results', 'workflow_diagram', 'diplotypes', 'recommendations', 'workflow'] and value:
                    if isinstance(value, str) and len(value) > 0:
                        story.append(Paragraph(f"<b>{key.replace('_', ' ').title()}:</b> {value}", normal_style))
                    elif isinstance(value, dict) and value:
                        story.append(Paragraph(f"<b>{key.replace('_', ' ').title()}:</b>", heading_style))
                        for sub_key, sub_value in value.items():
                            if sub_value:
                                story.append(Paragraph(f"  • <b>{sub_key}:</b> {sub_value}", normal_style))
                        story.append(Spacer(1, 6))
                    elif isinstance(value, list) and value:
                        story.append(Paragraph(f"<b>{key.replace('_', ' ').title()}:</b>", heading_style))
                        for item in value:
                            if item:
                                story.append(Paragraph(f"  • {item}", normal_style))
                        story.append(Spacer(1, 6))
            
            # Genomic Datafile Header section (new page)
            try:
                header_text = template_data.get('header_text')
                if not header_text:
                    # Try to load from output directory using report_id or patient_id
                    patient_dir = Path(output_path).parent
                    report_id = str(template_data.get('report_id') or '').strip()
                    candidates = []
                    if report_id:
                        candidates.append(patient_dir / f"{report_id}.header.txt")
                    sample_identifier = str(template_data.get('sample_identifier') or template_data.get('patient_id') or '').strip()
                    if sample_identifier:
                        candidates.append(patient_dir / f"{sample_identifier}.header.txt")
                    # Any *.header.txt fallback
                    for p in patient_dir.glob("*.header.txt"):
                        if p not in candidates:
                            candidates.append(p)
                    for cand in candidates:
                        if cand.exists():
                            header_text = cand.read_text(encoding='utf-8', errors='ignore')
                            break
                if header_text:
                    story.append(PageBreak())
                    story.append(Paragraph("Genomic Datafile Header", heading_style))
                    story.append(Paragraph("The submitted genomic datafile's header matter is reproduced below. Please note that if any conversion or re-alignment operations were performed, the header information below does not reflect that; the header from the original uploaded file is what is shown.", normal_style))
                    story.append(Spacer(1, 6))
                    story.append(Preformatted(header_text, mono_style))
            except Exception as _hdr_e:
                logger.debug(f"Header text section skipped: {_hdr_e}")

            # Footer
            story.append(Spacer(1, 20))
            footer_style = ParagraphStyle(
                'Footer',
                parent=styles['Normal'],
                fontSize=9,
                textColor=colors.grey,
                alignment=TA_CENTER
            )
            story.append(Paragraph("Generated by ZaroPGx - Pharmacogenomic Analysis Platform", footer_style))
            
            # Page number callback (bottom-right, subtle gray)
            def _add_page_number(canvas_obj, doc_obj):
                try:
                    canvas_obj.saveState()
                    page_num_text = f"Page {canvas_obj.getPageNumber()}"
                    canvas_obj.setFont("Helvetica", 9)
                    canvas_obj.setFillColor(colors.grey)
                    text_width = canvas_obj.stringWidth(page_num_text, "Helvetica", 9)
                    x = doc_obj.pagesize[0] - doc_obj.rightMargin - text_width
                    y = 12 * mm  # slightly above bottom margin
                    canvas_obj.drawString(x, y, page_num_text)
                finally:
                    canvas_obj.restoreState()

            # Build PDF with page number on every page
            doc.build(story, onFirstPage=_add_page_number, onLaterPages=_add_page_number)
            logger.info(f"✓ PDF generated successfully using {self.name}: {output_path}")
            return True
            
        except ImportError as e:
            logger.error(f"✗ ReportLab not available: {e}")
            return False
        except Exception as e:
            logger.error(f"✗ ReportLab PDF generation failed: {e}", exc_info=True)
            return False
    
    def get_supported_features(self) -> Dict[str, bool]:
        """Return ReportLab supported features."""
        return {
            "text_rendering": True,
            "searchable_text": True,
            "workflow_diagrams": True,
            "complex_layouts": True,
            "professional_quality": True,
            "no_external_deps": True
        }

class WeasyPrintGenerator(PDFGenerator):
    """
    WeasyPrint-based PDF generator (Fallback).
    
    Features:
    - HTML-to-PDF conversion
    - CSS styling support
    - Complex layouts
    - Template-based generation
    """
    
    def __init__(self):
        self.name = "WeasyPrint"
        self.version = "66.0+"
        
    def generate_pdf(self, 
                    template_data: Dict[str, Any], 
                    output_path: str,
                    workflow_diagram: Optional[bytes] = None) -> bool:
        """Generate PDF using WeasyPrint (fallback method)."""
        try:
            logger.info(f"🔄 Using {self.name} as fallback for PDF generation")
            
            # Generate proper HTML using the PDF template structure
            # This ensures we get the right template for PDF generation
            try:
                # Get the template directory path
                template_dir = os.path.join(os.path.dirname(__file__), 'templates')
                env = Environment(loader=FileSystemLoader(template_dir))
                
                # Use the PDF template (report_template.html) for proper PDF generation
                template = env.get_template("report_template.html")

                # Build the template data structure expected by report_template.html
                report_data = {
                    "patient_id": template_data.get("patient_id", "Unknown"),
                    "report_id": template_data.get("report_id", "Unknown"),
                    "sample_identifier": template_data.get("sample_identifier", template_data.get("patient_id", "Unknown")),
                    "report_date": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "diplotypes": template_data.get("diplotypes", []),
                    "recommendations": template_data.get("recommendations", []),
                    # Pass TSV-driven Executive Summary rows when provided
                    "execsum_from_tsv": template_data.get("execsum_from_tsv"),
                    "disclaimer": get_disclaimer(),
                    "platform_info": build_platform_info(),
                    "citations": build_citations(),
                    "author_name": get_author_name(),
                    "license_name": get_license_name(),
                    "license_url": get_license_url(),
                    "source_url": get_source_url(),
                    "current_year": datetime.now().year,
                    "workflow": template_data.get("workflow", {}),
                    "workflow_diagram": template_data.get("workflow_diagram", {}),
                    "header_text": template_data.get("header_text", "")
                }
                # Prefer Mermaid → PNG for PDF for fidelity; else embed Graphviz/Kroki SVG; else PNG file
                try:
                    patient_id = str(report_data.get("patient_id") or "").strip()
                    report_dir = Path(output_path).parent
                    # Prefer Graphviz SVG for PDFs (WeasyPrint-friendly), then Kroki
                    kroki_svg_path = report_dir / f"{patient_id}_workflow_kroki_mermaid.svg"
                    graphviz_svg_path = report_dir / f"{patient_id}_workflow.svg"
                    png_path = report_dir / f"{patient_id}_workflow.png"
                    if patient_id:
                        # Try Mermaid → PNG via Kroki for full-fidelity raster
                        try:
                            mermaid_src = read_workflow_mermaid()
                            png_bytes = render_with_kroki(mermaid_src, fmt="png")
                            if png_bytes:
                                report_data["workflow_kroki_svg"] = None
                                report_data["workflow_svg"] = None
                                report_data["workflow_png_file_url"] = ""
                                report_data["workflow_png_data_uri"] = f"data:image/png;base64,{base64.b64encode(png_bytes).decode()}"
                                logger.info("✓ Embedded Mermaid→PNG data URI for PDF")
                        except Exception as e:
                            logger.warning(f"Mermaid→PNG generation failed, falling back to SVG/PNG files: {e}")

                        # If no PNG data URI from Mermaid, fallback to Graphviz SVG, then Kroki SVG, then PNG file
                        if not report_data.get("workflow_png_data_uri"):
                            if graphviz_svg_path.exists():
                                try:
                                    svg_text = graphviz_svg_path.read_text(encoding="utf-8", errors="ignore")
                                    report_data["workflow_svg"] = _sanitize_graphviz_svg(svg_text)
                                    logger.info(f"✓ Embedded Graphviz SVG into PDF template: {graphviz_svg_path}")
                                except Exception as e:
                                    logger.warning(f"Failed to read Graphviz SVG: {e}")
                            if "workflow_svg" not in report_data and kroki_svg_path.exists():
                                try:
                                    svg_text = kroki_svg_path.read_text(encoding="utf-8", errors="ignore")
                                    report_data["workflow_kroki_svg"] = _sanitize_graphviz_svg(svg_text)
                                    logger.info(f"✓ Embedded Kroki Mermaid SVG into PDF template: {kroki_svg_path}")
                                except Exception as e:
                                    logger.warning(f"Failed to read Kroki SVG: {e}")
                            if "workflow_kroki_svg" not in report_data and "workflow_svg" not in report_data and png_path.exists():
                                try:
                                    report_data["workflow_png_file_url"] = ""
                                    report_data["workflow_png_data_uri"] = f"data:image/png;base64,{base64.b64encode(png_path.read_bytes()).decode()}"
                                    logger.info(f"✓ Embedded PNG data URI into PDF template: {png_path}")
                                except Exception as e:
                                    logger.warning(f"Failed to embed PNG workflow: {e}")
                except Exception as e:
                    logger.warning(f"Workflow asset embedding skipped due to error: {e}")
                
                # Ensure header_text is available in report_data
                try:
                    if not report_data.get("header_text"):
                        pid = str(report_data.get("patient_id") or "").strip()
                        rid = str(report_data.get("report_id") or "").strip()
                        out_dir = Path(output_path).parent
                        candidates = []
                        if rid:
                            candidates.append(out_dir / f"{rid}.header.txt")
                        if pid:
                            candidates.append(out_dir / f"{pid}.header.txt")
                        for p in out_dir.glob("*.header.txt"):
                            if p not in candidates:
                                candidates.append(p)
                        for cand in candidates:
                            if cand.exists():
                                report_data["header_text"] = cand.read_text(encoding="utf-8", errors="ignore")
                                break
                except Exception:
                    pass

                # Generate the HTML content using the PDF template
                html_content = template.render(**report_data)
                logger.info(f"✓ Generated HTML content using PDF template for {self.name}: {len(html_content)} characters")
                
                # Add PDF-specific CSS optimizations
                pdf_optimization_css = """
                <style>
                    /* PDF-specific optimizations */
                    @page {
                        size: A4;
                        margin: 16mm;
                        @bottom-right {
                            content: "Page " counter(page) " of " counter(pages);
                            font-size: 10px;
                            color: #888;
                        }
                    }
                    /* Monospaced header text: full width, normal flow (no card) */
                    .file-header { font-family: 'Courier New', monospace; font-size: 12px; white-space: pre; background-color: transparent; border: none; padding: 0; border-radius: 0; line-height: 1.35; tab-size: 4; overflow: visible; }
                    .header-note { font-size: 0.95em; color: #555; margin: 0 0 8px 0; }
                    .header-section { page-break-before: always !important; margin: 0 !important; padding: 0 !important; }
                    
                    /* Give workflow its own page and fill printable area */
                    .workflow-section {
                        page-break-before: always !important;
                        page-break-after: always !important;
                        margin: 0 !important;
                        padding: 0 !important;
                    }
                    .workflow-figure {
                        margin: 0 !important;
                        padding: 0 !important;
                        background: #ffffff !important;
                        border: none !important;
                        border-radius: 0 !important;
                        width: 178mm !important;   /* 210mm - 32mm */
                        height: 265mm !important;  /* 297mm - 32mm */
                    }
                    .workflow-figure img,
                    .workflow-figure svg {
                        width: 100% !important;
                        height: 100% !important;
                        object-fit: contain !important;
                        background: #ffffff !important;
                        border-radius: 0 !important;
                        display: block !important;
                        margin: 0 auto !important;
                    }
                </style>
                """
                
                # Insert the CSS optimization + SVG text visibility CSS into the HTML head
                if '<head>' in html_content:
                    svg_text_css = """
                    <style>
                    .workflow-figure svg text,
                    .workflow-figure svg tspan {
                        fill: #000000 !important;
                        color: #000000 !important;
                        font-family: Arial, sans-serif !important;
                        font-size: 12px !important;
                        visibility: visible !important;
                        opacity: 1 !important;
                    }
                    </style>
                    """
                    html_content = html_content.replace('<head>', f'<head>{pdf_optimization_css}{svg_text_css}')
                else:
                    svg_text_css = """
                    <style>
                    .workflow-figure svg text,
                    .workflow-figure svg tspan {
                        fill: #000000 !important;
                        color: #000000 !important;
                        font-family: Arial, sans-serif !important;
                        font-size: 12px !important;
                        visibility: visible !important;
                        opacity: 1 !important;
                    }
                    </style>
                    """
                    html_content = f'<head>{pdf_optimization_css}{svg_text_css}</head>{html_content}'
                
            except Exception as e:
                logger.warning(f"⚠ Failed to generate HTML using PDF template: {e}, using fallback template")
                
                # Fallback to basic template if PDF template generation fails
                html_template = """
                <!DOCTYPE html>
                <html>
                <head>
                    <meta charset="UTF-8">
                    <title>Pharmacogenomic Report</title>
                    <style>
                        @page { size: A4; margin: 16mm; }
                        body { font-family: Arial, sans-serif; font-size: 12px; }
                        .title { font-size: 18px; font-weight: bold; text-align: center; color: #2c3e50; margin-bottom: 20px; }
                        .section { margin-bottom: 15px; }
                        .section-title { font-size: 14px; font-weight: bold; color: #2c3e50; margin-bottom: 8px; }
                        .content { margin-bottom: 6px; }
                        .footer { text-align: center; color: #7f8c8d; font-size: 9px; margin-top: 30px; }
                    </style>
                </head>
                <body>
                    <div class="title">Pharmacogenomic Report</div>
                    
                    <div class="section">
                        <div class="section-title">Sample Information</div>
                        <div class="content"><strong>Sample ID:</strong> {{ patient_id or 'N/A' }}</div>
                        <div class="content"><strong>Report ID:</strong> {{ report_id or 'N/A' }}</div>
                    </div>
                    
                    <div class="section">
                        <div class="section-title">Analysis Results</div>
                        {% if analysis_results %}
                            {% for key, value in analysis_results.items() %}
                                {% if value %}
                                <div class="content"><strong>{{ key }}:</strong> {{ value }}</div>
                                {% endif %}
                            {% endfor %}
                        {% else %}
                        <div class="content">No analysis results available</div>
                        {% endif %}
                    </div>
                    
                    <div class="footer">
                        Generated by ZaroPGx - Pharmacogenomic Analysis Platform
                    </div>
                </body>
                </html>
                """
                
                # Prepare template data for fallback template
                template_data_copy = template_data.copy()
                html_content = Template(html_template).render(**template_data_copy)
            
            # Create temporary HTML file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False, encoding='utf-8') as f:
                f.write(html_content)
                temp_html_path = f.name
            
            try:
                # Generate PDF using WeasyPrint
                font_config = FontConfiguration()
                html_doc = HTML(filename=temp_html_path)
                html_doc.write_pdf(output_path, font_config=font_config)
                
                logger.info(f"✓ PDF generated successfully using {self.name} fallback: {output_path}")
                return True
                
            finally:
                # Clean up temporary file
                if os.path.exists(temp_html_path):
                    os.unlink(temp_html_path)
                    
        except ImportError as e:
            logger.error(f"✗ WeasyPrint not available: {e}")
            return False
        except Exception as e:
            logger.error(f"✗ WeasyPrint PDF generation failed: {e}", exc_info=True)
            return False
    
    def get_supported_features(self) -> Dict[str, bool]:
        """Return WeasyPrint supported features."""
        return {
            "text_rendering": False,  # Known issues with text rendering
            "searchable_text": False,  # Text often not searchable
            "workflow_diagrams": True,
            "complex_layouts": True,
            "professional_quality": False,  # Text rendering issues
            "no_external_deps": False  # Requires external dependencies
        }

class PDFGeneratorFactory:
    """Factory for creating PDF generators with fallback support."""
    
    def __init__(self):
        self.generators = []
        self._initialize_generators()
    
    def _initialize_generators(self):
        """Initialize available PDF generators based on environment configuration."""
        # Read environment configuration
        pdf_engine = os.environ.get("PDF_ENGINE", "weasyprint").lower()
        pdf_fallback = os.environ.get("PDF_FALLBACK", "true").lower() == "true"
        
        logger.info(f"📋 PDF Configuration: Engine={pdf_engine}, Fallback={pdf_fallback}")
        
        # Initialize primary generator based on environment
        primary_generator = None
        fallback_generator = None
        
        if pdf_engine == "reportlab":
            try:
                primary_generator = ReportLabGenerator()
                logger.info("✓ ReportLab generator initialized as primary")
            except ImportError:
                logger.warning("⚠ ReportLab not available for primary engine")
        elif pdf_engine == "weasyprint":
            try:
                primary_generator = WeasyPrintGenerator()
                logger.info("✓ WeasyPrint generator initialized as primary")
            except ImportError:
                logger.warning("⚠ WeasyPrint not available for primary engine")
        else:
            logger.warning(f"⚠ Invalid PDF_ENGINE '{pdf_engine}', defaulting to 'weasyprint'")
            try:
                primary_generator = WeasyPrintGenerator()
                logger.info("✓ WeasyPrint generator initialized as primary (default)")
            except ImportError:
                logger.warning("⚠ WeasyPrint not available for default engine")
        
        # Initialize fallback generator if enabled
        if pdf_fallback:
            if pdf_engine == "reportlab":
                # Try WeasyPrint as fallback
                try:
                    fallback_generator = WeasyPrintGenerator()
                    logger.info("✓ WeasyPrint generator initialized as fallback")
                except ImportError:
                    logger.warning("⚠ WeasyPrint not available for fallback")
            elif pdf_engine == "weasyprint":
                # Try ReportLab as fallback
                try:
                    fallback_generator = ReportLabGenerator()
                    logger.info("✓ ReportLab generator initialized as fallback")
                except ImportError:
                    logger.warning("⚠ ReportLab not available for fallback")
        
        # Build generators list in priority order
        self.generators = []
        if primary_generator:
            self.generators.append(primary_generator)
        if fallback_generator:
            self.generators.append(fallback_generator)
        
        if not self.generators:
            raise RuntimeError("No PDF generators available!")
        
        logger.info(f"📋 Final generator order: {[gen.name for gen in self.generators]}")
    
    def get_generator(self, preferred_type: Optional[str] = None) -> PDFGenerator:
        """
        Get the best available PDF generator.
        
        Args:
            preferred_type: Preferred generator type ('reportlab' or 'weasyprint')
            
        Returns:
            PDFGenerator: Best available generator
        """
        if preferred_type:
            for generator in self.generators:
                if generator.name.lower() == preferred_type.lower():
                    return generator
        
        # Return first available (highest priority)
        return self.generators[0]
    
    def generate_pdf_with_fallback(self,
                                 template_data: Dict[str, Any],
                                 output_path: str,
                                 workflow_diagram: Optional[bytes] = None,
                                 preferred_generator: Optional[str] = None) -> Dict[str, Any]:
        """
        Generate PDF with automatic fallback if primary generator fails.
        
        Args:
            template_data: Data to render in the template
            output_path: Path where PDF should be saved
            workflow_diagram: Optional PNG bytes for workflow diagram
            preferred_generator: Preferred generator type
            
        Returns:
            Dict with generation results
        """
        result = {
            "success": False,
            "generator_used": None,
            "fallback_used": False,
            "error": None,
            "output_path": output_path
        }
        
        # Try preferred generator first
        if preferred_generator:
            try:
                generator = self.get_generator(preferred_generator)
                logger.info(f"🎯 Attempting PDF generation with preferred generator: {generator.name}")
                
                if generator.generate_pdf(template_data, output_path, workflow_diagram):
                    result["success"] = True
                    result["generator_used"] = generator.name
                    return result
                else:
                    logger.warning(f"⚠ Preferred generator {generator.name} failed, trying fallback")
                    result["fallback_used"] = True
            except Exception as e:
                logger.warning(f"⚠ Preferred generator {preferred_generator} failed: {e}")
                result["fallback_used"] = True
        
        # Try all available generators in order
        for i, generator in enumerate(self.generators):
            try:
                logger.info(f"🔄 Attempting PDF generation with {generator.name} (attempt {i+1})")
                
                if generator.generate_pdf(template_data, output_path, workflow_diagram):
                    result["success"] = True
                    result["generator_used"] = generator.name
                    if i > 0:  # Not the first generator
                        result["fallback_used"] = True
                    return result
                    
            except Exception as e:
                logger.error(f"✗ {generator.name} failed: {e}")
                if i == len(self.generators) - 1:  # Last generator
                    result["error"] = f"All generators failed. Last error: {e}"
        
        return result
    
    def get_available_generators(self) -> list:
        """Get list of available generator names."""
        return [gen.name for gen in self.generators]
    
    def get_generator_info(self) -> Dict[str, Dict[str, Any]]:
        """Get detailed information about all available generators."""
        info = {}
        for i, generator in enumerate(self.generators):
            info[generator.name] = {
                "version": generator.version,
                "features": generator.get_supported_features(),
                "priority": "Primary" if i == 0 else "Fallback",
                "order": i + 1
            }
        return info

# Global factory instance
pdf_generator_factory = PDFGeneratorFactory()

def generate_pdf_report_dual_lane(template_data: Dict[str, Any],
                                output_path: str,
                                workflow_diagram: Optional[bytes] = None,
                                preferred_generator: Optional[str] = None) -> Dict[str, Any]:
    """
    Convenience function for dual-lane PDF generation.
    
    Args:
        template_data: Data to render in the template
        output_path: Path where PDF should be saved
        workflow_diagram: Optional PNG bytes for workflow diagram
        preferred_generator: Preferred generator type ('reportlab' or 'weasyprint')
        
    Returns:
        Dict with generation results
    """
    return pdf_generator_factory.generate_pdf_with_fallback(
        template_data, output_path, workflow_diagram, preferred_generator
    )
