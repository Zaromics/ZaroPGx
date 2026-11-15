"""The gene sets agree with each other, not only with themselves.

``test_pypgx_gene_sets.py`` covers how a set is looked up -- an unknown name, the
fallback config, a category of the same name -- after a fallback that answered
"pharmcat_can_call" with all 91 genes and so handed PharmCAT no outside call at all.
This covers the other half: what the sets *contain*, relative to one another.

Each relationship below is arithmetic the pipeline relies on, and each is breakable by
a one-line edit to ``config/genes.json`` that nothing else would notice:

* ``pypgx_minus_pharmcat`` is exactly ``pypgx`` less ``pharmcat_can_call``. The report
  asks for it on a VCF run and for ``pypgx`` on an alignment
  (``generator.py``: ``_load_gene_set("pypgx_minus_pharmcat" if variant_input else
  "pypgx")``), so a gene added to ``pypgx`` and forgotten here is silently not analysed
  on every VCF run -- it reaches the report as ``not_tested``, which is honest and
  wrong.
* ``pharmcat_can_call`` is a subset of ``pharmcat_all``. A gene PharmCAT is said to
  call itself but is not among the genes it reports is a contradiction.
* CYP2D6 is in ``pypgx_minus_pharmcat``: PharmCAT does not call it from a VCF and takes
  PyPGx's as an outside call, so PyPGx must be asked for it on both input kinds.
* ``all`` is the two callers' sets plus HLA-C, which comes from OptiType and from
  neither of them (``mark_not_tested_and_failed`` says so: "HLA-C, which PharmCAT has
  no row for, after an OptiType run that typed none").

The counts as they stand: all 91, pypgx 87, pharmcat_all 25, pharmcat_can_call 21,
pypgx_minus_pharmcat 66.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "config" / "genes.json"
GENE_TABLE = REPO / "lexicon" / "gene-table.csv"

#: The gene only OptiType supplies, in `all` and in neither caller's set.
OPTITYPE_ONLY = {"HLA-C"}

#: PharmCAT reports it but cannot call it from a VCF; PyPGx must be asked on both paths.
OUTSIDE_CALL = "CYP2D6"


@pytest.fixture(scope="module")
def sets() -> dict:
    raw = json.loads(CONFIG.read_text(encoding="utf-8"))["sets"]
    out = {}
    for name, value in raw.items():
        genes = value.get("genes") if isinstance(value, dict) else value
        out[name] = set(genes or [])
    return out


def test_the_sets_this_file_reasons_about_are_all_there(sets):
    """Otherwise every assertion below passes over an empty set."""
    for name in (
        "all",
        "pypgx",
        "pharmcat_all",
        "pharmcat_can_call",
        "pypgx_minus_pharmcat",
    ):
        assert sets.get(name), f"{name} is missing or empty in {CONFIG.name}"


def test_pypgx_minus_pharmcat_is_pypgx_less_what_pharmcat_can_call(sets):
    expected = sets["pypgx"] - sets["pharmcat_can_call"]
    missing = sorted(expected - sets["pypgx_minus_pharmcat"])
    extra = sorted(sets["pypgx_minus_pharmcat"] - expected)
    assert not missing, (
        "these are in pypgx and not in pharmcat_can_call, so a VCF run must ask PyPGx "
        f"for them, and it does not: {missing}"
    )
    assert (
        not extra
    ), f"these are in pypgx_minus_pharmcat but not in pypgx at all: {extra}"


def test_what_pharmcat_can_call_is_among_what_it_reports(sets):
    stray = sorted(sets["pharmcat_can_call"] - sets["pharmcat_all"])
    assert (
        not stray
    ), f"PharmCAT is said to call these itself but does not report them: {stray}"


def test_the_outside_call_gene_is_asked_of_pypgx_on_both_input_kinds(sets):
    assert OUTSIDE_CALL in sets["pypgx"]
    assert OUTSIDE_CALL in sets["pypgx_minus_pharmcat"], (
        f"{OUTSIDE_CALL} must stay in the VCF-run set: PharmCAT cannot call it from a "
        "VCF and takes PyPGx's call as an outside call"
    )
    assert OUTSIDE_CALL not in sets["pharmcat_can_call"]


def test_every_gene_in_all_comes_from_a_caller_that_can_supply_it(sets):
    covered = sets["pypgx"] | sets["pharmcat_all"] | OPTITYPE_ONLY
    orphans = sorted(sets["all"] - covered)
    assert not orphans, (
        "these are offered in `all` and no caller's set claims them, so nothing would "
        f"ever report them: {orphans}"
    )
    unknown = sorted(covered - sets["all"])
    assert (
        not unknown
    ), f"a caller's set names genes that `all` does not offer: {unknown}"


@pytest.fixture(scope="module")
def lexicon() -> list:
    with GENE_TABLE.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_the_pypgx_set_is_exactly_the_genes_pypgx_calls_targets(sets, lexicon):
    """A PyPGx upgrade that adds a target gene must be noticed, not followed silently.

    `lexicon/gene-table.csv` is PyPGx's own table, and its `Target` column is what the
    tool will call. The set we ask for is the 87 genes marked TRUE, exactly. The five
    other rows are machinery, not results: CYP2A7, CYP2B7 and CYP2D7 are the paralogs
    PyPGx compares against, and EGFR and VDR are depth-normalisation controls (RYR1 is
    both a control and a real target, so it stays).

    When this fails after a version bump, the answer is a decision rather than an edit:
    adding a gene changes the 91 the README lists and the report's gene list with it.
    """
    targets = {row["Gene"] for row in lexicon if row["Target"] == "TRUE"}
    assert targets, "no Target=TRUE rows -- this test has gone blind"

    unasked = sorted(targets - sets["pypgx"])
    assert not unasked, (
        "PyPGx calls these genes targets and nothing asks it for them, so they are "
        f"never reported: {unasked}"
    )
    unknown = sorted(sets["pypgx"] - targets)
    assert not unknown, (
        "we ask PyPGx for these and its own table does not mark them targets, so the "
        f"answer can only be nothing: {unknown}"
    )


def test_every_gene_offered_is_one_some_tool_knows(sets, lexicon):
    """A gene in `all` is in PyPGx's table, or comes from PharmCAT or OptiType."""
    table = {row["Gene"] for row in lexicon}
    # HLA-A and HLA-B are PharmCAT's with an outside call, HLA-C is OptiType's alone,
    # and MT-RNR1 is the mtDNA lane's.
    elsewhere = {"HLA-A", "HLA-B", "HLA-C", "MT-RNR1"}
    stray = sorted(sets["all"] - table - elsewhere)
    assert not stray, f"these are offered and no tool's table knows them: {stray}"
