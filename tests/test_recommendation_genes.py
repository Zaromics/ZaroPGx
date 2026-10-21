"""A recommendation is filed under every gene it rests on, not the first.

PharmCAT 3 writes an annotation's ``lookupKey`` as a list of dicts. Both report
lanes read it as a dict, missed, and fell back to the first diplotype's gene, so
CPIC's sertraline recommendation -- looked up by CYP2B6 *and* CYP2C19 -- was
headed "CYP2B6" alone. On 30x NA12878 that was 30 of 246 annotations (29 resting
on two genes, and CPIC warfarin on three).

The annotations below are NA12878's, from PharmCAT 3's report.json, trimmed.
"""

from __future__ import annotations

import re

from app.pharmcat.pharmcat_client import normalize_pharmcat_results
from app.pharmcat.report_json import annotation_genes


def _diplotype(gene, label, phenotype):
    return {"gene": gene, "label": label, "phenotypes": [phenotype]}


SERTRALINE = {
    "drugRecommendation": "Initiate therapy with recommended starting dose. "
    "Consider a slower titration schedule and lower maintenance dose.",
    "classification": "Moderate",
    "lookupKey": [
        {"CYP2B6": "Normal Metabolizer", "CYP2C19": "Intermediate Metabolizer"}
    ],
    "genotypes": [
        {
            "diplotypes": [
                _diplotype("CYP2B6", "*1/*1", "Normal Metabolizer"),
                _diplotype("CYP2C19", "*1/*2", "Intermediate Metabolizer"),
            ]
        }
    ],
    "dosingInformation": True,
    "alternateDrugAvailable": False,
    "otherPrescribingGuidance": False,
}
# CPIC warfarin: no lookupKey; a dosing algorithm over three genes.
WARFARIN = {
    "drugRecommendation": "Use the warfarin dosing algorithm.",
    "classification": "Strong",
    "lookupKey": [],
    "genotypes": [
        {
            "diplotypes": [
                _diplotype("CYP2C9", "*1/*2", "Intermediate Metabolizer"),
                _diplotype("CYP4F2", "*1/*1", ""),
                _diplotype(
                    "VKORC1", "rs9923231 reference (C)/rs9923231 variant (T)", ""
                ),
            ]
        }
    ],
}
REPORT = {
    "pharmcatVersion": "3.1.1",
    "genes": {},
    "drugs": {
        "CPIC Guideline Annotation": {
            name: {
                "name": name,
                "id": f"PA-{name}",
                "guidelines": [
                    {
                        "name": f"Annotation of CPIC Guideline for {name}",
                        "annotations": [a],
                    }
                ],
            }
            for name, a in (("sertraline", SERTRALINE), ("warfarin", WARFARIN))
        }
    },
}


def test_annotation_genes():
    assert annotation_genes(SERTRALINE) == ["CYP2B6", "CYP2C19"]
    assert annotation_genes(WARFARIN) == ["CYP2C9", "CYP4F2", "VKORC1"]
    # PharmCAT 2's dict-shaped lookupKey.
    assert annotation_genes({"lookupKey": {"HLA-B": "*57:01 negative"}}) == ["HLA-B"]
    # lookupKey wins over genotypes: FDA fosphenytoin in PharmCAT's example is
    # looked up by CYP2C9 and HLA-B though its genotypes also carry CYP2C19.
    assert annotation_genes(
        {
            "lookupKey": {"CYP2C9": "2.0", "HLA-B": "*15:02 positive"},
            "genotypes": [{"diplotypes": [{"gene": "CYP2C19"}, {"gene": "CYP2C9"}]}],
        }
    ) == ["CYP2C9", "HLA-B"]
    assert annotation_genes({"phenotypes": {"TPMT": "Normal"}}) == ["TPMT"]
    assert annotation_genes(None) == [] and annotation_genes({}) == []


def test_the_json_lane_names_every_gene_once():
    data = normalize_pharmcat_results(REPORT)["data"]
    by_drug = {
        d["drug"]: d for d in data["drugRecommendations"] if "recommendations" in d
    }
    sertraline = by_drug["sertraline"]
    assert sorted(sertraline["genes"]) == ["CYP2B6", "CYP2C19"]
    # One entry for one recommendation, carrying both genes.
    assert len(sertraline["recommendations"]) == 1
    assert sertraline["recommendations"][0]["recommendation_genes"] == [
        "CYP2B6",
        "CYP2C19",
    ]
    assert by_drug["warfarin"]["recommendations"][0]["recommendation_genes"] == [
        "CYP2C9",
        "CYP4F2",
        "VKORC1",
    ]


def test_the_database_lane_names_every_gene():
    from app.services.pharmcat_data_service import PharmCATDataService

    service = PharmCATDataService.__new__(PharmCATDataService)
    grouped = service._transform_drug_recommendations_for_reports(
        [
            {
                "drug_name": "sertraline",
                # The column holds one gene: the first diplotype's.
                "gene_symbol": "CYP2B6",
                "drug_recommendation": SERTRALINE["drugRecommendation"],
                "classification": "Moderate",
                "recommendation_data": SERTRALINE,
            }
        ]
    )
    assert grouped[0]["genes"] == ["CYP2B6", "CYP2C19"]
    entry = grouped[0]["recommendations"]
    assert len(entry) == 1 and entry[0]["recommendation_genes"] == ["CYP2B6", "CYP2C19"]


def test_the_reports_head_the_recommendation_with_both_genes(monkeypatch, tmp_path):
    import app.reports.generator as generator_module

    for key in list(generator_module.REPORT_CONFIG):
        if key.startswith(("write_", "show_")):
            monkeypatch.setitem(
                generator_module.REPORT_CONFIG,
                key,
                key in ("write_html", "write_interactive_html"),
            )
    result = generator_module.generate_report(
        {"data": REPORT}, str(tmp_path), {"id": "p1"}, job_id=None
    )
    for key in ("html_path", "interactive_html_path"):
        page = (tmp_path / result[key].split("/")[-1]).read_text(encoding="utf-8")
        headings = [
            re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", h)).strip()
            for h in re.findall(r"<h4[^>]*>(.*?)</h4>", page, re.S)
        ]
        genes = [h.split(" - ")[0] for h in headings if " - " in h]
        assert "CYP2B6, CYP2C19" in genes, (key, headings)
        assert "CYP2C9, CYP4F2, VKORC1" in genes, (key, headings)


def test_a_guideline_without_recommendation_text_says_so():
    """CPIC's warfarin annotation has no text and no classification (its dosing is
    the guideline's flow chart). Its card read "See report for details", naming no
    report, and "Strength of Recommendation: Unknown"."""
    from app.reports.evidence import NO_RECOMMENDATION_TEXT
    from app.services.pharmcat_data_service import PharmCATDataService

    service = PharmCATDataService.__new__(PharmCATDataService)
    (drug,) = service._transform_drug_recommendations_for_reports(
        [
            {
                "drug_name": "warfarin",
                "gene_symbol": "CYP2C9",
                "drug_recommendation": None,
                "classification": None,
                "recommendation_data": None,
            }
        ]
    )
    (entry,) = drug["recommendations"]
    assert entry["recommendation"] == NO_RECOMMENDATION_TEXT
    assert "See report for details" not in entry["recommendation"]
    assert entry["evidence_class"] == "evidence-unclassified"
