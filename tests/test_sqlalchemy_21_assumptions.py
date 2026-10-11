"""SQLAlchemy 2.1 changed two behaviours; ZaroPGx is insulated from both by
facts these tests pin.

1. 2.1 autoflushes on *every* execute (Core text() included) when autoflush is
   on. SessionLocal turns it off, and several call sites rely on that
   (app/services/job_service.py comments at the populate_existing reads).
2. 2.1's filter_by() searches every FROM entity and raises AmbiguousColumnError
   on a shared column name. The app does not call filter_by() at all.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_sessionlocal_keeps_autoflush_off():
    from app.api.db import SessionLocal

    assert SessionLocal.kw["autoflush"] is False


def test_app_does_not_use_filter_by():
    hits = [
        f"{p.relative_to(ROOT)}:{n}"
        for p in (ROOT / "app").rglob("*.py")
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(r"\.filter_by\(", line)
    ]
    assert not hits, f"filter_by() is ambiguity-prone under SQLAlchemy 2.1: {hits}"
