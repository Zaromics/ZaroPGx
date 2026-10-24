"""scripts/getrm/score.py reads GeT-RM's genotypes as published and compares them with
ZaroPGx's calls only where both can speak.

GeT-RM writes its consensus in each study's nomenclature, with parentheses for alleles a
single assay confirmed and "*1" for "nothing the study tested". These pin the readings
that turn that into a fair comparison, so a score cannot quietly drift into counting
nomenclature as errors (or errors as nomenclature).
"""

from __future__ import annotations

import csv
import importlib.util
from collections import Counter
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
GETRM = REPO / "scripts" / "getrm"


@pytest.fixture(scope="module")
def score():
    spec = importlib.util.spec_from_file_location("getrm_score", GETRM / "score.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "value, readings",
    [
        ("*1/*2", ["*1/*2"]),
        ("*4/*5 or *5/*6", ["*4/*5", "*5/*6"]),
        ("*A/*C;*B/*D", ["*A/*C", "*B/*D"]),
        ("*1A/*1L,*1C/*1F", ["*1A/*1L", "*1C/*1F"]),
        ("*3/(*68)+*4", ["*3/*68+*4"]),
        ("*1(*5)/*1(*27)", ["*1/*1", "*1/*27", "*5/*1", "*5/*27"]),
        ("(*2;*10)/*6", ["*2/*6", "*10/*6"]),
        ("*1/*1 (*27)", ["*1/*1", "*1/*27", "*27/*1"]),
        ("*2 (*35)/*9", ["*2/*9", "*35/*9"]),
    ],
)
def test_parentheses_and_alternatives(score, value, readings):
    assert score.alternatives(value) == readings


def test_haplotypes_count_their_copies(score):
    first, second = score.diplotype("*36x2+*10/*1", "CYP2D6")
    assert first == Counter({"*36": 2, "*10": 1}) and second == Counter({"*1": 1})
    assert score.diplotype("*2xN/*1", "CYP2D6")[0] == Counter({"*2": 2})
    assert score.diplotype("*1 *27/*1", "CYP2B6") is None


@pytest.mark.parametrize(
    "gene, value, expected",
    [
        ("GSTM1", "DEL/DEL", ("*0", "*0")),
        ("GSTT1", "0", ("*0", "*0")),
        ("GSTT1", "(*A/0)", ("*A", "*0")),
        ("SLCO2B1", "WT/(S464F)", ("*1", "*S464F")),
        ("SLC22A2", "*1/(K432Q)", ("*1", "*K432Q")),
    ],
)
def test_deletions_and_protein_names(score, gene, value, expected):
    pair = score.diplotype(score.alternatives(value)[0], gene)
    assert tuple(next(iter(h)) for h in pair) == expected


def test_untested_alleles_are_set_aside_and_said(score):
    # NA12878's UGT1A1: GeT-RM tested *60, which PharmCAT has no definition of, and never
    # *80, which PharmCAT calls on the same haplotype as *28.
    verdict, called, note = score.score_star(
        "*60/*28", "UGT1A1", ["*1/*80+*28"],
        caller_vocab={"*1", "*28", "*80", "*80+*28"},
        tested={"*1", "*28", "*60"}, ref="*1",
    )
    assert verdict == "match_projected"
    assert "not tested by GeT-RM: *80" in note and "not defined by the caller: *60" in note


def test_a_real_difference_stays_one(score):
    verdict, _, _ = score.score_star(
        "*1/*2", "CYP2C19", ["*1/*17"],
        caller_vocab={"*1", "*2", "*17"}, tested={"*1", "*2", "*17"}, ref="*1",
    )
    assert verdict == "discordant"


def test_an_allele_the_caller_cannot_name_is_not_called_its_error(score):
    verdict, _, note = score.score_star(
        "*2/*3", "NUDT15", ["*3/*3"],
        caller_vocab={"*1", "*3"}, tested={"*1", "*2", "*3"}, ref="*1",
    )
    assert verdict == "caller_cannot_name" and "*2" in note


@pytest.mark.parametrize(
    "value, expected",
    [
        ("*1/(*4)", {"c.1601G>A": 1}),
        ("*9/*9", {"c.85T>C": 2}),
        ("c.1627A>G het, c.1905+1G>A het", {"c.1627A>G": 1, "c.1905+1G>A": 1}),
        ("c.1120C>T, c.1896T>C / c.1120C>T, c.1896T>C", {"c.1120C>T": 2, "c.1896T>C": 2}),
        ("c.1679T>G/ no variant detected", {"c.1679T>G": 1}),
        ("exon 4 deletion/ no variants detected", None),
    ],
)
def test_dpyd_is_read_as_variants(score, value, expected):
    got = score.dpyd_truth(value)
    assert (dict(got) if got is not None else None) == expected


def test_dpyd_compares_genotypes_at_the_tested_variants(score):
    entry = {"variants": [
        {"call": "C/T", "referenceAllele": "C", "alleles": ["c.1601G>A (*4)"]},
        {"call": "T/C", "referenceAllele": "T", "alleles": ["c.1627A>G (*5)"]},
        {"call": "C/C", "referenceAllele": "C", "alleles": ["c.1905+1G>A (*2A)"]},
    ]}
    # GeT-RM's 2016 study never tested c.1627A>G, so NA12878's *5 is not held against it.
    assert score.score_dpyd("*1/(*4)", entry, {"c.1601G>A", "c.85T>C"})[0] == "match"
    assert score.score_dpyd("*4/*4", entry, {"c.1601G>A"})[0] == "discordant"


def test_hla_is_compared_at_two_fields(score):
    assert score.score_hla("A*02:01:01/A*32:01:01", ["*32:01/*02:01"])[0] == "match"
    assert score.score_hla("A*01:01/A*11:01", ["A*01:01/A*24:02"])[0] == "discordant"


def test_every_truth_value_is_read_but_a_handful(score):
    """A GeT-RM value score.py cannot read is reported not_scored, never guessed. Keep
    that set small and known."""
    unread = []
    for row in score.load_tsv(GETRM / "truth.tsv"):
        gene, value = row["gene"], row["truth"]
        if gene.startswith(("HLA-", "GGCX", "CYP2C_", "VKORC1_")):
            continue
        if gene == "DPYD":
            ok = score.dpyd_truth(value) is not None
        else:
            ok = all(score.diplotype(v, gene) for v in score.alternatives(value))
        if not ok:
            unread.append((gene, value))
    assert sorted(unread) == sorted([
        ("CYP2B6", "*1(*5) or *1(*22)"),
        ("DPYD", "exon 4 deletion/ no variants detected"),
        ("DPYD", "c.85T>C, (c.1218G>A) (hemizygous), c.1627A>G / exon 11 deletion"),
    ])


def test_truth_and_samples_agree():
    def column(name):
        lines = (GETRM / name).read_text().splitlines()
        return {r["sample"] for r in csv.DictReader(lines, delimiter="\t")}

    samples, truth = column("samples.tsv"), column("truth.tsv")
    core = set((GETRM / "core.txt").read_text().split())
    assert truth == samples and core <= samples and len(core) == 60


def test_a_nameable_allele_missed_beside_an_unnameable_one_is_an_error(score):
    # GeT-RM *27/*28+*60; PyPGx has no *60, but it has *27 and left it out.
    verdict, _, _ = score.score_star(
        "*27/(*28+*60)", "UGT1A1", ["*1/*80+*28"],
        caller_vocab={"*1", "*27", "*28", "*80", "*80+*28"},
        tested={"*1", "*27", "*28", "*60"}, ref="*1",
    )
    assert verdict == "discordant"


def test_nat2_is_compared_by_legacy_group(score):
    # PharmVar's *16 is 341T>C on 803A, the legacy *5A; its *1 is the reference
    # haplotype, 803G, the legacy *12, which GeT-RM's labs mostly reported as *4.
    groups = score.pharmcat_nat2_groups()
    assert groups["*16"] == "*5" and groups["*1"] == "*4" and groups["*6"] == "*6"
    assert "*30" not in groups and "*61" not in groups  # two slow variants; one GeT-RM never tested
    assert score.legacy_nat2("*6/*16", groups) == "*6/*5"
    vocab = {"*4", "*5", "*6", "*7", "*14"}
    tested = {"*4", "*5", "*6", "*7", "*14"}
    assert score.score_star("*5/*6", "NAT2", ["*6/*5"], vocab, tested, "*4")[0] == "match"
    assert score.score_star("(*12)/*5", "NAT2", ["*4/*5"], vocab, tested, "*4")[0] == "match"


def test_hla_ambiguity_is_either_allele(score):
    assert score.score_hla("B*40:06/11/B*51:06", ["B*40:06/B*51:06"])[0] == "match"
    assert score.score_hla("B*40:06/11/B*51:06", ["B*40:11/B*51:06"])[0] == "match"
    assert score.score_hla("C*06:02/C*07:18", ["C*06:02/C*07:01"])[0] == "discordant"


def test_copy_states_a_study_never_reported_are_set_aside(score):
    # GeT-RM's GSTM1 genotypes are DEL/DEL or two named copies: no one-copy deletion.
    verdict, _, note = score.score_star(
        "*B/*B", "GSTM1", ["*0/*B"], caller_vocab={"*0", "*A", "*B"},
        tested={"*0", "*A", "*B"}, ref="*A",
    )
    assert verdict == "match_projected" and "one-copy deletion not tested" in note
    # Where GeT-RM did report one (GSTT1 "*A/0"), the difference stands.
    verdict, _, _ = score.score_star(
        "*A/*A", "GSTT1", ["*0/*A"], caller_vocab={"*0", "*A"},
        tested={"*0", "*A", score.HET_DELETION}, ref="*A",
    )
    assert verdict == "discordant"
    # CYP2A6: no duplication reported, so *1x2 reads as *1.
    verdict, _, note = score.score_star(
        "*1/*1", "CYP2A6", ["*1x2/*1"], caller_vocab={"*1"}, tested={"*1", "*4"}, ref="*1",
    )
    assert verdict == "match_projected" and "extra copies not tested" in note


def test_the_study_vocabulary_marks_copy_states(score):
    rows = [
        {"sample": "s1", "gene": "GSTT1", "truth": "(*A/0)", "source": "x"},
        {"sample": "s2", "gene": "GSTT1", "truth": "(*A/*AXN)", "source": "x"},
        {"sample": "s3", "gene": "GSTM1", "truth": "DEL/DEL", "source": "x"},
    ]
    vocab = score.tested_alleles(rows)
    assert {score.HET_DELETION, score.EXTRA_COPIES} <= vocab[("GSTT1", "x")]
    assert score.HET_DELETION not in vocab[("GSTM1", "x")]
