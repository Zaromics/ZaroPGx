"""The progress bar follows the work done, whatever order the steps run in.

The bar laid the steps end to end in a fixed order, each in a band, and showed the
end of the furthest finished one. Nextflow runs them side by side (mtDNA and ZaroHLA
beside the PyPGx steps), so a step that finished early pulled the bar to its band's
end and held it there, and a step reporting nothing held it at its band's start.
Replayed on the step rows of three real jobs (2026-10-07):

- 30x NA12878 BAM: 65% at 4 minutes, unmoved for the next 15.
- 237 MB whole-genome VCF: 51% at 30 seconds, unmoved for PyPGx's 5 minutes (also
  seen live, polled every 5 s).
- 30x NA12878 CRAM: 9% for the 33-minute conversion.

It is now a weighted sum over the job's steps: each weighs what it takes on a whole
genome (STEP_EXPECTED_SECONDS); a finished one counts in full; a running one counts
by its own reported progress, else by its elapsed time against its expectation, up
to PACED_CEILING.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.workflow_progress_calculator import WorkflowProgressCalculator

T0 = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)

# (step, start s, end s, reports per-batch progress) from each job's step rows.
BAM = (
    {"file_type": "bam", "needs_hla": True, "needs_mtdna": True},
    [
        ("header_analysis", 0, 2, False),
        ("hla_typing", 12, 258, False),
        ("pypgx_bam2vcf", 259, 845, False),
        ("pypgx_analysis", 846, 1163, True),
        ("mtdna_analysis", 12, 253, False),
        ("pharmcat_analysis", 1163, 1168, False),
        ("report_generation", 1174, 1188, False),
    ],
)
CRAM = (
    {"file_type": "cram", "needs_gatk": True, "needs_hla": True, "needs_mtdna": True},
    [
        ("header_analysis", 0, 0, False),
        ("gatk_cram_sam_to_bam", 4, 1975, False),
        ("hla_typing", 1975, 2218, False),
        ("pypgx_bam2vcf", 2218, 2255, False),
        ("pypgx_analysis", 2255, 2499, True),
        ("mtdna_analysis", 1975, 2212, False),
        ("pharmcat_analysis", 2499, 2503, False),
        ("report_generation", 2507, 2516, False),
    ],
)
VCF = (
    {"file_type": "vcf", "needs_mtdna": True},
    [
        ("header_analysis", 0, 0, False),
        ("pypgx_analysis", 4, 326, True),
        ("mtdna_analysis", 4, 32, False),
        ("pharmcat_analysis", 327, 342, False),
        ("report_generation", 345, 354, False),
    ],
)


def _steps_at(plan, t):
    """The step rows at second t: PyPGx reports after each of its 9 batches."""
    rows = []
    for order, (name, start, end, batches) in enumerate(plan, 1):
        row = {"step_name": name, "step_order": order, "output_data": {}}
        if t < start:
            row["status"] = "pending"
        elif t >= end:
            row["status"] = "completed"
        else:
            row["status"] = "running"
            if batches:
                done = int((t - start) / (end - start) * 9)
                row["output_data"] = {"progress_percent": int(done * 100 / 9)}
        if t >= start:
            row["started_at"] = T0 + timedelta(seconds=start)
        rows.append(row)
    return rows


def _replay(job, label):
    cfg, plan = job
    end = max(e for _, _, e, _ in plan)
    WorkflowProgressCalculator._previous_progress_cache.pop(label, None)
    return [
        (
            t,
            WorkflowProgressCalculator()
            .calculate_progress_from_steps(
                _steps_at(plan, t),
                cfg,
                workflow_id=label,
                now=T0 + timedelta(seconds=t),
            )
            .progress_percentage,
        )
        for t in range(0, end + 6, 5)
    ]


def _longest_flat_seconds(curve):
    longest = run = 0
    for (_, a), (_, b) in zip(curve, curve[1:]):
        run = run + 5 if a == b else 0
        longest = max(longest, run)
    return longest


@pytest.mark.parametrize(
    "job, label, flat_s, off_by",
    [
        # Was 15.1 minutes flat at 65%, 44 points from elapsed time.
        (BAM, "replay-bam", 60, 15),
        # Was 4.8 minutes flat at 51%, 44 points off.
        (VCF, "replay-vcf", 60, 15),
        # Was 32.8 minutes flat at 9%, 69 points off. The conversion runs past its
        # expectation's ceiling for its last 3 minutes, and bam2vcf took 37 s where
        # the BAM lane's took 10 minutes.
        (CRAM, "replay-cram", 240, 30),
    ],
)
def test_a_real_job_moves_steadily_to_100(job, label, flat_s, off_by):
    curve = _replay(job, label)
    end = curve[-1][0]
    values = [p for _, p in curve]
    assert values == sorted(values), "the bar ran backwards"
    assert values[-1] == 100
    assert max(values[:-2]) < 100, "100% before the report was generated"
    assert _longest_flat_seconds(curve) <= flat_s, curve
    assert max(abs(p - 100 * t / end) for t, p in curve) <= off_by, curve


def test_a_step_finishing_early_does_not_pull_the_bar_ahead():
    """mtDNA ends 27 s into the VCF job; PyPGx has 5 minutes to go."""
    cfg, plan = VCF
    early = WorkflowProgressCalculator().calculate_progress_from_steps(
        _steps_at(plan, 40), cfg, now=T0 + timedelta(seconds=40)
    )
    assert early.progress_percentage < 25
    assert early.current_step_name == "pypgx_analysis"


def _one_running(name, started_s_ago, now, **row):
    return [
        {"step_name": "header_analysis", "status": "completed"},
        {
            "step_name": name,
            "status": "running",
            "started_at": now - timedelta(seconds=started_s_ago),
            **row,
        },
        {"step_name": "pharmcat_analysis", "status": "pending"},
        {"step_name": "report_generation", "status": "pending"},
    ]


def test_a_silent_step_is_paced_by_its_elapsed_time_and_never_finished_by_it():
    calc = WorkflowProgressCalculator()
    cfg = {"file_type": "cram"}
    at = [
        calc.calculate_progress_from_steps(
            _one_running("gatk_cram_sam_to_bam", s, T0), cfg, now=T0
        ).progress_percentage
        for s in (0, 600, 1200, 1970 * 0.9, 5000)
    ]
    assert at[0] < at[1] < at[2] < at[3]
    # Overrunning holds at PACED_CEILING of its share, below a finished step's.
    assert at[3] == at[4]
    done = calc.calculate_progress_from_steps(
        [
            {"step_name": "header_analysis", "status": "completed"},
            {"step_name": "gatk_cram_sam_to_bam", "status": "completed"},
            {"step_name": "pharmcat_analysis", "status": "pending"},
            {"step_name": "report_generation", "status": "pending"},
        ],
        cfg,
    ).progress_percentage
    assert at[4] < done


def test_a_step_s_own_progress_wins_over_its_clock():
    calc = WorkflowProgressCalculator()
    cfg = {"file_type": "vcf"}
    quick = calc.calculate_progress_from_steps(
        _one_running("pypgx_analysis", 5, T0, output_data={"progress_percent": 80}),
        cfg,
        now=T0,
    ).progress_percentage
    paced = calc.calculate_progress_from_steps(
        _one_running("pypgx_analysis", 5, T0), cfg, now=T0
    ).progress_percentage
    assert quick > paced


def test_mtdna_from_a_variant_file_weighs_what_it_takes():
    """27 s from the WGS VCF, against 4 minutes from reads."""
    calc = WorkflowProgressCalculator()
    assert calc._expected_seconds("mtdna_analysis", {"file_type": "vcf"}) == 30
    assert (
        calc._expected_seconds(
            "mtdna_analysis", {"file_analysis": {"file_type": "vcf"}}
        )
        == 30
    )
    assert calc._expected_seconds("mtdna_analysis", {"file_type": "bam"}) == 240


def test_a_step_the_table_does_not_name_still_counts():
    steps = [
        {"step_name": "header_analysis", "status": "completed"},
        {"step_name": "something_new", "status": "completed"},
        {"step_name": "report_generation", "status": "pending"},
    ]
    with_it = WorkflowProgressCalculator().calculate_progress_from_steps(steps)
    without = WorkflowProgressCalculator().calculate_progress_from_steps(
        [steps[0], steps[2]]
    )
    assert with_it.progress_percentage > without.progress_percentage


def test_a_failed_or_skipped_step_is_over():
    """PyPGx failing leaves a PharmCAT-only run going; the bar must not wait on it."""
    for status in ("failed", "skipped"):
        info = WorkflowProgressCalculator().calculate_progress_from_steps(
            [
                {"step_name": "header_analysis", "status": "completed"},
                {"step_name": "pypgx_analysis", "status": status},
                {"step_name": "pharmcat_analysis", "status": "completed"},
                {"step_name": "report_generation", "status": "pending"},
            ],
            {"file_type": "vcf"},
        )
        assert info.progress_percentage >= 90, status


def test_100_only_once_the_report_is_generated():
    steps = [
        {"step_name": name, "status": "completed"}
        for name in ("header_analysis", "pypgx_analysis", "pharmcat_analysis")
    ] + [{"step_name": "report_generation", "status": "running"}]
    calc = WorkflowProgressCalculator()
    running = calc.calculate_progress_from_steps(
        steps[:-1]
        + [
            {
                "step_name": "report_generation",
                "status": "running",
                "output_data": {"progress_percent": 100},
            }
        ]
    )
    assert running.progress_percentage == 99
    steps[-1]["status"] = "completed"
    assert calc.calculate_progress_from_steps(steps).progress_percentage == 100


def test_a_start_time_as_text_is_read():
    """The upload router's endpoint and the job service both pass datetimes, but a
    JSON round trip hands back ISO text."""
    calc = WorkflowProgressCalculator()
    as_text = _one_running("gatk_cram_sam_to_bam", 600, T0)
    as_text[1]["started_at"] = as_text[1]["started_at"].isoformat()
    assert (
        calc.calculate_progress_from_steps(
            as_text, {"file_type": "cram"}, now=T0
        ).progress_percentage
        == calc.calculate_progress_from_steps(
            _one_running("gatk_cram_sam_to_bam", 600, T0), {"file_type": "cram"}, now=T0
        ).progress_percentage
        > 0
    )


def test_a_status_still_held_as_the_enum_counts():
    """Within the session that set it, a step's status is the StepStatus member."""
    from app.api.models import StepStatus

    info = WorkflowProgressCalculator().calculate_progress_from_steps(
        [
            {"step_name": "header_analysis", "status": StepStatus.COMPLETED},
            {"step_name": "pharmcat_analysis", "status": StepStatus.COMPLETED},
            {"step_name": "report_generation", "status": StepStatus.PENDING},
        ]
    )
    assert info.progress_percentage > 0
