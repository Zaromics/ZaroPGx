"""The phenotype a row shows is decided in one place.

The report reaches the same cell by two paths -- the gene rows, and PharmCAT's
calls-only TSV behind the Executive Summary -- and each carried its own copy of the two
rules that decide what the phenotype column says: the wild-type label for a reference
call the input can support, and PharmCAT's "N/A" for a call the calling tool assigns no
phenotype to.

Copies of one rule are how surfaces come to disagree, which this release has seen twice:
the Alerts box against the Executive Summary (a withheld verdict printed anyway), and the
warnings box against itself (six warnings rendered as one line, because a predicate asked
about a representation the code had stopped producing). The copies agreed when this was
written. These keep them from drifting apart.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.reports.generator import (
    NO_PHENOTYPE_STANDARD,
    shown_phenotype,
    wild_type_phenotype,
)

SOURCE = Path(__file__).resolve().parents[1] / "app" / "reports" / "generator.py"


@pytest.mark.parametrize(
    "row, file_type, expected",
    [
        # A reference call on an input that can support the claim.
        (
            {"gene": "CYP2C19", "diplotype": "*1/*1", "phenotype": ""},
            "vcf",
            wild_type_phenotype("*1/*1", "", "vcf"),
        ),
        # A phenotype the calling tool gave: left alone.
        (
            {
                "gene": "CYP2C19",
                "diplotype": "*1/*2",
                "phenotype": "Intermediate Metabolizer",
            },
            "vcf",
            "Intermediate Metabolizer",
        ),
        # A PharmCAT gene whose calls define no phenotype.
        (
            {
                "gene": "VKORC1",
                "diplotype": "rs9923231 variant (T)/rs9923231 variant (T)",
                "phenotype": "Unknown",
            },
            "vcf",
            NO_PHENOTYPE_STANDARD,
        ),
        # A PyPGx-only gene with no phenotype method: the case the first cut of this
        # function broke, by passing only a gene and a diplotype.
        (
            {
                "gene": "GSTM1",
                "diplotype": "*A/*B",
                "phenotype": None,
                "pyPgxOnly": True,
            },
            "bam",
            NO_PHENOTYPE_STANDARD,
        ),
        # A PyPGx gene that does have one keeps its blank: missing data, not "N/A".
        (
            {
                "gene": "CYP2A6",
                "diplotype": "*1/*2",
                "phenotype": None,
                "pyPgxOnly": True,
                "pypgx_phenotype_method": "Score",
            },
            "bam",
            None,
        ),
        # OptiType's HLA-C, which PharmCAT has no row for.
        (
            {
                "gene": "HLA-C",
                "diplotype": "*01:02/*07:01",
                "phenotype": "",
                "optitypeOnly": True,
            },
            "bam",
            NO_PHENOTYPE_STANDARD,
        ),
        # Not a reference call and not a no-phenotype gene: unchanged.
        (
            {"gene": "CYP2D6", "diplotype": "*4/*5", "phenotype": "Poor Metabolizer"},
            "bam",
            "Poor Metabolizer",
        ),
    ],
)
def test_the_rule_is_one_function(row, file_type, expected):
    assert shown_phenotype(row, file_type) == expected


def _without(source: str, *definitions: str) -> str:
    """The module with those functions' own bodies cut out, so what is left is the
    callers."""
    for name in definitions:
        start = source.index(f"def {name}(")
        end = source.index("\ndef ", start + 10)
        source = source[:start] + source[end:]
    return source


def test_the_two_paths_agree_by_construction():
    """Not by each remembering: the gene-row path and the TSV path must both call the
    one function, and neither may re-derive the rules from the parts. The two live in
    different functions -- the gene rows are normalized well before generate_report
    reads the TSV -- which is exactly why a shared rule is worth pinning."""
    source = SOURCE.read_text(encoding="utf-8")
    callers = _without(source, "shown_phenotype", "wild_type_phenotype")

    assert (
        callers.count("shown_phenotype(") >= 2
    ), "a path stopped using the shared rule"
    assert "wild_type_phenotype(" not in callers, (
        "a path is deciding the wild-type label for itself again; call shown_phenotype "
        "so the two cells cannot drift"
    )
    assert (
        "= NO_PHENOTYPE_STANDARD" not in callers
    ), "a path is assigning the no-phenotype label for itself again"


def test_the_shared_rule_still_says_why_it_is_shared():
    """A docstring is what keeps the next person from inlining it back."""
    source = SOURCE.read_text(encoding="utf-8")
    doc = source[source.index("def shown_phenotype(") :][:1400]

    assert "two paths" in doc or "two rules" in doc
    assert re.search(r"Executive Summary", doc)


def test_a_row_without_a_phenotype_key_does_not_gain_one():
    """pdf_generators reads it with a default -- diplotype.get("phenotype", "Unknown")
    -- so a row that never carried the key must not come back carrying None, which
    would defeat that default. The rule may only change a value, never add one."""
    from app.reports.generator import shown_phenotype

    row = {"gene": "CYP2D6", "diplotype": "*4/*5"}
    shown = shown_phenotype(row, "bam")
    if shown != row.get("phenotype"):
        row["phenotype"] = shown

    assert "phenotype" not in row, "the call site added a key the row did not have"
