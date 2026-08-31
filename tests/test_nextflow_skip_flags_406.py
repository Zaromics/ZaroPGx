"""406 — the GATK/Report toggles must survive the app -> Nextflow hand-off.

`upload_router` computes `skip_gatk`/`skip_report` and posts them to the Nextflow
runner, but `NextflowRunRequest` only declared `skip_hla`/`skip_pypgx`, so pydantic
v2 silently dropped the other two and the pipeline ran regardless of the toggles.
These tests pin the whole chain: request model -> argv -> pipeline params.
"""

import importlib.util
import inspect
import os
import re
import sys
import tempfile
import threading
from pathlib import Path

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = REPO_ROOT / "docker" / "nextflow" / "runner.py"
MAIN_NF = REPO_ROOT / "pipelines" / "pgx" / "main.nf"
UPLOAD_ROUTER = REPO_ROOT / "app" / "api" / "routes" / "upload_router.py"

SKIP_FLAGS = ("skip_hla", "skip_pypgx", "skip_gatk", "skip_report")


def _load_runner():
    """Import docker/nextflow/runner.py by path — docker/ is not a package."""
    name = "zaropgx_nextflow_runner"
    if name in sys.modules:
        return sys.modules[name]
    # runner.py configures a file handler at import time against the container's
    # /data volume; redirect it so importing here cannot write outside the tmpdir.
    os.environ.setdefault(
        "NEXTFLOW_PROGRESS_LOG",
        str(Path(tempfile.gettempdir()) / "zaropgx_nextflow_progress_test.log"),
    )
    spec = importlib.util.spec_from_file_location(name, RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


runner = _load_runner()


def _sample_payload(**overrides):
    payload = {
        "input": "/data/uploads/sample.vcf",
        "input_type": "vcf",
        "patient_id": "patient-1",
        "report_id": "job-1",
        "job_id": "job-1",
        "skip_hla": "true",
        "skip_pypgx": "true",
        "skip_gatk": "true",
        "skip_report": "true",
    }
    payload.update(overrides)
    return payload


def test_run_request_declares_all_four_skip_flags():
    fields = set(runner.NextflowRunRequest.model_fields)
    missing = [flag for flag in SKIP_FLAGS if flag not in fields]
    assert not missing, f"NextflowRunRequest drops skip flags: {missing}"


def test_run_request_does_not_silently_drop_skip_flags():
    request = runner.NextflowRunRequest(**_sample_payload())
    for flag in SKIP_FLAGS:
        assert getattr(request, flag) == "true", flag


def test_run_request_skip_flags_default_to_false():
    request = runner.NextflowRunRequest(
        input="/data/uploads/sample.vcf", input_type="vcf", patient_id="patient-1"
    )
    for flag in SKIP_FLAGS:
        assert getattr(request, flag) == "false", flag


def _argv_value(cmd, flag):
    assert flag in cmd, f"{flag} missing from Nextflow argv: {cmd}"
    return cmd[cmd.index(flag) + 1]


def test_build_nextflow_command_emits_every_skip_flag():
    cmd = runner.build_nextflow_command(
        input_path="/data/uploads/sample.bam",
        input_type="bam",
        patient_id="patient-1",
        report_id="job-1",
        reference="hg38",
        outdir="/data/reports/patient-1/job-1",
        skip_hla="true",
        skip_pypgx="false",
        skip_gatk="true",
        skip_report="true",
    )
    assert _argv_value(cmd, "--skip_hla") == "true"
    assert _argv_value(cmd, "--skip_pypgx") == "false"
    assert _argv_value(cmd, "--skip_gatk") == "true"
    assert _argv_value(cmd, "--skip_report") == "true"


def test_build_nextflow_command_passes_false_through():
    cmd = runner.build_nextflow_command(
        input_path="/data/uploads/sample.vcf",
        input_type="vcf",
        patient_id="patient-1",
        report_id="job-1",
        reference="hg38",
        outdir="/data/reports/patient-1/job-1",
        skip_gatk="false",
        skip_report="false",
    )
    assert _argv_value(cmd, "--skip_gatk") == "false"
    assert _argv_value(cmd, "--skip_report") == "false"


def test_run_endpoint_hands_every_skip_flag_to_the_job(monkeypatch, tmp_path):
    """/run passes 15 positional args to run_nextflow_job; pin the binding."""
    real_signature = inspect.signature(runner.run_nextflow_job)
    handed = {}
    done = threading.Event()

    def _capture(*args, **kwargs):
        bound = real_signature.bind(*args, **kwargs)
        bound.apply_defaults()
        handed.update(bound.arguments)
        done.set()

    monkeypatch.setattr(runner, "run_nextflow_job", _capture)

    # job_id omitted on purpose: it would send the handler looking for the database.
    payload = _sample_payload(job_id=None, report_id=None, outdir=str(tmp_path))
    response = TestClient(runner.app).post("/run", json=payload)

    assert response.status_code == 200, response.text
    assert done.wait(10), "run_nextflow_job was never invoked"
    for flag in SKIP_FLAGS:
        assert handed[flag] == "true", f"{flag} did not reach run_nextflow_job"


def test_upload_payload_skip_keys_all_exist_on_the_request_model():
    """Every skip_* key the app posts must be a declared field, or pydantic eats it."""
    text = UPLOAD_ROUTER.read_text(encoding="utf-8")
    posted = set(re.findall(r'"(skip_[a-z_]+)":', text))
    assert posted, "upload_router no longer posts any skip_* key"
    fields = set(runner.NextflowRunRequest.model_fields)
    assert posted <= fields, f"posted but undeclared: {sorted(posted - fields)}"


def test_failure_summary_surfaces_the_pipeline_error_not_the_version_nag():
    """Nextflow writes error() text to stdout; stderr carries the upgrade nag."""
    stdout = "N E X T F L O W  ~  version 25.04.7\n--skip_gatk is not compatible"
    stderr = "Nextflow 26.04.6 is available - please consider updating"
    summary = runner.summarize_nextflow_failure(stdout, stderr)
    assert "--skip_gatk is not compatible" in summary
    assert "26.04.6" in summary, "stderr must be kept too, not traded away"


def test_failure_summary_keeps_the_tail_and_handles_empty_streams():
    summary = runner.summarize_nextflow_failure("x" * 50 + "TAIL", "", tail=10)
    assert "TAIL" in summary
    assert len(summary) < 40, "stdout must be tail-truncated"
    assert runner.summarize_nextflow_failure(None, None) == "Unknown error"
    assert runner.summarize_nextflow_failure("   ", "  ") == "Unknown error"


def test_main_nf_is_honest_that_the_pipeline_does_not_honour_skip_report():
    """The param is carried and declared here; the gate that acts on it is app-side.

    The comment used to say NOT YET IMPLEMENTED, true until the final-stage report
    worker started gating on needs_report. It must now name where the gate lives
    without implying this pipeline enforces the toggle.
    """
    text = MAIN_NF.read_text(encoding="utf-8")
    start = text.index("params.skip_report")
    block = text[max(0, start - 800) : start]
    assert "NOT YET IMPLEMENTED" not in block, "the app-side gate exists now"
    assert "upload_router" in block, "name the real location of the gate"
    assert "app/reports/generator.py" not in block, "generator.py does not honour it"
    # The comment's central claim: nothing in the pipeline reads the param, it is
    # only declared. If a process ever does, the comment has to be revisited.
    readers = [
        line.strip()
        for line in text.splitlines()
        if "params.skip_report" in line
        and not line.strip().startswith("params.skip_report")
    ]
    assert not readers, f"the pipeline now reads it; re-word the comment: {readers}"


def test_main_nf_declares_all_four_skip_params():
    text = MAIN_NF.read_text(encoding="utf-8")
    for flag in SKIP_FLAGS:
        assert f"params.{flag}" in text, flag


def test_main_nf_rejects_skip_gatk_for_conversion_inputs():
    """fastq/cram/sam cannot reach a BAM, and bcf cannot reach a readable VCF,
    without the gatk-api container; that must error, not hang."""
    text = MAIN_NF.read_text(encoding="utf-8")
    assert "params.skip_gatk" in text
    guard = re.search(r"if \(params\.skip_gatk[^\n]*\n[^\n]*error", text)
    assert guard, "main.nf must fail fast when skip_gatk conflicts with input_type"


# PyPGxGenotypeAll degrades to PharmCAT-only on purpose; PharmCATRun ends in `|| true`.
EXEMPT_CURL_ENDPOINTS = ("http://pypgx:5000/genotype", "http://pharmcat:5000/genotype")


def _curl_call_sites():
    text = MAIN_NF.read_text(encoding="utf-8")
    return text, [
        line.strip()
        for line in text.splitlines()
        if "curl " in line and "http://" in line
    ]


def test_every_curl_call_site_fails_on_http_errors_or_is_annotated_exempt():
    """Plain curl exits 0 on 4xx/5xx and writes the error doc where a result belongs."""
    text, calls = _curl_call_sites()
    # 11: 10 after Task 9 added MtdnaCall's call to http://mtdna:5000/call-mtdna,
    # plus BcfToVCF's call to gatk-api's /bcf-to-vcf.
    assert len(calls) == 11, f"curl call-site count changed to {len(calls)}; re-audit"
    for call in calls:
        if any(endpoint in call for endpoint in EXEMPT_CURL_ENDPOINTS):
            continue
        assert "--fail-with-body" in call, f"unguarded curl: {call}"
    # The two exemptions must stay deliberate, not drift into being accidental.
    assert text.count("DELIBERATELY EXEMPT") == 2


def test_guarded_curl_failures_surface_the_server_message():
    """--fail-with-body is only useful if the body and curl's diagnostic get out."""
    text, calls = _curl_call_sites()
    guarded = [call for call in calls if "--fail-with-body" in call]
    # 9: MtdnaCall (Task 9) and BcfToVCF are guarded like every other call site
    # except the two DELIBERATELY EXEMPT ones.
    assert len(guarded) == 9, f"{len(guarded)} guarded call sites, expected 9"
    assert text.count("returned an error:") == 9, "each guarded call must echo the body"
    # `2>service.log` buried curl's own error in a work-dir file nobody reads; -sS puts
    # it on stderr, where Nextflow picks it up into .command.err and the error report.
    for swallowed in ("2>gatk.log", "2>hla.log", "2>pypgx_bam2vcf.log"):
        assert swallowed not in text, swallowed


def test_runner_progress_log_is_size_bounded():
    """252 — /data is a shared volume; the progress log must not grow unbounded."""
    text = RUNNER_PATH.read_text(encoding="utf-8")
    assert "RotatingFileHandler" in text
    assert "maxBytes" in text
    assert "backupCount" in text
    assert "logging.FileHandler(" not in text
