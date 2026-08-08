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

A third parser survived that pass, on the far side of an image boundary:
``docker/pharmcat/pharmcat.py`` re-read the flag with its own
``os.environ.get("OUTSIDECALLSOVERRIDE", "").lower()`` -- again with no
``.strip()`` -- to decide whether to copy a manual override file over the
uploaded outside-calls TSV. That branch is now deleted rather than repaired,
because it was **unreachable under Compose**: ``compose.yml`` gives an
``env_file:`` to the ``app`` service only, and the ``pharmcat`` service's
``environment:`` block never lists the variable, so it was unset in that
container and the branch was unconditionally False. Repairing it would also have
meant a second parser in an image the app-side tests cannot reach.

Nothing is lost: ``app/pharmcat/pharmcat_client.py`` resolves the override with
the single resolver and posts the resulting file as the ``outside_tsv`` multipart
part, which the sidecar's remaining branch handles -- and which additionally
applies the PyPGx->PharmCAT synonym translation the deleted branch skipped.
"""

from __future__ import annotations

import ast
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


def _env_lookup_sites():
    """Every line of Python under app/ or docker/ that names the flag as a key.

    A lookup *key* is the quoted name passed to os.getenv/os.environ.get, which
    is what a second parser looks like. Prose mentioning the flag in a comment or
    docstring is left alone -- explaining why a service does not read it is the
    opposite of the defect.
    """
    sites = []
    for path in sorted(REPO_ROOT.glob("app/**/*.py")) + sorted(
        REPO_ROOT.glob("docker/**/*.py")
    ):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if '"OUTSIDECALLSOVERRIDE"' in line or "'OUTSIDECALLSOVERRIDE'" in line:
                sites.append((path.relative_to(REPO_ROOT).as_posix(), number, stripped))
    return sites


def test_flag_name_appears_in_exactly_one_env_lookup():
    """Only the resolver may name the variable as a lookup key.

    Scanned across app/ *and* docker/ rather than the two files the original fix
    touched: the third parser lived in docker/pharmcat/pharmcat.py and a scan
    limited to app/ could not have seen it.
    """
    lookups = _env_lookup_sites()
    assert len(lookups) == 1, f"expected exactly one env lookup, found: {lookups}"
    assert lookups[0][0] == "app/utils/outside_calls_override.py"


def test_the_pharmcat_sidecar_does_not_re_parse_the_flag():
    """The sidecar's copy is deleted, not merely fixed.

    It was unreachable anyway -- compose.yml gives an env_file: to the app
    service only, and the pharmcat service's environment: block never lists the
    variable -- and a second parser across an image boundary is one no app-side
    test can hold in step with the first.
    """
    source = (REPO_ROOT / "docker" / "pharmcat" / "pharmcat.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    lookups = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and any(
            isinstance(arg, ast.Constant) and arg.value == "OUTSIDECALLSOVERRIDE"
            for arg in node.args
        )
    ]
    assert not lookups, (
        "docker/pharmcat/pharmcat.py re-parses OUTSIDECALLSOVERRIDE; the single "
        "resolver is app/utils/outside_calls_override.py:is_override_enabled()"
    )
    assert "OUTSIDE_CALLS_OVERRIDE_PATH" not in {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }, "the override path constant outlived the branch that used it"


def test_compose_does_not_pass_the_flag_to_the_pharmcat_service():
    """Documents the reachability claim the deletion rests on.

    Written as a note-to-self rather than a rule: if someone later *does* pass
    the variable to that service, this failing is the reminder that the sidecar
    no longer acts on it and the app-side resolver is what actually applies.
    """
    compose = (REPO_ROOT / "compose.yml").read_text(encoding="utf-8")
    assert "OUTSIDECALLSOVERRIDE" not in compose, (
        "compose.yml now mentions OUTSIDECALLSOVERRIDE. The pharmcat sidecar no "
        "longer reads it -- the override is resolved app-side by "
        "app/utils/outside_calls_override.py and shipped as the outside_tsv "
        "upload. Update this test and say which service is meant to see it."
    )


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
