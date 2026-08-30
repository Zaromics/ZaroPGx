"""Citations must read as citations, not as Python dicts.

The Drug Recommendations section rendered its references with
``|join(", ")`` over a list of *dicts*. Jinja has no join semantics for a dict,
so it fell back to ``repr`` and every citation in the report -- across roughly
thirty-five pages -- read:

    {'pmid': '21412232', 'year': 2011, 'title': '...', '_sameAs':
    'https://...', 'journal': 'Clinical pharmacology and therapeutics'}

Braces, quoted keys, and ``_sameAs``: a JSON-LD internal that means nothing to a
reader and is derivable from the PMID anyway.

Formatting at render time alone was not enough, and that is the real lesson
here: ``app/services/pharmcat_data_service.py`` had already collapsed each
citation with ``str(c)`` before any template saw it, so the repr was what got
stored, returned by the API and printed. The fix belongs at the conversion, not
at the display -- hence the last test in this module.

The sentinels matter as much as the formatting. FDA Table entries arrive with
``pmid: None, year: -1, journal: None``, and the repr printed them verbatim -- a
clinical document carrying a citation dated year -1.
"""

from __future__ import annotations

import pytest

from app.reports.generator import env
from app.utils.literature import (
    format_literature_reference,
    format_literature_references,
)

PUBMED = {
    "pmid": "21412232",
    "year": 2011,
    "title": "Pharmacogenetics: from bench to byte--an update of guidelines.",
    "_sameAs": "https://www.ncbi.nlm.nih.gov/pubmed/21412232",
    "journal": "Clinical pharmacology and therapeutics",
}
FDA = {
    "pmid": None,
    "year": -1,
    "title": "FDA Table of Pharmacogenetic Associations",
    "_sameAs": "https://www.fda.gov/medical-devices",
    "journal": None,
}


def test_a_pubmed_reference_reads_as_a_citation():
    text = format_literature_reference(PUBMED)
    assert text == (
        "Pharmacogenetics: from bench to byte--an update of guidelines. "
        "Clinical pharmacology and therapeutics. 2011. PMID 21412232."
    )


@pytest.mark.parametrize("noise", ["{", "}", "'", "_sameAs", "pmid'"])
def test_no_dict_syntax_survives(noise):
    assert noise not in format_literature_references([PUBMED, FDA])


def test_sentinel_year_and_pmid_are_dropped_not_printed():
    """`year: -1` and `pmid: None` are absences, not data."""
    text = format_literature_reference(FDA)

    assert text == "FDA Table of Pharmacogenetic Associations."
    assert "-1" not in text
    assert "None" not in text


def test_multiple_references_are_separated():
    text = format_literature_references([PUBMED, FDA])
    assert text.count("PMID") == 1
    assert "guidelines. Clinical" in text and "FDA Table" in text


@pytest.mark.parametrize("empty", [[], None, ""])
def test_nothing_renders_when_there_are_no_references(empty):
    assert format_literature_references(empty) == ""


def test_a_plain_string_is_passed_through():
    """Older payloads stored references as text; they must not be mangled."""
    assert format_literature_references("Smith et al. 2019.") == "Smith et al. 2019."


def test_a_reference_with_only_a_pmid_still_says_something():
    assert format_literature_reference({"pmid": "12345"}) == "PMID 12345."


def test_the_filter_is_registered_for_the_templates():
    """Jinja resolves filters at compile time; an unregistered one is a stub page."""
    assert "literature_references" in env.filters


@pytest.mark.parametrize(
    "template_name", ["report_template.html", "interactive_report.html"]
)
def test_both_templates_use_the_filter_rather_than_join(template_name):
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "reports"
        / "templates"
        / template_name
    ).read_text(encoding="utf-8")

    assert (
        "literature_references|join" not in source
    ), f"{template_name} joins reference dicts again; Jinja will repr them"
    assert "literature_references|literature_references" in source


def test_the_data_service_formats_citations_rather_than_stringifying_them():
    """The actual origin. A render-time filter cannot undo `str(dict)` upstream.

    pharmcat_data_service turns PharmCAT's citation objects into the
    List[str] the model declares; doing that with str() is what put Python
    reprs into storage, into the API response and onto the page.
    """
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "services"
        / "pharmcat_data_service.py"
    ).read_text(encoding="utf-8")

    assert "format_literature_reference" in source
    assert "[str(c) for c in citations]" not in source, (
        "citations are being stringified again; the report will print dict reprs "
        "no matter what the template does"
    )
