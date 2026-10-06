"""Reports print the sample identifier entered at upload.

The upload form's Sample Identifier never reached a report. generate_report put the
PharmCAT run's title first -- the patient UUID -- and, finding it UUID-like, swapped
in the job id read off the PharmCAT file names. Every report of the 30x NA12878
runs, uploaded as "NA12878-wgs-validation", said "Sample ID: <job uuid>". The
identifier was there all along: in the job's metadata, and in the
``patient_info["sample_identifier"]`` upload_router passes.

These reproduce that setting: the PharmCAT title is the patient UUID and a
``<job>_pgx_pharmcat.tsv`` sits in the report directory.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

import app.reports.generator as generator_module
from app.api.db import Job
from app.reports.generator import entered_sample_identifier

PATIENT = "b5bab0f9-177e-4a5b-bab6-437c936628cd"
JOB = "e2a0c2ac-3246-4d00-90ef-f6d84c3394cf"
GENE = {
    "gene": "CYP2C19",
    "diplotype": "*1/*2",
    "phenotype": "Intermediate Metabolizer",
}
LANES = ("write_html", "write_interactive_html")


class _Query:
    """Just enough of a Session.query chain for generate_report's Job read."""

    def __init__(self, row):
        self._row = row

    def __getattr__(self, name):  # filter, populate_existing, order_by, ...
        return lambda *args, **kwargs: self

    def first(self):
        return self._row

    def all(self):
        return []


class _Session:
    def __init__(self, job_metadata):
        self._job = SimpleNamespace(id=JOB, job_metadata=job_metadata)

    def query(self, model, *args):
        return _Query(self._job if model is Job else None)


def _render(monkeypatch, tmp_path, patient_info, lane, db_session=None):
    for key in (
        "write_pdf",
        "write_json",
        "write_tsv",
        "write_workflow_svg",
        "write_workflow_png",
        "show_pharmcat_html_report",
        "show_pharmcat_json_report",
        "show_pharmcat_tsv_report",
        *LANES,
    ):
        monkeypatch.setitem(generator_module.REPORT_CONFIG, key, key == lane)
    (tmp_path / f"{JOB}_pgx_pharmcat.tsv").write_text("")
    result = generator_module.generate_report(
        {
            "data": {
                "genes": [GENE],
                "drugRecommendations": [],
                "sample_identifier": PATIENT,  # the PharmCAT title
            }
        },
        str(tmp_path),
        patient_info,
        job_id=JOB if db_session else None,
        db_session=db_session,
    )
    key = "html_path" if lane == "write_html" else "interactive_html_path"
    page = (tmp_path / result[key].split("/")[-1]).read_text(encoding="utf-8")
    (shown,) = set(re.findall(r"Sample ID:</strong>\s*([^<\s]+)", page))
    return shown


@pytest.mark.parametrize("lane", LANES)
def test_the_identifier_upload_router_passes_is_printed(monkeypatch, tmp_path, lane):
    info = {"id": PATIENT, "sample_identifier": "NA12878-wgs-validation"}
    assert _render(monkeypatch, tmp_path, info, lane) == "NA12878-wgs-validation"


@pytest.mark.parametrize("lane", LANES)
def test_the_identifier_in_the_jobs_metadata_is_printed(monkeypatch, tmp_path, lane):
    """Whoever calls generate_report, the job's own metadata is enough."""
    session = _Session({"sample_identifier": "NA12878-wgs-validation"})
    shown = _render(monkeypatch, tmp_path, {"id": PATIENT}, lane, session)
    assert shown == "NA12878-wgs-validation"


@pytest.mark.parametrize("lane", LANES)
def test_with_no_identifier_the_old_fallback_stands(monkeypatch, tmp_path, lane):
    """upload_router falls back to the patient id, which is not an identifier. The
    lanes' fallbacks stay as they were, and they differ: on 30x NA12878 the HTML
    report printed the patient id and the interactive one the job id."""
    info = {"id": PATIENT, "sample_identifier": PATIENT}
    assert _render(monkeypatch, tmp_path, info, lane) in (PATIENT, JOB)


@pytest.mark.parametrize(
    "metadata, patient_info, expected",
    [
        (
            {"sample_identifier": "typed", "header_sample_identifier": "hdr"},
            {},
            "typed",
        ),
        ({"header_sample_identifier": "hdr"}, {"sample_identifier": "x"}, "hdr"),
        ({}, {"id": "p", "sample_identifier": "passed"}, "passed"),
        ({"sample_identifier": "  "}, {"id": "p", "sample_identifier": "p"}, None),
        (None, None, None),
    ],
)
def test_entered_sample_identifier(metadata, patient_info, expected):
    assert entered_sample_identifier(metadata, patient_info) == expected
