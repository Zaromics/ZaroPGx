from app.utils.pharmcat_assume_ref import (
    parse_bool,
    resolve_assume_ref_flags,
    pharmcat_cli_ref_flags,
    methodology_assume_ref_paragraph,
)


def test_parse_bool_truthy_falsy():
    assert parse_bool("true") is True
    assert parse_bool("1") is True
    assert parse_bool("yes") is True
    assert parse_bool("on") is True
    assert parse_bool("false") is False
    assert parse_bool("") is False
    assert parse_bool(None) is False
    assert parse_bool(None, default=True) is True


def test_resolve_form_overrides_env():
    absent, unspec = resolve_assume_ref_flags(
        form_absent="false",
        form_unspecified="true",
        env_absent="true",
        env_unspecified="false",
    )
    assert absent is False
    assert unspec is True


def test_resolve_env_when_form_absent():
    absent, unspec = resolve_assume_ref_flags(
        form_absent=None,
        form_unspecified=None,
        env_absent="true",
        env_unspecified="true",
    )
    assert absent is True and unspec is True


def test_cli_missing_to_ref_when_both():
    assert pharmcat_cli_ref_flags(True, True) == ["--missing-to-ref"]
    assert pharmcat_cli_ref_flags(True, False) == ["--absent-to-ref"]
    assert pharmcat_cli_ref_flags(False, True) == ["--unspecified-to-ref"]
    assert pharmcat_cli_ref_flags(False, False) == []


def test_methodology_none_when_off():
    assert methodology_assume_ref_paragraph(False, False) is None
    text = methodology_assume_ref_paragraph(True, True)
    assert text is not None
    assert "missing-to-ref" in text.lower() or "absent" in text.lower()
    assert "0/0" in text or "homozygous reference" in text.lower()


from pathlib import Path

PHARMCAT = Path("docker/pharmcat/pharmcat.py")
DOCKERFILE = Path("docker/pharmcat/Dockerfile")


def test_pharmcat_form_declares_assume_ref_fields():
    text = PHARMCAT.read_text(encoding="utf-8")
    assert "pharmcat_absent_to_ref: Optional[str] = Form(None)" in text
    assert "pharmcat_unspecified_to_ref: Optional[str] = Form(None)" in text


def test_pharmcat_dockerfile_copies_assume_ref_helper():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "pharmcat_assume_ref.py" in text


MAIN_NF = Path("pipelines/pgx/main.nf")
RUNNER = Path("docker/nextflow/runner.py")
UPLOAD = Path("app/api/routes/upload_router.py")


def test_main_nf_passes_assume_ref_form_fields():
    text = MAIN_NF.read_text(encoding="utf-8")
    assert "params.pharmcat_absent_to_ref" in text
    assert "params.pharmcat_unspecified_to_ref" in text
    assert "-F pharmcat_absent_to_ref=" in text
    assert "-F pharmcat_unspecified_to_ref=" in text


def test_runner_accepts_assume_ref_fields():
    text = RUNNER.read_text(encoding="utf-8")
    assert "pharmcat_absent_to_ref" in text
    assert "pharmcat_unspecified_to_ref" in text
    assert "--pharmcat_absent_to_ref" in text


def test_upload_router_mentions_assume_ref_form():
    text = UPLOAD.read_text(encoding="utf-8")
    assert "pharmcat_absent_to_ref" in text
    assert "pharmcat_unspecified_to_ref" in text


MAIN_PY = Path("app/main.py")
INDEX = Path("app/templates/index.html")
UPLOAD_JS = Path("app/static/js/GenomeDownloadProgress.js")


def test_services_config_exposes_pharmcat_assume_ref():
    text = MAIN_PY.read_text(encoding="utf-8")
    assert "absent_to_ref" in text
    assert "PHARMCAT_ABSENT_TO_REF" in text


def test_index_has_pharmcat_assume_ref_controls():
    text = INDEX.read_text(encoding="utf-8")
    assert "pharmcat_absent_to_ref" in text
    assert "pharmcat_unspecified_to_ref" in text
    assert "stagePharmcat" in text


def test_upload_js_appends_assume_ref():
    text = UPLOAD_JS.read_text(encoding="utf-8")
    assert "pharmcat_absent_to_ref" in text
    assert "pharmcat_unspecified_to_ref" in text


FILE_PROC = Path("app/api/utils/file_processor.py")
REPORT_TPL = Path("app/reports/templates/report_template.html")
INTERACTIVE = Path("app/reports/templates/interactive_report.html")


def test_file_processor_no_longer_emits_env_assume_ref_warning_html():
    text = FILE_PROC.read_text(encoding="utf-8")
    assert "PharmCAT Configuration Warning" not in text
    assert "PHARMCAT_ABSENT_TO_REF is enabled" not in text


def test_report_templates_include_assume_ref_methodology_hook():
    for path in (REPORT_TPL, INTERACTIVE):
        text = path.read_text(encoding="utf-8")
        assert "pharmcat_assume_ref_methodology" in text
