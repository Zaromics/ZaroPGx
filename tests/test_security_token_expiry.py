"""Regression test: create_access_token's no-expires_delta fallback must
honor ACCESS_TOKEN_EXPIRE_MINUTES rather than a hardcoded 15-minute default.
"""

from datetime import datetime, timedelta, timezone

from jose import jwt

from app.api.utils import security


def test_create_access_token_fallback_uses_configured_expiry(monkeypatch):
    # Use a value that is unambiguously distinct from both the old hardcoded
    # fallback (15) and the module default (30).
    monkeypatch.setattr(security, "ACCESS_TOKEN_EXPIRE_MINUTES", 5)

    before = datetime.now(timezone.utc)
    token = security.create_access_token(data={"sub": "test"})
    after = datetime.now(timezone.utc)

    payload = jwt.decode(token, security.SECRET_KEY, algorithms=[security.ALGORITHM])
    expire = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)

    # JWT "exp" is truncated to whole seconds, so allow a 1s tolerance.
    assert before + timedelta(minutes=5, seconds=-1) <= expire
    assert expire <= after + timedelta(minutes=5)
    # Guard against the old hardcoded 15-minute fallback regressing back in.
    assert expire < before + timedelta(minutes=10)
