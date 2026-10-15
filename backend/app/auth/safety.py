"""Safety controls — FastAPI dependencies for kill switch and rate limiting."""
from fastapi import Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.kill_switch import KillSwitchScope
from app.services.kill_switch import is_kill_switch_active
from app.services.rate_limit import increment_rate_limit
from app.auth.jwt import get_current_user
from app.models.user import User


def check_kill_switch(scope: KillSwitchScope):
    """FastAPI dependency to check kill switch before route execution.
    
    Raises HTTP 503 if kill switch is active.
    """
    def dependency(db: Session = Depends(get_db)):
        try:
            if is_kill_switch_active(db, scope):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"Service temporarily unavailable (kill switch active for {scope.value})",
                )
        except HTTPException:
            raise
        except Exception as e:
            # Fail-closed: if we can't read the switch, block the operation
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Service temporarily unavailable (unable to verify system state)",
            )
    
    return dependency


def rate_limit(route_category: str):
    """FastAPI dependency to enforce rate limiting.
    
    Raises HTTP 429 if rate limit exceeded.
    
    Dual enforcement for API keys:
    - User-level limit: aggregate across all API keys for that user
    - API-key-level limit: per-key limit
    
    Both must pass for the request to be allowed. This prevents rotating
    keys from bypassing the user cap.
    """
    async def dependency(
        request: Request,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
    ):
        # Always check user-level rate limit first
        user_identity_key = f"user:{current_user.id}"
        allowed, current_count, limit = increment_rate_limit(
            db, user_identity_key, route_category
        )
        
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded for {route_category} ({current_count}/{limit} requests per hour)",
                headers={"X-RateLimit-Limit": str(limit), "X-RateLimit-Remaining": "0"},
            )
        
        # If using API key, also check API-key-level rate limit
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            from app.models.api_key import ApiKey
            api_key = db.query(ApiKey).filter(ApiKey.key_hash == token).first()
            if api_key:
                api_key_identity_key = f"api_key:{api_key.id}"
                allowed, current_count, limit = increment_rate_limit(
                    db, api_key_identity_key, route_category
                )
                
                if not allowed:
                    raise HTTPException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        detail=f"Rate limit exceeded for API key ({current_count}/{limit} requests per hour)",
                        headers={"X-RateLimit-Limit": str(limit), "X-RateLimit-Remaining": "0"},
                    )
    
    return dependency


def safety_control(scope: KillSwitchScope, route_category: str):
    """Combined dependency: kill switch + rate limiting.
    
    Checks kill switch first, then rate limit.
    
    Dual enforcement for API keys:
    - User-level limit: aggregate across all API keys for that user
    - API-key-level limit: per-key limit
    
    Both must pass for the request to be allowed.
    """
    async def dependency(
        request: Request,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
    ):
        # Check kill switch first
        try:
            if is_kill_switch_active(db, scope):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"Service temporarily unavailable (kill switch active for {scope.value})",
                )
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Service temporarily unavailable (unable to verify system state)",
            )
        
        # Always check user-level rate limit first
        user_identity_key = f"user:{current_user.id}"
        allowed, current_count, limit = increment_rate_limit(
            db, user_identity_key, route_category
        )
        
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded for {route_category} ({current_count}/{limit} requests per hour)",
                headers={"X-RateLimit-Limit": str(limit), "X-RateLimit-Remaining": "0"},
            )
        
        # If using API key, also check API-key-level rate limit
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            from app.models.api_key import ApiKey
            api_key = db.query(ApiKey).filter(ApiKey.key_hash == token).first()
            if api_key:
                api_key_identity_key = f"api_key:{api_key.id}"
                allowed, current_count, limit = increment_rate_limit(
                    db, api_key_identity_key, route_category
                )
                
                if not allowed:
                    raise HTTPException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        detail=f"Rate limit exceeded for API key ({current_count}/{limit} requests per hour)",
                        headers={"X-RateLimit-Limit": str(limit), "X-RateLimit-Remaining": "0"},
                    )
    
    return dependency
