"""The PyPGx the image installs, the version the bundle names, and the tables we copied.

Three facts that must move together, and nothing moved them together:

* ``docker/pypgx/Dockerfile.pypgx`` installs a pinned ``pypgx==X``. That is the PyPGx
  that calls.
* ``docker/genome-downloader/annotation_bundle.py`` declares ``PYPGX_VERSION`` for what
  it fetches and records.
* ``lexicon/gene-table.csv`` and ``lexicon/allele-table.csv`` are **copies of PyPGx's
own
  data files** -- the same rows, verbatim: this repository's SLC22A2 definitions match
  the ones inside ``zaromicsresearch/zaropgx-pypgx`` exactly.

Bump the Dockerfile alone and the lexicon is quietly a previous release's answer.
Nothing
fails. ``test_gene_set_relationships.py`` keeps passing, because it compares
``config/genes.json`` against that same stale copy; the report keeps naming alleles from
definitions the caller has moved past; and a gene the new PyPGx supports is never
offered.
This is the shape ``test_getrm_allele_snapshot.py`` describes for the scorer's snapshot
--
"nothing fails, and the number is wrong in the flattering direction".

So the check is at the bump: the two declared versions must agree, and the message says
the lexicon is the third thing to refresh. Measured when this was written: the
Dockerfile
pins 0.27.0, the bundle declares 0.27.0, and the shipped image reports 0.27.0.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOCKERFILE = REPO / "docker" / "pypgx" / "Dockerfile.pypgx"
BUNDLE = REPO / "docker" / "genome-downloader" / "annotation_bundle.py"
LEXICON = REPO / "lexicon"


def _installed_version() -> str:
    """The pypgx the image installs, from its pinned pip line."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    found = re.search(r"pypgx==([0-9][0-9A-Za-z.\-]*)", text)
    assert found, f"{DOCKERFILE.name} no longer pins pypgx to a version"
    return found.group(1)


def _declared_version() -> str:
    text = BUNDLE.read_text(encoding="utf-8")
    found = re.search(r'^PYPGX_VERSION\s*=\s*"([^"]+)"', text, re.M)
    assert found, f"{BUNDLE.name} no longer declares PYPGX_VERSION as a literal"
    return found.group(1)


def test_the_bundle_names_the_pypgx_the_image_installs():
    installed, declared = _installed_version(), _declared_version()
    assert declared == installed, (
        f"{BUNDLE.name} declares PyPGx {declared} and {DOCKERFILE.name} installs "
        f"{installed}. Refresh lexicon/gene-table.csv, allele-table.csv AND "
        "phenotype-table.csv from the new release -- keeping the MT-RNR1 trim -- "
        "because they are copies of PyPGx's own tables, and a stale "
        "copy fails nothing -- the gene sets still agree with it, and the report keeps "
        "naming alleles the caller has moved past."
    )


def test_the_lexicon_tables_are_there_to_be_refreshed():
    """The message above names three files to refresh; they must exist."""
    for name in ("gene-table.csv", "allele-table.csv", "phenotype-table.csv"):
        path = LEXICON / name
        assert path.is_file(), f"lexicon/{name} is missing"
        assert len(path.read_text(encoding="utf-8").splitlines()) > 20, (
            f"lexicon/{name} holds almost nothing, so anything checked against it "
            "is checked against nothing"
        )
