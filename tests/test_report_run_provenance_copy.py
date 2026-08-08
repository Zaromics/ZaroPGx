"""The run-provenance line must not contradict the GRCh37 alert -- in both lanes.

``matcherMetadata.genomeBuild`` is the build PharmCAT's *allele definitions* are
specified on. It is emitted by PharmCAT itself, it is always ``GRCh38.x``, and it
is never the reference build of the uploaded file -- this pipeline does not lift
GRCh37 over, so a GRCh37 upload is matched against GRCh38 definitions and is
flagged provisional for exactly that reason
(``app/api/utils/file_processor.py``: "any results for this file are provisional
and should not be relied on").

The copy shipped before this module read:

    This sample was analyzed against the GRCh38.p14 reference genome build.

which a reader takes as a statement about their upload. So a GRCh37 file got the
"results are provisional" alert on the alerts page and, two pages later, an
apparently confident claim that it had been analysed on GRCh38. Both sentences
were rendered from the same run; only one of them was about the file.

The second half of this module covers the other lane gap: all three provenance
sentences reached ``report_template.html`` only. ``interactive_report.html``
carried none of them, and ``create_interactive_html_report`` never probed for
them, so the same run produced a print report with provenance and an interactive
report without. The sibling evidence-tier work in the same branch mirrored its
legend into both templates byte-identically, so this was an omission rather than
a decision.

These tests render the real templates through the app's own Jinja environment --
source-text assertions have repeatedly proved worthless here -- and pin that the
provenance sentence names *whose* build it is and disclaims the file's.
"""

from __future__ import annotations

import json
import re

import pytest

TEMPLATES = ["report_template.html", "interactive_report.html"]

# The alert file_processor emits for a non-GRCh38 VCF, verbatim.
GRCH37_ALERT = (
    "<p>⚠️ This file is aligned to the GRCh37 reference genome. "
    "ZaroPGx supports GRCh38/hg38 VCF files only, so any results for this file "
    "are provisional and should not be relied on.</p>"
)

BUILD = "GRCh38.p14"


def _render(template_name, **overrides):
    """Render a report template through the app's own Jinja environment."""
    from app.reports.generator import env

    context = {
        "diplotypes": [],
        "recommendations": [],
        "gene_drug_recommendations": [],
        "organized_recommendations": [],
        "patient_id": "test-patient",
        "report_id": "test-report",
        "report_date": "2026-08-08",
        "organization": "ZaroPGx",
        "disclaimer": "",
        "genome_build": BUILD,
        "named_allele_matcher_version": "2.0.0",
        "pharmcat_data_version": "2025-11-05-00-25",
    }
    context.update(overrides)
    return env.get_template(template_name).render(**context)


def _provenance_text(html):
    """The visible text of the ``run-provenance`` paragraph, tags stripped."""
    match = re.search(
        r'<p class="run-provenance">(.*?)</p>', html, re.S | re.I
    )  # one paragraph, all three sentences
    if match is None:
        return None
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", match.group(1))).strip()


@pytest.mark.parametrize("template_name", TEMPLATES)
def test_provenance_paragraph_renders_when_the_run_reported_a_build(template_name):
    text = _provenance_text(_render(template_name))
    assert text is not None, "run-provenance paragraph did not render"
    assert BUILD in text


@pytest.mark.parametrize("template_name", TEMPLATES)
def test_the_sentence_no_longer_claims_the_sample_was_analysed_on_that_build(
    template_name,
):
    """The exact retraction this module exists to prevent."""
    text = _provenance_text(_render(template_name))
    assert "This sample was analyzed against" not in text
    # Nothing of the shape "<the sample> ... analy[sz]ed against ... <build>".
    assert not re.search(
        r"\bsample\b[^.]{0,80}\banaly[sz]ed against\b", text, re.I
    ), f"provenance still reads as a claim about the upload: {text!r}"


@pytest.mark.parametrize("template_name", TEMPLATES)
def test_the_sentence_attributes_the_build_to_pharmcats_definitions(template_name):
    text = _provenance_text(_render(template_name))
    assert "PharmCAT's allele definitions" in text
    assert "not the reference build of the uploaded file" in text


@pytest.mark.parametrize("template_name", TEMPLATES)
def test_the_alert_and_the_provenance_can_both_be_true_on_one_page(template_name):
    """A GRCh37 upload: warning and provenance must coexist without conflict."""
    html = _render(template_name, workflow_warnings=[GRCH37_ALERT])

    assert "are provisional and should not be relied on" in html
    text = _provenance_text(html)
    assert BUILD in text
    # The reader is pointed at the alert rather than left to reconcile the two.
    assert "Alerts and Warnings" in text
    assert "not the reference build of the uploaded file" in text


@pytest.mark.parametrize("template_name", TEMPLATES)
def test_no_genome_sentence_when_the_run_reported_no_build(template_name):
    """v2-shaped reports carry no ``matcherMetadata``; nothing may be invented."""
    text = _provenance_text(_render(template_name, genome_build=None))
    assert text is not None, "the other two sentences should still render"
    assert "allele definitions" not in text
    assert "GRCh" not in text
    assert "Named Allele Matcher v2.0.0" in text


@pytest.mark.parametrize("template_name", TEMPLATES)
def test_whole_paragraph_disappears_when_the_run_reported_nothing(template_name):
    html = _render(
        template_name,
        genome_build=None,
        named_allele_matcher_version=None,
        pharmcat_data_version=None,
    )
    assert _provenance_text(html) is None


@pytest.mark.parametrize("template_name", TEMPLATES)
@pytest.mark.parametrize("build", ["GRCh38.p14", "GRCh38.p13", "GRCh38"])
def test_the_build_string_is_echoed_verbatim_not_normalised(template_name, build):
    text = _provenance_text(_render(template_name, genome_build=build))
    assert build in text


def test_both_lanes_render_the_same_three_sentences():
    """Byte-identical copy, like the evidence legend the sibling stream mirrored."""
    texts = {name: _provenance_text(_render(name)) for name in TEMPLATES}
    printed = texts["report_template.html"]
    interactive = texts["interactive_report.html"]
    assert (
        printed == interactive
    ), f"lanes drifted:\n  print:       {printed!r}\n  interactive: {interactive!r}"
    # Each of the three facts is actually present, not just an empty match.
    for fragment in (
        "PharmCAT's allele definitions",
        "Named Allele Matcher v2.0.0",
        "guideline data version 2025-11-05-00-25",
    ):
        assert fragment in interactive


# ---------------------------------------------------------------------------
# The other half of the lane gap: the interactive generator never probed at all.
# ---------------------------------------------------------------------------


def _write_pharmcat_json(directory, report_id):
    payload = {
        "matcherMetadata": {
            "namedAlleleMatcherVersion": "2.0.0",
            "genomeBuild": BUILD,
        },
        "dataVersion": "2025-11-05-00-25",
    }
    path = directory / f"{report_id}_pgx_pharmcat.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_interactive_generator_resolves_provenance_from_the_report_dir(tmp_path):
    """End-to-end for the interactive lane: run artefact -> rendered sentence."""
    from app.reports.generator import create_interactive_html_report

    report_id = "job-abc"
    _write_pharmcat_json(tmp_path, report_id)
    output_path = tmp_path / f"{report_id}_interactive.html"

    create_interactive_html_report(
        patient_id="patient-1",
        report_id=report_id,
        diplotypes=[],
        recommendations=[],
        output_path=str(output_path),
    )

    text = _provenance_text(output_path.read_text(encoding="utf-8"))
    assert text is not None, "interactive report rendered no provenance paragraph"
    assert BUILD in text
    assert "Named Allele Matcher v2.0.0" in text
    assert "guideline data version 2025-11-05-00-25" in text


def test_interactive_generator_renders_nothing_when_the_run_left_no_json(tmp_path):
    """No PharmCAT JSON must mean no sentences, never a placeholder."""
    from app.reports.generator import create_interactive_html_report

    output_path = tmp_path / "job-none_interactive.html"
    create_interactive_html_report(
        patient_id="patient-1",
        report_id="job-none",
        diplotypes=[],
        recommendations=[],
        output_path=str(output_path),
    )

    html = output_path.read_text(encoding="utf-8")
    assert _provenance_text(html) is None
    # The paragraph itself, not the always-present .run-provenance CSS rule.
    assert '<p class="run-provenance">' not in html
    # The failure mode a bare `{{ genome_build }}` would produce: Jinja renders a
    # None straight through as the literal string "None".
    for stem in (
        "reference assembly",
        "Named Allele Matcher",
        "guideline data version",
    ):
        assert stem not in html, f"a provenance sentence leaked without a value: {stem}"
