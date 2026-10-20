"""A drug's therapeutic implication cites its genes' phenotypes; it does not repeat them.

Each gene's phenotype is a therapeutic-implication observation of its own, derived
from the genotype. A drug's implication pointed only at the genotypes, so a reader
had to find the phenotype behind the drug's recommendation by gene. The IG's own
examples restate the phenotype in each drug's implication; the user's decision
(2026-10-19) was to point at it instead, so the bundle states each phenotype once.

The drug's implication now lists, in derivedFrom, after its genes' genotypes, the
phenotype implications of the same genes, matched exactly as the genotypes are
(CYP3A4 is not CYP3A43). The IG's therapeutic-implication profile slices derivedFrom
openly (variant, genotype, haplotype, biomarker) over any Observation, so such a
reference is allowed: HAPI 8.12 with the IG gives no error or warning for it, only
the information that it matches none of the named slices.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.services.fhir_export_service import FHIRExportService

GENES = [
    {"gene": "CYP3A43", "diplotype": "*1/*1", "phenotype": "Normal Function"},
    {"gene": "CYP3A4", "diplotype": "*1/*22", "phenotype": "Decreased Function"},
    {"gene": "CYP4F2", "diplotype": "*1/*3", "phenotype": "Decreased Function"},
    {"gene": "F2", "diplotype": "*1/*1", "phenotype": "Normal Function"},
    {"gene": "CYP2C9", "diplotype": "*1/*3", "phenotype": "Intermediate Metabolizer"},
    # The calling tool defines no phenotype for VKORC1: nothing to cite.
    {"gene": "VKORC1", "diplotype": "-1639 GA", "phenotype": "N/A"},
]

DRUGS = [
    {"drug": "tacrolimus", "genes": ["CYP3A4"]},
    {"drug": "anticoagulant", "genes": ["F2"]},
    {"drug": "warfarin", "genes": ["CYP2C9", "CYP4F2", "VKORC1"]},
    {"drug": "acenocoumarol", "genes": ["VKORC1"]},
]


def _bundle():
    data = {
        "pharmcat_version": "3.4.0",
        "genes": GENES,
        "drugRecommendations": [
            dict(d, recommendations=[{"recommendation": f"Advice for {d['drug']}."}])
            for d in DRUGS
        ],
    }
    result = FHIRExportService(MagicMock()).export_pgx_report(
        run_id="run-1", patient_info={"id": "p"}, pharmcat_data=data
    )
    assert result["success"], result
    return {e["fullUrl"]: e["resource"] for e in result["bundle"]["entry"]}


def _drug_implications(resources):
    return {
        r["component"][0]["valueCodeableConcept"]["text"]: r
        for r in resources.values()
        if r["resourceType"] == "Observation"
        and r["component"][0]["code"]["coding"][0].get("code") == "51963-7"
    }


def test_a_drug_cites_its_genes_phenotypes_after_their_genotypes():
    resources = _bundle()
    drugs = _drug_implications(resources)
    assert [d["display"] for d in drugs["warfarin"]["derivedFrom"]] == [
        "CYP2C9 *1/*3",
        "CYP4F2 *1/*3",
        "VKORC1 -1639 GA",
        "CYP2C9 phenotype",
        "CYP4F2 phenotype",
    ]
    # Each citation is the gene's phenotype implication in the bundle.
    for citation in drugs["warfarin"]["derivedFrom"][3:]:
        cited = resources[citation["reference"]]
        assert cited["code"]["text"] == citation["display"]
        assert cited["meta"]["profile"][0].endswith("/therapeutic-implication")


def test_the_phenotype_is_matched_by_the_exact_gene():
    drugs = _drug_implications(_bundle())
    assert [d["display"] for d in drugs["tacrolimus"]["derivedFrom"]] == [
        "CYP3A4 *1/*22",
        "CYP3A4 phenotype",
    ]
    assert [d["display"] for d in drugs["anticoagulant"]["derivedFrom"]] == [
        "F2 *1/*1",
        "F2 phenotype",
    ]


def test_a_gene_without_a_phenotype_is_cited_by_its_genotype_alone():
    drugs = _drug_implications(_bundle())
    assert [d["display"] for d in drugs["acenocoumarol"]["derivedFrom"]] == [
        "VKORC1 -1639 GA"
    ]


def test_the_drug_implication_does_not_restate_the_phenotype():
    resources = _bundle()
    for drug in _drug_implications(resources).values():
        assert [c["code"]["coding"][0]["code"] for c in drug["component"]] == [
            "51963-7",
            "conclusion-string",
        ]
        # Not in the citation's label either: the cited observation states it.
        for citation in drug["derivedFrom"]:
            assert "Function" not in citation["display"]
            assert "Metabolizer" not in citation["display"]
