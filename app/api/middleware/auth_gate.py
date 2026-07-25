"""Front-door auth gate (Wave 2 Step 7 / design 4b).

Raw ASGI middleware — not BaseHTTPMiddleware — so the 2 GB streaming upload
path is not buffered by Starlette's BaseHTTPMiddleware body cache.

Modes (ZAROPGX_AUTH_MODE):
  open     — pass everything (default; behaviourally a no-op for existing installs)
  audit    — resolve identity, log would-deny at WARNING, still pass
  password — require a session cookie or Authorization: Bearer

Cookie sessions use SameSite=Lax deliberately: report downloads are plain
anchor navigations (index.html), and Strict would drop the cookie on those
top-level GETs from some browser contexts. Do not "harden" to Strict without
re-testing report download links.

Legacy alias: ZAROPGX_DEV_MODE=false with unset ZAROPGX_AUTH_MODE maps to
open and logs a loud warning. Existing .env.production users believed they
had auth; they never did. Default-open keeps git pull && up a no-op.
"""

from __future__ import annotations

import logging
import os
from typing import Optional
from urllib.parse import quote

from jose import JWTError, jwt
from starlette.datastructures import Headers
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.utils.security import ALGORITHM, SECRET_KEY

logger = logging.getLogger("app.auth_gate")

COOKIE_NAME = "zaropgx_auth"
# SameSite=Lax is load-bearing for anchor-href report downloads — see module docstring.
COOKIE_SAMESITE = "lax"
AUTH_MODES = frozenset({"open", "audit", "password"})

# Paths that never require a gate credential. Prefixes end with "/".
ALLOWLIST_EXACT = frozenset(
    {
        "/health",
        "/openapi.json",
        "/docs",
        "/redoc",
        "/docs/oauth2-redirect",
        "/api-reference",
        "/login",
        "/logout",
        "/token",
        "/favicon.ico",
    }
)
ALLOWLIST_PREFIXES = (
    "/static/",
    "/documentation/",
    "/api/v1/workflows/",
)


def resolve_auth_mode() -> str:
    """Return the effective auth mode, applying the asymmetric DEV_MODE alias."""
    explicit = (os.getenv("ZAROPGX_AUTH_MODE") or "").strip().lower()
    if explicit:
        if explicit not in AUTH_MODES:
            logger.warning(
                "Unknown ZAROPGX_AUTH_MODE=%r; falling back to open. "
                "Valid values: open, audit, password.",
                explicit,
            )
            return "open"
        return explicit

    # Asymmetric legacy alias: DEV_MODE=false does NOT enable password mode.
    if os.getenv("ZAROPGX_DEV_MODE", "true").lower() == "false":
        logger.warning(
            "ZAROPGX_DEV_MODE=false is no longer an auth switch. "
            "Effective mode is open. Set ZAROPGX_AUTH_MODE=password to enforce "
            "the front-door gate (and set ZAROPGX_AUTH_PASSWORD)."
        )
    return "open"


def is_allowlisted(path: str) -> bool:
    if path in ALLOWLIST_EXACT:
        return True
    if path.startswith("/docs/") or path.startswith("/redoc/"):
        return True
    for prefix in ALLOWLIST_PREFIXES:
        if path.startswith(prefix):
            return True
    if path == "/api/v1/workflows":
        return True
    return False


def _client_ip(scope: Scope) -> str:
    client = scope.get("client")
    if client:
        return str(client[0])
    return "unknown"


def gate_password() -> str:
    return (os.getenv("ZAROPGX_AUTH_PASSWORD") or "").strip()


def mint_session_token(subject: str = "gate") -> str:
    """Mint a JWT used as both the session cookie value and a Bearer token."""
    return jwt.encode({"sub": subject, "gate": True}, SECRET_KEY, algorithm=ALGORITHM)


def identity_from_headers(headers: Headers) -> Optional[str]:
    """Return a subject string if cookie or Bearer proves gate access."""
    cookie_header = headers.get("cookie") or ""
    for part in cookie_header.split(";"):
        part = part.strip()
        if part.startswith(f"{COOKIE_NAME}="):
            token = part.split("=", 1)[1].strip()
            subject = _decode_gate_token(token)
            if subject:
                return subject

    auth = headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        subject = _decode_gate_token(token)
        if subject:
            return subject
        if token and token == gate_password():
            return "gate"
    return None


def _decode_gate_token(token: str) -> Optional[str]:
    if not token or not SECRET_KEY:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None
    sub = payload.get("sub")
    return str(sub) if sub else None


def check_password(password: str) -> bool:
    expected = gate_password()
    if not expected:
        return False
    return password == expected


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite=COOKIE_SAMESITE,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/")


class AuthGateMiddleware:
    """Pure ASGI auth gate. Constructed by Starlette's add_middleware()."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path") or "/"
        method = scope.get("method") or "GET"
        headers = Headers(scope=scope)
        mode = resolve_auth_mode()
        # CORS preflight must reach CORSMiddleware; never challenge OPTIONS.
        allowlisted = is_allowlisted(path) or method == "OPTIONS"
        identity = identity_from_headers(headers)

        if mode == "open" or allowlisted:
            await self.app(scope, receive, send)
            return

        if identity:
            await self.app(scope, receive, send)
            return

        ip = _client_ip(scope)
        if mode == "audit":
            logger.warning(
                "would-deny %s %s from %s (ZAROPGX_AUTH_MODE=audit)",
                method,
                path,
                ip,
            )
            await self.app(scope, receive, send)
            return

        logger.warning(
            "denied %s %s from %s reason=unauthenticated",
            method,
            path,
            ip,
        )
        accept = (headers.get("accept") or "").lower()
        wants_html = "text/html" in accept and "application/json" not in accept
        if wants_html and method in {"GET", "HEAD"}:
            response: Response = RedirectResponse(
                url=f"/login?next={quote(path, safe='/:?=&')}",
                status_code=303,
            )
        else:
            response = JSONResponse(
                {"detail": "Authentication required"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        await response(scope, receive, send)
