"""Each gene's phenotype is a therapeutic implication, coded the IG's way.

The Genomics Reporting IG 3.0.0 states a pharmacogenomic phenotype in the
"therapeutic-implication" component of a therapeutic-implication observation
derived from the genotype, coded from genetic-therapeutic-implications-vs (LOINC
answers). The bundle carried it as a text component of the genotype, which the
genotype profile has no place for. A phenotype that value set has no equivalent
for stays text: no code is invented or stretched to fit.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.fhir_export_service import FHIRExportService

GR = "http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/"
TBD = "http://hl7.org/fhir/uv/genomics-reporting/CodeSystem/tbd-codes-cs"

# Every phenotype ZaroPGx is known to report, and the LOINC answer it is sent with
# (None: text only). docs/samples/README.md has the same table.
MAPPING = [
    ("Ultrarapid Metabolizer", "LA10315-2"),
    ("Rapid Metabolizer", "LA25390-8"),
    ("Normal Metabolizer", "LA25391-6"),
    ("Intermediate Metabolizer", "LA10317-8"),
    ("Poor Metabolizer", "LA9657-3"),
    ("Likely Intermediate Metabolizer", None),
    ("Possible Intermediate Metabolizer", None),
    ("Likely Poor Metabolizer", None),
    ("Increased Function", "LA25392-4"),
    ("Normal Function", "LA25393-2"),
    ("Decreased Function", "LA25395-7"),
    ("Possible Decreased Function", None),
    ("Poor Function", "LA25394-0"),
    ("Indeterminate", None),
    ("No Result", None),
    ("Normal", None),  # G6PD's activity terms
    ("Deficient", None),
    ("Deficient with CNSHA", None),
    ("Variable", None),
    ("*15:02 positive", "LA19541-4"),
    ("*57:01 negative", "LA19542-2"),
    ("*58:01 negative", "LA19542-2"),
    ("*31:01 positive", "LA19541-4"),
    ("increased risk of aminoglycoside-induced hearing loss", "LA19541-4"),
    ("normal risk of aminoglycoside-induced hearing loss", "LA19542-2"),
    ("uncertain risk of aminoglycoside-induced hearing loss", None),
    ("Malignant Hyperthermia Susceptible", "LA19541-4"),
    ("Uncertain Susceptibility", None),
    ("ivacaftor responsive in CF patients", "LA6677-4"),
    ("ivacaftor non-responsive in CF patients", None),
    ("Likely Wild Type", None),
    ("Possibly Wild Type", None),
    ("Normal Metabolizer or Intermediate Metabolizer", None),
    ("  poor   METABOLIZER ", "LA9657-3"),
]

DISPLAYS = {
    "LA10315-2": "Ultrarapid metabolizer",
    "LA25390-8": "Rapid metabolizer",
    "LA25391-6": "Normal metabolizer",
    "LA10317-8": "Intermediate metabolizer",
    "LA9657-3": "Poor metabolizer",
    "LA25392-4": "Increased function",
    "LA25393-2": "Normal function",
    "LA25395-7": "Decreased function",
    "LA25394-0": "Poor function",
    "LA6677-4": "Responsive",
    "LA19541-4": "High risk",
    "LA19542-2": "Low risk",
}


@pytest.mark.parametrize("phenotype, code", MAPPING)
def test_each_phenotype_is_coded_only_where_the_ig_has_its_equivalent(phenotype, code):
    concept = FHIRExportService._phenotype_concept(phenotype)
    assert concept["text"] == phenotype.strip()
    if code is None:
        assert concept == {"text": phenotype.strip()}
    else:
        assert concept["coding"] == [
            {"system": "http://loinc.org", "code": code, "display": DISPLAYS[code]}
        ]


# PharmCAT gives HLA-B one result per risk allele it screens, always *15:02, *57:01,
# *58:01; ZaroPGx states them joined with ", " (report_json.diplotype_phenotype).
# They hold at once: any positive is High risk, and Low risk needs every one
# negative. By the first alone, a *57:01 or *58:01 carrier would be Low risk.
HLA_RESULTS = [
    ("*31:01 negative", "LA19542-2"),  # HLA-A: one result
    ("*15:02 negative, *57:01 negative, *58:01 negative", "LA19542-2"),
    ("*15:02 negative, *57:01 positive, *58:01 negative", "LA19541-4"),
    ("*15:02 negative, *57:01 negative, *58:01 positive", "LA19541-4"),
    ("*15:02 positive, *57:01 negative, *58:01 negative", "LA19541-4"),
    ("*15:02 POSITIVE,*57:01  negative , *58:01 negative", "LA19541-4"),
]


@pytest.mark.parametrize("phenotype, code", HLA_RESULTS)
def test_an_hla_phenotype_is_high_risk_if_any_result_is_positive(phenotype, code):
    concept = FHIRExportService._phenotype_concept(phenotype)
    assert concept == {
        "coding": [
            {"system": "http://loinc.org", "code": code, "display": DISPLAYS[code]}
        ],
        "text": phenotype,
    }


# A part that is not an allele result: the phenotype is not what the rule reads,
# so it is not coded, whatever the other parts say.
NOT_HLA_RESULTS = [
    "*15:02 negative, *57:01 positive, *58:01 unknown",
    "*15:02 negative, *57:01 negative, *58:01",
    "*15:02 negative, , *58:01 negative",
    "*15:02 negative, *57:01 positive,",
    "*15:02 negative, *57:01 positive or *57:01 negative, *58:01 negative",
    "HLA-B*57:01 positive",
    ",",
    "",
]


@pytest.mark.parametrize("phenotype", NOT_HLA_RESULTS)
def test_an_hla_phenotype_with_a_part_that_is_not_a_result_stays_text(phenotype):
    assert FHIRExportService._phenotype_concept(phenotype) == {"text": phenotype}


def test_an_hla_b_carrier_is_high_risk_in_the_bundle():
    bundle = _bundle(
        [
            {
                "gene": "HLA-B",
                "diplotype": "*08:01/*57:01",
                "phenotype": "*15:02 negative, *57:01 positive, *58:01 negative",
            }
        ]
    )
    (phenotype,) = [
        e["resource"]
        for e in _by_profile(bundle, "therapeutic-implication")
        if e["resource"]["code"].get("text") == "HLA-B phenotype"
    ]
    assert phenotype["component"][0]["valueCodeableConcept"] == {
        "coding": [
            {"system": "http://loinc.org", "code": "LA19541-4", "display": "High risk"}
        ],
        "text": "*15:02 negative, *57:01 positive, *58:01 negative",
    }


def _bundle(genes):
    data = {
        "pharmcat_version": "3.4.0",
        "genes": genes,
        "drugRecommendations": [
            {
                "drug": "codeine",
                "genes": ["CYP2D6"],
                "recommendations": [{"recommendation": "Avoid codeine."}],
            }
        ],
    }
    result = FHIRExportService(MagicMock()).export_pgx_report(
        run_id="run-1", patient_info={"id": "p"}, pharmcat_data=data
    )
    assert result["success"], result
    return result["bundle"]


GENES = [
    {"gene": "CYP2D6", "diplotype": "*4/*4", "phenotype": "Poor Metabolizer"},
    {"gene": "CYP1A2", "diplotype": "*1/*1", "phenotype": "Likely Wild Type"},
    {"gene": "VKORC1", "diplotype": "-1639 GG", "phenotype": "N/A"},
    {"gene": "XPC", "diplotype": "*1/*1", "phenotype": "Unknown"},
]


def _by_profile(bundle, profile):
    return [
        e
        for e in bundle["entry"]
        if e["resource"].get("meta", {}).get("profile") == [GR + profile]
    ]


def test_the_genotype_no_longer_carries_the_phenotype():
    for entry in _by_profile(_bundle(GENES), "genotype"):
        codes = [c["code"] for c in entry["resource"]["component"]]
        assert {"text": "Phenotype"} not in codes


def test_each_stated_phenotype_is_an_implication_of_its_genotype():
    bundle = _bundle(GENES)
    genotypes = {
        e["resource"]["code"]["text"].split()[0]: e["fullUrl"]
        for e in _by_profile(bundle, "genotype")
    }
    phenotypes = [
        e["resource"]
        for e in _by_profile(bundle, "therapeutic-implication")
        if not any(
            c["code"].get("coding", [{}])[0].get("code") == "51963-7"
            for c in e["resource"]["component"]
        )
    ]
    # N/A (the tool defines none) and Unknown (none reported): nothing to state.
    assert [p["code"]["text"] for p in phenotypes] == [
        "CYP2D6 phenotype",
        "CYP1A2 phenotype",
    ]
    cyp2d6, cyp1a2 = phenotypes
    assert cyp2d6["derivedFrom"] == [
        {"reference": genotypes["CYP2D6"], "display": "CYP2D6 *4/*4"}
    ]
    assert cyp2d6["code"]["coding"] == [
        {
            "system": TBD,
            "code": "therapeutic-implication",
            "display": "Therapeutic Implication",
        }
    ]
    (component,) = cyp2d6["component"]
    assert component["code"]["coding"][0]["code"] == "therapeutic-implication"
    assert component["valueCodeableConcept"] == {
        "coding": [
            {
                "system": "http://loinc.org",
                "code": "LA9657-3",
                "display": "Poor metabolizer",
            }
        ],
        "text": "Poor Metabolizer",
    }
    assert cyp1a2["component"][0]["valueCodeableConcept"] == {
        "text": "Likely Wild Type"
    }
    # The implication profile forbids a value: the phenotype is the component.
    assert not any(key.startswith("value") for key in cyp2d6)


def test_the_report_lists_the_phenotypes_between_genotypes_and_drugs():
    bundle = _bundle(GENES)
    report = bundle["entry"][0]["resource"]
    assert [r["display"] for r in report["result"]] == [
        "CYP2D6 *4/*4",
        "CYP1A2 *1/*1",
        "VKORC1 -1639 GG",
        "XPC *1/*1",
        "CYP2D6 Poor Metabolizer",
        "CYP1A2 Likely Wild Type",
        "Therapeutic implication for codeine",
    ]


def test_the_drug_implication_is_unchanged():
    bundle = _bundle(GENES)
    (codeine,) = [
        e["resource"]
        for e in _by_profile(bundle, "therapeutic-implication")
        if e["resource"]["code"].get("text") is None
    ]
    assert [c["code"]["coding"][0]["code"] for c in codeine["component"]] == [
        "51963-7",
        "conclusion-string",
    ]
    assert [d["display"] for d in codeine["derivedFrom"]] == ["CYP2D6 *4/*4"]
