import hashlib
import secrets
import time
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.auth.jwt import security as jwt_security
from app.config import get_settings
from app.database import get_db
from app.models.api_key import ApiKey
from app.models.user import User

settings = get_settings()


def generate_api_key(
    db: Session,
    user_id: UUID,
    name: str,
    scopes: Optional[list] = None,
    expires_at: Optional[datetime] = None,
) -> tuple[str, str]:
    """Generate a new API key. Returns (key_prefix, raw_key). The raw key is shown only once."""
    raw_key = f"alk_{secrets.token_urlsafe(48)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    key_prefix = raw_key[:8]

    api_key = ApiKey(
        user_id=user_id,
        name=name,
        key_hash=key_hash,
        key_prefix=key_prefix,
        scopes=scopes or [],
        is_active=True,
        expires_at=expires_at,
    )
    db.add(api_key)
    db.commit()
    return key_prefix, raw_key


def authenticate_api_key(db: Session, raw_key: str) -> Optional[User]:
    """Authenticate using a raw API key. Returns the associated User or None."""
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    api_key = db.query(ApiKey).filter(ApiKey.key_hash == key_hash).first()

    if api_key is None:
        return None
    if not api_key.is_active:
        return None
    if api_key.expires_at is not None and api_key.expires_at < datetime.utcnow():
        return None

    # Update last_used_at
    api_key.last_used_at = datetime.utcnow()
    db.commit()

    user = db.query(User).filter(User.id == api_key.user_id).first()
    if user is None or not user.is_active:
        return None
    return user


def get_api_key_dependency():
    """
    Returns a FastAPI dependency that tries JWT first, then falls back to API key.
    """

    async def _dependency(
        request: Request,
        db: Session = Depends(get_db),
    ) -> User:
        auth_header = request.headers.get("authorization", "")
        if not auth_header.lower().startswith("bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = auth_header[7:].strip()

        # Try JWT first
        try:
            payload = jwt.decode(
                token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
            )
            user_id_str = payload.get("sub")
            if user_id_str is not None:
                user_uuid = UUID(user_id_str)
                user = db.query(User).filter(User.id == user_uuid).first()
                if user and user.is_active:
                    return user
        except (JWTError, ValueError):
            pass

        # Fall back to API key
        user = authenticate_api_key(db, token)
        if user is not None:
            return user

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return _dependency
