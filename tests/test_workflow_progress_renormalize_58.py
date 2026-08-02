"""58/102 — progress skip renormalization."""

from app.services.workflow_progress_calculator import WorkflowProgressCalculator


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


def test_planned_steps_vcf_omits_gatk_hla_bam2vcf():
    calc = WorkflowProgressCalculator()
    planned = calc._planned_steps_from_config(_vcf_config())
    assert "gatk_cram_sam_to_bam" not in planned
    assert "gatk_alignment" not in planned
    assert "hla_typing" not in planned
    assert "pypgx_bam2vcf" not in planned
    assert planned == [
        "header_analysis",
        "pypgx_analysis",
        "pharmcat_analysis",
        "diagram_generation",
        "report_generation",
    ]


def test_renormalized_ranges_vcf_cover_0_to_100_no_gaps():
    calc = WorkflowProgressCalculator()
    active = calc._planned_steps_from_config(_vcf_config())
    ranges = calc._renormalized_ranges(active)
    assert ranges["header_analysis"] == (0, 15)
    assert ranges["pypgx_analysis"] == (16, 43)
    assert ranges["pharmcat_analysis"] == (44, 71)
    assert ranges["diagram_generation"] == (72, 79)
    assert ranges["report_generation"] == (80, 100)
    # Contiguous / full cover
    assert ranges["header_analysis"][0] == 0
    assert ranges["report_generation"][1] == 100
    ordered = list(ranges.values())
    for i in range(len(ordered) - 1):
        assert ordered[i][1] + 1 == ordered[i + 1][0]


def test_vcf_header_complete_progress_is_end_of_first_range():
    calc = WorkflowProgressCalculator()
    steps = [
        {"step_name": "header_analysis", "status": "completed", "step_order": 1},
    ]
    info = calc.calculate_progress_from_steps(steps, _vcf_config())
    assert info.progress_percentage == 15


def test_vcf_pypgx_running_50_maps_to_mid_range():
    calc = WorkflowProgressCalculator()
    steps = [
        {"step_name": "header_analysis", "status": "completed", "step_order": 1},
        {
            "step_name": "pypgx_analysis",
            "status": "running",
            "step_order": 2,
            "output_data": {"progress_percent": 50},
        },
    ]
    info = calc.calculate_progress_from_steps(steps, _vcf_config())
    # range 16-43 width 28; 50% -> 16 + 14 = 30
    assert info.progress_percentage == 30


def test_vcf_complete_is_100():
    calc = WorkflowProgressCalculator()
    steps = [
        {"step_name": "header_analysis", "status": "completed", "step_order": 1},
        {"step_name": "pypgx_analysis", "status": "completed", "step_order": 2},
        {"step_name": "pharmcat_analysis", "status": "completed", "step_order": 3},
        {"step_name": "diagram_generation", "status": "completed", "step_order": 4},
        {"step_name": "report_generation", "status": "completed", "step_order": 5},
    ]
    info = calc.calculate_progress_from_steps(steps, _vcf_config())
    assert info.progress_percentage == 100


def test_bam_includes_bam2vcf_not_alignment():
    calc = WorkflowProgressCalculator()
    cfg = {
        "needs_gatk": False,
        "needs_hla": False,
        "needs_pypgx": True,
        "needs_pypgx_bam2vcf": True,
        "file_analysis": {"file_type": "bam"},
    }
    planned = calc._planned_steps_from_config(cfg)
    assert "gatk_alignment" not in planned
    assert "pypgx_bam2vcf" in planned
    assert planned.index("pypgx_analysis") < planned.index("pypgx_bam2vcf")


def test_fastq_includes_gatk_alignment_when_needs_gatk():
    calc = WorkflowProgressCalculator()
    cfg = {
        "needs_gatk": True,
        "needs_hla": True,
        "needs_pypgx": True,
        "file_analysis": {"file_type": "fastq"},
    }
    planned = calc._planned_steps_from_config(cfg)
    assert "gatk_alignment" in planned
    assert "hla_typing" in planned
    assert "pypgx_bam2vcf" in planned  # default for non-vcf
    ranges = calc._renormalized_ranges(planned)
    assert ranges["report_generation"][1] == 100
    # Heavier base-weight steps get wider or equal ranges than lighter ones
    w = lambda a, b: b - a + 1
    assert w(*ranges["pypgx_analysis"]) >= w(*ranges["header_analysis"])


def test_no_decrease_with_workflow_id():
    calc = WorkflowProgressCalculator()
    cfg = _vcf_config()
    steps_high = [
        {"step_name": "header_analysis", "status": "completed", "step_order": 1},
        {
            "step_name": "pypgx_analysis",
            "status": "running",
            "step_order": 2,
            "output_data": {"progress_percent": 80},
        },
    ]
    steps_low = [
        {"step_name": "header_analysis", "status": "completed", "step_order": 1},
        {
            "step_name": "pypgx_analysis",
            "status": "running",
            "step_order": 2,
            "output_data": {"progress_percent": 10},
        },
    ]
    high = calc.calculate_progress_from_steps(steps_high, cfg, workflow_id="job-58")
    low = calc.calculate_progress_from_steps(steps_low, cfg, workflow_id="job-58")
    assert low.progress_percentage >= high.progress_percentage
