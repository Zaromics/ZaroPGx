"""Unit tests for canonical workflow stage/step vocabulary (Wave 4 / 136)."""

from app.services.workflow_stages import (
    WorkflowStage,
    normalize_step_name,
    parse_stage,
    stage_display_name,
    stage_from_step,
)


def test_canonical_steps_map_to_stages():
    assert stage_from_step("header_analysis") == WorkflowStage.ANALYSIS
    assert stage_from_step("gatk_cram_sam_to_bam") == WorkflowStage.GATK
    assert stage_from_step("gatk_alignment") == WorkflowStage.GATK
    assert stage_from_step("hla_typing") == WorkflowStage.HLA
    assert stage_from_step("pypgx_analysis") == WorkflowStage.PYPGX
    assert stage_from_step("pypgx_bam2vcf") == WorkflowStage.PYPGX
    assert stage_from_step("pharmcat_analysis") == WorkflowStage.PHARMCAT
    assert stage_from_step("diagram_generation") == WorkflowStage.REPORT
    assert stage_from_step("report_generation") == WorkflowStage.REPORT
    assert stage_from_step("completed") == WorkflowStage.COMPLETED


def test_workflow_diagram_alias_maps_to_report():
    assert normalize_step_name("workflow_diagram") == "diagram_generation"
    assert stage_from_step("workflow_diagram") == WorkflowStage.REPORT


def test_unknown_step_defaults_to_analysis():
    assert stage_from_step("not_a_real_step") == WorkflowStage.ANALYSIS


def test_display_names():
    assert stage_display_name(WorkflowStage.HLA) == "OptiType"
    assert stage_display_name(WorkflowStage.PHARMCAT) == "PharmCAT"
    assert stage_display_name(WorkflowStage.COMPLETED) == "Complete"
    assert stage_display_name(WorkflowStage.PYPGX) == "PyPGx"
    assert stage_display_name(WorkflowStage.UPLOAD) == "Upload"
    assert stage_display_name("hla") == "OptiType"


def test_parse_stage_aliases_uploading():
    assert parse_stage("uploading") == WorkflowStage.UPLOAD
    assert parse_stage("upload") == WorkflowStage.UPLOAD
    assert WorkflowStage.UPLOAD.value == "upload"


def test_parse_stage_unknown_falls_back_to_analysis():
    assert parse_stage("nope") == WorkflowStage.ANALYSIS
