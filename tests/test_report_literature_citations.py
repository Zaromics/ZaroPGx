"""The reports' literature names each tool's papers, correctly, when it ran.

They thanked PharmCAT, GATK and PyPGx in print but not OptiType or mtDNA-Server 2,
whose results they also carry; Stargazer was dated 2018 (online; the issue is
2019) and the PyPGx papers had no volume or pages. OptiType's first author is
Szolek, and mtDNA-Server 2 is in Nucleic Acids Research 52(W1):W102-W107.
"""

from __future__ import annotations

import pytest

import app.reports.generator as generator_module
from app.api.db import JobStep
from app.api.models import JobCreate, StepStatus
from app.services.job_service import JobService

C = StepStatus.COMPLETED
ALIGNMENT = {
    "hla_typing": C,
    "pypgx_bam2vcf": C,
    "pypgx_analysis": C,
    "mtdna_analysis": C,
    "pharmcat_analysis": C,
}
VCF = {"pypgx_analysis": C, "pharmcat_analysis": C}


@pytest.fixture
def pages(session_factory, tmp_path, monkeypatch):
    sessions = []

    def _render(steps):
        session = session_factory()
        sessions.append(session)
        job = JobService(session).create_job(
            JobCreate(workflow_type="genomic_analysis", name="literature")
        )
        session.query(JobStep).filter(JobStep.job_id == job.id).delete()
        for order, (name, status) in enumerate(steps.items(), start=1):
            session.add(
                JobStep(job_id=job.id, step_name=name, step_order=order, status=status)
            )
        session.commit()
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
        out = tmp_path / str(job.id)
        out.mkdir()
        result = generator_module.generate_report(
            {"data": {"genes": [], "drugRecommendations": []}},
            str(out),
            {"id": "p1"},
            job_id=str(job.id),
            db_session=session,
        )
        return [
            (out / result[key].split("/")[-1]).read_text(encoding="utf-8")
            for key in ("html_path", "interactive_html_path")
        ]

    yield _render
    for session in sessions:
        session.rollback()
        session.close()


def test_an_alignment_report_cites_optitype_and_mtdna_server_2(pages):
    for page in pages(ALIGNMENT):
        assert "Szolek A, Schubert B" in page
        assert "2014;30(23):3310–3316" in page
        assert "2024;52(W1):W102–W107" in page
        assert "Haplogrep 3" in page
        assert "<em>Genetics in Medicine</em>. 2019;21(2):361–372" in page


def test_a_vcf_report_without_them_does_not(pages):
    for page in pages(VCF):
        assert "Szolek A" not in page
        assert "W102–W107" not in page
        assert "<em>PLOS ONE</em>. 2022;17(7):e0272129" in page
