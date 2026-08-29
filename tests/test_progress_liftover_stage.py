"""The liftover step must move the bar and must not be announced as skipped.

Two separate defects, one cause: ``liftover`` was wired into the *stage*
vocabulary (``workflow_stages.STEP_TO_STAGE`` maps it to ``WorkflowStage.GATK``)
but never into the progress calculator's *step* tables.

**The frozen bar.** ``_active_ordered_steps`` keeps only names present in
``STEP_BASE_BANDS``, and ``liftover`` was absent, so it never reached
``_renormalized_ranges``. In
``_calculate_stage_progress_with_container_mapping`` the running-step loop then
hit ``current_step_name not in ranges`` and ``continue``d, falling through to
``return max_achieved_progress`` -- the end of ``header_analysis``. The bar sat
still for the entire lift, which on a real VCF is the longest early step.

**"Skipping GATK processing - not required".** ``_get_stage_message`` and
``_should_skip_stage`` keyed the GATK stage on ``needs_gatk`` alone. A GRCh37 VCF
has ``needs_gatk=False`` and ``needs_liftover=True`` (verified against a real
job's ``job_metadata.workflow``), so the UI announced GATK was being skipped
while Picard LiftoverVcf was running inside the gatk-api container -- the very
reason the stage is GATK.

The bar assertions are written as monotonic-and-distinct rather than against
fixed numbers: ``_renormalized_ranges`` redistributes every band whenever the
active step set changes, so hardcoding percentages would break on any unrelated
step being added.
"""

from __future__ import annotations

import pytest

from app.services.workflow_progress_calculator import (
    WorkflowProgressCalculator,
    WorkflowStage,
)

# Exactly the shape a GRCh37 VCF upload writes to job_metadata["workflow"].
LIFTOVER_CFG = {
    "file_type": "vcf",
    "needs_gatk": False,
    "needs_hla": False,
    "needs_pypgx": True,
    "needs_pypgx_bam2vcf": False,
    "needs_liftover": True,
    "needs_report": True,
}

PLAIN_VCF_CFG = {**LIFTOVER_CFG, "needs_liftover": False}

LIFTOVER_STEP_ORDER = [
    "header_analysis",
    "liftover",
    "pypgx_analysis",
    "pharmcat_analysis",
    "diagram_generation",
    "report_generation",
]


def _steps_with_running(order, running):
    """Step rows with everything before `running` completed."""
    idx = order.index(running)
    return [
        {
            "step_name": name,
            "status": (
                "completed" if i < idx else "running" if i == idx else "pending"
            ),
        }
        for i, name in enumerate(order)
    ]


@pytest.fixture()
def calc():
    return WorkflowProgressCalculator()


# --------------------------------------------------------------------------
# The message
# --------------------------------------------------------------------------


def test_liftover_is_not_announced_as_skipped_gatk(calc):
    """The exact string the user saw while GATK was in fact running."""
    steps = _steps_with_running(LIFTOVER_STEP_ORDER, "liftover")
    info = calc.calculate_progress_from_steps(steps, LIFTOVER_CFG)

    assert "skipping" not in info.message.lower(), info.message


def test_liftover_message_says_what_is_happening(calc):
    steps = _steps_with_running(LIFTOVER_STEP_ORDER, "liftover")
    info = calc.calculate_progress_from_steps(steps, LIFTOVER_CFG)

    assert info.stage is WorkflowStage.GATK
    assert "lift" in info.message.lower()
    assert "GRCh38" in info.message


def test_a_vcf_with_no_liftover_still_reports_gatk_as_skipped(calc):
    """Negative control: the skip message is right when GATK really is idle."""
    order = [s for s in LIFTOVER_STEP_ORDER if s != "liftover"]
    steps = _steps_with_running(order, "header_analysis")
    message = calc._get_stage_message(WorkflowStage.GATK, steps, PLAIN_VCF_CFG)

    assert message == "Skipping GATK processing - not required"


def test_the_gatk_stage_is_not_skippable_when_a_lift_is_planned(calc):
    assert calc._should_skip_stage(WorkflowStage.GATK, LIFTOVER_CFG) is False
    assert calc._should_skip_stage(WorkflowStage.GATK, PLAIN_VCF_CFG) is True


# --------------------------------------------------------------------------
# The bar
# --------------------------------------------------------------------------


def test_liftover_is_a_planned_step(calc):
    assert "liftover" in calc._planned_steps_from_config(LIFTOVER_CFG)
    assert "liftover" not in calc._planned_steps_from_config(PLAIN_VCF_CFG)


def test_liftover_gets_a_progress_band(calc):
    """Absent from STEP_BASE_BANDS it was filtered out and never got a range."""
    active = calc._active_ordered_steps([], LIFTOVER_CFG)
    assert "liftover" in active
    assert "liftover" in calc._renormalized_ranges(active)


def test_starting_the_lift_moves_the_bar_at_all(calc):
    """The frozen-bar regression itself, stated precisely.

    The symptom was not that the number was low -- it was that entering the
    liftover step changed *nothing*. With no band, the running-step loop skipped
    the unknown name and fell through to `max_achieved_progress`, which is
    header_analysis's ceiling: exactly the value already showing before the lift
    began. So the two states below were identical and the bar sat still for the
    whole step. Comparing against header_analysis *running* would not catch it,
    because that value is lower and the bar does appear to advance once.
    """
    idle = calc.calculate_progress_from_steps(
        [
            {"step_name": "header_analysis", "status": "completed"},
            *({"step_name": n, "status": "pending"} for n in LIFTOVER_STEP_ORDER[1:]),
        ],
        LIFTOVER_CFG,
    ).progress_percentage

    lifting = calc.calculate_progress_from_steps(
        _steps_with_running(LIFTOVER_STEP_ORDER, "liftover"), LIFTOVER_CFG
    ).progress_percentage

    assert lifting > idle, (
        f"the bar reads {idle}% both before and during the lift -- entering the "
        "step moved nothing. 'liftover' is missing from STEP_BASE_BANDS again."
    )


def test_progress_is_strictly_increasing_across_the_whole_lifted_run(calc):
    seen = []
    for name in LIFTOVER_STEP_ORDER:
        info = calc.calculate_progress_from_steps(
            _steps_with_running(LIFTOVER_STEP_ORDER, name), LIFTOVER_CFG
        )
        seen.append((name, info.progress_percentage))

    values = [v for _, v in seen]
    assert values == sorted(values), seen
    assert len(set(values)) == len(values), f"two steps share a percentage: {seen}"


def test_a_completed_lifted_run_reaches_100(calc):
    steps = [{"step_name": n, "status": "completed"} for n in LIFTOVER_STEP_ORDER]
    info = calc.calculate_progress_from_steps(steps, LIFTOVER_CFG)

    assert info.progress_percentage == 100
    assert info.stage is WorkflowStage.COMPLETED


def test_the_unlifted_vcf_run_is_unaffected(calc):
    """Adding liftover must not perturb the ordinary GRCh38 path."""
    order = [s for s in LIFTOVER_STEP_ORDER if s != "liftover"]
    values = [
        calc.calculate_progress_from_steps(
            _steps_with_running(order, name), PLAIN_VCF_CFG
        ).progress_percentage
        for name in order
    ]

    assert values == sorted(values)
    assert len(set(values)) == len(values), values


# --------------------------------------------------------------------------
# The bar must not run backwards
# --------------------------------------------------------------------------


def _pypgx_running_at(container_pct):
    return [
        {"step_name": "header_analysis", "status": "completed"},
        {"step_name": "liftover", "status": "completed"},
        {
            "step_name": "pypgx_analysis",
            "status": "running",
            "output_data": {"progress": container_pct},
        },
        {"step_name": "pharmcat_analysis", "status": "pending"},
        {"step_name": "diagram_generation", "status": "pending"},
        {"step_name": "report_generation", "status": "pending"},
    ]


def test_the_no_decrease_rule_survives_a_fresh_calculator_per_request():
    """It never fired once, because the cache was instance state.

    Both call sites -- ``job_service.get_job_progress`` and upload_router's
    status endpoint -- construct ``WorkflowProgressCalculator()` inline on every
    request, so a per-instance cache started empty each time and the guard was
    dead code. Observed live on a real run as 50% -> 40% mid-PyPGx, when PyPGx's
    own reported progress dipped.

    A single calculator would pass this test even with the bug, so each call here
    deliberately builds a new one, exactly as production does.
    """
    job = "regression-no-decrease"
    high = (
        WorkflowProgressCalculator()
        .calculate_progress_from_steps(_pypgx_running_at(90), LIFTOVER_CFG, job)
        .progress_percentage
    )
    dipped = (
        WorkflowProgressCalculator()
        .calculate_progress_from_steps(_pypgx_running_at(50), LIFTOVER_CFG, job)
        .progress_percentage
    )

    assert dipped >= high, f"bar ran backwards: {high}% -> {dipped}%"


def test_the_floor_is_per_job_not_global():
    """One job's progress must not drag another's bar forward."""
    WorkflowProgressCalculator().calculate_progress_from_steps(
        _pypgx_running_at(95), LIFTOVER_CFG, "job-far-along"
    )
    other = (
        WorkflowProgressCalculator()
        .calculate_progress_from_steps(
            [
                {"step_name": "header_analysis", "status": "running"},
                *(
                    {"step_name": n, "status": "pending"}
                    for n in LIFTOVER_STEP_ORDER[1:]
                ),
            ],
            LIFTOVER_CFG,
            "job-just-started",
        )
        .progress_percentage
    )

    assert other == 0, f"a different job's floor leaked in: {other}%"


def test_the_progress_cache_is_bounded():
    """Process-lifetime state keyed by job id needs a ceiling."""
    calc = WorkflowProgressCalculator()
    cap = type(calc)._PROGRESS_CACHE_MAX
    for i in range(cap + 50):
        WorkflowProgressCalculator().calculate_progress_from_steps(
            _pypgx_running_at(10), LIFTOVER_CFG, f"bounded-{i}"
        )

    assert len(type(calc)._previous_progress_cache) <= cap
