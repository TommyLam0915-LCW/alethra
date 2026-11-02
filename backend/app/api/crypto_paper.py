"""Simulated crypto paper orders on the shared paper ledger.

Every route here is simulated: no route reads a private venue account, sends an
order to Kraken, or needs a trading key. The quote a fill uses is an explicitly
identified completed bar; missing, forming, stale or unlabelled bars are refused
before anything is booked.

The lane books through ``services.crypto_paper.submit_manual_order``, which in
turn calls the same ``book_fill`` every other paper order uses, so cash and
positions stay in one ledger.
"""

from typing import Optional, Union
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.crypto_failures import crypto_failure
from app.auth.api_keys import check_api_key_scope
from app.auth.jwt import get_current_user
from app.auth.provenance import request_actor_provenance
from app.auth.rate_limit import api_key_rate_limit_dependency
from app.auth.safety import rate_limit, safety_control
from app.config import get_settings
from app.data.crypto_instruments import CRYPTO_INSTRUMENTS, get_crypto_instrument
from app.models.kill_switch import KillSwitchScope
from app.models.paper_portfolio import PaperPortfolio
from app.models.user import User
from app.database import get_db
from app.providers.crypto_registry import get_crypto_provider
from app.providers.kraken_provider import KrakenError
from app.services.crypto_paper import (
    DEFAULT_PROVIDER_ID,
    DEFAULT_QUOTE_MAX_AGE_SECONDS,
    EXECUTION_ASSUMPTION,
    LEDGER_MIRROR_LABEL,
    CryptoPaperError,
    build_quote,
    latest_quote_bar,
    receipt_as_dict,
    receipts_csv,
    receipts_for_portfolio,
    record_quote_bars,
    submit_manual_order,
)
from app.services.crypto_paper_fixture import seed_crypto_paper_fixture

_settings = get_settings()

router = APIRouter(
    prefix="/crypto-paper",
    tags=["crypto-paper"],
    dependencies=[
        Depends(
            api_key_rate_limit_dependency(
                max_requests=_settings.api_key_rate_limit_requests,
                window_seconds=_settings.api_key_rate_limit_window_seconds,
            )
        )
    ],
)

# Codes that mean "the stored data is not usable right now" rather than "the
# request itself is malformed". The UI renders them as a non-destructive
# refusal, so they get 409, not 400.
_STATE_CODES = frozenset(
    {
        "missing_quote",
        "forming_quote",
        "stale_quote",
        "unverified_quote",
        "incompatible_quote",
        "incompatible_instrument",
        "incompatible_provider",
        "below_min_notional",
        "invalid_price",
        "insufficient_cash",
        "insufficient_position",
        "idempotency_conflict",
    }
)

PAPER_INTERVALS = (1, 5)
# One Kraken request per capture; the returned window is bounded because only
# the latest bars can ever be inside the quote window.
CAPTURE_BARS = 60
LIVE_PRICE_SOURCE = "kraken:public_rest:completed_bars"


def _refuse(exc: CryptoPaperError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT if exc.code in _STATE_CODES else status.HTTP_400_BAD_REQUEST,
        detail={"code": exc.code, "message": exc.message},
    )


def _owned_portfolio(portfolio_id: UUID, db: Session, current_user: User) -> PaperPortfolio:
    portfolio = db.query(PaperPortfolio).filter(PaperPortfolio.id == portfolio_id).first()
    if portfolio is None:
        raise HTTPException(status_code=404, detail={"code": "portfolio_not_found"})
    if portfolio.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail={"code": "not_authorized"})
    return portfolio


def _locked_portfolio(portfolio_id: UUID, db: Session, current_user: User) -> PaperPortfolio:
    portfolio = (
        db.query(PaperPortfolio)
        .populate_existing()
        .filter(PaperPortfolio.id == portfolio_id)
        .with_for_update()
        .first()
    )
    if portfolio is None:
        raise HTTPException(status_code=404, detail={"code": "portfolio_not_found"})
    if portfolio.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail={"code": "not_authorized"})
    return portfolio


QuantityInput = Union[str, int, float]


class CryptoPaperOrderRequest(BaseModel):
    instrument_id: str
    interval_minutes: int = Field(default=1, ge=1)
    side: str
    quantity: QuantityInput
    idempotency_key: Optional[str] = None
    rationale: Optional[str] = None
    expected_outcome: Optional[str] = None
    expected_timeframe_days: Optional[int] = None
    self_reported_confidence: str = "not_provided"
    falsification_condition: Optional[str] = None


class CryptoPaperFixtureRequest(BaseModel):
    interval_minutes: int = Field(default=1, ge=1)


class CryptoQuoteCaptureRequest(BaseModel):
    instrument_id: str
    interval_minutes: int = Field(default=1, ge=1)


@router.get("/instruments")
def list_crypto_paper_instruments(current_user: User = Depends(get_current_user)):
    """The declared spot instruments this lane can price, with their specs."""
    return {
        "lane": "crypto_paper_simulated",
        "provider_id": DEFAULT_PROVIDER_ID,
        "quote_window_seconds": DEFAULT_QUOTE_MAX_AGE_SECONDS,
        "paper_intervals_minutes": list(PAPER_INTERVALS),
        "execution_assumption": EXECUTION_ASSUMPTION,
        "ledger_mirror": LEDGER_MIRROR_LABEL,
        "simulated": True,
        "instruments": [
            {
                "instrument_id": instrument.instrument_id,
                "symbol": instrument.symbol,
                "name": instrument.name,
                "venue": instrument.venue,
                "base_asset": instrument.base_asset,
                "quote_asset": instrument.quote_asset,
                "price_precision": instrument.price_precision,
                "quantity_precision": instrument.quantity_precision,
                "min_quantity": str(instrument.min_quantity) if instrument.min_quantity is not None else None,
                "min_notional": str(instrument.min_notional) if instrument.min_notional is not None else None,
            }
            for instrument in CRYPTO_INSTRUMENTS
        ],
    }


@router.post("/quote/capture")
def capture_live_quote(
    payload: CryptoQuoteCaptureRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
    _rate_limit=Depends(rate_limit("crypto_quote_capture")),
):
    """Fetch the venue's latest completed bars into the quote contract.

    This reads public market data only: no private account, no order, and no
    synthetic substitute when the venue fails. It exists so a paper order can
    be priced on a real completed bar instead of the labelled fixture. The
    shared upstream budget still gates the single Kraken request.
    """
    instrument = get_crypto_instrument(payload.instrument_id)
    if instrument is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "incompatible_instrument",
                "instrument_id": payload.instrument_id,
                "declared": [i.instrument_id for i in CRYPTO_INSTRUMENTS],
            },
        )
    if payload.interval_minutes not in PAPER_INTERVALS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "unsupported_interval",
                "interval_minutes": payload.interval_minutes,
                "supported": list(PAPER_INTERVALS),
            },
        )

    provider = get_crypto_provider()
    try:
        series = provider.get_candle_series(
            payload.instrument_id,
            interval_minutes=payload.interval_minutes,
            limit=CAPTURE_BARS,
        )
    except KrakenError as exc:
        raise crypto_failure(exc) from exc

    if not series.completed:
        return {
            "status": "no_data",
            "instrument_id": payload.instrument_id,
            "interval_minutes": payload.interval_minutes,
            "message": "The venue returned no completed candles for this request.",
        }

    counts = record_quote_bars(
        db,
        instrument_id=payload.instrument_id,
        provider_id=provider.provider_id,
        interval_minutes=payload.interval_minutes,
        candles=series.completed,
        price_source=LIVE_PRICE_SOURCE,
        data_nature="live",
    )
    db.commit()

    latest = latest_quote_bar(
        db,
        instrument_id=payload.instrument_id,
        provider_id=provider.provider_id,
        interval_minutes=payload.interval_minutes,
    )
    return {
        "status": "captured",
        "instrument_id": payload.instrument_id,
        "interval_minutes": payload.interval_minutes,
        "provider_id": provider.provider_id,
        "data_nature": "live",
        "price_source": LIVE_PRICE_SOURCE,
        "quote_window_seconds": DEFAULT_QUOTE_MAX_AGE_SECONDS,
        # The series separates the open candle before the recorder sees it, so
        # this is the only place the response can say it was left out.
        "forming_excluded": series.forming is not None,
        **counts,
        "latest_completed_bar": (
            {
                "open_time": latest.open_time.isoformat(),
                "close_time": latest.close_time.isoformat(),
                "close": str(latest.close),
            }
            if latest is not None
            else None
        ),
    }


@router.get("/quote")
def preview_crypto_paper_quote(
    instrument_id: str,
    side: str,
    quantity: str,
    interval_minutes: int = 1,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Price an order without booking it. Nothing is written."""
    try:
        quote = build_quote(
            db,
            instrument_id=instrument_id,
            interval_minutes=interval_minutes,
            side=side,
            quantity=quantity,
        )
    except CryptoPaperError as exc:
        raise _refuse(exc) from exc
    return {**quote.as_dict(), "quote_window_seconds": DEFAULT_QUOTE_MAX_AGE_SECONDS}


@router.post(
    "/portfolios/{portfolio_id}/orders",
    status_code=status.HTTP_201_CREATED,
)
def submit_crypto_paper_order(
    portfolio_id: UUID,
    payload: CryptoPaperOrderRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
    _safety=Depends(safety_control(KillSwitchScope.execution, "execution")),
):
    portfolio = _locked_portfolio(portfolio_id, db, current_user)
    try:
        result = submit_manual_order(
            db,
            portfolio=portfolio,
            instrument_id=payload.instrument_id,
            interval_minutes=payload.interval_minutes,
            side=payload.side,
            quantity=payload.quantity,
            actor_id=current_user.id,
            actor_type=request_actor_provenance(request),
            idempotency_key=payload.idempotency_key,
            rationale=payload.rationale,
            expected_outcome=payload.expected_outcome,
            expected_timeframe_days=payload.expected_timeframe_days,
            self_reported_confidence=payload.self_reported_confidence,
            falsification_condition=payload.falsification_condition,
        )
    except CryptoPaperError as exc:
        db.rollback()
        raise _refuse(exc) from exc

    db.commit()
    db.refresh(result.order)
    db.refresh(result.fill)
    db.refresh(result.receipt)
    return result.as_dict()


@router.get("/portfolios/{portfolio_id}/executions")
def list_crypto_paper_executions(
    portfolio_id: UUID,
    format: str = "json",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Owner-authorized receipt export. ``format=csv`` is an attachment."""
    if format not in ("csv", "json"):
        raise HTTPException(status_code=400, detail={"code": "unsupported_format"})

    portfolio = _owned_portfolio(portfolio_id, db, current_user)
    receipts = receipts_for_portfolio(db, portfolio.id)

    if format == "csv":
        filename = f"crypto-paper-executions-{portfolio.id}.csv"
        return Response(
            content=receipts_csv(receipts),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return {
        "portfolio_id": str(portfolio.id),
        "currency": portfolio.currency,
        "lane": "crypto_paper_simulated",
        "row_count": len(receipts),
        "ledger_mirror": LEDGER_MIRROR_LABEL,
        "execution_assumption": EXECUTION_ASSUMPTION,
        "simulated": True,
        "records": [receipt_as_dict(receipt) for receipt in receipts],
    }


@router.post("/admin/fixture", status_code=status.HTTP_201_CREATED)
def seed_crypto_paper_fixture_endpoint(
    payload: CryptoPaperFixtureRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
):
    """Seed the labelled synthetic fixture. Admin-only, never live data."""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail={"code": "admin_required"})

    counts = seed_crypto_paper_fixture(db, interval_minutes=payload.interval_minutes)
    db.commit()
    return counts


__all__ = ["router", "PAPER_INTERVALS"]
