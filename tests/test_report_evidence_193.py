"""Unit tests for the PharmCAT evidence-level tier mapping (BACKLOG 193).

The corpus below is every distinct ``classification`` value present across all
four checked-in PharmCAT fixtures. Before 193, ``Unspecified`` / ``None`` /
``No recommendation`` all fell through the Jinja ``{% else %}`` into
``evidence-0``, the bucket the legend labelled "Guideline available".
"""

from __future__ import annotations

import pytest

from app.reports.evidence import (
    NO_RECOMMENDATION,
    UNCLASSIFIED,
    classify_evidence,
    max_evidence,
)
from app.reports.generator import map_recommendations_for_template

# Every distinct value across all four fixtures.
FIXTURE_CLASSIFICATIONS = [
    "Strong",
    "Moderate",
    "Optional",
    "No recommendation",
    "Unspecified",
    "None",
]


@pytest.mark.parametrize(
    "classification,name,rank,css_class",
    [
        ("Strong", "Strong", 3, "evidence-3"),
        ("Moderate", "Moderate", 2, "evidence-2"),
        ("Optional", "Optional", 1, "evidence-1"),
        ("No recommendation", "No recommendation", 0, "evidence-0"),
        ("Unspecified", "Unclassified", -1, "evidence-unclassified"),
        ("None", "Unclassified", -1, "evidence-unclassified"),
    ],
)
def test_fixture_values_map_to_explicit_tiers(classification, name, rank, css_class):
    tier = classify_evidence(classification)
    assert tier.name == name
    assert tier.rank == rank
    assert tier.css_class == css_class


def test_no_recommendation_is_not_the_same_tier_as_unspecified():
    """The 193 defect in one assertion: these two used to render identically."""
    assert classify_evidence("No recommendation") is NO_RECOMMENDATION
    assert classify_evidence("Unspecified") is UNCLASSIFIED
    assert classify_evidence("No recommendation") != classify_evidence("Unspecified")
    assert NO_RECOMMENDATION.css_class != UNCLASSIFIED.css_class


@pytest.mark.parametrize("classification", FIXTURE_CLASSIFICATIONS)
def test_every_fixture_value_is_explicitly_classified(classification):
    """No fixture value may land in the catch-all by accident."""
    tier = classify_evidence(classification)
    if classification in {"Unspecified", "None"}:
        assert tier is UNCLASSIFIED
    else:
        assert tier is not UNCLASSIFIED


@pytest.mark.parametrize(
    "raw", ["strong", "  Strong  ", "STRONG", "no recommendation", "UNSPECIFIED"]
)
def test_matching_is_case_and_whitespace_insensitive(raw):
    assert classify_evidence(raw) is classify_evidence(raw.strip().title())


@pytest.mark.parametrize(
    "legacy,rank",
    [
        ("A", 3),
        ("Level A", 3),
        ("Actionable", 3),
        ("B", 2),
        ("Level B", 2),
        ("C", 1),
        ("Level C", 1),
        ("Informational", 1),
        ("Weak", 1),
    ],
)
def test_legacy_letter_forms_keep_their_pre_193_tier(legacy, rank):
    """The old Jinja ladder honoured these; do not regress reports built on them."""
    assert classify_evidence(legacy).rank == rank


@pytest.mark.parametrize("empty", [None, "", "   ", "Unknown", "Related drug"])
def test_absent_or_synthesised_values_are_unclassified(empty):
    assert classify_evidence(empty) is UNCLASSIFIED


def test_unrecognised_value_is_unclassified_not_no_recommendation():
    tier = classify_evidence("Some Future PharmCAT Level")
    assert tier is UNCLASSIFIED
    assert tier.css_class == "evidence-unclassified"


def test_max_evidence_picks_the_highest_rank():
    assert max_evidence(["Unspecified", "Strong", "No recommendation"]).rank == 3
    assert max_evidence(["Unspecified", "Optional"]).rank == 1


def test_max_evidence_prefers_no_recommendation_over_unclassified():
    """No recommendation is a positive finding; Unclassified is absence of one."""
    assert max_evidence(["Unspecified", "No recommendation"]) is NO_RECOMMENDATION


def test_max_evidence_of_all_unclassified_stays_unclassified():
    """Regression on the old ``namespace(max_evidence=0)`` seed."""
    assert max_evidence(["Unspecified", "None", None]) is UNCLASSIFIED


def test_max_evidence_of_empty_is_unclassified():
    assert max_evidence([]) is UNCLASSIFIED


def _grouped(drug, gene, recommendation, classification):
    """The shape real callers pass.

    ``map_recommendations_for_template`` selects its branch on
    ``isinstance(genes, list) and isinstance(recommendations, list)``, and both
    ``.get`` calls default to ``[]`` -- so the "legacy flattened" ``else`` arm is
    unreachable for a flat ``{"drug", "gene", ...}`` dict and such input maps to
    ``[]``. The only live call site (``generator.py:941-945``) gates on
    ``"genes" in rec and "gene" not in rec``, i.e. this grouped shape.
    """
    return {
        "drug": drug,
        "genes": [gene],
        "recommendations": [
            {
                "gene": gene,
                "recommendation": recommendation,
                "classification": classification,
            }
        ],
    }


def test_map_recommendations_stamps_evidence_keys():
    mapped = map_recommendations_for_template(
        [
            _grouped("amitriptyline", "CYP2D6", "Consider alternative", "Strong"),
            _grouped(
                "warfarin", "CYP2C9", "See report for details", "No recommendation"
            ),
            _grouped("codeine", "CYP2D6", "See report for details", "Unspecified"),
        ]
    )
    by_drug = {m["drug"]: m for m in mapped}

    assert by_drug["amitriptyline"]["evidence_rank"] == 3
    assert by_drug["amitriptyline"]["evidence_class"] == "evidence-3"
    assert by_drug["warfarin"]["evidence_rank"] == 0
    assert by_drug["warfarin"]["evidence_class"] == "evidence-0"
    assert by_drug["codeine"]["evidence_rank"] == -1
    assert by_drug["codeine"]["evidence_class"] == "evidence-unclassified"


def test_map_recommendations_does_not_mutate_classification():
    """FHIR export and app/api/models.py consume `classification` verbatim."""
    mapped = map_recommendations_for_template(
        [_grouped("codeine", "CYP2D6", "x", "Unspecified")]
    )
    assert mapped[0]["classification"] == "Unspecified"
