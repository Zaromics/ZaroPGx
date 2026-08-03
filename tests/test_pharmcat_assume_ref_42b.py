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
