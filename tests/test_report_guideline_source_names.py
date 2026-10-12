"""A recommendation names the guideline it comes from, in both report lanes.

The database lane stores PharmCAT's code for a drug's source (CPIC_GUIDELINE,
DPWG_GUIDELINE, FDA_LABEL, FDA_ASSOC) and mapped it through a table that knew only
"CPIC", "DPWG" and "FDA". Nothing matched, so every recommendation in a real report
lost its source and the heading read "<genes> - General"; recommendations from two
guidelines with the same text could also merge under one heading, since the source
is part of the template's grouping key. The JSON lane used PharmCAT's section name
("CPIC Guideline Annotation"). Both now do, and FHIR export reads that name.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import app.reports.generator as generator_module
from app.pharmcat.report_json import GUIDELINE_SOURCE_NAMES, guideline_source_name

EXAMPLE = (
    Path(__file__).resolve().parents[1] / "test_data" / "pharmcat.example.report.json"
)


def test_each_pharmcat_code_names_its_section():
    """Read off PharmCAT's own example: each drug's source and the section it is in."""
    report = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    pairs = {
        (drug.get("source"), section)
        for section, drugs in report["drugs"].items()
        for drug in drugs.values()
    }
    assert pairs == {(code, name) for code, name in GUIDELINE_SOURCE_NAMES.items()}
    for code, name in pairs:
        assert guideline_source_name(code) == name
        assert guideline_source_name(name) == name  # a name passes through
    assert guideline_source_name(None) == ""


def _db_rows():
    annotation = {"dosingInformation": True, "lookupKey": [{"CYP2C19": "*1/*2"}]}
    return [
        {
            "drug_name": "clopidogrel",
            "gene_symbol": "CYP2C19",
            "drug_recommendation": "Use an alternative.",
            "classification": "Strong",
            "guideline_source": source,
            "recommendation_data": annotation,
        }
        for source in ("CPIC_GUIDELINE", "DPWG_GUIDELINE")
    ]


def test_the_database_lane_names_the_source():
    from app.services.pharmcat_data_service import PharmCATDataService

    service = PharmCATDataService.__new__(PharmCATDataService)
    grouped = service._transform_drug_recommendations_for_reports(_db_rows())
    sources = sorted(r["guideline_source"] for r in grouped[0]["recommendations"])
    assert sources == ["CPIC Guideline Annotation", "DPWG Guideline Annotation"]
    mapped = generator_module.map_recommendations_for_template(grouped)
    assert sorted(m["guideline_source"] for m in mapped) == sources


def _job(session):
    from app.api.db import Job
    from app.api.models import JobCreate
    from app.services.job_service import JobService

    job = JobService(session).create_job(
        JobCreate(workflow_type="genomic_analysis", name="guideline-names")
    )
    session.commit()
    row = session.query(Job).filter(Job.id == job.id).first()
    row.job_metadata = {"file_analysis": {"file_type": "vcf"}, "workflow": {}}
    session.commit()
    return str(job.id)


def test_a_real_report_s_headings_name_the_guideline(
    session_factory, monkeypatch, tmp_path
):
    """Through the database lane, as every report from an upload is built."""
    from app.services.pharmcat_data_service import PharmCATDataService

    service = PharmCATDataService.__new__(PharmCATDataService)
    grouped = service._transform_drug_recommendations_for_reports(_db_rows())
    monkeypatch.setattr(
        generator_module.PharmCATDataService,
        "get_pharmcat_data_for_workflow",
        lambda self, job: {
            "genes": [
                {
                    "gene": "CYP2C19",
                    "diplotype": "*1/*2",
                    "phenotype": "Intermediate Metabolizer",
                }
            ],
            "drugRecommendations": grouped,
            "file_type": "vcf",
            "workflow": {},
        },
    )
    for key in list(generator_module.REPORT_CONFIG):
        if key.startswith(("write_", "show_")):
            monkeypatch.setitem(
                generator_module.REPORT_CONFIG, key, key == "write_html"
            )
    session = session_factory()
    try:
        result = generator_module.generate_report(
            {"data": {"genes": [], "drugRecommendations": []}},
            str(tmp_path),
            {"id": "p1"},
            job_id=_job(session),
            db_session=session,
        )
    finally:
        session.rollback()
        session.close()
    page = (tmp_path / result["html_path"].split("/")[-1]).read_text(encoding="utf-8")
    headings = re.findall(r"<h4>([^<]*CYP2C19[^<]*)</h4>", page)
    assert "CYP2C19 - CPIC Guideline Annotation" in headings
    assert "CYP2C19 - DPWG Guideline Annotation" in headings
    assert not any("General" in h for h in headings)


@pytest.mark.parametrize(
    "source, url",
    [
        ("CPIC Guideline Annotation", "https://www.clinpgx.org/guidelineAnnotations"),
        ("CPIC_GUIDELINE", "https://www.clinpgx.org/guidelineAnnotations"),
        ("C", "https://www.clinpgx.org/guidelineAnnotations"),
        ("DPWG Guideline Annotation", "https://www.pharmgkb.org/page/dpwg"),
        (
            "FDA PGx Association",
            "https://www.fda.gov/drugs/science-and-research-drugs/table-pharmacogenomic-biomarkers-drug-labeling",
        ),
    ],
)
def test_fhir_links_the_guideline_by_its_name(source, url):
    from app.services.fhir_export_service import FHIRExportService

    service = FHIRExportService.__new__(FHIRExportService)
    assert service._get_guideline_url(source, "clopidogrel") == url


def test_fhir_displays_a_section_name_as_it_stands():
    """ "CPIC Guideline Annotation Guideline" was the JSON lane's display."""
    from app.services.fhir_export_service import FHIRExportService

    service = FHIRExportService.__new__(FHIRExportService)
    observation = service._create_therapeutic_implication(
        {
            "drug": "clopidogrel",
            "genes": ["CYP2C19"],
            "recommendations": [
                {
                    "recommendation": "Use an alternative.",
                    "guideline_source": "CPIC Guideline Annotation",
                }
            ],
        },
        "Patient/p1",
        [],
        "run-1",
    )
    displays = [
        e["valueRelatedArtifact"]["display"]
        for e in observation.get("extension", [])
        if "valueRelatedArtifact" in e
    ]
    assert displays == ["CPIC Guideline Annotation"]
