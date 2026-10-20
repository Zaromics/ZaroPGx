"""A section that spills a few lines onto a page of their own is pulled back.

A real VCF report's Drugs list ended with a quarter-full page, and Platform and
Citations with a page under half full. Sections like these run to however many
pages they need; they only need to end well. The PDF now tightens such a section
(app/reports/pdf_fit.py) until it ends a page sooner, and prints it as designed
when no step gets it there or the last page is well used anyway.

Each test states its layout before fitting, so a font change that moves it shows
up as a failed precondition, not as a pass that tested nothing.
"""

from __future__ import annotations

import re

import pytest

import app.reports.generator as generator_module
from app.reports import pdf_fit

weasyprint = pytest.importorskip("weasyprint")

ALERT = (
    '<div class="alert-box" style="margin-bottom: 15px; padding: 12px; '
    "border-left: 4px solid #f39c12; background-color: #fffbf0; "
    'border-radius: 4px;">&#9888; Partly covered genes: CYP2B6 (*2, *3, *5) and '
    "SLCO1B1 (*2, *4) could not be assessed; the data did not cover the positions "
    "that define them.</div>"
)


def _html(monkeypatch, tmp_path, drugs=0):
    for key in list(generator_module.REPORT_CONFIG):
        if key.startswith(("write_", "show_")):
            monkeypatch.setitem(
                generator_module.REPORT_CONFIG, key, key == "write_html"
            )
    recommendations = [
        {
            "drug": f"Drug {i:03d}",
            "genes": ["CYP2C19"],
            "recommendations": [
                {
                    "gene": "CYP2C19",
                    "recommendation": "Use as directed.",
                    "classification": "Strong",
                    "guideline": "CPIC",
                }
            ],
        }
        for i in range(drugs)
    ]
    result = generator_module.generate_report(
        {"data": {"genes": [], "drugRecommendations": recommendations}},
        str(tmp_path),
        {"id": "p1"},
    )
    return (tmp_path / result["html_path"].split("/")[-1]).read_text(encoding="utf-8")


def _with_alerts(html, count):
    return re.sub(
        r'(<div class="alerts-container"[^>]*>)(.*?)(</div>\s*</div>)',
        lambda m: m.group(1) + ALERT * count + m.group(3),
        html,
        count=1,
        flags=re.S,
    )


def _layout(monkeypatch, html, sections=None):
    """(fragments per section anchor, steps used) as the PDF lays it out."""
    if sections is not None:
        monkeypatch.setattr(
            generator_module,
            "fit_sections",
            lambda render: pdf_fit.fit_sections(render, sections),
        )
    document, levels = generator_module.render_pdf_document(html)
    return (
        {
            section.anchor: pdf_fit.section_fragments(document, section.anchor)
            for section in pdf_fit.SECTIONS
        },
        levels,
    )


def _untightened(monkeypatch, html):
    with monkeypatch.context() as m:
        m.setattr(generator_module, "fit_sections", lambda render: (render(""), {}))
        fragments, _ = _layout(m, html)
    return fragments


@pytest.mark.parametrize("drugs", [40, 100])
def test_a_drugs_list_ending_a_few_rows_over_ends_a_page_sooner(
    monkeypatch, tmp_path, drugs
):
    html = _html(monkeypatch, tmp_path, drugs)
    before = _untightened(monkeypatch, html)["drugs-heading"]
    assert len(before) >= 2 and before[-1][1] <= 0.3, before
    after, levels = _layout(monkeypatch, html)
    assert len(after["drugs-heading"]) == len(before) - 1
    assert levels["drugs-heading"] >= 1


def test_a_well_used_last_page_is_left_as_designed(monkeypatch, tmp_path):
    html = _html(monkeypatch, tmp_path, 80)
    before = _untightened(monkeypatch, html)["drugs-heading"]
    assert len(before) == 2 and before[-1][1] > 0.5, before
    renders = []

    def counted(render):
        def count(css):
            renders.append(css)
            return render(css)

        return pdf_fit.fit_sections(count)

    monkeypatch.setattr(generator_module, "fit_sections", counted)
    after, levels = _layout(monkeypatch, html)
    assert levels["drugs-heading"] == 0
    # Ends as designed. (It may start a page sooner: the summary before it, all 25
    # rows uncalled here, is fitted to its one page.)
    assert [used for _, used in after["drugs-heading"]] == [used for _, used in before]
    # No step tried for it (Platform and Citations, in the same report, is).
    assert not any("#drugs-heading" in css for css in renders)


def test_a_tail_no_step_can_pull_back_prints_as_designed(monkeypatch, tmp_path):
    html = _html(monkeypatch, tmp_path, 40)
    before = _untightened(monkeypatch, html)["drugs-heading"]
    useless = pdf_fit.FitSection(
        anchor="drugs-heading",
        label="Drugs",
        steps=("/* changes nothing */",),
        max_tail=0.5,
    )
    after, levels = _layout(monkeypatch, html, (useless,))
    assert levels["drugs-heading"] == 0
    assert after["drugs-heading"] == before


def test_alerts_spilling_onto_a_second_page_are_pulled_back(monkeypatch, tmp_path):
    html = _with_alerts(_html(monkeypatch, tmp_path), 12)
    before = _untightened(monkeypatch, html)["alerts-heading"]
    assert len(before) == 2 and before[-1][1] <= 0.3, before
    after, levels = _layout(monkeypatch, html)
    assert len(after["alerts-heading"]) == 1
    assert levels["alerts-heading"] >= 1


def test_an_alert_that_fits_a_page_is_not_split_across_two(monkeypatch, tmp_path):
    """A VCF's "Positions not in the VCF" broke at the foot of page 2, its box
    running on empty to the foot. Wherever the alerts before it end, it moves to
    the next page whole."""
    items = "".join(
        f"<li><strong>GENE{i}</strong>: 2 of 20 positions</li>" for i in range(16)
    )
    tall = ALERT.replace(
        'class="alert-box"', 'id="tall-alert" class="alert-box"', 1
    ).replace("</div>", f"<ul>{items}</ul></div>")
    for count in (5, 7, 9):
        html = re.sub(
            r'(<div class="alerts-container"[^>]*>)(.*?)(</div>\s*</div>)',
            lambda m: m.group(1) + ALERT * count + tall + m.group(3),
            _html(monkeypatch, tmp_path),
            count=1,
            flags=re.S,
        )
        with monkeypatch.context() as m:
            m.setattr(generator_module, "fit_sections", lambda render: (render(""), {}))
            document, _ = generator_module.render_pdf_document(html)
        assert len(pdf_fit.section_pages(document, "tall-alert")) == 1, count


def _job(monkeypatch, file_type, steps, pypgx):
    """A job that ran ``steps``: what Platform and Citations credits follows them."""
    records = [
        {
            "name": step,
            "status": "completed",
            "seconds": 1,
            "output": pypgx if step == "pypgx_analysis" else {},
        }
        for step in steps
    ]
    monkeypatch.setattr(
        generator_module, "load_job_step_records", lambda db, job: records
    )
    monkeypatch.setattr(
        generator_module, "load_job_file_type", lambda db, job: file_type
    )

    # The same layout wherever the suite runs: with a Kroki to reach (a developer's
    # stack) the diagram is drawn, and Kroki and Mermaid are credited, two rows and
    # two references more than without one (CI).
    def no_kroki(*args, **kwargs):
        raise RuntimeError("no Kroki in this test")

    monkeypatch.setattr(generator_module, "render_with_kroki", no_kroki)


def test_platform_and_citations_a_little_over_end_a_page_sooner(monkeypatch, tmp_path):
    """Its content is what the job used, the references and their terms. A VCF
    whose phasing was skipped: half a page over, pulled back."""
    _job(
        monkeypatch, "vcf", ["pypgx_analysis", "pharmcat_analysis"], {"beagle_genes": 0}
    )
    html = _html(monkeypatch, tmp_path)
    before = _untightened(monkeypatch, html)["platform-heading"]
    assert len(before) == 3 and before[-1][1] <= 0.5, before
    after, levels = _layout(monkeypatch, html)
    assert len(after["platform-heading"]) == 2
    assert levels["platform-heading"] >= 1


def test_a_section_still_ending_on_a_sliver_is_pulled_back_again(monkeypatch, tmp_path):
    """A BAM with HLA typing and mtDNA: a tenth of a fifth page. One page fewer
    still ended on a tenth of a page (its terms of use); a tighter step takes off
    another, and the section ends on a well-used page."""
    _job(
        monkeypatch,
        "bam",
        ["pypgx_bam2vcf", "hla_typing", "pypgx_analysis", "mtdna_analysis"],
        {"beagle_genes": 3, "cnv_model_genes": 1},
    )
    html = _html(monkeypatch, tmp_path)
    before = _untightened(monkeypatch, html)["platform-heading"]
    assert len(before) == 5 and before[-1][1] <= 0.2, before
    after, levels = _layout(monkeypatch, html)
    assert len(after["platform-heading"]) == 3
    assert after["platform-heading"][-1][1] > 0.5
    assert levels["platform-heading"] >= 2


def test_platform_and_citations_with_a_well_used_last_page_print_as_designed(
    monkeypatch, tmp_path
):
    """A VCF that PyPGx phased: two thirds of a last page, which is no sparse page."""
    _job(
        monkeypatch, "vcf", ["pypgx_analysis", "pharmcat_analysis"], {"beagle_genes": 3}
    )
    html = _html(monkeypatch, tmp_path)
    before = _untightened(monkeypatch, html)["platform-heading"]
    assert len(before) == 3 and before[-1][1] > 0.5, before
    after, levels = _layout(monkeypatch, html)
    assert len(after["platform-heading"]) == 3
    assert levels["platform-heading"] == 0


def test_tightening_one_section_leaves_the_others_alone(monkeypatch, tmp_path):
    """Drugs pulled back; the summary and Platform measured on their own."""
    alone_dir = tmp_path / "no-drugs"
    alone_dir.mkdir()
    _, alone = _layout(monkeypatch, _html(monkeypatch, alone_dir, 0))
    html = _html(monkeypatch, tmp_path, 40)
    after, levels = _layout(monkeypatch, html)
    # The summary takes the step it takes without the Drugs list at all.
    assert levels["exec-summary"] == alone["exec-summary"]
    assert levels["alerts-heading"] == 0
    assert len(after["exec-summary"]) == 1
