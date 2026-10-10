"""Every service URL the code falls back to must stay inside the stack.

docs/to-do.md: "Ensure self-hosted deployments never transmit genomic data
externally." Audited 2026-10-05: everything the app and the sidecars send a sample's
data to is reached through an environment variable whose default names a compose
service (http://pharmcat:5000, http://fhir-server:8080/fhir, ...). The audit's one
false alarm says why this needs pinning: render_with_kroki's docstring said it
fell back to the public https://kroki.io, which would have sent each run's workflow
diagram off the machine. The code fell back to localhost; the docstring was wrong.
A default that did point outside would be invisible in a normal run, because compose
sets every one of these variables.

So every `os.getenv` / `os.environ.get` default that is a URL, in app/ and docker/,
must name a compose service or the local host. The one exception is SOURCE_URL: the
AGPL source link shown to users, which nothing requests.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[1]
SHOWN_NOT_REQUESTED = {"SOURCE_URL"}


def _url_defaults():
    for path in sorted(
        [*ROOT.joinpath("app").rglob("*.py"), *ROOT.joinpath("docker").rglob("*.py")]
    ):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or len(node.args) < 2:
                continue
            func = node.func
            is_getenv = isinstance(func, ast.Attribute) and func.attr == "getenv"
            is_environ_get = (
                isinstance(func, ast.Attribute)
                and func.attr == "get"
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "environ"
            )
            if not (is_getenv or is_environ_get):
                continue
            key, default = node.args[:2]
            if (
                isinstance(key, ast.Constant)
                and isinstance(default, ast.Constant)
                and isinstance(default.value, str)
                and re.match(r"https?://", default.value)
            ):
                yield path.relative_to(ROOT), node.lineno, key.value, default.value


def _internal_hosts():
    compose = yaml.safe_load((ROOT / "compose.yml").read_text(encoding="utf-8"))
    return set(compose["services"]) | {"localhost", "127.0.0.1"}


def test_the_scan_finds_the_service_defaults():
    """Guards the guard: a broken scan would pass vacuously."""
    keys = {key for _, _, key, _ in _url_defaults()}
    assert {"PHARMCAT_API_URL", "FHIR_SERVER_URL", "NEXTFLOW_RUNNER_URL"} <= keys


def test_service_url_defaults_stay_inside_the_stack():
    internal = _internal_hosts()
    outside = [
        f"{path}:{line} {key}={url}"
        for path, line, key, url in _url_defaults()
        if key not in SHOWN_NOT_REQUESTED and urlparse(url).hostname not in internal
    ]
    assert (
        not outside
    ), "service URLs that default to a host outside the stack:\n  " + "\n  ".join(
        outside
    )
