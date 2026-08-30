"""The mtDNA-server-2 citation must not read as a tool that ran.

The stack is half-landed: ``compose.yml`` now declares an ``mtdna`` service
(Task 5) -- built, healthy, reachable at ``http://mtdna:5000`` -- but nothing
calls it yet. ``pipelines/pgx/main.nf`` has no process that invokes it,
``needs_mtdna`` is never set anywhere -- it is only *read*, with a ``False``
default, at ``upload_router.py`` -- so ``used_mtdna`` is always False and the
workflow diagram's mtDNA node never renders.

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
supplies it. That stays true with the container running but uncalled -- the
citation is still accurate.

``test_there_is_now_an_mtdna_service_in_the_stack`` records that the service
half of the premise changed. ``test_the_pipeline_still_has_no_mtdna_process``
was the other half of the old tripwire: it failed the day ``main.nf`` gained a
process that calls the service (Task 9) -- which is the correct moment to
revisit the citation. That day is this one for the pipeline wiring, but NOT yet
for the citation text: ``params.skip_mtdna`` defaults to ``true``, so no real
job produces an mtDNA result until a later task adds the user-facing toggle.
The test below now pins both halves of that: the process exists, AND the
pipeline still ships with mtDNA off by default -- so "not yet enabled" stays
true of every report a reader can generate today, and a future accidental
default flip (without updating the citation) is caught here instead of
silently making the citation a lie.
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
# The premise: the service exists, but nothing calls it yet
# --------------------------------------------------------------------------


def test_there_is_now_an_mtdna_service_in_the_stack():
    """The service landed in Task 5; this is the record that it did.

    Compose declaring the service is not the thing the citation is about --
    the citation is about whether a run actually calls it and produces an
    MT-RNR1 result. That's still no. This test just retires the half of the
    old premise that's now false, so it stops muddying the real tripwire
    below.
    """
    assert "mtdna" in COMPOSE.read_text(encoding="utf-8").lower(), (
        "compose.yml no longer declares an mtDNA service; the service Task 5 "
        "landed appears to have been removed"
    )


def test_the_pipeline_now_calls_mtdna_but_stays_default_off():
    """Task 9 landed: main.nf gained an mtDNA process. The citation is not
    updated in this same task (that's Task 12) -- it stays honest only because
    the process is wired but switched off by default.

    Two assertions, both load-bearing:
      - the process exists (the old tripwire, inverted -- this IS the moment
        Task 9 was supposed to arrive at);
      - params.skip_mtdna still defaults to true, so a real run produces no
        mtDNA result unless something explicitly opts in. If a later change
        flips that default without updating the citation together, this catches
        it instead of letting "not yet enabled" quietly become false.
    """
    text = MAIN_NF.read_text(encoding="utf-8")
    assert (
        "process MtdnaCall" in text
    ), "main.nf has no mtDNA process; Task 9 has not landed yet"
    match = re.search(
        r"params\.skip_mtdna\s*=.*?\?\s*params\.skip_mtdna\s*:\s*(\w+)", text
    )
    assert match, "could not find params.skip_mtdna's default-value expression"
    assert match.group(1) == "true", (
        "params.skip_mtdna no longer defaults to true -- real jobs would start "
        "producing mtDNA results while the citation still says the feature is "
        "not yet enabled. Update the citation (and this test) together with "
        "the default, in the task that adds the user-facing toggle."
    )


def test_mt_rnr1_is_an_outside_call_gene_which_is_why_it_no_calls():
    """The reason the copy gives has to stay true of the configuration."""
    genes = json.loads(GENES_JSON.read_text(encoding="utf-8"))
    outside = genes["categories"]["pharmcat_outside_callers"]["genes"]
    assert "MT-RNR1" in outside, (
        "MT-RNR1 is no longer configured as an outside call; the citation's "
        "explanation for the no-call is stale"
    )
