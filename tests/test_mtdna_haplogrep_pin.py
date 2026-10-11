"""The mtDNA citation and the sidecar's tree must name what the image installs.

generator.py's mtDNA citation hardcodes the component versions rather than
reading the components the sidecar publishes to /data/versions/mtdna-server-2.json,
so this ties that literal, and app.py's PHYLOTREE, to the Dockerfile pins. The
citation can't then silently claim a haplogrep3 the image no longer ships.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = (ROOT / "docker/mtdna-server-2/Dockerfile").read_text(encoding="utf-8")


def _arg(name: str) -> str:
    m = re.search(rf"^ARG {name}=(\S+)$", DOCKERFILE, re.M)
    assert m, f"ARG {name} missing from docker/mtdna-server-2/Dockerfile"
    return m.group(1)


def test_citation_names_the_installed_haplogrep3():
    from app.reports.generator import build_citations

    text = next(c["text"] for c in build_citations() if c["name"] == "mtDNA-server-2")
    assert f"haplogrep3 {_arg('HAPLOGREP_RELEASE')}" in text, text


def test_sidecar_classifies_with_the_installed_tree():
    app_py = (ROOT / "docker/mtdna-server-2/app.py").read_text(encoding="utf-8")
    assert f'PHYLOTREE = "{_arg("PHYLOTREE")}"' in app_py
