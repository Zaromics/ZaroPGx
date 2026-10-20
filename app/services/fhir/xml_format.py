"""FHIR R4 XML for the bundles ZaroPGx writes.

FHIR's XML is not a mechanical mirror of its JSON: a resource sits inside an
element named for its type (``<resource><Observation>``), every element's
children come in the order the specification defines, a primitive is a
``value`` attribute, and an element's id and an extension's url are attributes.
The converter that wrote ``<job>_pgx_fhir_report.xml`` before followed the JSON
and dropped the resource wrapper, so no FHIR parser could read the file (HAPI:
'Unknown resource name "id"'), and kept JSON's key order, which XML does not
allow.

The child order of every structure the export uses is in ``_ORDER_SOURCE``,
taken from the StructureDefinitions of hl7.fhir.r4.core 4.0.1: ``name`` is a
primitive, ``name:Type`` a complex child (a datatype, a backbone element's
path, or ``Resource``), ``name[x]`` a choice, whose type the JSON key's suffix
names (``valueQuantity``). A key the table does not know raises ``ValueError``
rather than being written out of order or dropped.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from typing import Any, Dict, Tuple

FHIR_NAMESPACE = "http://hl7.org/fhir"
XHTML_NAMESPACE = "http://www.w3.org/1999/xhtml"

# Generated from hl7.fhir.r4.core 4.0.1 (see the module docstring).
_ORDER_SOURCE = {
    "Bundle": (
        "id meta:Meta implicitRules language identifier:Identifier type timestamp "
        "total link:Bundle.link entry:Bundle.entry signature:Signature"
    ),
    "Bundle.link": "extension:Extension modifierExtension:Extension relation url",
    "Bundle.entry": (
        "extension:Extension modifierExtension:Extension link:Bundle.link fullUrl "
        "resource:Resource search:Bundle.entry.search "
        "request:Bundle.entry.request response:Bundle.entry.response"
    ),
    "Bundle.entry.search": "extension:Extension modifierExtension:Extension mode score",
    "Bundle.entry.request": (
        "extension:Extension modifierExtension:Extension method url ifNoneMatch "
        "ifModifiedSince ifMatch ifNoneExist"
    ),
    "Bundle.entry.response": (
        "extension:Extension modifierExtension:Extension status location etag "
        "lastModified outcome:Resource"
    ),
    "Patient": (
        "id meta:Meta implicitRules language text:Narrative contained:Resource "
        "extension:Extension modifierExtension:Extension identifier:Identifier "
        "active name:HumanName telecom:ContactPoint gender birthDate deceased[x] "
        "address:Address maritalStatus:CodeableConcept multipleBirth[x] "
        "photo:Attachment contact:Patient.contact "
        "communication:Patient.communication generalPractitioner:Reference "
        "managingOrganization:Reference link:Patient.link"
    ),
    "Patient.contact": (
        "extension:Extension modifierExtension:Extension "
        "relationship:CodeableConcept name:HumanName telecom:ContactPoint "
        "address:Address gender organization:Reference period:Period"
    ),
    "Patient.communication": (
        "extension:Extension modifierExtension:Extension language:CodeableConcept "
        "preferred"
    ),
    "Patient.link": (
        "extension:Extension modifierExtension:Extension other:Reference type"
    ),
    "Observation": (
        "id meta:Meta implicitRules language text:Narrative contained:Resource "
        "extension:Extension modifierExtension:Extension identifier:Identifier "
        "basedOn:Reference partOf:Reference status category:CodeableConcept "
        "code:CodeableConcept subject:Reference focus:Reference "
        "encounter:Reference effective[x] issued performer:Reference value[x] "
        "dataAbsentReason:CodeableConcept interpretation:CodeableConcept "
        "note:Annotation bodySite:CodeableConcept method:CodeableConcept "
        "specimen:Reference device:Reference "
        "referenceRange:Observation.referenceRange hasMember:Reference "
        "derivedFrom:Reference component:Observation.component"
    ),
    "Observation.referenceRange": (
        "extension:Extension modifierExtension:Extension low:Quantity "
        "high:Quantity type:CodeableConcept appliesTo:CodeableConcept age:Range "
        "text"
    ),
    "Observation.component": (
        "extension:Extension modifierExtension:Extension code:CodeableConcept "
        "value[x] dataAbsentReason:CodeableConcept interpretation:CodeableConcept "
        "referenceRange:Observation.referenceRange"
    ),
    "DiagnosticReport": (
        "id meta:Meta implicitRules language text:Narrative contained:Resource "
        "extension:Extension modifierExtension:Extension identifier:Identifier "
        "basedOn:Reference status category:CodeableConcept code:CodeableConcept "
        "subject:Reference encounter:Reference effective[x] issued "
        "performer:Reference resultsInterpreter:Reference specimen:Reference "
        "result:Reference imagingStudy:Reference media:DiagnosticReport.media "
        "conclusion conclusionCode:CodeableConcept presentedForm:Attachment"
    ),
    "DiagnosticReport.media": (
        "extension:Extension modifierExtension:Extension comment link:Reference"
    ),
    "Task": (
        "id meta:Meta implicitRules language text:Narrative contained:Resource "
        "extension:Extension modifierExtension:Extension identifier:Identifier "
        "instantiatesCanonical instantiatesUri basedOn:Reference "
        "groupIdentifier:Identifier partOf:Reference status "
        "statusReason:CodeableConcept businessStatus:CodeableConcept intent "
        "priority code:CodeableConcept description focus:Reference for:Reference "
        "encounter:Reference executionPeriod:Period authoredOn lastModified "
        "requester:Reference performerType:CodeableConcept owner:Reference "
        "location:Reference reasonCode:CodeableConcept reasonReference:Reference "
        "insurance:Reference note:Annotation relevantHistory:Reference "
        "restriction:Task.restriction input:Task.input output:Task.output"
    ),
    "Task.restriction": (
        "extension:Extension modifierExtension:Extension repetitions "
        "period:Period recipient:Reference"
    ),
    "Task.input": (
        "extension:Extension modifierExtension:Extension type:CodeableConcept "
        "value[x]"
    ),
    "Task.output": (
        "extension:Extension modifierExtension:Extension type:CodeableConcept "
        "value[x]"
    ),
    "Procedure": (
        "id meta:Meta implicitRules language text:Narrative contained:Resource "
        "extension:Extension modifierExtension:Extension identifier:Identifier "
        "instantiatesCanonical instantiatesUri basedOn:Reference partOf:Reference "
        "status statusReason:CodeableConcept category:CodeableConcept "
        "code:CodeableConcept subject:Reference encounter:Reference performed[x] "
        "recorder:Reference asserter:Reference performer:Procedure.performer "
        "location:Reference reasonCode:CodeableConcept reasonReference:Reference "
        "bodySite:CodeableConcept outcome:CodeableConcept report:Reference "
        "complication:CodeableConcept complicationDetail:Reference "
        "followUp:CodeableConcept note:Annotation "
        "focalDevice:Procedure.focalDevice usedReference:Reference "
        "usedCode:CodeableConcept"
    ),
    "Procedure.performer": (
        "extension:Extension modifierExtension:Extension function:CodeableConcept "
        "actor:Reference onBehalfOf:Reference"
    ),
    "Procedure.focalDevice": (
        "extension:Extension modifierExtension:Extension action:CodeableConcept "
        "manipulated:Reference"
    ),
    "Meta": (
        "extension:Extension versionId lastUpdated source profile security:Coding "
        "tag:Coding"
    ),
    "Narrative": "extension:Extension status div",
    "Extension": "extension:Extension value[x]",
    "Identifier": (
        "extension:Extension use type:CodeableConcept system value period:Period "
        "assigner:Reference"
    ),
    "CodeableConcept": "extension:Extension coding:Coding text",
    "Coding": "extension:Extension system version code display userSelected",
    "Reference": "extension:Extension reference type identifier:Identifier display",
    "Quantity": "extension:Extension value comparator unit system code",
    "Period": "extension:Extension start end",
    "Range": "extension:Extension low:Quantity high:Quantity",
    "Ratio": "extension:Extension numerator:Quantity denominator:Quantity",
    "Annotation": "extension:Extension author[x] time text",
    "Attachment": (
        "extension:Extension contentType language data url size hash title " "creation"
    ),
    "HumanName": (
        "extension:Extension use text family given prefix suffix period:Period"
    ),
    "ContactPoint": "extension:Extension system value use rank period:Period",
    "Address": (
        "extension:Extension use type text line city district state postalCode "
        "country period:Period"
    ),
    "RelatedArtifact": (
        "extension:Extension type label display citation url document:Attachment "
        "resource"
    ),
}

_PRIMITIVE_TYPES = frozenset(
    {
        "base64Binary",
        "boolean",
        "canonical",
        "code",
        "date",
        "dateTime",
        "decimal",
        "id",
        "instant",
        "integer",
        "markdown",
        "oid",
        "positiveInt",
        "string",
        "time",
        "unsignedInt",
        "uri",
        "url",
        "uuid",
    }
)


def _parse_order(source: str) -> Tuple[Tuple[str, str], ...]:
    """("name", type) pairs; type is "" for a primitive, "[x]" for a choice."""
    children = []
    for token in source.split():
        name, _, typ = token.partition(":")
        if name.endswith("[x]"):
            children.append((name[:-3], "[x]"))
        else:
            children.append((name, typ))
    return tuple(children)


_ORDER: Dict[str, Tuple[Tuple[str, str], ...]] = {
    structure: _parse_order(source) for structure, source in _ORDER_SOURCE.items()
}


def bundle_to_xml(bundle: Dict[str, Any]) -> str:
    """Serialize a FHIR R4 resource (the export's Bundle) as FHIR XML."""
    root = _resource_element(bundle, None)
    root.set("xmlns", FHIR_NAMESPACE)
    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{body}\n'


def _resource_element(resource: Dict[str, Any], parent) -> ET.Element:
    resource_type = resource.get("resourceType")
    if resource_type not in _ORDER:
        raise ValueError(f"no FHIR XML element order for resource {resource_type!r}")
    element = (
        ET.Element(resource_type)
        if parent is None
        else ET.SubElement(parent, resource_type)
    )
    body = {k: v for k, v in resource.items() if k != "resourceType"}
    _write_children(element, body, resource_type)
    return element


def _write_children(element: ET.Element, data: Dict[str, Any], structure: str) -> None:
    order = _ORDER.get(structure)
    if order is None:
        raise ValueError(f"no FHIR XML element order for {structure!r}")
    # Element ids are attributes; Extension.url is too.
    if "id" in data and not any(name == "id" for name, _ in order):
        element.set("id", str(data["id"]))
    if structure == "Extension":
        element.set("url", data["url"])
    handled = {"id", "url"} if structure == "Extension" else {"id"}
    for name, typ in order:
        if typ == "[x]":
            for key, value in data.items():
                suffix = key[len(name) :]
                if key.startswith(name) and suffix[:1].isupper():
                    _write_value(element, key, value, _choice_type(suffix))
                    handled.add(key)
            continue
        if name in data:
            _write_value(element, name, data[name], typ)
            handled.add(name)
    unknown = set(data) - handled
    if unknown:
        raise ValueError(f"{structure} has no FHIR element {sorted(unknown)}")


def _choice_type(suffix: str) -> str:
    """valueString -> string, valueCodeableConcept -> CodeableConcept."""
    primitive = suffix[:1].lower() + suffix[1:]
    return primitive if primitive in _PRIMITIVE_TYPES else suffix


def _write_value(parent: ET.Element, name: str, value: Any, typ: str) -> None:
    for item in value if isinstance(value, list) else [value]:
        if item is None:
            continue
        if typ in ("", *_PRIMITIVE_TYPES):
            ET.SubElement(parent, name, value=_primitive_text(item))
        elif typ == "Resource":
            _resource_element(item, ET.SubElement(parent, name))
        elif typ == "Narrative":
            _write_narrative(ET.SubElement(parent, name), item)
        else:
            _write_children(ET.SubElement(parent, name), item, typ)


def _write_narrative(element: ET.Element, narrative: Dict[str, Any]) -> None:
    ET.SubElement(element, "status", value=narrative["status"])
    div = ET.fromstring(narrative["div"])
    # ElementTree spells the namespace out in the tag; keep XHTML's own default.
    for node in div.iter():
        if node.tag.startswith("{" + XHTML_NAMESPACE + "}"):
            node.tag = node.tag[len(XHTML_NAMESPACE) + 2 :]
    div.set("xmlns", XHTML_NAMESPACE)
    element.append(div)


def _primitive_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    return str(value)
