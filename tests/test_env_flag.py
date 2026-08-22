"""Truth table for the shared env_flag() helper.

Decision (adopted, see .superpowers/sdd/2026-08-22-queued-followups/task-5-brief.md):
an explicitly blank boolean env var means the flag is off, regardless of the
site's own default. Unset means the site's default applies. Truthy spellings
are 1/true/yes/on (case-insensitive, whitespace-tolerant); anything else that
is set but unrecognised also reads False.
"""

import pytest

from app.utils.env import env_flag

TRUTHY = [
    "1",
    "true",
    "True",
    "TRUE",
    "yes",
    "Yes",
    "YES",
    "on",
    "On",
    "ON",
    " true ",
    "\ttrue\n",
]

FALSY_SET = ["false", "False", "FALSE", "0", "no", "off", "nope", "garbage", " "]

ENV_VAR = "ZPGX_TEST_FLAG_5"


def test_unset_returns_default_false(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert env_flag(ENV_VAR) is False


def test_unset_returns_default_true(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert env_flag(ENV_VAR, True) is True


def test_blank_means_off_even_when_default_is_true(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "")
    assert env_flag(ENV_VAR, True) is False


def test_whitespace_only_means_off(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "   ")
    assert env_flag(ENV_VAR, True) is False


@pytest.mark.parametrize("raw", TRUTHY)
def test_truthy_spellings(monkeypatch, raw):
    monkeypatch.setenv(ENV_VAR, raw)
    assert env_flag(ENV_VAR, False) is True, f"{raw!r} should read True"


@pytest.mark.parametrize("raw", FALSY_SET)
def test_falsy_and_garbage_spellings(monkeypatch, raw):
    monkeypatch.setenv(ENV_VAR, raw)
    assert env_flag(ENV_VAR, True) is False, f"{raw!r} should read False"


def test_reads_lazily_not_cached_at_import(monkeypatch):
    """No import-time snapshot: flipping os.environ after import must flip the read."""
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert env_flag(ENV_VAR, False) is False
    monkeypatch.setenv(ENV_VAR, "true")
    assert env_flag(ENV_VAR, False) is True
    monkeypatch.setenv(ENV_VAR, "")
    assert env_flag(ENV_VAR, False) is False


# --------------------------------------------------------------------------
# migration guard: the four private near-copies must be gone
# --------------------------------------------------------------------------
def test_no_private_env_flag_copies_remain():
    """Grep guard: the four private ``_env_flag`` definitions this task removed
    (app/main.py, app/api/routes/upload_router.py, app/reports/generator.py,
    app/visualizations/workflow_diagram.py) must not come back."""
    import re
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    hits = [
        f"{path.relative_to(repo_root).as_posix()}:{i}"
        for path in sorted(repo_root.glob("app/**/*.py"))
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if re.match(r"\s*def _env_flag\(", line)
    ]
    assert not hits, f"stray _env_flag definitions remain: {hits}"


def test_migrated_sites_share_one_helper():
    """The four migrated modules must import the same function object -- a
    fresh copy would silently reintroduce a split-brain."""
    import app.api.routes.upload_router as upload_router_module
    import app.main as main_module
    import app.reports.generator as generator_module
    import app.visualizations.workflow_diagram as wd_module

    for module in (
        main_module,
        upload_router_module,
        generator_module,
        wd_module,
    ):
        assert module.env_flag is env_flag, f"{module.__name__} has its own env_flag"
        assert not hasattr(
            module, "_env_flag"
        ), f"{module.__name__} still has _env_flag"


# --------------------------------------------------------------------------
# KROKI split-brain regression
#
# app.main computed KROKI_ENABLED with the shared _env_flag (blank -> False),
# but app.visualizations.workflow_diagram had its own copy that treated a
# blank value as unset and fell back to its own default=True. So
# KROKI_ENABLED="" disabled the /kroki status the UI reported while Kroki
# rendering itself stayed on. Sharing one helper makes both sides agree.
# --------------------------------------------------------------------------
def test_kroki_blank_reads_false_the_way_main_computes_it(monkeypatch):
    monkeypatch.setenv("KROKI_ENABLED", "")
    assert env_flag("KROKI_ENABLED", True) is False


def test_render_with_kroki_disabled_by_blank_env(monkeypatch):
    """workflow_diagram.render_with_kroki must treat KROKI_ENABLED="" as
    disabled, not fall back to its call site's own default=True."""
    from unittest.mock import patch

    from app.visualizations import workflow_diagram as wd

    monkeypatch.setenv("KROKI_ENABLED", "")
    monkeypatch.delenv("KROKI_TIMEOUT", raising=False)
    monkeypatch.delenv("KROKI_URL", raising=False)
    with patch.object(wd.requests, "post") as post:
        with pytest.raises(RuntimeError, match="Kroki disabled"):
            wd.render_with_kroki("flowchart TD\n  A-->B", fmt="svg")
        post.assert_not_called()
