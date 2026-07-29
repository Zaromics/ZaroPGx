import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

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
            f"GET /api/v1/jobs/{{job_id}} instead. {_RETIRED_REPORT_STUB_DETAIL}"
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


@router.get("/recommendations/{patient_id}")
async def get_drug_recommendations(
    patient_id: str,
    drug: Optional[str] = None,
    current_user: str = Depends(get_optional_user),
):
    """
    Retired stub. Previously matched toy cpic.guidelines rows by gene only against
    legacy PharmCAT TSV filenames — not the live report pipeline.
    """
    raise HTTPException(
        status_code=501,
        detail=(
            f"GET /reports/recommendations/{patient_id} is retired. "
            "Use generated report artifacts under GET /reports/{patient_id}/{filename} "
            "or FHIR export under /fhir/* instead."
        ),
    )


@router.post("/{report_id}/export-to-fhir")
async def export_report_to_fhir(
    report_id: str,
    target_fhir_url: Optional[str] = None,
    patient_info: Optional[dict] = None,
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
