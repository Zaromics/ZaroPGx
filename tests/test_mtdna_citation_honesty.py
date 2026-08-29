"""The mtDNA-server-2 citation must not read as a tool that ran.

There is no mtDNA service in this stack: ``docker/mtdna-server-2/`` holds a
17-byte README, ``compose.yml`` declares no such service, and
``pipelines/pgx/main.nf`` has no process that calls one. ``needs_mtdna`` is never
set anywhere -- it is only *read*, with a ``False`` default, at
``upload_router.py`` -- so ``used_mtdna`` is always False and the workflow
diagram's mtDNA node never renders.

Every report nonetheless listed mtDNA-server-2 in "Platform and Citations",
unqualified, beside PyPGx/PharmCAT/GATK/ZaroHLA, which do run. The old code also
resolved a version for it (falling back to a hardcoded "2.1.16") and then never
used the variable -- publishing a version for software that is not present would
have been worse than omitting one, so the resolve is gone rather than wired up.

The citation stays, marked not-yet-active. What it must say is the consequence
the reader can see on their own page: MT-RNR1 comes back as a no-call. That is
verified behaviour, not an assumption -- a real GRCh37 run returns
``call_source: "NONE"``, ``called_by: "–"``, ``phenotype: "No Result"`` for
MT-RNR1, because MT-RNR1 is one of the four genes PharmCAT expects as an
*outside* call (``config/genes.json``, ``categories.pharmcat_outside_callers``) and nothing
supplies it.

These tests fail the day the service lands, which is the point: the citation has
to be revisited then, not left claiming the feature is off after it is on.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE = REPO_ROOT / "compose.yml"
MAIN_NF = REPO_ROOT / "pipelines" / "pgx" / "main.nf"
GENES_JSON = REPO_ROOT / "config" / "genes.json"


def _mtdna_citation() -> dict:
    from app.reports.generator import build_citations

    matches = [c for c in build_citations() if c["name"] == "mtDNA-server-2"]
    assert len(matches) == 1, f"expected exactly one mtDNA citation, got {matches}"
    return matches[0]


def test_the_citation_is_still_present():
    """Kept deliberately: dropping it would lose the roadmap attribution."""
    assert _mtdna_citation()["repo"] == "https://github.com/genepi/mtdna-server-2"


def test_the_citation_says_the_feature_is_not_enabled():
    text = _mtdna_citation()["text"].lower()
    assert "not yet enabled" in text


def test_the_citation_names_the_consequence_for_mt_rnr1():
    """A reader seeing an empty MT-RNR1 row should learn why from this line."""
    text = _mtdna_citation()["text"].lower()
    assert "mt-rnr1" in text
    assert "no-call" in text or "no call" in text


def test_the_citation_publishes_no_version_for_absent_software():
    """The old code resolved one, defaulted to "2.1.16", and dropped it."""
    text = _mtdna_citation()["text"]
    assert "2.1.16" not in text
    assert not re.search(r"\bversion\s+\d", text), text


def test_every_other_cited_tool_that_claims_a_version_actually_ships_one():
    """Guard the rule this citation is the exception to."""
    from app.reports.generator import build_citations

    for citation in build_citations():
        if citation["name"] == "mtDNA-server-2":
            continue
        if re.search(r"\bversion\s+", citation["text"]):
            assert re.search(r"\bversion\s+[\w.]+", citation["text"]), citation


# --------------------------------------------------------------------------
# The premise: no mtDNA service exists
# --------------------------------------------------------------------------


def test_there_is_still_no_mtdna_service_in_the_stack():
    """When this fails, the service landed -- go update the citation."""
    assert "mtdna" not in COMPOSE.read_text(encoding="utf-8").lower(), (
        "compose.yml now declares an mtDNA service; the citation still says "
        "mitochondrial typing is not enabled"
    )
    assert "mtdna" not in MAIN_NF.read_text(encoding="utf-8").lower(), (
        "main.nf now has an mtDNA process; the citation still says mitochondrial "
        "typing is not enabled"
    )


def test_mt_rnr1_is_an_outside_call_gene_which_is_why_it_no_calls():
    """The reason the copy gives has to stay true of the configuration."""
    genes = json.loads(GENES_JSON.read_text(encoding="utf-8"))
    outside = genes["categories"]["pharmcat_outside_callers"]["genes"]
    assert "MT-RNR1" in outside, (
        "MT-RNR1 is no longer configured as an outside call; the citation's "
        "explanation for the no-call is stale"
    )
