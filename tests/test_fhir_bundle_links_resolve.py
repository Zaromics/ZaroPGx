"""Every reference in the bundle points at a resource the bundle carries.

A receiving system follows these links. A reference to a resource that is not in the
bundle cannot be followed, and a resource nothing points at is never reached -- the
same two failures as a report marker with no reader, in bundle form.

Both are easy to introduce and invisible without a check: the bundle is assembled from
several builders, each minting ids, and a renamed or re-minted id breaks the pointer at
the other end silently. The export was validated once against HAPI FHIR 8.12 with the
Genomics Reporting IG loaded (2026-10-16, 731 errors found and fixed, pinned by
``test_fhir_ig_conformance.py``), but that was one run against one bundle on one
day, and the export has changed since.

Measured when this was written: 12 entries, 28 references -- every one a ``urn:uuid:``
matching an entry's ``fullUrl`` -- none dangling, and the only resource nothing
points at is the ``DiagnosticReport``, which is the bundle's root.

The first version of this test excused every reference beginning ``urn:`` as pointing
outside the bundle. Since all of them begin that way, it asserted nothing; breaking a
reference on purpose did not fail it. A `urn:uuid:` is how a bundle names its OWN
resources.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple
from unittest.mock import MagicMock

import pytest

from app.services.fhir_export_service import FHIRExportService
from tests.test_fhir_ig_conformance import PHARMCAT_DATA

#: A resource that is the report itself, rather than evidence the report points at.
BUNDLE_ROOT = "DiagnosticReport"


@pytest.fixture
def bundle() -> Dict[str, Any]:
    service = FHIRExportService(MagicMock())
    result = service.export_pgx_report(
        run_id="run-1",
        patient_info={"id": "patient-1"},
        pharmcat_data=PHARMCAT_DATA,
        output_format="json",
    )
    assert result["success"], result
    return json.loads(result["content"])


def _references(node: Any, path: str = "") -> List[Tuple[str, str]]:
    """Every ``reference`` string in the bundle, with where it was found."""
    found: List[Tuple[str, str]] = []
    if isinstance(node, dict):
        if isinstance(node.get("reference"), str):
            found.append((node["reference"], path))
        for key, value in node.items():
            found.extend(_references(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(_references(value, f"{path}[{index}]"))
    return found


def _addresses(bundle: Dict[str, Any]) -> set:
    """Both ways a bundled resource can be addressed: Type/id and its fullUrl."""
    addresses = set()
    for entry in bundle.get("entry", []):
        resource = entry.get("resource") or {}
        if resource.get("id"):
            addresses.add(f"{resource['resourceType']}/{resource['id']}")
        if entry.get("fullUrl"):
            addresses.add(entry["fullUrl"])
    return addresses


def test_every_reference_resolves_inside_the_bundle(bundle):
    addresses = _addresses(bundle)
    references = _references(bundle)
    assert references, "a bundle with no references at all would pass every check below"

    # This bundle addresses its own resources by `urn:uuid:`, matching each entry's
    # fullUrl, so a urn is NOT evidence that a reference points outside. Excusing every
    # urn here is how the first version of this test came to check nothing at all.
    dangling = [
        (ref, path)
        for ref, path in references
        # Only an absolute http(s) URL names a resource on another server.
        if not ref.startswith(("http://", "https://")) and ref not in addresses
    ]
    assert not dangling, (
        "these references name a resource the bundle does not carry, so a receiving "
        f"system cannot follow them: {dangling}"
    )


def test_nothing_but_the_report_itself_is_unreferenced(bundle):
    """A resource nothing points at is in the bundle and out of reach."""
    pointed_at = {ref for ref, _ in _references(bundle)}
    stranded = []
    for entry in bundle.get("entry", []):
        resource = entry.get("resource") or {}
        key = f"{resource.get('resourceType')}/{resource.get('id')}"
        if key in pointed_at or entry.get("fullUrl") in pointed_at:
            continue
        if resource.get("resourceType") == BUNDLE_ROOT:
            continue  # the report is what points at everything else
        stranded.append(key)
    assert not stranded, (
        f"nothing in the bundle points at {stranded}, so a reader that walks the "
        "report's links never reaches them"
    )


def test_the_report_root_is_present_and_points_at_the_rest(bundle):
    """The check above excuses the root, so the root must actually be there."""
    roots = [
        e["resource"]
        for e in bundle.get("entry", [])
        if (e.get("resource") or {}).get("resourceType") == BUNDLE_ROOT
    ]
    assert len(roots) == 1, f"expected one {BUNDLE_ROOT}, found {len(roots)}"
    assert _references(roots[0]), f"the {BUNDLE_ROOT} points at nothing"
