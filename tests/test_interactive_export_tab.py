"""The interactive report's Export tab lists the job's files that exist.

It was an "Under Construction" card linking to http://localhost:8090, the HAPI FHIR
server's loopback address: wrong for anyone viewing the report from another machine,
and the server is never written to. The FHIR bundle, PharmCAT's files and the PDF are
written beside the interactive report, so the tab now links to them.
"""

from __future__ import annotations

import inspect
import re

from app.reports import generator
from app.reports.generator import create_interactive_html_report, export_downloads


def _touch(directory, *names):
    for name in names:
        (directory / name).write_text("x", encoding="utf-8")


def test_only_files_on_disk_are_listed_in_a_fixed_order(tmp_path):
    _touch(
        tmp_path,
        "job-1_pgx_report.pdf",
        "job-1_pgx_fhir_report.xml",
        "job-1_pgx_fhir_report.json",
        "job-1_pgx_pharmcat.tsv",
        "job-2_pgx_pharmcat.json",  # another job's file
    )

    downloads = export_downloads(str(tmp_path), "job-1")

    assert [d["href"] for d in downloads] == [
        "job-1_pgx_fhir_report.json",
        "job-1_pgx_fhir_report.xml",
        "job-1_pgx_pharmcat.tsv",
        "job-1_pgx_report.pdf",
    ]
    assert downloads[0] == {
        "label": "FHIR R4 bundle",
        "format": "JSON",
        "href": "job-1_pgx_fhir_report.json",
    }


def _export_tab(html: str) -> str:
    start = html.index('<div id="export" class="tab-content">')
    return html[start : html.index('<div id="header" class="tab-content">', start)]


def _render(tmp_path, downloads):
    output = tmp_path / "job-1_pgx_report_interactive.html"
    create_interactive_html_report(
        patient_id="patient-1",
        report_id="job-1",
        diplotypes=[],
        recommendations=[],
        output_path=str(output),
        downloads=downloads,
    )
    return _export_tab(output.read_text(encoding="utf-8"))


def test_the_tab_links_each_file_relative_to_the_report(tmp_path):
    _touch(tmp_path, "job-1_pgx_fhir_report.json", "job-1_pgx_pharmcat.json")

    tab = _render(tmp_path, export_downloads(str(tmp_path), "job-1"))

    hrefs = re.findall(r'href="([^"]+)"', tab)
    assert hrefs == ["job-1_pgx_fhir_report.json", "job-1_pgx_pharmcat.json"]
    assert "localhost:8090" not in tab
    assert "Under Construction" not in tab


def test_no_files_says_so(tmp_path):
    tab = _render(tmp_path, [])

    assert "No export files were written" in tab
    assert "href=" not in tab


def test_the_interactive_report_is_written_after_the_files_it_lists():
    source = inspect.getsource(generator.generate_report)
    interactive = source.index("create_interactive_html_report(")
    assert source.index("pdf_path = os.path.join(report_dir") < interactive
    assert source.index('pharmcat_tsv_filename = f"{report_id}') < interactive
    assert source.index("fhir_service.save_fhir_export(") < interactive
