"""The mtDNA credits and the sidecar's tree must name what the image installs.

The report credits Haplogrep 3 at the version the sidecar records
(HAPLOGREP_VERSION, set from the Dockerfile's HAPLOGREP_RELEASE) and classifies
with app.py's PHYLOTREE; both are tied to the Dockerfile pins here.
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


def test_the_recorded_haplogrep3_is_the_installed_one():
    assert "ENV HAPLOGREP_VERSION=${HAPLOGREP_RELEASE}" in DOCKERFILE
    app_py = (ROOT / "docker/mtdna-server-2/app.py").read_text(encoding="utf-8")
    assert '"haplogrep3": os.getenv("HAPLOGREP_VERSION"' in app_py


def test_sidecar_classifies_with_the_installed_tree():
    app_py = (ROOT / "docker/mtdna-server-2/app.py").read_text(encoding="utf-8")
    assert f'PHYLOTREE = "{_arg("PHYLOTREE")}"' in app_py
