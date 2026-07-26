"""Smoke tests: progress calculator uses shared workflow stage vocabulary."""

from app.services import workflow_stages
from app.services.workflow_progress_calculator import (
    WorkflowProgressCalculator,
    WorkflowStage,
)


def test_calculator_reexports_shared_workflow_stage():
    assert WorkflowStage is workflow_stages.WorkflowStage


def test_calculator_maps_pharmcat_step_via_shared_module():
    calc = WorkflowProgressCalculator()
    assert calc._map_step_name_to_stage("pharmcat_analysis") == WorkflowStage.PHARMCAT
    assert calc._map_step_name_to_stage("workflow_diagram") == WorkflowStage.REPORT


def test_empty_steps_use_soft_upload_stage():
    info = WorkflowProgressCalculator().calculate_progress_from_steps([])
    assert info.stage == WorkflowStage.UPLOAD
    assert info.stage.value == "upload"
