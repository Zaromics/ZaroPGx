"""Push a bundle to a real HAPI FHIR server twice; validate it if the server can.

Opt-in: set ZAROPGX_FHIR_TEST_URL to the base URL of a throwaway HAPI server,
never one holding data you keep (the test writes a synthetic patient to it):

    docker run -d --rm --name fhirtest -p 127.0.0.1:18090:8080 \\
      -e hapi.fhir.implementationguides.gr.name=hl7.fhir.uv.genomics-reporting \\
      -e hapi.fhir.implementationguides.gr.version=3.0.0 \\
      -e hapi.fhir.implementationguides.gr.installMode=STORE_AND_INSTALL \\
      -e hapi.fhir.implementationguides.gr.fetchDependencies=true \\
      hapiproject/hapi:v8.12.0-2
    ZAROPGX_FHIR_TEST_URL=http://127.0.0.1:18090/fhir \\
      pytest tests/test_fhir_push_hapi.py

The IG settings are only needed for the validation test, which skips on a server
that does not have the Genomics Reporting IG.
"""

from __future__ import annotations

import copy
import json
import os
import uuid
from unittest.mock import MagicMock

import httpx
import pytest

from app.services.fhir import push
from app.services.fhir_export_service import FHIRExportService

pytestmark = pytest.mark.e2e

GENOTYPE_PROFILE = (
    "http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/genotype"
)

DATA = {
    "pharmcat_version": "3.4.0",
    "total_genes": 2,
    "actionable_findings": [{"gene": "CYP2C19"}],
    "genes": [
        {
            "gene": "CYP2C19",
            "diplotype": "*1/*2",
            "phenotype": "Intermediate Metabolizer",
            "allele1": "*1",
            "allele2": "*2",
        },
        {
            "gene": "CYP2D6",
            "diplotype": "*1/*4",
            "phenotype": "Intermediate Metabolizer",
            "activity_score": "1.0",
        },
    ],
    "drugRecommendations": [
        {
            "drug": "clopidogrel",
            "genes": ["CYP2C19"],
            "recommendations": [
                {
                    "recommendation": "Use an alternative antiplatelet agent.",
                    "classification": "Strong",
                    "guideline_source": "CPIC",
                }
            ],
        }
    ],
}


@pytest.fixture(scope="module")
def server():
    url = os.environ.get("ZAROPGX_FHIR_TEST_URL", "").rstrip("/")
    if not url:
        pytest.skip("Set ZAROPGX_FHIR_TEST_URL to a throwaway HAPI FHIR server")
    try:
        httpx.get(f"{url}/metadata", timeout=30).raise_for_status()
    except httpx.HTTPError as exc:
        pytest.skip(f"no FHIR server at {url}: {exc}")
    return url


def _bundle(patient_id, report_id, data=DATA):
    result = FHIRExportService(MagicMock()).export_pgx_report(
        run_id=report_id,
        patient_info={"id": patient_id},
        pharmcat_data=data,
        output_format="json",
    )
    assert result["success"], result
    return json.loads(result["content"])


def _count(server, query):
    # no-cache: HAPI reuses a search's results for a minute by default.
    response = httpx.get(
        f"{server}/{query}&_summary=count",
        headers={"Cache-Control": "no-cache"},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()["total"]


def test_pushing_twice_keeps_one_copy(server):
    patient_id = f"pytest-{uuid.uuid4()}"
    report_id = f"pytest-report-{uuid.uuid4()}"
    bundle = _bundle(patient_id, report_id)

    job_id = f"pytest-job-{uuid.uuid4()}"
    first = push.push_bundle(bundle, job_id, server_url=server)
    second = push.push_bundle(bundle, job_id, server_url=server)

    assert first["entries"] == second["entries"] == len(bundle["entry"])
    assert all(c["existing"] == 0 for c in first["resources"].values())
    assert all(c["created"] == 0 for c in second["resources"].values())
    assert first["diagnostic_report"].split("/_history")[0] == (
        second["diagnostic_report"].split("/_history")[0]
    )

    assert (
        _count(server, f"Patient?identifier=urn:zaropgx:patient-id|{patient_id}") == 1
    )
    assert (
        _count(server, f"DiagnosticReport?identifier=urn:zaropgx:report-id|{report_id}")
        == 1
    )

    report_url = first["diagnostic_report"].split("/_history")[0]
    report = httpx.get(f"{server}/{report_url}", timeout=60).json()
    patient_url = first["patient"].split("/_history")[0]
    assert report["subject"]["reference"] == patient_url
    # 2 genotypes, their 2 phenotypes, 1 drug.
    assert len(report["result"]) == 5
    for result in report["result"]:
        observation = httpx.get(f"{server}/{result['reference']}", timeout=60).json()
        assert observation["subject"]["reference"] == patient_url

    # A regenerated bundle of the same report (new timestamps) updates in place.
    regenerated = _bundle(patient_id, report_id)
    third = push.push_bundle(regenerated, job_id, server_url=server)
    assert all(c["created"] == 0 for c in third["resources"].values())
    assert _count(server, f"Observation?subject={patient_url}") == 5
    # The phenotype, coded the IG's way, read back from the server.
    poor = httpx.get(
        f"{server}/Observation",
        params={
            "subject": patient_url,
            "component-value-concept": "http://loinc.org|LA10317-8",
        },
        headers={"Cache-Control": "no-cache"},
        timeout=60,
    ).json()
    assert sorted(e["resource"]["code"]["text"] for e in poor["entry"]) == [
        "CYP2C19 phenotype",
        "CYP2D6 phenotype",
    ]

    # Regenerated without the drug: its implication and recommendation go.
    smaller = copy.deepcopy(DATA)
    smaller["drugRecommendations"] = []
    fourth = push.push_bundle(
        _bundle(patient_id, report_id, smaller), job_id, server_url=server
    )
    assert fourth["resources"]["Observation"]["deleted"] == 1
    assert fourth["resources"]["Task"]["deleted"] == 1
    assert _count(server, f"Observation?subject={patient_url}") == 4
    assert _count(server, f"Task?subject={patient_url}") == 0
    tag = f"urn:zaropgx:job-id|{job_id}"
    assert _count(server, f"Observation?_tag={tag}") == 4
    report = httpx.get(f"{server}/{report_url}", timeout=60).json()
    assert len(report["result"]) == 4
    assert "extension" not in report or all(
        not x["url"].endswith("recommended-action") for x in report["extension"]
    )


def test_the_bundle_validates_against_the_genomics_reporting_ig(server):
    if _count(server, f"StructureDefinition?url={GENOTYPE_PROFILE}") == 0:
        pytest.skip("the server does not have the Genomics Reporting IG loaded")
    bundle = _bundle("pytest-validate", "pytest-validate-report")
    response = httpx.post(
        f"{server}/Bundle/$validate",
        content=json.dumps(bundle),
        headers={"Content-Type": "application/fhir+json"},
        timeout=300,
    )
    outcome = response.json()
    errors = [
        issue
        for issue in outcome.get("issue", [])
        if issue.get("severity") in ("error", "fatal")
    ]
    assert errors == []
