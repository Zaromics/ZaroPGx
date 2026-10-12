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


def _mtdna_credit(versions=None, components=None, steps=None) -> dict:
    from app.reports.credits import CreditRun, report_credits

    run = CreditRun(steps=steps, file_type="bam")
    rows = report_credits(run, versions or {}, components or {})["software"]
    matches = [row for row in rows if row["key"] == "mtdna"]
    assert len(matches) <= 1, f"expected at most one mtDNA credit, got {matches}"
    return matches[0] if matches else None


def test_the_credit_is_still_present():
    """Kept deliberately: the only place in the report a reader learns which
    pipeline release produced the haplogroup and MT-RNR1 calls on their page."""
    assert _mtdna_credit()["url"] == "https://github.com/genepi/mtdna-server-2"


def test_only_a_run_that_used_it_credits_it():
    """Credited by what ran (app/reports/credits.py): a job whose mtDNA step did
    not complete does not thank mtDNA-Server 2."""
    assert _mtdna_credit(steps=frozenset({"pypgx_analysis"})) is None
    assert _mtdna_credit(steps=frozenset({"mtdna_analysis"})) is not None


def test_the_credit_does_not_say_the_feature_is_off():
    assert "not yet enabled" not in _mtdna_credit()["used_for"].lower()


def test_the_credit_names_what_it_supplies_for_mt_rnr1():
    """Which gene this service supplies the outside call for. Whether a given
    run's MT-RNR1 row is empty, and why, is the report section's job."""
    assert "mt-rnr1" in _mtdna_credit()["used_for"].lower()


def test_the_version_is_a_real_lookup(tmp_path, monkeypatch):
    """The version printed is the one the sidecar recorded, and so are its tools':
    no hardcoded literal that happens to match today's release (BACKLOG 375)."""
    import app.reports.generator as generator
    from app.core.version_manager import VersionManager

    monkeypatch.setenv("KROKI_ENABLED", "false")
    versions_dir = tmp_path / "versions"
    versions_dir.mkdir()
    (versions_dir / "mtdna-server-2.json").write_text(
        json.dumps(
            {
                "name": "mtDNA-server-2",
                "version": "v9.9.9",
                "components": {
                    "mutserve": "8.8.8",
                    "haplogrep3": "7.7.7",
                    "haplocheck": "6.6.6",
                    "phylotree": "phylotree-fu-rcrs@5.5",
                },
            }
        ),
        encoding="utf-8",
    )
    manager = VersionManager(str(versions_dir))
    monkeypatch.setattr(generator, "get_versions_dict", manager.get_versions_dict)
    monkeypatch.setattr(generator, "get_components", manager.get_components)
    credits = generator.build_report_credits()
    versions = {
        row["name"]: row["version"] for row in credits["software"] + credits["data"]
    }
    assert versions["mtDNA-Server 2"] == "9.9.9"
    assert versions["mutserve"] == "8.8.8"
    assert versions["Haplogrep 3"] == "7.7.7"
    assert versions["haplocheck"] == "6.6.6"
    assert versions["PhyloTree"] == "17, Forensic Update 5.5"


def test_without_a_manifest_no_version_is_claimed():
    """A missing manifest prints "not recorded", never a guess."""
    assert _mtdna_credit()["version"] == "not recorded"


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


def test_mt_rnr1_has_one_source_and_it_is_the_mtdna_service():
    """PyPGx has genotyped MT-RNR1 since 0.26.0 (region M:410-1840), and ZaroPGx
    leaves it out of the PyPGx gene set on purpose (checked 2026-09-27). PyPGx calls
    chrM with a diploid bcftools model, has no heteroplasmy handling or coverage
    floor, so an empty region comes back Reference/Reference ("Normal Risk" from
    absent data). Its "X/X" MT-RNR1 diplotype is also refused as an outside call by
    PharmCAT 3.4.0 for a single-chromosome gene, which fails the whole PharmCAT run,
    and main.nf's outside-call merge does not deduplicate a second MT-RNR1 line. A
    future "sync with PyPGx's gene list" must not put it back."""
    sets = json.loads(GENES_JSON.read_text(encoding="utf-8"))["sets"]
    assert "MT-RNR1" not in sets["pypgx"]
    assert "MT-RNR1" not in sets["pypgx_minus_pharmcat"]
    assert "MT-RNR1" in sets["pharmcat_outside_callers"]
