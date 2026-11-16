"""The judgement in discordance_evidence.py, on inputs it cannot get wrong by accident.

The script itself needs a corpus and a CRAM for every measurement, so the part worth
pinning is the pure decision: given what the reads showed and what each side's alleles
require, which side leaves something unexplained. It was validated by hand against four
rows of score-final (two for us, one for GeT-RM, one arrangement) and this keeps that
logic honest without the 62 cuts.

The rule it must not lose: a site the reads cannot call confidently -- an indel, a thin
pileup, or an alternate fraction between "absent" and "one copy" -- is kept out of every
verdict. The first version of the script decided NA06991's CYP2B6 row on 3 alternate
reads of 15, and a tool that settles a validation disagreement on that is worse than no
tool at all.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "getrm" / "discordance_evidence.py"


@pytest.fixture(scope="module")
def evidence():
    spec = importlib.util.spec_from_file_location("getrm_discordance_evidence", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


#: One gene's cores, in the shape the lexicon gives: *4 is *2 and *3 together, the way
#: CYP4F2 really defines it, and *1 is the allele for a chromosome with nothing on it.
CORES = {
    "*1": [],
    "*2": ["19-15897578-A-C"],
    "*3": ["19-15879621-C-T"],
    "*4": ["19-15879621-C-T", "19-15897578-A-C"],
}


def seen(**states):
    """Measured rows, keyed by the shorthand this test uses for each position."""
    where = {"a": "19-15879621-C-T", "b": "19-15897578-A-C"}
    return [
        {"variant": where[key], "state": state, "alt": 20, "depth": 40, "af": 0.5}
        for key, state in states.items()
    ]


def test_a_variant_no_allele_on_a_side_requires_is_unexplained_by_that_side(evidence):
    """NA18564's shape: *3 homozygous, *2 present, and GeT-RM's *3/*3 has no *2."""
    theirs, ours = evidence.judge(
        ["*3", "*3"], ["*3", "*4"], seen(a="hom", b="one copy"), CORES
    )
    assert theirs and "15897578" in theirs[0], theirs
    assert ours == []


def test_the_same_question_answered_against_us(evidence):
    """HG00276's shape, inverted: our side is the one missing a present variant."""
    theirs, ours = evidence.judge(
        ["*4", "*1"], ["*1", "*1"], seen(a="one copy", b="one copy"), CORES
    )
    assert ours, "a call naming no variant at all must not explain two present ones"
    assert theirs == []


def test_both_sides_accounting_for_everything_is_not_a_disagreement(evidence):
    """GeT-RM's *2/*3 against our *1/*4: the same two variants, cis against trans."""
    theirs, ours = evidence.judge(
        ["*2", "*3"], ["*1", "*4"], seen(a="one copy", b="one copy"), CORES
    )
    assert (theirs, ours) == ([], []), "an arrangement is not a disagreement"


def test_a_required_variant_the_reads_call_absent_is_reported(evidence):
    theirs, _ = evidence.judge(["*3", "*3"], ["*1", "*1"], seen(a="absent"), CORES)
    assert theirs and "absent" in theirs[0], theirs


@pytest.mark.parametrize("excluded", [{"indel": True}, {"thin": True, "depth": 3}])
def test_a_site_the_reads_cannot_call_is_kept_out_of_the_verdict(evidence, excluded):
    row = {"variant": "19-15897578-A-C", "state": "one copy", "alt": 3, "depth": 15}
    row.update(excluded)
    theirs, ours = evidence.judge(["*1", "*1"], ["*1", "*1"], [row], CORES)
    assert (theirs, ours) == ([], []), "an unmeasurable site must decide nothing"


@pytest.mark.parametrize(
    "alt,depth,expected",
    [
        (3, 15, "unclear"),  # the row the first version of the script decided on
        (4, 20, "unclear"),  # AF 0.20: under ONE_COPY_AF whatever the read count
        (1, 8, "unclear"),  # AF 0.12: above ABSENT_AF, nowhere near a copy
        (5, 20, "one copy"),  # exactly at both thresholds
        (20, 40, "one copy"),
        (0, 38, "absent"),
        (2, 40, "absent"),  # AF 0.05
        (34, 34, "hom"),
        (0, 0, "unclear"),  # no reads at all
    ],
)
def test_what_the_reads_say_at_one_position(evidence, alt, depth, expected):
    """The thresholds themselves, which is where the 3-of-15 bug lived.

    This is the assertion that bites: removing the `unclear` band from ``judge`` changes
    This is the assertion that bites: removing the `unclear` band from ``judge``
    changes nothing, because "unclear" matches none of its branches -- the band only
    does its work here, by never calling a weak site a copy in the first place.
    """
    assert evidence.call_state(alt, depth) == expected


def test_a_weak_site_classified_here_cannot_reach_a_verdict(evidence):
    """End to end over the two functions: 3 of 15 decides nothing."""
    row = {
        "variant": "19-15897578-A-C",
        "alt": 3,
        "depth": 15,
        "af": 0.2,
        "state": evidence.call_state(3, 15),
    }
    assert row["state"] == "unclear"
    assert evidence.judge(["*2", "*2"], ["*1", "*1"], [row], CORES) == ([], [])


def test_a_diplotype_splits_into_the_alleles_it_names(evidence):
    assert evidence.alleles_in("*1/(*2)") == ["*1", "*2"]
    assert evidence.alleles_in("*36 + *10/*36 + *10") == ["*36", "*10", "*36", "*10"]
    assert evidence.alleles_in("*5/*36x2+*10x2") == ["*5", "*36", "*10"]
    assert evidence.alleles_in("") == []
