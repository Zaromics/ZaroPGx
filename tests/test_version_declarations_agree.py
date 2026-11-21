"""The version the API reports and the version the report cites are one number.

They are declared independently. ``app/main.py``'s ``APP_VERSION`` is what the API
serves -- ``/api-status``, the OpenAPI document, and ``build_identity``, whose startup
warning compares it against the image's own tag. The report's provenance comes from
somewhere else: ``generator.get_zaropgx_version()`` reads ``pyproject.toml``'s
``version`` unless ``ZAROPGX_VERSION`` overrides it, and ``credits.py`` prints that
beside the tools that produced the result.

So a release that bumps one and not the other leaves the report citing one version of
this software and the API stating another, with nothing to say which produced the file
in front of you. Measured before this test existed: setting ``APP_VERSION`` to a number
the package does not have passes **412** of the version, credits, identity, bootstrap
and citation tests without a murmur.

The other links are already pinned, which is why only this one needed writing:
``test_bootstrap_contract.py`` ties both bootstrap scripts and the ``.env`` profiles to
one release constant, ``test_compose_contract.py`` ties those profiles to compose's
default tag, and ``test_credits_docs.py`` ties ``CITATION.cff`` to ``pyproject``.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: ``APP_VERSION = "0.4.3"`` in app/main.py, read as text so that importing the app --
#: which wants a database -- is not the price of checking a string.
APP_VERSION = re.compile(r'^APP_VERSION\s*=\s*"([^"]+)"', re.M)


def _packaged() -> str:
    with (REPO / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]["version"]


def _served() -> str:
    text = (REPO / "app" / "main.py").read_text(encoding="utf-8")
    match = APP_VERSION.search(text)
    assert match, "app/main.py no longer declares APP_VERSION as a plain string"
    return match.group(1)


def test_the_api_serves_the_version_the_package_has():
    served, packaged = _served(), _packaged()
    assert served == packaged, (
        f"the API reports {served} and the package is {packaged}, so the report "
        "cites one version of this software and the API states another"
    )


def test_the_report_takes_its_provenance_from_the_package():
    """Pin where the report's version comes from: the test above compares to it."""
    path = REPO / "app" / "reports" / "generator.py"
    generator = path.read_text(encoding="utf-8")
    assert "_read_version_from_pyproject" in generator, (
        "get_zaropgx_version no longer reads pyproject.toml, so the comparison "
        "above no longer covers what the report prints"
    )
    assert (
        'os.getenv("ZAROPGX_VERSION")' in generator
    ), "the documented override is gone; a run can no longer state its own version"


def test_both_are_a_plain_version_a_tag_can_carry():
    for name, value in (("app/main.py", _served()), ("pyproject.toml", _packaged())):
        assert re.fullmatch(r"\d+\.\d+\.\d+(?:[-.][0-9A-Za-z.]+)?", value), (
            f"{name} declares {value!r}, which a tag of the form v<version> cannot "
            "carry cleanly"
        )


# --- Every other place the release number is written ------------------------------------
#
# The 0.4.4 bump missed seven of them, which 0.4.3's had found: docker-bake.hcl's default
# tag, config/genes.json, the PharmCAT router's /health, the pharmcat and pypgx sidecars'
# FastAPI version and pypgx's root, and uv.lock's own entry for this package. None was
# tested, so 0.4.4's pharmcat and pypgx images say 0.4.3 about themselves. uv.lock went
# unnoticed because CI syncs with --frozen, which does not compare the lock to pyproject.
#
# Two checks, because either alone has a hole. The sweep finds a stale quoted version
# anywhere, including a site nobody listed; the list proves the sweep still sees the
# sites it is meant to, so a site rewritten into a shape the pattern misses cannot drop
# out of it silently.

#: A quoted dotted version, exactly: "0.4.4" or '0.4.4'.
QUOTED_VERSION = re.compile(r"""["'](\d+)\.(\d+)\.(\d+)["']""")

#: The first ZaroPGx release. A quoted version older than this is not ours.
FIRST_RELEASE = (0, 3, 0)

#: History, fixtures and other packages: these name past releases on purpose.
NOT_DECLARATIONS = ("docs/", "tests/", "UPGRADING.md", "CHANGELOG", "uv.lock")

#: Every file that declares the release as a quoted string. Unquoted sites (compose's
#: tag defaults, the .env profiles) are pinned by test_compose_contract.py and
#: test_bootstrap_contract.py.
DECLARED_IN = (
    "pyproject.toml",
    "app/main.py",
    "app/api/routes/pharmcat_router.py",
    "config/genes.json",
    "docker-bake.hcl",
    "bootstrap.sh",
    "bootstrap.ps1",
    "start-docker.sh",
    "start-docker.ps1",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    "docker/gatk-api/gatk_api.py",
    "docker/genome-downloader/downloader_api.py",
    "docker/nextflow/runner.py",
    "docker/pharmcat/pharmcat.py",
    "docker/pypgx/pypgx_wrapper.py",
)


def _release() -> tuple[int, int, int]:
    major, minor, patch = (int(x) for x in _packaged().split(".")[:3])
    return major, minor, patch


def _tracked() -> list[Path]:
    import subprocess

    names = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    return [
        REPO / name
        for name in names
        if not name.startswith(NOT_DECLARATIONS) and (REPO / name).is_file()
    ]


def _quoted_versions(path: Path) -> list[tuple[int, tuple[int, int, int]]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        for match in QUOTED_VERSION.finditer(line):
            found.append((number, tuple(int(g) for g in match.groups())))
    return found


def test_no_file_still_declares_an_earlier_release():
    release = _release()
    stale = [
        f"{path.relative_to(REPO)}:{line} says {'.'.join(map(str, version))}"
        for path in _tracked()
        for line, version in _quoted_versions(path)
        if FIRST_RELEASE <= version < release
    ]
    assert not stale, (
        f"the release is {_packaged()}, but these still name an earlier one:\n  "
        + "\n  ".join(stale)
    )


def test_the_sweep_still_sees_every_declaration():
    release = _release()
    unseen = [
        name
        for name in DECLARED_IN
        if release not in {v for _, v in _quoted_versions(REPO / name)}
    ]
    assert not unseen, (
        f"these no longer declare {_packaged()} as a quoted string, so the sweep "
        f"above cannot see them: {unseen}"
    )


def test_the_lock_records_the_package_version():
    with (REPO / "uv.lock").open("rb") as handle:
        packages = tomllib.load(handle)["package"]
    ours = [p for p in packages if p.get("name") == "zaropgx"]
    assert len(ours) == 1, "uv.lock has no single entry for this package"
    assert ours[0]["version"] == _packaged(), (
        f"uv.lock records zaropgx {ours[0]['version']}, pyproject says {_packaged()}; "
        "CI's `uv sync --frozen` does not notice"
    )
