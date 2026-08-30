"""MT-RNR1's vocabulary is PharmCAT's, exactly -- pinned to the jar.

MT-RNR1 is haploid: every diplotypekey in PharmCAT's MT_RNR1.json is a single
allele with count 1. So the outside call is one name, not a diplotype, and it
has to be spelled the way PharmCAT spells it or the call is silently dropped.
"""

import pytest

from app.mtdna.mt_rnr1 import (
    BASIS_INFERRED,
    BASIS_MEASURED,
    MT_RNR1_ALLELES,
    MT_RNR1_SPAN,
    NO_CALL_NO_CHRM_DATA,
    NO_CALL_NOT_CONSENTED,
    NO_CALL_REGION_NOT_COVERED,
    NO_CALL_UNRESOLVED_961_DELINS,
    REFERENCE,
    RISK_INCREASED,
    RISK_NORMAL,
    RISK_UNCERTAIN,
    VcfRecord,
    has_unresolved_961_deletion,
    has_variant_in_gene,
    match_alleles,
    resolve_mt_rnr1_call,
    select_call,
    vcf_evidence,
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
    result = resolve_mt_rnr1_call(
        ["m.1555A>G"], [], evidence_reason=NO_CALL_NO_CHRM_DATA
    )
    assert result.call == "m.1555A>G"
    assert result.no_call_reason is None


def test_resolve_blocks_on_missing_evidence_before_looking_at_961():
    """No positive evidence at all -- e.g. app/static/demo/pharmcat.example.vcf,
    which has no chrM contig header and no chrM record -- must never reach
    Reference no matter what `records` contains."""
    result = resolve_mt_rnr1_call([], [], evidence_reason=NO_CALL_NO_CHRM_DATA)
    assert result.call is None
    assert result.no_call_reason == NO_CALL_NO_CHRM_DATA


def test_resolve_promotes_to_reference_when_evidence_is_positive_and_961_is_clear():
    """HG00096 at ~1331x: real variants, none of them PGx alleles, none of
    them near 961 -- this must still reach Reference."""
    records = [VcfRecord(750, "A", "G"), VcfRecord(1438, "A", "G")]
    matched = match_alleles(records)
    assert matched == []
    result = resolve_mt_rnr1_call(matched, records, evidence_reason=None)
    assert result.call == REFERENCE
    assert result.no_call_reason is None


def test_resolve_withholds_reference_for_an_unresolved_961_delins():
    """Even with positive evidence, an unmatched delins at 961 must not
    silently become 'normal risk' -- a carrier of only m.961T>del+Cn is a
    no-call, not a guess."""
    records = [VcfRecord(960, "CTC", "G")]
    matched = match_alleles(records)
    assert matched == []
    result = resolve_mt_rnr1_call(matched, records, evidence_reason=None)
    assert result.call is None
    assert result.no_call_reason == NO_CALL_UNRESOLVED_961_DELINS


# --- has_variant_in_gene / the evidence ladder's basis ----------------------
#
# HG00096's real haplogroup-defining variants, from this repo's own run.
# 750 and 1438 are inside MT-RNR1 (648-1601); the rest are not.
HG00096_RECORDS = [
    VcfRecord(152, "T", "C"),
    VcfRecord(263, "A", "G"),
    VcfRecord(750, "A", "G"),
    VcfRecord(1438, "A", "G"),
    VcfRecord(4769, "A", "G"),
    VcfRecord(8860, "A", "G"),
    VcfRecord(15326, "A", "G"),
]
OUTSIDE_GENE_ONLY = [VcfRecord(263, "A", "G"), VcfRecord(4769, "A", "G")]


def test_in_gene_predicate_sees_750_and_1438():
    """The two near-universal rCRS differences that land inside MT-RNR1."""
    assert has_variant_in_gene(HG00096_RECORDS) is True


def test_in_gene_predicate_rejects_variants_outside_the_span():
    assert has_variant_in_gene(OUTSIDE_GENE_ONLY) is False


def test_in_gene_predicate_on_no_records():
    assert has_variant_in_gene([]) is False


def test_tier_c_promotes_with_in_gene_variant_and_clean_polys():
    """The case this whole change exists to unlock: a real VCF-input job."""
    result = resolve_mt_rnr1_call(
        [], HG00096_RECORDS, evidence_reason=None, basis=BASIS_INFERRED
    )
    assert result.call == REFERENCE
    assert result.no_call_reason is None
    assert result.basis == BASIS_INFERRED


def test_tier_a_promotion_is_labelled_measured():
    result = resolve_mt_rnr1_call(
        [], HG00096_RECORDS, evidence_reason=None, basis=BASIS_MEASURED
    )
    assert result.call == REFERENCE
    assert result.basis == BASIS_MEASURED


def test_a_denied_tier_carries_its_reason_and_no_basis():
    """A basis passed in on a denied tier must not leak through to the
    no-call. Catches `return MtRnr1Call(None, evidence_reason, basis)` in
    the `evidence_reason` branch -- passing `basis=None` here would let that
    mutation through, since None in, None out is trivially true regardless
    of the branch's own logic.
    """
    result = resolve_mt_rnr1_call(
        [],
        OUTSIDE_GENE_ONLY,
        evidence_reason=NO_CALL_REGION_NOT_COVERED,
        basis=BASIS_INFERRED,
    )
    assert result.call is None
    assert result.no_call_reason == NO_CALL_REGION_NOT_COVERED
    assert result.basis is None


def test_a_real_match_still_wins_over_every_tier():
    """A variant that is actually there is not erased by thin evidence."""
    result = resolve_mt_rnr1_call(
        ["m.1555A>G"],
        [VcfRecord(1555, "A", "G")],
        evidence_reason=NO_CALL_REGION_NOT_COVERED,
        basis=None,
    )
    assert result.call == "m.1555A>G"
    assert result.no_call_reason is None


def test_a_matched_allele_never_carries_a_supplied_basis():
    """A named allele is its own evidence -- a basis beside it would be
    noise. Catches `return MtRnr1Call(call, None, basis)` in the
    already-matched branch: a caller can pass a basis for other reasons (it
    always sets one on the VCF path, say), and this must still come back
    None.
    """
    result = resolve_mt_rnr1_call(
        ["m.1555A>G"],
        [VcfRecord(1555, "A", "G")],
        evidence_reason=None,
        basis=BASIS_MEASURED,
    )
    assert result.call == "m.1555A>G"
    assert result.basis is None


def test_the_961_suppression_still_fires_under_a_licensed_tier():
    """Tier C must not smuggle past the unresolved-delins guard."""
    result = resolve_mt_rnr1_call(
        [], [VcfRecord(960, "CTC", "G")], evidence_reason=None, basis=BASIS_INFERRED
    )
    assert result.call is None
    assert result.no_call_reason == NO_CALL_UNRESOLVED_961_DELINS
    assert result.basis is None


def test_basis_is_never_set_on_a_no_call():
    """A basis describes how a call was established; there is no call here.

    A non-None basis goes in on purpose: passing basis=None would make this
    trivially true regardless of what the `evidence_reason` branch does with
    it. This is the loop form of test_a_denied_tier_carries_its_reason_and_
    no_basis, covering both NO_CALL_* reasons that branch can be handed.
    """
    for reason in (NO_CALL_NO_CHRM_DATA, NO_CALL_REGION_NOT_COVERED):
        result = resolve_mt_rnr1_call(
            [], [], evidence_reason=reason, basis=BASIS_INFERRED
        )
        assert result.basis is None


# --- vcf_evidence: the VCF path's Tier C/D/E decision -----------------------
#
# Lifted out of docker/mtdna-server-2/app.py's _call_from_vcf (review round 1,
# finding 1, 2026-08-30) because that module is not importable here (see
# tests/test_mtdna_vcf_path.py's fixture docstring), so a test pinned against
# its source text can only assert substrings are present -- which cannot fail
# when a conjunction silently degrades to a disjunction, or when
# absent_to_ref stops being checked first. These tests exercise the real
# function with real inputs instead.


def test_vcf_evidence_promotes_when_both_tier_c_halves_hold():
    """The case this whole change exists to unlock: an in-gene variant and a
    clean haplogroup, with consent given."""
    reason, basis = vcf_evidence(
        absent_to_ref=True,
        carried_chrm_data=True,
        records=HG00096_RECORDS,
        haplogroup="H16a1",
        not_found_polys="",
    )
    assert reason is None
    assert basis == BASIS_INFERRED


def test_vcf_evidence_tier_d_on_a_dirty_not_found_polys():
    """An in-gene variant alone is not enough: a non-empty Not_Found_Polys
    means the assigned haplogroup predicted variants that were not observed,
    so coverage was patchy. This is the case a conjunction-turned-disjunction
    would wrongly promote."""
    reason, basis = vcf_evidence(
        absent_to_ref=True,
        carried_chrm_data=True,
        records=HG00096_RECORDS,
        haplogroup="H16a1",
        not_found_polys="16234, 16311",
    )
    assert reason == NO_CALL_REGION_NOT_COVERED
    assert basis is None


def test_vcf_evidence_tier_d_on_no_in_gene_variant():
    """A clean Not_Found_Polys alone is not enough: nothing establishes that
    MT-RNR1 itself was ever interrogated without a variant inside the gene's
    own span. The other half of the same conjunction-turned-disjunction
    case."""
    reason, basis = vcf_evidence(
        absent_to_ref=True,
        carried_chrm_data=True,
        records=OUTSIDE_GENE_ONLY,
        haplogroup="H16a1",
        not_found_polys="",
    )
    assert reason == NO_CALL_REGION_NOT_COVERED
    assert basis is None


def test_vcf_evidence_tier_d_on_no_haplogroup_is_not_a_vacuous_pass():
    """haplogroup=None must fail the conjunction, not pass it by accident --
    e.g. `not (not_found_polys or "").strip()` alone, without the
    `haplogroup is not None` guard, would treat a never-classified sample as
    clean."""
    reason, basis = vcf_evidence(
        absent_to_ref=True,
        carried_chrm_data=True,
        records=HG00096_RECORDS,
        haplogroup=None,
        not_found_polys=None,
    )
    assert reason == NO_CALL_REGION_NOT_COVERED
    assert basis is None


def test_vcf_evidence_tier_e_when_no_chrm_data_beats_everything_else():
    """No chrM data at all is Tier E, not Tier D -- and wins even when the
    caller (wrongly) supplied evidence that would otherwise satisfy Tier C,
    because a real sidecar never would with carried_chrm_data=False (haplogrep3
    never runs), but the function's own gate must not depend on that."""
    reason, basis = vcf_evidence(
        absent_to_ref=True,
        carried_chrm_data=False,
        records=HG00096_RECORDS,
        haplogroup="H16a1",
        not_found_polys="",
    )
    assert reason == NO_CALL_NO_CHRM_DATA
    assert basis is None


def test_vcf_evidence_checks_consent_before_every_other_tier():
    """absent_to_ref is gated first: full Tier C evidence must still
    withhold without the user's consent."""
    reason, basis = vcf_evidence(
        absent_to_ref=False,
        carried_chrm_data=True,
        records=HG00096_RECORDS,
        haplogroup="H16a1",
        not_found_polys="",
    )
    assert reason == NO_CALL_NOT_CONSENTED
    assert basis is None


def test_vcf_evidence_checks_consent_before_the_chrm_data_gate_too():
    """Pins the actual ordering between the two earliest checks: with both
    absent_to_ref and carried_chrm_data false, the reason reported is still
    NOT_CONSENTED, not NO_CHRM_DATA. Reversing that order (checking
    carried_chrm_data first) leaves every other test in this file green --
    none of them exercise both gates failing at once -- so this is the one
    that actually pins the ordering rather than just the presence of the
    absent_to_ref check."""
    reason, basis = vcf_evidence(
        absent_to_ref=False,
        carried_chrm_data=False,
        records=[],
        haplogroup=None,
        not_found_polys=None,
    )
    assert reason == NO_CALL_NOT_CONSENTED
    assert basis is None


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
