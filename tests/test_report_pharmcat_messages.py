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
    GENERIC,
    LEFT_OUT,
    POSITIONS_RULES,
    STRAND_FOOTNOTE,
    WHEN_NOT_CALLED,
    api_messages,
    drug_messages,
    gene_messages,
    paragraphs,
    shown,
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
    assert set(LEFT_OUT) == {"reference-allele", "pcat-outside-call"}
    assert POSITIONS_RULES == {"missing-variants"}
    for path in (EXAMPLE, UNPHASED, V340):
        report = _load(path)
        for entries in list(gene_messages(report).values()) + list(
            drug_messages(report).values()
        ):
            assert not {m["rule"] for m in entries} & (set(LEFT_OUT) | POSITIONS_RULES)


def test_positions_notes_are_shown_when_the_report_has_no_positions_alert():
    """The file lane (a report rebuilt from a report.json) has no positions alert:
    PharmCAT's own notes on missing positions are then the only ones."""
    report = _load(UNPHASED)
    drugs = drug_messages(report, positions_stated=False)
    assert any(m["rule"] == "missing-variants" for m in drugs["efavirenz"])
    genes = gene_messages(report, positions_stated=False)
    # reference-allele's "... or because the position is missing" form, too.
    assert any(
        m["rule"] == "reference-allele" and "missing" in m["paragraphs"][0]
        for m in genes["CYP2B6"]
    )
    # Its plain form never: the call's name says it.
    plain = {
        "rule_name": "reference-allele",
        "exception_type": "note",
        "message": "The CYP2B6 *1 allele assignment is characterized by the absence "
        "of variants at the positions that are included in the underlying allele "
        "definitions.",
    }
    assert not shown(plain, positions_stated=False)


def test_footnotes_are_read_and_notes_on_every_call_left_out():
    """The strand footnotes are read (the report says them once, for all their
    genes); the notes PharmCAT gives every CYP3A4 or G6PD call whatever the sample
    are not: "The CYP3A4 alleles are determined based on PharmVar CYP3A4 allele
    definitions. See PharmCAT disclaimer ...", and the VCF-format note on G6PD
    ("some VCF files do not specify hemizygous ..."), on G6PD and on its drugs."""
    genes = gene_messages(_load(EXAMPLE))
    assert any(
        "negative chromosomal strand" in m["paragraphs"][0] for m in genes["DPYD"]
    )
    assert "CYP3A4" not in genes and "G6PD" not in genes
    drugs = drug_messages(_load(EXAMPLE))
    for drug, entries in drugs.items():
        assert not [m for m in entries if m["rule"] in GENERIC], drug
    assert set(GENERIC) == {"CYP3A4", "G6PD hemizygous samples"}


def test_a_drug_message_is_shown_once_across_sources():
    drugs = drug_messages(_load(UNPHASED))
    rules = [m["rule"] for m in drugs["efavirenz"]]
    assert len(rules) == len(set(rules))
    assert "CYP2B6 *1/*13 warning" in rules


def test_markup_is_removed_and_the_words_kept():
    assert paragraphs(
        'Please follow the flow chart in figure 2 of the <a href="https://x">CPIC '
        "warfarin guideline</a> to determine.<br><br>Second &amp; last.<p>Third</p>"
    ) == [
        "Please follow the flow chart in figure 2 of the CPIC warfarin guideline "
        "(https://x) to determine.",
        "Second & last.",
        "Third",
    ]
    # A link within PharmCAT's own report leads nowhere here: its words only.
    assert paragraphs('See <a href="#CYP2C19">CYP2C19</a> in Section III') == [
        "See CYP2C19 in Section III"
    ]
    # A literal "<" is text, not a tag.
    assert paragraphs("AS < 1.5 and AS > 0.5") == ["AS < 1.5 and AS > 0.5"]


def _highlighted(drugs, drug):
    return [
        m["paragraphs"][0] for m in drugs[drug] if m["kind"] == "report-as-genotype"
    ]


def test_report_as_genotype_is_read_from_highlighted_variants():
    """PharmCAT's cpic-warfarin-rs12777823 rule has no text: PharmCAT writes the
    genotype into warfarin's annotations (highlightedVariants), once however many
    sources carry it; "|" from phased input reads "/", and PharmCAT's "Unknown"
    stays."""
    assert _highlighted(drug_messages(_load(EXAMPLE)), "warfarin") == ["rs12777823:G/G"]
    report = {
        "drugs": {
            source: {
                "warfarin": {
                    "name": "warfarin",
                    "messages": [],
                    "guidelines": [
                        {"annotations": [{"highlightedVariants": [variant]}]}
                    ],
                }
            }
            for source, variant in (
                ("CPIC Guideline Annotation", "rs12777823:G|A"),
                ("DPWG Guideline Annotation", "rs12777823:G/A"),
            )
        }
    }
    assert _highlighted(drug_messages(report), "warfarin") == ["rs12777823:G/A"]
    report["drugs"]["CPIC Guideline Annotation"]["warfarin"]["guidelines"][0][
        "annotations"
    ][0]["highlightedVariants"] = ["rs12777823:Unknown"]
    assert "rs12777823:Unknown" in _highlighted(drug_messages(report), "warfarin")


def test_a_drug_note_names_its_gene():
    """An ambiguity's text names no gene; amitriptyline rests on two."""
    drugs = drug_messages(_load(UNPHASED))
    (entry,) = [m for m in drugs["efavirenz"] if m["rule"] == "CYP2B6 *1/*13 warning"]
    assert entry["gene"] == "CYP2B6"


def test_top_level_messages_are_read():
    report = _load(V340)
    report["messages"] = [
        {"rule_name": "r", "exception_type": "note", "message": "A general note."}
    ]
    assert gene_messages(report)[""][0]["paragraphs"] == ["A general note."]


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
    # What the report shows, so the API and the report agree.
    assert [m["message"] for m in api_messages(messages)] == ["a", "c", "f"]
    assert api_messages([{"exception_type": "new-kind", "message": "g"}]) == [
        {"exception_type": "new-kind", "message": "g"}
    ]


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
    assert "PharmCAT (CYP2B6): The assigned genotype is *1/*13" in recs
    # reference-allele's plain form is left out; its "... or missing" form is a
    # positions note, shown here, where (no job) the report has no positions alert.
    text = _clean(page)
    assert "is characterized by the absence of variants" in text
    assert "underlying allele definitions." not in text


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
    results = page[page.index("<h2>Genetic Results</h2>") :]
    results = _clean(results[: results.index("<h2>", 10)])
    strand = []
    for gene, entries in gene_messages(_load(path)).items():
        for m in entries:
            if f"<td>{gene}</td>" not in page:
                continue
            if STRAND_FOOTNOTE.match(m["rule"]):
                # Said once for all of them, not once a gene.
                strand.append(gene)
                assert m["paragraphs"][0] not in page, (gene, m)
            elif m["rule"] in WHEN_NOT_CALLED:
                # SLCO1B1's text on what is shown if no star allele is called:
                # it has one.
                assert m["paragraphs"][0] not in page, (gene, m)
            else:
                # A called gene's messages are under Genetic Results.
                assert m["paragraphs"][0] in page, (gene, m)
    if strand:
        names = sorted(strand)
        listed = (
            names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        )
        assert (
            f"{listed} {'is' if len(names) == 1 else 'are'} on the negative strand: "
            "as PharmCAT notes," in results
        ), results
        assert results.count("negative strand") == 1
        assert "negative chromosomal strand" not in results


# PharmCAT 3.4.0's only non-match rule (reporter/messages.json), which it adds to
# RYR1 when RYR1 has data but no call, and to the seven anaesthetics.
MH = {
    "rule_name": "Malignant Hyperthermia Susceptibility warning",
    "version": "2",
    "matches": {
        "gene": "RYR1",
        "drugs": ["desflurane", "sevoflurane", "succinylcholine"],
    },
    "exception_type": "non-match",
    "message": "If the RYR1 genotype is \u201cnot called\u201d and the drug section "
    "reports RYR1:Unknown/Unknown, please refer to the RYR1 gene table in section III "
    "for the variants included in the input VCF file. In case one or more variant(s) "
    "with \u201cMalignant Hyperthermia associated\u201d function are present, please "
    "refer to the guideline publication PMID:30499100 since halogenated volatile "
    "anesthetics or depolarizing muscle relaxants succinylcholine are relatively "
    "contraindicated in persons with Malignant Hyperthermia Susceptibility. ",
}


def _uncalled_ryr1(tmp_path):
    """The unphased example (RYR1 Unknown/Unknown) with PharmCAT's warning on RYR1
    and on desflurane, which has no annotation for this sample."""
    report = _load(UNPHASED)
    report["genes"]["RYR1"].setdefault("messages", []).append(MH)
    report["drugs"]["CPIC Guideline Annotation"]["desflurane"] = {
        "name": "desflurane",
        "id": "PA164749136",
        "source": "CPIC",
        "messages": [MH],
        "variants": [],
        "urls": [],
        "citations": [],
        "guidelines": [{"id": "g", "name": "n", "source": "CPIC", "annotations": []}],
    }
    path = tmp_path / "ryr1.report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def test_an_uncalled_ryr1s_warning_reaches_both_reports(monkeypatch, tmp_path):
    path = _uncalled_ryr1(tmp_path)
    page = unescape(_page(monkeypatch, tmp_path / "html", path, "html"))
    results = page[page.index("<h2>Genetic Results</h2>") :]
    results = _clean(results[: results.index("<h2>", 10)])
    assert "RYR1 (not called): If the RYR1 genotype is “not called”" in results
    recs = _clean(page[page.index("<h2>Drug Recommendations</h2>") :])
    assert "PharmCAT's notes on drugs without a recommendation for this sample" in recs
    assert "Desflurane — PharmCAT (RYR1): If the RYR1 genotype" in recs
    summary = page[
        page.index('id="exec-summary"') : page.index("<h2>Genetic Results</h2>")
    ]
    assert "Malignant Hyperthermia associated" not in summary

    page = _page(monkeypatch, tmp_path / "int", path, "interactive_html")
    found = dict(
        re.findall(
            r'data-gene="([^"]+)"[^>]*?data-pharmcat-messages=\'([^\']*)\'', page, re.S
        )
    )
    assert any(
        "RYR1 genotype is" in line for line in json.loads(unescape(found["RYR1"]))
    )
    recs = unescape(page[page.index('<div id="recs"') :])
    recs = _clean(recs[: recs.index('<div id="visualizations"')])
    assert "Desflurane — PharmCAT (RYR1): If the RYR1 genotype" in recs


def test_notes_on_drugs_without_an_annotation_are_one_entry_each(monkeypatch, tmp_path):
    """The example's FDA chloroquine, glyburide, ...: no annotation for this sample.
    PharmCAT's VCF-format G6PD note on each (GENERIC) is left out; an uncalled
    RYR1's warning (test_an_uncalled_ryr1s_warning_reaches_both_reports) is not."""
    page = unescape(_page(monkeypatch, tmp_path, EXAMPLE, "html"))
    assert "PharmCAT reports based on VCF" not in page
    assert "drugs without a recommendation for this sample" not in page


def _notes_page(rows, general=(), strand=()):
    from app.reports.generator import env

    macros = env.get_template("_pharmcat_messages.html").module
    return _clean(str(macros.gene_notes(rows, list(general), list(strand))))


def _note(rule, text, kind="note"):
    return {"kind": kind, "rule": rule, "gene": "", "paragraphs": [text]}


def test_a_genes_notes_are_one_line():
    """UGT1A1's ambiguity and its repeat footnote were two bullets, as were
    SLCO1B1's two notes."""
    rows = [
        {
            "gene": "UGT1A1",
            "diplotype": "*1/*80+*28",
            "pharmcat_messages": [
                _note(
                    "UGT1A1 *1/*80+*28 warning",
                    "The assigned genotype is A.",
                    "ambiguity",
                ),
                _note(
                    "UGT1A1 repeat warning", "Some alleles ... the repeat.", "footnote"
                ),
            ],
        }
    ]
    body = _notes_page(rows)
    assert body.count("UGT1A1 :") == 1
    assert "UGT1A1 : The assigned genotype is A. Some alleles ... the repeat." in body


def test_the_strand_footnotes_are_one_line(monkeypatch, tmp_path):
    """Five genes' "The X gene is on the negative chromosomal strand, ..." said
    once; the demo listed CYP3A5, DPYD, IFNL3, TPMT and VKORC1 a bullet each."""
    from app.reports.generator import attach_pharmcat_messages

    report = _load(EXAMPLE)
    rows = [
        {"gene": g, "diplotype": "*1/*1"}
        for g in ("CYP3A5", "DPYD", "IFNL3", "TPMT", "VKORC1", "SLCO1B1", "UGT1A1")
    ]
    notes = attach_pharmcat_messages(report, rows, [])
    assert notes["strand"] == ["CYP3A5", "DPYD", "IFNL3", "TPMT", "VKORC1"]
    by_gene = {r["gene"]: r.get("pharmcat_messages") or [] for r in rows}
    for gene in notes["strand"]:
        assert not [m for m in by_gene[gene] if STRAND_FOOTNOTE.match(m["rule"])]
    # DPYD keeps its activity-score note, which bears on its score.
    assert [m["rule"] for m in by_gene["DPYD"]] == ["DPYD lowest activity score note"]
    # SLCO1B1 has a call: its "will be displayed if determinable" text goes.
    assert not by_gene["SLCO1B1"]
    body = _notes_page(rows, (), notes["strand"])
    assert body.count("negative strand") == 1
    assert (
        "CYP3A5, DPYD, IFNL3, TPMT and VKORC1 are on the negative strand: as "
        "PharmCAT notes, their genotypes are given here on the positive strand, "
        "complemented from the gene's bases." in body
    )


def test_slco1b1s_text_stays_when_it_has_no_call():
    """It says recommendations then rest on rs4149056 alone: then it matters."""
    from app.reports.generator import attach_pharmcat_messages

    rows = [{"gene": "SLCO1B1", "diplotype": "Unknown/Unknown"}]
    attach_pharmcat_messages(_load(V340), rows, [])
    rules = [m["rule"] for m in rows[0]["pharmcat_messages"]]
    assert "SLCO1B1-drug text" in rules
