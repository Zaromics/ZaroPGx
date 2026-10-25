"""Uploads in flight, for the scripts that stop the stack.

An upload is received in full before its endpoint runs (FastAPI parses the
multipart body first), so no endpoint can count it while it arrives. This raw
ASGI middleware counts every POST under /upload/ from its first byte until its
response, and any background work it starts, is done. /internal/activity
reports the count with the jobs still running, and start-docker.sh and
start-docker.ps1 ask it before they stop the stack: a restart fails a job that
is running and an upload that has not finished arriving (a whole genome's BAM
takes a quarter of an hour).

Raw ASGI like the auth gate, so the streaming upload is not buffered.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send

_in_flight = 0


def uploads_in_flight() -> int:
    return _in_flight


def is_upload(scope: Scope) -> bool:
    return (
        scope.get("type") == "http"
        and scope.get("method") == "POST"
        and str(scope.get("path", "")).startswith("/upload/")
    )


class UploadActivityMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not is_upload(scope):
            await self.app(scope, receive, send)
            return
        global _in_flight
        _in_flight += 1
        try:
            await self.app(scope, receive, send)
        finally:
            _in_flight -= 1
