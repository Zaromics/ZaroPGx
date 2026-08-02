"""BACKLOG 66/125/42a — PyPGx→PharmCAT outside-call synonym translation."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.utils import allele_translate as at

FIXTURE_MAP = {
    "ABCG2": {
        "Reference": "rs2231142 reference (G)",
        "rs2231142": "rs2231142 variant (T)",
        "rs2231142 reference (G)": "rs2231142 reference (G)",
        "rs2231142 variant (T)": "rs2231142 variant (T)",
    },
    "IFNL3": {
        "Reference": "rs12979860 reference (C)",
        "rs12979860": "rs12979860 variant (T)",
    },
    "VKORC1": {
        "Reference": "rs9923231 reference (C)",
        "rs9923231": "rs9923231 variant (T)",
    },
    "CYP2D6": {"*1": "*1", "*4": "*4"},
}


def test_translate_haplotype_found_and_absent():
    out, found = at.translate_haplotype("ABCG2", "Reference", FIXTURE_MAP)
    assert found is True
    assert out == "rs2231142 reference (G)"
    out2, found2 = at.translate_haplotype("ABCG2", "NOT_A_REAL_TOKEN", FIXTURE_MAP)
    assert found2 is False
    assert out2 == "NOT_A_REAL_TOKEN"


def test_translate_diplotype_abcg2():
    dip, unmapped = at.translate_diplotype("ABCG2", "Reference/rs2231142", FIXTURE_MAP)
    assert dip == "rs2231142 reference (G)/rs2231142 variant (T)"
    assert unmapped == []


def test_translate_outside_tsv_text_synonym_only():
    text = (
        "Gene\tDiplotype\tPhenotype\tActivityScore\n"
        "ABCG2\tReference/rs2231142\t\t\n"
        "IFNL3\tReference/rs12979860\t\t\n"
        "VKORC1\trs9923231/Reference\t\t\n"
        "CYP2D6\t*1/*4\t\t\n"
        "HLA-A\tA*01:01,A*24:02\n"
        "# comment\n"
    )
    out = at.translate_outside_tsv_text(text, FIXTURE_MAP)
    lines = out.splitlines()
    assert lines[0].startswith("Gene\t")
    assert lines[1] == "ABCG2\trs2231142 reference (G)/rs2231142 variant (T)\t\t"
    assert "rs12979860 reference (C)/rs12979860 variant (T)" in lines[2]
    assert "rs9923231 variant (T)/rs9923231 reference (C)" in lines[3]
    assert lines[4] == "CYP2D6\t*1/*4\t\t"
    assert lines[5] == "HLA-A\tA*01:01,A*24:02"
    assert lines[6] == "# comment"


def test_translate_outside_tsv_file_inplace(tmp_path: Path):
    p = tmp_path / "x.outside.tsv"
    p.write_text("ABCG2\tReference/rs2231142\t\t\n", encoding="utf-8")
    at.translate_outside_tsv_file(p, FIXTURE_MAP)
    assert (
        p.read_text(encoding="utf-8").rstrip("\r\n")
        == "ABCG2\trs2231142 reference (G)/rs2231142 variant (T)\t\t"
    )


def test_unmapped_haplotype_passthrough():
    dip, unmapped = at.translate_diplotype("ABCG2", "Reference/WEIRD", FIXTURE_MAP)
    assert dip == "rs2231142 reference (G)/WEIRD"
    assert unmapped == ["WEIRD"]


def test_identity_map_hit_not_unmapped():
    dip, unmapped = at.translate_diplotype(
        "ABCG2", "rs2231142 reference (G)/rs2231142 variant (T)", FIXTURE_MAP
    )
    assert "rs2231142 reference (G)" in dip
    assert unmapped == []


def test_pharmcat_dockerfile_copies_lexicon_assets():
    df = Path("docker/pharmcat/Dockerfile").read_text(encoding="utf-8")
    assert "allele_translate.py" in df
    assert "allele_map_pypgx_to_pharmcat.json" in df
    assert "/lexicon-lib/" in df


def test_repo_lexicon_candidate_skips_container_layout(tmp_path: Path):
    """Container copy is /lexicon-lib/allele_translate.py — must not use parents[2]."""
    container_mod = tmp_path / "lexicon-lib" / "allele_translate.py"
    container_mod.parent.mkdir(parents=True)
    container_mod.write_text("# stub\n", encoding="utf-8")
    assert at._repo_lexicon_candidate(container_mod) is None


def test_repo_lexicon_candidate_resolves_app_utils_layout(tmp_path: Path):
    mod = tmp_path / "app" / "utils" / "allele_translate.py"
    mod.parent.mkdir(parents=True)
    mod.write_text("# stub\n", encoding="utf-8")
    expected = tmp_path / "lexicon" / "allele_map_pypgx_to_pharmcat.json"
    assert at._repo_lexicon_candidate(mod) == expected
