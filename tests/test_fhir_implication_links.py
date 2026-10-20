"""A therapeutic implication derives from its own genes' genotypes, exactly.

The genotype was found by substring: "CYP3A4" is in "CYP3A43 *1/*1" and "F2" in
"CYP4F2 *1/*1", so a drug's implication pointed at whichever came first in the
gene list. And a drug none of whose genes had a genotype got "derivedFrom": [],
which FHIR JSON does not allow.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.services.fhir_export_service import FHIRExportService


def _implications(genes, drug_genes):
    data = {
        "pharmcat_version": "3.4.0",
        "genes": [{"gene": g, "diplotype": "*1/*1"} for g in genes],
        "drugRecommendations": [
            {"drug": f"drug-{n}", "genes": dg, "recommendations": []}
            for n, dg in enumerate(drug_genes)
        ],
    }
    result = FHIRExportService(MagicMock()).export_pgx_report(
        run_id="run-1", patient_info={"id": "p"}, pharmcat_data=data
    )
    return [
        e["resource"]
        for e in result["bundle"]["entry"]
        if e["resource"]["meta"]["profile"][0].endswith("therapeutic-implication")
    ]


def test_a_gene_is_not_matched_inside_a_longer_gene_name():
    cyp3a4, f2 = _implications(
        ["CYP3A43", "CYP3A4", "CYP4F2", "F2"], [["CYP3A4"], ["F2"]]
    )
    assert [d["display"] for d in cyp3a4["derivedFrom"]] == ["CYP3A4 *1/*1"]
    assert [d["display"] for d in f2["derivedFrom"]] == ["F2 *1/*1"]


def test_every_gene_of_a_drug_is_linked():
    (warfarin,) = _implications(
        ["CYP2C9", "CYP4F2", "VKORC1"], [["CYP2C9", "CYP4F2", "VKORC1"]]
    )
    assert [d["display"].split()[0] for d in warfarin["derivedFrom"]] == [
        "CYP2C9",
        "CYP4F2",
        "VKORC1",
    ]


def test_no_genotype_means_no_derived_from_not_an_empty_list():
    (implication,) = _implications(["CYP2D6"], [["CYP2C19"]])
    assert "derivedFrom" not in implication
