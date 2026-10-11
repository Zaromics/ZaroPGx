"""A reference call's wild-type label follows the uploaded file's type.

"Likely Wild Type" is the label for a reference call made from reads, "Possibly
Wild Type" for one from a variant-only file, where a reference call can also be
a site the file never covered. The report took the type from the database lane's
data, which hard-codes "vcf", so a 30x NA12878 BAM run labelled CYP4F2 *1/*1
"Possibly Wild Type". The job's metadata records the real type
(file_analysis.file_type); the report now uses it, from either lane.

Run against a real job row; only PharmCAT's data is stubbed.
"""

from __future__ import annotations

import re

import pytest

from app.api.db import Job


def _job(session, file_type):
    from app.api.models import JobCreate
    from app.services.job_service import JobService

    job = JobService(session).create_job(
        JobCreate(workflow_type="genomic_analysis", name="wild-type-label")
    )
    session.commit()
    row = session.query(Job).filter(Job.id == job.id).first()
    row.job_metadata = {"file_analysis": {"file_type": file_type}, "workflow": {}}
    session.commit()
    return str(job.id)


GENES = [{"gene": "CYP4F2", "diplotype": "*1/*1", "phenotype": ""}]


def _report(session_factory, monkeypatch, tmp_path, file_type, database_lane):
    """Generate a report for a job of this input type. The database lane says "vcf"
    whatever the input; without it the report reads PharmCAT's files."""
    import app.reports.generator as generator_module

    session = session_factory()
    try:
        job_id = _job(session, file_type)
        monkeypatch.setattr(
            generator_module.PharmCATDataService,
            "get_pharmcat_data_for_workflow",
            lambda self, job: (
                {
                    "genes": GENES,
                    "drugRecommendations": [],
                    "file_type": "vcf",
                    "workflow": {},
                }
                if database_lane
                else None
            ),
        )
        for key in list(generator_module.REPORT_CONFIG):
            if key.startswith(("write_", "show_")):
                monkeypatch.setitem(
                    generator_module.REPORT_CONFIG, key, key == "write_html"
                )
        result = generator_module.generate_report(
            {"data": {"genes": GENES, "drugRecommendations": []}},
            str(tmp_path),
            {"id": "p1"},
            job_id=job_id,
            db_session=session,
        )
    finally:
        session.rollback()
        session.close()
    rows = {g["gene"]: g for g in result["processed_data"]["genes"]}
    page = (tmp_path / result["html_path"].split("/")[-1]).read_text(encoding="utf-8")
    return rows["CYP4F2"]["phenotype"], page


@pytest.mark.parametrize("database_lane", [True, False], ids=["database", "files"])
@pytest.mark.parametrize(
    "file_type, label",
    [
        ("bam", "Likely Wild Type"),
        ("cram", "Likely Wild Type"),
        ("fastq", "Likely Wild Type"),
        # Genotyped at every PharmCAT position from its reference blocks.
        ("gvcf", "Likely Wild Type"),
        ("vcf", "Possibly Wild Type"),
        ("bcf", "Possibly Wild Type"),
        ("23andme", "Possibly Wild Type"),
        ("ancestry", "Possibly Wild Type"),
    ],
)
def test_the_label_follows_the_uploaded_file(
    session_factory, monkeypatch, tmp_path, file_type, label, database_lane
):
    phenotype, page = _report(
        session_factory, monkeypatch, tmp_path, file_type, database_lane
    )
    assert phenotype == label
    assert re.search(r"<td>CYP4F2</td>\s*<td>\*1/\*1</td>\s*<td>" + label, page)


def test_an_unknown_input_gets_no_wild_type_label(
    session_factory, monkeypatch, tmp_path
):
    phenotype, _ = _report(session_factory, monkeypatch, tmp_path, "unknown", True)
    assert "Wild Type" not in str(phenotype)
