"""Every published image is built for linux/amd64, whichever machine cuts the release.

`compose.yml` asks for `platform: linux/amd64` on every image it pulls, and
`docs/user/installation.md` says the published images are `linux/amd64`. Before this
pin, `docker buildx bake --push` built for the builder's own platform, so that held only
because releases have been cut on an amd64 machine: cut one on Apple Silicon and arm64
images would go out under the same tags.

Read as text rather than through `docker buildx bake --print`, so the suite does not
need a Docker daemon to check it.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BAKE = (REPO / "docker-bake.hcl").read_text(encoding="utf-8")


def _blocks() -> dict[str, str]:
    """`target "name" { ... }` bodies by name. Bake blocks here do not nest braces."""
    return {
        m.group(1): m.group(2)
        for m in re.finditer(r'^target "([^"]+)" \{(.*?)^\}', BAKE, re.M | re.S)
    }


def test_the_shared_target_pins_amd64_only():
    shared = _blocks().get("_release")
    assert shared is not None, "the shared _release target is gone"
    assert re.search(
        r'platforms\s*=\s*\["linux/amd64"\]', shared
    ), "_release no longer pins platforms to exactly linux/amd64"


def test_every_release_target_inherits_it():
    targets = {k: v for k, v in _blocks().items() if not k.startswith("_")}
    assert (
        len(targets) == 7
    ), f"expected the seven baked images, found {sorted(targets)}"
    missing = [
        name
        for name, body in targets.items()
        if not re.search(r'inherits\s*=\s*\[[^\]]*"_release"', body)
    ]
    assert not missing, f"built for the builder's own platform, not amd64: {missing}"


def test_no_target_overrides_the_platform():
    overriding = [
        name
        for name, body in _blocks().items()
        if not name.startswith("_") and "platforms" in body
    ]
    assert not overriding, f"these set their own platforms: {overriding}"


def test_the_hand_built_mtdna_image_is_pinned_too():
    """mtdna is not a bake target; its build command is in the release procedure."""
    procedure = (REPO / "docs" / "developer" / "contributing.md").read_text(
        encoding="utf-8"
    )
    build = re.search(r"docker build[^\n]*\\\n[^\n]*zaropgx-mtdna", procedure)
    assert build, "the mtdna build command is no longer in contributing.md"
    assert "--platform linux/amd64" in build.group(
        0
    ), "the mtdna build no longer passes --platform linux/amd64"
