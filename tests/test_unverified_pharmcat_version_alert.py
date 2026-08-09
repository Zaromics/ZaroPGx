"""An unverified PharmCAT version has to reach the reader, not just the log.

265's version gate is deliberately toothless on its own: an unknown
``pharmcatVersion`` produces a *warning* and never blocks, because hard-failing
on an upstream point release would turn a harmless bump into a clinical outage
after the expensive preprocessing already succeeded.  The structure checks are
what carry the teeth.

The consequence of that choice is that somebody has to be told.  The warning went
to the application log, where the operator sees it and the clinician holding the
report does not -- and "these calls came out of a PharmCAT release this pipeline
has never been checked against" is precisely the sort of qualification the
report's own Alerts and Warnings page exists to carry.

These tests drive the real ``generate_report`` and read the HTML it writes; the
alert is asserted as rendered text on the page, not as a string in the source.
"""

from __future__ import annotations

import html as html_module
import json
import re

import pytest

import app.reports.generator as generator_module
from app.pharmcat.report_json import SUPPORTED_VERSION_SERIES

SUPPORTED_VERSION = "{}.{}.0".format(*sorted(SUPPORTED_VERSION_SERIES)[-1])
UNVERIFIED_VERSION = "9.9.1"

GENE_BLOCK = {
    "gene": "CYP2C19",
    "diplotype": "*1/*2",
    "phenotype": "Intermediate Metabolizer",
}


def _render_report(monkeypatch, tmp_path, payload_extra):
    """Run generate_report with only the HTML lane on; return the rendered page."""
    for key in (
        "write_pdf",
        "write_interactive_html",
        "write_json",
        "write_tsv",
        "write_workflow_svg",
        "write_workflow_png",
        "show_pharmcat_html_report",
        "show_pharmcat_json_report",
        "show_pharmcat_tsv_report",
    ):
        monkeypatch.setitem(generator_module.REPORT_CONFIG, key, False)
    monkeypatch.setitem(generator_module.REPORT_CONFIG, "write_html", True)

    payload = {"genes": [GENE_BLOCK], "drugRecommendations": []}
    payload.update(payload_extra)

    result = generator_module.generate_report(
        {"data": payload}, str(tmp_path), {"id": "p1"}, job_id=None
    )
    html_path = result.get("html_path") or result.get("html_report_path")
    assert html_path, f"no HTML report was written: {sorted(result)}"
    return (tmp_path / html_path.split("/")[-1]).read_text(encoding="utf-8")


def _alerts_section(page):
    """The rendered markup of the Alerts and Warnings section.

    Sliced from its own heading to the start of the next section, so a match
    proves the text is on *that page section* rather than anywhere in the file.
    """
    match = re.search(r"<h2>\s*Alerts and Warnings\s*</h2>", page)
    assert match, "the report has no Alerts and Warnings section"
    rest = page[match.end() :]
    end = re.search(r"<h2[ >]", rest)
    return rest[: end.start()] if end else rest


def _text_of(markup):
    """Tag-stripped, entity-decoded text, for assertions about what is *read*."""
    return html_module.unescape(re.sub(r"<[^>]+>", " ", markup))


# ---------------------------------------------------------------------------
# The helper's decision
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"pharmcatVersion": SUPPORTED_VERSION},
        # No version key at all: the TSV fallback's shape, and already-normalised
        # data. Neither is making a claim about a PharmCAT release, so alerting
        # would put a banner on every TSV-rescued run.
        {},
        {"genes": {}},
        None,
        "not a mapping",
    ],
)
def test_no_alert_when_there_is_nothing_to_say(payload):
    assert generator_module.unverified_pharmcat_version_alert(payload) is None
    assert generator_module.unverified_pharmcat_version_alert({"data": payload}) is None


@pytest.mark.parametrize(
    "version",
    [
        UNVERIFIED_VERSION,
        "1.0.0",
        "v99.0",
        "not-a-version",
        "",
        3.4,  # a JSON number is not the release string "3.4.0"
        None,
    ],
)
def test_an_unverified_version_produces_an_alert(version):
    alert = generator_module.unverified_pharmcat_version_alert(
        {"pharmcatVersion": version}
    )
    assert alert, f"{version!r} was treated as a verified release"
    assert "Unverified PharmCAT version" in alert


@pytest.mark.parametrize("version", [None, "", "   "])
def test_a_version_field_that_says_nothing_is_not_rendered_as_a_version(version):
    """``str(None)`` is ``"None"`` and is truthy, so an ``or`` fallback never
    fires -- the report used to tell the reader the release was called None."""
    alert = generator_module.unverified_pharmcat_version_alert(
        {"pharmcatVersion": version}
    )

    assert alert
    assert "None" not in alert, alert
    assert "does not say which release" in alert, alert
    assert "produced by PharmCAT <strong>" not in alert, alert


def test_every_supported_series_is_silent():
    """The alert must not fire on the versions this pipeline actually runs."""
    for major, minor in SUPPORTED_VERSION_SERIES:
        payload = {"pharmcatVersion": f"{major}.{minor}.7"}
        assert (
            generator_module.unverified_pharmcat_version_alert(payload) is None
        ), f"{major}.{minor} is a supported series but was flagged unverified"


def test_the_version_string_is_escaped():
    """The alert is rendered with ``|safe``, so its interpolations must be inert."""
    alert = generator_module.unverified_pharmcat_version_alert(
        {"pharmcatVersion": "<script>alert(1)</script>"}
    )
    assert "<script>" not in alert
    assert "&lt;script&gt;" in alert


def test_the_wrapped_and_bare_payload_shapes_agree():
    """upload_router hands generate_report ``{"data": <report.json>}``."""
    bare = {"pharmcatVersion": UNVERIFIED_VERSION}
    assert generator_module.unverified_pharmcat_version_alert(
        bare
    ) == generator_module.unverified_pharmcat_version_alert({"data": bare})


# ---------------------------------------------------------------------------
# What the reader actually sees
# ---------------------------------------------------------------------------


def test_the_alert_is_rendered_on_the_report(monkeypatch, tmp_path):
    page = _render_report(
        monkeypatch, tmp_path, {"pharmcatVersion": UNVERIFIED_VERSION}
    )
    alerts = _text_of(_alerts_section(page))

    assert "Unverified PharmCAT version" in alerts, alerts
    assert UNVERIFIED_VERSION in alerts, alerts
    # And the section no longer claims there is nothing to report.
    assert "No alerts or warnings" not in alerts, alerts


def test_a_verified_version_leaves_the_page_alone(monkeypatch, tmp_path):
    page = _render_report(monkeypatch, tmp_path, {"pharmcatVersion": SUPPORTED_VERSION})
    alerts = _text_of(_alerts_section(page))

    assert "Unverified PharmCAT version" not in alerts, alerts
    assert "No alerts or warnings" in alerts, alerts


def test_a_payload_with_no_version_leaves_the_page_alone(monkeypatch, tmp_path):
    """The TSV-fallback shape must not acquire a banner it has no basis for."""
    page = _render_report(monkeypatch, tmp_path, {})

    assert "Unverified PharmCAT version" not in _text_of(_alerts_section(page))


def test_the_alert_renders_as_markup_not_as_escaped_source(monkeypatch, tmp_path):
    """The warnings loop uses ``|safe``; a literal ``&lt;p&gt;`` on the page would
    mean the alert was escaped between here and the template."""
    section = _alerts_section(
        _render_report(monkeypatch, tmp_path, {"pharmcatVersion": UNVERIFIED_VERSION})
    )

    assert "&lt;p&gt;" not in section
    assert "<strong>Unverified PharmCAT version</strong>" in section


def test_a_hostile_version_string_cannot_inject_markup(monkeypatch, tmp_path):
    """``pharmcatVersion`` comes out of a file this pipeline did not write, and
    the alert is rendered ``|safe``, so the escaping has to happen here."""
    section = _alerts_section(
        _render_report(
            monkeypatch, tmp_path, {"pharmcatVersion": "<script>alert('x')</script>"}
        )
    )

    assert "<script>" not in section, section
    # It is still shown to the reader -- as text.
    assert "<script>alert('x')</script>" in _text_of(section)


def test_the_interactive_report_carries_it_too(monkeypatch, tmp_path):
    """Two artifacts are handed to the reader, and the alert belongs on both."""
    for key in (
        "write_pdf",
        "write_html",
        "write_json",
        "write_tsv",
        "write_workflow_svg",
        "write_workflow_png",
        "show_pharmcat_html_report",
        "show_pharmcat_json_report",
        "show_pharmcat_tsv_report",
    ):
        monkeypatch.setitem(generator_module.REPORT_CONFIG, key, False)
    monkeypatch.setitem(generator_module.REPORT_CONFIG, "write_interactive_html", True)

    result = generator_module.generate_report(
        {
            "data": {
                "pharmcatVersion": UNVERIFIED_VERSION,
                "genes": [GENE_BLOCK],
                "drugRecommendations": [],
            }
        },
        str(tmp_path),
        {"id": "p1"},
        job_id=None,
    )
    path = result.get("interactive_html_path")
    assert path, f"no interactive report was written: {sorted(result)}"
    page = (tmp_path / path.split("/")[-1]).read_text(encoding="utf-8")

    assert "Unverified PharmCAT version" in _text_of(
        page
    ), "the interactive report's Alerts tab does not carry the version alert"
    assert UNVERIFIED_VERSION in _text_of(page)


GRCH37_ALERT = (
    "<p>This file is aligned to the GRCh37 reference genome, so any results "
    "for it are provisional.</p>"
)


def test_the_jobs_own_warnings_survive_alongside_the_alert(
    monkeypatch, tmp_path, db_session
):
    """The alert is appended, never a replacement.

    Overwriting ``workflow_warnings`` would delete the GRCh37 provisional alert,
    which is the one warning that page carries today. ``generate_report`` reads
    those warnings off the Job row, so this drives the real read.
    """
    from app.api.db import Job
    from app.api.models import JobCreate
    from app.services.job_service import JobService

    job = JobService(db_session).create_job(
        JobCreate(workflow_type="genomic_analysis", name="version-alert-probe")
    )
    db_session.commit()
    row = db_session.query(Job).filter(Job.id == job.id).first()
    row.job_metadata = {"workflow": {"warnings": [GRCH37_ALERT]}}
    db_session.commit()

    for key in (
        "write_pdf",
        "write_interactive_html",
        "write_json",
        "write_tsv",
        "write_workflow_svg",
        "write_workflow_png",
        "show_pharmcat_html_report",
        "show_pharmcat_json_report",
        "show_pharmcat_tsv_report",
    ):
        monkeypatch.setitem(generator_module.REPORT_CONFIG, key, False)
    monkeypatch.setitem(generator_module.REPORT_CONFIG, "write_html", True)

    result = generator_module.generate_report(
        {
            "data": {
                "pharmcatVersion": UNVERIFIED_VERSION,
                "genes": [GENE_BLOCK],
                "drugRecommendations": [],
            }
        },
        str(tmp_path),
        {"id": "p1"},
        job_id=str(job.id),
        db_session=db_session,
    )
    page = (tmp_path / result["html_path"].split("/")[-1]).read_text(encoding="utf-8")
    alerts = _text_of(_alerts_section(page))

    assert "GRCh37 reference genome" in alerts, alerts
    assert "Unverified PharmCAT version" in alerts, alerts

    # And the Job's stored metadata was not edited on the way past.
    db_session.expire_all()
    stored = (
        db_session.query(Job).filter(Job.id == job.id).first().job_metadata["workflow"]
    )
    assert stored["warnings"] == [GRCH37_ALERT], stored


def test_the_alert_changes_nothing_about_the_analysis(monkeypatch, tmp_path):
    """Purely additive: it is report copy, not a change to what was called."""
    for key in (
        "write_pdf",
        "write_html",
        "write_interactive_html",
        "write_json",
        "write_tsv",
        "write_workflow_svg",
        "write_workflow_png",
        "show_pharmcat_html_report",
        "show_pharmcat_json_report",
        "show_pharmcat_tsv_report",
    ):
        monkeypatch.setitem(generator_module.REPORT_CONFIG, key, False)

    def _processed(version):
        payload = {"genes": [GENE_BLOCK], "drugRecommendations": []}
        if version is not None:
            payload["pharmcatVersion"] = version
        result = generator_module.generate_report(
            {"data": payload}, str(tmp_path), {"id": "p1"}, job_id=None
        )
        return json.dumps(
            result["processed_data"]["genes"], sort_keys=True, default=str
        )

    assert _processed(UNVERIFIED_VERSION) == _processed(SUPPORTED_VERSION)
    assert _processed(UNVERIFIED_VERSION) == _processed(None)
