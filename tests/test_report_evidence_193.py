"""Unit tests for the PharmCAT evidence-level tier mapping (BACKLOG 193).

The corpus below is every distinct ``classification`` value present across all
four checked-in PharmCAT fixtures. Before 193, ``Unspecified`` / ``None`` /
``No recommendation`` all fell through the Jinja ``{% else %}`` into
``evidence-0``, the bucket the legend labelled "Guideline available".

Fix round 1 corrected a wrong premise in the first 193 design: ``Unspecified``
was treated as missing data and ranked *below* ``No recommendation``. It is
actually the classification every non-CPIC source (DPWG, FDA) uses, it always
carries real recommendation text, and ranking it low made drug tiles assert
"no dosing change advised" over live DPWG/FDA advisories. Six tiers now, with
``Unspecified`` -> "Guideline available" ranked above ``No recommendation``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.reports.evidence import (
    GUIDELINE_AVAILABLE,
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
        ("Unspecified", "Guideline available", 0, "evidence-unspecified"),
        ("No recommendation", "No recommendation", -1, "evidence-0"),
        ("None", "Unclassified", -2, "evidence-unclassified"),
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
    assert classify_evidence("Unspecified") is GUIDELINE_AVAILABLE
    assert classify_evidence("No recommendation") != classify_evidence("Unspecified")
    assert NO_RECOMMENDATION.css_class != GUIDELINE_AVAILABLE.css_class


def test_unspecified_outranks_no_recommendation():
    """Fix round 1. ``Unspecified`` is *not* absence of information.

    Measured across all four checked-in fixtures, every one of the 100
    ``Unspecified`` annotations carries substantive ``drugRecommendation`` text,
    and it is emitted exclusively by non-CPIC sources (DPWG Guideline
    Annotation, FDA Label Annotation, FDA PGx Association) -- never by CPIC.
    It means "a guideline exists but carries no CPIC letter grade", so it must
    outrank ``No recommendation``, which is a positive statement that nothing
    should change.
    """
    assert GUIDELINE_AVAILABLE.rank > NO_RECOMMENDATION.rank
    assert NO_RECOMMENDATION.rank > UNCLASSIFIED.rank
    assert GUIDELINE_AVAILABLE.css_class != NO_RECOMMENDATION.css_class
    assert GUIDELINE_AVAILABLE is not UNCLASSIFIED


@pytest.mark.parametrize("classification", FIXTURE_CLASSIFICATIONS)
def test_every_fixture_value_is_explicitly_classified(classification):
    """No fixture value may land in the catch-all by accident."""
    tier = classify_evidence(classification)
    if classification == "None":
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
    assert max_evidence(["None", "No recommendation"]) is NO_RECOMMENDATION


def test_max_evidence_prefers_guideline_available_over_no_recommendation():
    """The venlafaxine/eliglustat inversion, at the aggregation level."""
    assert max_evidence(["No recommendation", "Unspecified"]) is GUIDELINE_AVAILABLE
    assert max_evidence(["Unspecified", "No recommendation"]) is GUIDELINE_AVAILABLE


def test_max_evidence_of_all_unclassified_stays_unclassified():
    """Regression on the old ``namespace(max_evidence=0)`` seed."""
    assert max_evidence(["None", "Unknown", None]) is UNCLASSIFIED


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
    assert by_drug["warfarin"]["evidence_rank"] == -1
    assert by_drug["warfarin"]["evidence_class"] == "evidence-0"
    assert by_drug["codeine"]["evidence_rank"] == 0
    assert by_drug["codeine"]["evidence_class"] == "evidence-unspecified"
    # the whole point of round 1: DPWG/FDA guidance outranks "nothing to do"
    assert by_drug["codeine"]["evidence_rank"] > by_drug["warfarin"]["evidence_rank"]


def test_map_recommendations_does_not_mutate_classification():
    """FHIR export and app/api/models.py consume `classification` verbatim."""
    mapped = map_recommendations_for_template(
        [_grouped("codeine", "CYP2D6", "x", "Unspecified")]
    )
    assert mapped[0]["classification"] == "Unspecified"


# ---------------------------------------------------------------------------
# Real-fixture ordering cases (fix round 1)
# ---------------------------------------------------------------------------

TEST_DATA = Path(__file__).resolve().parent.parent / "test_data"
EXAMPLE_REPORT = TEST_DATA / "pharmcat.example.report.json"


def _fixture_annotations(drug):
    """Real ``(source, classification, drugRecommendation)`` rows for one drug.

    Reads the checked-in ``pharmcat.example.report.json``, whose ``drugs`` map is
    keyed by annotation source ("CPIC Guideline Annotation", "DPWG Guideline
    Annotation", "FDA Label Annotation", "FDA PGx Association").
    """
    report = json.loads(EXAMPLE_REPORT.read_text(encoding="utf-8"))
    rows = []
    for source, drugs in (report.get("drugs") or {}).items():
        entry = (drugs or {}).get(drug)
        if not isinstance(entry, dict):
            continue
        for guideline in entry.get("guidelines") or []:
            for annotation in guideline.get("annotations") or []:
                rows.append(
                    (
                        source,
                        annotation.get("classification"),
                        (annotation.get("drugRecommendation") or "").strip(),
                    )
                )
    return rows


def _grouped_from_fixture(drug):
    """Grouped-shape input for ``map_recommendations_for_template``, real data."""
    rows = _fixture_annotations(drug)
    assert rows, f"{drug} not present in {EXAMPLE_REPORT.name}"
    return {
        "drug": drug,
        "genes": ["CYP2D6"],
        "recommendations": [
            {
                "gene": "CYP2D6",
                "recommendation": text,
                "classification": classification,
                "guideline_source": source,
            }
            for source, classification, text in rows
        ],
    }


def _top_evidence_class(mapped):
    """Mirror of the templates' ``|sort(attribute='evidence_rank')|last``."""
    return sorted(mapped, key=lambda r: r["evidence_rank"])[-1]["evidence_class"]


def test_venlafaxine_prefers_dpwg_guidance_over_cpic_no_recommendation():
    """The headline inversion, straight from the checked-in fixture.

    CPIC says ``No recommendation`` / "No action recommended based on genotype".
    DPWG says ``Unspecified`` and carries real dosing guidance. Ranking
    ``Unspecified`` below ``No recommendation`` made the tile assert
    "no dosing change advised" while a DPWG advisory sat underneath it.
    """
    rows = _fixture_annotations("venlafaxine")
    classifications = {c for _, c, _ in rows}
    assert "No recommendation" in classifications
    assert "Unspecified" in classifications
    # every Unspecified row carries substantive text -- it is not "missing data"
    assert all(text for _, c, text in rows if c == "Unspecified")

    mapped = map_recommendations_for_template([_grouped_from_fixture("venlafaxine")])
    assert _top_evidence_class(mapped) == "evidence-unspecified"
    assert _top_evidence_class(mapped) != "evidence-0"


def test_eliglustat_all_unspecified_is_not_unclassified():
    """Breakage 2: an all-``Unspecified`` drug used to render faintest-gray.

    In this fixture eliglustat carries DPWG + FDA Label + FDA PGx guidance, all
    classified ``Unspecified``. Rendering that as "no evidence level reported"
    put real FDA dosing guidance below "nothing to do".
    """
    rows = _fixture_annotations("eliglustat")
    assert {c for _, c, _ in rows} == {"Unspecified"}
    assert all(text for _, _, text in rows)

    mapped = map_recommendations_for_template([_grouped_from_fixture("eliglustat")])
    assert _top_evidence_class(mapped) == "evidence-unspecified"
    assert _top_evidence_class(mapped) != "evidence-unclassified"


def test_eliglustat_mixed_shape_resolves_to_guideline_available():
    """The eliglustat shape carried by ``example_pgx_pharmcat.json``.

    That fixture lives under ``dev-notes/`` (gitignored, absent in CI), so its
    exact shape is reproduced here: DPWG ``No recommendation`` alongside two FDA
    ``Unspecified`` annotations that do carry dosing text.
    """
    mapped = map_recommendations_for_template(
        [
            {
                "drug": "eliglustat",
                "genes": ["CYP2D6"],
                "recommendations": [
                    {
                        "gene": "CYP2D6",
                        "classification": "No recommendation",
                        "recommendation": (
                            "The guideline does not provide a recommendation for "
                            "eliglustat in normal metabolizers."
                        ),
                        "guideline_source": "DPWG Guideline Annotation",
                    },
                    {
                        "gene": "CYP2D6",
                        "classification": "Unspecified",
                        "recommendation": (
                            "CYP2D6 normal metabolizers have a recommended dose "
                            "of 84 mg orally twice daily."
                        ),
                        "guideline_source": "FDA Label Annotation",
                    },
                    {
                        "gene": "CYP2D6",
                        "classification": "Unspecified",
                        "recommendation": (
                            "Alters systemic concentrations, effectiveness, and "
                            "adverse reaction risk (QT prolongation)."
                        ),
                        "guideline_source": "FDA PGx Association",
                    },
                ],
            }
        ]
    )
    assert _top_evidence_class(mapped) == "evidence-unspecified"


def test_every_mapped_recommendation_is_stamped():
    """Guards the ``UndefinedError`` risk in ``|sort(attribute='evidence_rank')``.

    Jinja's ``sort`` raises on a *mixed* sequence where some items lack the
    attribute, which would abort the whole report render. The templates are safe
    only because the mapping funnel stamps every recommendation, so pin that
    across the full fixture rather than a hand-picked drug.
    """
    report = json.loads(EXAMPLE_REPORT.read_text(encoding="utf-8"))
    drugs = sorted(
        {
            drug
            for source_drugs in (report.get("drugs") or {}).values()
            for drug in (source_drugs or {})
        }
    )
    # some drugs are listed by a source but carry no annotations at all
    drugs = [d for d in drugs if _fixture_annotations(d)]
    assert len(drugs) > 50, "fixture should carry a broad drug set"

    mapped = map_recommendations_for_template(
        [_grouped_from_fixture(drug) for drug in drugs]
    )
    assert mapped
    missing = [
        r for r in mapped if "evidence_rank" not in r or "evidence_class" not in r
    ]
    assert not missing, f"{len(missing)} recommendations were left unstamped"
    assert all(isinstance(r["evidence_rank"], int) for r in mapped)


# ---------------------------------------------------------------------------
# Template rendering -- the four ladders and both legends had zero coverage
# ---------------------------------------------------------------------------


def _render(template_name, recommendations):
    """Render a report template through the app's own Jinja environment.

    Uses ``app.reports.generator.env`` rather than a fresh ``Environment`` so the
    custom ``activity_score_num`` filter is registered exactly as it is in the
    HTML lane.
    """
    from app.reports.generator import env

    return env.get_template(template_name).render(
        recommendations=recommendations,
        diplotypes=[],
        gene_drug_recommendations=[],
        organized_recommendations=[],
        patient_id="test-patient",
        report_id="test-report",
        report_date="2026-08-08",
        organization="ZaroPGx",
        disclaimer="",
    )


@pytest.mark.parametrize(
    "template_name", ["report_template.html", "interactive_report.html"]
)
def test_template_renders_venlafaxine_tile_as_guideline_available(template_name):
    """End-to-end: fixture -> mapping funnel -> ``sort|last`` -> tile class."""
    mapped = map_recommendations_for_template([_grouped_from_fixture("venlafaxine")])
    html = _render(template_name, mapped)

    assert "evidence-unspecified" in html
    # the tile itself, not merely the CSS rule or the legend row
    assert 'class="drug-item evidence-unspecified"' in html or (
        'class="drug-item-interactive evidence-unspecified"' in html
    )


@pytest.mark.parametrize(
    "template_name", ["report_template.html", "interactive_report.html"]
)
def test_template_legend_has_all_six_tiers(template_name):
    """Both legends must name every tier the mapping can emit."""
    html = _render(template_name, [])
    for css_class in (
        "evidence-3",
        "evidence-2",
        "evidence-1",
        "evidence-unspecified",
        "evidence-0",
        "evidence-unclassified",
    ):
        assert f'legend-item {css_class}"' in html, f"legend row missing {css_class}"


@pytest.mark.parametrize(
    "template_name", ["report_template.html", "interactive_report.html"]
)
def test_template_legend_makes_no_false_no_dosing_change_claim(template_name):
    """The pre-fix legend asserted a clinical fact the tier did not support."""
    html = _render(template_name, [])
    assert "Guideline available &mdash;" in html or "Guideline available —" in html
    assert "no dosing change advised" not in html


def test_template_debug_artifacts_are_gone():
    """The shipped DEBUG comment and raw-classification tooltip stay deleted."""
    mapped = map_recommendations_for_template([_grouped_from_fixture("venlafaxine")])
    for template_name in ("report_template.html", "interactive_report.html"):
        html = _render(template_name, mapped)
        assert "<!-- DEBUG:" not in html
        assert "Classifications:" not in html
