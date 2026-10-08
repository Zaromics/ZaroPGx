"""A recommendation says which candidate it applies to when the call is unresolved.

An unphased file can leave several diplotypes that fit, and PharmCAT then writes
one recommendation per phenotype they could have: CPIC simvastatin for SLCO1B1
"Decreased Function" (*1/*15, *1/*45, *1/*47) and another for "Poor Function".
The report listed both under the same heading, which reads as two pieces of advice
for one patient. Each now says what it applies if; guidance that is the same
whatever the truth (FDA's simvastatin text) says nothing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import app.reports.generator as generator_module
from app.pharmcat.pharmcat_client import normalize_pharmcat_results
from app.pharmcat.report_json import annotation_conditions
from app.reports.generator import _applies_if, map_recommendations_for_template

EXAMPLE = (
    Path(__file__).resolve().parents[1] / "test_data" / "pharmcat.example.report.json"
)

DECREASED = ("Decreased Function", ["*1/*15", "*1/*45", "*1/*47"])
POOR = ("Poor Function", ["*15/*45", "*45/*45", "*45/*47"])


def _annotation(text, *genes):
    """A PharmCAT 3 annotation for (gene, phenotype, diplotypes) calls."""
    return {
        "drugRecommendation": text,
        "classification": "Strong",
        "lookupKey": [{gene: phenotype for gene, phenotype, _ in genes}],
        "genotypes": [
            {
                "diplotypes": [
                    {"gene": gene, "label": label, "phenotypes": [phenotype]}
                    for gene, phenotype, labels in genes
                    for label in labels
                ]
            }
        ],
        "dosingInformation": False,
        "alternateDrugAvailable": True,
        "otherPrescribingGuidance": False,
    }


def _simvastatin_report():
    """Simvastatin as PharmCAT reports it for an unphased SLCO1B1 *1/*15/*45/*47."""

    def drug(source, texts):
        return {
            "name": "simvastatin",
            "source": source,
            "guidelines": [
                {
                    "name": f"{source} simvastatin",
                    "annotations": [
                        _annotation(text, ("SLCO1B1", *call))
                        for text, call in zip(texts, (DECREASED, POOR))
                    ],
                }
            ],
        }

    report = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    report["drugs"] = {
        "CPIC Guideline Annotation": {
            "simvastatin": drug(
                "CPIC_GUIDELINE",
                ["Prescribe <=20 mg/day.", "Prescribe an alternative statin."],
            )
        },
        "FDA Label Annotation": {
            "simvastatin": drug("FDA_LABEL", ["Higher systemic exposure."] * 2)
        },
    }
    return report


def _mapped(report):
    data = normalize_pharmcat_results(report)["data"]
    return map_recommendations_for_template(data["drugRecommendations"])


def test_an_annotation_names_the_candidates_it_was_matched_on():
    annotation = _annotation("x", ("SLCO1B1", *DECREASED))
    assert annotation_conditions(annotation) == [("SLCO1B1", *DECREASED)]
    assert annotation_conditions(None) == []


def test_guidance_that_depends_on_the_candidate_says_which():
    labels = {
        (m["guideline_source"][:4], m["recommendation"]): m.get("applies_if")
        for m in _mapped(_simvastatin_report())
    }
    assert labels[("CPIC", "Prescribe <=20 mg/day.")] == (
        "SLCO1B1 Decreased Function (*1/*15, *1/*45, *1/*47)"
    )
    assert labels[("CPIC", "Prescribe an alternative statin.")] == (
        "SLCO1B1 Poor Function (*15/*45, *45/*45, *45/*47)"
    )
    # The same text for every candidate holds whatever the truth.
    assert labels[("FDA ", "Higher systemic exposure.")] is None


def test_a_resolved_call_carries_no_label():
    """PharmCAT's phased example: every gene called one way. DPWG gives one
    annotation per gene for azathioprine (TPMT, NUDT15); both hold at once."""
    report = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    mapped = _mapped(report)
    assert {"TPMT", "NUDT15"} <= {
        c[0] for m in mapped if m["drug"] == "azathioprine" for c in m["conditions"]
    }
    assert not any(m.get("applies_if") for m in mapped)


def test_only_the_gene_in_question_is_named():
    """DPWG phenytoin: one annotation for CYP2C9 (called), one per HLA-B candidate."""
    cyp2c9 = ("CYP2C9", "Intermediate Metabolizer", ["*1/*3"])
    report = _simvastatin_report()
    report["drugs"] = {
        "DPWG Guideline Annotation": {
            "phenytoin": {
                "name": "phenytoin",
                "source": "DPWG_GUIDELINE",
                "guidelines": [
                    {
                        "name": "DPWG phenytoin",
                        "annotations": [
                            _annotation("Reduce the dose.", cyp2c9),
                            _annotation(
                                "Avoid phenytoin.",
                                ("HLA-B", "*15:02 positive", ["*15:02/*44:02"]),
                            ),
                            _annotation(
                                "No action.",
                                ("HLA-B", "*15:02 negative", ["*44:02/*44:03"]),
                            ),
                        ],
                    }
                ],
            }
        }
    }
    labels = {m["recommendation"]: m.get("applies_if") for m in _mapped(report)}
    assert labels == {
        "Reduce the dose.": None,
        "Avoid phenytoin.": "HLA-B *15:02 positive (*15:02/*44:02)",
        "No action.": "HLA-B *15:02 negative (*44:02/*44:03)",
    }


def test_two_genes_are_named_together_only_when_any_mix_holds():
    normal = ("SLCO1B1", "Normal Function", ("*1/*1",))
    decreased = ("SLCO1B1", "Decreased Function", ("*1/*5",))
    im = ("CYP2C9", "Intermediate Metabolizer", ("*1/*3",))
    nm = ("CYP2C9", "Normal Metabolizer", ("*1/*1",))
    assert _applies_if([(normal, im)]) == (
        "SLCO1B1 Normal Function (*1/*1) and CYP2C9 Intermediate Metabolizer (*1/*3)"
    )
    assert _applies_if([(decreased, im), (normal, im)]) == (
        "SLCO1B1 Decreased Function or Normal Function (*1/*5, *1/*1)"
        " and CYP2C9 Intermediate Metabolizer (*1/*3)"
    )
    # Normal with intermediate, or decreased with normal: not normal with normal.
    assert _applies_if([(decreased, nm), (normal, im)]) == (
        "SLCO1B1 Decreased Function (*1/*5) and CYP2C9 Normal Metabolizer (*1/*1)"
        "; or SLCO1B1 Normal Function (*1/*1) and CYP2C9 Intermediate Metabolizer"
        " (*1/*3)"
    )


def test_many_candidates_are_counted_not_listed():
    many = ("CYP2B6", "Intermediate Metabolizer", tuple(f"*1/*{n}" for n in range(6)))
    assert _applies_if([(many,)]) == (
        "CYP2B6 Intermediate Metabolizer (*1/*0, *1/*1, *1/*2 or 3 more)"
    )


def _db_rows():
    """The database lane stores one row per annotation and gene."""
    rows = []
    for source in ("CPIC_GUIDELINE", "FDA_LABEL"):
        for text, call in zip(
            (
                ["Prescribe <=20 mg/day.", "Prescribe an alternative statin."]
                if source == "CPIC_GUIDELINE"
                else ["Higher systemic exposure."] * 2
            ),
            (DECREASED, POOR),
        ):
            rows.append(
                {
                    "drug_name": "simvastatin",
                    "gene_symbol": "SLCO1B1",
                    "drug_recommendation": text,
                    "classification": "Strong",
                    "guideline_source": source,
                    "recommendation_data": _annotation(text, ("SLCO1B1", *call)),
                }
            )
    return rows


def test_the_database_lane_labels_the_same():
    from app.services.pharmcat_data_service import PharmCATDataService

    service = PharmCATDataService.__new__(PharmCATDataService)
    grouped = service._transform_drug_recommendations_for_reports(_db_rows())
    labels = {
        m["recommendation"]: m.get("applies_if")
        for m in map_recommendations_for_template(grouped)
    }
    assert labels == {
        "Prescribe <=20 mg/day.": "SLCO1B1 Decreased Function (*1/*15, *1/*45, *1/*47)",
        "Prescribe an alternative statin.": (
            "SLCO1B1 Poor Function (*15/*45, *45/*45, *45/*47)"
        ),
        "Higher systemic exposure.": None,
    }


def _job(session):
    from app.api.db import Job
    from app.api.models import JobCreate
    from app.services.job_service import JobService

    job = JobService(session).create_job(
        JobCreate(workflow_type="genomic_analysis", name="applies-if")
    )
    session.commit()
    row = session.query(Job).filter(Job.id == job.id).first()
    row.job_metadata = {"file_analysis": {"file_type": "vcf"}, "workflow": {}}
    session.commit()
    return str(job.id)


def test_both_reports_print_the_label(session_factory, monkeypatch, tmp_path):
    """Through the database lane, as every report from an upload is built."""
    from app.services.pharmcat_data_service import PharmCATDataService

    service = PharmCATDataService.__new__(PharmCATDataService)
    grouped = service._transform_drug_recommendations_for_reports(_db_rows())
    monkeypatch.setattr(
        generator_module.PharmCATDataService,
        "get_pharmcat_data_for_workflow",
        lambda self, job: {
            "genes": [
                {
                    "gene": "SLCO1B1",
                    "diplotype": "*1/*15 or *1/*45",
                    "phenotype": "Decreased Function or Poor Function",
                }
            ],
            "drugRecommendations": grouped,
            "file_type": "vcf",
            "workflow": {},
        },
    )
    for key in list(generator_module.REPORT_CONFIG):
        if key.startswith(("write_", "show_")):
            monkeypatch.setitem(
                generator_module.REPORT_CONFIG,
                key,
                key in ("write_html", "write_interactive_html"),
            )
    session = session_factory()
    try:
        result = generator_module.generate_report(
            {"data": {"genes": [], "drugRecommendations": []}},
            str(tmp_path),
            {"id": "p1"},
            job_id=_job(session),
            db_session=session,
        )
    finally:
        session.rollback()
        session.close()
    for key in ("html_path", "interactive_html_path"):
        page = (tmp_path / result[key].split("/")[-1]).read_text(encoding="utf-8")
        shown = re.findall(r"<strong>Applies if:</strong> ([^<]*)</p>", page)
        assert sorted(shown) == [
            "SLCO1B1 Decreased Function (*1/*15, *1/*45, *1/*47)",
            "SLCO1B1 Poor Function (*15/*45, *45/*45, *45/*47)",
        ], key
        assert "holds only if the true call is among those it names" in page


def test_a_called_gene_is_left_out_of_a_two_gene_label():
    """CPIC fluvastatin rests on CYP2C9 and SLCO1B1 in each annotation. With
    CYP2C9 called one way, the label names SLCO1B1 alone: CYP2C9 holds for all."""
    cyp2c9 = ("CYP2C9", "Intermediate Metabolizer", ["*1/*3"])
    report = _simvastatin_report()
    report["drugs"] = {
        "CPIC Guideline Annotation": {
            "fluvastatin": {
                "name": "fluvastatin",
                "source": "CPIC_GUIDELINE",
                "guidelines": [
                    {
                        "name": "CPIC fluvastatin",
                        "annotations": [
                            _annotation(
                                "Prescribe <=20 mg.", cyp2c9, ("SLCO1B1", *DECREASED)
                            ),
                            _annotation(
                                "Use another statin.", cyp2c9, ("SLCO1B1", *POOR)
                            ),
                        ],
                    }
                ],
            }
        }
    }
    labels = {m["applies_if"] for m in _mapped(report)}
    assert labels == {
        "SLCO1B1 Decreased Function (*1/*15, *1/*45, *1/*47)",
        "SLCO1B1 Poor Function (*15/*45, *45/*45, *45/*47)",
    }
