"""The rare-variant alert is shown once, not once per lane that can add it.

``generate_report`` puts the alert into the ``workflow_warnings`` it hands to
``create_interactive_html_report``; the re-render lanes (``app/main.py``,
``upload_router``) read their warnings from the job's metadata, which has none.
The interactive renderer therefore adds the alert only when it is not already
there. Before that check it printed twice on the page and once in the PDF.
"""

import re

from app.reports import rarity
from app.reports.generator import create_interactive_html_report

# One call resting on a variant almost nobody carries: what rarity.alert() needs.
RARITY = {
    "status": "done",
    "shown": 1,
    "total": 1,
    "rare": [{"gene": "CYP1A2", "allele": "*1K", "max_af": 0.0068, "group": "mid"}],
}


def _render(tmp_path, workflow_warnings):
    output = tmp_path / "job-1_pgx_report_interactive.html"
    create_interactive_html_report(
        patient_id="patient-1",
        report_id="job-1",
        diplotypes=[],
        recommendations=[],
        output_path=str(output),
        workflow_warnings=workflow_warnings,
        rarity_context=RARITY,
    )
    return output.read_text(encoding="utf-8")


def test_the_alert_is_not_repeated_when_the_caller_already_supplied_it(tmp_path):
    """generate_report's lane: the alert arrives in workflow_warnings."""
    alert = rarity.alert(RARITY)
    assert alert, "the fixture must produce an alert for this test to mean anything"

    html = _render(tmp_path, [alert])

    assert html.count("A call rests on a rare variant") == 1


def test_the_alert_is_added_when_the_caller_has_none(tmp_path):
    """The re-render lanes: warnings come from job metadata, without the alert."""
    html = _render(tmp_path, [])

    assert html.count("A call rests on a rare variant") == 1


def test_a_warning_the_caller_supplied_is_kept_beside_it(tmp_path):
    """Adding the alert must not drop what was already in the channel."""
    html = _render(tmp_path, ["<p>⚠️ <strong>Structural alleles:</strong> x</p>"])

    assert "Structural alleles" in html
    assert html.count("A call rests on a rare variant") == 1


def test_no_alert_leaves_the_caller_s_warnings_untouched(tmp_path):
    """Nothing rare: the channel is exactly what was passed in."""
    passed = "<p>⚠️ <strong>Structural alleles:</strong> x</p>"
    output = tmp_path / "job-1_pgx_report_interactive.html"
    create_interactive_html_report(
        patient_id="patient-1",
        report_id="job-1",
        diplotypes=[],
        recommendations=[],
        output_path=str(output),
        workflow_warnings=[passed],
        rarity_context={"status": "done", "shown": 0, "total": 0, "rare": []},
    )
    html = output.read_text(encoding="utf-8")

    assert "Structural alleles" in html
    assert "rests on a rare variant" not in html


def test_the_renderer_does_not_mutate_the_list_it_was_given(tmp_path):
    """generate_report reuses its own list after this call; appending in place
    would put the alert in twice on a second render."""
    given = []
    _render(tmp_path, given)

    assert given == []
