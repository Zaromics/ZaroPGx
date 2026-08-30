"""b37 and hg19 do not share a mitochondrion, and one chain cannot serve both.

hg19 chrM is NC_001807, 16571 bp (Yoruba). GRCh37/b37 MT is NC_012920 (rCRS),
16569 bp -- the same sequence GRCh38 uses. So a b37 MT record is already in the
target coordinate system and lifting it *introduces* an error: inside MT-RNR1
the hg19->hg38 chain applies a constant -2 shift, which would move an
m.1555A>G to 1553.
"""

import pytest

from app.mtdna.builds import (
    MitoBuild,
    classify_build,
    classify_from_mito_contig,
    plan_for,
)


@pytest.mark.parametrize(
    "detected,expected",
    [
        ("GRCh38", MitoBuild.GRCH38),
        ("hg38", MitoBuild.GRCH38),
        ("GRCh37", MitoBuild.B37),
        ("b37", MitoBuild.B37),
        ("hg19", MitoBuild.HG19),
        ("T2T-CHM13", MitoBuild.UNSUPPORTED),
        ("unknown", MitoBuild.UNSUPPORTED),
    ],
)
def test_build_classification(detected, expected):
    assert classify_build(detected) == expected


def test_hg19_liftover_target_filename_is_not_shadowed_by_38():
    """header_inspector.py cites "hg19_to_hg38_lifted.fasta" as a realistic
    reference value. It contains "38" as the liftover *target*, but the
    evidence for the *source* build -- hg19 -- must win, because the whole
    reason this module exists is to catch hg19's un-lifted chrM.
    """
    assert classify_build("hg19_to_hg38_lifted.fasta") == MitoBuild.HG19


def test_grch38_is_used_as_is():
    plan = plan_for(MitoBuild.GRCH38)
    assert plan.supported
    assert not plan.needs_liftover
    assert not plan.rename_mt_to_chrm


def test_b37_is_renamed_but_never_lifted():
    """The whole point: b37 MT is already rCRS."""
    plan = plan_for(MitoBuild.B37)
    assert plan.supported
    assert plan.rename_mt_to_chrm
    assert not plan.needs_liftover


def test_hg19_is_lifted():
    plan = plan_for(MitoBuild.HG19)
    assert plan.supported
    assert plan.needs_liftover


def test_an_unknown_build_is_refused_with_a_reason():
    plan = plan_for(MitoBuild.UNSUPPORTED)
    assert not plan.supported
    assert plan.reason


def test_hg19_and_b37_do_not_share_a_plan():
    """A regression guard for the bug in gatk_api's LIFTOVER_SOURCE_BUILDS."""
    assert plan_for(MitoBuild.HG19) != plan_for(MitoBuild.B37)


# -- classify_from_mito_contig: length is ground truth, not the build label --


def test_contig_length_16571_is_hg19_regardless_of_contig_name():
    """16571 bp is NC_001807. Only hg19's chrM is that length, whatever the
    ##contig line happens to call it."""
    assert classify_from_mito_contig("chrM", 16571) == MitoBuild.HG19
    assert classify_from_mito_contig("MT", 16571) == MitoBuild.HG19


def test_contig_mt_at_16569_is_b37():
    assert classify_from_mito_contig("MT", 16569) == MitoBuild.B37


def test_contig_chrm_at_16569_is_grch38():
    assert classify_from_mito_contig("chrM", 16569) == MitoBuild.GRCH38


def test_missing_length_is_unsupported():
    assert classify_from_mito_contig("chrM", None) == MitoBuild.UNSUPPORTED
    assert classify_from_mito_contig(None, None) == MitoBuild.UNSUPPORTED


def test_absurd_length_is_unsupported():
    assert classify_from_mito_contig("chrM", 12345) == MitoBuild.UNSUPPORTED


def test_hg19_mislabelled_as_grch37_is_still_hg19_by_contig_length():
    """The regression this function exists to prevent.

    file_processor._normalize_reference_genome collapses "hg19" to "GRCh37"
    (file_processor.py:611-612) -- correct for the autosomal liftover, where
    hg19 and GRCh37 share coordinates, but wrong for chrM, the one contig
    where the two builds carry genuinely different sequences. A real hg19
    upload therefore reaches this module labelled "GRCh37"; classify_build
    on that label would return B37 and its chrM would never be lifted,
    reproducing the exact bug this module exists to prevent -- one layer up,
    where the module can't see it. Reading the contig's own length instead
    of the (already-collapsed) label is the fix: it is unaffected by what
    file_processor decided to call the build.
    """
    label = "GRCh37"
    assert classify_build(label) == MitoBuild.B37
    assert classify_from_mito_contig("chrM", 16571) == MitoBuild.HG19
