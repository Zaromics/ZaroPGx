"""Fit report sections to their PDF pages by tightening their proportions.

Some sections are laid out to a page by design: the Executive Summary is one page.
How much they hold depends on the sample -- ten genes from a variant-only VCF,
twenty-five from a whole genome -- so no one set of proportions suits every
report: generous spacing that suits ten rows sends a twenty-two-row summary two
rows and its notes onto a second page. So the PDF is laid out, each such section
measured, and one that runs over is laid out again with tighter spacing and
smaller type, a step at a time, until it fits or the steps run out. A report that
fits is laid out once and printed as designed.

Each section starts a page (a forced break before it), so tightening one moves
nothing before it and changes no other section's fit.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Dict, List, Sequence, Tuple

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FitSection:
    """A section the PDF fits to one page.

    ``anchor`` is the id of the section's element. ``steps`` are stylesheets, each
    complete and tighter than the one before; one is applied at a time. They are
    passed to WeasyPrint as user stylesheets, so ``!important`` in them outranks
    the template's own ``!important`` print rules.
    """

    anchor: str
    label: str
    steps: Tuple[str, ...]


_EXEC_SUMMARY_TIGHT = """
#exec-summary h2 { margin-bottom: 12px !important; }
#exec-summary p { margin: 6px 0 !important; }
#exec-summary .exec-summary-table { margin-top: 4px !important; }
#exec-summary .exec-summary-table td { padding: 3px 6px !important; }
#exec-summary .header-note { font-size: 0.85em !important; }
"""

EXEC_SUMMARY = FitSection(
    anchor="exec-summary",
    label="Executive Summary",
    steps=(
        _EXEC_SUMMARY_TIGHT,
        _EXEC_SUMMARY_TIGHT + """
#exec-summary > p, #exec-summary .summary-highlights > p { font-size: 0.9em !important; }
#exec-summary .exec-summary-table { font-size: 14px !important; }
#exec-summary .exec-summary-table td { padding: 2px 5px !important; }
#exec-summary .exec-summary-diplotype.long-diplotype { font-size: 12px !important; }
#exec-summary .gene-phenotype { padding: 1px 8px !important; }
#exec-summary .header-note { font-size: 0.8em !important; margin: 0 0 4px 0 !important; }
""",
        _EXEC_SUMMARY_TIGHT + """
#exec-summary h2 { font-size: 1.3em !important; margin-bottom: 8px !important; }
#exec-summary > p, #exec-summary .summary-highlights > p { font-size: 0.82em !important; margin: 4px 0 !important; }
#exec-summary .exec-summary-table { font-size: 12.5px !important; line-height: 1.2 !important; }
#exec-summary .exec-summary-table td { padding: 1px 4px !important; }
#exec-summary .exec-summary-diplotype.long-diplotype { font-size: 11px !important; }
#exec-summary .gene-phenotype { padding: 0 7px !important; }
#exec-summary .as-badge svg { width: 12px !important; height: 12px !important; }
#exec-summary .header-note { font-size: 0.75em !important; margin: 0 0 3px 0 !important; }
""",
    ),
)

SECTIONS: Tuple[FitSection, ...] = (EXEC_SUMMARY,)


def section_pages(document, anchor: str) -> List[int]:
    """The indices of the pages the element with this id is laid out on."""
    return [i for i, page in enumerate(document.pages) if anchor in page.anchors]


def fit_sections(
    render: Callable[[str], object], sections: Sequence[FitSection] = SECTIONS
) -> Tuple[object, Dict[str, int]]:
    """Lay the document out, tightening each section that runs over its page.

    ``render(css)`` lays the document out with ``css`` added and returns the
    WeasyPrint Document. Returns the Document and, per section, the step used
    (0: none). A section still over its page after the last step keeps the last
    step, and says so in the log.
    """
    levels = {section.anchor: 0 for section in sections}
    document = render("")
    while True:
        over = [
            section
            for section in sections
            if len(section_pages(document, section.anchor)) > 1
            and levels[section.anchor] < len(section.steps)
        ]
        if not over:
            break
        for section in over:
            levels[section.anchor] += 1
        document = render(
            "\n".join(
                section.steps[levels[section.anchor] - 1]
                for section in sections
                if levels[section.anchor]
            )
        )
    for section in sections:
        pages = len(section_pages(document, section.anchor))
        if levels[section.anchor]:
            logger.info(
                "PDF: %s tightened, step %d of %d",
                section.label,
                levels[section.anchor],
                len(section.steps),
            )
        if pages > 1:
            logger.warning(
                "PDF: %s still runs to %d pages at its tightest", section.label, pages
            )
    return document, levels
