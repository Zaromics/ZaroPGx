"""Autoescaping across every Environment that renders the two report templates.

Before this, three Environments loaded ``report_template.html`` /
``interactive_report.html`` and only one of them escaped:

* ``generator.env`` (module level)                          -- autoescape OFF
* an inline ``Environment`` inside ``generate_report``       -- autoescape ON
* ``pdf_generators.WeasyPrintGenerator.generate_pdf``        -- autoescape OFF

Both settings were wrong somewhere, and the split was visible in the shipped
artifacts under ``data/reports``:

* The OFF lane wrote ``data-recommendation="{{ rec.recommendation }}"`` raw. Of
  the 151 such attributes in one real interactive report, 45 contain PharmCAT's
  ``<h4 id="other-considerations">`` -- so the browser ends the attribute at that
  inner quote and the rest of the dosing text becomes stray attributes on the
  ``<div>``. ``pgx-report.js`` reads ``dataset.recommendation`` and keyword-matches
  it for "avoid"/"alternative"/"standard", against text that was cut in half.
* The ON lane escaped that same recommendation body in element position, so 20 of
  24 shipped ``_pgx_report.html`` files print literal ``&lt;ul&gt;&lt;li&gt;``
  inside dosing advice.

The fix is both halves: escape everywhere, and mark the one field that is
genuinely markup ``|safe``. Measured over all 24 runs under ``data/reports``,
``recommendations[].recommendation`` is the *only* field carrying tags or
entities -- 751 values with tags, 498 with entities, nothing else anywhere.

Everything here asserts on rendered output.
"""

from __future__ import annotations

import re

import pytest

TEMPLATES = ["report_template.html", "interactive_report.html"]

XSS = "<script>alert('pwned')</script>"

# PharmCAT's own shape: an HTML fragment, quotes and all.
GUIDELINE_HTML = (
    "Prescribe desired starting dose.\n"
    '<h4 id="other-considerations">Other Considerations</h4>\n'
    "<ul><li>recommend 50% of the standard initial dose</li></ul>\n"
    "&quot;Results in higher systemic concentrations.&quot;"
)


def _payload(**overrides):
    data = {
        "patient_id": "test-patient",
        "report_id": "test-report",
        "report_date": "2026-08-08",
        "sample_identifier": "NA12878",
        "organization": "ZaroPGx",
        "disclaimer": "",
        "diplotypes": [
            {
                "gene": "CYP2C9",
                "diplotype": "*2/*3",
                "phenotype": "Poor Metabolizer",
                "activity_score": 0.5,
            }
        ],
        "recommendations": [],
        "gene_drug_recommendations": [],
        "organized_recommendations": [],
    }
    data.update(overrides)
    return data


def _render(template_name, **overrides):
    from app.reports.generator import env

    return env.get_template(template_name).render(**_payload(**overrides))


# ---------------------------------------------------------------------------
# The module Environment: user-influenced values must not reach the page raw
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("template_name", TEMPLATES)
@pytest.mark.parametrize(
    "field", ["gene", "diplotype", "phenotype"], ids=["gene", "diplotype", "phenotype"]
)
def test_gene_table_values_are_escaped(template_name, field):
    """Gene, diplotype and phenotype are matcher output derived from an upload."""
    diplotype = {
        "gene": "CYP2C9",
        "diplotype": "*2/*3",
        "phenotype": "Poor Metabolizer",
        "activity_score": 0.5,
    }
    diplotype[field] = f"{diplotype[field]}{XSS}"
    html = _render(template_name, diplotypes=[diplotype])

    assert (
        XSS not in html
    ), f"{template_name}: raw <script> from d.{field} reached output"
    assert "&lt;script&gt;" in html


@pytest.mark.parametrize("template_name", TEMPLATES)
def test_the_sample_identifier_is_escaped(template_name):
    """The sample id is taken from the uploaded file's header."""
    html = _render(template_name, sample_identifier=f"NA12878{XSS}")
    assert XSS not in html
    assert "&lt;script&gt;" in html


@pytest.mark.parametrize("template_name", TEMPLATES)
def test_drug_names_are_escaped(template_name):
    rec = {
        "drug": f"warfarin{XSS}",
        "gene": "CYP2C9",
        "recommendation": "Standard dosing.",
        "classification": "Strong",
        "evidence_class": "evidence-3",
        "literature_references": [],
    }
    html = _render(
        template_name,
        recommendations=[rec],
        gene_drug_recommendations=[rec],
        organized_recommendations=[
            {
                "drug": rec["drug"],
                "recommendation_groups": {"CYP2C9": {"genes": ["CYP2C9"], "rec": rec}},
            }
        ],
    )
    assert XSS not in html
    assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# ...but the one field that IS markup must still render as markup
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("template_name", TEMPLATES)
def test_pharmcat_guideline_html_still_renders_as_markup(template_name):
    """The |safe half. Escaping this printed &lt;ul&gt; into dosing advice."""
    rec = {
        "drug": "warfarin",
        "gene": "CYP2C9",
        "recommendation": GUIDELINE_HTML,
        "classification": "Strong",
        "evidence_class": "evidence-3",
        "literature_references": [],
    }
    html = _render(
        template_name,
        recommendations=[rec],
        gene_drug_recommendations=[rec],
        organized_recommendations=[
            {
                "drug": "warfarin",
                "recommendation_groups": {"CYP2C9": {"genes": ["CYP2C9"], "rec": rec}},
            }
        ],
    )

    # The visible paragraph only. ``interactive_report.html`` also stashes the
    # same text in a ``data-recommendation`` attribute, where escaping is the
    # correct and required behaviour -- see the round-trip test below.
    body = re.search(r"<strong>Recommendation:</strong>(.*?)</p>", html, re.S)
    assert body, f"{template_name}: no recommendation paragraph rendered"
    body = body.group(1)

    assert "<ul><li>recommend 50% of the standard initial dose</li></ul>" in body
    assert '<h4 id="other-considerations">Other Considerations</h4>' in body
    assert (
        "&lt;ul&gt;" not in body
    ), f"{template_name}: PharmCAT's dosing list rendered as literal &lt;ul&gt; text"
    assert (
        "&amp;quot;" not in body
    ), f"{template_name}: already-escaped guideline text was escaped a second time"


def test_the_data_recommendation_attribute_survives_markup_in_the_value():
    """The bug the OFF lane shipped: the attribute ended at PharmCAT's own quote.

    ``pgx-report.js`` keyword-matches this value, so truncating it silently
    changed how drugs were classified.
    """
    rec = {
        "drug": "warfarin",
        "gene": "CYP2C9",
        "recommendation": GUIDELINE_HTML,
        "classification": "Strong",
        "evidence_class": "evidence-3",
        "literature_references": [],
    }
    html = _render("interactive_report.html", recommendations=[rec])

    values = re.findall(r'data-recommendation="([^"]*)"', html)
    assert len(values) == 1, values
    # Round-trip it the way a browser does before handing it to dataset.
    import html as html_mod

    decoded = html_mod.unescape(values[0])
    assert (
        decoded == GUIDELINE_HTML
    ), "data-recommendation no longer round-trips PharmCAT's recommendation text"


def test_no_template_variable_is_interpolated_inside_a_script_block():
    """Why escaping cannot break the report's inline JavaScript.

    HTML escaping inside a ``<script>`` body would corrupt it -- that is the one
    place ``autoescape`` is actively wrong. Neither template does it, and this
    fails the day one starts.
    """
    from pathlib import Path

    import app.reports.generator as generator

    offenders = []
    for name in TEMPLATES:
        text = Path(generator.TEMPLATE_DIR, name).read_text(encoding="utf-8")
        for match in re.finditer(r"<script\b[^>]*>(.*?)</script>", text, re.S | re.I):
            for var in re.findall(r"\{\{.*?\}\}", match.group(1)):
                offenders.append(f"{name}: {var}")
    assert offenders == [], offenders


# ---------------------------------------------------------------------------
# The WeasyPrint Environment, driven through the real generator
# ---------------------------------------------------------------------------


@pytest.fixture
def weasyprint_lane(monkeypatch, tmp_path):
    """Drive ``WeasyPrintGenerator.generate_pdf`` and hand back the HTML it made.

    WeasyPrint's native libraries are absent outside the container, so the engine
    is stubbed. The Jinja render -- the part under test -- is the real one.
    """
    import app.reports.pdf_generators as pdf_generators

    captured = {}

    class _StubHTML:
        def __init__(self, filename=None, string=None, **_kw):
            from pathlib import Path

            captured["html"] = (
                Path(filename).read_text(encoding="utf-8") if filename else string
            )

        def write_pdf(self, target=None, **_kw):
            from pathlib import Path

            Path(target).write_bytes(b"%PDF-1.4 stub\n%%EOF\n")

    monkeypatch.setattr(pdf_generators, "_HAS_WEASYPRINT", True)
    monkeypatch.setattr(pdf_generators, "HTML", _StubHTML)
    monkeypatch.setattr(pdf_generators, "FontConfiguration", lambda *a, **k: None)

    def _run(template_data):
        ok = pdf_generators.WeasyPrintGenerator().generate_pdf(
            template_data=template_data, output_path=str(tmp_path / "out.pdf")
        )
        assert ok is True
        return captured["html"]

    return _run


def test_weasyprint_renders_the_real_gene_table_not_the_fallback_stub(weasyprint_lane):
    """The WeasyPrint lane silently degraded to a 5-line stub once already.

    ``generate_pdf`` wraps the whole template render in ``except Exception`` and
    falls back to a hardcoded four-line document, so a template that fails to
    load costs a real report and raises nothing. Pin the genes.
    """
    genes = ["CYP2C9", "CYP2C19", "CYP2D6", "SLCO1B1", "TPMT"]
    html = weasyprint_lane(
        {
            "patient_id": "test-patient",
            "report_id": "test-report",
            "sample_identifier": "NA12878",
            "diplotypes": [
                {
                    "gene": g,
                    "diplotype": "*1/*1",
                    "phenotype": "Normal Metabolizer",
                    "activity_score": 2.0,
                }
                for g in genes
            ],
            "recommendations": [],
        }
    )

    assert "No analysis results available" not in html, "fell back to the stub template"
    assert len(html.splitlines()) > 500, f"only {len(html.splitlines())} lines rendered"
    for gene in genes:
        assert re.search(
            rf"<td[^>]*>\s*{gene}\s*</td>", html
        ), f"{gene} missing from the WeasyPrint gene table"


def test_the_weasyprint_environment_escapes_like_the_others(weasyprint_lane):
    """The third Environment. It used to be the only one still unescaped."""
    html = weasyprint_lane(
        {
            "patient_id": "test-patient",
            "report_id": "test-report",
            "sample_identifier": f"NA12878{XSS}",
            "diplotypes": [
                {
                    "gene": f"CYP2C9{XSS}",
                    "diplotype": "*2/*3",
                    "phenotype": "Poor Metabolizer",
                    "activity_score": 0.5,
                }
            ],
            "recommendations": [],
        }
    )
    assert XSS not in html
    assert "&lt;script&gt;" in html


def test_the_weasyprint_lane_keeps_pharmcat_guideline_html(weasyprint_lane):
    """Escaping the third Environment must not cost it the |safe field either."""
    rec = {
        "drug": "warfarin",
        "gene": "CYP2C9",
        "recommendation": GUIDELINE_HTML,
        "classification": "Strong",
        "evidence_class": "evidence-3",
        "literature_references": [],
    }
    html = weasyprint_lane(
        {
            "patient_id": "test-patient",
            "report_id": "test-report",
            "sample_identifier": "NA12878",
            "diplotypes": [],
            "recommendations": [rec],
        }
    )
    body = re.search(r"<strong>Recommendation:</strong>(.*?)</p>", html, re.S)
    assert body, "no recommendation paragraph in the WeasyPrint HTML"
    assert "<ul><li>recommend 50% of the standard initial dose</li></ul>" in body.group(
        1
    )
    assert "&lt;ul&gt;" not in body.group(1)
