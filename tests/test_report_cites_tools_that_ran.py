"""The report names the tools that ran on the sample, and no others.

A VCF is never aligned or HLA-typed, yet its report listed GATK and OptiType in the
Software Platform table, cited "OptiType-based HLA typing", and thanked GATK, beside
an alert saying HLA typing could not be performed. The job's step rows record what
ran; the platform table, the citations and both reports' acknowledgements now
follow them. When they cannot be read, the report names every tool, as before.
"""

from __future__ import annotations

import uuid

import pytest

import app.reports.generator as generator_module
from app.api.db import JobStep
from app.api.models import JobCreate, StepStatus
from app.reports.generator import (
    build_citations,
    build_platform_info,
    tools_run_for_job,
)
from app.services.job_service import JobService

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


def test_the_tools_of_completed_steps(job_with_steps):
    session, job_id = job_with_steps(VCF_STEPS)
    assert tools_run_for_job(session, job_id) == {"PyPGx", "mtDNA-Server 2"}
    session, job_id = job_with_steps(BAM_STEPS)
    assert tools_run_for_job(session, job_id) == {"PyPGx", "mtDNA-Server 2"}


def test_unknown_when_the_steps_cannot_be_read(session_factory):
    session = session_factory()
    try:
        assert tools_run_for_job(session, str(uuid.uuid4())) is None
        assert tools_run_for_job(session, None) is None
        assert tools_run_for_job(None, str(uuid.uuid4())) is None
    finally:
        session.close()


def test_the_platform_table_and_citations_follow_them(monkeypatch):
    monkeypatch.setattr(
        generator_module,
        "get_all_versions",
        lambda: [
            {"name": name, "version": "1.0"}
            for name in ("PharmCAT", "PyPGx", "GATK", "OptiType", "mtDNA-server-2")
        ],
    )
    ran = frozenset({"PyPGx", "mtDNA-Server 2"})
    assert [row["name"] for row in build_platform_info(ran)] == [
        "ZaroPGx",
        "PharmCAT",
        "PyPGx",
        "mtDNA-Server 2",
    ]
    cited = [citation["name"] for citation in build_citations(ran)]
    assert "GATK" not in cited and "ZaroHLA" not in cited
    assert {"PharmCAT", "PyPGx", "mtDNA-server-2", "CPIC", "PharmGKB"} <= set(cited)
    # Unknown: every tool, as before.
    assert len(build_platform_info(None)) == 6
    assert {"GATK", "ZaroHLA"} <= {c["name"] for c in build_citations(None)}


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


def test_a_vcf_report_thanks_neither_gatk_nor_optitype(
    job_with_steps, tmp_path, monkeypatch
):
    session, job_id = job_with_steps(VCF_STEPS)
    for page in _pages(session, job_id, tmp_path, monkeypatch):
        # (The Call legend names OptiType as a call source; that stays.)
        assert "OptiType-based HLA typing" not in page
        assert "Genome Analysis Toolkit" not in page
        assert "mtDNA-Server 2" in page
        assert "Stargazer" in page  # PyPGx ran: its papers stay


def test_an_unknown_job_names_every_tool(tmp_path, monkeypatch):
    for page in _pages(None, None, tmp_path, monkeypatch):
        assert "OptiType-based HLA typing" in page
        assert "Genome Analysis Toolkit" in page
