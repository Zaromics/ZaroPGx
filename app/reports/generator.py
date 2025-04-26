import os
import json
import logging
from datetime import datetime
from typing import List, Dict, Any
from weasyprint import HTML, CSS
from jinja2 import Environment, FileSystemLoader, select_autoescape
from app.pharmcat.pharmcat_client import normalize_pharmcat_results

# Version
__version__ = "1.0.0"

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
    report_path: str
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
        report_data = {
            "patient_id": patient_id,
            "report_id": report_id,
            "report_date": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
            "diplotypes": diplotypes,
            "recommendations": recommendations,
            "disclaimer": get_disclaimer()
        }
        
        # Load and render the HTML template
        template = env.get_template("report_template.html")
        html_content = template.render(**report_data)
        
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
    DISCLAIMER: This pharmacogenomic report is for informational purposes only and is not intended
    to be used as a substitute for professional medical advice, diagnosis, or treatment. The recommendations
    in this report are based on guidelines from the Clinical Pharmacogenetics Implementation Consortium (CPIC)
    and are subject to change as new research becomes available.
    
    The results should be interpreted by a healthcare professional in the context of the patient's
    clinical situation. Medication decisions should never be made solely based on this report without
    consulting a qualified healthcare provider.
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
    output_path: str
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
        
        # Prepare the report data
        report_data = {
            "patient_id": patient_id,
            "report_id": report_id,
            "report_date": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
            "diplotypes": diplotypes,
            "recommendations": template_recommendations,
            "organized_recommendations": json.dumps(organize_gene_drug_recommendations(template_recommendations)),
            "disclaimer": get_disclaimer()
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
        Dict containing file paths for HTML and PDF reports
    """
    logger.info("Generating report from PharmCAT results")
    
    # Ensure normalized data is used consistently
    normalized_results = normalize_pharmcat_results(pharmcat_results)
    data = normalized_results["data"]
    
    # Map recommendations to template-compatible format
    template_recommendations = map_recommendations_for_template(data.get("drugRecommendations", []))
    
    # Prepare the template data
    template_data = {
        "patient": patient_info or {},
        "report_date": datetime.now().strftime("%Y-%m-%d"),
        "genes": data.get("genes", []),
        "diplotypes": data.get("genes", []),  # For compatibility with template
        "recommendations": template_recommendations,  # Use mapped recommendations
        "drug_recommendations": data.get("drugRecommendations", []),  # Keep original for reference
        "version": __version__
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
    
    # Create a unique filename based on report_id
    base_filename = f"{report_id}_pgx_report"
    
    try:
        # Generate both standard HTML report and interactive HTML report
        html_path = os.path.join(report_dir, f"{base_filename}.html")
        interactive_html_path = os.path.join(report_dir, f"{base_filename}_interactive.html")
        
        # Generate standard HTML report
        env = Environment(
            loader=FileSystemLoader(os.path.join(os.path.dirname(__file__), "templates")),
            autoescape=select_autoescape(['html', 'xml'])
        )
        template = env.get_template("report_template.html")
        html_content = template.render(**template_data)
        
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        logger.info(f"HTML report generated: {html_path}")
        
        # Generate interactive HTML report
        create_interactive_html_report(
            patient_id=patient_id,
            report_id=report_id,
            diplotypes=data.get("genes", []),
            recommendations=template_recommendations,
            output_path=interactive_html_path
        )
        logger.info(f"Interactive HTML report generated: {interactive_html_path}")
        
        # Generate PDF report from the HTML
        pdf_path = os.path.join(report_dir, f"{base_filename}.pdf")
        html = HTML(string=html_content)
        html.write_pdf(pdf_path)
        logger.info(f"PDF report generated: {pdf_path}")
        
        # Return file paths that include the report directory
        server_html_path = f"/reports/{report_id}/{base_filename}.html"
        server_interactive_html_path = f"/reports/{report_id}/{base_filename}_interactive.html"
        server_pdf_path = f"/reports/{report_id}/{base_filename}.pdf"
        
        # Update the normalized results with the report paths
        normalized_results["data"]["html_report_url"] = server_html_path
        normalized_results["data"]["interactive_html_report_url"] = server_interactive_html_path
        normalized_results["data"]["pdf_report_url"] = server_pdf_path
        
        return {
            "html_path": server_html_path,
            "interactive_html_path": server_interactive_html_path,
            "pdf_path": server_pdf_path,
            "normalized_results": normalized_results
        }
    
    except Exception as e:
        logger.error(f"Error generating report: {str(e)}")
        raise ReportGenerationError(f"Failed to generate report: {str(e)}") 