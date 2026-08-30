"""The mtDNA service is declared consistently everywhere it has to be."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


def _compose() -> dict:
    return yaml.safe_load((REPO_ROOT / "compose.yml").read_text(encoding="utf-8"))


def test_the_service_exists():
    assert "mtdna" in _compose()["services"]


def test_it_binds_5062_not_a_port_another_service_owns():
    """5060 is zarohla; 5061 is reserved by the commented hlatyping block."""
    ports = _compose()["services"]["mtdna"]["ports"]
    assert any(":5062:5000" in str(p) for p in ports), ports


def test_it_pins_the_upstream_image_tag():
    dockerfile = (REPO_ROOT / "docker/mtdna-server-2/Dockerfile").read_text(
        encoding="utf-8"
    )
    assert "quay.io/genepi/mtdna-server-2:v2.1.16" in dockerfile


def test_reference_is_mounted_read_only():
    """It needs the liftover chain for hg19 input, and stages nothing itself."""
    volumes = [str(v) for v in _compose()["services"]["mtdna"]["volumes"]]
    assert any(v.endswith("/reference:ro") for v in volumes), volumes


def test_the_app_knows_where_to_reach_it():
    main_py = (REPO_ROOT / "app/main.py").read_text(encoding="utf-8")
    assert "MTDNA_API_URL" in main_py
    assert "http://mtdna:5000" in main_py
