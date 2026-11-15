"""Every tab in the interactive report opens a panel, and every panel has a tab.

The report's own JavaScript switches tabs like this (interactive_report.html):

    function showTab(tabId) {
        ...
        document.getElementById(tabId).classList.add('active');
        document.querySelector(`.tab[onclick="showTab('${tabId}')"]`).classList.add('active');
    }

So a tab button whose panel id does not exist raises a TypeError on `null.classList`
and the click does nothing at all -- no panel opens, no highlight moves, and the only
sign is a console error nobody is watching. The inverse is quieter still: a
`tab-content` panel that no tab names is in the page and out of reach, which is the
report-marker-with-no-reader failure of #254, #265 and #266 in the one surface a
reader navigates by hand.

Neither direction is visible to the rest of the suite. Nothing opens this report in a
browser: `tests/e2e/test_ui_smoke.py` drives the upload page in both themes and
`tests/e2e/report_links.py` fetches every report link and requires a 200 with a body,
but `pgx_report` appears nowhere in the smoke test, so the tabs have never been
clicked by anything. This asks the question statically instead, which needs no browser
and no new dependency.

Measured when this was written: ten tabs -- alerts, summary, genes, drugs, recs,
visualizations, method, export, header, acknowledgements -- and ten panels, matching
exactly in both directions.
"""

from __future__ import annotations

import re

import pytest

#: `onclick="showTab('genes')"` on a tab button.
TAB_CALL = re.compile(r"""onclick="showTab\('([^']+)'\)""")

#: A `tab-content` panel's id, with the attributes in either order.
PANEL_ID_FIRST = re.compile(r'<div[^>]*id="([^"]+)"[^>]*class="tab-content[^"]*"')
PANEL_CLASS_FIRST = re.compile(r'<div[^>]*class="tab-content[^"]*"[^>]*id="([^"]+)"')


def _markup() -> str:
    """The rendered report with its scripts removed.

    The scripts are stripped because `showTab`'s own selector contains the literal
    text ``onclick="showTab('${tabId}')"``, which otherwise reads as an eleventh tab
    whose panel is missing.
    """
    from app.reports.generator import env

    html = env.get_template("interactive_report.html").render(
        diplotypes=[],
        recommendations=[],
        gene_drug_recommendations=[],
        organized_recommendations=[],
        patient_id="test-patient",
        report_id="test-report",
        report_date="2026-11-15",
        organization="ZaroPGx",
        disclaimer="",
    )
    return re.sub(r"<script\b.*?</script>", "", html, flags=re.S)


@pytest.fixture(scope="module")
def markup() -> str:
    return _markup()


def test_every_tab_opens_a_panel_that_exists(markup):
    tabs = set(TAB_CALL.findall(markup))
    assert tabs, "no tab buttons found -- this test has gone blind"
    ids = set(re.findall(r'id="([^"]+)"', markup))

    missing = sorted(tab for tab in tabs if tab not in ids)
    assert not missing, (
        "these tabs call showTab for a panel id the page does not contain, so "
        f"clicking one raises on null.classList and nothing happens: {missing}"
    )


def test_every_panel_has_a_tab_that_opens_it(markup):
    panels = set(PANEL_ID_FIRST.findall(markup)) | set(
        PANEL_CLASS_FIRST.findall(markup)
    )
    assert panels, "no tab-content panels found -- this test has gone blind"
    tabs = set(TAB_CALL.findall(markup))

    unreachable = sorted(panel for panel in panels if panel not in tabs)
    assert not unreachable, (
        "these panels are in the report and no tab opens them, so whatever they "
        f"hold is never seen: {unreachable}"
    )


def test_the_report_still_has_the_tabs_it_had(markup):
    """A tab that disappears takes its panel's content out of reach quietly."""
    expected = {
        "alerts",
        "summary",
        "genes",
        "drugs",
        "recs",
        "visualizations",
        "method",
        "export",
        "header",
        "acknowledgements",
    }
    assert set(TAB_CALL.findall(markup)) == expected
