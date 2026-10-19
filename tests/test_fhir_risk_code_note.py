"""A phenotype coded High or Low risk says that the code is CPIC's priority.

The IG's genetic-therapeutic-implications value set has no answer for an HLA risk
allele found or not, MT-RNR1's risk of hearing loss or a malignant hyperthermia
susceptibility. ZaroPGx codes them LOINC "High risk" or "Low risk" as CPIC's EHR
priority term for the result reads ("Abnormal/Priority/High Risk",
"Normal/Routine/Low Risk"). The user's decision (2026-10-19): keep them coded for
now, and tell the reader. Each such observation carries a note naming the call as
ZaroPGx stated it and the CPIC term its code follows; no other phenotype has one,
since its code says the same thing as the call. The mapping is provisional
(docs/samples/README.md).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from unittest.mock import MagicMock

import pytest

from app.services.fhir_export_service import FHIRExportService

NS = "{http://hl7.org/fhir}"

HIGH = ("LA19541-4", "High risk", "Abnormal/Priority/High Risk")
LOW = ("LA19542-2", "Low risk", "Normal/Routine/Low Risk")

# The phenotypes coded after CPIC's EHR priority, as docs/samples/README.md lists them.
RISK_CODED = [
    ("*15:02 positive", HIGH),
    ("*31:01 positive", HIGH),
    ("*57:01 negative", LOW),
    ("*58:01 negative", LOW),
    ("increased risk of aminoglycoside-induced hearing loss", HIGH),
    ("normal risk of aminoglycoside-induced hearing loss", LOW),
    ("Malignant Hyperthermia Susceptible", HIGH),
]

# Coded with a term that states the call itself, or not coded at all.
NOT_RISK_CODED = [
    "Poor Metabolizer",
    "Decreased Function",
    "ivacaftor responsive in CF patients",
    "uncertain risk of aminoglycoside-induced hearing loss",
    "Uncertain Susceptibility",
    "Likely Wild Type",
    "Deficient",
    "*15:02 negative, *57:01 positive, *58:01 unknown",  # not read: text
]


def _phenotype(phenotype):
    return FHIRExportService(MagicMock())._create_phenotype_implication(
        gene_data={"gene": "GENE", "diplotype": "*1/*1", "phenotype": phenotype},
        patient_ref="urn:uuid:p",
        genotype_ref={"reference": "urn:uuid:g", "display": "GENE *1/*1"},
        resource_id="id-1",
    )


@pytest.mark.parametrize("phenotype, risk", RISK_CODED)
def test_a_risk_coded_after_cpic_priority_says_so(phenotype, risk):
    code, display, priority = risk
    observation = _phenotype(phenotype)
    value = observation["component"][0]["valueCodeableConcept"]
    assert value["coding"][0]["code"] == code
    assert observation["note"] == [
        {
            "text": (
                f'ZaroPGx called this phenotype "{phenotype}". It is coded LOINC '
                f'{code} "{display}" after CPIC\'s EHR priority term for that '
                f'result, "{priority}", as the IG\'s value set has no code for the '
                "call itself."
            )
        }
    ]


# HLA-B states one result per risk allele it screens, joined with ", " (#124 codes
# it High risk if any is positive, Low risk only if all are negative). "That
# result" would be wrong for three: High risk follows CPIC's term for a positive
# one among them, Low risk its term for negative results.
SEVERAL_RESULTS = [
    ("*15:02 negative, *57:01 positive, *58:01 negative", HIGH, "a positive result"),
    ("*15:02 positive, *57:01 positive, *58:01 negative", HIGH, "a positive result"),
    ("*15:02 negative, *57:01 negative, *58:01 positive", HIGH, "a positive result"),
    ("*15:02 negative, *57:01 negative, *58:01 negative", LOW, "negative results"),
]


@pytest.mark.parametrize("phenotype, risk, basis", SEVERAL_RESULTS)
def test_a_note_on_several_results_names_the_result_its_code_follows(
    phenotype, risk, basis
):
    code, display, priority = risk
    observation = _phenotype(phenotype)
    value = observation["component"][0]["valueCodeableConcept"]
    assert value["coding"][0]["code"] == code
    assert value["text"] == phenotype
    assert observation["note"] == [
        {
            "text": (
                f'ZaroPGx called this phenotype "{phenotype}". It is coded LOINC '
                f'{code} "{display}" after CPIC\'s EHR priority term for {basis}, '
                f'"{priority}", as the IG\'s value set has no code for the call '
                "itself."
            )
        }
    ]


@pytest.mark.parametrize("phenotype", NOT_RISK_CODED)
def test_no_other_phenotype_has_a_note(phenotype):
    assert "note" not in _phenotype(phenotype)


def test_the_note_names_the_call_as_reported_not_as_matched():
    # The match ignores case and spacing; the note quotes ZaroPGx's words.
    observation = _phenotype("  Malignant  Hyperthermia Susceptible ")
    assert observation["note"][0]["text"].startswith(
        'ZaroPGx called this phenotype "Malignant  Hyperthermia Susceptible".'
    )


DATA = {
    "pharmcat_version": "3.4.0",
    "genes": [
        {"gene": "HLA-B", "diplotype": "*15:02/*57:01", "phenotype": "*15:02 positive"},
        {"gene": "CYP2D6", "diplotype": "*4/*4", "phenotype": "Poor Metabolizer"},
    ],
    "drugRecommendations": [
        {
            "drug": "carbamazepine",
            "genes": ["HLA-B"],
            "recommendations": [{"recommendation": "Avoid carbamazepine."}],
        }
    ],
}


def _export(output_format):
    result = FHIRExportService(MagicMock()).export_pgx_report(
        run_id="run-1",
        patient_info={"id": "p"},
        pharmcat_data=DATA,
        output_format=output_format,
    )
    assert result["success"], result
    return result


def test_in_the_bundle_only_the_risk_coded_phenotype_has_the_note():
    observations = {
        e["resource"]["code"].get("text"): e["resource"]
        for e in _export("json")["bundle"]["entry"]
        if e["resource"]["resourceType"] == "Observation"
    }
    assert "Abnormal/Priority/High Risk" in (
        observations["HLA-B phenotype"]["note"][0]["text"]
    )
    assert "note" not in observations["CYP2D6 phenotype"]
    # The drug's implication and the genotypes have none either.
    assert [text for text, obs in observations.items() if "note" in obs] == [
        "HLA-B phenotype"
    ]


def test_an_hla_b_carrier_in_the_bundle_is_high_risk_for_its_positive_result():
    data = {
        **DATA,
        "genes": [
            {
                "gene": "HLA-B",
                "diplotype": "*08:01/*57:01",
                "phenotype": "*15:02 negative, *57:01 positive, *58:01 negative",
            }
        ],
    }
    result = FHIRExportService(MagicMock()).export_pgx_report(
        run_id="run-1", patient_info={"id": "p"}, pharmcat_data=data
    )
    assert result["success"], result
    (observation,) = [
        e["resource"]
        for e in result["bundle"]["entry"]
        if e["resource"].get("code", {}).get("text") == "HLA-B phenotype"
    ]
    assert observation["note"] == [
        {
            "text": (
                'ZaroPGx called this phenotype "*15:02 negative, *57:01 positive, '
                '*58:01 negative". It is coded LOINC LA19541-4 "High risk" after '
                "CPIC's EHR priority term for a positive result, "
                '"Abnormal/Priority/High Risk", as the IG\'s value set has no code '
                "for the call itself."
            )
        }
    ]


def test_the_xml_puts_the_note_where_fhir_wants_it():
    root = ET.fromstring(_export("xml")["content"])
    (observation,) = [
        obs
        for obs in root.iter(f"{NS}Observation")
        if obs.find(f"{NS}code/{NS}text[@value='HLA-B phenotype']") is not None
    ]
    children = [child.tag[len(NS) :] for child in observation]
    assert children.index("note") < children.index("derivedFrom")
    assert (
        observation.find(f"{NS}note/{NS}text")
        .get("value")
        .startswith('ZaroPGx called this phenotype "*15:02 positive".')
    )
