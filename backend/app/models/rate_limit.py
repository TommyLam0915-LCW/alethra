"""Rate limiting model — shared state across processes."""
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base
from app.lib.timeutil import utcnow


class RateLimitCounter(Base):
    """Shared rate limit counter stored in PostgreSQL.
    
    Tracks request counts per identity key (user_id, api_key_id, or IP)
    per route category within a time window.
    
    This provides cross-process rate limiting without Redis.
    
    Algorithm: Fixed window per identity. Each identity gets a 60-minute
    window starting from their first request. After the window expires,
    a new window is created on the next request.
    
    Unique constraint: (identity_key, route_category, window_start) ensures
    only one counter per identity per window, even under concurrent creation.
    """
    __tablename__ = "rate_limit_counters"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=lambda: __import__("uuid").uuid4())
    identity_key = Column(String(255), nullable=False, index=True)  # user:{id}, api_key:{id}, ip:{addr}
    route_category = Column(String(100), nullable=False, index=True)  # training, execution, inference, etc.
    window_start = Column(DateTime, nullable=False, index=True)
    request_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    
    __table_args__ = (
        UniqueConstraint('identity_key', 'route_category', 'window_start', name='uq_rate_limit_identity_window'),
    )
    
    def __repr__(self):
        return f"<RateLimitCounter identity={self.identity_key} route={self.route_category} count={self.request_count}>"
