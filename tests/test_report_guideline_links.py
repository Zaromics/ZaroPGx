"""Each recommendation links to its guideline at ClinPGx, in both report lanes.

PharmCAT 3.4.0 no longer carries a recommendation's literature ("citations" and
"urls" left its drug entries), so the reports' "References:" line under every
recommendation was empty. It does carry each guideline's own page at ClinPGx
(https://www.clinpgx.org/guidelineAnnotation/PA...), where the guideline's text
and literature are; the database lane stored it and showed it nowhere. Both lanes
now carry it to the PDF, HTML and interactive report.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import app.reports.generator as generator_module

DATA = Path(__file__).resolve().parents[1] / "test_data"
CLINPGX = "https://www.clinpgx.org/guidelineAnnotation/PA166104"


def _pages(monkeypatch, tmp_path):
    for key in list(generator_module.REPORT_CONFIG):
        if key.startswith(("write_", "show_")):
            monkeypatch.setitem(
                generator_module.REPORT_CONFIG,
                key,
                key in ("write_html", "write_interactive_html"),
            )
    report = json.loads(
        (DATA / "pharmcat.example.v340.report.json").read_text(encoding="utf-8")
    )
    result = generator_module.generate_report(
        {"data": report}, str(tmp_path), {"id": "p1"}
    )
    return [
        (tmp_path / result[key].split("/")[-1]).read_text(encoding="utf-8")
        for key in ("html_path", "interactive_html_path")
    ]


def test_the_json_lane_links_each_recommendation_to_its_guideline(
    monkeypatch, tmp_path
):
    for page in _pages(monkeypatch, tmp_path):
        links = re.findall(
            r'<p class="guideline-source"[^>]*><strong>Source:</strong> ([^<]+), '
            r'<a href="([^"]+)"',
            page,
        )
        assert links, "no recommendation names its guideline"
        for name, url in links:
            assert url.startswith("https://www.clinpgx.org/"), url
            assert name.strip()
        assert any(url.startswith(CLINPGX) for _, url in links)


def test_the_database_lane_carries_the_link():
    from app.services.pharmcat_data_service import PharmCATDataService

    rows = [
        {
            "drug_name": "clopidogrel",
            "gene_symbol": "CYP2C19",
            "drug_recommendation": "Use an alternative.",
            "classification": "Strong",
            "guideline_source": "CPIC_GUIDELINE",
            "guideline_name": "Annotation of CPIC Guideline for clopidogrel and CYP2C19",
            "guideline_url": "https://www.clinpgx.org/guidelineAnnotation/PA166104948",
            "recommendation_data": {"lookupKey": [{"CYP2C19": "*1/*2"}]},
        }
    ]
    service = PharmCATDataService.__new__(PharmCATDataService)
    grouped = service._transform_drug_recommendations_for_reports(rows)
    (mapped,) = generator_module.map_recommendations_for_template(grouped)
    assert mapped["guideline_url"] == rows[0]["guideline_url"]
    assert mapped["guideline"] == rows[0]["guideline_name"]


def test_no_link_no_line():
    """A recommendation without a guideline page prints no empty Source line."""
    template = generator_module.env.get_template("report_template.html")
    html = template.render(
        recommendations=[
            {
                "drug": "clopidogrel",
                "gene": "CYP2C19",
                "recommendation": "Use an alternative.",
                "classification": "Strong",
                "guideline_source": "CPIC Guideline Annotation",
            }
        ]
    )
    assert '<p class="guideline-source">' not in html
