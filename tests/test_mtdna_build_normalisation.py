"""b37 and hg19 do not share a mitochondrion, and one chain cannot serve both.

hg19 chrM is NC_001807, 16571 bp (Yoruba). GRCh37/b37 MT is NC_012920 (rCRS),
16569 bp -- the same sequence GRCh38 uses. So a b37 MT record is already in the
target coordinate system and lifting it *introduces* an error: inside MT-RNR1
the hg19->hg38 chain applies a constant -2 shift, which would move an
m.1555A>G to 1553.
"""

import pytest

from app.mtdna.builds import MitoBuild, classify_build, plan_for


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
