"""58/102 — the bar spans only the steps a job has.

A step the job does not need is never minted (workflow_registry), so it takes no
share of the bar: a VCF job's 100% is header, PyPGx, PharmCAT and the report alone.
This used to be done by laying bands end to end and renormalising them; the bar is
now a weighted sum over the job's own steps (tests/test_progress_parallel_steps.py),
which spans exactly those steps by construction.
"""

from app.services.workflow_progress_calculator import WorkflowProgressCalculator

E = WorkflowProgressCalculator.STEP_EXPECTED_SECONDS
VCF_STEPS = [
    "header_analysis",
    "pypgx_analysis",
    "pharmcat_analysis",
    "report_generation",
]


def _vcf_config(**overrides):
    cfg = {
        "needs_gatk": False,
        "needs_hla": False,
        "needs_pypgx": True,
        "needs_pypgx_bam2vcf": False,
        "file_analysis": {"file_type": "vcf"},
    }
    cfg.update(overrides)
    return cfg


def _rows(**status):
    return [
        {
            "step_name": name,
            "step_order": i,
            "status": status.get(name, "pending"),
            "output_data": {},
        }
        for i, name in enumerate(VCF_STEPS, 1)
    ]


def test_vcf_pypgx_running_50_is_half_its_share():
    rows = _rows(header_analysis="completed", pypgx_analysis="running")
    rows[1]["output_data"] = {"progress_percent": 50}
    info = WorkflowProgressCalculator().calculate_progress_from_steps(
        rows, _vcf_config()
    )
    total = sum(E[s] for s in VCF_STEPS)
    assert info.progress_percentage == int(
        (E["header_analysis"] + E["pypgx_analysis"] / 2) * 100 / total
    )


def test_a_step_the_job_does_not_have_takes_no_share():
    """With ZaroHLA minted, the same point in the run is a smaller share."""
    calc = WorkflowProgressCalculator()
    plain = calc.calculate_progress_from_steps(
        _rows(header_analysis="completed", pypgx_analysis="completed"), _vcf_config()
    )
    with_hla = calc.calculate_progress_from_steps(
        _rows(header_analysis="completed", pypgx_analysis="completed")
        + [{"step_name": "hla_typing", "status": "pending"}],
        _vcf_config(),
    )
    assert with_hla.progress_percentage < plain.progress_percentage


def test_vcf_complete_is_100():
    info = WorkflowProgressCalculator().calculate_progress_from_steps(
        _rows(**{s: "completed" for s in VCF_STEPS}), _vcf_config()
    )
    assert info.progress_percentage == 100


def test_bam2vcf_is_listed_before_the_genotyping_it_feeds():
    """PyPGxBam2Vcf produces the VCF PyPGxGenotypeAll reads."""
    order = WorkflowProgressCalculator.CANONICAL_STEP_ORDER
    assert order.index("pypgx_bam2vcf") < order.index("pypgx_analysis")


def test_no_decrease_with_workflow_id():
    calc = WorkflowProgressCalculator()
    cfg = _vcf_config()
    steps_high = _rows(header_analysis="completed", pypgx_analysis="running")
    steps_high[1]["output_data"] = {"progress_percent": 80}
    steps_low = _rows(header_analysis="completed", pypgx_analysis="running")
    steps_low[1]["output_data"] = {"progress_percent": 10}
    high = calc.calculate_progress_from_steps(steps_high, cfg, workflow_id="job-58")
    low = calc.calculate_progress_from_steps(steps_low, cfg, workflow_id="job-58")
    assert low.progress_percentage >= high.progress_percentage
