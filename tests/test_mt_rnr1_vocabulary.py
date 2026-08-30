"""MT-RNR1's vocabulary is PharmCAT's, exactly -- pinned to the jar.

MT-RNR1 is haploid: every diplotypekey in PharmCAT's MT_RNR1.json is a single
allele with count 1. So the outside call is one name, not a diplotype, and it
has to be spelled the way PharmCAT spells it or the call is silently dropped.
"""

import pytest

from app.mtdna.mt_rnr1 import (
    MT_RNR1_ALLELES,
    MT_RNR1_SPAN,
    RISK_INCREASED,
    RISK_NORMAL,
    RISK_UNCERTAIN,
    VcfRecord,
    match_alleles,
    select_call,
)

# Verbatim from org/pharmgkb/pharmcat/phenotype/MT_RNR1.json (PharmCAT 3.4.0).
PHARMCAT_HAPLOTYPES = {
    "Reference": RISK_NORMAL,
    "m.663A>G": RISK_UNCERTAIN,
    "m.669T>C": RISK_UNCERTAIN,
    "m.747A>G": RISK_UNCERTAIN,
    "m.786G>A": RISK_UNCERTAIN,
    "m.807A>C": RISK_UNCERTAIN,
    "m.807A>G": RISK_UNCERTAIN,
    "m.827A>G": RISK_NORMAL,
    "m.839A>G": RISK_UNCERTAIN,
    "m.896A>G": RISK_UNCERTAIN,
    "m.930G>A": RISK_UNCERTAIN,
    "m.951G>A": RISK_UNCERTAIN,
    "m.960C>del": RISK_UNCERTAIN,
    "m.961T>G": RISK_UNCERTAIN,
    "m.961T>del": RISK_UNCERTAIN,
    "m.961T>del+Cn": RISK_UNCERTAIN,
    "m.988G>A": RISK_UNCERTAIN,
    "m.1095T>C": RISK_INCREASED,
    "m.1189T>C": RISK_UNCERTAIN,
    "m.1243T>C": RISK_UNCERTAIN,
    "m.1494C>T": RISK_INCREASED,
    "m.1520T>C": RISK_UNCERTAIN,
    "m.1537C>T": RISK_UNCERTAIN,
    "m.1555A>G": RISK_INCREASED,
    "m.1556C>T": RISK_UNCERTAIN,
}


def test_the_vocabulary_matches_pharmcat_exactly():
    assert set(MT_RNR1_ALLELES) == set(PHARMCAT_HAPLOTYPES)


def test_every_allele_carries_pharmcats_risk_tier():
    for name, tier in PHARMCAT_HAPLOTYPES.items():
        assert MT_RNR1_ALLELES[name].risk == tier, name


def test_every_variant_position_is_inside_the_gene():
    low, high = MT_RNR1_SPAN
    for name, allele in MT_RNR1_ALLELES.items():
        if name == "Reference":
            continue
        assert low <= allele.pos <= high, name


def test_a_plain_snv_matches_on_position_ref_and_alt():
    assert match_alleles([VcfRecord(1555, "A", "G")]) == ["m.1555A>G"]


def test_position_alone_is_not_enough():
    """m.807A>C and m.807A>G share a position and differ only in ALT."""
    assert match_alleles([VcfRecord(807, "A", "C")]) == ["m.807A>C"]
    assert match_alleles([VcfRecord(807, "A", "G")]) == ["m.807A>G"]


def test_an_unlisted_variant_matches_nothing():
    assert match_alleles([VcfRecord(1600, "G", "A")]) == []


def test_a_single_base_deletion_in_the_c_tract_matches():
    """m.960C>del, left-aligned by bcftools norm onto the 960-965 C-tract."""
    assert match_alleles([VcfRecord(959, "AC", "A")]) == ["m.960C>del"]


def test_a_single_base_deletion_matches_exactly_one_allele():
    """A one-base deletion spans one position, so it names one allele.

    Guards an off-by-one in _deleted_span: with a +1 on the range end, a
    deletion at 960 also matched m.961T>del and m.961T>del+Cn, and select_call
    could then report the wrong variant.
    """
    assert len(match_alleles([VcfRecord(959, "AC", "A")])) == 1


def test_increased_risk_outranks_uncertain():
    call = select_call(["m.663A>G", "m.1555A>G"])
    assert call == "m.1555A>G"


def test_uncertain_outranks_normal():
    assert select_call(["m.827A>G", "m.663A>G"]) == "m.663A>G"


def test_within_a_tier_the_lowest_position_wins():
    """Deterministic, so the same sample never yields two different reports."""
    assert select_call(["m.1494C>T", "m.1095T>C"]) == "m.1095T>C"


def test_no_matches_is_not_silently_reference():
    """Reference is a positive claim; the caller decides, not this function."""
    assert select_call([]) is None


import json
import shutil
import subprocess

MT_RNR1_JSON = "org/pharmgkb/pharmcat/phenotype/MT_RNR1.json"

_RISK_FROM_PHENOTYPE = {
    "increased risk of aminoglycoside-induced hearing loss": RISK_INCREASED,
    "uncertain risk of aminoglycoside-induced hearing loss": RISK_UNCERTAIN,
    "normal risk of aminoglycoside-induced hearing loss": RISK_NORMAL,
}


def _haplotypes_from_running_pharmcat():
    """Read MT_RNR1.json out of the live pharmcat container, or skip."""
    if shutil.which("docker") is None:
        return None
    try:
        proc = subprocess.run(
            [
                "docker", "--context", "pgx-native", "exec", "pgx_pharmcat",
                "sh", "-c",
                f"cd /tmp && unzip -o -p /pharmcat/pharmcat.jar {MT_RNR1_JSON}",
            ],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return json.loads(proc.stdout)["haplotypes"]


@pytest.mark.skipif(
    _haplotypes_from_running_pharmcat() is None,
    reason="pgx_pharmcat not reachable on the pgx-native context",
)
def test_the_table_matches_the_jar_it_was_transcribed_from():
    haplotypes = _haplotypes_from_running_pharmcat()
    assert set(haplotypes) == set(MT_RNR1_ALLELES)
    for name, phenotype in haplotypes.items():
        expected = _RISK_FROM_PHENOTYPE[phenotype.strip().lower()]
        assert MT_RNR1_ALLELES[name].risk == expected, name
