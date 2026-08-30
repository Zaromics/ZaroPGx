"""The Genetic Results table must fit the printed page.

The table ran off the right edge of every PDF, taking the Guide column with it --
so the Source Legend explained a column the reader physically could not see, and
half the provenance work from BACKLOG 28/216 was invisible in the format most
people actually read.

Two causes, and the first is why nobody spotted the second:

* ``.narrow-col th`` is a *descendant* selector, but the class sits on the ``<th>``
  itself (``<th class="narrow-col">Call</th>``). It matched nothing, so the
  intended header rotation never applied at all. Corrected to ``th.narrow-col``
  it did apply -- and WeasyPrint rendered ``vertical-rl`` + ``rotate(180deg)``
  upside down rather than bottom-to-top the way browsers do. The rotation is gone
  rather than fixed twice.
* the table was ``table-layout: auto``, so the long Implications strings and the
  "Activity Score" header set a minimum width wider than the page. A browser
  would scroll; WeasyPrint has nowhere to go and simply overflows. Explicit
  column widths summing to 100% under ``table-layout: fixed`` remove the choice.

Widths are pinned as a sum, not individually -- rebalancing them is ordinary
tuning, but a sum over 100 is the bug coming back.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TEMPLATES = {
    "report_template.html": Path(__file__).resolve().parents[1]
    / "app"
    / "reports"
    / "templates"
    / "report_template.html",
    "interactive_report.html": Path(__file__).resolve().parents[1]
    / "app"
    / "reports"
    / "templates"
    / "interactive_report.html",
}

REPORT = TEMPLATES["report_template.html"]


def _source(name: str = "report_template.html") -> str:
    return TEMPLATES[name].read_text(encoding="utf-8")


@pytest.mark.parametrize("name", sorted(TEMPLATES))
def test_no_descendant_narrow_col_selector(name):
    """`.narrow-col th` silently matches nothing; the class is on the th."""
    assert not re.search(r"(?<!\w)\.narrow-col\s+th\s*\{", _source(name)), (
        f"{name} styles `.narrow-col th`, a descendant selector that cannot "
        "match -- the class is on the <th> itself. Use `th.narrow-col`."
    )


def test_the_results_table_has_a_fixed_layout():
    body = re.search(r"\.gene-table\s*\{([^}]*)\}", _source()).group(1)
    assert "table-layout: fixed" in body, (
        "auto layout lets the Implications column set a minimum width wider than "
        "the page, and WeasyPrint overflows rather than scrolling"
    )


def test_the_column_widths_sum_to_one_hundred_percent():
    widths = [
        int(m)
        for m in re.findall(
            r"\.gene-table col\.c-[a-z]+\s*\{\s*width:\s*(\d+)%", _source()
        )
    ]
    assert len(widths) == 7, f"expected 7 column widths, found {widths}"
    assert sum(widths) == 100, f"widths sum to {sum(widths)}%, not 100: {widths}"


def test_every_declared_column_is_used_in_the_markup():
    """A colgroup that does not match the header row silently misaligns."""
    source = _source()
    declared = set(re.findall(r"\.gene-table col\.(c-[a-z]+)", source))
    used = set(re.findall(r'<col class="(c-[a-z]+)"', source))
    assert declared == used, f"declared {declared - used}, used {used - declared}"


def test_the_colgroup_matches_the_header_count():
    source = _source()
    colgroup = re.search(r"<colgroup>(.*?)</colgroup>", source, re.S).group(1)
    cols = len(re.findall(r"<col\b", colgroup))
    header = re.search(r"<thead>\s*<tr>(.*?)</tr>", source, re.S).group(1)
    headers = len(re.findall(r"<th\b", header))
    assert cols == headers, f"{cols} <col> vs {headers} <th> -- columns will skew"


def test_no_rotation_trick_remains_in_the_print_template():
    """WeasyPrint renders the vertical-rl + rotate(180deg) idiom upside down."""
    body = re.search(r"th\.narrow-col\s*\{([^}]*)\}", _source()).group(1)
    assert "writing-mode" not in body, body
    assert "rotate(" not in body, body
