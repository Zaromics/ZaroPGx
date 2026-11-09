"""A module a sidecar image copies on its own cannot import the ``app`` package.

Several `app/` modules are COPYed into the sidecar images one file at a time --
`app/utils/pharmcat_assume_ref.py` lands in `/assume-ref-lib/` and is imported
as a bare `pharmcat_assume_ref`, with no `app` package anywhere in that image.
A module-level `from app... import ...` in such a file therefore raises
`ModuleNotFoundError: No module named 'app'` inside the sidecar, before it does
any work.

That is not hypothetical: adding one to `pharmcat_assume_ref` made PharmCAT
fail every run, which showed only as a job that finished with no PharmCAT files
and no report links. Unit tests all passed -- they import the module from the
repo, where `app` is importable -- and only the e2e lane caught it.

The rule here is **no `app` import at any level**, not merely none at module
scope. An import nested inside a function would survive the sidecar only while
the sidecar never calls that function, which is a precondition nobody can see
from the file and nothing can check. Code that needs the rest of `app` belongs
on the app side instead: `assume_ref_alert` moved to `app/reports/assume_ref.py`
for exactly this reason, leaving this file as shared flag logic with no `app`
dependency at all.
"""

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILES = sorted(p for p in (ROOT / "docker").rglob("Dockerfile*") if p.is_file())


def _standalone_copies() -> list[Path]:
    """Every `app/...` *file* a Dockerfile copies, as opposed to a package."""
    found: set[Path] = set()
    for dockerfile in DOCKERFILES:
        for line in dockerfile.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*COPY\s+(app/\S+)\s+\S+", line)
            if not m:
                continue
            src = m.group(1)
            if src.endswith("/"):  # a whole package keeps its __init__
                continue
            path = ROOT / src
            if path.is_file() and path.suffix == ".py":
                found.add(path)
    return sorted(found)


STANDALONE = _standalone_copies()


def test_the_audit_found_the_modules_it_is_meant_to_police():
    names = {p.name for p in STANDALONE}

    assert "pharmcat_assume_ref.py" in names, names
    assert "job_client.py" in names, names
    assert len(STANDALONE) >= 5, names


@pytest.mark.parametrize("path", STANDALONE, ids=lambda p: p.name)
def test_no_app_import_anywhere(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    offenders = []
    for node in ast.walk(tree):  # any level: a nested one is no safer
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app"):
            offenders.append((node.lineno, f"from {node.module} import ..."))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("app"):
                    offenders.append((node.lineno, f"import {alias.name}"))

    assert not offenders, (
        f"{path.relative_to(ROOT)} is copied into a sidecar image on its own, "
        f"where no 'app' package exists, but imports it: {offenders}. "
        "Move the code that needs 'app' to the app side -- nesting the import "
        "only hides the failure until the sidecar calls that function."
    )
