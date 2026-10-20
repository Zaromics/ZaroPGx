"""The bundled HAPI FHIR server loads the Genomics Reporting IG from committed files.

compose.yml's fhir-server installs the HL7 Genomics Reporting IG 3.0.0, and the two
packages it depends on, from docker/fhir-server/packages so that its $validate checks
ZaroPGx's bundles against the IG's profiles. Nothing may be fetched: a package named
in compose.yml without its file, or a dependency left out, would either stop HAPI
starting or have it validate without the definitions it needs, which is invisible
until someone reads an OperationOutcome closely. These tests read compose.yml and the
package files; no server runs.
"""

from __future__ import annotations

import hashlib
import json
import re
import tarfile
from pathlib import Path

import pytest

from app.services.fhir import terminology

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = ROOT / "docker" / "fhir-server" / "packages"
MOUNT = "/fhir-packages"
IG_KEY = "hapi.fhir.implementationguides."
# Built into HAPI (its FHIR R4 validation resources), so not a package of ours.
BUILT_IN = {"hl7.fhir.r4.core"}


@pytest.fixture(scope="module")
def fhir_server():
    compose = yaml.safe_load((ROOT / "compose.yml").read_text(encoding="utf-8"))
    return compose["services"]["fhir-server"]


@pytest.fixture(scope="module")
def guides(fhir_server):
    """{key: {setting: value}} from fhir-server's hapi.fhir.implementationguides.*"""
    out: dict[str, dict[str, str]] = {}
    for entry in fhir_server["environment"]:
        name, _, value = str(entry).partition("=")
        if name.startswith(IG_KEY):
            key, _, setting = name[len(IG_KEY) :].partition(".")
            out.setdefault(key, {})[setting] = value
    return out


def _package_json(path: Path) -> dict:
    with tarfile.open(path, "r:gz") as tar:
        member = tar.extractfile("package/package.json")
        assert member is not None, f"{path.name} has no package/package.json"
        return json.load(member)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sums() -> dict[str, str]:
    sums = {}
    for line in (PACKAGES / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, name = line.split()
        sums[name] = digest
    return sums


def test_the_genomics_reporting_ig_is_pinned_and_installed(guides):
    ig = [g for g in guides.values() if g["name"] == "hl7.fhir.uv.genomics-reporting"]
    assert len(ig) == 1
    assert ig[0]["version"] == "3.0.0"
    # As resources, so its profiles can be searched; the dependencies are stored only.
    assert ig[0]["installMode"] == "STORE_AND_INSTALL"
    assert ig[0]["fetchDependencies"] == "false"


def test_every_package_is_a_committed_file_never_a_download(guides):
    assert guides, "fhir-server installs no implementation guide"
    for key, guide in guides.items():
        url = guide.get("packageUrl", "")
        # Without packageUrl, HAPI asks the package registry for name#version.
        assert url.startswith(f"file://{MOUNT}/"), f"{key}: packageUrl {url!r}"
        file = PACKAGES / url[len(f"file://{MOUNT}/") :]
        assert file.is_file(), f"{key}: {file.relative_to(ROOT)} is not committed"
        assert file.name == f"{guide['name']}-{guide['version']}.tgz"
        assert guide.get("fetchDependencies", "false") == "false", key


def test_the_packages_are_mounted_read_only(fhir_server):
    assert f"./docker/fhir-server/packages:{MOUNT}:ro" in fhir_server["volumes"]


def test_each_file_is_the_package_it_is_named_for_with_its_recorded_hash(guides):
    sums = _sums()
    files = {p.name for p in PACKAGES.glob("*.tgz")}
    configured = {g["packageUrl"].rsplit("/", 1)[1] for g in guides.values()}
    assert set(sums) == files == configured, "SHA256SUMS, files and compose.yml differ"
    for guide in guides.values():
        path = PACKAGES / f"{guide['name']}-{guide['version']}.tgz"
        assert _sha256(path) == sums[path.name], f"{path.name} changed"
        meta = _package_json(path)
        assert (meta["name"], meta["version"]) == (guide["name"], guide["version"])


def test_every_dependency_is_installed_at_the_version_asked_for(guides):
    """With nothing fetched, a dependency not listed would simply be missing."""
    installed = {g["name"]: g["version"] for g in guides.values()}
    for guide in guides.values():
        meta = _package_json(PACKAGES / f"{guide['name']}-{guide['version']}.tgz")
        for name, version in meta.get("dependencies", {}).items():
            if name in BUILT_IN:
                continue
            assert installed.get(name) == version, (
                f"{guide['name']} needs {name}#{version}; compose.yml installs "
                f"{installed.get(name)!r}"
            )


def test_the_bundled_ig_defines_every_profile_the_export_names():
    """A bump to the IG that dropped or renamed a profile would leave the export
    naming one the server cannot check against."""
    path = PACKAGES / "hl7.fhir.uv.genomics-reporting-3.0.0.tgz"
    with tarfile.open(path, "r:gz") as tar:
        member = tar.extractfile("package/.index.json")
        assert member is not None
        defined = {f["url"] for f in json.load(member)["files"] if f.get("url")}
    named = {
        value
        for name, value in vars(terminology).items()
        if name.isupper()
        and isinstance(value, str)
        and value.startswith("http://hl7.org/fhir/uv/genomics-reporting/")
    }
    assert named, "the scan found none of the export's IG URLs"
    assert named <= defined, sorted(named - defined)


def test_the_developer_docs_record_each_hash():
    docs = (ROOT / "docs" / "developer" / "architecture.md").read_text(encoding="utf-8")
    for name, digest in _sums().items():
        assert re.search(rf"{re.escape(name)}.*{digest}", docs), name
