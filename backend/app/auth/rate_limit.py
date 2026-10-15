import time
from collections import defaultdict
from typing import Optional

from fastapi import HTTPException, Request, Response, status


class RateLimiter:
    """Simple in-memory sliding-window rate limiter."""

    def __init__(self):
        self._requests: dict[str, list[float]] = defaultdict(list)

    def _cleanup(self, key: str, window_seconds: float, now: float) -> None:
        cutoff = now - window_seconds
        self._requests[key] = [ts for ts in self._requests[key] if ts > cutoff]

    def check_rate_limit(self, key: str, max_requests: int, window_seconds: float) -> bool:
        """Return True if the request is allowed, False if rate-limited."""
        now = time.time()
        self._cleanup(key, window_seconds, now)
        if len(self._requests[key]) >= max_requests:
            return False
        self._requests[key].append(now)
        return True

    def remaining(self, key: str, max_requests: int, window_seconds: float) -> int:
        now = time.time()
        self._cleanup(key, window_seconds, now)
        return max(0, max_requests - len(self._requests[key]))

    def reset_at(self, key: str, window_seconds: float) -> float:
        """Return the timestamp when the oldest request in the window expires."""
        now = time.time()
        self._cleanup(key, window_seconds, now)
        if self._requests[key]:
            return self._requests[key][0] + window_seconds
        return now + window_seconds

    def reset_store(self) -> None:
        """Clear all stored timestamps (useful for testing)."""
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
