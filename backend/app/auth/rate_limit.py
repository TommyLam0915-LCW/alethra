import threading
import time
from collections import defaultdict
from typing import Optional

from fastapi import Depends, HTTPException, Request, Response, status


class RateLimiter:
    """Simple in-memory sliding-window rate limiter.

    The window is guarded by a lock: without it two concurrent requests can both
    read the same count, both decide there is room, and both append, so the
    configured ceiling would only hold for sequential callers.
    """

    def __init__(self):
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def _cleanup(self, key: str, window_seconds: float, now: float) -> None:
        cutoff = now - window_seconds
        self._requests[key] = [ts for ts in self._requests[key] if ts > cutoff]

    def check_rate_limit(self, key: str, max_requests: int, window_seconds: float) -> bool:
        """Return True if the request is allowed, False if rate-limited."""
        with self._lock:
            now = time.time()
            self._cleanup(key, window_seconds, now)
            if len(self._requests[key]) >= max_requests:
                return False
            self._requests[key].append(now)
            return True

    def remaining(self, key: str, max_requests: int, window_seconds: float) -> int:
        with self._lock:
            now = time.time()
            self._cleanup(key, window_seconds, now)
            return max(0, max_requests - len(self._requests[key]))

    def reset_at(self, key: str, window_seconds: float) -> float:
        """Return the timestamp when the oldest request in the window expires."""
        with self._lock:
            now = time.time()
            self._cleanup(key, window_seconds, now)
            if self._requests[key]:
                return self._requests[key][0] + window_seconds
            return now + window_seconds

    def reset_store(self) -> None:
        """Clear all stored timestamps (useful for testing)."""
        with self._lock:
            self._requests.clear()


# Global default instance
_default_limiter = RateLimiter()


def rate_limit_dependency(
    max_requests: int = 60,
    window_seconds: float = 60,
    limiter: Optional[RateLimiter] = None,
):
    """
    FastAPI dependency factory that enforces rate limiting per client IP.
    Returns 429 when the limit is exceeded.
    """
    if limiter is None:
        limiter = _default_limiter

    async def _dependency(request: Request, response: Response) -> None:
        client_ip = request.client.host if request.client else "unknown"
        key = f"rl:{client_ip}"

        allowed = limiter.check_rate_limit(key, max_requests, window_seconds)
        remaining = limiter.remaining(key, max_requests, window_seconds)
        reset_ts = limiter.reset_at(key, window_seconds)

        response.headers["X-RateLimit-Limit"] = str(max_requests)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(int(reset_ts))

        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Please try again later.",
                headers={
                    "X-RateLimit-Limit": str(max_requests),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(reset_ts)),
                    "Retry-After": str(int(reset_ts - time.time()) + 1),
                },
            )

    return _dependency


def reset_default_limiter() -> None:
    """Clear the shared limiter's windows.

    Used between tests so one test does not inherit another's consumed budget.
    """
    _default_limiter.reset_store()


def api_key_rate_limit_dependency(
    max_requests: int = 60,
    window_seconds: float = 60,
    limiter: Optional[RateLimiter] = None,
):
    """Enforce a per-API-key request budget, returning 429 once it is spent.

    The bucket is keyed on the authenticated key's id rather than the client IP:
    two keys belonging to one user must not share an allowance, and a caller
    behind a shared NAT must not be limited by a stranger's traffic.  Because the
    key id is only known after authentication, ``get_current_user`` is declared as
    a parameter here, which fixes the ordering regardless of how the endpoint
    declares its own dependencies.

    JWT callers are deliberately not limited by this dependency — a browser
    session is not a programmatic client, and the per-IP limiter above is the
    appropriate tool for it.

    The counter is process-local, so it is a ceiling per worker: running N
    uvicorn workers makes the effective ceiling N * ``max_requests``, and a
    restart clears every window.  Enforcing a global budget across workers or
    restarts needs a shared store, which this module does not provide.

    The raw key never reaches a header, a detail message or a log; only its
    database id is used, and only to build the bucket name.
    """
    from app.auth.jwt import get_current_user
    from app.models.user import User

    if limiter is None:
        limiter = _default_limiter

    async def _dependency(
        request: Request,
        response: Response,
        current_user: User = Depends(get_current_user),
    ) -> User:
        key_id = getattr(request.state, "api_key_id", None)
        if key_id is None:
            return current_user

        bucket = f"apk:{key_id}"
        allowed = limiter.check_rate_limit(bucket, max_requests, window_seconds)
        remaining = limiter.remaining(bucket, max_requests, window_seconds)
        reset_ts = limiter.reset_at(bucket, window_seconds)

        response.headers["X-RateLimit-Limit"] = str(max_requests)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(int(reset_ts))

        if not allowed:
            retry_after = max(1, int(reset_ts - time.time()) + 1)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    "API key rate limit exceeded. "
                    f"Budget is {max_requests} requests per {int(window_seconds)}s; "
                    "retry after the interval given in Retry-After."
                ),
                headers={
                    "X-RateLimit-Limit": str(max_requests),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(reset_ts)),
                    "Retry-After": str(retry_after),
                },
            )
        return current_user

    return _dependency
