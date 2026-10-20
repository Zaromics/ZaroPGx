"""The XML bundle is FHIR XML: resources wrapped, children in the spec's order.

The file written before followed the JSON: ``<resource>`` held the resource's
fields with no element naming its type, and children came in dict order. No FHIR
parser could read it (HAPI: 'Unknown resource name "id"'). Validated against the
Genomics Reporting IG, the XML of a 30x NA12878 job now gives exactly what its
JSON gives: no errors.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from unittest.mock import MagicMock

import pytest

from app.services.fhir import xml_format
from app.services.fhir_export_service import FHIRExportService

NS = "{http://hl7.org/fhir}"

DATA = {
    "pharmcat_version": "3.4.0",
    "total_genes": 1,
    "actionable_findings": [],
    "genes": [
        {
            "gene": "CYP2D6",
            "diplotype": "*1/*4",
            "phenotype": "Intermediate Metabolizer",
            "activity_score": "1.5",
            "allele1": "*1",
            "allele2": "*4",
        }
    ],
    "drugRecommendations": [
        {
            "drug": "codeine",
            "genes": ["CYP2D6"],
            "recommendations": [
                {
                    "recommendation": "Use label recommended dosing.",
                    "classification": "Moderate",
                    "guideline_source": "CPIC",
                }
            ],
        }
    ],
}


def _export(fmt):
    result = FHIRExportService(MagicMock()).export_pgx_report(
        run_id="run-1",
        patient_info={"id": "patient-1", "name": {"family": "Doe", "given": "Jane"}},
        pharmcat_data=DATA,
        output_format=fmt,
    )
    assert result["success"], result
    return result


@pytest.fixture
def exported():
    return _export("xml")


@pytest.fixture
def parsed(exported):
    xml_text = exported["content"]
    assert xml_text.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    return ET.fromstring(xml_text.encode("utf-8"))


def _tag(element):
    return element.tag.replace(NS, "")


def _position(structure, tag):
    """Where an element of this tag sits in the structure's definition."""
    order = xml_format._ORDER[structure]
    for index, (name, typ) in enumerate(order):
        if tag == name or (typ == "[x]" and tag.startswith(name)):
            return index
    raise AssertionError(f"{tag} is not an element of {structure}")


def _in_spec_order(element, structure):
    positions = [_position(structure, _tag(child)) for child in element]
    return positions == sorted(positions)


def test_every_resource_is_wrapped_in_its_type(parsed, exported):
    assert parsed.tag == NS + "Bundle"
    bundle = exported["bundle"]
    entries = parsed.findall(NS + "entry")
    assert len(entries) == len(bundle["entry"])
    for element, entry in zip(entries, bundle["entry"]):
        (resource,) = list(element.find(NS + "resource"))
        assert _tag(resource) == entry["resource"]["resourceType"]
        assert resource.find(NS + "id").get("value") == entry["resource"]["id"]


def test_children_follow_the_specification_order(parsed):
    """JSON put a DiagnosticReport's extensions last; XML needs them before
    identifier, and an Observation's category before its code, etc."""
    assert _in_spec_order(parsed, "Bundle")
    for entry in parsed.findall(NS + "entry"):
        assert _in_spec_order(entry, "Bundle.entry")
        (resource,) = list(entry.find(NS + "resource"))
        assert _in_spec_order(resource, _tag(resource)), _tag(resource)
        for component in resource.findall(NS + "component"):
            assert _in_spec_order(component, "Observation.component")
    report = parsed.find(f"{NS}entry/{NS}resource/{NS}DiagnosticReport")
    tags = [_tag(c) for c in report]
    assert tags.index("extension") < tags.index("identifier")


def test_primitives_extensions_and_choices(parsed):
    genotype = next(
        o
        for o in parsed.iter(NS + "Observation")
        if o.find(f"{NS}code/{NS}coding/{NS}code").get("value") == "84413-4"
    )
    assert genotype.find(f"{NS}valueCodeableConcept/{NS}text").get("value") == "*1/*4"
    score = [
        c
        for c in genotype.findall(NS + "component")
        if c.find(NS + "valueQuantity") is not None
    ]
    assert score[0].find(f"{NS}valueQuantity/{NS}value").get("value") == "1.5"

    report = parsed.find(f"{NS}entry/{NS}resource/{NS}DiagnosticReport")
    extension = report.find(NS + "extension")
    assert extension.get("url").endswith("genomic-study-reference")
    assert extension.find(f"{NS}valueReference/{NS}reference") is not None

    patient = next(parsed.iter(NS + "Patient"))
    assert patient.find(f"{NS}name/{NS}given").get("value") == "Jane"


def test_booleans_and_numbers_are_written_as_fhir_values():
    xml_text = xml_format.bundle_to_xml(
        {
            "resourceType": "Patient",
            "active": True,
            "multipleBirthInteger": 2,
        }
    )
    root = ET.fromstring(xml_text.encode("utf-8"))
    assert root.find(NS + "active").get("value") == "true"
    assert root.find(NS + "multipleBirthInteger").get("value") == "2"


def test_an_unknown_element_is_an_error_not_a_silent_drop():
    with pytest.raises(ValueError, match="notAnElement"):
        xml_format.bundle_to_xml({"resourceType": "Patient", "notAnElement": 1})
    with pytest.raises(ValueError, match="Medication"):
        xml_format.bundle_to_xml({"resourceType": "Medication"})
