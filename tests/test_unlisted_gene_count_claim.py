"""The reports doc's gene count is the one the bundle can actually read.

`docs/user/reports.md` says the unlisted-variant check reads the coding exons of N
genes. The bundle's builder names 52 (`annotation_bundle.GENES`) and records which of
them it found no transcript for, because GRCh38 places GSTT1 off the primary assembly:
51 exon models for 52 names. The doc said 52, which told a reader GSTT1's exons were
screened when they are in no run's regions and never will be.

This pins the claim to the builder's own list, so a gene added to the bundle fails here
rather than quietly making the doc wrong again.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BUILDER = REPO / "docker" / "genome-downloader" / "annotation_bundle.py"
DOC = REPO / "docs" / "user" / "reports.md"

#: GRCh38 has no primary-assembly transcript for these, so the bundle carries no exon
#: model for them. Ensembl 116; the manifest records the same list per build.
WITHOUT_A_TRANSCRIPT = {"GSTT1"}


def _builder_genes() -> list:
    tree = ast.parse(BUILDER.read_text(encoding="utf-8"))
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and getattr(node.targets[0], "id", "") == "GENES"
        ):
            return [element.value for element in node.value.elts]
    raise AssertionError("annotation_bundle.GENES not found")


def test_the_doc_counts_the_genes_whose_exons_are_actually_read():
    genes = _builder_genes()
    readable = len(set(genes) - WITHOUT_A_TRANSCRIPT)

    said = re.search(
        r"reads every other variant in the coding exons, and splice sites, of (\d+) genes",
        DOC.read_text(encoding="utf-8"),
    )
    assert said, "reports.md no longer makes this claim in the form this test checks"
    assert int(said.group(1)) == readable, (
        f"the doc says {said.group(1)} genes; the bundle names {len(genes)} and carries "
        f"exon models for {readable}"
    )


def test_the_doc_also_gives_the_total_and_names_the_exception():
    doc = DOC.read_text(encoding="utf-8")
    assert f"names {len(_builder_genes())} such genes" in doc
    for gene in WITHOUT_A_TRANSCRIPT:
        assert gene in doc, f"{gene} is read by nothing and the doc does not say so"


def test_the_excluded_genes_are_genes_the_bundle_asks_for():
    """An exclusion for a gene the builder does not list is a stale excuse."""
    assert WITHOUT_A_TRANSCRIPT <= set(_builder_genes())
