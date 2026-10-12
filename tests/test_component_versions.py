"""Each service records the versions of the tools inside it, and PyPGx what it ran.

A report credits the tools that did the work (Beagle phasing for PyPGx, YARA and GLPK
for OptiType, bcftools in PharmCAT's preprocessor...) at the versions that ran. Those
versions live in each service's manifest under "components" (data/versions/about.md),
and PyPGx's step output says how many genes Beagle phased and how many its
copy-number models called, so a report credits them only when they ran.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.core.version_manager import VersionManager
from tests.test_pypgx_structural_variants import pypgx  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parent.parent


def _manifest(directory: Path, filename: str, body: dict) -> None:
    directory.mkdir(exist_ok=True)
    (directory / filename).write_text(json.dumps(body), encoding="utf-8")


def test_components_are_kept_per_service(tmp_path, monkeypatch):
    monkeypatch.setenv("KROKI_ENABLED", "false")
    versions = tmp_path / "versions"
    _manifest(
        versions,
        "pypgx.json",
        {"name": "PyPGx", "version": "0.27.0", "components": {"Beagle": "5.4"}},
    )
    _manifest(
        versions,
        "mtdna-server-2.json",
        {
            "name": "mtDNA-server-2",
            "version": "v2.1.16",
            "components": {"bcftools": "1.19"},
        },
    )
    _manifest(
        versions,
        "pharmcat.json",
        {"name": "PharmCAT", "version": "3.4.0", "components": {"bcftools": "1.24"}},
    )
    _manifest(versions, "nextflow.json", {"name": "Nextflow", "version": "25.10.8"})

    components = VersionManager(str(versions)).get_components()

    # Keyed by service: the same tool at two versions in two services stays two.
    assert components == {
        "pypgx": {"beagle": "5.4"},
        "mtdna-server-2": {"bcftools": "1.19"},
        "pharmcat": {"bcftools": "1.24"},
    }


def test_a_malformed_components_field_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("KROKI_ENABLED", "false")
    versions = tmp_path / "versions"
    _manifest(
        versions,
        "gatk.json",
        {"name": "GATK", "version": "4.7.0.0", "components": ["picard"]},
    )
    manager = VersionManager(str(versions))
    assert manager.get_components() == {}
    assert manager.get_versions_dict()["gatk"] == "4.7.0.0"


class _Response:
    status_code = 200

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


def test_kroki_and_mermaid_versions_come_from_kroki(tmp_path, monkeypatch):
    monkeypatch.setenv("KROKI_ENABLED", "true")
    monkeypatch.setenv("KROKI_URL", "http://kroki.test:8000")
    asked = []

    def get(url, timeout):
        asked.append(url)
        return _Response(
            {
                "version": {
                    "kroki": {"number": "0.32.1", "build_hash": "x"},
                    "mermaid": "11.16.0",
                },
                "status": "pass",
            }
        )

    monkeypatch.setattr("app.core.version_manager.requests.get", get)
    components = VersionManager(str(tmp_path / "none")).get_components()

    assert asked == ["http://kroki.test:8000/health"]
    assert components == {"kroki": {"kroki": "0.32.1", "mermaid": "11.16.0"}}


def test_kroki_off_is_not_asked(tmp_path, monkeypatch):
    monkeypatch.setenv("KROKI_ENABLED", "false")

    def get(*args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("Kroki was asked although it is off")

    monkeypatch.setattr("app.core.version_manager.requests.get", get)
    assert VersionManager(str(tmp_path / "none")).get_components() == {}


# The manifest table in data/versions/about.md is what the report relies on; each
# service's manifest writer must name every component listed there.
_WRITERS = {
    "pharmcat.json": "docker/pharmcat/start.sh",
    "pypgx.json": "docker/pypgx/setup_pypgx.sh",
    "gatk.json": "docker/gatk-api/gatk_api.py",
    "optitype.json": "docker/zarohla/app.py",
    "mtdna-server-2.json": "docker/mtdna-server-2/app.py",
    "nextflow.json": "docker/nextflow/runner.py",
}


def _documented_components():
    rows = {}
    about = (ROOT / "data" / "versions" / "about.md").read_text(encoding="utf-8")
    for line in about.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 2 and cells[0].endswith(".json"):
            names = re.sub(r"\(.*?\)", "", cells[1])
            rows[cells[0]] = [n.strip() for n in names.split(",") if n.strip()]
    return rows


def test_every_service_writer_is_documented():
    assert set(_documented_components()) == set(_WRITERS)


@pytest.mark.parametrize("manifest", sorted(_WRITERS))
def test_each_writer_records_its_documented_components(manifest):
    source = (ROOT / _WRITERS[manifest]).read_text(encoding="utf-8")
    assert manifest in source
    for name in _documented_components()[manifest]:
        assert re.search(
            rf"""["']{re.escape(name)}["']|\\"{re.escape(name)}\\\"""", source
        ), f"{_WRITERS[manifest]} does not record {name}"


# ---------------------------------------------------------------------------
# PyPGx: which genes Beagle phased and the copy-number models called
# ---------------------------------------------------------------------------


def test_pipeline_methods_reads_the_files_pypgx_wrote(pypgx, tmp_path):
    gene_dir = tmp_path / "CYP2D6-pipeline"
    gene_dir.mkdir()
    assert pypgx.pipeline_methods(gene_dir) == {"beagle": False, "cnv_model": False}
    (gene_dir / "phased-variants.zip").write_bytes(b"")
    (gene_dir / "cnv-calls.zip").write_bytes(b"")
    assert pypgx.pipeline_methods(gene_dir) == {"beagle": True, "cnv_model": True}


def test_a_gene_result_says_what_ran(pypgx, monkeypatch, tmp_path):
    class Popen:
        def __init__(self, cmd, **kwargs):
            # run-ngs-pipeline <gene> <dir>: phase, as PyPGx does for unphased input.
            Path(cmd[3]).mkdir(parents=True)
            (Path(cmd[3]) / "phased-variants.zip").write_bytes(b"")
            self.pid, self.returncode = 4242, 0

        def communicate(self, timeout=None):
            return "", ""

    monkeypatch.setattr(pypgx.subprocess, "Popen", Popen)
    monkeypatch.setattr(pypgx, "parse_pypgx_results", lambda *a: ("*1/*2", {}))
    result = pypgx.run_pypgx(
        str(tmp_path / "in.vcf.gz"), str(tmp_path), "CYP2C9", "GRCh38"
    )

    assert result["success"]
    assert result["methods"] == {"beagle": True, "cnv_model": False}
