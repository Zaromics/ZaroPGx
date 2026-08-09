"""Every script the report loads must have something to bind to (BACKLOG 410).

``interactive_report.html`` loaded ``/static/js/pgx-fhir-export.js``, which looked
up seven element ids -- ``fhirExportBtn``, ``fhirServerUrl``, ``exportResult``,
``patientFamilyName``, ``patientGivenName``, ``patientGender``,
``patientBirthDate`` -- of which exactly zero existed in any template in the repo.
``initFhirExport()`` therefore found no button, attached no listener, and
``exportToFhir()`` was unreachable from the page.

Nothing user-facing went with it. Even if a button had existed, the endpoint it
posted to -- ``POST /reports/{report_id}/export-to-fhir`` -- is retired and
answers 501; the script had grown a special case just to render that 501's
detail message. FHIR export itself is alive elsewhere: ``fhir_export_router``
serves ten routes under ``/fhir`` (``/export/run/{run_id}``,
``/export/workflow/{workflow_id}``, ``/save/run/{run_id}``, ...), and
``generate_report`` writes the JSON and XML Bundles into the report directory and
records them as ``fhir_json_url`` / ``fhir_xml_url``.

Worth stating plainly, because it is the real gap: the report templates link to
neither of those. FHIR export has no UI entry point at all -- it did not have one
before this deletion either, since the only candidate never bound.

The general test is the point. A ``<script>`` tag that binds to nothing fails
silently forever, which is exactly how this survived; so assert the property
rather than the one filename.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TEMPLATES = ["report_template.html", "interactive_report.html"]


def _static_root():
    import app.reports.generator as generator

    return Path(generator.__file__).resolve().parents[1] / "static"


def _render(template_name):
    from app.reports.generator import env

    return env.get_template(template_name).render(
        patient_id="test-patient",
        report_id="test-report",
        report_date="2026-08-08",
        sample_identifier="NA12878",
        diplotypes=[
            {
                "gene": "CYP2C9",
                "diplotype": "*2/*3",
                "phenotype": "Poor Metabolizer",
                "activity_score": 0.5,
            }
        ],
        recommendations=[
            {
                "drug": "warfarin",
                "gene": "CYP2C9",
                "recommendation": "Standard dosing.",
                "classification": "Strong",
                "evidence_class": "evidence-3",
                "literature_references": [],
            }
        ],
        gene_drug_recommendations=[],
        organized_recommendations=[],
        disclaimer="",
    )


def _local_scripts(html):
    """The ``/static/...`` scripts the rendered page loads, in order."""
    return [
        src
        for src in re.findall(r'<script[^>]+src="([^"]+)"', html)
        if src.startswith("/static/")
    ]


@pytest.mark.parametrize("template_name", TEMPLATES)
def test_every_script_the_report_loads_exists_on_disk(template_name):
    html = _render(template_name)
    root = _static_root()
    for src in _local_scripts(html):
        path = root / src[len("/static/") :]
        assert path.is_file(), f"{template_name} loads {src}, which does not exist"


@pytest.mark.parametrize("template_name", TEMPLATES)
def test_every_dom_hook_of_every_loaded_script_exists_on_the_page(template_name):
    """The orphan condition, stated generally.

    "At least one hook resolves" is too weak to have caught this: the orphan
    reached for ``#pgxData`` too, which does exist, so 1 of its 8 hooks matched
    even though the button it needed never did. Measured against the rendered
    page, the two scripts sat on opposite sides of a wide gap --
    ``pgx-report.js`` resolves 9 of 9, ``pgx-fhir-export.js`` resolved 1 of 8 --
    so require all of them and the distinction needs no threshold.

    A script that fails this is either dead or is being loaded into the wrong
    page; either way it fails silently at runtime forever, which is precisely
    how the FHIR one survived.
    """
    html = _render(template_name)
    root = _static_root()

    for src in _local_scripts(html):
        source = (root / src[len("/static/") :]).read_text(encoding="utf-8")
        ids = set(re.findall(r"""getElementById\(\s*['"]([\w-]+)['"]""", source))
        classes = set(
            re.findall(r"""querySelector(?:All)?\(\s*['"]\.([\w-]+)""", source)
        )
        if not ids and not classes:
            continue  # binds by other means; nothing to check here

        missing_ids = sorted(i for i in ids if f'id="{i}"' not in html)
        missing_classes = sorted(
            c
            for c in classes
            if f'class="{c}"' not in html and f'class="{c} ' not in html
        )
        assert not missing_ids and not missing_classes, (
            f"{template_name} loads {src}, which reaches for DOM hooks that are "
            f"not on the page: ids={missing_ids} classes={missing_classes}"
        )


def test_the_retired_fhir_export_script_is_gone():
    """Named explicitly so the deletion cannot be quietly undone."""
    assert not (_static_root() / "js" / "pgx-fhir-export.js").exists()

    html = _render("interactive_report.html")
    assert "pgx-fhir-export.js" not in html


def test_fhir_export_still_has_its_api_surface():
    """What replaced it: ten live routes under /fhir, and files on disk.

    Deleting the script removed no reachable capability. This pins that the
    capability it *claimed* to offer still exists behind the API.
    """
    from app.api.routes.fhir_export_router import router

    paths = {route.path for route in router.routes}
    assert router.prefix == "/fhir"
    for expected in (
        "/fhir/export/run/{run_id}",
        "/fhir/export/workflow/{workflow_id}",
        "/fhir/save/run/{run_id}",
        "/fhir/status",
    ):
        assert expected in paths, sorted(paths)
