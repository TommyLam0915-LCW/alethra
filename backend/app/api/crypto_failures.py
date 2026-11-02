"""One mapping from venue failures to HTTP responses, shared by the crypto lanes.

The read-only market route and the paper quote capture make the same upstream
call, so they must answer a budget refusal, a venue rate limit and an outage the
same way. Keeping two copies of this table would let the lanes drift apart in
what they tell the user.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException

from app.lib.error_safety import safe_error_message
from app.providers.kraken_provider import (
    KrakenApiError,
    KrakenBudgetExceededError,
    KrakenError,
    KrakenRateLimitError,
    KrakenUnavailableError,
)

logger = logging.getLogger(__name__)


def crypto_failure(exc: Exception) -> HTTPException:
    logger.warning("Crypto market data failure: %r", exc, exc_info=exc)
    detail = {
        "available": False,
        "provider": "kraken",
        "detail": safe_error_message(exc),
        "no_fallback": "Synthetic or demo bars are never substituted for a live venue feed.",
    }
    if isinstance(exc, KrakenBudgetExceededError):
        # Our own cap refused the call, so the venue was never contacted. This
        # is not an outage: retrying cannot help until the window rolls over or
        # an operator starts a new run. Stored bars stay readable.
        detail["reason"] = "budget_exhausted"
        detail["denied_layer"] = exc.denied_layer
        detail["budget"] = exc.reservation.as_dict()
        detail["stored_available"] = "/market/crypto/stored"
        detail["retry_safe"] = False
        return HTTPException(status_code=503, detail=detail)
    if isinstance(exc, KrakenRateLimitError):
        detail["reason"] = "venue_rate_limited"
        return HTTPException(status_code=429, detail=detail)
    if isinstance(exc, KrakenUnavailableError):
        detail["reason"] = "venue_unreachable"
        return HTTPException(status_code=503, detail=detail)
    if isinstance(exc, KrakenApiError):
        detail["reason"] = "venue_rejected_request"
        return HTTPException(status_code=503, detail=detail)
    if isinstance(exc, KrakenError):
        detail["reason"] = "venue_error"
        return HTTPException(status_code=503, detail=detail)
    detail["reason"] = "unexpected_error"
    return HTTPException(status_code=500, detail=detail)


__all__ = ["crypto_failure"]
