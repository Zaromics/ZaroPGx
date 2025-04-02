import os
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from fastapi.security.utils import get_authorization_scheme_param
from jose import JWTError, jwt
from datetime import datetime, timedelta
from typing import Optional

# These imports would be used in a real application
# from app.api.models import TokenData
# from app.api.db import get_user

# Constants
SECRET_KEY = os.getenv("SECRET_KEY", "supersecretkey")  # In production, use env var
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# OAuth2 scheme with auto_error=False to prevent 401s
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)

# Custom OAuth2 scheme that never raises 401s - for development only
class OptionalOAuth2PasswordBearer(OAuth2PasswordBearer):
    def __init__(self, tokenUrl: str):
        super().__init__(tokenUrl=tokenUrl, auto_error=False)
        
    async def __call__(self, request: Request) -> Optional[str]:
        # Development mode - never require authentication
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

# Function to create access token
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

# Function to validate token and get current user
async def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if token is None:
        raise credentials_exception
        
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        # In a real app, we would get the user from the database
        # token_data = TokenData(username=username)
        # user = get_user(username=token_data.username)
        # if user is None:
        #     raise credentials_exception
        # return user
        return username  # Simplified for this prototype
    except JWTError:
        raise credentials_exception

# Truly optional authentication - never raises 401 errors
async def get_optional_user(token: Optional[str] = Depends(optional_oauth2_scheme)):
    # Always return a default user in development mode
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

# Function to encrypt sensitive data (HIPAA compliance)
def encrypt_data(data: str) -> str:
    """
    Encrypt sensitive patient data for HIPAA compliance.
    """
    # In a real application, this would use proper encryption
    # For now, we'll just implement a placeholder
    # Using libraries like cryptography.fernet would be appropriate
    return f"encrypted_{data}"

# Function to decrypt sensitive data
def decrypt_data(encrypted_data: str) -> str:
    """
    Decrypt encrypted patient data.
    """
    # In a real application, this would use proper decryption
    if encrypted_data.startswith("encrypted_"):
        return encrypted_data[10:]
    return encrypted_data 