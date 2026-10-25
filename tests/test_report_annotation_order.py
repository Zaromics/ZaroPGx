"""A drug's guideline annotations are listed in PharmCAT's order, every run.

Two runs of the same input listed atomoxetine's DPWG and FDA label annotations,
and pantoprazole's DPWG and FDA PGx ones, in different orders, from byte-identical
PharmCAT output. The database lane read the rows back without an ORDER BY, so
they came in the order they lay in the table, and Postgres puts a row wherever
its free space map finds room: not the order they were written.

The rows are now read in the order the parser wrote them (PharmCAT's), and each
drug's annotations are listed by source in PharmCAT's order: CPIC, DPWG, FDA
label, FDA PGx association, as its report.json orders its sections and its HTML
report lists them.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import text

from app.pharmcat.pharmcat_parser import PharmCATParser
from app.pharmcat.report_json import (
    GUIDELINE_SOURCE_NAMES,
    guideline_source_name,
    guideline_source_rank,
)
from app.reports.evidence import NO_RECOMMENDATION_TEXT
from app.services.pharmcat_data_service import PharmCATDataService

EXAMPLE = (
    Path(__file__).resolve().parents[1] / "test_data" / "pharmcat.example.report.json"
)


def _pharmcat_order(report):
    """Each drug's annotations as PharmCAT's report.json lists them."""
    order = {}
    for section, drugs in report["drugs"].items():
        for drug, data in drugs.items():
            for guideline in data.get("guidelines") or []:
                for annotation in guideline.get("annotations") or []:
                    order.setdefault(drug, []).append(
                        (
                            section,
                            annotation.get("drugRecommendation")
                            or NO_RECOMMENDATION_TEXT,
                        )
                    )
    return order


def _report_order(data):
    return {
        drug["drug"]: [
            (rec.get("guideline_source"), rec["recommendation"])
            for rec in drug["recommendations"]
        ]
        for drug in data["drugRecommendations"]
    }


def test_pharmcat_s_sections_are_in_its_source_order():
    """The rank follows PharmCAT's own section order, codes and names alike."""
    report = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    sections = list(report["drugs"])
    assert sections == list(GUIDELINE_SOURCE_NAMES.values())
    assert [guideline_source_rank(s) for s in sections] == list(range(len(sections)))
    assert [guideline_source_rank(c) for c in GUIDELINE_SOURCE_NAMES] == list(
        range(len(sections))
    )
    assert guideline_source_rank("Something else") == len(sections)
    assert guideline_source_rank(None) == len(sections)


def test_stored_annotations_come_back_in_pharmcat_s_order(db_session):
    """However the rows lie in the table."""
    report = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    run_id = PharmCATParser(db_session).parse_and_load(report, run_id="order")
    db_session.flush()
    # Rewrite the CPIC rows in place: their new versions go where Postgres finds
    # room, after the others, as rows of a busier table do on insert.
    db_session.execute(
        text(
            "UPDATE pharmcat.drug_recommendations SET drug_name = drug_name "
            "WHERE run_id = :run_id AND guideline_source = 'CPIC_GUIDELINE'"
        ),
        {"run_id": run_id},
    )
    db_session.flush()
    physical = [
        source
        for (source,) in db_session.execute(
            text(
                "SELECT guideline_source FROM pharmcat.drug_recommendations "
                "WHERE run_id = :run_id AND drug_name = 'atomoxetine'"
            ),
            {"run_id": run_id},
        )
    ]
    assert physical[-1] == "CPIC_GUIDELINE", "the table order should be disturbed"

    data = PharmCATDataService(db_session).get_pharmcat_data_by_run_id(run_id)
    expected = _pharmcat_order(report)
    assert _report_order(data) == expected
    # The case two runs of the NA12878 demo listed differently: a drug with
    # annotations from three sources or more (atomoxetine, pantoprazole).
    assert any(len({s for s, _ in v}) >= 3 for v in expected.values())


def test_each_drug_s_sources_are_listed_in_pharmcat_s_order():
    """In whatever order the rows arrive; within a source, the order given."""
    rows = [
        {
            "drug_name": "pantoprazole",
            "gene_symbol": "CYP2C19",
            "guideline_source": source,
            "drug_recommendation": f"{source} {n}",
            "classification": "Unspecified",
        }
        for source, n in (
            ("FDA_ASSOC", 1),
            ("DPWG_GUIDELINE", 1),
            ("FDA_LABEL", 1),
            ("CPIC_GUIDELINE", 1),
            ("DPWG_GUIDELINE", 2),
            ("CPIC_GUIDELINE", 2),
        )
    ]
    service = PharmCATDataService.__new__(PharmCATDataService)
    for given in (rows, rows[::-1]):
        (drug,) = service._transform_drug_recommendations_for_reports(given)
        listed = [r["recommendation"] for r in drug["recommendations"]]
        sources = [r["guideline_source"] for r in drug["recommendations"]]
        assert sources == sorted(sources, key=guideline_source_rank)
        assert sources[0] == guideline_source_name("CPIC_GUIDELINE")
        for source in ("CPIC_GUIDELINE", "DPWG_GUIDELINE"):
            mine = [t for t in listed if t.startswith(source)]
            assert mine == [
                r["drug_recommendation"]
                for r in given
                if r["guideline_source"] == source
            ]
