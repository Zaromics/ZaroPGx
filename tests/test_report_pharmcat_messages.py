"""PharmCAT's own messages appear where PharmCAT's report shows them.

PharmCAT 3.4 puts messages on each gene and each drug in report.json: an
unphased call's "The assigned genotype is *1/*13; however, *6/*8 cannot be ruled
out without phased data.", G6PD's hemizygous-sample note, the reverse-strand
footnotes, the warfarin notes. The report showed none of them, and the API's
warning_messages selected kinds 3.4 never emits.
"""

from __future__ import annotations

import json
import re
from html import unescape
from pathlib import Path

import pytest

import app.reports.generator as generator_module
from app.pharmcat.pharmcat_messages import (
    LEFT_OUT,
    REPORT_AS_GENOTYPE,
    api_messages,
    drug_messages,
    gene_messages,
    paragraphs,
)

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "test_data"
EXAMPLE = DATA / "pharmcat.example.report.json"  # 2.x nested shape
UNPHASED = DATA / "pharmcat.unphased.example.report.json"  # 3.4
V340 = DATA / "pharmcat.example.v340.report.json"


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_an_ambiguous_call_keeps_pharmcats_words():
    genes = gene_messages(_load(UNPHASED))
    texts = [p for m in genes["CYP2B6"] for p in m["paragraphs"]]
    assert (
        "The assigned genotype is *1/*13; however, *6/*8 cannot be ruled out "
        "without phased data." in texts
    )
    assert {m["kind"] for m in genes["CYP2B6"]} == {"ambiguity"}


def test_left_out_rules_are_the_ones_the_report_states_itself():
    assert set(LEFT_OUT) == {
        "reference-allele",
        "missing-variants",
        "pcat-outside-call",
    }
    for path in (EXAMPLE, UNPHASED, V340):
        report = _load(path)
        for entries in list(gene_messages(report).values()) + list(
            drug_messages(report).values()
        ):
            assert not {m["rule"] for m in entries} & set(LEFT_OUT)


def test_footnotes_and_notes_are_shown():
    genes = gene_messages(_load(EXAMPLE))
    assert any(
        "negative chromosomal strand" in m["paragraphs"][0] for m in genes["DPYD"]
    )
    drugs = drug_messages(_load(EXAMPLE))
    hemizygous = [m for m in drugs["dapsone"] if m["rule"] == "G6PD hemizygous samples"]
    assert hemizygous and hemizygous[0]["kind"] == "note"
    assert hemizygous[0]["paragraphs"][0].startswith(
        "PharmCAT reports based on VCF file input and some VCF files do not specify "
        "hemizygous (one X chromosome)"
    )


def test_a_drug_message_is_shown_once_across_sources():
    drugs = drug_messages(_load(UNPHASED))
    rules = [m["rule"] for m in drugs["efavirenz"]]
    assert len(rules) == len(set(rules))
    assert "CYP2B6 *1/*13 warning" in rules


def test_markup_is_removed_and_the_words_kept():
    assert paragraphs(
        'Please follow the flow chart in figure 2 of the <a href="https://x">CPIC '
        "warfarin guideline</a> to determine.<br><br>Second &amp; last."
    ) == [
        "Please follow the flow chart in figure 2 of the CPIC warfarin guideline to "
        "determine.",
        "Second & last.",
    ]


def test_report_as_genotype_gives_the_variants_genotype():
    """PharmCAT's cpic-warfarin-rs12777823 rule has no text: it reports the
    variant's genotype with warfarin. The example's CYP2C9 has rs12777823 G/G."""
    assert REPORT_AS_GENOTYPE == {"warfarin": (("CYP2C9", "rs12777823"),)}
    drugs = drug_messages(_load(EXAMPLE))
    lines = [
        m["paragraphs"][0]
        for m in drugs["warfarin"]
        if m["kind"] == "report-as-genotype"
    ]
    assert lines == ["CYP2C9 rs12777823: G/G"]


def test_the_api_returns_the_kinds_pharmcat_emits():
    messages = [
        {"exception_type": "ambiguity", "rule_name": "x", "message": "a"},
        {
            "exception_type": "note",
            "rule_name": "G6PD hemizygous samples",
            "message": "b",
        },
        {"exception_type": "footnote", "rule_name": "y", "message": "c"},
        {"exception_type": "note", "rule_name": "reference-allele", "message": "d"},
        {"exception_type": "note", "rule_name": "missing-variants", "message": "e"},
        {"exception_type": "warning", "rule_name": "z", "message": "f"},
    ]
    assert [m["message"] for m in api_messages(messages)] == ["a", "b", "c", "f"]


def _page(monkeypatch, tmp_path, path, kind):
    for key in list(generator_module.REPORT_CONFIG):
        if key.startswith(("write_", "show_")):
            monkeypatch.setitem(
                generator_module.REPORT_CONFIG,
                key,
                key == f"write_{kind}" or key.startswith("show_"),
            )
    result = generator_module.generate_report(
        {"data": _load(path)}, str(tmp_path), {"id": "p1"}
    )
    return (tmp_path / result[f"{kind}_path"].split("/")[-1]).read_text(
        encoding="utf-8"
    )


def _clean(fragment):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def test_the_pdf_html_shows_gene_and_drug_messages(monkeypatch, tmp_path):
    page = _page(monkeypatch, tmp_path, UNPHASED, "html")
    summary = page[
        page.index('id="exec-summary"') : page.index("<h2>Genetic Results</h2>")
    ]
    # Nothing on the Executive Summary or its notes.
    assert "cannot be ruled out" not in summary
    results = page[page.index("<h2>Genetic Results</h2>") :]
    results = results[: results.index("<h2>", 10)]
    body = _clean(results)
    assert "PharmCAT's notes on these calls" in body
    assert (
        "CYP2B6 : The assigned genotype is *1/*13; however, *6/*8 cannot be ruled out "
        "without phased data." in body
    )
    recs = page[page.index('id="drug-rec-efavirenz"') :]
    recs = _clean(recs[: recs.index('class="drug-recommendation"', 10)])
    assert "PharmCAT: The assigned genotype is *1/*13" in recs
    assert "reference allele assignment" not in _clean(page)


def test_the_interactive_report_shows_them(monkeypatch, tmp_path):
    page = _page(monkeypatch, tmp_path, UNPHASED, "interactive_html")
    genes = page[page.index('<div id="genes"') :]
    genes = genes[: genes.index('<div id="geneInfoSection"')]
    assert "however, *6/*8 cannot be ruled out" in unescape(genes)
    found = dict(
        re.findall(
            r'data-gene="([^"]+)"[^>]*?data-pharmcat-messages=\'([^\']*)\'', page, re.S
        )
    )
    lines = json.loads(unescape(found["CYP2B6"]))
    assert any("however, *6/*8 cannot be ruled out" in line for line in lines)
    recs = page[page.index('id="drug-rec-efavirenz"') :]
    assert "however, *6/*8 cannot be ruled out" in unescape(recs[:20000])


@pytest.mark.parametrize("path", [EXAMPLE, V340])
def test_every_report_renders_its_messages(monkeypatch, tmp_path, path):
    page = unescape(_page(monkeypatch, tmp_path, path, "html"))
    for gene, entries in gene_messages(_load(path)).items():
        for m in entries:
            # A called gene's messages are under Genetic Results.
            if f"<td>{gene}</td>" in page:
                assert m["paragraphs"][0] in page, (gene, m)
