"""The mtDNA toggle and downloads exist, and the report count stops lying."""

import re
from pathlib import Path

INDEX = Path(__file__).resolve().parent.parent / "app/templates/index.html"


def _html() -> str:
    return INDEX.read_text(encoding="utf-8")


def test_there_is_an_mtdna_toggle():
    assert 'data-flag="mtdna_enabled"' in _html() or "mtdnaEnabled" in _html()


def test_there_is_an_mtdna_download_group():
    assert "mtDNA Reports" in _html()


def test_the_report_count_is_not_hardcoded():
    """'5 reports are available' was already wrong the moment a group was added."""
    assert not re.search(r"\b5 reports are available", _html())
