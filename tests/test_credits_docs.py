"""The docs, the README and NOTICE credit what the reports credit.

app/reports/credits.py is the one list of what ZaroPGx is built on. The docs'
Credits page and the README's Acknowledgements are written from it by
scripts/render_credits.py; run that after changing the list. NOTICE is written by
hand and records what the repository and images contain and the terms that come
with it.
"""

from __future__ import annotations

import importlib.util
import re
import tomllib
from pathlib import Path

import pytest

from app.reports.credits import CREDITS, PLATFORM

ROOT = Path(__file__).resolve().parent.parent
NOTICE = (ROOT / "NOTICE").read_text(encoding="utf-8")


def _render():
    spec = importlib.util.spec_from_file_location(
        "render_credits", ROOT / "scripts" / "render_credits.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_docs_page_is_current():
    render = _render()
    assert (ROOT / "docs" / "user" / "credits.md").read_text(
        encoding="utf-8"
    ) == render.docs_page(), "run: python scripts/render_credits.py"


def test_the_readme_is_current():
    render = _render()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert render.readme(readme) == readme, "run: python scripts/render_credits.py"


def test_the_docs_page_is_in_the_documentation():
    index = (ROOT / "docs" / "index.md").read_text(encoding="utf-8")
    assert "user/credits" in index


def test_the_upload_page_links_to_it():
    page = (ROOT / "app" / "templates" / "index.html").read_text(encoding="utf-8")
    assert 'href="/docs/user/credits.html"' in page


@pytest.mark.parametrize(
    "vendored", sorted(p.name for p in (ROOT / "app" / "static" / "vendor").iterdir())
)
def test_notice_covers_every_vendored_library(vendored):
    assert f"app/static/vendor/{vendored}/" in NOTICE


@pytest.mark.parametrize(
    "name",
    [
        "ClinPGx",
        "CPIC",
        "PharmVar",
        "UCSC Chain Files",
        "1000 Genomes",
        "IPD-IMGT/HLA",
        "PhyloTree",
        "LOINC",
        "SNOMED CT",
        "HL7",
    ],
)
def test_notice_states_the_terms_of_each_source_that_has_them(name):
    assert name in NOTICE


@pytest.mark.parametrize(
    "credit",
    [c for c in CREDITS if c.kind == "software" and c.key != "zaropgx"],
    ids=lambda c: c.key,
)
def test_notice_names_every_tool_the_images_carry(credit):
    names = {"htslib": "HTSlib", "bwa": "BWA-MEM", "haplogrep": "Haplogrep 3"}
    assert names.get(credit.key, credit.name) in NOTICE, credit.name


@pytest.mark.parametrize(
    "licence, copyright_line",
    [
        ("lexicon/LICENSE.pypgx", 'Copyright (c) 2020 Seung-been "Steven" Lee'),
        (
            "docker/mtdna-server-2/LICENSE.mtdna-server-2",
            "Copyright (c) 2023 Hansi Weißensteiner",
        ),
    ],
)
def test_files_copied_from_mit_projects_carry_the_licence_text(licence, copyright_line):
    """MIT's one condition: its copyright line and permission notice go with copies.

    The PyPGx tables in lexicon/ and mtDNA-Server 2's report.Rmd and rCRS FASTA are
    copied into the repository; NOTICE names the licence file beside them.
    """
    text = (ROOT / licence).read_text(encoding="utf-8")
    assert copyright_line in text
    assert "Permission is hereby granted, free of charge" in text
    assert licence in NOTICE
    assert copyright_line in " ".join(NOTICE.split())


def test_every_platform_component_has_a_licence():
    for item in PLATFORM:
        assert item.licence and item.url.startswith("https://"), item


def test_citation_cff_names_the_release():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    cff = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    assert re.search(
        rf"^version: {re.escape(pyproject['project']['version'])}$", cff, re.M
    )
    assert "license: AGPL-3.0-only" in cff
