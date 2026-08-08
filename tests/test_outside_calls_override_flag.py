"""OUTSIDECALLSOVERRIDE must be parsed in exactly one place.

Same defect as FHIR_EXPORT_ENABLED, found while grepping for it:

- ``app/main.py:156`` parsed it with ``_env_flag`` (which ``.strip()``s)
- ``app/utils/outside_calls_override.py:33`` and ``:73`` parsed it with a bare
  ``os.getenv(...).lower()`` (no strip), twice, inline

Two parsers, different whitespace rules. The user-visible half is the util's:
``OUTSIDECALLSOVERRIDE='true '`` silently turned the override *off*, so a
manually curated ``lexicon/outside_calls.tsv`` -- the whole point of the flag,
often the only source of CYP2D6/HLA calls -- was ignored without a word.

``is_override_enabled()`` is now the single resolver and is whitespace-tolerant;
``get_override_file_path()`` calls it instead of re-parsing.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.utils import outside_calls_override as override_module
from app.utils.outside_calls_override import (
    get_override_file_path,
    is_override_enabled,
)

REPO_ROOT = Path(override_module.__file__).resolve().parents[2]

TRUTHY = [
    "true",
    "True",
    "TRUE",
    "1",
    "yes",
    "on",
    "true ",
    " true",
    "  TRUE  ",
    "\ttrue",
]
FALSY = ["false", "False", "0", "no", "off", "", " ", "nope"]


@pytest.mark.parametrize("raw", TRUTHY)
def test_truthy_spellings_enable_override(monkeypatch, raw):
    monkeypatch.setenv("OUTSIDECALLSOVERRIDE", raw)
    assert is_override_enabled() is True, f"{raw!r} should enable the override"


@pytest.mark.parametrize("raw", FALSY)
def test_falsy_spellings_disable_override(monkeypatch, raw):
    monkeypatch.setenv("OUTSIDECALLSOVERRIDE", raw)
    assert is_override_enabled() is False, f"{raw!r} should disable the override"


def test_unset_defaults_to_disabled(monkeypatch):
    monkeypatch.delenv("OUTSIDECALLSOVERRIDE", raising=False)
    assert is_override_enabled() is False


@pytest.fixture
def override_file(tmp_path, monkeypatch):
    """Put a real lexicon/outside_calls.tsv where the resolver will look."""
    target = tmp_path / "lexicon" / "outside_calls.tsv"
    target.parent.mkdir(parents=True)
    target.write_text("CYP2D6\t*1/*4\n", encoding="utf-8")
    monkeypatch.setattr(os, "getcwd", lambda: str(tmp_path))
    return target


@pytest.mark.parametrize("raw", ["true", "true ", " true", "\tTRUE "])
def test_whitespace_padded_true_still_finds_the_override_file(
    monkeypatch, override_file, raw
):
    """The pinned regression: whitespace must not silently disable the override.

    Exercises the *other* half - the gate inside get_override_file_path() - so a
    future edit cannot fix one parser and leave the other behind.
    """
    monkeypatch.setenv("OUTSIDECALLSOVERRIDE", raw)

    assert is_override_enabled() is True
    assert get_override_file_path() is not None, (
        f"OUTSIDECALLSOVERRIDE={raw!r} enabled the override but "
        "get_override_file_path() refused to look for the file"
    )


def test_disabled_override_does_not_look_for_a_file(monkeypatch, override_file):
    monkeypatch.setenv("OUTSIDECALLSOVERRIDE", "false")
    assert get_override_file_path() is None


def test_both_halves_agree_for_every_spelling(monkeypatch, override_file):
    """The split-brain guard: one parser, so the two entry points cannot drift."""
    for raw in TRUTHY + FALSY:
        monkeypatch.setenv("OUTSIDECALLSOVERRIDE", raw)
        enabled = is_override_enabled()
        found = get_override_file_path() is not None
        assert enabled == found, (
            f"OUTSIDECALLSOVERRIDE={raw!r}: is_override_enabled()={enabled} but "
            f"get_override_file_path() {'found' if found else 'refused'} the file"
        )


def test_flag_name_appears_in_exactly_one_env_lookup():
    """Only the resolver may name the variable as a lookup key."""
    lookups = [
        (name, line.strip())
        for name in ("app/main.py", "app/utils/outside_calls_override.py")
        for line in (REPO_ROOT / name).read_text(encoding="utf-8").splitlines()
        if '"OUTSIDECALLSOVERRIDE"' in line
    ]
    assert len(lookups) == 1, f"expected exactly one env lookup, found: {lookups}"
    assert lookups[0][0] == "app/utils/outside_calls_override.py"


def test_main_keeps_no_copy_of_the_flag():
    import app.main as main_module

    assert not hasattr(main_module, "OUTSIDE_CALLS_OVERRIDE_ENABLED"), (
        "app.main parsed OUTSIDECALLSOVERRIDE into a constant of its own; that "
        "parse strips whitespace and the util's did not, which is the bug"
    )


def test_flag_is_not_frozen_at_import_time(monkeypatch):
    """No module-level snapshot may survive, or the load_dotenv() ordering bites."""
    monkeypatch.setenv("OUTSIDECALLSOVERRIDE", "on")
    assert is_override_enabled() is True
    monkeypatch.setenv("OUTSIDECALLSOVERRIDE", "off")
    assert is_override_enabled() is False
