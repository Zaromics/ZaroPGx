"""Fit report sections to their PDF pages by tightening their proportions.

Some sections are laid out to a page by design: the Executive Summary is one page.
How much they hold depends on the sample -- ten genes from a variant-only VCF,
twenty-five from a whole genome -- so no one set of proportions suits every
report: generous spacing that suits ten rows sends a twenty-two-row summary two
rows and its notes onto a second page. So the PDF is laid out, each such section
measured, and one that runs over is laid out again with tighter spacing and
smaller type, a step at a time, until it fits or the steps run out. The Executive
Summary is then laid out once more with the height left under it shared out
between its rows (fill_summary_page), so it fills its page whatever its type size
and rows.

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


def _summary_step(
    *,
    h2_margin: float,
    p_margin: float,
    table_margin: float,
    cell_y: float,
    cell_x: float = 6,
    note: float = 0.85,
    font: Optional[float] = None,
) -> str:
    """One Executive Summary step: its spacing, and with ``font`` its type, the
    padding of its pills and the size of its score gauges scaled to it."""
    css = f"""
#exec-summary h2 {{ margin-bottom: {h2_margin:g}px !important; }}
#exec-summary p {{ margin: {p_margin:g}px 0 !important; }}
#exec-summary .exec-summary-table {{ margin-top: {table_margin:g}px !important; }}
#exec-summary .exec-summary-table td {{ padding: {cell_y:g}px {cell_x:g}px !important; }}
#exec-summary .header-note {{ font-size: {note:g}em !important; }}
"""
    if font is None:
        return css
    # 0 at the template's 16px, 1 at the smallest step, 12.5px.
    t = (16 - font) / 3.5
    return css + f"""
#exec-summary h2 {{ font-size: {1.5 - 0.2 * t:.3g}em !important; }}
#exec-summary > p, #exec-summary .summary-highlights > p {{ font-size: {1 - 0.18 * t:.3g}em !important; }}
#exec-summary .exec-summary-table {{ font-size: {font:g}px !important; }}
#exec-summary .exec-summary-diplotype.long-diplotype {{ font-size: {14 - 3 * t:.3g}px !important; }}
#exec-summary .gene-phenotype {{ padding: {3 - 3 * t:.2g}px {10 - 3 * t:.3g}px !important; }}
#exec-summary .phenotype-line {{ line-height: {1.15 * font + 2 * (3 - 3 * t) + 6:.3g}px !important; }}
#exec-summary .as-badge svg {{ width: {16 - 4 * t:.3g}px !important; height: {16 - 4 * t:.3g}px !important; }}
"""


def _exec_summary_steps() -> Tuple[str, ...]:
    """Each tighter than the last: two of spacing, then the type half a pixel at a
    time from 15.5px to 12.5px, the spacing closing with it.

    Fine, because the table is one page and should fill it. With three coarse
    steps, a summary one step over its page dropped to type that left a fifth of
    the page blank under it.
    """
    steps = [
        _summary_step(h2_margin=16, p_margin=10, table_margin=6, cell_y=4.5, note=0.9),
        _summary_step(h2_margin=12, p_margin=6, table_margin=4, cell_y=3),
    ]
    for font in (15.5, 15, 14.5, 14, 13.5, 13, 12.5):
        t = (16 - font) / 3.5
        steps.append(
            _summary_step(
                h2_margin=round(12 - 4 * t, 1),
                p_margin=round(6 - 2 * t, 1),
                table_margin=4,
                cell_y=round(3 - 2 * t, 2),
                cell_x=round(6 - 2 * t, 2),
                note=round(0.85 - 0.1 * t, 3),
                font=font,
            )
        )
    return tuple(steps)


EXEC_SUMMARY = FitSection(
    anchor="exec-summary",
    label="Executive Summary",
    steps=_exec_summary_steps(),
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
{_PLATFORM} ul, {_PLATFORM} ol {{ margin: 4px 0 !important; }}
{_PLATFORM} li {{ font-size: 0.85em !important; line-height: 1.3 !important; }}
""",
        f"""
{_PLATFORM} h3 {{ margin: 8px 0 4px 0 !important; font-size: 1.05em !important; }}
{_PLATFORM} p {{ margin: 3px 0 !important; font-size: 0.82em !important; }}
{_PLATFORM} td, {_PLATFORM} th {{ padding: 2px 5px !important; font-size: 0.85em !important; }}
{_PLATFORM} ul, {_PLATFORM} ol {{ margin: 3px 0 !important; }}
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
    (0: none): the loosest step that fits, found by halving the range of steps
    left: nine steps take at most five layouts after the first. Sections are found
    together, one layout per round. A one-page section still over its page at the
    last step keeps the last step, and says so in the log.
    """

    def css(levels):
        return "\n".join(
            section.steps[levels[section.anchor] - 1]
            for section in sections
            if levels[section.anchor]
        )

    def fits(document, section):
        return len(section_pages(document, section.anchor)) <= targets[section.anchor]

    levels = {section.anchor: 0 for section in sections}
    document = render("")
    targets = {section.anchor: _target(document, section) for section in sections}
    # Per section that runs over: the tightest step known not to fit (lo) and the
    # loosest one not known to fail (hi), which is a fit once tried.
    search = {
        section.anchor: [0, len(section.steps), False]
        for section in sections
        if section.steps and not fits(document, section)
    }
    while True:
        trying = {}
        for anchor, (lo, hi, hi_fits) in search.items():
            if hi - lo > 1:
                trying[anchor] = (lo + hi) // 2
            elif not hi_fits and lo < hi:
                trying[anchor] = hi
        if not trying:
            break
        for section in sections:
            if section.anchor in search:
                lo, hi, hi_fits = search[section.anchor]
                levels[section.anchor] = trying.get(section.anchor, hi)
        document = render(css(levels))
        for section in sections:
            step = trying.get(section.anchor)
            if step is None:
                continue
            if fits(document, section):
                search[section.anchor][1:] = [step, True]
            else:
                search[section.anchor][0] = step
    # The loosest step that fits; none fitting, the tightest for a one-page
    # section, and the design itself for one that only needed to end sooner.
    chosen = dict(levels)
    for section in sections:
        if section.anchor not in search:
            continue
        lo, hi, hi_fits = search[section.anchor]
        if hi_fits:
            chosen[section.anchor] = hi
        else:
            chosen[section.anchor] = (
                len(section.steps) if section.max_tail is None else 0
            )
    if chosen != levels:
        levels = chosen
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


# How near the paper's right edge Executive Summary text may run: 8mm, in CSS pixels
# (96 to the inch). The template lets a phenotype run that far into the 16mm margin.
PILL_EDGE = 8 / 25.4 * 96

# Left between the filled summary and the foot of its page, in CSS pixels, so
# rounding in the layout cannot push its last line over.
FILL_MARGIN = 1.0


def _descendants(box):
    for child in getattr(box, "children", None) or []:
        yield child
        yield from _descendants(child)


def _summary_cells(page_box):
    """(row id, cell box) for the cells of the Executive Summary table's rows on
    this page."""
    cells = []

    def walk(box, row_id=None):
        element = getattr(box, "element", None)
        tag = element.tag.rsplit("}", 1)[-1] if element is not None else ""
        if tag == "tr" and (element.get("id") or "").startswith("summary-"):
            row_id = element.get("id")
        if row_id and box.__class__.__name__ == "TableCellBox":
            cells.append((row_id, box))
            return
        for child in getattr(box, "children", None) or []:
            walk(child, row_id)

    walk(page_box)
    return cells


def summary_pill_lines(page_box):
    """(row id, line boxes of its pill) for each summary row with a phenotype pill:
    one box per line the pill takes."""
    found = []

    def walk(box, row_id=None):
        element = getattr(box, "element", None)
        if element is not None and element.tag.rsplit("}", 1)[-1] == "tr":
            row_id = element.get("id") or None
        if (
            row_id
            and row_id.startswith("summary-")
            and element is not None
            and ("exec-summary-phenotype" in (element.get("class") or "").split())
        ):
            pills = [
                b
                for b in _descendants(box)
                if b.__class__.__name__ == "InlineBox"
                and "gene-phenotype"
                in (getattr(b, "element", None).get("class") or "").split()
            ]
            if pills:
                found.append((row_id, pills))
            return
        for child in getattr(box, "children", None) or []:
            walk(child, row_id)

    walk(page_box)
    return found


def fill_summary_page(render, document, css: str):
    """The Executive Summary laid out again to fill its page: the height left under
    it, whatever the type size and however many rows, shared out between its rows
    as cell padding, so its last line meets the foot of the page.

    ``css`` is the stylesheet ``document`` was laid out with. Padding does not
    change where any line wraps, so the rows grow by exactly what is added and one
    layout lands it; a layout that would push the summary onto a second page is
    discarded. A summary that already runs over is returned as it is.
    """
    pages = section_pages(document, EXEC_SUMMARY.anchor)
    if len(pages) != 1:
        return document
    page_box = document.pages[pages[0]]._page_box
    section = _outer_box(page_box, _section_element(page_box, EXEC_SUMMARY.anchor))
    cells = _summary_cells(page_box)
    rows = len({row_id for row_id, _ in cells})
    # To the section's last line: its own bottom margin may sit in the page's.
    bottom = section.border_box_y() + section.border_height()
    slack = page_box.content_box_y() + page_box.height - bottom - FILL_MARGIN
    if not rows or slack < 1:
        return document
    # Every cell has the same vertical padding, the template's or a fitting step's.
    # Read the specified value: WeasyPrint centres a cell's content by adding to its
    # laid-out top padding.
    cell_y = cells[0][1].style["padding_top"].value
    for share in (1.0, 0.98):
        extra = slack * share / rows / 2
        rule = (
            "#exec-summary .exec-summary-table td { "
            f"padding-top: {cell_y + extra:.3f}px !important; "
            f"padding-bottom: {cell_y + extra:.3f}px !important; }}"
        )
        filled = render(f"{css}\n{rule}")
        if len(section_pages(filled, EXEC_SUMMARY.anchor)) == 1:
            return filled
    logger.warning("PDF: Executive Summary could not be filled to its page")
    return document
