"""A null strength does not become the word "None" in a clinical bundle.

PharmCAT writes `"classification": null` for CPIC's warfarin annotation, whose entry is
a dosing algorithm rather than a graded recommendation. The normalizer took the
`elif "classification" in annotation` branch -- the key is present, its value is null --
and `str(None)` gave the string "None".

That string is not empty, so it passed `_text()` and the export's own
`if classification and classification.lower() != "unknown"` guard, and every bundle built
from a report carrying warfarin guidance contained:

    {"type": {"text": "Strength of recommendation"},
     "valueCodeableConcept": {"text": "None"}}

Found by building all 61 GeT-RM samples' bundles and reading them: PharmCAT's own JSON
contains no "None" string anywhere, so the value was ours.

`classify_evidence` maps None, "" and "None" alike to Unclassified, so the report page is
unchanged by this -- the defect was only ever visible in the bundle.
"""

from __future__ import annotations

import json

import pytest

from app.pharmcat.pharmcat_client import normalize_pharmcat_results
from app.reports.evidence import classify_evidence


def _report(classification):
    """A PharmCAT report.json holding one drug with one annotation.

    Shaped like the real thing and like tests/test_recommendation_genes.py's fixture:
    `drugs` is keyed by SOURCE, then by drug name, with guidelines[].annotations[].
    """
    annotation = {
        "drugRecommendation": "Use the warfarin dosing algorithm.",
        "classification": classification,
        "lookupKey": [],
        "genotypes": [
            {
                "diplotypes": [
                    {
                        "gene": "CYP2C9",
                        "label": "*1/*2",
                        "phenotypes": ["Intermediate Metabolizer"],
                    }
                ]
            }
        ],
    }
    return {
        "pharmcatVersion": "3.1.1",
        "genes": {},
        "drugs": {
            "CPIC Guideline Annotation": {
                "warfarin": {
                    "name": "warfarin",
                    "id": "PA-warfarin",
                    "guidelines": [
                        {
                            "name": "Annotation of CPIC Guideline for warfarin",
                            "annotations": [annotation],
                        }
                    ],
                }
            }
        },
    }


def _classifications(report):
    """Every classification the normalizer puts on a nested recommendation -- which is
    what the FHIR Task builder reads (`drug_rec["recommendations"]`), not the flat
    drugRecommendations list."""
    data = normalize_pharmcat_results(report).get("data") or {}
    return [
        rec.get("classification")
        for drug in data.get("drugRecommendations") or []
        for rec in drug.get("recommendations") or []
    ]


def test_a_null_classification_does_not_become_the_word_none():
    assert "None" not in _classifications(_report(None))


@pytest.mark.parametrize(
    "value,expected",
    [
        ({"term": "Strong"}, "Strong"),
        ("Moderate", "Moderate"),
        (None, ""),
    ],
)
def test_the_value_that_is_there_still_comes_through(value, expected):
    found = _classifications(_report(value))

    assert found, "the fixture produced no recommendation to classify"
    assert all(c in ("", expected) for c in found), found


def test_none_empty_and_the_word_none_are_all_unclassified():
    """Why this fix cannot change the report: the page derives a card's tier and color
    from classify_evidence, which treats all three the same."""
    tiers = {classify_evidence(v).css_class for v in (None, "", "None")}

    assert tiers == {"evidence-unclassified"}


def test_the_fixture_matches_what_pharmcat_writes():
    """A guard on the test itself: PharmCAT's own JSON carries no "None" string, which
    is what made ours identifiable as ours."""
    assert '"None"' not in json.dumps(_report(None))
