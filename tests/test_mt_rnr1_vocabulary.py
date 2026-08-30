"""MT-RNR1's vocabulary is PharmCAT's, exactly -- pinned to the jar.

MT-RNR1 is haploid: every diplotypekey in PharmCAT's MT_RNR1.json is a single
allele with count 1. So the outside call is one name, not a diplotype, and it
has to be spelled the way PharmCAT spells it or the call is silently dropped.
"""

import pytest

from app.mtdna.mt_rnr1 import (
    MT_RNR1_ALLELES,
    MT_RNR1_SPAN,
    NO_CALL_NO_CHRM_DATA,
    NO_CALL_UNRESOLVED_961_DELINS,
    REFERENCE,
    RISK_INCREASED,
    RISK_NORMAL,
    RISK_UNCERTAIN,
    VcfRecord,
    has_unresolved_961_deletion,
    match_alleles,
    resolve_mt_rnr1_call,
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


def test_a_pure_deletion_at_961_names_only_the_plain_deletion():
    """m.961T>del and m.961T>del+Cn share a position; only REF/ALT tells them apart.

    (960, "CT", "C") is exactly the shape bcftools norm emits for a plain
    deletion of the T at 961, left-aligned onto the preceding C. Naming
    m.961T>del+Cn here would report an inserted C the record does not carry.
    Both alleles share the "uncertain" risk tier, so PharmCAT's phenotype
    would come out identical either way and nothing downstream would catch
    the mistake.
    """
    assert match_alleles([VcfRecord(960, "CT", "C")]) == ["m.961T>del"]


def test_a_pure_deletion_never_names_the_insertion_allele():
    """m.961T>del+Cn requires an inserted C; a plain deletion never has one.

    Same failure mode as above, checked directly and across more than one
    pure-deletion shape: a record with nothing inserted (ALT exactly a
    prefix of REF) must never be reported as m.961T>del+Cn, however many
    positions it spans.
    """
    for record in [VcfRecord(960, "CT", "C"), VcfRecord(959, "ACT", "A")]:
        assert "m.961T>del+Cn" not in match_alleles([record])


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


# --- has_unresolved_961_deletion / resolve_mt_rnr1_call ---------------------
#
# These back the actual "empty match -> Reference" promotion the sidecar
# performs (docker/mtdna-server-2/app.py's _call_from_vcf and
# _call_from_alignment both call resolve_mt_rnr1_call rather than
# hand-rolling this). Real inputs, not substring checks on app.py's source --
# see review round 1, finding 4 (2026-08-30).


def test_961_overlap_is_false_with_no_records():
    assert has_unresolved_961_deletion([]) is False


def test_961_overlap_is_false_for_snvs_that_are_not_pgx_alleles():
    """HG00096's own variants: real, but not deletions, not near 961."""
    records = [VcfRecord(750, "A", "G"), VcfRecord(1438, "A", "G")]
    assert has_unresolved_961_deletion(records) is False


def test_961_overlap_is_false_for_a_deletion_elsewhere_in_the_gene():
    assert has_unresolved_961_deletion([VcfRecord(700, "AC", "A")]) is False


def test_961_overlap_is_true_for_a_delins_touching_961():
    """A synthetic delins shape (ref longer than alt, alt not a prefix of
    ref -- so not a plain deletion) whose deleted span includes 961. Stands
    in for m.961T>del+Cn, whose real normalised-VCF shape match_alleles
    deliberately does not claim to know (see its own comment) -- this test
    exercises the overlap *mechanism*, not a claim about mutserve's actual
    output shape.
    """
    assert has_unresolved_961_deletion([VcfRecord(960, "CTC", "G")]) is True


def test_961_overlap_is_true_even_for_a_pure_deletion():
    """The helper alone does not distinguish pure vs. delins -- it only
    reports footprint overlap. That is fine: resolve_mt_rnr1_call only ever
    consults it when `matched` is already empty, and a pure deletion at 961
    always matches m.961T>del (see
    test_a_pure_deletion_at_961_names_only_the_plain_deletion), so `matched`
    is never empty for this case in practice.
    """
    assert has_unresolved_961_deletion([VcfRecord(960, "CT", "C")]) is True


def test_resolve_a_real_match_wins_regardless_of_evidence():
    """A caller who forgot pharmcat_absent_to_ref does not get to erase a
    variant that is actually there."""
    call, reason = resolve_mt_rnr1_call(
        ["m.1555A>G"], [], evidence_reason=NO_CALL_NO_CHRM_DATA
    )
    assert call == "m.1555A>G"
    assert reason is None


def test_resolve_blocks_on_missing_evidence_before_looking_at_961():
    """No positive evidence at all -- e.g. app/static/demo/pharmcat.example.vcf,
    which has no chrM contig header and no chrM record -- must never reach
    Reference no matter what `records` contains."""
    call, reason = resolve_mt_rnr1_call([], [], evidence_reason=NO_CALL_NO_CHRM_DATA)
    assert call is None
    assert reason == NO_CALL_NO_CHRM_DATA


def test_resolve_promotes_to_reference_when_evidence_is_positive_and_961_is_clear():
    """HG00096 at ~1331x: real variants, none of them PGx alleles, none of
    them near 961 -- this must still reach Reference."""
    records = [VcfRecord(750, "A", "G"), VcfRecord(1438, "A", "G")]
    matched = match_alleles(records)
    assert matched == []
    call, reason = resolve_mt_rnr1_call(matched, records, evidence_reason=None)
    assert call == REFERENCE
    assert reason is None


def test_resolve_withholds_reference_for_an_unresolved_961_delins():
    """Even with positive evidence, an unmatched delins at 961 must not
    silently become 'normal risk' -- a carrier of only m.961T>del+Cn is a
    no-call, not a guess."""
    records = [VcfRecord(960, "CTC", "G")]
    matched = match_alleles(records)
    assert matched == []
    call, reason = resolve_mt_rnr1_call(matched, records, evidence_reason=None)
    assert call is None
    assert reason == NO_CALL_UNRESOLVED_961_DELINS


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
                "docker",
                "--context",
                "pgx-native",
                "exec",
                "pgx_pharmcat",
                "sh",
                "-c",
                f"cd /tmp && unzip -o -p /pharmcat/pharmcat.jar {MT_RNR1_JSON}",
            ],
            capture_output=True,
            text=True,
            timeout=60,
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
