"""No report prints the template's debug context.

Every HTML report ended with a "debug veneer" (marked "remove in production" in
the template): a JSON dump of what it was rendered from, with internal ids and
Python reprs of the patient's first diplotype and recommendation. It is logged at
DEBUG now, and no template has the block.
"""

from __future__ import annotations

import logging
from pathlib import Path

import app.reports.generator as generator_module

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "reports" / "templates"
DUMP_KEYS = ("template_data_keys", "footer_context", "workflow_assets")


def test_reports_carry_no_debug_dump(monkeypatch, tmp_path, caplog):
    for key in list(generator_module.REPORT_CONFIG):
        if key.startswith(("write_", "show_")):
            monkeypatch.setitem(
                generator_module.REPORT_CONFIG,
                key,
                key in ("write_html", "write_interactive_html"),
            )
    with caplog.at_level(logging.DEBUG, logger=generator_module.logger.name):
        result = generator_module.generate_report(
            {"data": {"genes": [], "drugRecommendations": []}},
            str(tmp_path),
            {"id": "p1"},
        )
    for key in ("html_path", "interactive_html_path"):
        page = (tmp_path / result[key].split("/")[-1]).read_text(encoding="utf-8")
        assert not [k for k in DUMP_KEYS if k in page], key
    # Still there for troubleshooting, in the log.
    assert any("template_data_keys" in r.getMessage() for r in caplog.records)


def test_no_template_has_the_debug_block():
    for template in TEMPLATES.glob("*.html"):
        assert "debug_json" not in template.read_text(encoding="utf-8"), template.name
