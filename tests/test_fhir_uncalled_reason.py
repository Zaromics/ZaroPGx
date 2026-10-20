"""A genotype with no call says why, coded.

Since fix/fhir-no-empty-values every gene the run did not call had the genotype
valueCodeableConcept {"text": "Unknown"}, whether a tool reported the gene and could
not call it or no tool reported it at all (HLA-C from a VCF; F2 and F5 where PyPGx has
nothing for them). A reader could tell "not tested" from "tested, no call" only by
matching text, and not even that: both read "Unknown".

The IG's genotype profile (Genomics Reporting 3.0.0) requires value[x] (1..1), with an
example binding, and obs-6 forbids dataAbsentReason beside a value. So the reason goes
in the value, as a coding of HL7's DataAbsentReason code system: not-performed for a
gene no tool reported (the placeholder row app/reports/generator.py
_build_canonical_diplotypes adds, diplotype ""), unknown for one a tool reported but
could not call ("Unknown", "Unknown/Unknown"). HAPI FHIR 8.12's $validate, with the IG
and HL7 Terminology 6.1.0 loaded, checks the code (a bogus one is an error) and gave
the two release-candidate bundles rebuilt this way no errors.
"""

from __future__ import annotations

import json
import tarfile
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

from app.reports import generator as generator_module
from app.services.fhir import terminology
from app.services.fhir_export_service import FHIRExportService

REPO_ROOT = Path(__file__).resolve().parent.parent
TERMINOLOGY_PACKAGE = (
    REPO_ROOT / "docker" / "fhir-server" / "packages" / "hl7.terminology.r4-6.1.0.tgz"
)
DAR = "http://terminology.hl7.org/CodeSystem/data-absent-reason"

NOT_PERFORMED = {
    "coding": [{"system": DAR, "code": "not-performed", "display": "Not Performed"}],
    "text": "Not performed",
}


def _unknown(text: str) -> Dict[str, Any]:
    return {
        "coding": [{"system": DAR, "code": "unknown", "display": "Unknown"}],
        "text": text,
    }


# Rows as the report step builds them for the VCF run of PharmCAT's example VCF.
REPORTED_ROWS: List[Dict[str, Any]] = [
    # PharmCAT called it.
    {
        "gene": "CYP2C19",
        "diplotype": "*38/*38",
        "phenotype": "Normal Metabolizer",
        "call_source": "MATCHER",
    },
    # PharmCAT reported it, callSource NONE: "Unknown/Unknown".
    {
        "gene": "HLA-A",
        "diplotype": "Unknown/Unknown",
        "phenotype": "Unknown",
        "call_source": "NONE",
    },
    # PharmCAT reported it with no outside call to take.
    {"gene": "MT-RNR1", "diplotype": "Unknown", "call_source": "NONE"},
    # PyPGx reported it and made no call: the enrichment's "Unknown".
    {
        "gene": "ABCB1",
        "diplotype": "Unknown",
        "phenotype": None,
        "tool_source": "PyPGx",
        "pyPgxOnly": True,
        "no_call_note": "No genotype in the VCF at any of PyPGx's positions",
    },
    # One allele called is a call (generator.gene_was_called).
    {"gene": "CYP2D6", "diplotype": "*1/Unknown", "tool_source": "PyPGx"},
]


def _bundle(rows: List[Dict[str, Any]], run_id: str = "run-uncalled"):
    data = {
        "genes": generator_module._build_canonical_diplotypes(
            raw_gene_entries=rows, file_type="vcf", workflow_config=None
        ),
        "drugRecommendations": [],
    }
    result = FHIRExportService(MagicMock()).export_pgx_report(
        run_id=run_id, output_format="json", pharmcat_data=data
    )
    assert result["success"], result.get("error")
    return result["bundle"]


def _genotypes(bundle: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        entry["resource"]["code"]["text"].removesuffix(" Genotype"): entry["resource"]
        for entry in bundle["entry"]
        if entry["resource"]["resourceType"] == "Observation"
        and entry["resource"]["code"].get("text", "").endswith(" Genotype")
    }


def test_a_gene_no_tool_reported_is_coded_not_performed():
    genotypes = _genotypes(_bundle(REPORTED_ROWS))
    for gene in ("F2", "F5", "HLA-C"):
        assert genotypes[gene]["valueCodeableConcept"] == NOT_PERFORMED, gene


def test_a_gene_reported_but_not_called_is_coded_unknown_in_the_tools_words():
    genotypes = _genotypes(_bundle(REPORTED_ROWS))
    assert genotypes["HLA-A"]["valueCodeableConcept"] == _unknown("Unknown/Unknown")
    assert genotypes["MT-RNR1"]["valueCodeableConcept"] == _unknown("Unknown")
    assert genotypes["ABCB1"]["valueCodeableConcept"] == _unknown("Unknown")


def test_a_call_has_no_reason():
    genotypes = _genotypes(_bundle(REPORTED_ROWS))
    assert genotypes["CYP2C19"]["valueCodeableConcept"] == {"text": "*38/*38"}
    assert genotypes["CYP2D6"]["valueCodeableConcept"] == {"text": "*1/Unknown"}


def test_every_genotype_keeps_a_value_and_no_data_absent_reason():
    """value[x] 1..1 in the IG's genotype profile; obs-6 forbids dataAbsentReason
    beside it."""
    for gene, genotype in _genotypes(_bundle(REPORTED_ROWS)).items():
        assert genotype["valueCodeableConcept"]["text"].strip(), gene
        assert "dataAbsentReason" not in genotype, gene


def test_the_report_lists_a_gene_not_tested_as_such():
    report = _bundle(REPORTED_ROWS)["entry"][0]["resource"]
    displays = {ref["display"] for ref in report["result"]}
    assert {"F2 Not performed", "HLA-C Not performed", "ABCB1 Unknown"} <= displays


def test_the_ids_do_not_change_so_a_re_push_updates_in_place():
    """A genotype's id is named by the report and the gene, not by its value."""
    genotypes = _genotypes(_bundle(REPORTED_ROWS, run_id="run-ids"))
    for gene in ("F2", "HLA-A", "CYP2C19"):
        assert genotypes[gene]["id"] == FHIRExportService._stable_id(
            "genotype", "run-ids", gene
        )


def test_the_codes_are_hl7_terminologys_with_its_displays():
    """The codes and displays sent are those of the DataAbsentReason code system in
    the HL7 Terminology package the bundled FHIR server installs."""
    with tarfile.open(TERMINOLOGY_PACKAGE) as package:
        member = package.extractfile("package/CodeSystem-data-absent-reason.json")
        code_system = json.load(member)
    assert code_system["url"] == terminology.DATA_ABSENT_REASON == DAR

    def concepts(nodes):
        for node in nodes:
            yield node
            yield from concepts(node.get("concept", []))

    displays = {c["code"]: c["display"] for c in concepts(code_system["concept"])}
    for code, display in terminology.DATA_ABSENT_REASON_DISPLAYS.items():
        assert displays[code] == display


def test_the_xml_bundle_carries_the_coding():
    data = {
        "genes": generator_module._build_canonical_diplotypes(
            raw_gene_entries=REPORTED_ROWS, file_type="vcf", workflow_config=None
        ),
        "drugRecommendations": [],
    }
    xml = FHIRExportService(MagicMock()).export_pgx_report(
        run_id="run-uncalled", output_format="xml", pharmcat_data=data
    )["content"]
    assert f'<system value="{DAR}" />' in xml
    assert '<code value="not-performed" />' in xml
    assert '<code value="unknown" />' in xml
