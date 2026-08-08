"""BACKLOG 345 - coverage for the JWT helpers in app/api/utils/security.py.

Three things are pinned down here:

1. ``create_access_token`` - claim round-tripping, the expiry it actually
   stamps, and that a token it did not mint is rejected (expired, re-signed,
   tampered, or "alg": "none").

2. ``OptionalOAuth2PasswordBearer`` - header parsing with
   ``ZAROPGX_DEV_MODE=false``, which no existing test exercises.

3. ``get_optional_user`` - which returns the literal string ``"test"`` for a
   missing token, a missing ``sub`` claim, and any JWTError, *including in
   production mode*.  That is deliberate and deferred (BACKLOG 419): the real
   authentication boundary is now the ASGI front-door gate in
   app/api/middleware/auth_gate.py and the UI sends no bearer token, so this
   dependency is decoration rather than a gate.  The tests below assert that
   behaviour as-is; they are a tripwire for it changing by accident, not an
   endorsement.  What they do enforce is that a forged token can never make
   ``get_optional_user`` return the *attacker's* identity.

SECRET_KEY / ALGORITHM are read once at import time, so tests read them off the
module rather than trying to monkeypatch the environment.
"""

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import Request
from jose import jwt
from jose.exceptions import JWTError

from app.api.utils import security
from app.api.utils.security import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    ALGORITHM,
    SECRET_KEY,
    OptionalOAuth2PasswordBearer,
    create_access_token,
    get_optional_user,
    optional_oauth2_scheme,
)

OTHER_SECRET = "an-attacker-controlled-signing-key"


@pytest.fixture
def prod_mode(monkeypatch):
    """Leave development mode, which conftest turns on for the whole suite."""
    monkeypatch.setenv("ZAROPGX_DEV_MODE", "false")


def _request(**headers) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "query_string": b"",
            "headers": raw,
        }
    )


def _b64(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode(token: str) -> dict:
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])


def _unverified_claims(token: str) -> dict:
    """Read the payload segment without validating - used to prove test setup."""
    payload = token.split(".")[1]
    padded = payload + "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


def _exp_of(token: str) -> datetime:
    return datetime.fromtimestamp(_decode(token)["exp"], tz=timezone.utc)


# ---------------------------------------------------------------------------
# create_access_token
# ---------------------------------------------------------------------------


def test_create_access_token_round_trips_claims():
    token = create_access_token({"sub": "alice", "scope": "reports:read"})
    claims = _decode(token)

    assert claims["sub"] == "alice"
    assert claims["scope"] == "reports:read"
    assert "exp" in claims


def test_create_access_token_signs_with_the_configured_algorithm():
    header = jwt.get_unverified_header(create_access_token({"sub": "alice"}))
    assert header["alg"] == ALGORITHM


def test_create_access_token_default_expiry_is_fifteen_minutes():
    """The default is a hard-coded 15 minutes, NOT ACCESS_TOKEN_EXPIRE_MINUTES.

    Callers that want the configured lifetime have to pass expires_delta
    themselves - main.py's /token endpoint does, auth_gate mints its own.  If
    that hard-coded default is ever unified with the setting, this test is the
    one that should be updated deliberately.
    """
    before = datetime.now(timezone.utc)
    exp = _exp_of(create_access_token({"sub": "alice"}))

    assert timedelta(minutes=14) <= exp - before <= timedelta(minutes=16)
    assert ACCESS_TOKEN_EXPIRE_MINUTES == 30


def test_create_access_token_honours_expires_delta():
    before = datetime.now(timezone.utc)
    exp = _exp_of(create_access_token({"sub": "alice"}, timedelta(hours=8)))

    assert (
        timedelta(hours=7, minutes=59) <= exp - before <= timedelta(hours=8, minutes=1)
    )


def test_create_access_token_does_not_mutate_the_caller_dict():
    data = {"sub": "alice"}
    create_access_token(data)
    assert data == {"sub": "alice"}


# ---------------------------------------------------------------------------
# token rejection
# ---------------------------------------------------------------------------


def test_expired_token_is_rejected():
    """A negative expires_delta mints a token that is already past its exp."""
    token = create_access_token({"sub": "alice"}, timedelta(minutes=-1))
    # The exp claim really is in the past, so the rejection is not incidental.
    assert _unverified_claims(token)["exp"] < datetime.now(timezone.utc).timestamp()
    with pytest.raises(JWTError):
        _decode(token)


def test_token_signed_with_another_key_is_rejected():
    forged = jwt.encode(
        {"sub": "attacker", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        OTHER_SECRET,
        algorithm=ALGORITHM,
    )
    with pytest.raises(JWTError):
        _decode(forged)


def test_tampered_payload_is_rejected():
    header, _, signature = create_access_token({"sub": "alice"}).split(".")
    swapped = _b64(
        {
            "sub": "root",
            "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()),
        }
    )
    with pytest.raises(JWTError):
        _decode(f"{header}.{swapped}.{signature}")


def test_tampered_signature_is_rejected():
    header, payload, signature = create_access_token({"sub": "alice"}).split(".")
    flipped = ("B" if signature[0] != "B" else "C") + signature[1:]
    with pytest.raises(JWTError):
        _decode(f"{header}.{payload}.{flipped}")


def test_unsigned_alg_none_token_is_rejected():
    """The classic JWT downgrade: an unsigned token must never be honoured."""
    unsigned = "{}.{}.".format(
        _b64({"alg": "none", "typ": "JWT"}),
        _b64(
            {
                "sub": "attacker",
                "exp": int(
                    (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()
                ),
            }
        ),
    )
    with pytest.raises(JWTError):
        _decode(unsigned)


@pytest.mark.parametrize(
    "garbage", ["", "not-a-token", "a.b", "a.b.c.d", "....", "Bearer x.y.z"]
)
def test_malformed_tokens_are_rejected(garbage):
    with pytest.raises(JWTError):
        _decode(garbage)


# ---------------------------------------------------------------------------
# OptionalOAuth2PasswordBearer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scheme_returns_none_in_dev_mode_even_with_a_valid_header(monkeypatch):
    monkeypatch.setenv("ZAROPGX_DEV_MODE", "true")
    token = create_access_token({"sub": "alice"})
    assert (
        await optional_oauth2_scheme(_request(Authorization=f"Bearer {token}")) is None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["true", "True", "TRUE"])
async def test_scheme_dev_mode_flag_is_case_insensitive(monkeypatch, value):
    monkeypatch.setenv("ZAROPGX_DEV_MODE", value)
    assert await optional_oauth2_scheme(_request(Authorization="Bearer tok")) is None


@pytest.mark.asyncio
async def test_scheme_defaults_to_dev_mode_when_unset(monkeypatch):
    monkeypatch.delenv("ZAROPGX_DEV_MODE", raising=False)
    assert await optional_oauth2_scheme(_request(Authorization="Bearer tok")) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["1", "yes", "on", "dev", " true"])
async def test_scheme_only_the_literal_true_enables_dev_mode(monkeypatch, value):
    """Trap worth pinning: only "true" counts, everything else is production.

    ZAROPGX_DEV_MODE=1 in a .env file reads as *not* dev mode here, which is the
    opposite of what an operator writing "1" would expect.  auth_gate.py parses
    the same variable the same way, so at least the two agree.
    """
    monkeypatch.setenv("ZAROPGX_DEV_MODE", value)
    assert await optional_oauth2_scheme(_request(Authorization="Bearer tok")) == "tok"


@pytest.mark.asyncio
async def test_scheme_extracts_bearer_token_in_production(prod_mode):
    assert await optional_oauth2_scheme(_request(Authorization="Bearer tok-123")) == (
        "tok-123"
    )


@pytest.mark.asyncio
async def test_scheme_bearer_keyword_is_case_insensitive(prod_mode):
    assert await optional_oauth2_scheme(_request(Authorization="bearer tok-123")) == (
        "tok-123"
    )


@pytest.mark.asyncio
async def test_scheme_returns_none_without_an_authorization_header(prod_mode):
    # auto_error=False is the whole point of the subclass: no 401 is raised.
    assert await optional_oauth2_scheme(_request()) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "header", ["Basic dXNlcjpwYXNz", "Token tok-123", "tok-123", ""]
)
async def test_scheme_returns_none_for_non_bearer_authorization(prod_mode, header):
    assert await optional_oauth2_scheme(_request(Authorization=header)) is None


@pytest.mark.asyncio
async def test_scheme_bearer_without_a_value_yields_empty_string(prod_mode):
    """Boundary: "Bearer" alone is not None, so downstream gets "" to decode."""
    assert await optional_oauth2_scheme(_request(Authorization="Bearer")) == ""


def test_scheme_never_auto_errors():
    assert OptionalOAuth2PasswordBearer(tokenUrl="token").auto_error is False
    assert optional_oauth2_scheme.auto_error is False
    # The plain oauth2_scheme exported alongside it must be non-raising too.
    assert security.oauth2_scheme.auto_error is False


# ---------------------------------------------------------------------------
# get_optional_user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_optional_user_returns_test_in_dev_mode(monkeypatch):
    monkeypatch.setenv("ZAROPGX_DEV_MODE", "true")
    token = create_access_token({"sub": "alice"})
    # Dev mode short-circuits before the token is even looked at.
    assert await get_optional_user(token=token) == "test"
    assert await get_optional_user(token=None) == "test"


@pytest.mark.asyncio
async def test_get_optional_user_returns_the_subject_of_a_valid_token(prod_mode):
    token = create_access_token({"sub": "alice"})
    assert await get_optional_user(token=token) == "alice"


@pytest.mark.asyncio
async def test_get_optional_user_falls_back_to_test_without_a_token(prod_mode):
    # Deliberate and deferred - see BACKLOG 419 and this module's docstring.
    assert await get_optional_user(token=None) == "test"


@pytest.mark.asyncio
async def test_get_optional_user_falls_back_to_test_without_a_sub_claim(prod_mode):
    token = create_access_token({"scope": "reports:read"})
    assert await get_optional_user(token=token) == "test"


@pytest.mark.asyncio
async def test_get_optional_user_falls_back_to_test_on_an_expired_token(prod_mode):
    token = create_access_token({"sub": "alice"}, timedelta(minutes=-1))
    assert await get_optional_user(token=token) == "test"


@pytest.mark.asyncio
async def test_get_optional_user_rejects_a_token_signed_with_another_key(prod_mode):
    """The identity in a forged token must never come back out."""
    forged = jwt.encode(
        {"sub": "attacker", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        OTHER_SECRET,
        algorithm=ALGORITHM,
    )
    assert await get_optional_user(token=forged) == "test"


@pytest.mark.asyncio
async def test_get_optional_user_rejects_an_unsigned_token(prod_mode):
    unsigned = "{}.{}.".format(
        _b64({"alg": "none", "typ": "JWT"}),
        _b64(
            {
                "sub": "attacker",
                "exp": int(
                    (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()
                ),
            }
        ),
    )
    assert await get_optional_user(token=unsigned) == "test"


@pytest.mark.asyncio
@pytest.mark.parametrize("garbage", ["", "not-a-token", "a.b.c"])
async def test_get_optional_user_never_raises_on_garbage(prod_mode, garbage):
    assert await get_optional_user(token=garbage) == "test"
