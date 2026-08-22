import asyncio
import html
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
import zipfile
from asyncio import Queue
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote

import aiohttp
import httpx
import requests
from dotenv import load_dotenv
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.api.db import get_db
from app.api.middleware.auth_gate import (
    AuthGateMiddleware,
    check_password,
    clear_session_cookie,
    mint_session_token,
    resolve_auth_mode,
    safe_next_path,
    set_session_cookie,
)
from app.api.models import (
    Token,
)
from app.api.routes import report_router, upload_router
from app.api.routes.fhir_export_router import router as fhir_export_router
from app.api.routes.job_router import router as job_router
from app.api.routes.pharmcat_router import router as pharmcat_router
from app.api.routes.workflow_recipe_router import router as workflow_recipe_router
from app.api.utils.security import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    SECRET_KEY,
    create_access_token,
    get_optional_user,
)
from app.pharmcat import pharmcat_client
from app.reports.generator import (
    create_interactive_html_report,
    generate_pdf_report,
    generate_report,
)
from app.services.cleanup_service import cleanup_service
from app.services.fhir_export_service import fhir_export_enabled
from app.services.job_service import JobService
from app.utils.env import env_flag

# This module logs liberally with emoji. The container's stdout is UTF-8, but a Windows host
# console is cp1252, where a single emoji raises UnicodeEncodeError and takes down the whole
# import — which in turn breaks the test suite and any tooling that imports the app. Degrade
# unencodable characters instead of dying; a no-op wherever stdout is already UTF-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass

# Configure more detailed logging
log_level = os.getenv("LOG_LEVEL", "DEBUG").upper()
logging.basicConfig(
    level=getattr(logging, log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)

# Set specific loggers to DEBUG level
for logger_name in ["app", "uvicorn", "fastapi", "aiohttp.client"]:
    logging.getLogger(logger_name).setLevel(logging.DEBUG)

logger = logging.getLogger("app")
logger.info(f"Starting app with log level: {log_level}")

# Add more aggressive console logging for debugging
print(f"=========== ZaroPGx STARTING UP AT: {datetime.now(timezone.utc)} ===========")
print(f"LOG LEVEL: {log_level}")
print(f"GATK SERVICE URL: {os.getenv('GATK_API_URL', 'http://gatk-api:5000')}")
print(f"PHARMCAT SERVICE URL: {os.getenv('PHARMCAT_API_URL', 'http://pharmcat:5000')}")
print(f"PYPGX SERVICE URL: {os.getenv('PYPGX_API_URL', 'http://pypgx:5000')}")

# Load environment variables
load_dotenv()

# Sentinels match values formerly shipped in tracked .env templates.
# SECRET_KEY / ALGORITHM / ACCESS_TOKEN_EXPIRE_MINUTES are single-sourced from
# app.api.utils.security (imported above).
_SECRET_KEY_SENTINELS = frozenset(
    {
        "",
        "change_me",
        "change_me_in_production",
        "supersecretkey",
        "supersecretkey_for_development",
    }
)

# Constants
GATK_SERVICE_URL = os.getenv("GATK_API_URL", "http://gatk-api:5000")
PYPGX_SERVICE_URL = os.getenv("PYPGX_API_URL", "http://pypgx:5000")
PHARMCAT_API_URL = os.getenv("PHARMCAT_API_URL", "http://pharmcat:5000")
ZAROHLA_API_URL = os.getenv("ZAROHLA_API_URL", "http://zarohla:5000")


# Service toggle configuration / service enablement flags
GATK_ENABLED = env_flag("GATK_ENABLED", True)
PYPGX_ENABLED = env_flag("PYPGX_ENABLED", True)
OPTITYPE_ENABLED = env_flag("OPTITYPE_ENABLED", True)
GENOME_DOWNLOADER_ENABLED = env_flag("GENOME_DOWNLOADER_ENABLED", True)
KROKI_ENABLED = env_flag("KROKI_ENABLED", True)
HAPI_FHIR_ENABLED = env_flag("HAPI_FHIR_ENABLED", True)
# OUTSIDECALLSOVERRIDE is NOT parsed here either. This module's copy stripped
# whitespace while app.utils.outside_calls_override's did not, and it was dead
# besides — assigned once, read nowhere. The single reader is
# outside_calls_override.is_override_enabled().
# FHIR export is NOT parsed here. app.services.fhir_export_service.fhir_export_enabled()
# is the single reader, shared with the /fhir/* router's own guard — a second
# parse here is what let the mount and the guard disagree over "true ".
PHARMCAT_ABSENT_TO_REF = env_flag("PHARMCAT_ABSENT_TO_REF", False)
PHARMCAT_UNSPECIFIED_TO_REF = env_flag("PHARMCAT_UNSPECIFIED_TO_REF", False)
TEMP_DIR = Path("/tmp")
DATA_DIR = Path("/data")
REPORTS_DIR = Path(os.getenv("REPORT_DIR", "/data/reports"))
UPLOADS_DIR = Path(os.getenv("UPLOAD_DIR", "/data/uploads"))
BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = BASE_DIR / "templates"

# These directories are created by startup_event(), not at import time: the defaults are
# absolute container paths, so creating them on import would litter the host filesystem
# (C:\data, C:\tmp on Windows) merely from `import app.main`.

# Extensions this app recognises, longest first so `.vcf.gz` wins over `.gz`.
# The stored filename's extension is always one of these literals, never a slice of
# the upload's own name.
#
# DELIBERATE DUPLICATE of docker/gatk-api/gatk_api.py's SAFE_UPLOAD_EXTENSIONS /
# safe_upload_name(). The two must stay byte-for-byte equivalent in behaviour;
# tests/test_upload_name_sanitiser.py runs both implementations over the same
# corpus and asserts they return identical names, so a change to one that is not
# made to the other fails the suite.
#
# The compound `.gz` forms come first: an extension is chosen by the first
# `endswith` that matches, so a bare `.gz` variant added later must never
# precede the two-part form it is a suffix of.
SAFE_UPLOAD_EXTENSIONS = (
    ".vcf.gz",
    ".fastq.gz",
    ".fq.gz",
    ".vcf",
    ".bcf",
    ".bam",
    ".cram",
    ".sam",
    ".fastq",
    ".fq",
)


def safe_upload_name(filename: Optional[str], local_job_id: str) -> str:
    """Return a shell- and filesystem-safe name to store an upload under.

    `file.filename` is attacker-controlled: it arrives in the multipart body and
    nothing upstream sanitises it. This route previously used werkzeug's bare
    `secure_filename`, which is not enough on its own -- it returns `""` for a
    wholly pathological name such as `"..."`, and `os.path.join(temp_dir, "")` is
    the *directory*, which the next line then opened for writing.

    Same construction as the gatk-api sidecar's `safe_upload_name`: the name is
    rebuilt from parts this process controls rather than filtered,

      <allowlisted fragment of the original>_<our uuid><extension from the tuple above>

    so every byte of the result is either [A-Za-z0-9_-], our own uuid, or one of
    the literal extensions above. The fragment is kept only so logs and on-disk
    debugging still resemble the upload; correctness does not depend on it.

    Why a duplicate rather than a shared helper: `docker/gatk-api/gatk_api.py`
    ships in a different image, and that image has no werkzeug (see
    Dockerfile.gatk-api) -- which is why the sidecar hand-rolled this in the first
    place. Sharing *is* mechanically possible: every sidecar Dockerfile already
    does `COPY app/utils/job_client.py /job-client/`, so a small pure-Python
    module under app/utils/ could be copied in the same way. It would cost a
    Dockerfile line per image and a rebuild of each, which is why this change
    keeps the duplicate and pins the two together with a differential test
    instead. `app/api/utils/file_processor.py:safe_upload_basename` is a third,
    weaker sanitiser for the upload-router path; it guarantees non-emptiness but
    neither an allowlisted extension nor a length cap.
    """
    original = os.path.basename(filename or "")
    lowered = original.lower()

    extension = ""
    for candidate in SAFE_UPLOAD_EXTENSIONS:
        if lowered.endswith(candidate):
            extension = candidate
            break

    stem_source = original[: len(original) - len(extension)] if extension else original
    # Allowlist, not denylist: anything not explicitly safe is dropped.
    fragment = re.sub(r"[^A-Za-z0-9_-]", "", stem_source)[:40]

    return f"{fragment or 'upload'}_{local_job_id}{extension}"


# Initialize templates
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

Author_Name = "Iliya Yaroshevskiy"


# ----- Legal/Attribution helpers for AGPL notices -----
def _read_author_from_pyproject() -> str:
    try:
        project_root = os.path.dirname(os.path.dirname(__file__))
        pyproject_path = os.path.join(project_root, "pyproject.toml")
        if not os.path.exists(pyproject_path):
            return Author_Name
        with open(pyproject_path, "r", encoding="utf-8") as f:
            content = f.read()
        # Extract authors array content
        authors_block_match = re.search(
            r"^\s*authors\s*=\s*\[(.*?)\]", content, flags=re.DOTALL | re.MULTILINE
        )
        block = authors_block_match.group(1) if authors_block_match else content
        name_match = re.search(r"name\s*=\s*\"([^\"]+)\"", block)
        if name_match:
            return name_match.group(1).strip()
        return Author_Name
    except Exception:
        return Author_Name


def get_author_name() -> str:
    env_author = os.getenv("AUTHOR_NAME")
    if env_author:
        return env_author
    return _read_author_from_pyproject()


# Initialize FastAPI app
app = FastAPI(
    title="ZaroPGx, an Individual Pharmacogenomic Analysis Platform",
    description="An application with an API for processing genetic data and generating pharmacogenomic reports",
    version="0.2.8",
)

# Set up static file serving for application static assets
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# Static file serving for reports is now handled by custom routes
# app.mount("/reports", StaticFiles(directory=str(REPORTS_DIR)), name="reports")

# Mount built Sphinx documentation (if present) at /documentation
DOCS_BUILD_DIR = BASE_DIR.parent / "docs" / "_build" / "html"
if DOCS_BUILD_DIR.exists():
    app.mount(
        "/documentation",
        StaticFiles(directory=str(DOCS_BUILD_DIR), html=True),
        name="sphinx-docs",
    )


def _build_docs_if_missing() -> None:
    try:
        docs_index = DOCS_BUILD_DIR / "index.html"
        if not docs_index.exists():
            DOCS_BUILD_DIR.mkdir(parents=True, exist_ok=True)
            # Build docs using Sphinx if available
            cmd = [
                sys.executable,
                "-m",
                "sphinx",
                "-b",
                "html",
                "docs",
                str(DOCS_BUILD_DIR),
            ]
            subprocess.run(cmd, check=False)
        # Mount after building if not already mounted
        if (
            "/documentation"
            not in {m.path for m in app.router.routes if hasattr(m, "path")}
            and DOCS_BUILD_DIR.exists()
        ):
            app.mount(
                "/documentation",
                StaticFiles(directory=str(DOCS_BUILD_DIR), html=True),
                name="sphinx-docs",
            )
    except Exception as e:
        logger.warning(f"Docs build skipped or failed: {e}")


@app.on_event("startup")
async def ensure_docs_built_on_start() -> None:
    _build_docs_if_missing()


# Front-door auth gate (outermost). Default mode is open — a no-op for existing
# installs. See app.api.middleware.auth_gate for allowlist and cookie policy.
app.add_middleware(AuthGateMiddleware)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        # Live reference instance
        "https://pgx.zaromics.com",
        "http://pgx.zaromics.com",
        # Localhost development - main app ports
        "http://localhost:8765",  # Main FastAPI app external port
        "http://localhost:8000",  # Internal app port
        "http://127.0.0.1:8765",
        "http://127.0.0.1:8000",
        # Common frontend development ports
        "http://localhost:3000",
        "http://localhost:8080",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:8080",
        # Service-specific ports from docker-compose.yml
        "http://localhost:5050",  # genome-downloader
        "http://localhost:2323",  # pharmcat
        "http://localhost:8090",  # fhir-server
        "http://localhost:5001",  # pharmcat API port
        "http://localhost:5002",  # gatk-api
        "http://localhost:5053",  # pypgx
        "http://localhost:5444",  # PostgreSQL
        "http://localhost:5060",  # zarohla
        "http://localhost:5055",  # nextflow
        # 127.0.0.1 equivalents
        "http://127.0.0.1:5050",
        "http://127.0.0.1:2323",
        "http://127.0.0.1:8090",
        "http://127.0.0.1:5001",
        "http://127.0.0.1:5002",
        "http://127.0.0.1:5053",
        "http://127.0.0.1:5444",
        "http://127.0.0.1:5060",
        "http://127.0.0.1:5055",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(upload_router.router)
app.include_router(report_router.router)
app.include_router(workflow_recipe_router)
app.include_router(job_router)
app.include_router(pharmcat_router)

# Conditionally include FHIR export router (enabled by default).
# This runs below load_dotenv(), and the router's own guard calls the same
# resolver, so the mount decision and the guard can never disagree.
if fhir_export_enabled():
    app.include_router(fhir_export_router)
    logger.info("FHIR export functionality enabled (endpoints at /fhir/*)")
else:
    logger.info(
        "FHIR export functionality disabled (set FHIR_EXPORT_ENABLED=true to enable)"
    )


# Simple wrapper page for API reference with a Back button
@app.get("/api-reference", include_in_schema=False)
async def api_reference() -> HTMLResponse:
    html = """
<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>ZaroPGx API Reference</title>
  <style>
    body, html { margin: 0; padding: 0; height: 100%; }
    .topbar { display: flex; align-items: center; gap: 8px; padding: 10px; border-bottom: 1px solid #e5e7eb; }
    .topbar h1 { font-size: 16px; margin: 0; font-weight: 600; }
    .btn { display: inline-block; padding: 8px 12px; border-radius: 8px; text-decoration: none; font-size: 14px; }
    .btn-primary { background: #0d6efd; color: #fff; }
    .btn-primary:hover { background: #0b5ed7; }
    .frame { width: 100%; height: calc(100vh - 48px); border: 0; }
  </style>
  <link rel="icon" type="image/png" href="/static/favicon.png">
  <link rel="shortcut icon" href="/static/favicon.png">
  <link rel="apple-touch-icon" href="/static/favicon.png">
  <meta http-equiv=\"Content-Security-Policy\" content=\"frame-ancestors 'self';\" />
  <meta http-equiv=\"X-Frame-Options\" content=\"SAMEORIGIN\" />
  <meta http-equiv=\"Referrer-Policy\" content=\"no-referrer\" />
  <meta http-equiv=\"Permissions-Policy\" content=\"interest-cohort=()\" />
</head>
<body>
  <div class=\"topbar\">
    <a class=\"btn btn-primary\" href=\"/\">Back to ZaroPGx</a>
    <h1>API Reference</h1>
  </div>
  <iframe class=\"frame\" src=\"/docs\" title=\"Swagger UI\" loading=\"lazy\"></iframe>
</body>
</html>
        """
    return HTMLResponse(content=html)


_LOGIN_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>ZaroPGx — Sign in</title>
  <style>
    :root { color-scheme: light; --ink: #1a2332; --muted: #5a6577; --line: #d5dbe6; --accent: #0b6e4f; }
    body { margin: 0; min-height: 100vh; font-family: "Segoe UI", system-ui, sans-serif;
      background: radial-gradient(1200px 600px at 10% -10%, #e7f3ee 0%, transparent 55%),
                  linear-gradient(180deg, #f4f7fb 0%, #e9eef5 100%);
      color: var(--ink); display: grid; place-items: center; padding: 24px; }
    form { width: min(380px, 100%); }
    h1 { font-size: 1.75rem; margin: 0 0 0.25rem; letter-spacing: -0.02em; }
    p { margin: 0 0 1.25rem; color: var(--muted); line-height: 1.45; }
    label { display: block; font-size: 0.85rem; margin: 0.75rem 0 0.35rem; }
    input { width: 100%; box-sizing: border-box; padding: 0.7rem 0.8rem; border: 1px solid var(--line);
      border-radius: 6px; font-size: 1rem; background: #fff; }
    button { margin-top: 1.1rem; width: 100%; padding: 0.75rem; border: 0; border-radius: 6px;
      background: var(--accent); color: #fff; font-size: 1rem; cursor: pointer; }
    button:hover { filter: brightness(1.05); }
    .err { color: #9b1c1c; margin: 0 0 0.75rem; font-size: 0.9rem; }
    .note { margin-top: 1rem; font-size: 0.8rem; color: var(--muted); }
  </style>
</head>
<body>
  <form method="post" action="/login">
    <h1>ZaroPGx</h1>
    <p>Sign in with the shared install password to reach this instance.</p>
    {error}
    <input type="hidden" name="next" value="{next}" />
    <label for="password">Password</label>
    <input id="password" name="password" type="password" autocomplete="current-password" required autofocus />
    <button type="submit">Sign in</button>
    <p class="note">Anyone past this gate can still open any patient report on this install.
      There is no per-user access control yet.</p>
  </form>
</body>
</html>
"""


@app.get("/login", include_in_schema=False)
async def login_page(next: str = "/", error: str = "") -> HTMLResponse:
    safe_next = safe_next_path(next)
    err_html = f'<p class="err">{html.escape(error)}</p>' if error else ""
    # Avoid str.format — the page CSS contains literal braces.
    page = _LOGIN_PAGE.replace("{error}", err_html).replace(
        "{next}", html.escape(safe_next, quote=True)
    )
    return HTMLResponse(content=page)


@app.post("/login", include_in_schema=False)
async def login_submit(
    password: str = Form(...),
    next: str = Form("/"),
) -> Response:
    destination = safe_next_path(next)
    if not check_password(password):
        if resolve_auth_mode() == "password":
            return RedirectResponse(
                url=(
                    f"/login?next={quote(destination, safe='/:?=&')}"
                    f"&error={quote('Incorrect password')}"
                ),
                status_code=303,
            )
        # open/audit with no/wrong password: gate is not enforcing; send them on.
        return RedirectResponse(url=destination, status_code=303)
    token = mint_session_token()
    response: Response = RedirectResponse(url=destination, status_code=303)
    set_session_cookie(response, token)
    return response


@app.get("/logout", include_in_schema=False)
@app.post("/logout", include_in_schema=False)
async def logout() -> Response:
    response: Response = RedirectResponse(url="/login", status_code=303)
    clear_session_cookie(response)
    return response


# Add direct routes for status and reports
@app.get("/status/{data_id}")
async def get_status(
    data_id: str,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_optional_user),
):
    """Status by genetic-data id. Prefer /upload/status/{job_id} for run progress."""
    return await upload_router.get_upload_status_by_data_id(data_id, db)


# Generic report file serving route removed - now handled by specific endpoints
# This route was conflicting with the specific /reports/{job_id} endpoint
# Individual report files are now served through the get_report_urls function


# Report serving routes - order matters for path matching
@app.get("/reports/job/{job_id}")
async def get_reports_by_job_id(
    job_id: str, current_user: str = Depends(get_optional_user)
):
    """Get reports by job ID - forwards to upload_router"""
    from app.api.db import SessionLocal

    db = SessionLocal()
    try:
        return await upload_router.get_report_urls(job_id, db)
    finally:
        db.close()


@app.get("/reports/{job_id}")
async def get_reports_direct(
    job_id: str, current_user: str = Depends(get_optional_user)
):
    """Direct reports endpoint for frontend compatibility - same as /reports/job/{job_id}"""
    from app.api.db import SessionLocal

    db = SessionLocal()
    try:
        return await upload_router.get_report_urls(job_id, db)
    finally:
        db.close()


# Add a route to serve individual report files (MUST be after the job_id routes)
@app.api_route("/reports/{patient_id}/{filename:path}", methods=["GET", "HEAD"])
async def serve_report_file(
    patient_id: str, filename: str, current_user: str = Depends(get_optional_user)
):
    """Serve individual report files from the reports directory"""
    import mimetypes

    from app.api.utils.path_jail import resolve_under

    try:
        file_path = resolve_under(REPORTS_DIR, patient_id, filename)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Access denied")

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    media_type, _ = mimetypes.guess_type(str(file_path))
    return FileResponse(
        path=file_path,
        media_type=media_type or "application/octet-stream",
    )


# Authentication endpoint
@app.post("/token", response_model=Token)
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends()):
    """Issue a Bearer token.

    In password mode the shared ZAROPGX_AUTH_PASSWORD is required and the JWT
    carries gate=true so it unlocks the front door. In open/audit modes the
    legacy test/test credentials still work for API explorers, but those JWTs
    deliberately omit gate=true and cannot bypass password mode.
    """
    mode = resolve_auth_mode()
    if mode == "password":
        if not check_password(form_data.password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect username or password",
                headers={"WWW-Authenticate": "Bearer"},
            )
        subject = form_data.username.strip() or "gate"
        access_token = mint_session_token(subject=subject)
        return {"access_token": access_token, "token_type": "bearer"}

    if form_data.username != "test" or form_data.password != "test":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": form_data.username}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Root endpoint to serve the homepage with pharmacogenomic analysis form"""
    try:
        # Check service status before rendering page
        service_status = {
            "status": "ok",
            "message": "All services are available",
            "unhealthy_services": {},
        }

        # Internal check of services - don't expose network errors to users
        try:
            # Since we're in the app code, app is by definition running
            # Only check external services
            service_urls = {
                "gatk": os.getenv("GATK_API_URL", "http://gatk-api:5000") + "/health",
                "pharmcat": os.getenv("PHARMCAT_API_URL", "http://pharmcat:5000")
                + "/health",
                "pypgx": "http://pypgx:5000/health",  # Force to port 5000 directly
                "zarohla": os.getenv("ZAROHLA_API_URL", "http://zarohla:5000")
                + "/health",
            }

            unhealthy_services = []

            async with httpx.AsyncClient() as client:
                for service_name, url in service_urls.items():
                    try:
                        logger.info(f"Homepage check: Checking {service_name} at {url}")
                        response = await client.get(
                            url, timeout=2.0, follow_redirects=True
                        )
                        logger.info(
                            f"Homepage check: {service_name} response status={response.status_code}"
                        )
                        if response.status_code < 200 or response.status_code >= 300:
                            unhealthy_services.append(service_name)
                    except Exception as e:
                        # If we can't reach a service, mark it as unhealthy
                        logger.error(
                            f"Homepage check: Error checking {service_name}: {str(e)}"
                        )
                        unhealthy_services.append(service_name)

            # If any services are unhealthy, set status to error
            if unhealthy_services:
                service_status = {
                    "status": "error",
                    "message": "Some services are unavailable",
                    "unhealthy_services": unhealthy_services,
                }
        except Exception as e:
            # If something goes wrong with the check, just log it
            logger.exception(f"Error checking services: {str(e)}")

        # Render the template with service status
        service_alert = None
        if service_status["status"] == "error":
            unhealthy_list = service_status["unhealthy_services"]
            # Format names for display
            if len(unhealthy_list) == 1:
                service_message = f"{unhealthy_list[0]} is unavailable."
            else:
                service_message = f"{', '.join(unhealthy_list)} are unavailable."

            service_alert = service_message

        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "service_alert": service_alert,
                "author_name": get_author_name(),
                "license_name": "GNU Affero General Public License v3.0",
                "license_url": "https://www.gnu.org/licenses/agpl-3.0.html",
                "source_url": os.getenv(
                    "SOURCE_URL", "https://github.com/Zaromics/ZaroPGx"
                ),
                "current_year": datetime.now().year,
            },
        )
    except Exception as e:
        logger.exception(f"Error in home route: {str(e)}")
        return HTMLResponse(f"<h1>Error</h1><p>{str(e)}</p>")


@app.get("/license")
async def license_text():
    try:
        project_root = Path(__file__).resolve().parent.parent
        license_path = project_root / "LICENSE"
        if license_path.exists():
            return FileResponse(str(license_path), media_type="text/plain")
        return HTMLResponse("<pre>LICENSE file not found.</pre>", status_code=404)
    except Exception:
        return HTMLResponse("<pre>Unable to serve LICENSE.</pre>", status_code=500)


@app.get("/notice")
async def notice_text():
    try:
        project_root = Path(__file__).resolve().parent.parent
        notice_path = project_root / "NOTICE"
        if notice_path.exists():
            return FileResponse(str(notice_path), media_type="text/plain")
        return HTMLResponse("<pre>NOTICE file not found.</pre>", status_code=404)
    except Exception:
        return HTMLResponse("<pre>Unable to serve NOTICE.</pre>", status_code=500)


@app.get("/api")
async def api_root():
    return {"message": "Welcome to ZaroPGx API", "docs": "/docs"}


# Make the health check endpoint simple and dependency-free
@app.get("/health")
async def health_check():
    logger.info("Health check called")
    return {"status": "healthy", "timestamp": str(datetime.now(timezone.utc))}


# Legacy call_gatk_variants function removed - replaced by GATK API service

# Legacy process_file_in_background function removed - replaced by process_file_background in upload_router.py

# Legacy SSE progress endpoint removed - use workflow monitoring system instead

# Legacy event_generator function removed - use workflow monitoring system instead

# Legacy job-status endpoint removed - use workflow monitoring system instead


@app.post("/api/variant-call")
async def call_variants(
    file: UploadFile = File(...),
    reference_genome: str = Form("hg38"),
    regions: Optional[str] = Form(None),
):
    """Call variants using GATK API service."""
    try:
        # Save the file to a temporary location to get its path.
        # safe_upload_name(), not bare secure_filename(): the latter returns "" for
        # a name like "..." and os.path.join(temp_dir, "") is the directory itself,
        # which the open() below then tried to write to.
        temp_dir = tempfile.mkdtemp(dir="./data")
        input_path = os.path.join(
            temp_dir, safe_upload_name(file.filename, uuid.uuid4().hex)
        )

        with open(input_path, "wb") as temp_file:
            content = await file.read()
            temp_file.write(content)

        # Prepare the multipart/form-data
        files = {"file": open(input_path, "rb")}
        data = {"reference_genome": reference_genome}
        if regions:
            data["regions"] = regions

        # Call the GATK API service
        response = requests.post(
            f"{GATK_SERVICE_URL}/variant-call",
            files=files,
            data=data,
            timeout=3600,  # Allow up to 1 hour for large files
        )
        response.raise_for_status()

        # Return the API response
        return JSONResponse(status_code=200, content=response.json())
    except requests.RequestException as e:
        logging.error(f"GATK API error: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": "Variant calling failed", "details": str(e)},
        )
    except Exception as e:
        logging.error(f"Unexpected error: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        # Clean up the file (optional)
        try:
            if "files" in locals() and "file" in files:
                files["file"].close()
        except Exception:
            logger.debug(
                "Failed closing temp upload handle after variant-call", exc_info=True
            )


# Cleanup endpoints
@app.post("/api/cleanup/job/{job_id}")
async def cleanup_job_files(job_id: str, patient_id: Optional[str] = None):
    """Clean up temporary files for a specific job."""
    try:
        result = cleanup_service.cleanup_job_files(job_id=job_id, patient_id=patient_id)
        return JSONResponse(content=result)
    except Exception as e:
        logger.error(f"Failed to cleanup job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/cleanup/old-files")
async def cleanup_old_temp_files(max_age_hours: int = 24):
    """Clean up old temporary files based on age."""
    try:
        result = cleanup_service.cleanup_old_temp_files(max_age_hours=max_age_hours)
        return JSONResponse(content=result)
    except Exception as e:
        logger.error(f"Failed to cleanup old temp files: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/cleanup/status")
async def get_cleanup_status():
    """Get current status of temporary directories."""
    try:
        result = cleanup_service.get_cleanup_status()
        return JSONResponse(content=result)
    except Exception as e:
        logger.error(f"Failed to get cleanup status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Legacy GATK test endpoint removed - use proper test endpoints in services


@app.get("/api-status", response_class=JSONResponse)
async def api_status():
    """Endpoint to check all API services and list available routes"""
    try:
        # Get the router routes
        routes = []
        for route in app.routes:
            if hasattr(route, "methods") and hasattr(route, "path"):
                routes.append(
                    {
                        "path": route.path,
                        "methods": list(route.methods),
                        "name": route.name,
                    }
                )

        # Check GATK API status
        gatk_status = {"available": False, "message": "Not checked"}
        try:
            gatk_api_url = os.getenv("GATK_API_URL", "http://gatk-api:5000")
            logger.info(f"Checking GATK API status at {gatk_api_url}/health")

            async with aiohttp.ClientSession() as session:
                async with session.get(f"{gatk_api_url}/health", timeout=5) as response:
                    if response.status == 200:
                        gatk_data = await response.json()
                        gatk_status = {
                            "available": True,
                            "message": "Healthy",
                            "details": gatk_data,
                        }
                    else:
                        gatk_status = {
                            "available": False,
                            "message": f"Unhealthy (Status: {response.status})",
                            "response": await response.text(),
                        }
        except Exception as e:
            gatk_status = {"available": False, "message": f"Error connecting: {str(e)}"}

        # Try to connect directly to the test-job endpoint
        test_job_status = {"available": False, "message": "Not checked"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{gatk_api_url}/test-job", timeout=5
                ) as response:
                    if response.status in (200, 202):
                        test_data = await response.json()
                        test_job_status = {
                            "available": True,
                            "message": "Test endpoint working",
                            "job_id": test_data.get("job_id"),
                        }
                    else:
                        test_job_status = {
                            "available": False,
                            "message": f"Test endpoint failed (Status: {response.status})",
                            "response": await response.text(),
                        }
        except Exception as e:
            test_job_status = {
                "available": False,
                "message": f"Error connecting to test-job: {str(e)}",
            }

        return {
            "timestamp": time.time(),
            "gatk_api": gatk_status,
            "test_job_endpoint": test_job_status,
            "routes": routes,
            "app_name": "ZaroPGx API",
            "version": "0.2.8",
        }
    except Exception as e:
        logger.exception(f"Error in api-status endpoint: {str(e)}")
        return {"error": str(e), "traceback": traceback.format_exc()}


@app.get("/services-config", response_class=JSONResponse)
async def services_config():
    """Get current service configuration and toggle status"""
    return {
        "services": {
            "gatk": {"enabled": GATK_ENABLED},
            "pypgx": {"enabled": PYPGX_ENABLED},
            "optitype": {"enabled": OPTITYPE_ENABLED},
            "genome_downloader": {"enabled": GENOME_DOWNLOADER_ENABLED},
            "kroki": {"enabled": KROKI_ENABLED},
            "hapi_fhir": {"enabled": HAPI_FHIR_ENABLED},
            "fhir_export": {
                "enabled": fhir_export_enabled(),
                "description": "FHIR R4 export for pharmacogenomic reports",
                "endpoints": "/fhir/*" if fhir_export_enabled() else None,
            },
            "pharmcat": {
                "enabled": True,
                "absent_to_ref": PHARMCAT_ABSENT_TO_REF,
                "unspecified_to_ref": PHARMCAT_UNSPECIFIED_TO_REF,
            },
        }
    }


@app.get("/services-status", response_class=JSONResponse)
async def services_status(
    request: Request, current_user: str = Depends(get_optional_user)
):
    """Check the status of all services and return a comprehensive health check"""
    # Log the request details for debugging
    logger.info(f"==== SERVICE STATUS CHECK REQUEST ====")
    logger.info(
        f"Client IP: {request.client.host}, Method: {request.method}, Path: {request.url.path}"
    )
    logger.info(f"Headers: {request.headers}")

    # Only check services that are enabled
    services_to_check = {
        "app": {
            "url": "http://localhost:8000/health",  # Using internal port 8000 instead of request.base_url
            "timeout": 5,
            "enabled": True,  # App is always enabled
        },
        "database": {
            "url": os.getenv("DATABASE_URL", ""),
            "timeout": 5,
            "enabled": True,  # Database is always enabled
        },
    }

    # Add enabled services only
    if GATK_ENABLED:
        services_to_check["gatk"] = {
            "url": os.getenv("GATK_API_URL", "http://gatk-api:5000") + "/health",
            "timeout": 10,
            "enabled": True,
        }

    if PYPGX_ENABLED:
        services_to_check["pypgx"] = {
            "url": os.getenv("PYPGX_API_URL", "http://pypgx:5000") + "/health",
            "timeout": 10,
            "enabled": True,
        }

    # PharmCAT is always enabled (core service)
    services_to_check["pharmcat"] = {
        "url": os.getenv("PHARMCAT_API_URL", "http://pharmcat:5000") + "/health",
        "timeout": 10,
        "enabled": True,
    }

    # Add zarohla if OptiType is enabled (zarohla is the OptiType implementation)
    if OPTITYPE_ENABLED:
        services_to_check["zarohla"] = {
            "url": os.getenv("ZAROHLA_API_URL", "http://zarohla:5000") + "/health",
            "timeout": 10,
            "enabled": True,
        }

    # For debugging - log the URLs we're trying to check
    service_urls = []
    for k, v in services_to_check.items():
        if k != "database":
            service_urls.append(f"{k}: {v['url']}")
    logger.info(f"Checking services: {', '.join(service_urls)}")

    # Debugging for environment variables
    logger.info(f"PYPGX_API_URL: {os.getenv('PYPGX_API_URL', 'not set')}")
    logger.info(f"GATK_API_URL: {os.getenv('GATK_API_URL', 'not set')}")
    logger.info(f"PHARMCAT_API_URL: {os.getenv('PHARMCAT_API_URL', 'not set')}")
    logger.info(f"ZAROHLA_API_URL: {os.getenv('ZAROHLA_API_URL', 'not set')}")

    # Check each service
    unhealthy_services = {}
    service_check_results = {}

    # Use httpx for concurrent requests
    async with httpx.AsyncClient() as client:
        # Check app health directly first (no HTTP request)
        logger.info("Checking app health (direct check)")
        service_check_results["app"] = {"status": "healthy", "method": "direct"}

        # Check database separately
        db_service = services_to_check.get("database")
        if db_service:
            logger.info(f"Checking database at {db_service['url']}")
            try:
                # Try to connect to the database
                from sqlalchemy import create_engine, text

                engine = create_engine(db_service["url"])
                with engine.connect() as connection:
                    result = connection.execute(text("SELECT 1"))
                    if not result.fetchone():
                        logger.error("Database connection test failed")
                        unhealthy_services["database"] = (
                            "Database connection test failed"
                        )
                        service_check_results["database"] = {
                            "status": "error",
                            "message": "Connection test failed",
                        }
                    else:
                        logger.info("Database connection test succeeded")
                        service_check_results["database"] = {"status": "healthy"}
            except Exception as e:
                logger.error(f"Database error: {str(e)}")
                unhealthy_services["database"] = f"Database error: {str(e)}"
                service_check_results["database"] = {
                    "status": "error",
                    "message": str(e),
                }

        # Check pypgx with retries
        pypgx_service = services_to_check.get("pypgx")
        if pypgx_service:
            logger.info(f"Checking pypgx at {pypgx_service['url']}")
            max_retries = 2
            retry_count = 0
            success = False

            while retry_count <= max_retries and not success:
                try:
                    logger.info(f"PyPGx check attempt {retry_count+1}/{max_retries+1}")
                    # Add some extra request headers and a very short timeout to avoid blocking
                    response = await client.get(
                        pypgx_service["url"],
                        timeout=5.0,  # Reduced timeout for faster retries
                        headers={"User-Agent": "ZaroPGx-HealthCheck"},
                        follow_redirects=True,
                    )

                    logger.info(
                        f"PyPGx response: status={response.status_code}, body={response.text[:100]}..."
                    )

                    # Accept 200-299 status codes as success
                    if 200 <= response.status_code < 300:
                        success = True
                        service_check_results["pypgx"] = {
                            "status": "healthy",
                            "response_code": response.status_code,
                        }
                        logger.info(
                            f"PyPGx check successful on attempt {retry_count+1}"
                        )
                        break
                    else:
                        retry_count += 1
                        logger.warning(
                            f"PyPGx returned status {response.status_code} (retry {retry_count}/{max_retries})"
                        )
                        service_check_results["pypgx"] = {
                            "status": "error",
                            "response_code": response.status_code,
                            "attempt": retry_count,
                        }
                        await asyncio.sleep(0.5)  # Short delay between retries
                except Exception as e:
                    retry_count += 1
                    logger.warning(
                        f"Error checking PyPGx health (retry {retry_count}/{max_retries}): {str(e)}"
                    )
                    service_check_results["pypgx"] = {
                        "status": "error",
                        "message": str(e),
                        "attempt": retry_count,
                    }
                    await asyncio.sleep(0.5)  # Short delay between retries

            if not success:
                logger.error(
                    f"PyPGx health check failed after {max_retries+1} attempts"
                )
                unhealthy_services["pypgx"] = f"Failed after {max_retries} retries"

        # Check other HTTP services
        for service_name, service_info in services_to_check.items():
            # Skip services we've already checked
            if service_name in ["app", "database", "pypgx"]:
                continue

            logger.info(f"Checking {service_name} at {service_info['url']}")
            try:
                # Add some extra request headers and increase timeout
                response = await client.get(
                    service_info["url"],
                    timeout=service_info["timeout"],
                    headers={"User-Agent": "ZaroPGx-HealthCheck"},
                    follow_redirects=True,
                )

                logger.info(f"{service_name} response: status={response.status_code}")

                # Accept 200-299 status codes as success
                if 200 <= response.status_code < 300:
                    service_check_results[service_name] = {
                        "status": "healthy",
                        "response_code": response.status_code,
                    }
                else:
                    unhealthy_services[service_name] = f"HTTP {response.status_code}"
                    service_check_results[service_name] = {
                        "status": "error",
                        "response_code": response.status_code,
                    }
                    logger.warning(
                        f"Service {service_name} returned status {response.status_code}"
                    )
            except Exception as e:
                logger.warning(f"Error checking {service_name} health: {str(e)}")
                unhealthy_services[service_name] = str(e)
                service_check_results[service_name] = {
                    "status": "error",
                    "message": str(e),
                }

    # Log the final results
    logger.info(f"==== SERVICE STATUS CHECK RESULTS ====")
    for service, result in service_check_results.items():
        logger.info(f"{service}: {result}")

    # Return status
    if unhealthy_services:
        result = {
            "status": "error",
            "message": "Some services are unavailable",
            "unhealthy_services": unhealthy_services,
            "check_time": str(datetime.now()),
        }
        logger.info(f"Returning error result: {result}")
        return result
    else:
        result = {
            "status": "ok",
            "message": "All services are available",
            "check_time": str(datetime.now()),
        }
        logger.info(f"Returning success result: {result}")
        return result


# Wait for services to be ready
@app.on_event("startup")
async def startup_event():
    """Check if required services are ready before starting the app"""
    print("=================== STARTING ZaroPGx ===================")
    logger.info("Starting ZaroPGx application")

    # Remember the main loop so sync WorkflowService methods can schedule
    # WebSocket broadcasts via run_coroutine_threadsafe when off-loop.
    from app.services.job_service import remember_event_loop

    remember_event_loop()

    # Ensure database is properly initialized
    try:
        from app.api.db import init_db

        init_db()
        logger.info("Database connection verified")
        print("✅ Database connection verified")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        print(f"❌ Database initialization failed: {e}")
        # Don't exit, let the app start and handle errors gracefully

    # Services to check
    services = {
        "GATK API": f"{GATK_SERVICE_URL}/health",
        "PharmCAT Wrapper": f"{os.getenv('PHARMCAT_API_URL', 'http://pharmcat:5000')}/health",
        "PyPGx": f"{os.getenv('PYPGX_API_URL', 'http://pypgx:5000')}/health",
    }

    max_retries = 12  # Increased from 6 to 12
    retry_delay = 5  # Reduced from 10 to 5 seconds

    for service_name, service_url in services.items():
        logger.info(f"Checking if {service_name} is ready at {service_url}...")
        print(f"Checking {service_name} at {service_url}")

        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(service_url, timeout=5.0)
                    if response.status_code == 200:
                        logger.info(f"{service_name} is ready!")
                        print(f"✅ {service_name} is ready!")
                        break
                    else:
                        logger.warning(
                            f"{service_name} returned status {response.status_code}"
                        )
                        print(
                            f"⚠️ {service_name} returned status {response.status_code}"
                        )
            except Exception as e:
                logger.warning(
                    f"{service_name} not ready yet (attempt {attempt + 1}/{max_retries}): {str(e)}"
                )
                print(
                    f"⚠️ {service_name} not ready (attempt {attempt + 1}/{max_retries})"
                )

            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
            else:
                logger.warning(
                    f"{service_name} health check failed after {max_retries} attempts, but we'll continue anyway"
                )
                print(
                    f"⚠️ {service_name} health check failed after {max_retries} attempts, continuing anyway"
                )

    # Check temp and data directories
    for dir_path in [TEMP_DIR, DATA_DIR, REPORTS_DIR, UPLOADS_DIR]:
        if not os.path.exists(dir_path):
            try:
                os.makedirs(dir_path, exist_ok=True)
                logger.info(f"Created directory: {dir_path}")
                print(f"Created directory: {dir_path}")
            except Exception as e:
                logger.error(f"Failed to create directory {dir_path}: {str(e)}")
                print(f"❌ Failed to create directory {dir_path}: {str(e)}")
        else:
            logger.info(f"Directory exists: {dir_path}")
            print(f"✅ Directory exists: {dir_path}")

    # Refuse known-weak or missing signing keys. start-docker generates a
    # per-install SECRET_KEY into .env; do not fall back to a public default.
    if SECRET_KEY.strip() in _SECRET_KEY_SENTINELS:
        message = (
            "SECRET_KEY is missing or still a tracked placeholder. "
            "Run ./start-docker.sh (or start-docker.ps1) or set a unique "
            "SECRET_KEY in .env, then restart the app."
        )
        logger.error(message)
        print(f"❌ {message}")
        raise RuntimeError(message)

    auth_mode = resolve_auth_mode()
    bind_address = os.getenv("BIND_ADDRESS", "8765")
    print(f"AUTH MODE: {auth_mode} | BIND_ADDRESS: {bind_address}")
    logger.info("Effective auth mode=%s bind_address=%s", auth_mode, bind_address)
    if auth_mode == "password" and not os.getenv("ZAROPGX_AUTH_PASSWORD"):
        logger.warning(
            "ZAROPGX_AUTH_MODE=password but ZAROPGX_AUTH_PASSWORD is empty — "
            "login will fail until a password is set."
        )

    print(r"""
 _____                    ____  ______    
/__  /  ____ __________  / __ \/ ____/  __
  / /  / __ `/ ___/ __ \/ /_/ / / __| |/_/
 / /__/ /_/ / /  / /_/ / ____/ /_/ />  <  
/____/\__,_/_/   \____/_/    \____/_/|_|  

Welcome to ZaroPGx, an intelligent individual pharmacogenomic analysis pipeline
                      
=================== STARTUP COMPLETE ===================
ZaroPGx is ready and listening for requests!
""")
    logger.info("ZaroPGx startup complete")


# Add middleware to log all requests
@app.middleware("http")
async def log_requests(request: Request, call_next):
    print(f"[REQUEST] {request.method} {request.url.path}")
    logger.info(f"[REQUEST] {request.method} {request.url.path}")
    try:
        response = await call_next(request)
        print(
            f"[RESPONSE] {request.method} {request.url.path} - Status: {response.status_code}"
        )
        logger.info(
            f"[RESPONSE] {request.method} {request.url.path} - Status: {response.status_code}"
        )
        return response
    except Exception as e:
        print(f"[ERROR] {request.method} {request.url.path} - Error: {str(e)}")
        logger.exception(f"Error handling request {request.method} {request.url.path}")
        raise


# Additional endpoints would go here


@app.get("/check-reports/{job_id}")
async def check_reports(job_id: str):
    """
    Check for reports and manually trigger completion notification
    """
    try:
        # Define reports directory
        reports_dir = REPORTS_DIR

        # Prefer nested /data/reports/{patient_id}/{job_id}/ when job metadata has patient_id
        patient_id = None
        try:
            # SessionLocal() + try/finally, not `next(get_db())`: get_db's
            # `finally: db.close()` only runs when the generator is exhausted or
            # closed, and advancing it once never gets there, so every session
            # taken that way leaked its connection.
            from app.api.db import SessionLocal
            from app.services.job_service import JobService

            db = SessionLocal()
            try:
                job_service = JobService(db)
                job = job_service.get_job(job_id)
                if job and job.job_metadata:
                    patient_id = job.job_metadata.get("patient_id")
            finally:
                db.close()
        except Exception:
            patient_id = None

        if patient_id:
            job_reports_dir = reports_dir / str(patient_id) / str(job_id)
        else:
            job_reports_dir = reports_dir / str(job_id)

        # Check for report files
        pdf_path = job_reports_dir / f"{job_id}_pgx_report.pdf"
        html_path = job_reports_dir / f"{job_id}_pgx_report.html"

        pdf_exists = pdf_path.exists()
        html_exists = html_path.exists()

        # Check workflow status using the new workflow system
        try:
            from app.api.db import SessionLocal
            from app.services.job_service import JobService

            # Get database session (see the note above on SessionLocal vs get_db)
            db = SessionLocal()
            try:
                job_service = JobService(db)

                # Look up run-instance Job by id (path param may be job UUID)
                from app.api.models import JobStatus, JobUpdate

                job = job_service.get_job(job_id)
                job_data = {"status": "unknown", "complete": False}

                if job:
                    job_data = {
                        "status": job.status,  # already a string from the database
                        "complete": job.status in ["completed", "failed"],
                        "job_id": str(job.id),
                    }

                    # Update job status if reports exist
                    if pdf_exists or html_exists and job.status != "completed":
                        job_service.update_job(
                            job.id,
                            JobUpdate(status=JobStatus.COMPLETED),
                        )
                        logger.info(f"Updated job status for job {job_id} to completed")
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"Could not check workflow status for job {job_id}: {e}")
            job_data = {"status": "unknown", "complete": False}

        if patient_id:
            pdf_url = (
                f"/reports/{patient_id}/{job_id}/{job_id}_pgx_report.pdf"
                if pdf_exists
                else None
            )
            html_url = (
                f"/reports/{patient_id}/{job_id}/{job_id}_pgx_report.html"
                if html_exists
                else None
            )
        else:
            pdf_url = f"/reports/{job_id}_pgx_report.pdf" if pdf_exists else None
            html_url = f"/reports/{job_id}_pgx_report.html" if html_exists else None

        return {
            "job_id": job_id,
            "reports": {
                "pdf_exists": pdf_exists,
                "pdf_path": str(pdf_path) if pdf_exists else None,
                "pdf_url": pdf_url,
                "html_exists": html_exists,
                "html_path": str(html_path) if html_exists else None,
                "html_url": html_url,
            },
            "job_status": job_data,
            "instructions": "To check your report, click on the PDF or HTML URL link.",
        }
    except Exception as e:
        logger.exception(f"Error checking reports: {str(e)}")
        return {"status": "error", "message": f"Error checking reports: {str(e)}"}


@app.get("/trigger-completion/{job_id}", response_class=HTMLResponse)
async def trigger_completion(job_id: str):
    """
    A troubleshooting endpoint to manually trigger completion flow and provide direct report links.
    This is a backup method when the SSE progress monitor fails to notify the frontend.
    """
    # Prefer nested /data/reports/{patient_id}/{job_id}/ when job metadata has patient_id
    patient_id = None
    try:
        # SessionLocal() + try/finally, not `next(get_db())` -- see check_reports.
        from app.api.db import SessionLocal
        from app.services.job_service import JobService

        db = SessionLocal()
        try:
            job_service = JobService(db)
            job = job_service.get_job(job_id)
            if job and job.job_metadata:
                patient_id = job.job_metadata.get("patient_id")
        finally:
            db.close()
    except Exception:
        patient_id = None

    if patient_id:
        job_reports_dir = Path(f"/data/reports/{patient_id}/{job_id}")
        pdf_path = str(job_reports_dir / f"{job_id}_pgx_report.pdf")
        html_path = str(job_reports_dir / f"{job_id}_pgx_report.html")
        pdf_href = f"/reports/{patient_id}/{job_id}/{job_id}_pgx_report.pdf"
        html_href = f"/reports/{patient_id}/{job_id}/{job_id}_pgx_report.html"
    else:
        pdf_path = f"/data/reports/{job_id}_pgx_report.pdf"
        html_path = f"/data/reports/{job_id}_pgx_report.html"
        pdf_href = f"/reports/{job_id}_pgx_report.pdf"
        html_href = f"/reports/{job_id}_pgx_report.html"

    pdf_exists = os.path.exists(pdf_path)
    html_exists = os.path.exists(html_path)

    # Update workflow status if reports exist
    if pdf_exists or html_exists:
        try:
            from app.api.db import SessionLocal
            from app.services.job_service import JobService

            # Get database session (SessionLocal + try/finally, see check_reports)
            db = SessionLocal()
            try:
                job_service = JobService(db)

                from app.api.models import JobStatus, JobUpdate

                job = job_service.get_job(job_id)

                if job:
                    job_service.update_job(
                        job.id,
                        JobUpdate(status=JobStatus.COMPLETED),
                    )
                    logger.info(
                        f"Manual trigger for job {job_id} - Job status updated to completed"
                    )
                else:
                    logger.warning(f"Manual trigger for job {job_id} - No job found")
            finally:
                db.close()
        except Exception as e:
            logger.error(f"Manual trigger for job {job_id} - Error updating job: {e}")
    else:
        logger.error(
            f"Manual trigger for job {job_id} - No reports found at expected locations"
        )

    # Return an HTML page with direct links and troubleshooting help
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>PharmGx Report Manual Access</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/css/bootstrap.min.css" rel="stylesheet">
        <style>
            body {{ padding: 20px; }}
            .report-link {{ margin: 10px 0; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1 class="mb-4">PharmGx Report Manual Access</h1>
            
            <div class="card mb-4">
                <div class="card-header bg-primary text-white">
                    <h3>Job Status and Reports</h3>
                </div>
                <div class="card-body">
                    <p><strong>Job ID:</strong> {job_id}</p>
                    <p><strong>PDF Report:</strong> {"Available" if pdf_exists else "Not found"}</p>
                    <p><strong>HTML Report:</strong> {"Available" if html_exists else "Not found"}</p>
                    
                    <div class="report-link">
                        <h4>Direct Report Links:</h4>
                        {"<a href='" + pdf_href + "' class='btn btn-primary' target='_blank'>View PDF Report</a>" if pdf_exists else "<span class='text-danger'>PDF report not found</span>"}
                    </div>
                    
                    <div class="report-link">
                        {"<a href='" + html_href + "' class='btn btn-info' target='_blank'>View HTML Report</a>" if html_exists else "<span class='text-danger'>HTML report not found</span>"}
                    </div>
                </div>
            </div>
            
            <div class="card">
                <div class="card-header bg-info text-white">
                    <h3>Troubleshooting Information</h3>
                </div>
                <div class="card-body">
                    <p>If the main interface doesn't display your reports, you can use the links above to access them directly.</p>
                    <p>Job Status Information:</p>
                    <pre>{json.dumps({"job_id": job_id, "pdf_exists": pdf_exists, "html_exists": html_exists}, indent=2)}</pre>
                    
                    <div class="mt-3">
                        <a href="/" class="btn btn-secondary">Return to Main Page</a>
                    </div>
                </div>
            </div>
        </div>
    </body>
    </html>
    """

    return HTMLResponse(content=html_content)


@app.post("/reprocess-report/{report_id}")
async def reprocess_report(report_id: str):
    """
    Reprocess an existing report by re-running PharmCAT analysis with the updated parser.
    This is primarily for testing parser changes.
    Path param is treated as job_id (display report_id = job_id).
    """
    try:
        job_id = report_id
        logger.info(f"Reprocessing report {job_id}")

        # Resolve patient_id from job metadata when available for nested layout
        patient_id = None
        try:
            # SessionLocal() + try/finally, not `next(get_db())` -- see check_reports.
            from app.api.db import SessionLocal
            from app.services.job_service import JobService

            db = SessionLocal()
            try:
                job_service = JobService(db)
                job = job_service.get_job(job_id)
                if job and job.job_metadata:
                    patient_id = job.job_metadata.get("patient_id")
            finally:
                db.close()
        except Exception:
            patient_id = None

        if patient_id:
            report_dir = Path(f"/data/reports/{patient_id}/{job_id}")
        else:
            report_dir = Path(f"/data/reports/{job_id}")
        uploads_dir = Path(f"/data/uploads/{patient_id or job_id}")

        # Look for VCF files in both directories
        vcf_files = []
        for directory in [report_dir, uploads_dir]:
            if directory.exists():
                vcf_files.extend(
                    list(directory.glob("*.vcf")) + list(directory.glob("*.vcf.gz"))
                )

        if not vcf_files:
            # If no VCF files found, return an error
            logger.error(f"No VCF files found for report {job_id}")
            return {
                "success": False,
                "message": f"No VCF files found for report {job_id}",
            }

        # Use the first VCF file found
        vcf_path = str(vcf_files[0])
        logger.info(f"Using VCF file: {vcf_path}")

        # Run PharmCAT analysis with the existing report ID
        results = await pharmcat_client.async_call_pharmcat_api(vcf_path)

        # Check if the analysis was successful
        if not results.get("success", False):
            logger.error(
                f"PharmCAT analysis failed for report {job_id}: {results.get('message', 'Unknown error')}"
            )
            return {
                "success": False,
                "message": f"PharmCAT analysis failed: {results.get('message', 'Unknown error')}",
            }

        # Generate reports using the updated results
        from app.reports.generator import generate_report

        # Create patient info dictionary (pass data_id explicitly, not report_id alias)
        resolved_patient_id = patient_id or f"patient_{job_id}"
        patient_info = {
            "id": resolved_patient_id,
            "data_id": job_id,
            "name": f"Patient {resolved_patient_id}",
            "age": "N/A",
            "sex": "N/A",
            "encounter_date": datetime.now().strftime("%Y-%m-%d"),
        }

        # Generate report files under nested outdir when patient_id known
        report_paths = generate_report(
            results,
            str(report_dir),
            patient_info,
            job_id=job_id,
        )

        # Return the results with report paths
        return {
            "success": True,
            "message": "Report reprocessed successfully",
            "data": {
                "report_id": job_id,
                "report_paths": report_paths,
                "genes": results.get("data", {}).get("genes", []),
                "drugRecommendations": results.get("data", {}).get(
                    "drugRecommendations", []
                ),
            },
        }

    except Exception as e:
        logger.error(f"Error reprocessing report {report_id}: {str(e)}")
        logger.error(traceback.format_exc())
        return {"success": False, "message": f"Error reprocessing report: {str(e)}"}
