"""Shared boolean environment-flag parser.

Rule (adopted decision): an explicitly blank env var (``VAR=`` in a `.env`
file, or an empty string set some other way) means the flag is **off**,
regardless of the call site's own default. Unset means the call site's
default applies. Truthy spellings are ``1``/``true``/``yes``/``on``
(case-insensitive, surrounding whitespace ignored); anything else that is
set but unrecognised also reads False.
"""

from __future__ import annotations

import os

_TRUTHY = {"1", "true", "yes", "on"}


def env_flag(name: str, default: bool = False) -> bool:
    """Read a boolean environment variable.

    - Unset -> ``default``.
    - Set to an empty or whitespace-only string -> ``False`` (blank means off,
      even when ``default`` is True).
    - Set to one of ``1``/``true``/``yes``/``on`` (case-insensitive, stripped)
      -> ``True``.
    - Set to anything else -> ``False``.

    Reads ``os.environ`` lazily at call time -- nothing is cached at import,
    so flipping the environment after import is honored on the next call.
    """
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in _TRUTHY
