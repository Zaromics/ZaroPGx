"""The AMP/CAP minimum allele sets, and the one condition that withholds a call.

Why this module exists is the measurement in app/pharmcat/amp_tier1.py's docstring:
PharmCAT, run through its own full pipeline with NEITHER assume-reference flag and no
PyPGx anywhere, reported ``CYP2C9 Normal Metabolizer, Activity Score 2.0`` from 2 of 88
positions. A reference call is the one call absence can fake, so "was this gene tested
well enough" has to be answered before its verdict is shown -- and AMP/CAP publish that
answer, per gene, as a minimum ("Tier 1") allele set.

What is pinned here:

* **The table fails CLOSED.** A gene with no published minimum set is reported as not
  judged, never as adequate. That is the whole safety property: the alternative is a
  table that silently passes every gene it has no entry for.
* **The allele names are PharmCAT's.** Every Tier 1 string must appear verbatim in a
  real PharmCAT ``uncallableHaplotypes`` list, or the intersection matches nothing and
  the gate fails open while looking like it works. AMP writes DPYD's Tier 1 as
  ``c.1905+1G>A``; PharmCAT writes ``c.1905+1G>A (*2A)``.
* **Both directions, on real output.** A complete input keeps every verdict; the chip
  file loses every star-allele verdict. Either half alone would pass a broken gate.
"""

import pytest

from app.pharmcat.amp_tier1 import (
    PRIMARY,
    SECONDARY,
    SELF_GATING,
    TIER1,
    Adequacy,
    adequacy,
    statement,
)

# PharmCAT 3.4.0's own uncallableHaplotypes lists, as measured on this stack. Trimmed to
# what each assertion needs, but every string is verbatim from the real output.
CHIP_UNCALLED = {
    "CYP2C9": ["*5", "*6", "*8", "*11", "*12", "*13", "*15", "*61"],
    "CYP2C19": ["*3", "*4A", "*9", "*10", "*35"],
    "TPMT": ["*2", "*3A", "*3B", "*3C", "*11", "*29", "*42"],
    "NUDT15": ["*2", "*3", "*4", "*6", "*9"],
    "CYP3A5": ["*3", "*6", "*7", "*8", "*9"],
    "CYP3A4": ["*22", "*20", "*2"],
    "DPYD": [
        "c.1905+1G>A (*2A)",
        "c.1679T>G (*13)",
        "c.1129-5923C>G, c.1236G>A (HapB3)",
        "c.557A>G",
        "c.868A>G",
        "c.2279C>T",
        "c.2846A>T",
        "c.703C>T (*8)",
    ],
}

# What a COMPLETE input leaves unassessable: rare alleles that need structural or
# copy-number evidence no VCF carries. Measured on PharmCAT's own example VCF. None of
# these is Tier 1, which is the fact that makes the gate usable at all.
COMPLETE_UNCALLED = {
    "CYP2C9": ["*45", "*52", "*55", "*58", "*60", "*61", "*62"],
    "CYP2C19": [],
    "TPMT": ["*45", "*47"],
    "NUDT15": ["*21", "*22", "*23"],
    "CYP3A5": ["*10", "*11"],
    "CYP3A4": ["*21", "*22x2", "*33", "*34"],
    "DPYD": [],
}


# --------------------------------------------------------------------------
# The safety property: no entry means no judgement, never "adequate"
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "gene", ["SLCO1B1", "CFTR", "G6PD", "RYR1", "NAT2", "CYP2B6", "UGT1A1", "ABCG2"]
)
def test_a_gene_with_no_published_minimum_set_is_not_judged(gene):
    """AMP has published no minimum set for these, so there is nothing to fall short
    of -- and nothing to vouch for either. ``assessed`` False is not ``complete``."""
    result = adequacy(gene, [])

    assert result.assessed is False
    assert result.complete is False
    assert result.inadequate is False, "not judged must never withhold a call"
    assert statement(result) is None, "and must never claim anything in the report"


def test_an_unjudged_gene_is_not_mistaken_for_an_adequate_one():
    """The distinction the whole table rests on. A caller that reads ``complete`` alone
    would pass every gene with no entry, which is the fail-open this prevents."""
    unjudged = adequacy("SLCO1B1", ["*5", "*15"])
    adequate = adequacy("CYP2C9", [])

    assert unjudged.assessed is False and adequate.assessed is True
    assert adequate.complete is True and adequate.inadequate is False
    assert unjudged.complete is False, "and it must not read as complete"


def test_nothing_is_withheld_without_a_citation():
    """Every gate this module can fire has a paper behind it; a Tier 1 set without one
    would be an invented clinical threshold wearing AMP's name."""
    for gene, tier1 in TIER1.items():
        assert tier1.citation.strip(), gene
        assert tier1.source in (PRIMARY, SECONDARY), gene
        assert "J Mol Diagn" in tier1.citation, gene


def test_which_tier_1_lists_have_been_read_in_the_paper():
    """Pinned so the evidence behind each gate cannot drift unnoticed.

    These are clinical allele sets. A list nobody has read in the primary paper still
    gates -- it is the best available evidence and it agrees across three independent
    secondary sources -- but it must not quietly come to look as settled as one that was
    read. Moving a gene between these sets is a deliberate act that edits this test.
    """
    read_in_the_paper = {"CYP2C9", "CYP2D6", "CYP3A4", "CYP3A5", "CYP4F2", "DPYD"}
    secondary_only = {"CYP2C19", "TPMT", "NUDT15"}

    assert read_in_the_paper | secondary_only == set(TIER1)
    for gene in read_in_the_paper:
        assert TIER1[gene].source == PRIMARY, gene
    for gene in secondary_only:
        assert TIER1[gene].source == SECONDARY, gene


def test_cyp2d6_tier_1_carries_star_41():
    """The one list a secondary summary got wrong. The published Tier 1 is "*2 to *6,
    *9, *10, *17, *29, and *41"; omitting *41 would let a run that never assessed it
    keep a CYP2D6 verdict."""
    assert "*41" in TIER1["CYP2D6"].alleles
    assert TIER1["CYP2D6"].alleles == frozenset(
        {"*2", "*3", "*4", "*5", "*6", "*9", "*10", "*17", "*29", "*41"}
    )

    assert adequacy("CYP2D6", ["*41"]).inadequate is True


def test_cyp2d6_adequacy_does_not_speak_for_copy_number():
    """AMP's CYP2D6 Tier 1 also requires interrogating gene duplications, which is not
    an allele in ``uncalledHaplotypes`` and cannot be judged here. So "adequate" for
    CYP2D6 means its SNV alleles only, and the copy-number caveat is a separate
    statement that must not be read as covered by this one."""
    result = adequacy("CYP2D6", [])

    assert result.complete is True
    assert statement(result) is None, "it says nothing about copy number either way"


# --------------------------------------------------------------------------
# Both directions, on real PharmCAT output
# --------------------------------------------------------------------------


@pytest.mark.parametrize("gene,uncalled", sorted(COMPLETE_UNCALLED.items()))
def test_a_complete_input_keeps_every_verdict(gene, uncalled):
    """MEASURED on PharmCAT's own complete example VCF: 9 genes there are called with
    some allele unassessable, and NONE of those alleles is Tier 1. This is why
    ``uncalledHaplotypes`` alone cannot be the gate -- it is never empty -- and why
    intersecting it with Tier 1 is exact."""
    result = adequacy(gene, uncalled)

    assert result.assessed is True
    assert result.inadequate is False, f"{gene} would lose its call on complete data"
    assert result.missing == ()


@pytest.mark.parametrize(
    "gene,expected",
    [
        ("CYP2C9", ("*5", "*6", "*8", "*11")),
        ("CYP2C19", ("*3",)),
        ("TPMT", ("*2", "*3A", "*3B", "*3C")),
        ("NUDT15", ("*3",)),
        ("CYP3A5", ("*3", "*6", "*7")),
        ("CYP3A4", ("*22",)),
    ],
)
def test_the_chip_file_loses_its_star_allele_verdicts_and_says_which(gene, expected):
    result = adequacy(gene, CHIP_UNCALLED[gene])

    assert result.inadequate is True
    assert result.missing == expected


def test_nudt15_tier_1_is_star_3_alone_not_star_2():
    """*2 is the better known name and it is Tier 2. Taking it for Tier 1 would
    withhold NUDT15 on data that assessed everything AMP asks for."""
    assert TIER1["NUDT15"].alleles == frozenset({"*3"})

    only_two_missing = adequacy("NUDT15", ["*2", "*4", "*6"])

    assert only_two_missing.inadequate is False, "*2 alone is not a Tier 1 gap"


def test_cyp4f2_has_an_empty_minimum_set_and_still_counts_as_judged():
    """AMP put CYP4F2*3 in Tier 2 and nothing in Tier 1, so there is no minimum to
    fall short of. Recorded as an empty set rather than omitted, so it reads as
    "looked up" rather than "not covered"."""
    assert TIER1["CYP4F2"].alleles == frozenset()

    result = adequacy("CYP4F2", ["*3", "*2"])

    assert result.assessed is True
    assert result.inadequate is False


def test_a_self_gating_gene_is_judged_complete():
    """VKORC1's only defining position IS its Tier 1 variant (c.-1639G>A), so PharmCAT
    cannot call it without reading that variant -- a call is its own proof. Named in
    star notation it would match none of PharmCAT's rsID-style haplotype names and fail
    open, which is why it is handled here instead of in the table."""
    for gene in SELF_GATING:
        result = adequacy(gene, [])
        assert result.assessed is True
        assert result.inadequate is False


# --------------------------------------------------------------------------
# The allele names are PharmCAT's, and the sentence is readable
# --------------------------------------------------------------------------


def test_dpyds_tier_1_names_are_pharmcats_spelling_not_the_papers():
    """AMP writes ``c.1905+1G>A``; PharmCAT writes ``c.1905+1G>A (*2A)``. A table keyed
    on the paper's spelling intersects to nothing and passes every gene silently."""
    result = adequacy("DPYD", CHIP_UNCALLED["DPYD"])

    assert result.inadequate is True
    assert len(result.missing) == 7, result.missing
    assert "c.1905+1G>A (*2A)" in result.missing
    assert "c.1129-5923C>G, c.1236G>A (HapB3)" in result.missing
    # Tier 2's *8 is in the uncalled list and must NOT be counted as a Tier 1 gap.
    assert "c.703C>T (*8)" not in result.missing


def test_the_alleles_are_listed_in_numeric_order():
    """Plain string order gives "*11, *5, *6, *8", which reads as a mistake in a
    sentence a clinician is meant to act on."""
    assert adequacy("CYP2C9", CHIP_UNCALLED["CYP2C9"]).missing == (
        "*5",
        "*6",
        "*8",
        "*11",
    )


def test_the_sentence_names_the_alleles_and_says_why_it_matters():
    text = statement(adequacy("CYP2C9", CHIP_UNCALLED["CYP2C9"]))

    assert text is not None
    assert "*5, *6, *8 and *11" in text, "named, not counted"
    assert "AMP/CAP" in text and "CYP2C9" in text
    # The reason a reference call specifically cannot stand.
    assert "would rest on positions the data does not cover" in text


def test_an_allele_name_holding_a_comma_does_not_inflate_the_list():
    """DPYD's HapB3 name carries its own comma, so comma-separating the list around it
    made seven alleles read as eight."""
    text = statement(adequacy("DPYD", CHIP_UNCALLED["DPYD"]))

    assert text is not None
    assert "; " in text, "semicolons separate names that hold commas"


def test_an_adequate_gene_says_nothing():
    """A line on every row stating that nothing is wrong is noise."""
    assert statement(adequacy("CYP2C9", COMPLETE_UNCALLED["CYP2C9"])) is None


# --------------------------------------------------------------------------
# Deciding adequacy must never cost a report its gene rows
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "gene,uncalled",
    [
        (None, None),
        ("", []),
        ("CYP2C9", None),
        ("cyp2c9", ["*5"]),
        (123, ["*5"]),
        ("CYP2C9", [None, "", "  ", "*5"]),
        ("CYP2C9", [object()]),
    ],
)
def test_malformed_input_never_raises(gene, uncalled):
    result = adequacy(gene, uncalled)

    assert isinstance(result, Adequacy)


def test_the_gene_name_is_matched_case_insensitively():
    """PharmCAT's own JSON is consistent, but the gene row this is called with has been
    through several hands by then."""
    assert adequacy("cyp2c9", ["*5"]).inadequate is True
    assert adequacy(" CYP2C9 ", ["*5"]).inadequate is True
