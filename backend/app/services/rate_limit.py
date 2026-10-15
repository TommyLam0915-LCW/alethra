"""Rate limiting service — shared state across processes."""
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.models.rate_limit import RateLimitCounter


# Rate limit configuration (requests per window)
RATE_LIMITS = {
    "training": 10,  # 10 training jobs per window
    "execution": 50,  # 50 execution intents per window
    "inference": 100,  # 100 inference runs per window
    "experiment": 20,  # 20 experiments per window
    "backtest": 30,  # 30 backtests per window
    "auth": 20,  # 20 auth attempts per window
    "payment": 10,  # 10 payment intents per window
}

# Window duration (minutes)
RATE_LIMIT_WINDOW_MINUTES = 60


def increment_rate_limit(
    db: Session,
    identity_key: str,
    route_category: str,
    limit: Optional[int] = None,
    window_minutes: Optional[int] = None,
) -> tuple[bool, int, int]:
    """Check rate limit and increment counter if allowed.
    
    Returns:
        (allowed, current_count, limit)
    
    Algorithm: Fixed hourly window. All requests within the same hour (UTC)
    share the same window_start (truncated to hour boundary). This eliminates
    race conditions in window determination.
    
    For example, all requests between 14:00:00 and 14:59:59 UTC use
    window_start = 14:00:00. At 15:00:00, a new window begins.
    
    Uses PostgreSQL INSERT ... ON CONFLICT for concurrent-safe upsert.
    This handles the race condition where multiple processes try to create
    a counter for the same identity/window simultaneously.
    
    Transaction semantics: The counter is incremented atomically with the
    check. If the downstream operation fails and the transaction is rolled
    back, the counter increment is also rolled back. This ensures rejected
    operations do not consume rate limit quota.
    """
    if limit is None:
        limit = RATE_LIMITS.get(route_category, 100)
    
    # Use fixed hourly window (truncated to hour boundary)
    # This eliminates race conditions: all requests in the same hour use the same window_start
    now = datetime.utcnow()
    window_start = now.replace(minute=0, second=0, microsecond=0)
    
    # Use raw SQL for atomic upsert with conflict handling
    # This is concurrent-safe: if two processes try to create the same counter,
    # one will succeed and the other will update the existing row.
    # Note: UUID is generated in Python to support both SQLite and PostgreSQL
    import uuid
    result = db.execute(text("""
        INSERT INTO rate_limit_counters (id, identity_key, route_category, window_start, request_count, created_at, updated_at)
        VALUES (:id, :identity_key, :route_category, :window_start, 1, :now, :now)
        ON CONFLICT (identity_key, route_category, window_start)
        DO UPDATE SET 
            request_count = rate_limit_counters.request_count + 1,
            updated_at = :now
        RETURNING request_count
    """), {
        "id": str(uuid.uuid4()),
        "identity_key": identity_key,
        "route_category": route_category,
        "window_start": window_start,
        "now": now,
    })
    
    row = result.fetchone()
    if not row:
        # This should never happen, but fail-open if it does
        return True, 0, limit
    
    new_count = row[0]
    allowed = new_count <= limit
    
    # If not allowed, we still incremented the counter. This is intentional:
    # the counter tracks attempts, not successes. This prevents bypass by
    # repeated attempts at the boundary.
    
    return allowed, new_count, limit


def cleanup_old_counters(db: Session, max_age_hours: int = 24) -> int:
    """Delete rate limit counters older than max_age_hours.
    
    Returns number of deleted rows.
    
    This should be run periodically (e.g., daily) to prevent unbounded growth.
    Only deletes counters outside the active window, so it does not affect
    current rate limiting or audit evidence.
    """
    cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)
    
    deleted = db.query(RateLimitCounter).filter(
        RateLimitCounter.window_start < cutoff
    ).delete()
    
    db.commit()
    return deleted
