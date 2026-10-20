"""The Executive Summary is one page, and stays inside it.

It is laid out to a page by design, but how much it holds depends on the sample.
Twenty-two genes from a whole-genome VCF sent two rows and the notes onto a second
page; without PharmCAT's TSV it listed every gene the run reported, so a BAM with
PyPGx's whole record ran to 91 rows over four pages; and CFTR's "ivacaftor
non-responsive in CF patients", unbroken, ran the table to the paper's edge.

Now the summary is one sheet. Its table lists PharmCAT's panel, the 25 genes its
guidelines use, in the same order on every report, called or not, and the PDF
tightens its proportions a step at a time when it would run over
(app/reports/pdf_fit.py). The worst case below -- all 25 genes called, two
unresolved, the long phenotypes -- must fit. The table opens on a right-hand page
and its notes take the page behind it, so a double-sided print holds both on one
leaf.
"""

from __future__ import annotations

import json
import re
from html import unescape
from pathlib import Path

import pytest

import app.reports.generator as generator_module
from app.reports.pdf_fit import (
    EXEC_SUMMARY,
    PILL_EDGE,
    FitSection,
    _summary_cells,
    fit_sections,
    room_under,
    section_fragments,
    section_pages,
    summary_pill_lines,
)

weasyprint = pytest.importorskip("weasyprint")

DATA = Path(__file__).resolve().parents[1] / "test_data"
EXAMPLE_JSON = DATA / "pharmcat.example.v340.report.json"
EXAMPLE_TSV = DATA / "pharmcat.example.v340.report.tsv"
UNPHASED_JSON = DATA / "pharmcat.unphased.example.report.json"
UNPHASED_TSV = DATA / "pharmcat.unphased.example.report.tsv"


def _row(gene, diplotype, phenotype="", score="", outside="no"):
    """A PharmCAT 3.4 TSV row: source columns, then the lookup columns."""
    return "\t".join(
        [gene, diplotype, phenotype, score, "", "", "", "", "", "", outside, "", "no"]
        + [diplotype, phenotype, score]
    )


def _tsv_rows(path):
    return {
        line.split("\t", 1)[0]: line
        for line in path.read_text(encoding="utf-8").splitlines()[2:]
        if line.strip()
    }


def _worst_case_tsv():
    """PharmCAT's example, its seven missing genes added, two calls unresolved."""
    lines = EXAMPLE_TSV.read_text(encoding="utf-8").splitlines()
    rows = _tsv_rows(EXAMPLE_TSV)
    unphased = _tsv_rows(UNPHASED_TSV)
    rows["SLCO1B1"] = unphased["SLCO1B1"]
    rows["CYP2B6"] = unphased["CYP2B6"]
    rows["CYP2C9"] = _row("CYP2C9", "*1/*3", "Intermediate Metabolizer", "1.0")
    rows.update(
        {
            "CYP2D6": _row(
                "CYP2D6", "*1/*4", "Intermediate Metabolizer", "1.0", outside="yes"
            ),
            "F2": _row("F2", "rs1799963 reference (G)/rs1799963 reference (G)"),
            "F5": _row("F5", "rs6025 reference (C)/rs6025 reference (C)"),
            "HLA-A": _row("HLA-A", "*02:01/*31:01", "*31:01 positive", outside="yes"),
            # Every result PharmCAT gives HLA-B, one per risk allele: among the
            # longest pills.
            "HLA-B": _row(
                "HLA-B",
                "*15:02/*57:01",
                "*15:02 positive, *57:01 positive, *58:01 negative",
                outside="yes",
            ),
            "MT-RNR1": _row(
                "MT-RNR1",
                "Reference",
                "normal risk of aminoglycoside-induced hearing loss",
                outside="yes",
            ),
            "NAT2": _row("NAT2", "*5/*6", "Poor Metabolizer"),
        }
    )
    assert len(rows) == 25
    return "\n".join(lines[:2] + list(rows.values())) + "\n"


def _report_html(monkeypatch, tmp_path, data, tsv=None):
    monkeypatch.setattr(generator_module, "EXECSUM_USE_TSV", tsv is not None)
    for key in list(generator_module.REPORT_CONFIG):
        if key.startswith(("write_", "show_")):
            monkeypatch.setitem(
                generator_module.REPORT_CONFIG, key, key == "write_html"
            )
    if tsv is not None:
        (tmp_path / "p1_pgx_pharmcat.tsv").write_text(tsv, encoding="utf-8")
    result = generator_module.generate_report(
        {"data": data}, str(tmp_path), {"id": "p1"}, job_id=None
    )
    return (tmp_path / result["html_path"].split("/")[-1]).read_text(encoding="utf-8")


def _summary(page):
    return page[
        page.index('id="exec-summary"') : page.index("<h2>Genetic Results</h2>")
    ]


def _clean(fragment):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _summary_genes(page):
    return [
        _clean(g)
        for g in re.findall(r'class="exec-summary-gene">(.*?)</td>', _summary(page))
    ]


def _called_genes(page):
    return [
        _clean(g)
        for row, g in re.findall(
            r'<tr id="summary-[^"]+"( class="exec-summary-uncalled")?>\s*'
            r'<td class="exec-summary-number">'
            r'\d+\.</td>\s*<td class="exec-summary-gene">(.*?)</td>',
            _summary(page),
        )
        if not row
    ]


PANEL = sorted(
    json.loads(
        (Path(__file__).resolve().parents[1] / "config" / "genes.json").read_text(
            encoding="utf-8"
        )
    )["sets"]["pharmcat_all"]
)


def _past_the_paper_edge(document):
    """Text in the summary table nearer the paper's right edge than PILL_EDGE: a
    long pill may run on past the table into the margin, but not to the edge."""
    found = []
    for page in document.pages:
        limit = page._page_box.margin_width() - PILL_EDGE

        def walk(box, inside=False):
            element = getattr(box, "element", None)
            if element is not None and "exec-summary-table" in (
                element.get("class") or ""
            ):
                inside = True
            if inside and getattr(box, "text", None):
                if box.position_x + box.width > limit + 0.5:
                    found.append(box.text)
            for child in getattr(box, "children", None) or []:
                walk(child, inside)

        walk(page._page_box)
    return found


def _pills(document):
    """(row id, [(x, height) of each line of its pill], x where its cell's content
    starts) for each summary pill."""
    found = []
    for index in section_pages(document, "exec-summary"):
        page_box = document.pages[index]._page_box
        starts = {}
        for row, cell in _summary_cells(page_box):
            if "exec-summary-phenotype" in (cell.element.get("class") or ""):
                starts[row] = cell.content_box_x()
        for row, lines in summary_pill_lines(page_box):
            found.append(
                (
                    row,
                    [
                        (round(b.position_x, 1), round(b.margin_height(), 1))
                        for b in lines
                    ],
                    round(starts[row], 1),
                )
            )
    return found


def test_the_worst_case_fits_one_page(monkeypatch, tmp_path):
    data = json.loads(EXAMPLE_JSON.read_text(encoding="utf-8"))
    page = _report_html(monkeypatch, tmp_path, data, _worst_case_tsv())
    assert len(_summary_genes(page)) == 25
    document, levels = generator_module.render_pdf_document(page)
    assert len(section_pages(document, "exec-summary")) == 1
    # It took tightening: the template's own proportions do not hold 25 rows.
    assert levels["exec-summary"] >= 1
    assert _past_the_paper_edge(document) == []


def test_the_notes_are_on_the_page_behind_the_table(monkeypatch, tmp_path):
    data = json.loads(UNPHASED_JSON.read_text(encoding="utf-8"))
    page = _report_html(
        monkeypatch, tmp_path, data, UNPHASED_TSV.read_text(encoding="utf-8")
    )
    assert _summary_genes(page) == PANEL
    document, _ = generator_module.render_pdf_document(page)
    (table,) = section_pages(document, "exec-summary")
    assert table % 2 == 0  # page 3: a right-hand page
    assert section_pages(document, "exec-summary-notes") == [table + 1]
    assert "Notes on this table are on the next page" in page


def test_after_long_alerts_the_table_still_opens_on_a_right_hand_page(
    monkeypatch, tmp_path
):
    alert = (
        '<div class="alert-box">&#9888; Partly covered genes: CYP2B6 (*2, *3, *5) '
        "and SLCO1B1 (*2, *4) could not be assessed; the data did not cover the "
        "positions that define them.</div>"
    )
    page = _report_html(monkeypatch, tmp_path, {"genes": [], "drugRecommendations": []})
    page = re.sub(
        r'(<div class="alerts-container"[^>]*>)(.*?)(</div>\s*</div>)',
        lambda m: m.group(1) + alert * 30 + m.group(3),
        page,
        count=1,
        flags=re.S,
    )
    document, _ = generator_module.render_pdf_document(page)
    alerts = section_pages(document, "alerts-heading")
    assert alerts == [1, 2]  # pages 2 and 3: they end on a right-hand page
    (table,) = section_pages(document, "exec-summary")
    assert table == alerts[-1] + 2  # a page left blank, then a right-hand page
    assert section_pages(document, "exec-summary-notes") == [table + 1]


@pytest.mark.parametrize("step", [0, len(EXEC_SUMMARY.steps)], ids=["full", "tightest"])
def test_every_pill_line_is_one_height_and_a_long_one_wraps_at_its_column(
    monkeypatch, tmp_path, step
):
    """A pill too long for its line goes on to a second line, which starts where the
    phenotype column does and is a pill of the same height as every other: not
    smaller type (MT-RNR1's label used to be shrunk to fit), and not one tall block
    beside one-line pills."""
    data = json.loads(EXAMPLE_JSON.read_text(encoding="utf-8"))
    page = _report_html(monkeypatch, tmp_path, data, _worst_case_tsv())
    css = EXEC_SUMMARY.steps[step - 1] if step else ""
    monkeypatch.setattr(
        generator_module, "fit_sections", lambda render: (render(css), {})
    )
    document, _ = generator_module.render_pdf_document(page)
    pills = _pills(document)
    assert {"summary-CFTR", "summary-MT-RNR1", "summary-CYP4F2"} <= {
        row for row, _, _ in pills
    }
    assert len({height for _, lines, _ in pills for _, height in lines}) == 1, pills
    wrapped = [(row, lines, start) for row, lines, start in pills if len(lines) > 1]
    assert wrapped, "the worst case has a phenotype too long for one line"
    for row, lines, start in wrapped:
        # Each continuation starts at the column, inside its padding and border.
        for x, _ in lines[1:]:
            assert abs(x - start) < 1, (row, lines, start)
    assert _past_the_paper_edge(document) == []


def test_every_step_is_tighter_and_the_loosest_that_fits_is_used(monkeypatch, tmp_path):
    """Fine steps, so the table fills its page: with three, a summary one step over
    dropped to type that left a fifth of the page blank."""
    data = json.loads(EXAMPLE_JSON.read_text(encoding="utf-8"))
    page = _report_html(monkeypatch, tmp_path, data, _worst_case_tsv())
    fitted, levels = generator_module.render_pdf_document(page)
    heights = []
    one_page = []
    # Each step's own height: without the fill, which makes every page full.
    monkeypatch.setattr(
        generator_module, "fill_summary_page", lambda render, document, css: document
    )
    for css in ("",) + EXEC_SUMMARY.steps:
        monkeypatch.setattr(
            generator_module,
            "fit_sections",
            lambda render, css=css: (render(css), {}),
        )
        document, _ = generator_module.render_pdf_document(page)
        # One page, and ending on it: not with its bottom padding run past the
        # foot, which leaves the fill no room.
        one_page.append(
            len(section_pages(document, "exec-summary")) == 1
            and room_under(document, "exec-summary") >= 0
        )
        # Its height laid end to end, over however many pages it takes.
        heights.append(
            sum(
                box[3] - box[1]
                for box in (
                    document.pages[i].anchors["exec-summary"]
                    for i in section_pages(document, "exec-summary")
                )
            )
        )
    assert all(a > b for a, b in zip(heights, heights[1:])), heights
    assert levels["exec-summary"] == one_page.index(True) > 0
    # And it fills the page to its foot: the leftover height is shared out between
    # the rows. Three coarse steps once left it at 91%; nine fine ones at 92%.
    ((_, used),) = section_fragments(fitted, "exec-summary")
    assert used > 0.997, used


def test_finding_the_step_takes_few_layouts(monkeypatch, tmp_path):
    """The range of steps is halved each layout. Nine steps of which only the last
    two fit take five layouts; tried in turn, they took nine."""
    data = json.loads(EXAMPLE_JSON.read_text(encoding="utf-8"))
    page = _report_html(monkeypatch, tmp_path, data, _worst_case_tsv())
    late = FitSection(
        anchor="exec-summary",
        label="Executive Summary",
        steps=("",) * 7 + EXEC_SUMMARY.steps[-2:],
    )
    layouts = []

    def counting(render):
        def counted(css):
            layouts.append(css)
            return render(css)

        return fit_sections(counted, (late,))

    monkeypatch.setattr(generator_module, "fit_sections", counting)
    _, levels = generator_module.render_pdf_document(page)
    assert levels["exec-summary"] == 8
    assert len(layouts) == 5, len(layouts)


def test_without_the_tsv_it_keeps_to_pharmcats_panel(monkeypatch, tmp_path):
    """The gene list holds PyPGx's whole record; the summary does not."""
    data = {
        "genes": [
            {
                "gene": "CYP2C19",
                "diplotype": "*1/*2",
                "phenotype": "Intermediate Metabolizer",
                "call_source": "MATCHER",
            },
            {
                "gene": "ABCB1",
                "diplotype": "*1/*2",
                "phenotype": None,
                "tool_source": "PyPGx",
                "pyPgxOnly": True,
            },
            {"gene": "HLA-A", "diplotype": "Unknown/Unknown", "phenotype": "Unknown"},
        ],
        "drugRecommendations": [],
    }
    page = _report_html(monkeypatch, tmp_path, data)
    summary = _clean(_summary(page))
    assert _summary_genes(page) == PANEL
    assert _called_genes(page) == ["CYP2C19"]
    # Uncalled: a row that says so, and named in the notes. Outside the panel:
    # pointed to.
    assert "HLA-A" in summary[summary.index("Not called from this sample") :]
    assert "ABCB1" not in summary
    assert "The 1 other gene this run called" in summary


def test_not_called_never_names_a_gene_the_table_shows(monkeypatch, tmp_path):
    """The table comes from PharmCAT's TSV and the gene list: two sources. A gene
    the table shows as called is not listed again as not called."""
    page = _report_html(
        monkeypatch,
        tmp_path,
        {"genes": [], "drugRecommendations": []},
        EXAMPLE_TSV.read_text(encoding="utf-8"),
    )
    shown = set(_called_genes(page))
    assert len(shown) == 18
    summary = _clean(_summary(page))
    named = summary[summary.index("Not called from this sample") :]
    named = named[named.index(":") + 1 : named.index(". No phenotype")]
    assert {g.strip() for g in named.split(",")} & shown == set()


def test_a_gene_pharmcats_tsv_leaves_out_is_shown_called(monkeypatch, tmp_path):
    """PharmCAT 3.4's TSV has no row for HLA-A, HLA-B or MT-RNR1, the genes it
    takes only as outside calls. Called, they were in neither the table nor the
    not-called list: the summary dropped them."""
    data = {
        "genes": [
            {
                "gene": "MT-RNR1",
                "diplotype": "Reference",
                "phenotype": "normal risk of aminoglycoside-induced hearing loss",
            },
            {
                "gene": "HLA-B",
                "diplotype": "*15:02/*57:01",
                "phenotype": "*57:01 positive",
            },
        ],
        "drugRecommendations": [],
    }
    page = _report_html(
        monkeypatch, tmp_path, data, EXAMPLE_TSV.read_text(encoding="utf-8")
    )
    assert "MT-RNR1" not in EXAMPLE_TSV.read_text(encoding="utf-8")
    called = _called_genes(page)
    assert {"MT-RNR1", "HLA-B"} <= set(called)
    summary = _clean(_summary(page))
    assert "normal risk of aminoglycoside-induced hearing loss" in summary
    not_called = summary[summary.index("Not called from this sample") :]
    assert "MT-RNR1" not in not_called and "HLA-B" not in not_called


@pytest.mark.parametrize("case", ["worst", "unphased"])
def test_the_summary_fills_its_page_to_the_foot(monkeypatch, tmp_path, case):
    """No blank chin under the table, whatever its type size and rows: once the
    loosest step that fits is found, the height left under the summary is shared
    out between its rows, and its last line meets the foot of the page."""
    from app.reports.pdf_fit import _outer_box, _section_element

    if case == "worst":
        data = json.loads(EXAMPLE_JSON.read_text(encoding="utf-8"))
        tsv = _worst_case_tsv()
    else:
        data = json.loads(UNPHASED_JSON.read_text(encoding="utf-8"))
        tsv = UNPHASED_TSV.read_text(encoding="utf-8")
    page = _report_html(monkeypatch, tmp_path, data, tsv)
    document, _ = generator_module.render_pdf_document(page)
    (index,) = section_pages(document, "exec-summary")
    page_box = document.pages[index]._page_box
    section = _outer_box(page_box, _section_element(page_box, "exec-summary"))
    gap = (page_box.content_box_y() + page_box.height) - (
        section.border_box_y() + section.border_height()
    )
    assert 0 <= gap < 2, gap


def test_an_uncalled_row_is_as_tall_as_a_called_one(monkeypatch, tmp_path):
    """ "Not called" had a plain line box, shorter than a pill's: the grey rows of a
    sparse summary were thinner than the rest."""
    data = json.loads(UNPHASED_JSON.read_text(encoding="utf-8"))
    # Both called rows here (CYP2B6, SLCO1B1) would carry PharmCAT's copy-number
    # line, a second line under the pill: not the one-line pill this compares.
    monkeypatch.setattr(
        generator_module, "annotate_pharmcat_copy_number", lambda *a, **k: None
    )
    page = _report_html(
        monkeypatch, tmp_path, data, UNPHASED_TSV.read_text(encoding="utf-8")
    )
    assert 'class="exec-summary-uncalled"' in page
    document, _ = generator_module.render_pdf_document(page)
    (index,) = section_pages(document, "exec-summary")
    heights = {}

    def walk(box):
        element = getattr(box, "element", None)
        if box.__class__.__name__ == "TableRowBox" and (
            element.get("id") or ""
        ).startswith("summary-"):
            heights[element.get("id")] = (
                round(box.height, 1),
                "exec-summary-uncalled" in (element.get("class") or ""),
            )
            return
        for child in getattr(box, "children", None) or []:
            walk(child)

    walk(document.pages[index]._page_box)
    uncalled = [h for h, grey in heights.values() if grey]
    called = [h for h, grey in heights.values() if not grey]
    assert uncalled and called, heights
    # The shortest called row is a one-line pill's; no grey row is shorter.
    assert min(uncalled) >= min(called) - 0.5, heights
