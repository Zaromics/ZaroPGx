# app/utils/pharmcat_assume_ref.py
from __future__ import annotations

from typing import Optional, Union

Boolish = Union[str, bool, None]


def parse_bool(value: Boolish, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s == "":
        return default
    return s in {"1", "true", "yes", "on"}


def resolve_assume_ref_flags(
    *,
    form_absent: Boolish,
    form_unspecified: Boolish,
    env_absent: Boolish,
    env_unspecified: Boolish,
) -> tuple[bool, bool]:
    absent = (
        parse_bool(form_absent) if form_absent is not None else parse_bool(env_absent)
    )
    unspecified = (
        parse_bool(form_unspecified)
        if form_unspecified is not None
        else parse_bool(env_unspecified)
    )
    return absent, unspecified


def pharmcat_cli_ref_flags(absent: bool, unspecified: bool) -> list[str]:
    if absent and unspecified:
        return ["--missing-to-ref"]
    flags: list[str] = []
    if absent:
        flags.append("--absent-to-ref")
    if unspecified:
        flags.append("--unspecified-to-ref")
    return flags


def methodology_assume_ref_paragraph(absent: bool, unspecified: bool) -> Optional[str]:
    if not absent and not unspecified:
        return None
    if absent and unspecified:
        mode = (
            "PharmCAT preprocessor flag <code>--missing-to-ref</code> "
            "(absent and unspecified PGx sites treated as homozygous reference 0/0)"
        )
    elif absent:
        mode = (
            "PharmCAT preprocessor flag <code>--absent-to-ref</code> "
            "(absent PGx sites treated as homozygous reference 0/0)"
        )
    else:
        mode = (
            "PharmCAT preprocessor flag <code>--unspecified-to-ref</code> "
            "(unspecified genotypes <code>./.</code> treated as homozygous reference 0/0)"
        )
    return (
        f"<p><strong>Assume reference when missing:</strong> This research run used {mode}. "
        "Fabricating reference calls can over-call *1/Reference and normal phenotypes; "
        "PharmCAT documents these flags as dangerous / research-oriented. "
        "Confirm assayed coverage before interpreting results.</p>"
    )
