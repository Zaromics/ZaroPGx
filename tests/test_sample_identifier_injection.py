"""Command-injection hardening for values that reach the Nextflow pipeline's shell.

`params.sample_identifier` and `reference_genome` are user-controlled strings that used
to be interpolated verbatim into main.nf's bash ``shell:`` blocks. Nextflow escapes
``path`` inputs before interpolation but NOT ``val``/``params`` strings, so a value
containing a double quote broke out of the surrounding quoting and the remainder ran as
shell - inside the nextflow container, which bind-mounts the Docker socket (root on the
host). That is an unauthenticated remote code execution because the app publishes on all
interfaces and the auth gate defaults to open.

These tests pin the fix at every layer and are written so that reverting any single fix
fails at least one of them (mutation-checked):

  * main.nf      - sample_identifier is read from ``$SAMPLE_IDENTIFIER``, never
                   interpolated; the full set of shell interpolations is locked so a new
                   user-controlled one cannot be added without re-auditing.
  * runner.py    - ``--sample_identifier`` is off the argv, the env var is exported to
                   the subprocess, and NextflowRunRequest allowlist-validates the two
                   user-controlled fields.
  * upload_router- the boundary allowlist rejects shell metacharacters with HTTP 400 and
                   sanitizes the (also attacker-controlled) VCF-header sample name.
"""

import importlib.util
import inspect
import os
import re
import sys
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN_NF = REPO_ROOT / "pipelines" / "pgx" / "main.nf"
RUNNER_PATH = REPO_ROOT / "docker" / "nextflow" / "runner.py"

# A payload that cleanly breaks out of BOTH interpolation sites in the old PharmCATRun
# shell block: it closes the array-assignment paren, runs an arbitrary command, and
# reopens the paren so the rest of the script still parses. Proven to create a marker
# file inside the running pgx_nextflow container against the vulnerable pipeline.
INJECTION_PAYLOAD = ") ; touch /tmp/pwned ; CURL_ARGS+=("


def _load_runner():
    """Import docker/nextflow/runner.py by path - docker/ is not a package."""
    name = "zaropgx_nextflow_runner"
    if name in sys.modules:
        return sys.modules[name]
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


# --------------------------------------------------------------------------- main.nf

# Every !{...} interpolation in main.nf, with the audited reason each is safe:
#   path inputs (bam/cram/fastq/sam/vcf)  - Nextflow escapes path-typed inputs, and they
#                                           are server-side staged paths anyway
#   patient_id / report_id                - server-minted UUIDs (create_patient / job.id)
#   reference                             - user-controlled, kept safe by the boundary
#                                           allowlist (a reference label has no shell
#                                           metacharacters); still interpolated on purpose
#   source_build                          - header-DERIVED build label ("GRCh37"/"hg19"),
#                                           audited 2026-08-23: the app filters it through
#                                           sanitize_optional_pipeline_token before posting
#                                           and NextflowRunRequest._validate_source_build
#                                           enforces the same allowlist as reference, so
#                                           only [A-Za-z0-9][A-Za-z0-9._-]{0,63} can reach
#                                           the LiftoverVCF shell block
#   bcf                                   - BcfToVCF's own `path` input (audited
#                                           2026-08-31): same class as the
#                                           bam/cram/fastq/sam/vcf path inputs above
#                                           - Nextflow escapes path-typed inputs.
#   params.pharmcat_absent_to_ref/...     - booleans rendered as "true"/"false"
#   variants_file                         - MtdnaCall's own `path` input (audited
#                                           2026-08-30, Task 9): same class as the
#                                           bam/cram/fastq/sam/vcf path inputs above
#                                           - Nextflow escapes path-typed inputs.
#   input_type (a process-local var, in   - audited 2026-08-30 for MtdnaCall and
#   MtdnaCall and PyPGxGenotypeAll)         re-audited 2026-08-31 when
#                                           PyPGxGenotypeAll took the same input:
#                                           both are computed in the workflow block
#                                           as Groovy ternaries over
#                                           params.input_type, itself a fixed
#                                           FileType enum value, and every arm is
#                                           either a string literal ('vcf'/'bam') or
#                                           params.input_type unchanged - never a
#                                           value read through from user input.
#                                           (`params.input_type` itself is no longer
#                                           interpolated anywhere: PyPGxGenotypeAll
#                                           was the last site, and it now takes this
#                                           corrected value instead, because a `bcf`
#                                           run hands PyPGx a converted VCF.)
#   absent_to_ref (MtdnaCall's local var) - audited 2026-08-30: the same
#                                           params.pharmcat_absent_to_ref boolean
#                                           ("true"/"false") already audited above,
#                                           just referenced through the process's
#                                           own local parameter name.
# sample_identifier is DELIBERATELY absent: it now travels via the environment.
EXPECTED_INTERPOLATIONS = {
    "bam",
    "bcf",
    "cram",
    "fastq",
    "sam",
    "vcf",
    "variants_file",
    "patient_id",
    "report_id",
    "reference",
    "source_build",
    "input_type",
    "absent_to_ref",
    "params.pharmcat_absent_to_ref",
    "params.pharmcat_unspecified_to_ref",
}


def test_main_nf_never_interpolates_sample_identifier_into_shell():
    """The vulnerable pattern - any !{...} touching sample_identifier - must be gone."""
    text = MAIN_NF.read_text(encoding="utf-8")
    offenders = re.findall(r"!\{[^}]*sample_identifier[^}]*\}", text)
    assert (
        not offenders
    ), f"sample_identifier is interpolated into the shell: {offenders}"


def test_main_nf_reads_sample_identifier_from_environment():
    """PharmCATRun must consume the value as a shell variable, i.e. inert data."""
    text = MAIN_NF.read_text(encoding="utf-8")
    assert (
        "${SAMPLE_IDENTIFIER" in text
    ), "PharmCATRun must read $SAMPLE_IDENTIFIER from the environment, not a param"


def test_main_nf_interpolation_set_is_locked_to_audited_sources():
    """Audit lock: adding any new !{...} forces a re-audit of user-controlled input."""
    text = MAIN_NF.read_text(encoding="utf-8")
    found = set(re.findall(r"!\{([^}]*)\}", text))
    assert found == EXPECTED_INTERPOLATIONS, (
        "main.nf shell interpolations changed; re-audit each for user-controlled input. "
        f"added={sorted(found - EXPECTED_INTERPOLATIONS)} "
        f"removed={sorted(EXPECTED_INTERPOLATIONS - found)}"
    )


# --------------------------------------------------------------------------- runner.py


def test_build_command_does_not_emit_sample_identifier():
    cmd = runner.build_nextflow_command(
        input_path="/data/uploads/sample.vcf",
        input_type="vcf",
        patient_id="patient-1",
        report_id="job-1",
        reference="hg38",
        outdir="/data/reports/patient-1/job-1",
    )
    assert (
        "--sample_identifier" not in cmd
    ), "sample_identifier must not ride the argv; it goes via the environment"


def test_build_command_signature_dropped_sample_identifier():
    params = inspect.signature(runner.build_nextflow_command).parameters
    assert "sample_identifier" not in params


def test_request_rejects_reference_with_shell_metacharacters():
    with pytest.raises(ValidationError):
        runner.NextflowRunRequest(
            input="/data/uploads/s.vcf",
            input_type="vcf",
            patient_id="patient-1",
            reference='hg38"; touch /tmp/pwned #',
        )


def test_request_rejects_sample_identifier_with_shell_metacharacters():
    with pytest.raises(ValidationError):
        runner.NextflowRunRequest(
            input="/data/uploads/s.vcf",
            input_type="vcf",
            patient_id="patient-1",
            sample_identifier=INJECTION_PAYLOAD,
        )


def test_request_accepts_realistic_identifiers_and_references():
    req = runner.NextflowRunRequest(
        input="/data/uploads/s.vcf",
        input_type="vcf",
        patient_id="patient-1",
        reference="GRCh37",
        sample_identifier="HG002.GRCh38",
    )
    assert req.reference == "GRCh37"
    assert req.sample_identifier == "HG002.GRCh38"


def test_request_blank_sample_identifier_normalizes_to_none():
    req = runner.NextflowRunRequest(
        input="/data/uploads/s.vcf",
        input_type="vcf",
        patient_id="patient-1",
        sample_identifier="   ",
    )
    assert req.sample_identifier is None


def test_run_nextflow_job_exports_sample_identifier_to_subprocess_env(monkeypatch):
    """The value must reach the pipeline as an env var and never as a --param.

    A hostile value is set verbatim in the environment (that is the point - it is inert
    there) while the argv stays free of it.
    """
    captured = {}

    class _FakePopen:
        def __init__(self, cmd, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = kwargs.get("env")
            self.returncode = 0
            self.pid = 4321

        def communicate(self):
            return ("", "")

        def poll(self):
            return 0

    # Stub the completion-monitor thread so nothing runs after we capture the argv/env;
    # keeps the test deterministic and free of a background KeyError race.
    class _NoThread:
        def __init__(self, *a, **k):
            self.daemon = True

        def start(self):
            pass

    monkeypatch.setattr(runner.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(runner.os, "makedirs", lambda *a, **k: None)
    monkeypatch.setattr(runner.threading, "Thread", _NoThread)
    runner.running_jobs["k-inject"] = {"status": "starting", "message": "x"}

    try:
        runner.run_nextflow_job(
            "k-inject",
            "/data/uploads/s.vcf",
            "vcf",
            "patient-1",
            "job-1",
            "hg38",
            "/data/reports/patient-1/job-1",
            sample_identifier=INJECTION_PAYLOAD,
        )

        assert captured["env"] is not None
        assert captured["env"].get("SAMPLE_IDENTIFIER") == INJECTION_PAYLOAD
        assert "--sample_identifier" not in captured["cmd"]
    finally:
        runner.running_jobs.pop("k-inject", None)


# --------------------------------------------------------------------- upload_router.py


def _upload_router():
    from app.api.routes import upload_router

    return upload_router


def test_validate_pipeline_token_accepts_realistic_values():
    ur = _upload_router()
    assert ur.validate_pipeline_token("NA12878", "sample_identifier") == "NA12878"
    assert (
        ur.validate_pipeline_token("HG002.GRCh38", "sample_identifier")
        == "HG002.GRCh38"
    )
    assert ur.validate_pipeline_token("  patient-123 ", "sample_identifier") == (
        "patient-123"
    )
    assert ur.validate_pipeline_token("hg38", "reference_genome") == "hg38"
    assert ur.validate_pipeline_token("GRCh37", "reference_genome") == "GRCh37"


@pytest.mark.parametrize(
    "bad",
    [
        ") ; touch /tmp/pwned ; CURL_ARGS+=(",
        'hg38"; touch /tmp/pwned #',
        "a b",  # whitespace
        "$(id)",
        "`id`",
        "name;rm -rf /",
        "-leading-hyphen",
        "",
        "   ",
        "x" * 65,  # too long
    ],
)
def test_validate_pipeline_token_rejects_unsafe(bad):
    from fastapi import HTTPException

    ur = _upload_router()
    with pytest.raises(HTTPException) as exc:
        ur.validate_pipeline_token(bad, "sample_identifier")
    assert exc.value.status_code == 400


def test_sanitize_optional_pipeline_token_drops_unsafe_keeps_safe():
    ur = _upload_router()
    assert ur.sanitize_optional_pipeline_token("clean_name") == "clean_name"
    assert ur.sanitize_optional_pipeline_token('bad"; rm -rf / #') is None
    assert ur.sanitize_optional_pipeline_token(None) is None
    assert ur.sanitize_optional_pipeline_token("   ") is None


_VALID_VCF = (
    b"##fileformat=VCFv4.2\n"
    b"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\n"
)


def _install_upload_success_mocks(monkeypatch, tmp_path):
    """Make everything downstream of the boundary succeed.

    Without this, the minimal VCF trips the endpoint's own 'invalid/unanalysable file'
    path and returns 400 for reasons unrelated to injection - which would let the
    rejection tests pass even with the validation removed (a false positive). With
    process_files/DB/job all stubbed to succeed, the ONLY thing that can still produce a
    400 is the boundary allowlist, so the assertions genuinely pin the validation.
    """
    import uuid as _uuid

    from app.api.models import FileType
    from app.api.routes import upload_router
    from app.api.utils.file_processor import FileAnalysis as DcFileAnalysis

    async def _fake_process_files(files, reference_genome, **kwargs):
        for f in files:
            await f.read()
        analysis = DcFileAnalysis(
            file_type=FileType.VCF,
            is_compressed=False,
            has_index=False,
            file_size=1,
            vcf_info=None,
            is_valid=True,
            validation_errors=[],
        )
        return {
            "success": True,
            "file_paths": [str(tmp_path / "s.vcf")],
            "file_analysis": analysis,
            "workflow": {
                "workflow_type": "genomic_analysis",
                "file_type": "vcf",
                "needs_report": True,
                "reference": reference_genome or "hg38",
                "is_provisional": False,
                "recommendations": [],
                "warnings": [],
            },
        }

    monkeypatch.setattr(
        upload_router.file_processor, "process_files", _fake_process_files
    )
    monkeypatch.setattr(
        upload_router, "create_patient", lambda db, identifier: str(_uuid.uuid4())
    )
    monkeypatch.setattr(
        upload_router,
        "register_genetic_data",
        lambda db, patient_id, file_type, file_path, is_supplementary: str(
            _uuid.uuid4()
        ),
    )

    class _FakeJob:
        def __init__(self):
            self.id = _uuid.uuid4()
            self.status = "running"
            self.job_metadata = {}

    class _FakeJobService:
        def __init__(self, db):
            self._job = _FakeJob()

        def create_job(self, job_create):
            return self._job

        def update_job(self, job_id, job_update):
            return self._job

    monkeypatch.setattr(upload_router, "JobService", _FakeJobService)

    async def _noop_background(*args, **kwargs):
        return None

    monkeypatch.setattr(
        upload_router, "process_file_nextflow_background_with_db", _noop_background
    )


def test_upload_endpoint_accepts_valid_sample_and_reference(
    client, monkeypatch, tmp_path
):
    """Positive control: with the downstream stubbed to succeed, a clean payload is a
    200. This is what proves the 400s below come from validation, not the file path."""
    _install_upload_success_mocks(monkeypatch, tmp_path)
    resp = client.post(
        "/upload/genomic-data",
        files={"files": ("s.vcf", _VALID_VCF, "text/plain")},
        data={"sample_identifier": "HG002_sample", "reference_genome": "hg38"},
    )
    assert resp.status_code == 200, resp.text


def test_upload_endpoint_rejects_injection_in_sample_identifier(
    client, monkeypatch, tmp_path
):
    _install_upload_success_mocks(monkeypatch, tmp_path)
    resp = client.post(
        "/upload/genomic-data",
        files={"files": ("s.vcf", _VALID_VCF, "text/plain")},
        data={"sample_identifier": INJECTION_PAYLOAD, "reference_genome": "hg38"},
    )
    assert resp.status_code == 400, resp.text


def test_upload_endpoint_rejects_injection_in_reference_genome(
    client, monkeypatch, tmp_path
):
    _install_upload_success_mocks(monkeypatch, tmp_path)
    resp = client.post(
        "/upload/genomic-data",
        files={"files": ("s.vcf", _VALID_VCF, "text/plain")},
        data={
            "sample_identifier": "ok_sample",
            "reference_genome": 'hg38"; touch /tmp/pwned #',
        },
    )
    assert resp.status_code == 400, resp.text
