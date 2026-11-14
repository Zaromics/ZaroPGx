"""The PDF rebuilds its own context for the shared template, so a key it does not
forward is not an error -- it is an omission, and the paragraph silently disappears
from the copy a reader keeps.

This has now happened three times: liftover_provenance (found while adding the line
above it), array_provenance (found by diffing the two renderers' keys), and the
gvcf one it was added beside. The failure is always the same word-for-word, so this
asserts the general rule instead of the three instances.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "app" / "reports" / "templates" / "report_template.html"
PDF_GENERATORS = REPO / "app" / "reports" / "pdf_generators.py"


def _provenance_names(template: str) -> set[str]:
    """Every run-provenance line the shared template reads.

    Narrow on purpose: these are the paragraphs that describe what was done to the
    reader's own file, each one rendered only when its key is truthy -- so a missing
    key reads exactly like a run that did not need it.
    """
    referenced = set(re.findall(r"\{\{-?\s*([a-z_][a-z0-9_]*)", template))
    referenced |= set(re.findall(r"\{%-?\s*if\s+([a-z_][a-z0-9_]*)", template))
    return {n for n in referenced if n.endswith(("_provenance", "_methodology"))}


def test_the_pdf_forwards_every_provenance_line_the_template_reads():
    names = _provenance_names(TEMPLATE.read_text(encoding="utf-8"))
    assert names, "the template reads no provenance lines -- this guard has gone blind"

    source = PDF_GENERATORS.read_text(encoding="utf-8")
    missing = sorted(name for name in names if f'"{name}"' not in source)

    assert not missing, (
        "read by report_template.html, never forwarded into the PDF's own context, so "
        "these paragraphs render in the HTML report and vanish from the PDF: "
        + ", ".join(missing)
    )


def test_the_guard_sees_the_names_it_is_meant_to_police():
    """A regex guard that matches nothing passes forever. These four are what the
    template reads today; a rename should fail here and be re-pointed deliberately."""
    assert _provenance_names(TEMPLATE.read_text(encoding="utf-8")) == {
        "array_provenance",
        "gvcf_provenance",
        "liftover_provenance",
        "pharmcat_assume_ref_methodology",
    }
