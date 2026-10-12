"""A run that assumed the reference allele says so under Alerts and Warnings.

PharmCAT's --missing-to-ref (or --absent-to-ref, --unspecified-to-ref) turns
positions the data never covered into reference calls, and can over-call normal
phenotypes. The report's only word on it was a paragraph of Methodology on the
cover page; Alerts and Warnings, the page for exactly this, was silent. The caution
is an alert now, built from the flags the run used. Methodology keeps the fact.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import app.reports.generator as generator_module
from app.utils.pharmcat_assume_ref import (
    assume_ref_alert,
    methodology_assume_ref_paragraph,
)

REPORT_JSON = (
    Path(__file__).resolve().parents[1]
    / "test_data"
    / "pharmcat.unphased.example.report.json"
)
CAUTION = "over-call *1/Reference and normal phenotypes"


@pytest.mark.parametrize(
    "absent, unspecified, flag, assumed",
    [
        (True, True, "--missing-to-ref", "Positions absent from the file, and"),
        (True, False, "--absent-to-ref", "Positions absent from the file were"),
        (False, True, "--unspecified-to-ref", "Positions the file lists without"),
    ],
)
def test_the_alert_names_the_flag_and_what_it_assumed(
    absent, unspecified, flag, assumed
):
    alert = assume_ref_alert(absent, unspecified)
    assert flag in alert and assumed in alert and CAUTION in alert
    methodology = methodology_assume_ref_paragraph(absent, unspecified)
    assert flag in methodology and CAUTION not in methodology
    assert "Alerts and Warnings" in methodology


def test_no_flag_no_alert():
    assert assume_ref_alert(False, False) is None


def _page(session_factory, monkeypatch, tmp_path, file_type, flags):
    from app.api.db import Job
    from app.api.models import JobCreate
    from app.services.job_service import JobService

    monkeypatch.setattr(
        generator_module.PharmCATDataService,
        "get_pharmcat_data_for_workflow",
        lambda self, job: None,
    )
    for key in list(generator_module.REPORT_CONFIG):
        if key.startswith(("write_", "show_")):
            monkeypatch.setitem(
                generator_module.REPORT_CONFIG, key, key == "write_html"
            )
    session = session_factory()
    try:
        job = JobService(session).create_job(
            JobCreate(workflow_type="genomic_analysis", name="assume-ref-alert")
        )
        session.commit()
        row = session.query(Job).filter(Job.id == job.id).first()
        row.job_metadata = {
            "file_analysis": {"file_type": file_type},
            "workflow": {},
            "pharmcat_absent_to_ref": flags,
            "pharmcat_unspecified_to_ref": flags,
        }
        session.commit()
        result = generator_module.generate_report(
            {"data": json.loads(REPORT_JSON.read_text(encoding="utf-8"))},
            str(tmp_path),
            {"id": "p1"},
            job_id=str(job.id),
            db_session=session,
        )
    finally:
        session.rollback()
        session.close()
    page = (tmp_path / result["html_path"].split("/")[-1]).read_text(encoding="utf-8")
    methodology = page[page.index("<h2>Methodology</h2>") :]
    methodology = methodology[: methodology.index("<h2>Alerts and Warnings</h2>")]
    alerts = page[page.index("<h2>Alerts and Warnings</h2>") :]
    alerts = alerts[: alerts.index('id="exec-summary"')]
    return methodology, alerts


def test_a_vcf_run_that_assumed_the_reference_alerts(
    session_factory, monkeypatch, tmp_path
):
    methodology, alerts = _page(session_factory, monkeypatch, tmp_path, "vcf", True)
    assert "--missing-to-ref" in alerts and CAUTION in alerts
    assert "Assume reference when missing:" in methodology
    assert CAUTION not in methodology


@pytest.mark.parametrize("file_type, flags", [("vcf", False), ("bam", True)])
def test_no_alert_when_nothing_was_assumed(
    session_factory, monkeypatch, tmp_path, file_type, flags
):
    """Off, or asked for on an alignment, where the pipeline genotypes every
    PharmCAT position and never applies the flags."""
    methodology, alerts = _page(
        session_factory, monkeypatch, tmp_path, file_type, flags
    )
    assert "assumed to be the reference allele" not in alerts
    assert "Assume reference when missing:" not in methodology
