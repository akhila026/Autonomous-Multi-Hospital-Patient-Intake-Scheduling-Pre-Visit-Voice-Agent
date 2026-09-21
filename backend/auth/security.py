import os
import secrets
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import bcrypt
import jwt

from backend.config import settings

def hash_password(password: str) -> str:
    """Hashes a password using bcrypt with automated salt generation."""
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(password.encode("utf-8"), salt)
    return hashed.decode("utf-8")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verifies a plaintext password against the stored bcrypt hash.
    Includes backward-compatible fallback for sha256 salt$hash if encountered.
    """
    if not hashed_password:
        return False
    
    # Check if bcrypt hash
    if hashed_password.startswith("$2b$") or hashed_password.startswith("$2a$") or hashed_password.startswith("$2y$"):
        try:
            return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
        except Exception:
            return False
            
    # Fallback for legacy salt$hash format
    if "$" in hashed_password:
        import hashlib
        import hmac
        salt, original_hash = hashed_password.split("$", 1)
        new_hash = hashlib.sha256((plain_password + salt).encode("utf-8")).hexdigest()
        return hmac.compare_digest(original_hash, new_hash)
        
    return False

def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """
    Encodes a signed JWT access token containing user identity, role, and tenant context.
    """
    to_encode = data.copy()
    now = datetime.utcnow()
    
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        
    to_encode.update({
        "exp": expire,
        "iat": now
    })
    
    encoded_jwt = jwt.encode(
        to_encode,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM
    )
    return encoded_jwt

def decode_access_token(token: str) -> Dict[str, Any]:
    """
    Decodes and validates token signature and expiration.
    Raises jwt.PyJWTError (e.g. ExpiredSignatureError, InvalidTokenError) on failure.
    """
    payload = jwt.decode(
        token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM]
    )
    return payload

def generate_session_token() -> str:
    """Generates a secure cryptographically strong random token."""
    return secrets.token_urlsafe(32)
