"""The real report template must render to PDF under the pinned WeasyPrint.

Every other WeasyPrint test stubs the engine (tests/test_report_autoescape.py's
weasyprint_lane), so none could see WeasyPrint 67-70's AssertionError in
weasyprint/layout/page.py on real reports. This drives the real engine and skips
only where WeasyPrint's native libraries (Pango) are absent.
"""

from __future__ import annotations

import pytest

import app.reports.generator as generator

pytestmark = pytest.mark.skipif(
    not generator._HAS_WEASYPRINT, reason="WeasyPrint native libraries unavailable"
)

_CLASSES = [
    "evidence-3",
    "evidence-2",
    "evidence-1",
    "evidence-unspecified",
    "evidence-0",
]


def _recommendations(n: int) -> list[dict]:
    return [
        {
            "drug": f"drug{i:03d}",
            "gene": "CYP2D6",
            "guideline_source": "CPIC",
            "classification": "Strong",
            "recommendation": "Initiate therapy with the recommended starting dose.",
            "evidence_rank": i % 5,
            "evidence_class": _CLASSES[i % 5],
        }
        for i in range(n)
    ]


@pytest.mark.parametrize("n_drugs", [10, 150])
def test_report_template_renders_to_a_real_pdf(tmp_path, n_drugs):
    html = generator.env.get_template("report_template.html").render(
        diplotypes=[],
        recommendations=_recommendations(n_drugs),
        gene_drug_recommendations=[],
        organized_recommendations=[],
        patient_id="test-patient",
        report_id="test-report",
        report_date="2026-10-04",
        organization="ZaroPGx",
        disclaimer="",
    )
    out = tmp_path / "report.pdf"
    # Re-raises WeasyPrint's own exception (after dumping report.txt), so a
    # layout assertion fails this test with WeasyPrint's traceback.
    generator.generate_pdf_from_html(html, str(out))

    data = out.read_bytes()
    assert data.startswith(b"%PDF") and data.rstrip().endswith(b"%%EOF")
    assert not (tmp_path / "report.txt").exists()
