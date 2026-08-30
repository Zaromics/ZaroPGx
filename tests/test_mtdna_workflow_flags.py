"""needs_mtdna is finally assigned, and its step is registered."""

from app.api.models import WorkflowOptions
from app.services.workflow_registry import GENOMIC_ANALYSIS, resolve_steps


def test_the_option_is_declared():
    assert "needs_mtdna" in GENOMIC_ANALYSIS.option_fields


def test_the_step_is_registered_or_the_ui_hangs():
    """A step name main.nf posts but no template mints 404s and shows [pending]."""
    names = [t.step_name for t in GENOMIC_ANALYSIS.step_templates]
    assert "mtdna_analysis" in names


def test_the_step_is_skipped_when_not_needed():
    steps = resolve_steps("genomic_analysis", WorkflowOptions(needs_mtdna=False))
    assert "mtdna_analysis" not in [s.step_name for s in steps]


def test_the_step_runs_before_pharmcat():
    """Its outside call has to exist before PharmCAT reads combined_outside.tsv."""
    names = [t.step_name for t in GENOMIC_ANALYSIS.step_templates]
    assert names.index("mtdna_analysis") < names.index("pharmcat_analysis")
