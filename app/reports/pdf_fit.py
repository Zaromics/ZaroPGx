"""Fit report sections to their PDF pages by tightening their proportions.

Some sections are laid out to a page by design: the Executive Summary is one page.
How much they hold depends on the sample -- ten genes from a variant-only VCF,
twenty-five from a whole genome -- so no one set of proportions suits every
report: generous spacing that suits ten rows sends a twenty-two-row summary two
rows and its notes onto a second page. So the PDF is laid out, each such section
measured, and one that runs over is laid out again with tighter spacing and
smaller type, a step at a time, until it fits or the steps run out. A report that
fits is laid out once and printed as designed.

Other sections run to several pages, and only need to end well: one that spills
a few lines onto a page of their own (the Drugs list, Platform and Citations) is
tightened the same way to end a page sooner, and printed as designed if no step
gets it there.

Each section is measured by its own boxes, wherever it starts, so tightening one
changes no other section's fit.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FitSection:
    """A section the PDF fits to its pages.

    ``anchor`` is the id of the section's element, or of its heading; with
    ``heading`` set, the heading is found by its text and given that id when the
    PDF is laid out (``mark_headings``). ``steps`` are stylesheets, each complete and tighter than the one before; one is
    applied at a time. They are passed to WeasyPrint as user stylesheets, so
    ``!important`` in them outranks the template's own ``!important`` print rules.

    ``max_tail`` ``None``: the section must be one page. Otherwise a section whose
    last page it fills to this fraction or less is pulled back onto one page
    fewer, if a step can do it.
    """

    anchor: str
    label: str
    steps: Tuple[str, ...]
    max_tail: Optional[float] = None
    heading: Optional[str] = None


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

# The sections below are found by their <h2>'s text, which mark_headings gives
# the anchor as its id, and styled through it. The template is left as it is:
# these sections' lines are moved by other changes to the report.
_ALERTS = ".section:has(> #alerts-heading)"
ALERTS = FitSection(
    anchor="alerts-heading",
    label="Alerts and Warnings",
    heading="Alerts and Warnings",
    max_tail=0.5,
    steps=(
        f"""
{_ALERTS} .alerts-container {{ margin-top: 10px !important; }}
{_ALERTS} .alert-box {{ margin-bottom: 8px !important; padding: 6px 10px !important; }}
{_ALERTS} .alert-box p {{ margin: 2px 0 !important; }}
""",
        f"""
{_ALERTS} > p {{ font-size: 0.9em !important; }}
{_ALERTS} .alerts-container {{ margin-top: 8px !important; }}
{_ALERTS} .alert-box {{ margin-bottom: 6px !important; padding: 4px 10px !important; font-size: 0.88em !important; }}
{_ALERTS} .alert-box p {{ margin: 1px 0 !important; }}
""",
    ),
)

_DRUGS = ".section:has(> #drugs-heading)"
DRUGS = FitSection(
    anchor="drugs-heading",
    label="Drugs",
    heading="Drugs",
    max_tail=0.5,
    steps=(
        f"""
{_DRUGS} .evidence-legend {{ padding: 6px 10px !important; margin-bottom: 8px !important; font-size: 0.85em !important; }}
{_DRUGS} .evidence-legend p {{ margin: 0 0 4px 0 !important; }}
{_DRUGS} .legend-item {{ margin-bottom: 2px !important; }}
{_DRUGS} .drugs-grid {{ gap: 3px 10px !important; margin: 8px 0 !important; }}
{_DRUGS} .drug-item {{ padding: 3px 8px !important; }}
""",
        f"""
{_DRUGS} > p {{ font-size: 0.9em !important; }}
{_DRUGS} .evidence-legend {{ padding: 6px 10px !important; margin-bottom: 8px !important; font-size: 0.8em !important; }}
{_DRUGS} .evidence-legend p {{ margin: 0 0 3px 0 !important; }}
{_DRUGS} .legend-item {{ margin-bottom: 1px !important; }}
{_DRUGS} .drugs-grid {{ grid-template-columns: repeat(3, 1fr) !important; gap: 3px 8px !important; margin: 6px 0 !important; }}
{_DRUGS} .drug-item {{ padding: 2px 6px !important; font-size: 0.9em !important; }}
""",
    ),
)

_PLATFORM = ".section:has(> #platform-heading)"
PLATFORM = FitSection(
    anchor="platform-heading",
    label="Platform and Citations",
    heading="Platform and Citations",
    max_tail=0.5,
    steps=(
        f"""
{_PLATFORM} h3 {{ margin: 12px 0 6px 0 !important; }}
{_PLATFORM} p {{ margin: 4px 0 !important; font-size: 0.9em !important; }}
{_PLATFORM} td, {_PLATFORM} th {{ padding: 3px 5px !important; font-size: 0.9em !important; }}
{_PLATFORM} ul {{ margin: 4px 0 !important; }}
{_PLATFORM} li {{ font-size: 0.85em !important; line-height: 1.3 !important; }}
""",
        f"""
{_PLATFORM} h3 {{ margin: 8px 0 4px 0 !important; font-size: 1.05em !important; }}
{_PLATFORM} p {{ margin: 3px 0 !important; font-size: 0.82em !important; }}
{_PLATFORM} td, {_PLATFORM} th {{ padding: 2px 5px !important; font-size: 0.85em !important; }}
{_PLATFORM} ul {{ margin: 3px 0 !important; }}
{_PLATFORM} li {{ font-size: 0.78em !important; line-height: 1.25 !important; }}
""",
    ),
)

SECTIONS: Tuple[FitSection, ...] = (ALERTS, EXEC_SUMMARY, DRUGS, PLATFORM)

_HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})


def mark_headings(page_html: str, sections: Sequence[FitSection] = SECTIONS) -> str:
    """Give each section's plain ``<h2>`` its anchor as an id, the first one
    whose text is the section's heading."""
    for section in sections:
        if section.heading:
            page_html = re.sub(
                r"<h2>(\s*" + re.escape(html.escape(section.heading)) + r"\s*)</h2>",
                lambda m, a=section.anchor: f'<h2 id="{a}">{m.group(1)}</h2>',
                page_html,
                count=1,
            )
    return page_html


def _section_element(page_box, anchor):
    """The element whose boxes are the section: the one with this id, or the
    parent of the heading with it."""

    def walk(box, parent):
        element = getattr(box, "element", None)
        if element is not None and element.get("id") == anchor:
            tag = str(element.tag).rsplit("}", 1)[-1].lower()
            return parent if tag in _HEADINGS else element
        for child in getattr(box, "children", None) or []:
            found = walk(child, element if element is not None else parent)
            if found is not None:
                return found
        return None

    return walk(page_box, None)


def _outer_box(page_box, element):
    """The outermost box on this page laid out for ``element``, if any."""
    if getattr(page_box, "element", None) is element:
        return page_box
    for child in getattr(page_box, "children", None) or []:
        found = _outer_box(child, element)
        if found is not None:
            return found
    return None


def section_fragments(document, anchor: str) -> List[Tuple[int, float]]:
    """(page index, fraction of the page's content height used down to the
    section's last line) for each page the section is laid out on."""
    fragments: List[Tuple[int, float]] = []
    element = None
    for index, page in enumerate(document.pages):
        page_box = page._page_box
        if element is None:
            if anchor not in page.anchors:
                continue
            element = _section_element(page_box, anchor)
            if element is None:
                continue
        box = _outer_box(page_box, element)
        if box is None:
            if fragments:
                break
            continue
        bottom = box.position_y + box.margin_height() - page_box.content_box_y()
        fragments.append((index, bottom / page_box.height))
    return fragments


def section_pages(document, anchor: str) -> List[int]:
    """The indices of the pages the section is laid out on."""
    return [index for index, _ in section_fragments(document, anchor)]


def _target(document, section: FitSection) -> int:
    """How many pages the section should take, from its untightened layout."""
    fragments = section_fragments(document, section.anchor)
    if section.max_tail is None:
        return 1
    if len(fragments) > 1 and fragments[-1][1] <= section.max_tail:
        return len(fragments) - 1
    return max(len(fragments), 1)


def fit_sections(
    render: Callable[[str], object], sections: Sequence[FitSection] = SECTIONS
) -> Tuple[object, Dict[str, int]]:
    """Lay the document out, tightening each section that runs over its pages.

    ``render(css)`` lays the document out with ``css`` added and returns the
    WeasyPrint Document. Returns the Document and, per section, the step used
    (0: none). A one-page section still over its page after the last step keeps
    the last step, and says so in the log.
    """

    def css(levels):
        return "\n".join(
            section.steps[levels[section.anchor] - 1]
            for section in sections
            if levels[section.anchor]
        )

    levels = {section.anchor: 0 for section in sections}
    document = render("")
    targets = {section.anchor: _target(document, section) for section in sections}
    while True:
        over = [
            section
            for section in sections
            if len(section_pages(document, section.anchor)) > targets[section.anchor]
            and levels[section.anchor] < len(section.steps)
        ]
        if not over:
            break
        for section in over:
            levels[section.anchor] += 1
        document = render(css(levels))
    # A section that only needed to end sooner, and could not, prints as designed.
    missed = [
        section
        for section in sections
        if section.max_tail is not None
        and levels[section.anchor]
        and len(section_pages(document, section.anchor)) > targets[section.anchor]
    ]
    if missed:
        for section in missed:
            levels[section.anchor] = 0
        document = render(css(levels))
    for section in sections:
        pages = len(section_pages(document, section.anchor))
        if levels[section.anchor]:
            logger.info(
                "PDF: %s tightened, step %d of %d",
                section.label,
                levels[section.anchor],
                len(section.steps),
            )
        if section.max_tail is None and pages > 1:
            logger.warning(
                "PDF: %s still runs to %d pages at its tightest", section.label, pages
            )
    return document, levels
