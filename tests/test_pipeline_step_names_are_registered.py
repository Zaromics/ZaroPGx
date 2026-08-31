"""Every step_name pipelines/pgx/main.nf posts must exist app-side.

A Nextflow process reports its status by POSTing ``job_id`` + ``step_name`` to its
sidecar, which forwards it to the app. ``JobService.update_job_step`` looks the row
up by EXACT ``step_name`` (``app/services/job_service.py``) and returns None -> 404
when there is none. Steps are only ever minted from the StepTemplates in
``app/services/workflow_registry.py``, so a name main.nf posts that the registry does
not carry never gets a row: every update 404s into a log nobody reads and the UI
leaves that step at [pending] for exactly as long as the step runs. To a user that is
indistinguishable from a stalled job.

That is not hypothetical -- it has now happened three times. ``liftover`` and
``mtdna_analysis`` were each added to main.nf before the registry knew them, and
CramToBAM/SamToBAM posted ``gatk_cram_to_bam``/``gatk_sam_to_bam`` while every
app-side file said ``gatk_cram_sam_to_bam`` and the registry had no GATK conversion
template at all -- so the CRAM and SAM lanes, both supported shipping input types,
hung there on every run.

The per-step tests those incidents left behind each pin one process. This one is
cross-boundary and general: it reads main.nf's own source for what is posted and the
registry/stage map for what exists, so a process added later fails the build instead
of hanging the UI. Neither side is hand-copied; a copy would rot the moment either
moved.

Conditionality is handled by asserting on TEMPLATES, not on the steps any single
``resolve_steps`` call happens to mint: most of these steps are gated on a
``needs_*`` flag and are legitimately absent from most jobs. What must never be
absent is the template.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.api.models import WorkflowOptions
from app.services.workflow_registry import GENOMIC_ANALYSIS, resolve_steps
from app.services.workflow_stages import STEP_TO_STAGE, WorkflowStage

MAIN_NF = Path(__file__).resolve().parents[1] / "pipelines" / "pgx" / "main.nf"

# Deliberately anchored on the curl form flag: `-F step_name=`. Prose in this file's
# comments mentions step names too, and matching those would let a comment satisfy the
# test for a process that posts something else.
_POSTED = re.compile(r"-F step_name=([A-Za-z0-9_]+)")


def _posted_step_names() -> set[str]:
    return set(_POSTED.findall(MAIN_NF.read_text(encoding="utf-8")))


def _templates() -> dict[str, object]:
    return {t.step_name: t for t in GENOMIC_ANALYSIS.step_templates}


def test_the_parser_actually_finds_the_posts():
    """Guard the parser: a silent parse failure would pass every test below."""
    posted = _posted_step_names()
    # The floor is the count at the time of writing (9 distinct names across 11 curl
    # call sites); it only ever grows.
    assert len(posted) >= 9, posted
    assert {"pharmcat_analysis", "liftover", "gatk_cram_sam_to_bam"} <= posted


@pytest.mark.parametrize("step_name", sorted(_posted_step_names()))
def test_every_posted_step_name_has_a_template(step_name):
    templates = _templates()
    assert step_name in templates, (
        f"pipelines/pgx/main.nf posts step_name={step_name!r} but "
        f"app/services/workflow_registry.py mints no such step: its status updates "
        f"will 404 and the UI will hang that step at [pending]. Add a StepTemplate "
        f"gated on the flag the input type actually sets."
    )


@pytest.mark.parametrize("step_name", sorted(_posted_step_names()))
def test_every_posted_step_name_has_a_stage(step_name):
    assert step_name in STEP_TO_STAGE, (
        f"main.nf posts {step_name!r} but STEP_TO_STAGE has no entry, so "
        f"stage_from_step() silently reports it as ANALYSIS -- the progress bar and "
        f"the glyph row will name the wrong stage for the whole step."
    )


@pytest.mark.parametrize(
    "step_name", sorted(t.step_name for t in GENOMIC_ANALYSIS.step_templates)
)
def test_every_gate_names_a_real_option_field(step_name):
    """A typo'd gate is a step that silently never mints.

    ``resolve_steps`` reads gates with ``getattr(options, name, False)``, so a
    misspelled field is not an error -- it is a permanently false condition, and the
    symptom is the same 404 as a missing template.
    """
    tmpl = _templates()[step_name]
    options = WorkflowOptions()
    for gate in (tmpl.when, tmpl.unless):
        if gate is not None:
            assert hasattr(options, gate), (
                f"StepTemplate {step_name!r} gates on {gate!r}, which is not a "
                f"WorkflowOptions field: the step would never mint."
            )
            assert gate in GENOMIC_ANALYSIS.option_fields, (
                f"StepTemplate {step_name!r} gates on {gate!r}, which the recipe does "
                f"not declare in option_fields."
            )


# --- the CRAM/SAM lane, which is what was actually broken ---------------------


def _cram_or_sam_options() -> WorkflowOptions:
    """What FileProcessor.determine_workflow's CRAM and SAM branches set.

    Both set needs_gatk / needs_pypgx / needs_mtdna and nothing else; needs_hla is
    left False there.
    """
    return WorkflowOptions(needs_gatk=True, needs_pypgx=True, needs_mtdna=True)


def test_a_cram_or_sam_job_mints_the_conversion_step():
    steps = resolve_steps("genomic_analysis", _cram_or_sam_options())
    by_name = {s.step_name: s for s in steps}
    assert "gatk_cram_sam_to_bam" in by_name, [s.step_name for s in steps]
    assert by_name["gatk_cram_sam_to_bam"].container_name == "gatk-api"


def test_the_conversion_runs_before_the_steps_that_read_its_bam():
    """PyPGx (and OptiType, when enabled) are handed the BAM this step produces."""
    ordered = [
        s.step_name
        for s in resolve_steps(
            "genomic_analysis",
            WorkflowOptions(
                needs_gatk=True,
                needs_pypgx=True,
                needs_pypgx_bam2vcf=True,
                needs_hla=True,
                needs_mtdna=True,
            ),
        )
    ]
    assert ordered.index("gatk_cram_sam_to_bam") < ordered.index("hla_typing")
    assert ordered.index("gatk_cram_sam_to_bam") < ordered.index("pypgx_bam2vcf")
    assert ordered.index("gatk_cram_sam_to_bam") < ordered.index("pypgx_analysis")


def test_the_conversion_step_lands_in_the_gatk_stage():
    assert STEP_TO_STAGE["gatk_cram_sam_to_bam"] is WorkflowStage.GATK


def test_a_plain_vcf_job_does_not_mint_the_conversion():
    names = [
        s.step_name
        for s in resolve_steps("genomic_analysis", WorkflowOptions(needs_pypgx=True))
    ]
    assert "gatk_cram_sam_to_bam" not in names


def test_a_bcf_job_mints_bcf_to_vcf_and_not_the_cram_sam_conversion():
    """The two conversions share needs_gatk and must not share a step.

    determine_workflow's BCF branch sets needs_conversion AND needs_gatk (bcftools
    runs in the gatk-api container). Gating the CRAM/SAM conversion on needs_gatk
    alone would mint it onto every BCF job, where no process posts it -- the same
    [pending]-forever failure, arrived at from the opposite direction.
    """
    names = [
        s.step_name
        for s in resolve_steps(
            "genomic_analysis",
            WorkflowOptions(needs_conversion=True, needs_gatk=True, needs_pypgx=True),
        )
    ]
    assert "bcf_to_vcf" in names
    assert "gatk_cram_sam_to_bam" not in names


def test_a_cram_job_does_not_mint_bcf_to_vcf():
    """The veto in the other direction: needs_conversion is what mints bcf_to_vcf."""
    names = [
        s.step_name for s in resolve_steps("genomic_analysis", _cram_or_sam_options())
    ]
    assert "bcf_to_vcf" not in names


# --- gatk_alignment: registered, and still unreachable ------------------------


def test_gatk_alignment_is_registered_so_the_registry_and_the_bar_agree():
    """It has a progress band and a canonical-order slot; it needed a template too."""
    from app.services.workflow_progress_calculator import WorkflowProgressCalculator

    assert "gatk_alignment" in _templates()
    assert "gatk_alignment" in WorkflowProgressCalculator.STEP_BASE_BANDS
    assert "gatk_alignment" in WorkflowProgressCalculator.CANONICAL_STEP_ORDER


def test_gatk_alignment_is_gated_on_a_flag_no_real_upload_sets():
    """Registering the name must not amount to claiming FASTQ works.

    FASTQ is refused at ingest -- ZaroPGx ships no aligner and gatk-api's
    /align-fastq answers HTTP 501 -- so determine_workflow's FASTQ branch sets no
    needs_* flag at all, and nothing anywhere sets needs_alignment. The gate is
    therefore false on every job a real upload can produce.
    """
    assert _templates()["gatk_alignment"].when == "needs_alignment"
    for options in (
        WorkflowOptions(),  # plain VCF
        WorkflowOptions(
            needs_conversion=True, needs_gatk=True, needs_pypgx=True
        ),  # BCF
        WorkflowOptions(needs_liftover=True, needs_pypgx=True),  # GRCh37 VCF
        _cram_or_sam_options(),  # CRAM / SAM
        WorkflowOptions(  # BAM
            needs_hla=True,
            needs_pypgx=True,
            needs_pypgx_bam2vcf=True,
            needs_mtdna=True,
        ),
    ):
        names = [s.step_name for s in resolve_steps("genomic_analysis", options)]
        assert "gatk_alignment" not in names, options
