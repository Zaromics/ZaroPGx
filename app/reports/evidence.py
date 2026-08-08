"""PharmCAT evidence-level tiering for report rendering (BACKLOG 193).

PharmCAT emits ``classification`` as a bare string. Across every checked-in
fixture the distinct values are ``Strong``, ``Moderate``, ``Optional``,
``No recommendation``, ``Unspecified`` and ``None``. The report templates used
to ladder only STRONG / MODERATE / OPTIONAL and dump everything else into
``evidence-0`` -- the bucket the legend labelled "Guideline available" -- so a
drug with *no* recommendation rendered identically to one with a guideline.

This module is the single source of truth for that mapping. It is deliberately
a pure function rather than a Jinja macro: this repo has no template-rendering
test harness, and ``pdf_generators.py`` builds a Jinja ``Environment`` without
registering custom callables, so template-side logic is untestable *and*
inconsistently available.

``rank`` exists only for per-drug aggregation (a drug's tile shows its highest
tier). ``css_class`` is decoupled from ``rank`` on purpose: ``evidence-0..3``
already drive dozens of CSS rules and appear in reports already on disk, so
they keep their numbering while ranks are free to be renumbered.

Six tiers, because ``Unspecified`` is its own thing
---------------------------------------------------
An earlier revision folded ``Unspecified`` into ``Unclassified`` on the premise
that it meant "no evidence level reported". That premise was wrong, and it was
a clinical-safety bug. Measured across all four checked-in PharmCAT fixtures:

* ``Unspecified`` is emitted **only** by non-CPIC sources -- DPWG Guideline
  Annotation (33), FDA Label Annotation (32), FDA PGx Association (35). CPIC
  never emits it.
* **Every single one** of those 100 annotations carries substantive
  ``drugRecommendation`` text; roughly half contains avoid / contra-indicated /
  dose-adjustment language.

So ``Unspecified`` means "a guideline exists, it just carries no CPIC letter
grade" -- strictly more informative than ``No recommendation``, which is the
positive statement that nothing should change. It therefore ranks *above*
``No recommendation``. Ranking it below produced two real defects: venlafaxine
(CPIC ``No recommendation`` + DPWG ``Unspecified`` advising against use) resolved
to the tile that claims "no dosing change advised", and all-``Unspecified``
drugs such as eliglustat rendered fainter than "nothing to do".

``Strong`` / ``Moderate`` / ``Optional`` are CPIC-exclusive in every fixture, so
their legend copy may safely cite CPIC Levels A/B/C. ``No recommendation`` is
**not** CPIC-exclusive (CPIC 3, DPWG 59), so its copy must stay source-neutral.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Tuple


@dataclass(frozen=True)
class EvidenceTier:
    """One rung of the evidence ladder."""

    name: str
    rank: int
    css_class: str


STRONG = EvidenceTier("Strong", 3, "evidence-3")
MODERATE = EvidenceTier("Moderate", 2, "evidence-2")
OPTIONAL = EvidenceTier("Optional", 1, "evidence-1")
# DPWG / FDA guidance with no CPIC letter grade. Ranks above NO_RECOMMENDATION
# because it carries actual dosing text -- see the module docstring.
GUIDELINE_AVAILABLE = EvidenceTier("Guideline available", 0, "evidence-unspecified")
# A positive statement that nothing should change. Ranks above UNCLASSIFIED
# (which is absence of information) and below GUIDELINE_AVAILABLE.
NO_RECOMMENDATION = EvidenceTier("No recommendation", -1, "evidence-0")
# Genuinely missing: null, blank, unknown, or unrecognised.
UNCLASSIFIED = EvidenceTier("Unclassified", -2, "evidence-unclassified")

# Ranks are deliberately NOT the numbers in the CSS class names. The classes
# keep their historical numbering (reports already on disk use them); the ranks
# are renumbered freely so ordering comes out right.
ALL_TIERS: Tuple[EvidenceTier, ...] = (
    STRONG,
    MODERATE,
    OPTIONAL,
    GUIDELINE_AVAILABLE,
    NO_RECOMMENDATION,
    UNCLASSIFIED,
)

# Exact matches, checked first so "NO RECOMMENDATION" cannot be swallowed by a
# substring arm. Keys are upper-cased and stripped.
_EXACT: Dict[str, EvidenceTier] = {
    "STRONG": STRONG,
    "MODERATE": MODERATE,
    "OPTIONAL": OPTIONAL,
    "NO RECOMMENDATION": NO_RECOMMENDATION,
    # Non-CPIC (DPWG / FDA) guidance. NOT "missing" -- see the module docstring.
    "UNSPECIFIED": GUIDELINE_AVAILABLE,
    "NONE": UNCLASSIFIED,
    "UNKNOWN": UNCLASSIFIED,
    # Synthesised by pharmcat_client for the relatedDrugs path; carries no
    # evidence statement of its own.
    "RELATED DRUG": UNCLASSIFIED,
    # CPIC letter levels, honoured by the pre-193 Jinja ladder. Absent from
    # every fixture, kept so archived/DB-lane data does not regress.
    "A": STRONG,
    "LEVEL A": STRONG,
    "B": MODERATE,
    "LEVEL B": MODERATE,
    "C": OPTIONAL,
    "LEVEL C": OPTIONAL,
}

# Substring arms, preserving the pre-193 ladder's fuzzy matching. Order matters.
_SUBSTRING: Tuple[Tuple[str, EvidenceTier], ...] = (
    ("STRONG", STRONG),
    ("ACTIONABLE", STRONG),
    ("MODERATE", MODERATE),
    ("OPTIONAL", OPTIONAL),
    ("INFORMATIONAL", OPTIONAL),
    ("WEAK", OPTIONAL),
)


def classify_evidence(classification: Optional[str]) -> EvidenceTier:
    """Map a PharmCAT ``classification`` string to its evidence tier.

    Unrecognised, empty and missing values resolve to :data:`UNCLASSIFIED` --
    never to :data:`NO_RECOMMENDATION`, which is a specific PharmCAT finding.
    """
    if classification is None:
        return UNCLASSIFIED

    normalized = str(classification).strip().upper()
    if not normalized:
        return UNCLASSIFIED

    exact = _EXACT.get(normalized)
    if exact is not None:
        return exact

    for needle, tier in _SUBSTRING:
        if needle in normalized:
            return tier

    return UNCLASSIFIED


def max_evidence(classifications: Iterable[Optional[str]]) -> EvidenceTier:
    """Highest tier across a drug's recommendations; ``UNCLASSIFIED`` if empty."""
    best = UNCLASSIFIED
    for classification in classifications:
        tier = classify_evidence(classification)
        if tier.rank > best.rank:
            best = tier
    return best
