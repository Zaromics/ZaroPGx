"""The report credits what the run used, and nothing else.

A VCF is never aligned or HLA-typed, yet its report once listed GATK and OptiType
in the Software Platform table, cited "OptiType-based HLA typing", and thanked GATK,
beside an alert saying HLA typing could not be performed. The job's step rows
record what ran; the credits (app/reports/credits.py) follow them in every report.
When they cannot be read, the report credits everything that could have run.
"""

from __future__ import annotations

import uuid

import pytest

import app.reports.generator as generator_module
from app.api.db import JobStep
from app.api.models import JobCreate, StepStatus
from app.reports.generator import credit_run_for_job
from app.services.job_service import JobService
from app.visualizations.job_workflow import load_job_step_records

# A VCF job: PyPGx and mtDNA ran; nothing was aligned, lifted or HLA-typed.
VCF_STEPS = {
    "pypgx_analysis": StepStatus.COMPLETED,
    "mtdna_analysis": StepStatus.COMPLETED,
}
# A BAM job whose HLA typing failed.
BAM_STEPS = {
    "pypgx_bam2vcf": StepStatus.COMPLETED,
    "hla_typing": StepStatus.FAILED,
    "pypgx_analysis": StepStatus.COMPLETED,
    "mtdna_analysis": StepStatus.COMPLETED,
}


@pytest.fixture
def job_with_steps(session_factory):
    sessions = []

    def _make(steps):
        session = session_factory()
        sessions.append(session)
        job = JobService(session).create_job(
            JobCreate(workflow_type="genomic_analysis", name="tools-that-ran")
        )
        for order, (name, status) in enumerate(steps.items(), start=10):
            session.add(
                JobStep(job_id=job.id, step_name=name, step_order=order, status=status)
            )
        session.commit()
        return session, str(job.id)

    yield _make
    for session in sessions:
        session.rollback()
        session.close()


def _credited(session, job_id, file_type):
    run = credit_run_for_job(load_job_step_records(session, job_id), file_type)
    credits = generator_module.build_report_credits(run)
    return {row["name"] for row in credits["software"] + credits["data"]}


def test_a_vcf_job_credits_what_ran(job_with_steps):
    session, job_id = job_with_steps(VCF_STEPS)
    credited = _credited(session, job_id, "vcf")
    assert {"PyPGx", "Beagle", "mtDNA-Server 2", "Haplogrep 3", "PharmCAT"} <= credited
    # Not aligned, lifted or HLA-typed; mutserve and haplocheck read alignments.
    assert not credited & {
        "GATK",
        "BWA-MEM",
        "Picard",
        "OptiType",
        "YARA",
        "GLPK",
        "fuc",
        "mutserve",
        "haplocheck",
        "UCSC hg38",
        "UCSC hg19ToHg38 chain",
        "IPD-IMGT/HLA",
    }


def test_a_failed_step_credits_nothing(job_with_steps):
    session, job_id = job_with_steps(BAM_STEPS)
    credited = _credited(session, job_id, "bam")
    assert not credited & {"OptiType", "YARA", "GLPK", "Pyomo", "IPD-IMGT/HLA"}
    assert {"fuc", "UCSC hg38", "mutserve", "haplocheck"} <= credited


def test_unknown_steps_credit_everything_that_could_have_run(session_factory):
    session = session_factory()
    try:
        records = load_job_step_records(session, str(uuid.uuid4()))
        run = credit_run_for_job(records, None)
    finally:
        session.close()
    assert run.steps is None
    credits = generator_module.build_report_credits(run)
    credited = {row["name"] for row in credits["software"] + credits["data"]}
    assert {"GATK", "OptiType", "mutserve", "UCSC hg19ToHg38 chain"} <= credited


def _pages(session, job_id, tmp_path, monkeypatch):
    for key in list(generator_module.REPORT_CONFIG):
        if key.startswith(("write_", "show_")):
            monkeypatch.setitem(
                generator_module.REPORT_CONFIG,
                key,
                key in ("write_html", "write_interactive_html"),
            )
    monkeypatch.setattr(
        generator_module.PharmCATDataService,
        "get_pharmcat_data_for_workflow",
        lambda self, job: None,
    )
    result = generator_module.generate_report(
        {"data": {"genes": [], "drugRecommendations": []}},
        str(tmp_path),
        {"id": "p1"},
        job_id=job_id,
        db_session=session,
    )
    return [
        (tmp_path / result[key].split("/")[-1]).read_text(encoding="utf-8")
        for key in ("html_path", "interactive_html_path")
    ]


def test_a_vcf_report_credits_neither_gatk_nor_optitype(
    job_with_steps, tmp_path, monkeypatch
):
    session, job_id = job_with_steps(VCF_STEPS)
    for page in _pages(session, job_id, tmp_path, monkeypatch):
        # (The Call legend names OptiType as a call source; that stays.)
        assert "OptiType: precision HLA typing" not in page
        assert "The Genome Analysis Toolkit" not in page
        assert "mtDNA-Server 2: advancing" in page
        assert "Stargazer" in page  # PyPGx ran: its papers stay


def test_an_unknown_job_credits_every_tool(tmp_path, monkeypatch):
    for page in _pages(None, None, tmp_path, monkeypatch):
        assert "OptiType: precision HLA typing" in page
        assert "The Genome Analysis Toolkit" in page
