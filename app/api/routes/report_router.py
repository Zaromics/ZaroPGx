import logging
import os
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.db import get_db, get_guidelines_for_gene_drug
from app.api.models import DrugRecommendation

from ..utils.security import get_optional_user

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize router
router = APIRouter(prefix="/reports", tags=["reports"])

_RETIRED_REPORT_STUB_DETAIL = (
    "This /reports stub is retired. Real report generation and delivery live under "
    "/upload (e.g. POST /upload/genomic-data, GET /upload/reports/job/{job_id}, "
    "GET /upload/reports/download/{patient_id}) and GET /reports/{patient_id}/{filename}."
)


@router.post("/generate")
async def generate_report(
    current_user: str = Depends(get_optional_user),
):
    """
    Retired stub. Report generation runs via the upload/Nextflow pipeline, not this route.
    """
    raise HTTPException(status_code=501, detail=_RETIRED_REPORT_STUB_DETAIL)


@router.get("/{report_id}/status")
async def get_report_status(
    report_id: str,
    current_user: str = Depends(get_optional_user),
):
    """
    Retired stub. Previously always returned status "completed" with no DB lookup.
    """
    raise HTTPException(
        status_code=501,
        detail=(
            f"GET /reports/{report_id}/status is retired (it always claimed "
            f"'completed'). Use GET /upload/status/{{job_id}} or "
            f"GET /api/v1/workflows/{{workflow_id}} instead. {_RETIRED_REPORT_STUB_DETAIL}"
        ),
    )


@router.get("/{report_id}/download")
async def download_report(
    report_id: str,
    current_user: str = Depends(get_optional_user),
):
    """
    Retired stub. Previously returned a JSON pointer to an unserved /static/reports path.
    """
    raise HTTPException(
        status_code=501,
        detail=(
            f"GET /reports/{report_id}/download is retired. Use "
            f"GET /upload/reports/download/{{patient_id}} or "
            f"GET /reports/{{patient_id}}/{{filename}}. {_RETIRED_REPORT_STUB_DETAIL}"
        ),
    )


@router.get("/recommendations/{patient_id}", response_model=List[DrugRecommendation])
async def get_drug_recommendations(
    patient_id: str,
    drug: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_optional_user),
):
    """
    Get drug recommendations for a patient based on their genetic profile.
    Optionally filter by specific drug.
    """
    try:
        # Load PharmCAT results from the data directory
        # Try multiple path resolution strategies
        pharmcat_data_dir = None

        # Strategy 1: Use environment variable if set
        if os.getenv("PHARMCAT_DATA_DIR"):
            pharmcat_data_dir = os.getenv("PHARMCAT_DATA_DIR")

        # Strategy 2: Look relative to current working directory
        if not pharmcat_data_dir or not os.path.exists(pharmcat_data_dir):
            pharmcat_data_dir = os.path.join(
                os.getcwd(), "data", "pharmcat_final_results"
            )

        # Strategy 3: Look relative to this file (for development)
        if not os.path.exists(pharmcat_data_dir):
            project_root = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            )
            pharmcat_data_dir = os.path.join(
                project_root, "data", "pharmcat_final_results"
            )

        # Strategy 4: Look in common path
        if not os.path.exists(pharmcat_data_dir):
            docker_paths = ["/data/reports"]
            for path in docker_paths:
                if os.path.exists(path):
                    pharmcat_data_dir = path
                    break

        logger.info(
            f"Using PharmCAT data directory for recommendations: {pharmcat_data_dir}"
        )
        logger.info(f"Directory exists: {os.path.exists(pharmcat_data_dir)}")

        # Check if PharmCAT results exist for this patient/sample
        sample_files = [
            f"{patient_id}.mm2.sortdup.bqsr.hc.report.json",
            f"{patient_id}.mm2.sortdup.bqsr.hc.report.tsv",
            f"{patient_id}.mm2.sortdup.bqsr.hc.phenotype.json",
        ]

        pharmcat_tsv_path = None

        for filename in sample_files:
            file_path = os.path.join(pharmcat_data_dir, filename)
            if os.path.exists(file_path) and filename.endswith(".tsv"):
                pharmcat_tsv_path = file_path
                logger.info(f"Found PharmCAT TSV file: {filename}")
                break

        if not pharmcat_tsv_path:
            logger.warning(f"No PharmCAT results found for patient {patient_id}.")
            # Do not fall back to mock data if PharmCAT results are not found
            diplotypes = []
        else:
            # Load PharmCAT data
            logger.info("Loading real PharmCAT results for drug recommendations")
            import pandas as pd

            # Load TSV data
            df = pd.read_csv(pharmcat_tsv_path, sep="\t", skiprows=1)

            # Convert to diplotypes format
            diplotypes = []
            for _, row in df.iterrows():
                if pd.notna(row["Gene"]) and pd.notna(row["Source Diplotype"]):
                    diplotypes.append(
                        {
                            "gene": row["Gene"],
                            "diplotype": row["Source Diplotype"],
                            "phenotype": (
                                row["Phenotype"]
                                if pd.notna(row["Phenotype"])
                                else "Unknown"
                            ),
                            "activity_score": (
                                row["Activity Score"]
                                if pd.notna(row["Activity Score"])
                                else None
                            ),
                        }
                    )

            logger.info(
                f"Loaded {len(diplotypes)} real pharmacogenomic findings for recommendations"
            )

        # Get drug recommendations based on diplotypes
        recommendations = []
        for diplotype in diplotypes:
            gene = diplotype["gene"]
            # In a real implementation, this would check the specific allele combination
            if drug:
                drug_guidelines = get_guidelines_for_gene_drug(db, gene, drug)
            else:
                drug_guidelines = get_guidelines_for_gene_drug(db, gene, None)

            for guideline in drug_guidelines:
                recommendations.append(
                    DrugRecommendation(
                        drug=guideline.drug,
                        gene=gene,
                        guideline=f"CPIC Guideline for {gene} and {guideline.drug}",
                        recommendation=guideline.recommendation,
                        # Do not invent evidence strength / PMIDs — cpic.guidelines has neither
                        classification=None,
                        literature_references=None,
                    )
                )

        return recommendations
    except Exception as e:
        logger.error(f"Error getting drug recommendations: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error getting recommendations: {str(e)}"
        )


@router.post("/{report_id}/export-to-fhir")
async def export_report_to_fhir(
    report_id: str,
    target_fhir_url: Optional[str] = None,
    patient_info: Optional[dict] = None,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_optional_user),
):
    """
    Retired: this endpoint previously POSTed fabricated diplotypes (e.g. CYP2D6 *1/*4)
    to a live FHIR server. Use the real FHIR export routes under /fhir/* instead
    (see app.api.routes.fhir_export_router / fhir_export_service).
    """
    raise HTTPException(
        status_code=501,
        detail=(
            f"POST /reports/{report_id}/export-to-fhir is retired because it "
            "fabricated clinical genotypes. Use /fhir/export/run/{run_id}, "
            "/fhir/export/workflow/{workflow_id}, or /fhir/save/* instead."
        ),
    )
