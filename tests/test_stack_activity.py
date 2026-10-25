"""start-docker.sh and start-docker.ps1 ask what a restart would interrupt before they
stop the stack.

The update path (bootstrap --update, then start-docker) ran `docker compose down` at
once: a job running was failed, and an upload still arriving was cut off (a whole
genome's BAM takes a quarter of an hour to arrive). FastAPI receives an upload in full
before its endpoint runs, so only middleware sees one arriving; /internal/activity
reports those with the jobs not yet finished, and answers only from inside the app's
container, where scripts/stack_activity.py asks it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.db import Job, active_job_count
from app.api.middleware import activity
from app.api.models import JobCreate, JobStatus
from app.services.job_service import JobService

REPO = Path(__file__).resolve().parents[1]


def test_an_upload_counts_until_it_is_answered():
    seen = []

    async def endpoint(scope, receive, send):
        seen.append(activity.uploads_in_flight())

    middleware = activity.UploadActivityMiddleware(endpoint)
    upload = {"type": "http", "method": "POST", "path": "/upload/genomic-data"}
    asyncio.run(middleware(upload, None, None))
    assert seen == [1] and activity.uploads_in_flight() == 0

    for other in (
        {"type": "http", "method": "GET", "path": "/upload/status/x"},
        {"type": "http", "method": "POST", "path": "/api/v1/jobs"},
        {"type": "websocket", "path": "/upload/genomic-data"},
    ):
        asyncio.run(middleware(other, None, None))
    assert seen == [1, 0, 0, 0]


def test_a_failed_upload_stops_counting():
    async def endpoint(scope, receive, send):
        raise RuntimeError("client went away")

    middleware = activity.UploadActivityMiddleware(endpoint)
    try:
        asyncio.run(
            middleware(
                {"type": "http", "method": "POST", "path": "/upload/demo"}, None, None
            )
        )
    except RuntimeError:
        pass
    assert activity.uploads_in_flight() == 0


def test_jobs_not_finished_are_counted(db_session):
    service = JobService(db_session)
    jobs = [
        service.create_job(JobCreate(workflow_type="genomic_analysis", name=f"j{i}"))
        for i in range(3)
    ]
    db_session.get(Job, jobs[0].id).status = JobStatus.COMPLETED
    db_session.get(Job, jobs[1].id).status = JobStatus.RUNNING
    db_session.flush()

    assert active_job_count(db_session) == 2  # one running, one pending


def test_the_route_answers_inside_the_container_only(
    override_db_dependency, db_session
):
    from app.main import app

    app.router.on_startup.clear()
    app.router.on_shutdown.clear()
    JobService(db_session).create_job(
        JobCreate(workflow_type="genomic_analysis", name="busy")
    )
    db_session.flush()

    with TestClient(app, client=("127.0.0.1", 50000)) as inside:
        answer = inside.get("/internal/activity")
    assert answer.status_code == 200
    assert answer.json() == {"uploads_in_flight": 0, "jobs_active": 1}

    with TestClient(app, client=("203.0.113.9", 50000)) as outside:
        assert outside.get("/internal/activity").status_code == 404


def test_the_route_is_open_at_the_gate_and_kept_out_of_the_api_docs(client):
    from app.api.middleware.auth_gate import is_allowlisted

    assert is_allowlisted("/internal/activity")
    assert "/internal/activity" not in client.get("/openapi.json").json()["paths"]


def test_both_start_scripts_ask_before_they_stop_the_stack():
    sh = (REPO / "start-docker.sh").read_text(encoding="utf-8")
    ps1 = (REPO / "start-docker.ps1").read_text(encoding="utf-8")
    for script, down in (
        (sh, "docker compose down"),
        (ps1, 'Invoke-Docker "docker compose down'),
    ):
        assert "scripts/stack_activity.py" in script
        assert script.index("stack_activity.py") < script.index(down)
    assert "check_stack_idle\nif ! docker compose down" in sh
    assert "--force) FORCE=true" in sh and "[switch]$Force" in ps1


def test_the_helper_falls_back_to_the_database_for_an_older_app():
    helper = (REPO / "scripts" / "stack_activity.py").read_text(encoding="utf-8")
    assert "http://127.0.0.1:8000/internal/activity" in helper
    assert 'print("?", active_job_count())' in helper
