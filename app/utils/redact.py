"""Keep the database password out of logs and API answers.

A connection error can quote the connection string it was given, password and
all: psycopg refused DATABASE_URL's SQLAlchemy form (``postgresql+psycopg://``)
with an error that repeated the whole URL, and the app logged it on every start.
"""

from __future__ import annotations

import os
import re

_URL_PASSWORD = re.compile(r"(://[^/:@\s]*:)[^@\s]+@")


def redact_secrets(text: object) -> str:
    """``text`` with the password of any ``scheme://user:password@`` URL, and the
    database password the environment holds, replaced by ``***``."""
    redacted = _URL_PASSWORD.sub(r"\1***@", str(text))
    for name in ("DB_PASSWORD", "POSTGRES_PASSWORD"):
        secret = os.environ.get(name) or ""
        if len(secret) >= 4:
            redacted = redacted.replace(secret, "***")
    return redacted
