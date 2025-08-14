#!/usr/bin/env python3
"""
Dual-Lane PDF Generation System

This module provides an abstracted interface for PDF generation with two backends:
1. ReportLab (Primary) - Professional PDF generation with excellent text rendering
2. WeasyPrint (Fallback) - HTML-to-PDF conversion for complex layouts

Both backends implement the same interface, making them interchangeable.
"""

import os
import logging
import base64
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, Union
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
            from reportlab.lib.pagesizes import A4
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, PageBreak
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.lib.units import mm
            from reportlab.lib import colors
            from reportlab.lib.enums import TA_CENTER, TA_LEFT
            from reportlab.pdfgen import canvas
            from reportlab.lib.utils import ImageReader
            from io import BytesIO
            
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
            
            # Title
            if 'sample_id' in template_data:
                title = f"Pharmacogenomic Report - Sample {template_data['sample_id']}"
            else:
                title = "Pharmacogenomic Report"
            story.append(Paragraph(title, title_style))
            story.append(Spacer(1, 12))
            
            # Sample Information
            if 'sample_id' in template_data:
                story.append(Paragraph("Sample Information", heading_style))
                story.append(Paragraph(f"<b>Sample ID:</b> {template_data['sample_id']}", normal_style))
                if 'file_type' in template_data:
                    story.append(Paragraph(f"<b>File Type:</b> {template_data['file_type']}", normal_style))
                story.append(Spacer(1, 12))
            
            # Workflow Diagram
            story.append(Paragraph("Analysis Workflow", heading_style))
            story.append(Spacer(1, 6))
            
            # Check if we have SVG workflow content in template data or workflow_diagram parameter
            workflow_text_extracted = False
            
            # First, try to extract from workflow_diagram parameter (SVG bytes)
            if workflow_diagram and isinstance(workflow_diagram, bytes):
                try:
                    svg_content = workflow_diagram.decode('utf-8')
                    # Extract text content from SVG for ReportLab
                    import re
                    text_matches = re.findall(r'<text[^>]*>(.*?)</text>', svg_content, re.DOTALL)
                    if text_matches:
                        # Create a text-based workflow representation
                        workflow_text = "Workflow Steps:\n"
                        for i, text in enumerate(text_matches, 1):
                            workflow_text += f"{i}. {text.strip()}\n"
                        story.append(Paragraph(workflow_text, normal_style))
                        logger.info(f"✓ Workflow text extracted from SVG bytes using {self.name}")
                        workflow_text_extracted = True
                except Exception as e:
                    logger.warning(f"Failed to extract text from workflow_diagram bytes: {e}")
            
            # If no text extracted from workflow_diagram, try template_html
            if not workflow_text_extracted and 'template_html' in template_data:
                # Try to extract workflow SVG content from the template HTML
                import re
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
            
            # Add all other template data that contains the actual pharmacogenomic information
            # This ensures we get the full report content, not just basic info
            for key, value in template_data.items():
                if key not in ['sample_id', 'file_type', 'analysis_results', 'workflow_diagram'] and value:
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
            
            # Build PDF
            doc.build(story)
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
            from weasyprint import HTML, CSS
            from weasyprint.text.fonts import FontConfiguration
            from jinja2 import Template
            import tempfile
            
            logger.info(f"🔄 Using {self.name} as fallback for PDF generation")
            
            # Use the actual template data instead of hardcoded basic template
            # This ensures we get the full pharmacogenomic report content
            if 'template_html' in template_data:
                # Use the actual template HTML directly - this contains the SVG workflow diagram
                html_content = template_data['template_html']
                logger.info(f"✓ Using actual template HTML for {self.name}")
                
                # Add PDF-specific CSS optimizations for better SVG workflow diagram rendering
                pdf_optimization_css = """
                <style>
                    /* PDF-specific optimizations for workflow diagrams */
                    @page { 
                        size: A4; 
                        margin: 18mm; 
                    }
                    
                    /* Ensure SVG workflow diagrams render properly in PDF */
                    .workflow-figure {
                        page-break-inside: avoid !important;
                        break-inside: avoid !important;
                        margin: 20px 0 !important;
                        padding: 15px !important;
                        border: 2px solid #000 !important;
                        background: #ffffff !important;
                        text-align: center !important;
                        max-width: 100% !important;
                    }
                    
                    .workflow-figure svg {
                        max-width: 100% !important;
                        height: auto !important;
                        display: block !important;
                        margin: 0 auto !important;
                        background: #ffffff !important;
                    }
                    
                    .workflow-figure img {
                        max-width: 100% !important;
                        height: auto !important;
                        display: block !important;
                        margin: 0 auto !important;
                    }
                    
                    /* Ensure SVG text is visible in PDF */
                    .workflow-figure text,
                    .workflow-figure tspan {
                        font-family: Arial, sans-serif !important;
                        font-size: 12px !important;
                        fill: #000000 !important;
                        color: #000000 !important;
                    }
                    
                    /* Additional SVG text visibility improvements */
                    .workflow-figure svg text,
                    .workflow-figure svg tspan {
                        font-family: Arial, sans-serif !important;
                        font-size: 12px !important;
                        fill: #000000 !important;
                        color: #000000 !important;
                        font-weight: normal !important;
                    }
                    
                    /* Ensure SVG elements are properly sized */
                    .workflow-figure svg {
                        width: 100% !important;
                        height: auto !important;
                        max-height: 400px !important;
                    }
                    
                    /* Print-specific styles */
                    @media print {
                        .workflow-figure {
                            page-break-inside: avoid !important;
                            break-inside: avoid !important;
                        }
                        
                        .workflow-figure * {
                            position: relative !important;
                            z-index: 1 !important;
                        }
                    }
                </style>
                """
                
                # Insert the CSS optimization into the HTML head
                if '<head>' in html_content:
                    html_content = html_content.replace('<head>', f'<head>{pdf_optimization_css}')
                else:
                    # If no head tag, add one at the beginning
                    html_content = f'<head>{pdf_optimization_css}</head>{html_content}'
                
            else:
                # Fallback to basic template if no actual template data
                logger.warning(f"⚠ No actual template HTML found, using basic template for {self.name}")
                
                # Basic fallback template for when no actual template is available
                html_template = """
                <!DOCTYPE html>
                <html>
                <head>
                    <meta charset="UTF-8">
                    <title>Pharmacogenomic Report</title>
                    <style>
                        @page { size: A4; margin: 18mm; }
                        body { font-family: Arial, sans-serif; font-size: 12px; }
                        .title { font-size: 18px; font-weight: bold; text-align: center; color: #2c3e50; margin-bottom: 20px; }
                        .section { margin-bottom: 15px; }
                        .section-title { font-size: 14px; font-weight: bold; color: #2c3e50; margin-bottom: 8px; }
                        .content { margin-bottom: 6px; }
                        .workflow-img { max-width: 100%; height: auto; text-align: center; margin: 15px 0; }
                        .footer { text-align: center; color: #7f8c8d; font-size: 9px; margin-top: 30px; }
                    </style>
                </head>
                <body>
                    <div class="title">Pharmacogenomic Report</div>
                    
                    <div class="section">
                        <div class="section-title">Sample Information</div>
                        <div class="content"><strong>Sample ID:</strong> {{ sample_id or 'N/A' }}</div>
                        <div class="content"><strong>File Type:</strong> {{ file_type or 'N/A' }}</div>
                    </div>
                    
                    <div class="section">
                        <div class="section-title">Analysis Workflow</div>
                        {% if workflow_diagram %}
                        <div class="workflow-img">
                            <img src="data:image/png;base64,{{ workflow_diagram_b64 }}" alt="Workflow Diagram">
                        </div>
                        {% else %}
                        <div class="content">Workflow diagram not available</div>
                        {% endif %}
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
                template = Template(html_template)
                html_content = template.render(**template_data_copy)
            
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
        """Initialize available PDF generators in priority order."""
        # Try ReportLab first (primary)
        try:
            import reportlab
            self.generators.append(ReportLabGenerator())
            logger.info("✓ ReportLab generator initialized")
        except ImportError:
            logger.warning("⚠ ReportLab not available")
        
        # Try WeasyPrint as fallback
        try:
            import weasyprint
            self.generators.append(WeasyPrintGenerator())
            logger.info("✓ WeasyPrint generator initialized")
        except ImportError:
            logger.warning("⚠ WeasyPrint not available")
        
        if not self.generators:
            raise RuntimeError("No PDF generators available!")
    
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
        for generator in self.generators:
            info[generator.name] = {
                "version": generator.version,
                "features": generator.get_supported_features(),
                "priority": "Primary" if generator.name == "ReportLab" else "Fallback"
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
