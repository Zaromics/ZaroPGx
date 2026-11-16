"""A marker with no reader is a disclosure that does not happen.

Three defects this weekend were the same shape: the report sets a field on a gene row
or a recommendation to say something true about the run, and nothing renders it.

- `tier1_withheld` reached three surfaces of four, so the Executive Summary printed
  verdicts the gate had withheld (#254).
- `call_error` and `not_tested` reached the FHIR export alone, so the page blamed the
  sample for a crashed caller and for a tool that never ran (#265).
- `tier1_no_call` reached nothing at all, so a drug card printed PharmCAT's advice and
  its strength grade over a call the same report declined to make (#266).

Each was found by one question -- who reads this? -- and this asks it of every marker,
every run. A reader may be a template or another module (the FHIR export is a legitimate
reader: a field may be machine-only by design, and this test is about fields read by
nobody at all).
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REPORTS = REPO / "app" / "reports"

#: `row["x"] = ...`, `rec["x"] = ...` and the two other names the report code builds
#: its rows under. Deliberately narrow: this is about per-row disclosure markers, not
#: about every dictionary in the package.
ASSIGNS = re.compile(
    r"""(?:row|rec|gene_entry|entry)\[["']([a-z_][a-z0-9_]*)["']\]\s*="""
)


def _markers() -> dict:
    found = defaultdict(set)
    for path in REPORTS.rglob("*.py"):
        for name in ASSIGNS.findall(path.read_text(encoding="utf-8", errors="replace")):
            found[name].add(str(path.relative_to(REPO)))
    return found


def _haystacks(exclude: set) -> str:
    """Everything that could READ a marker: the templates, and the app's other modules.

    The files that assign the marker are excluded, or each one counts as its own
    reader and the guard passes on a marker nothing else touches -- which is how it
    first passed against the unfixed templates.
    """
    parts = [
        path.read_text(encoding="utf-8", errors="replace")
        for path in (REPORTS / "templates").glob("*.html")
    ]
    parts += [
        path.read_text(encoding="utf-8", errors="replace")
        for path in (REPO / "app").rglob("*.py")
        if str(path.relative_to(REPO)) not in exclude
    ]
    return "\n".join(parts)


def test_every_marker_the_report_sets_is_read_by_something():
    markers = _markers()
    assert markers, "no markers found -- this guard has gone blind"

    unread = {
        name: where
        for name, where in markers.items()
        if not re.search(rf"\b{re.escape(name)}\b", _haystacks(exclude=where))
    }

    assert not unread, (
        "set on a row and read by no template and no other module, so whatever it says "
        "is never said to anyone: "
        + ", ".join(
            f"{name} ({', '.join(sorted(where))})"
            for name, where in sorted(unread.items())
        )
    )


def test_the_guard_sees_a_marker_nothing_reads():
    """The test above passes trivially if the regex stops matching. This feeds it a
    marker name that appears nowhere and requires it to be reported."""
    invented = "zaropgx_marker_nothing_reads"

    assert not re.search(rf"\b{invented}\b", _haystacks(exclude=set()))
    assert ASSIGNS.findall(f'row["{invented}"] = True') == [invented]
