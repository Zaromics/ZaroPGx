"""The mtDNA-server-2 citation must not read as a tool that ran.

The stack was half-landed for most of this feature's build-out: ``compose.yml``
declared an ``mtdna`` service (Task 5) -- built, healthy, reachable at
``http://mtdna:5000`` -- while ``pipelines/pgx/main.nf`` had no process that
invoked it, and ``needs_mtdna`` was never set anywhere, so ``used_mtdna`` was
always False and the workflow diagram's mtDNA node never rendered.

Every report nonetheless listed mtDNA-server-2 in "Platform and Citations",
unqualified, beside PyPGx/PharmCAT/GATK/ZaroHLA, which do run. The old code
also resolved a version for it (falling back to a hardcoded "2.1.16") and then
never used the variable -- publishing a version for software that is not
present would have been worse than omitting one, so the resolve was dropped
rather than wired up, and the citation was marked not-yet-active instead: MT-
RNR1 comes back as a no-call, verified against a real GRCh37 run --
``call_source: "NONE"``, ``called_by: "-"``, ``phenotype: "No Result"`` --
because MT-RNR1 is one of the four genes PharmCAT expects as an *outside*
call (``config/genes.json``, ``categories.pharmcat_outside_callers``) and
nothing supplied it.

Task 9 then gave ``main.nf`` a real ``MtdnaCall`` process, wired but switched
off by default (``params.skip_mtdna`` true) -- the pipeline could call the
service, but no ordinary job did, so the citation staying "not yet enabled"
was still accurate. Task 11 gave the user a toggle that flips that default
per-request. Task 12 -- this file -- is the day the citation's own premise
inverts: mtDNA is genuinely reachable from the UI, ``mtdna_result.json`` lands
in a real report directory, and the report itself now explains an MT-RNR1
no-call row from the run's own data (coverage, or a VCF job's
``pharmcat_absent_to_ref`` setting) instead of leaning on a blanket line in
the citation to do it. So the citation's job shrinks back to what every other
tool's citation does here: name the software, name a version that actually
resolved, say what it does. Not "not yet enabled". Not "so MT-RNR1 is a
no-call" -- that would now be printed on runs where MT-RNR1 resolved to a
real call, which would be worse than the thing this file was written to
prevent.

Every assertion below is either the direct inverse of what this file asserted
before Task 12, or an old assertion whose premise stayed true and is kept
that way on purpose (the pipeline still defaults to off; the citation is still
present; MT-RNR1 is still configured as an outside call).
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
    """Kept deliberately, assertion unchanged: dropping it would lose the
    citation's identity -- now the only place in the report a reader learns
    which pipeline release, and which three component tools inside it,
    produced the haplogroup and MT-RNR1 calls on their page.
    """
    assert _mtdna_citation()["repo"] == "https://github.com/genepi/mtdna-server-2"


def test_the_citation_no_longer_says_the_feature_is_off():
    """The inverse of test_the_citation_says_the_feature_is_not_enabled,
    which asserted the opposite: that "not yet enabled" *was* present. That
    was true through Task 9 (the process existed but stayed default-off); it
    stopped being true the day a user could flip the toggle and get a real
    result, which is this task.
    """
    text = _mtdna_citation()["text"].lower()
    assert "not yet enabled" not in text


def test_the_citation_still_names_what_it_supplies_for_mt_rnr1():
    """The inverse-in-spirit of test_the_citation_names_the_consequence_for_
    mt_rnr1, which required the literal words "no call"/"no-call" in the
    citation. Asserting that now would be actively wrong on a run where mtDNA
    did resolve an MT-RNR1 allele -- printing "no call" on a report that shows
    a real call is a worse lie than the one this file exists to catch. What
    survives is the fact underneath: the citation still has to say which gene
    this service supplies the outside call for. Which row is empty, and why,
    is now the report section's job (see generate_report's ``mtdna`` template
    context, built from mtdna_result.json), not the citation's.
    """
    text = _mtdna_citation()["text"].lower()
    assert "mt-rnr1" in text


def test_the_citation_publishes_a_real_version():
    """The inverse of test_the_citation_publishes_no_version_for_absent_
    software, which asserted no "version N" pattern was present at all --
    correct while nothing installed could resolve one. BACKLOG 375, closed
    properly this time: a resolved value, not a hardcoded literal quietly
    unused (that was the original bug the old test's docstring diagnosed).
    """
    assert re.search(r"\bversion\s+v?2\.1\.\d+", _mtdna_citation()["text"])


def test_the_version_is_a_real_lookup_not_a_coincidence(tmp_path, monkeypatch):
    """The test above only checks the *shape* of the version string, and
    generator.py's fallback -- ``_ver("mtdna-server-2", "v2.1.16")`` --
    happens to equal the exact literal the sidecar publishes today
    (docker/mtdna-server-2/app.py's ``PIPELINE_VERSION``). So a citation that
    merely matches ``version v2.1.x`` cannot tell a real manifest lookup from
    a lookup that silently missed and fell back -- which is precisely the
    defect BACKLOG 375 named: a version string printed for software that was
    never actually resolved. Point VersionManager at a manifest publishing a
    version the fallback could not produce by accident, and confirm the
    citation follows it rather than the hardcoded literal.
    """
    import app.reports.generator as generator
    from app.core.version_manager import VersionManager

    versions_dir = tmp_path / "versions"
    versions_dir.mkdir()
    (versions_dir / "mtdna-server-2.json").write_text(
        json.dumps(
            {
                "name": "mtDNA-server-2",
                "version": "v9.9.9",
                "components": {
                    "mutserve": "2.0.3",
                    "haplogrep3": "3.2.2",
                    "haplocheck": "1.3.3",
                },
            }
        ),
        encoding="utf-8",
    )
    # Same key resolution VersionManager.get_versions_dict documents: the
    # manifest's own "name" field, lowercased -- "mtDNA-server-2" -> the
    # "mtdna-server-2" key generator.py's _ver() looks up.
    monkeypatch.setattr(
        generator,
        "get_versions_dict",
        VersionManager(str(versions_dir)).get_versions_dict,
    )
    matches = [c for c in generator.build_citations() if c["name"] == "mtDNA-server-2"]
    text = matches[0]["text"]
    assert "9.9.9" in text, f"citation did not follow the manifest lookup: {text!r}"
    assert "2.1.16" not in text, (
        f"citation printed the hardcoded fallback instead of the resolved "
        f"manifest version: {text!r}"
    )


def test_every_cited_tool_that_claims_a_version_actually_ships_one():
    """The inverse of test_every_other_cited_tool_that_claims_a_version_
    actually_ships_one, which carved mtDNA-server-2 out of this guard because
    nothing installed could resolve a version for it yet. Now that it does
    (test_the_citation_publishes_a_real_version, above), it is checked like
    every other tool instead of skipped -- no more exception to guard.
    """
    from app.reports.generator import build_citations

    for citation in build_citations():
        if re.search(r"\bversion\s+", citation["text"]):
            assert re.search(r"\bversion\s+[\w.]+", citation["text"]), citation


# --------------------------------------------------------------------------
# The premise: the service exists, and now a real run can actually call it
# --------------------------------------------------------------------------


def test_there_is_now_an_mtdna_service_in_the_stack():
    """The service half of the old premise (Task 5) plus the pipeline half
    (Task 9), both still true and both still worth pinning together: if
    either the compose service or the main.nf process were ever removed, the
    citation above would go back to describing software that cannot run.
    """
    assert "mtdna" in COMPOSE.read_text(encoding="utf-8").lower(), (
        "compose.yml no longer declares an mtDNA service; the service Task 5 "
        "landed appears to have been removed"
    )
    assert "mtdna" in MAIN_NF.read_text(encoding="utf-8").lower(), (
        "pipelines/pgx/main.nf no longer mentions mtdna; the process Task 9 "
        "landed appears to have been removed"
    )


def test_the_pipeline_still_defaults_skip_mtdna_to_true():
    """Renamed from test_the_pipeline_now_calls_mtdna_but_stays_default_off,
    whose docstring tied this assertion to citation honesty ("the citation is
    not updated in this same task ... it stays honest only because the
    process is wired but switched off by default"). That reasoning is now
    stale: the citation above makes no claim about being universally off, so
    it no longer depends on this default. The default itself still matters
    for a different reason -- every job that has never touched Task 11's
    mtDNA toggle should keep behaving exactly as it did before this feature
    existed. A silent flip here would start calling an external service, on
    the shared /data volume, for jobs nobody opted in.
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
        "params.skip_mtdna no longer defaults to true -- a job that never "
        "touches the mtDNA toggle would now call an external service it did "
        "not ask for. If this default is meant to change, update it together "
        "with upload_router.py's toggle wiring, not by itself."
    )


def test_mt_rnr1_is_still_an_outside_call_gene():
    """Unchanged: this service is what supplies that outside call, whether
    the call it supplies is a named allele or a well-explained no-call.
    """
    genes = json.loads(GENES_JSON.read_text(encoding="utf-8"))
    outside = genes["categories"]["pharmcat_outside_callers"]["genes"]
    assert "MT-RNR1" in outside, (
        "MT-RNR1 is no longer configured as an outside call; the citation's "
        "'supplying the MT-RNR1 outside call' claim is stale"
    )
