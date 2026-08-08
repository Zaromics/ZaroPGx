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

What ``classification`` actually is
-----------------------------------
It is CPIC's **strength of recommendation** (Strong / Moderate / Optional, plus
``No recommendation``), *not* CPIC **Level of Evidence** (A / B / C / D). Those
are two separate CPIC axes: strength grades an individual therapeutic
recommendation, while CPIC Level is a designation on a gene-drug *pair*. No
level-of-evidence field exists anywhere in a PharmCAT ``report.json`` -- the
annotation object carries ``implications``, ``drugRecommendation``,
``classification``, ``activityScore``, ``population``, ``genotypes`` and
friends, and no A/B/C/D value appears in any fixture. Report copy must
therefore never render ``classification`` as "CPIC Level A/B/C".

Nor does a strength grade imply a prescribing *change*: 21 of the 52 ``Strong``
annotations in ``pharmcat.example.report.json`` advise the standard or
label-recommended dose. "Strong" means CPIC is confident in the recommendation,
whatever that recommendation says.

Six tiers, because ``Unspecified`` is its own thing
---------------------------------------------------
An earlier revision folded ``Unspecified`` into ``Unclassified`` on the premise
that it meant "nothing was reported". That premise was wrong, and it was a
clinical-safety bug.

Counts below are reproducible from the three **git-tracked** fixtures, which are
the only ones carrying ``classification``: ``test_data/pharmcat.example.report.json``,
``test_data/pharmcat.example.v340.report.json`` and
``test_data/pharmcat.example.nested.v2.report.json`` -- 175 annotations total.
(An earlier revision of this docstring also counted a fourth file under the
gitignored ``dev-notes/``. It is absent from clean checkouts and from CI, so its
numbers were not reproducible; it is no longer cited. Dropping it changed no
conclusion.)

* ``Unspecified`` -- 66 annotations, emitted **only** by non-CPIC sources: DPWG
  Guideline Annotation 29, FDA PGx Association 20, FDA Label Annotation 17.
  CPIC never emits it.
* **All 66** carry non-empty ``drugRecommendation`` text, 29 of them containing
  avoid / contra-indicated / dose-adjustment language.
* ``No recommendation`` -- 28 annotations (CPIC 3, DPWG 25). None directs a
  therapy change; they advise the standard dose or state that the guideline
  offers no recommendation.
* ``Strong`` 54 / ``Moderate`` 11 / ``Optional`` 15 -- all CPIC-exclusive, which
  is why only those three rows name CPIC in the legend.

So ``Unspecified`` means "a guideline exists, it just carries no CPIC strength
grade" -- strictly more informative than ``No recommendation``, which is the
positive statement that nothing should change. It therefore ranks *above*
``No recommendation``. Ranking it below produced two real defects: venlafaxine
(CPIC ``No recommendation`` + DPWG ``Unspecified`` advising against use) resolved
to the tile that claimed "no dosing change advised", and all-``Unspecified``
drugs such as eliglustat rendered fainter than "nothing to do".

``No recommendation`` is **not** CPIC-exclusive (CPIC 3, DPWG 25), so its legend
copy stays source-neutral.
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
