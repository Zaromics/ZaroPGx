"""CI's Lint job must run the same black/isort/flake8 the lock pins.

It used to `uv tool install black` unpinned, so CI linted with whatever PyPI
served that day while developers ran the locked version: the branch
fix/v0.3.2-bootstrap went red on formatting nobody could reproduce locally.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _locked(name: str) -> str:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    return next(p["version"] for p in lock["package"] if p["name"] == name)


def test_ci_lint_tools_are_pinned_to_the_locked_versions():
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    for tool in ("black", "isort", "flake8"):
        pins = re.findall(rf"uv tool install {tool}==([\w.]+)", ci)
        assert pins, f"{tool} is installed unpinned in ci.yml"
        assert pins == [_locked(tool)], (tool, pins, _locked(tool))
