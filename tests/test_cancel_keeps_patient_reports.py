"""Cancelling one job must not delete the patient's other reports.

Both cancellation cleanups -- the Nextflow runner's (docker/nextflow/runner.py,
on /cancel) and the app's delayed one (upload_router.delayed_cleanup_on_cancellation)
-- listed /data/reports/{patient_id}, the directory every job of that patient
publishes under. Seen on the verify stack: a sample re-uploaded under the same
identifier lands on the same patient, and cancelling the new job deleted the
completed job's PDF, HTML and FHIR reports with it.

These evaluate each cleanup list, as written in the source, for one patient and
one job.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
PATIENT, JOB = "patient-1", "job-1"


def _evaluate(elements, namespace):
    return [
        eval(compile(ast.Expression(body=e), "<cleanup>", "eval"), dict(namespace))
        for e in elements
    ]


def _runner_cleanup_paths():
    tree = ast.parse((ROOT / "docker/nextflow/runner.py").read_text(encoding="utf-8"))
    (paths,) = [
        value
        for node in ast.walk(tree)
        if isinstance(node, ast.Dict)
        for key, value in zip(node.keys, node.values)
        if isinstance(key, ast.Constant) and key.value == "cleanup_paths"
    ]
    request = SimpleNamespace(
        input="/data/uploads/upload_ab12_x.vcf", patient_id=PATIENT
    )
    return _evaluate(
        paths.elts,
        {
            "request": request,
            "report_id": JOB,
            "outdir": f"/data/reports/{PATIENT}/{JOB}",
        },
    )


def _app_cleanup_paths():
    tree = ast.parse(
        (ROOT / "app/api/routes/upload_router.py").read_text(encoding="utf-8")
    )
    (function,) = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "delayed_cleanup_on_cancellation"
    ]
    (paths,) = [
        node.value
        for node in ast.walk(function)
        if isinstance(node, ast.Assign)
        and getattr(node.targets[0], "id", None) == "cleanup_paths"
    ]
    return _evaluate(paths.elts, {"patient_id": PATIENT, "job_id": JOB})


def test_the_runner_removes_the_cancelled_jobs_reports_only():
    paths = [p.rstrip("/") for p in _runner_cleanup_paths()]
    assert f"/data/reports/{PATIENT}/{JOB}" in paths
    assert f"/data/reports/{PATIENT}" not in paths


def test_the_app_removes_the_cancelled_jobs_reports_only():
    paths = [p.rstrip("/") for p in _app_cleanup_paths()]
    assert f"/data/reports/{PATIENT}/{JOB}" in paths
    assert f"/data/reports/{PATIENT}" not in paths


def test_no_cleanup_list_reaches_above_a_job_under_reports():
    for path in _runner_cleanup_paths() + _app_cleanup_paths():
        parts = Path(path).parts
        if parts[:3] == ("/", "data", "reports"):
            assert len(parts) >= 5, f"{path} is above any one job's directory"
