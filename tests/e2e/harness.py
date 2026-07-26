"""Pure helpers for the full-stack e2e harness (env + vacuous-run guard)."""

from __future__ import annotations

from typing import Mapping, MutableMapping, Optional


def e2e_requested(
    environ: Optional[Mapping[str, str]] = None,
    *,
    cli_flag: bool = False,
) -> bool:
    """True when the caller intends to run full-stack e2e tests.

    Accepts either ZAROPGX_E2E=1 or an explicit CLI flag. The CLI path exists
    because Git Bash ``export`` often does not reach Win32 ``python.exe``.
    """
    if cli_flag:
        return True
    env = environ if environ is not None else __import__("os").environ
    return env.get("ZAROPGX_E2E") == "1"


def apply_e2e_env(
    environ: MutableMapping[str, str],
    *,
    cli_flag: bool = False,
    default_base_url: str = "http://127.0.0.1:18765",
) -> bool:
    """If e2e was requested, ensure ZAROPGX_E2E / BASE_URL are set. Return requested."""
    requested = e2e_requested(environ, cli_flag=cli_flag)
    if not requested:
        return False
    environ["ZAROPGX_E2E"] = "1"
    environ.setdefault("ZAROPGX_E2E_BASE_URL", default_base_url)
    return True


def vacuous_e2e_failure(
    *,
    requested: bool,
    passed: int,
    exitstatus: int,
) -> bool:
    """True when e2e was requested but nothing passed and pytest still exited 0.

    That pattern is the Git Bash skip-with-green-exit bug: all e2e tests skipped
    because ZAROPGX_E2E never reached the process.
    """
    return bool(requested) and passed == 0 and exitstatus == 0
