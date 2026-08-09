"""Wild-type labelling (BACKLOG 235), which had three implementations and no test.

``generator.py`` decided the same thing in three inline places -- the TSV
Executive Summary in the PDF lane, ``_build_canonical_diplotypes``, and the TSV
Executive Summary in the HTML lane -- and a repo-wide grep across ``tests/``
found no coverage of any of them.

**Did the three copies agree?** On every input reachable today, yes: a
cross-product of 9 diplotype spellings x 12 phenotype spellings x 11 file types
(1188 cases) run through verbatim copies of all three produced the same label
wherever the callers' own inputs could reach. They agreed by luck, not by
construction:

* The HTML-lane copy compared ``file_type`` **raw** -- ``if file_type in
  {"vcf", ...}`` -- while the other two lower-cased it. It only worked because
  ``generate_report`` happens to lower-case the value 130 lines earlier. Move or
  drop that one ``.lower()`` and the Executive Summary silently stops labelling
  while the gene table keeps going.
* The PDF-lane copy defaults a missing workflow to ``"vcf"``; the other two
  label nothing when the file type is unknown.

So the divergence was latent rather than live, and the correct behaviour is the
lower-casing one: the file type is a description of the *evidence*, and a report
that drops the label because someone wrote "VCF" instead of "vcf" is claiming
less than the run supports.

Which label is correct is the substantive question, and it is not symmetric:

* A VCF lists variants. It says nothing about positions it omits, so a reference
  call is *absence of evidence* -> "Possibly Wild Type".
* Aligned reads (BAM/CRAM/SAM) and FASTQ cover the locus, so a reference call is
  *evidence of absence* -> "Likely Wild Type".
* An unknown file type supports neither claim, so the report must say nothing.
  Defaulting to either label would put a confidence statement on the page that
  no part of the run earned.
"""

from __future__ import annotations

import itertools

import pytest

from app.reports.generator import (
    WILD_TYPE_ALIGNED_READS_LABEL,
    WILD_TYPE_VARIANT_ONLY_LABEL,
    wild_type_phenotype,
)

REFERENCE_DIPLOTYPES = [
    "*1/*1",
    "*1 / *1",
    "Reference/Reference",
    "REFERENCE/REFERENCE",
    "reference / reference",
    "  *1/*1  ",
]
ABSENT_PHENOTYPES = [
    None,
    "",
    "   ",
    "N/A",
    "n/a",
    " n/a ",
    "na",
    "Unknown",
    "UNKNOWN ",
    "none",
    "-",
    ".",
]
VARIANT_ONLY = ["vcf", "VCF", "vcf.gz", "VCF.GZ", "vcf.bgz", " vcf "]
ALIGNED_READS = ["bam", "BAM", "cram", "CRAM", "sam", "fastq", "FASTQ", "fq"]


# ---------------------------------------------------------------------------
# The two labels, and what earns each one
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("file_type", VARIANT_ONLY)
def test_a_variant_only_file_earns_only_possibly(file_type):
    """A VCF is silent about what it does not list -- absence of evidence."""
    assert wild_type_phenotype("*1/*1", "", file_type) == WILD_TYPE_VARIANT_ONLY_LABEL


@pytest.mark.parametrize("file_type", ALIGNED_READS)
def test_read_level_data_earns_likely(file_type):
    """Reads cover the locus, so no variant call is evidence of absence."""
    assert wild_type_phenotype("*1/*1", "", file_type) == WILD_TYPE_ALIGNED_READS_LABEL


@pytest.mark.parametrize("diplotype", REFERENCE_DIPLOTYPES)
def test_every_reference_spelling_is_recognised(diplotype):
    """The spellings PharmCAT and the TSV parser actually emit, plus padding."""
    assert wild_type_phenotype(diplotype, "", "vcf") == WILD_TYPE_VARIANT_ONLY_LABEL
    assert wild_type_phenotype(diplotype, "", "bam") == WILD_TYPE_ALIGNED_READS_LABEL


@pytest.mark.parametrize("phenotype", ABSENT_PHENOTYPES)
def test_every_spelling_of_a_missing_phenotype_is_recognised(phenotype):
    """ "n/a", "-", "." and friends mean "not reported", not a phenotype."""
    assert (
        wild_type_phenotype("*1/*1", phenotype, "vcf") == WILD_TYPE_VARIANT_ONLY_LABEL
    )


def test_the_two_labels_are_distinct():
    """They are different clinical claims; the report styles them differently."""
    assert WILD_TYPE_VARIANT_ONLY_LABEL != WILD_TYPE_ALIGNED_READS_LABEL


# ---------------------------------------------------------------------------
# The case that the HTML lane would have got wrong
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "file_type,expected",
    [
        ("VCF", WILD_TYPE_VARIANT_ONLY_LABEL),
        ("Vcf.Gz", WILD_TYPE_VARIANT_ONLY_LABEL),
        ("BAM", WILD_TYPE_ALIGNED_READS_LABEL),
        ("CRAM", WILD_TYPE_ALIGNED_READS_LABEL),
        ("  bam  ", WILD_TYPE_ALIGNED_READS_LABEL),
    ],
)
def test_file_type_casing_and_padding_do_not_change_the_answer(file_type, expected):
    """The one place the three copies differed.

    The HTML lane compared ``file_type`` raw and only worked because a caller
    130 lines up happened to lower-case it first.
    """
    assert wild_type_phenotype("*1/*1", "", file_type) == expected


# ---------------------------------------------------------------------------
# What must NOT be labelled
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "file_type", ["", None, "txt", "unknown", "tsv", "json", "bed"]
)
def test_an_unsupported_file_type_gets_no_label(file_type):
    """Silence, not a default.

    Labelling here would put "Possibly Wild Type" on a page where nothing in the
    run supports even that much.
    """
    assert wild_type_phenotype("*1/*1", "", file_type) is None


@pytest.mark.parametrize(
    "diplotype",
    [
        "*1/*4",
        "*4/*4",
        "*1/*2",
        "Unknown/Unknown",
        "",
        None,
        "*1",
        "*1/*1/*1",
        "rs2231142 reference (G)/rs2231142 reference (G)",
    ],
)
@pytest.mark.parametrize("file_type", ["vcf", "bam"])
def test_a_non_reference_diplotype_is_never_wild_type(diplotype, file_type):
    assert wild_type_phenotype(diplotype, "", file_type) is None


@pytest.mark.parametrize(
    "phenotype",
    [
        "Normal Metabolizer",
        "Poor Metabolizer",
        "Intermediate Metabolizer",
        "Normal Function",
        "Uncertain Susceptibility",
        "No Result",
        "ivacaftor non-responsive in CF patients",
    ],
)
@pytest.mark.parametrize("file_type", ["vcf", "bam"])
def test_a_reported_phenotype_is_never_overwritten(phenotype, file_type):
    """PharmCAT called a phenotype. The report must not replace it with a guess.

    "No Result" is deliberately in this list: it is PharmCAT's own statement
    about CYP2D6, not a blank, and it appears on real runs under data/reports.
    """
    assert wild_type_phenotype("*1/*1", phenotype, file_type) is None


# ---------------------------------------------------------------------------
# Shape: the callers pass raw row values straight in
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [None, 0, 0.0, False, [], {}],
    ids=["none", "int", "float", "bool", "list", "dict"],
)
def test_non_string_inputs_do_not_raise(value):
    """Two of the three call sites hand it ``row.get(...)`` with no coercion.

    They are inside ``except Exception`` blocks that would swallow a TypeError
    and drop the label with only a debug line, so this has to be total.
    """
    assert wild_type_phenotype(value, value, value) is None
    assert wild_type_phenotype("*1/*1", value, "vcf") == WILD_TYPE_VARIANT_ONLY_LABEL


def test_the_decision_is_a_pure_function_of_its_three_inputs():
    """No hidden state: the same inputs give the same answer every time.

    This is what makes one shared implementation safe for three call sites that
    reach it from different lanes with differently-derived values.
    """
    cases = list(
        itertools.product(
            REFERENCE_DIPLOTYPES[:3] + ["*1/*4"],
            ABSENT_PHENOTYPES[:4] + ["Poor Metabolizer"],
            VARIANT_ONLY[:2] + ALIGNED_READS[:2] + ["txt"],
        )
    )
    first = [wild_type_phenotype(*case) for case in cases]
    second = [wild_type_phenotype(*case) for case in reversed(cases)][::-1]
    assert first == second
