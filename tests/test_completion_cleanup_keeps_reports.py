"""A finished job must still have its reports.

``JobService.update_job_step`` calls ``cleanup_service.cleanup_job_files`` the
moment a job's progress reaches 100%. That path listed
``/data/reports/{patient_id}/{job_id}`` and ``/data/reports/{patient_id}`` among
the things it deletes, so every successful run destroyed its own output one
second after logging "Job completed successfully with reports generated" -- and
the second path took every *other* report for that patient with it.

``pipelines/pgx/main.nf`` publishes to ``data/reports/${patient_id}`` and
``app/main.py`` serves ``/reports/{patient_id}/{filename}`` out of the same tree,
so the effect was total: every report link 404'd, on every job, always. Observed
on a real run as 11.7 MB removed immediately after completion.

The distinction this module pins is between the two cleanup paths, which are not
interchangeable:

* completion (``CleanupService.cleanup_job_files``) -- temp and scratch only,
  never reports;
* cancellation (``upload_router.delayed_cleanup_on_cancellation``) -- reports
  too, correctly, because a cancelled job has no product worth keeping.

Both directions are asserted, so "fixing" this by making cancellation keep
reports would fail just as loudly.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def data_tree(tmp_path, monkeypatch):
    """A /data tree with reports, uploads and scratch for one patient + job."""
    from app.services.cleanup_service import CleanupService

    patient_id = "patient-1"
    job_id = "job-1"

    service = CleanupService()
    # All four, or the method still reaches the real filesystem: it builds its
    # path list from these attributes (it used to hardcode "/data/..." literals,
    # which is exactly why this bug was invisible to tests).
    monkeypatch.setattr(service, "data_dir", tmp_path)
    monkeypatch.setattr(service, "temp_dir", tmp_path / "tmp")
    monkeypatch.setattr(service, "reports_dir", tmp_path / "reports")
    monkeypatch.setattr(service, "uploads_dir", tmp_path / "uploads")

    report_dir = tmp_path / "reports" / patient_id / job_id
    report_dir.mkdir(parents=True)
    report = report_dir / f"{job_id}_pgx_report.pdf"
    report.write_bytes(b"%PDF-1.7 pretend report")

    sibling = tmp_path / "reports" / patient_id / "earlier-job"
    sibling.mkdir(parents=True)
    sibling_report = sibling / "earlier_pgx_report.html"
    sibling_report.write_text("<html>an earlier run</html>", encoding="utf-8")

    scratch = tmp_path / "temp" / job_id
    scratch.mkdir(parents=True)
    (scratch / "intermediate.bam").write_bytes(b"scratch")

    return {
        "service": service,
        "root": tmp_path,
        "patient_id": patient_id,
        "job_id": job_id,
        "report": report,
        "sibling_report": sibling_report,
        "scratch": scratch,
    }


def _path_lines() -> list[str]:
    """Non-comment lines of completion-cleanup that name a path.

    Source text rather than execution, because the method only *reports* paths
    that happened to exist. This catches a report path being re-added in either
    form -- a hardcoded "/data/reports/..." literal or a self.reports_dir join --
    which the on-disk test below cannot do for the literal.
    """
    from app.services.cleanup_service import CleanupService

    source = inspect.getsource(CleanupService.cleanup_job_files)
    body = source[
        source.index("cleanup_paths = []") : source.index("# Add any additional paths")
    ]
    return [
        line.strip()
        for line in body.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def test_completion_cleanup_names_no_report_path():
    """The regression itself, asserted on the code that caused it."""
    offenders = [
        line
        for line in _path_lines()
        if "/data/reports" in line or "reports_dir" in line or '"reports"' in line
    ]
    assert not offenders, (
        "completion cleanup would delete report artifacts: "
        f"{offenders}. Reports are the product of the run; only the "
        "cancellation path may remove them."
    )


def test_completion_cleanup_still_removes_scratch():
    """Negative control: the method must not have been gutted."""
    lines = " ".join(_path_lines())
    assert '"temp"' in lines or "/data/temp/" in lines
    assert '"results"' in lines or "temp_dir" in lines


def test_cancellation_cleanup_does_still_remove_reports():
    """The other direction: a cancelled job has no product to preserve."""
    source = (REPO_ROOT / "app" / "api" / "routes" / "upload_router.py").read_text(
        encoding="utf-8"
    )
    start = source.index("async def delayed_cleanup_on_cancellation")
    body = source[start : start + 3000]
    assert "/data/reports/{patient_id}" in body, (
        "cancellation cleanup no longer removes reports; a cancelled job now "
        "leaves partial output behind"
    )


def test_a_completed_job_keeps_its_report_on_disk(data_tree):
    """End to end against a real directory tree, not just the source text."""
    service = data_tree["service"]
    service.cleanup_job_files(
        job_id=data_tree["job_id"], patient_id=data_tree["patient_id"]
    )

    # Proof the run actually touched this tree. Without it the two assertions
    # below pass vacuously on any machine that has no /data -- which is how the
    # original bug survived a green suite.
    assert not data_tree["scratch"].exists(), (
        "cleanup did not run against the temp tree, so the report assertions "
        "below prove nothing"
    )

    assert data_tree["report"].exists(), "the job's own report was deleted"
    assert data_tree["sibling_report"].exists(), (
        "an unrelated earlier report for the same patient was deleted -- the "
        "/data/reports/{patient_id} entry is back"
    )


def test_the_reports_directory_is_what_main_nf_publishes_to():
    """If either side moves, the guard above stops matching the real path."""
    main_nf = (REPO_ROOT / "pipelines" / "pgx" / "main.nf").read_text(encoding="utf-8")
    main_py = (REPO_ROOT / "app" / "main.py").read_text(encoding="utf-8")

    assert "data/reports/${params.patient_id}" in main_nf
    assert '"/data/reports"' in main_py
