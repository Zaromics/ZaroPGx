"""The images take uv from Astral's image, pinned by version and digest.

They used to run `curl -LsSf https://astral.sh/uv/install.sh | sh`, which installs
whatever uv is newest the day the layer is built, and which failed a CI run on an SSL
error. Every Dockerfile that runs uv now copies it from ghcr.io/astral-sh/uv, all at one
version and digest, so a bump moves them together. CI's setup-uv installs the same
version, and pyproject.toml's [tool.uv] required-version names it as the floor.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILES = sorted([ROOT / "Dockerfile", *ROOT.glob("docker/*/Dockerfile*")])
PIN = re.compile(
    r"(?m)^COPY --from=ghcr\.io/astral-sh/uv:(?P<version>\d+\.\d+\.\d+)"
    r"@sha256:(?P<digest>[0-9a-f]{64}) /uv /uvx /usr/local/bin/$"
)
# Any use of uv or uvx in a RUN step (the pinned COPY line itself is not a use).
RUNS_UV = re.compile(
    r"(?m)^RUN\b.*(?:\buv (?:pip|venv|export|sync|run|tool|python|add)\b|\buvx\b)"
)
# `pip install uv` (or `uv pip install … uv==…`) would put a second, unpinned uv on
# PATH over the copied one.
PIP_INSTALLS_UV = re.compile(
    r"\bpip3? install\b[^\n]*?(?<![\w./-])uv(?:\[[^\]]*\])?(?:[=<>!~]=?[^\s]*)?(?![\w.-])"
)


def _name(path: Path) -> str:
    return str(path.relative_to(ROOT))


def _logical(text: str) -> str:
    """Dockerfile text with backslash-continued lines joined."""
    return re.sub(r"\\\n\s*", " ", text)


def _texts() -> dict[str, str]:
    return {_name(p): _logical(p.read_text(encoding="utf-8")) for p in DOCKERFILES}


def _uv_dockerfiles() -> dict[str, str]:
    return {name: text for name, text in _texts().items() if RUNS_UV.search(text)}


def _pinned_version() -> str:
    (version,) = {
        m["version"] for text in _uv_dockerfiles().values() for m in PIN.finditer(text)
    }
    return version


def test_the_images_that_run_uv_are_the_ones_expected():
    assert set(_uv_dockerfiles()) == {
        "Dockerfile",
        "docker/gatk-api/Dockerfile.gatk-api",
        "docker/genome-downloader/Dockerfile.downloader",
        "docker/nextflow/Dockerfile.nextflow",
        "docker/pharmcat/Dockerfile",
        "docker/pypgx/Dockerfile.pypgx",
    }


def test_the_uv_pattern_sees_every_way_of_running_uv():
    for line in (
        "RUN uv pip install --system x",
        "RUN uv venv /app/venv",
        "RUN uv run --no-project python -c 1",
        "RUN uv tool install black",
        "RUN uv python install 3.12",
        "RUN uv add requests",
        "RUN uvx ruff check",
        "RUN cd /x && uv export --frozen > r.txt",
    ):
        assert RUNS_UV.search(line), line
    for line in (
        "COPY --from=ghcr.io/astral-sh/uv:0.1.0 /uv /uvx /usr/local/bin/",
        'CMD ["uvicorn", "app:app"]',
        "RUN pip install uvicorn",
    ):
        assert not RUNS_UV.search(line), line


def test_no_dockerfile_installs_uv_any_other_way():
    for name, text in _texts().items():
        assert "astral.sh/uv/install.sh" not in text, name
        assert "/root/.local/bin" not in text, name
        assert not PIP_INSTALLS_UV.search(text), name


def test_the_pip_install_pattern_finds_uv_and_only_uv():
    for line in (
        "RUN pip install uv",
        "RUN pip3 install --no-cache-dir uv==0.9.0",
        "RUN uv pip install --system fastapi uv",
        "RUN pip install requests uv>=0.5",
    ):
        assert PIP_INSTALLS_UV.search(line), line
    for line in (
        "RUN uv pip install --system fastapi uvicorn httpx",
        "RUN pip install uvloop",
        "RUN uv pip install --system -r requirements.txt",
    ):
        assert not PIP_INSTALLS_UV.search(line), line


def test_each_image_copies_uv_pinned_before_it_runs_it():
    for name, text in _uv_dockerfiles().items():
        pins = list(PIN.finditer(text))
        assert len(pins) == 1, name
        assert pins[0].start() < RUNS_UV.search(text).start(), name


def test_every_image_pins_the_same_uv():
    pins = {
        (m["version"], m["digest"])
        for text in _uv_dockerfiles().values()
        for m in PIN.finditer(text)
    }
    assert len(pins) == 1, pins


def test_ci_installs_the_uv_the_images_copy():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    steps = re.findall(
        r"uses: astral-sh/setup-uv@\S+\n[ ]+with:\n((?:[ ]+[\w-]+: .*\n)+)", ci
    )
    assert steps, "ci.yml has no setup-uv step"
    for with_block in steps:
        versions = re.findall(r'^\s+version: "([^"]+)"$', with_block, re.M)
        assert versions == [_pinned_version()], with_block


def test_pyproject_requires_at_least_that_uv():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["tool"]["uv"]["required-version"] == f">={_pinned_version()}"
