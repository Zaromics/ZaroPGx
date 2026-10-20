"""Contain filesystem paths under a resolved base directory."""

from __future__ import annotations

from pathlib import Path


def resolve_under(base: Path, *parts: str | Path) -> Path:
    """
    Resolve ``base/parts`` and require the result stay under ``base``.

    Uses ``Path.is_relative_to`` (not ``str.startswith``) so sibling directories
    that share a prefix (e.g. ``/data/reports-old`` vs ``/data/reports``) are
    rejected. Raises ``PermissionError`` on escape attempts or resolve errors.

    On Python 3.12 ``resolve()`` raises RuntimeError for a symlink loop and
    ValueError for a NUL in the path (``/reports/p/x%00y``), not OSError; both are
    a path that names no file here, refused like the rest instead of escaping as a
    500.
    """
    try:
        base_resolved = base.resolve()
        candidate = base.joinpath(*parts).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise PermissionError(f"Invalid path under {base}: {exc}") from exc

    if not candidate.is_relative_to(base_resolved):
        raise PermissionError(
            f"Path {candidate} escapes base directory {base_resolved}"
        )
    return candidate
