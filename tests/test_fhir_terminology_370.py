"""FHIR export layer: terminology single-sourcing (370) and off-loop writes (371).

Item 370 moves every coding-system / profile / extension URI out of the resource
builders in ``app.services.fhir_export_service`` and into
``app.services.fhir.terminology``. That move must be *pure*: the emitted bundle
has to stay byte-identical.

The golden digests below were captured from the pre-refactor service at
``f4e1bb6`` (the commit immediately before the terminology module existed) with
``uuid``/``datetime`` frozen, so the byte comparison really is "before vs after"
and not just "after vs after".

Item 371 pushes the blocking ``save_fhir_export`` disk writes off the event loop
via ``asyncio.to_thread`` in all three saving handlers.

Everything here runs against in-memory fixtures: no FHIR server, no database, no
network.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import itertools
import threading
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

# sha256 of ``result["content"].encode("utf-8")`` for the fixture above. First
# captured from the pre-refactor service (370 had to be byte-identical); re-taken
# after fix/fhir-loinc-codes, which changed what is emitted on purpose: one
# genotype code (84413-4) for every gene instead of per-gene codes that named other
# genes, official LOINC display names, a phenotype component without CYP2C9's
# code, no "genomics-bundle" profile, SNOMED 405824009 as "Genetic test". Re-taken
# after fix/fhir-ig-conformance, which made the bundle validate against the Genomics
# Reporting IG's profiles: both observation categories, HGNC ids, no PharmVar or
# empty RxNorm codings, no tbd codes the IG does not define, no Task.code, the
# genomic study's fixed category, no conclusionCode. The XML digest once more when
# the XML became FHIR XML (resources wrapped in their type, children in the
# specification's order) instead of a mirror of the JSON no FHIR parser could read.
GOLDEN_JSON_SHA256 = "f9c4c8f60fda6eb2513ad6edada7384548e47f84cd3abd6863d912e7d39e0494"
GOLDEN_JSON_BYTES = 20497
GOLDEN_XML_SHA256 = "8bd1f3e70050c8355e4f5a977801dec8e3e3e918d620e1b0f94c435c54551421"
GOLDEN_XML_BYTES = 20429

# Every terminology URI the pre-refactor bundle emitted, with its multiplicity.
# Redundant with the digests above, but it fails readably: a wrong constant shows
# up here as a named URI rather than as an opaque hash mismatch.
GOLDEN_URI_CENSUS = {
    "http://hl7.org/fhir/StructureDefinition/Patient": 1,
    "http://hl7.org/fhir/StructureDefinition/workflow-relatedArtifact": 1,
    "http://hl7.org/fhir/uv/genomics-reporting/CodeSystem/tbd-codes-cs": 4,
    "http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/genomic-report": 1,
    "http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/genomic-study": 1,
    "http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/genomic-study-reference": 1,
    "http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/genotype": 3,
    "http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/medication-recommendation": 2,
    "http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/recommended-action": 2,
    "http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/therapeutic-implication": 2,
    "http://loinc.org": 16,
    "http://snomed.info/sct": 1,
    "http://terminology.hl7.org/CodeSystem/observation-category": 6,
    "http://terminology.hl7.org/CodeSystem/v2-0074": 6,
    "http://unitsofmeasure.org": 1,
    "http://www.genenames.org": 3,
    "https://www.clinpgx.org/guidelineAnnotations": 1,
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
    assert not hasattr(FHIRExportService, "GENE_LOINC_CODES")
    assert not hasattr(terminology, "GENE_LOINC_CODES")


# The official display of each LOINC code (tx.fhir.org, 2026-10-12 and -16). LOINC's
# licence asks that a code travel with one of its official names.
OFFICIAL_LOINC_DISPLAYS = {
    "84413-4": "Genotype display name",
    "51963-7": "Medication assessed [ID]",
    "51969-4": "Genetic analysis report",
    "84414-2": "Haplotype name",
    "48018-6": "Gene studied [ID]",
    "93044-6": "Level of evidence",
    "104666-3": "CYP2B6 Activity Score in Blood or Tissue",
    "104667-1": "CYP2C19 Activity Score in Blood or Tissue",
    "104668-9": "CYP2C9 Activity Score in Blood or Tissue",
    "104669-7": "CYP2D6 Activity Score in Blood or Tissue",
    "104665-5": "DPYD Activity Score in Blood or Tissue",
}


def _loinc_codings(node, found):
    if isinstance(node, dict):
        if node.get("system") == "http://loinc.org":
            found.append((node.get("code"), node.get("display")))
        for value in node.values():
            _loinc_codings(value, found)
    elif isinstance(node, list):
        for value in node:
            _loinc_codings(value, found)
    return found


def test_every_loinc_code_carries_its_official_display(frozen_export):
    import json

    bundle = json.loads(frozen_export("json")["content"])
    codings = _loinc_codings(bundle, [])
    assert codings
    for code, display in codings:
        assert OFFICIAL_LOINC_DISPLAYS.get(code) == display, (code, display)


def test_every_genotype_is_coded_as_a_genotype(frozen_export):
    """Not a gene-specific code: those named other genes (79714-2, sent for
    CYP2D6, is CYP2C19's) or did not exist."""
    import json

    bundle = json.loads(frozen_export("json")["content"])
    genotypes = [
        entry["resource"]
        for entry in bundle["entry"]
        if "genotype" in str(entry["resource"].get("meta", {}).get("profile", ""))
    ]
    assert len(genotypes) == 3
    for observation in genotypes:
        assert [c["code"] for c in observation["code"]["coding"]] == ["84413-4"]


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


# ---------------------------------------------------------------------------
# 371 - the blocking save must not run on the event loop thread
# ---------------------------------------------------------------------------


SAVE_RESULT = {
    "success": True,
    "files_saved": [{"format": "json", "path": "/data/reports/x/pgx_fhir_report.json"}],
    "report_directory": "/data/reports/x",
}


@pytest.fixture
def offload_probe(monkeypatch):
    """Instrument ``asyncio.to_thread`` and ``save_fhir_export``.

    ``to_thread`` still delegates to the real implementation, so the recorded
    thread id is the id of a genuine worker thread - the test proves the write
    left the loop thread, not merely that a helper was named.
    """
    seen: Dict[str, Any] = {"to_thread": [], "save_thread": None, "save_kwargs": None}
    real_to_thread = asyncio.to_thread

    async def recording_to_thread(func, /, *args, **kwargs):
        seen["to_thread"].append(func)
        return await real_to_thread(func, *args, **kwargs)

    def fake_save(self, **kwargs):
        seen["save_thread"] = threading.get_ident()
        seen["save_kwargs"] = kwargs
        return dict(SAVE_RESULT)

    monkeypatch.setattr(asyncio, "to_thread", recording_to_thread)
    monkeypatch.setattr(FHIRExportService, "save_fhir_export", fake_save)
    return seen


def _assert_ran_off_loop(seen: Dict[str, Any]) -> None:
    assert seen["to_thread"], "handler never called asyncio.to_thread"
    assert seen["save_thread"] is not None, "save_fhir_export was never reached"
    assert seen["save_thread"] != threading.get_ident(), (
        "save_fhir_export ran on the event loop thread - its mkdir/write would "
        "block every other request"
    )


def test_save_run_handler_offloads_the_write(offload_probe):
    from app.api.routes import fhir_export_router as router_module

    response = asyncio.run(
        router_module.save_fhir_export_for_run(
            run_id="run-370",
            request=router_module.FHIRSaveRequest(output_format="json"),
            db=MagicMock(),
            current_user="tester",
        )
    )

    _assert_ran_off_loop(offload_probe)
    assert offload_probe["save_kwargs"]["run_id"] == "run-370"
    assert response.success is True


def test_save_workflow_handler_offloads_the_write(offload_probe, monkeypatch):
    from app.api.routes import fhir_export_router as router_module
    from app.services.pharmcat_data_service import PharmCATDataService

    monkeypatch.setattr(
        PharmCATDataService,
        "get_pharmcat_data_for_workflow",
        lambda self, workflow_id: {"run_id": "run-370"},
    )

    response = asyncio.run(
        router_module.save_fhir_export_for_workflow(
            workflow_id="wf-370",
            request=router_module.FHIRSaveRequest(output_format="json"),
            db=MagicMock(),
            current_user="tester",
        )
    )

    # Reached transitively: handler -> to_thread -> save_fhir_export_for_workflow
    # -> save_fhir_export.
    _assert_ran_off_loop(offload_probe)
    assert offload_probe["save_kwargs"]["workflow_id"] == "wf-370"
    assert response.success is True


def test_quick_save_handler_offloads_the_write(offload_probe):
    from app.api.routes import fhir_export_router as router_module

    response = asyncio.run(
        router_module.quick_save_fhir_export(
            run_id="run-370",
            output_format="both",
            patient_id=None,
            db=MagicMock(),
            current_user="tester",
        )
    )

    _assert_ran_off_loop(offload_probe)
    assert offload_probe["save_kwargs"]["output_format"] == "both"
    assert response["success"] is True
