"""A patient's second job must report its own PharmCAT calls.

PharmCAT runs were stored under the report's ``title``, which PharmCAT takes from
its input file and the sidecar names after the patient. A second job for the same
patient therefore found "its" run already there, and parse_and_load swapped
``raw_data`` and returned: the gene, diplotype and recommendation rows that the
reports and the FHIR export read stayed the first job's. Seen on 30x NA12878: the
second job's report showed CYP2D6 "*3/*68+*4 Indeterminate" from the first job,
although its own PharmCAT output said "*3/*68 + *4, Poor Metabolizer".

The report lane now keys each run by job, and re-loading a run replaces its rows.

Runs on the suite's PostgreSQL (tests/postgres.py), whose ``db/init`` schema has
the pharmcat tables.
"""

from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

import pytest
from sqlalchemy import func

from app.pharmcat.pharmcat_parser import Base as PharmcatBase
from app.pharmcat.pharmcat_parser import PharmCATParser, get_pharmcat_summary

ROOT = Path(__file__).resolve().parents[1]
REPORT_JSON = ROOT / "test_data" / "pharmcat.example.v340.report.json"


@pytest.fixture
def session(db_session):
    return db_session


def _first_job():
    return json.loads(REPORT_JSON.read_text(encoding="utf-8"))


def _second_job():
    """The same patient (same title), re-analysed with a different CYP2C19 call."""
    report = copy.deepcopy(_first_job())
    report["genes"]["CYP2C19"]["sourceDiplotypes"][0]["label"] = "*1/*2"
    return report


def _cyp2c19_label(report):
    return report["genes"]["CYP2C19"]["sourceDiplotypes"][0]["label"]


def _cyp2c19(session, run_id):
    rows = PharmCATParser(session).get_diplotypes(run_id)
    return sorted(r["diplotype_label"] for r in rows if r["gene_symbol"] == "CYP2C19")


def _row_counts(session):
    return {
        table.name: session.execute(
            table.select().with_only_columns(func.count())
        ).scalar()
        for table in PharmcatBase.metadata.sorted_tables
    }


def test_the_fixture_is_one_patient_with_two_different_calls():
    assert _first_job()["title"] == _second_job()["title"]
    assert _cyp2c19_label(_first_job()) == "*38/*38"
    assert _cyp2c19_label(_second_job()) == "*1/*2"


def test_two_jobs_for_one_patient_keep_their_own_calls(session):
    parser = PharmCATParser(session)
    assert parser.parse_and_load(_first_job(), run_id="job-1") == "job-1"
    assert parser.parse_and_load(_second_job(), run_id="job-2") == "job-2"

    assert _cyp2c19(session, "job-1") == ["*38/*38"]
    assert _cyp2c19(session, "job-2") == ["*1/*2"]


def test_the_summary_sample_identifier_is_still_the_title(session):
    """It was the run id, which was the title; now that the run id is a job id,
    the summary still reports the title."""
    PharmCATParser(session).parse_and_load(_first_job(), run_id="job-1")
    summary = get_pharmcat_summary("job-1", session)
    assert summary["sample_identifier"] == "pharmcat.example.v340"


def test_reloading_a_run_replaces_its_rows(session):
    """Re-loading under the same id (the /load route keys by title) used to keep
    every row of the first payload and swap raw_data alone."""
    parser = PharmCATParser(session)
    parser.parse_and_load(_first_job())
    once = _row_counts(session)

    parser.parse_and_load(_second_job())

    assert _cyp2c19(session, "pharmcat.example.v340") == ["*1/*2"]
    # Every table, recommendation conditions included: nothing orphaned, nothing
    # doubled.
    assert _row_counts(session) == once
    assert once["diplotypes"] and once["recommendation_conditions"]


def test_the_report_lane_keys_the_run_by_job():
    tree = ast.parse((ROOT / "app/api/routes/upload_router.py").read_text())
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "load_pharmcat_file"
    ]
    assert len(calls) == 1
    run_id = {kw.arg: kw.value for kw in calls[0].keywords}.get("run_id")
    assert run_id is not None and ast.unparse(run_id) == "str(job_id)"
