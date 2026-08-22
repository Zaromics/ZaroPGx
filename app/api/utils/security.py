import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer
from fastapi.security.utils import get_authorization_scheme_param
from jose import JWTError, jwt

# Single source for JWT settings. main.py imports these; do not re-read them there.
SECRET_KEY = os.getenv("SECRET_KEY", "")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
try:
    ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
except ValueError:
    ACCESS_TOKEN_EXPIRE_MINUTES = 30

# OAuth2 scheme with auto_error=False to prevent 401s
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)


# Custom OAuth2 scheme that never raises 401s - for development only
class OptionalOAuth2PasswordBearer(OAuth2PasswordBearer):
    def __init__(self, tokenUrl: str):
        super().__init__(tokenUrl=tokenUrl, auto_error=False)

    async def __call__(self, request: Request) -> Optional[str]:
        # Development mode - never require authentication
        # Blank ZAROPGX_DEV_MODE already reads as off here ("" != "true"), which
        # matches the adopted blank-means-off rule (app/utils/env.py) -- left as
        # its own parse rather than migrated, per that task's explicit scope.
        if os.getenv("ZAROPGX_DEV_MODE", "true").lower() == "true":
            return None

        # Production mode - use normal behavior but don't auto error
        authorization = request.headers.get("Authorization")
        scheme, param = get_authorization_scheme_param(authorization)
        if not authorization or scheme.lower() != "bearer":
            return None
        return param


# Use this for development (doesn't require authentication)
optional_oauth2_scheme = OptionalOAuth2PasswordBearer(tokenUrl="token")


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


# Truly optional authentication - never raises 401 errors.
# get_current_user was deleted in Wave 2 Step 4a: nothing used it as Depends().
async def get_optional_user(token: Optional[str] = Depends(optional_oauth2_scheme)):
    # Always return a default user in development mode
    # Blank ZAROPGX_DEV_MODE already reads as off here too -- same rule, same
    # reasoning as the check above.
    if os.getenv("ZAROPGX_DEV_MODE", "true").lower() == "true":
        return "test"

    # If token is missing, return None
    if token is None:
        return "test"

    # If token is provided, try to validate it but don't raise errors
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            return "test"
        return username
    except JWTError:
        return "test"


# encrypt_data / decrypt_data stubs removed (Wave 3): they returned
# f"encrypted_{data}" / prefix-strip while claiming HIPAA compliance, with zero
# callers. Real PHI encryption belongs with key management in a later security wave.
