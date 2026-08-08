"""FHIR export layer: terminology single-sourcing (370).

Item 370 moves every coding-system / profile / extension URI out of the resource
builders in ``app.services.fhir_export_service`` and into
``app.services.fhir.terminology``. That move must be *pure*: the emitted bundle
has to stay byte-identical.

The golden digests below were captured from the pre-refactor service at
``f4e1bb6`` (the commit immediately before the terminology module existed) with
``uuid``/``datetime`` frozen, so the byte comparison really is "before vs after"
and not just "after vs after".

Everything here runs against in-memory fixtures: no FHIR server, no database, no
network.
"""

from __future__ import annotations

import ast
import hashlib
import itertools
import uuid as uuid_module
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

from app.services import fhir_export_service as fhir_service_module
from app.services.fhir_export_service import FHIRExportService

# ---------------------------------------------------------------------------
# Deterministic bundle fixture
# ---------------------------------------------------------------------------

# Chosen to touch every terminology-bearing branch of the builders: a gene with
# its own LOINC code plus activity score and both alleles, a gene that falls back
# to the generic LOINC code with an unusable activity score and no alleles, a
# gene with only one allele, a drug with two recommendations (one carrying a
# classification, one not) and a drug with none at all.
PHARMCAT_FIXTURE: Dict[str, Any] = {
    "run_id": "run-370",
    "pharmcat_version": "3.4.0",
    "total_genes": 3,
    "actionable_findings": [{"gene": "CYP2C19"}, {"gene": "DPYD"}],
    "genes": [
        {
            "gene": "CYP2D6",
            "diplotype": "*1/*4",
            "phenotype": "Intermediate Metabolizer",
            "activity_score": "1.0",
            "allele1": "*1",
            "allele2": "*4",
        },
        {
            "gene": "ABCG2",
            "diplotype": "rs2231142 reference (G)/rs2231142 reference (G)",
            "phenotype": "Unknown",
            "activity_score": "n/a",
        },
        {
            "gene": "CYP2C19",
            "diplotype": "*1/*2",
            "phenotype": "Intermediate Metabolizer",
            "activity_score": None,
            "allele1": "*1",
        },
    ],
    "drugRecommendations": [
        {
            "drug": "clopidogrel",
            "genes": ["CYP2C19"],
            "recommendations": [
                {
                    "recommendation": "Alternative antiplatelet therapy recommended.",
                    "classification": "Strong",
                    "guideline_source": "CPIC",
                },
                {
                    "recommendation": "Consider prasugrel or ticagrelor.",
                    "classification": "",
                    "guideline_source": "DPWG",
                },
            ],
        },
        {
            "drug": "codeine",
            "genes": ["CYP2D6"],
            "recommendations": [],
        },
    ],
}

PATIENT_FIXTURE: Dict[str, Any] = {
    "id": "patient-370",
    "name": {"family": "Doe", "given": ["Jane"]},
    "gender": "female",
    "birthDate": "1980-01-01",
}

# sha256 of ``result["content"].encode("utf-8")`` produced by the pre-refactor
# service for the fixture above.
GOLDEN_JSON_SHA256 = "9e78058145ab06a5e6205bffd9bda78eb38ccd5685557dfcb183199ad9a0fec7"
GOLDEN_JSON_BYTES = 22060
GOLDEN_XML_SHA256 = "aeef0c133219167d65f60b994edb0c0d83ec4643e4c641cba1e6a520cb839c27"
GOLDEN_XML_BYTES = 20141

# Every terminology URI the pre-refactor bundle emitted, with its multiplicity.
# Redundant with the digests above, but it fails readably: a wrong constant shows
# up here as a named URI rather than as an opaque hash mismatch.
GOLDEN_URI_CENSUS = {
    "http://hl7.org/fhir/StructureDefinition/Patient": 1,
    "http://hl7.org/fhir/StructureDefinition/workflow-relatedArtifact": 1,
    "http://hl7.org/fhir/uv/genomics-reporting/CodeSystem/tbd-codes-cs": 10,
    "http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/genomic-report": 1,
    "http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/genomic-study": 1,
    "http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/genomic-study-reference": 1,
    "http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/genomics-bundle": 1,
    "http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/genotype": 3,
    "http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/medication-recommendation": 2,
    "http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/recommended-action": 2,
    "http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/therapeutic-implication": 2,
    "http://loinc.org": 15,
    "http://snomed.info/sct": 1,
    "http://terminology.hl7.org/CodeSystem/observation-category": 5,
    "http://terminology.hl7.org/CodeSystem/v2-0074": 1,
    "http://unitsofmeasure.org": 1,
    "http://www.genenames.org/geneId": 3,
    "http://www.nlm.nih.gov/research/umls/rxnorm": 2,
    "http://www.pharmvar.org": 3,
    "https://www.clinpgx.org/guidelineAnnotations": 1,
    "urn:zaropgx:conclusion-codes": 1,
    "urn:zaropgx:patient-id": 1,
    "urn:zaropgx:report-id": 1,
}


class _SequentialUuid:
    """Stand-in for the ``uuid`` module handing out 00...01, 00...02, ..."""

    def __init__(self) -> None:
        self._counter = itertools.count(1)

    def uuid4(self) -> uuid_module.UUID:
        return uuid_module.UUID(int=next(self._counter))


class _FrozenDatetime:
    """Stand-in for ``datetime`` pinning every ``now()`` to one instant."""

    @staticmethod
    def now(tz=None) -> datetime:
        return datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


@pytest.fixture
def frozen_export(monkeypatch):
    """Build exports with uuid/timestamp entropy removed."""

    def _build(output_format: str) -> Dict[str, Any]:
        monkeypatch.setattr(fhir_service_module, "uuid", _SequentialUuid())
        monkeypatch.setattr(fhir_service_module, "datetime", _FrozenDatetime)
        service = FHIRExportService(MagicMock())
        result = service.export_pgx_report(
            run_id="run-370",
            patient_info=PATIENT_FIXTURE,
            output_format=output_format,
            include_recommendations=True,
            pharmcat_data=PHARMCAT_FIXTURE,
        )
        assert result["success"], result.get("error")
        return result

    return _build


def _uri_census(node: Any, counts: Dict[str, int]) -> Dict[str, int]:
    """Count every coding ``system``, ``meta.profile`` and extension ``url``."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "system" and isinstance(value, str):
                counts[value] = counts.get(value, 0) + 1
            elif key == "profile" and isinstance(value, list):
                for profile in value:
                    counts[profile] = counts.get(profile, 0) + 1
            elif key == "url" and isinstance(value, str):
                counts[value] = counts.get(value, 0) + 1
            else:
                _uri_census(value, counts)
    elif isinstance(node, list):
        for item in node:
            _uri_census(item, counts)
    return counts


# ---------------------------------------------------------------------------
# 370 - the terminology move must not change a single byte
# ---------------------------------------------------------------------------


def test_json_bundle_is_byte_identical_to_pre_refactor_golden(frozen_export):
    result = frozen_export("json")
    content = result["content"].encode("utf-8")
    assert _uri_census(result["bundle"], {}) == GOLDEN_URI_CENSUS
    assert len(content) == GOLDEN_JSON_BYTES
    assert hashlib.sha256(content).hexdigest() == GOLDEN_JSON_SHA256


def test_xml_bundle_is_byte_identical_to_pre_refactor_golden(frozen_export):
    result = frozen_export("xml")
    content = result["content"].encode("utf-8")
    assert len(content) == GOLDEN_XML_BYTES
    assert hashlib.sha256(content).hexdigest() == GOLDEN_XML_SHA256


def test_terminology_module_is_importable_and_single_sourced():
    from app.services.fhir import terminology

    assert terminology.LOINC == "http://loinc.org"
    # The class attributes must be the very objects the module owns, not copies.
    assert FHIRExportService.LOINC_CODES is terminology.LOINC_CODES
    assert FHIRExportService.GENE_LOINC_CODES is terminology.GENE_LOINC_CODES


def test_service_builders_hold_no_inline_terminology_literals():
    """No ``"system"``/``"profile"``/``"url"`` in the service may be a string literal."""
    source_path = Path(fhir_service_module.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"), str(source_path))

    terminology_keys = {"system", "url", "profile"}
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if not isinstance(key, ast.Constant) or key.value not in terminology_keys:
                continue
            candidates = value.elts if isinstance(value, ast.List) else [value]
            for candidate in candidates:
                if isinstance(candidate, ast.Constant) and isinstance(
                    candidate.value, str
                ):
                    offenders.append((candidate.lineno, key.value, candidate.value))

    assert offenders == [], (
        "terminology URIs must come from app.services.fhir.terminology; "
        f"inline literals remain: {offenders}"
    )
