"""Form job_id residue — Zaro Job PK on multipart + Nextflow curls."""

from pathlib import Path

GATK = Path("docker/gatk-api/gatk_api.py")
PHARMCAT = Path("docker/pharmcat/pharmcat.py")
PYPGX = Path("docker/pypgx/pypgx_wrapper.py")
ZAROHLA = Path("docker/zarohla/app.py")
MAIN_NF = Path("pipelines/pgx/main.nf")
RUNNER = Path("docker/nextflow/runner.py")


def test_container_forms_use_job_id_not_workflow_id():
    for path in (GATK, PHARMCAT, PYPGX, ZAROHLA):
        text = path.read_text(encoding="utf-8")
        assert "workflow_id: Optional[str] = Form(" not in text, path
        assert "job_id: Optional[str] = Form(" in text or "job_id: str = Form(" in text, path


def test_gatk_variant_call_has_single_job_pk_form_not_dual():
    text = GATK.read_text(encoding="utf-8")
    start = text.index('@app.post("/variant-call")')
    # Next route decorator or EOF
    nxt = text.find("\n@app.", start + 1)
    block = text[start : nxt if nxt != -1 else start + 2500]
    # Must not still declare both Form job_id (local) and Form workflow_id
    assert "workflow_id: Optional[str] = Form(" not in block
    # Zaro PK form present once in signature region
    assert block.count("job_id: Optional[str] = Form(") == 1


def test_main_nf_uses_job_id_env_and_form():
    text = MAIN_NF.read_text(encoding="utf-8")
    assert "-F job_id=" in text or "-F job_id=${JOB_ID}" in text
    assert "-F workflow_id=" not in text
    assert "${WORKFLOW_ID}" not in text
    assert "${JOB_ID" in text  # ${JOB_ID} or ${JOB_ID:-}


def test_nextflow_runner_sets_job_id_env():
    text = RUNNER.read_text(encoding="utf-8")
    assert "env['JOB_ID']" in text or 'env["JOB_ID"]' in text
