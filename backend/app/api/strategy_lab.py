"""Strategy Lab routes.

Read-only with respect to the platform's books: a run fetches public venue
candles, computes in memory and returns the result. It writes no portfolio row,
send no order and touches no payment state. The only external calls are the
bounded Kraken OHLC reads, and they use the same shared upstream budget as the
other crypto routes.
"""

from __future__ import annotations

from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator

from app.api.crypto_failures import crypto_failure
from app.auth.jwt import get_current_user
from app.data.crypto_instruments import CRYPTO_INSTRUMENTS, get_crypto_instrument
from app.models.user import User
from app.providers.crypto_registry import get_crypto_provider
from app.providers.kraken_provider import KRAKEN_INTERVALS, KrakenError
from app.services.strategy_lab import (
    DEFAULT_COMMISSION_RATE,
    DEFAULT_INTERVALS,
    DEFAULT_NOTIONAL_PER_TRADE,
    DEFAULT_SLIPPAGE_RATE,
    DEFAULT_STARTING_CASH,
    DEFAULT_TRADES_PER_STRATEGY,
    MAX_TRADES_PER_STRATEGY,
    LabBar,
    LabSettings,
    StrategyLabError,
    assemble_dataset,
    build_plan,
    run_lab,
    select_strategies,
)
from app.services.strategy_research import (
    DEFAULT_FOLDS,
    MIN_RESEARCH_BARS,
    ResearchSettings,
    run_study,
)

router = APIRouter(prefix="/strategy-lab", tags=["strategy-lab"])

DEFAULT_INSTRUMENT_ID = "CRYPTO:KRAKEN:BTC-USD"
BAR_LIMIT = 720
# Daily bars give about two years of history; 4h about four months. Sub-hourly
# bars cannot cover a 40 bps round trip at their typical range, so the study
# refuses to pretend they are tradeable.
RESEARCH_INTERVALS = frozenset({1440, 240, 60})


class StrategyLabRunRequest(BaseModel):
    instrument_id: str = DEFAULT_INSTRUMENT_ID
    trades_per_strategy: int = Field(
        default=DEFAULT_TRADES_PER_STRATEGY, ge=1, le=MAX_TRADES_PER_STRATEGY
    )
    starting_cash: Decimal = Field(default=DEFAULT_STARTING_CASH, gt=0)
    notional_per_trade: Decimal = Field(default=DEFAULT_NOTIONAL_PER_TRADE, gt=0)
    commission_rate: Decimal = Field(default=DEFAULT_COMMISSION_RATE, ge=0, le=Decimal("0.05"))
    slippage_rate: Decimal = Field(default=DEFAULT_SLIPPAGE_RATE, ge=0, le=Decimal("0.05"))
    intervals: List[int] = Field(default_factory=lambda: list(DEFAULT_INTERVALS))
    strategy_ids: Optional[List[str]] = None
    include_trades: bool = False

    @model_validator(mode="after")
    def _validate_balances_and_intervals(self) -> "StrategyLabRunRequest":
        if self.starting_cash < self.notional_per_trade:
            raise ValueError("starting_cash must be greater than or equal to notional_per_trade")
        if not self.intervals:
            raise ValueError("at least one interval is required")
        if len(set(self.intervals)) != len(self.intervals):
            raise ValueError("intervals must not repeat")
        return self


def _require_instrument(instrument_id: str):
    instrument = get_crypto_instrument(instrument_id)
    if instrument is None:
        raise HTTPException(
            status_code=404,
            detail={
                "reason": "unknown_crypto_instrument",
                "instrument_id": instrument_id,
                "declared": [item.instrument_id for item in CRYPTO_INSTRUMENTS],
            },
        )
    return instrument


class StrategyResearchRequest(BaseModel):
    instrument_id: str = DEFAULT_INSTRUMENT_ID
    interval_minutes: int = 1440
    folds: int = Field(default=DEFAULT_FOLDS, ge=3, le=8)
    starting_cash: Decimal = Field(default=DEFAULT_STARTING_CASH, gt=0)
    notional_per_trade: Decimal = Field(default=DEFAULT_NOTIONAL_PER_TRADE, gt=0)
    commission_rate: Decimal = Field(default=DEFAULT_COMMISSION_RATE, ge=0, le=Decimal("0.05"))
    slippage_rate: Decimal = Field(default=DEFAULT_SLIPPAGE_RATE, ge=0, le=Decimal("0.05"))
    include_trades: bool = False

    @model_validator(mode="after")
    def _validate_balances(self) -> "StrategyResearchRequest":
        if self.starting_cash < self.notional_per_trade:
            raise ValueError("starting_cash must be greater than or equal to notional_per_trade")
        return self


@router.get("/plan")
def strategy_lab_plan(current_user: User = Depends(get_current_user)):
    """The strategy schemes, cost model, isolation rules and limits.

    No venue read happens here, so opening the plan cannot spend the upstream
    budget or move a price.
    """
    return build_plan()


@router.post("/run")
def strategy_lab_run(
    payload: StrategyLabRunRequest,
    current_user: User = Depends(get_current_user),
):
    """Run the selected strategies for N one-bar round trips each.

    The response is computed in memory. If the venue cannot serve one of the
    requested intervals the whole run refuses; it never fills the gap with
    synthetic or stored bars.
    """
    instrument = _require_instrument(payload.instrument_id)

    unsupported = [interval for interval in payload.intervals if interval not in KRAKEN_INTERVALS]
    if unsupported:
        raise HTTPException(
            status_code=400,
            detail={
                "reason": "unsupported_interval",
                "intervals": unsupported,
                "supported": list(KRAKEN_INTERVALS),
            },
        )

    try:
        strategies = select_strategies(payload.strategy_ids)
    except StrategyLabError as exc:
        raise HTTPException(status_code=400, detail=exc.as_dict())

    provider = get_crypto_provider()
    series_by_interval = {}
    for interval in payload.intervals:
        try:
            series_by_interval[interval] = provider.get_candle_series(
                payload.instrument_id,
                interval_minutes=interval,
                limit=BAR_LIMIT,
            )
        except KrakenError as exc:
            raise crypto_failure(exc)

    data_nature = provider.get_data_quality(payload.instrument_id).get("data_nature", "unknown")

    try:
        dataset = assemble_dataset(
            payload.instrument_id,
            series_by_interval,
            provider_id=provider.provider_id,
            data_nature=data_nature,
        )
        result = run_lab(
            dataset,
            settings=LabSettings(
                trades_per_strategy=payload.trades_per_strategy,
                starting_cash=payload.starting_cash,
                notional_per_trade=payload.notional_per_trade,
                commission_rate=payload.commission_rate,
                slippage_rate=payload.slippage_rate,
            ),
            strategies=strategies,
            include_trades=payload.include_trades,
        )
    except StrategyLabError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.as_dict())

    result["instrument"] = {
        "instrument_id": instrument.instrument_id,
        "exchange": instrument.exchange.value,
        "pair": instrument.symbol.replace("-", "/"),
        "base_asset": instrument.base_asset,
        "quote_asset": instrument.quote_asset,
        "price_precision": instrument.price_precision,
        "quantity_precision": instrument.quantity_precision,
        "read_only": True,
        "orders_supported": False,
    }
    return result


@router.post("/research")
def strategy_lab_research(
    payload: StrategyResearchRequest,
    current_user: User = Depends(get_current_user),
):
    """Cost-aware multi-bar study with walk-forward out-of-sample selection.

    One venue read supplies one interval; the study then runs entirely in
    memory. Rule/exit selection for each fold reads only the folds before it,
    and the in-sample grid is returned only as a comparison, never as evidence.
    """
    instrument = _require_instrument(payload.instrument_id)

    if payload.interval_minutes not in RESEARCH_INTERVALS:
        raise HTTPException(
            status_code=400,
            detail={
                "reason": "unsupported_interval",
                "interval_minutes": payload.interval_minutes,
                "supported": sorted(RESEARCH_INTERVALS),
            },
        )

    provider = get_crypto_provider()
    try:
        series = provider.get_candle_series(
            payload.instrument_id,
            interval_minutes=payload.interval_minutes,
            limit=BAR_LIMIT,
        )
    except KrakenError as exc:
        raise crypto_failure(exc)

    bars = [
        LabBar(
            open_time=candle.open_time,
            interval_minutes=payload.interval_minutes,
            open=candle.open,
            high=candle.high,
            low=candle.low,
            close=candle.close,
            volume=candle.volume,
        )
        for candle in series.completed
    ]
    if len(bars) < MIN_RESEARCH_BARS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason": "insufficient_dataset",
                "message": (
                    f"{len(bars)} completed bars available; the study needs at least "
                    f"{MIN_RESEARCH_BARS} to warm up indicators and hold out folds."
                ),
                "bars": len(bars),
                "minimum_bars": MIN_RESEARCH_BARS,
            },
        )

    try:
        result = run_study(
            bars,
            settings=ResearchSettings(
                starting_cash=payload.starting_cash,
                notional_per_trade=payload.notional_per_trade,
                commission_rate=payload.commission_rate,
                slippage_rate=payload.slippage_rate,
            ),
            folds=payload.folds,
            include_trades=payload.include_trades,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"reason": "insufficient_dataset", "message": str(exc)},
        )

    data_nature = provider.get_data_quality(payload.instrument_id).get("data_nature", "unknown")
    result["dataset"] = {
        "instrument_id": payload.instrument_id,
        "interval_minutes": payload.interval_minutes,
        "provider_id": provider.provider_id,
        "data_nature": data_nature,
        "bars": len(bars),
        "first_open_time": bars[0].open_time.isoformat(),
        "last_open_time": bars[-1].open_time.isoformat(),
        "fingerprint": series.fingerprint,
        "integrity_ok": series.integrity_ok,
        "gap_count": len(series.gaps),
        "missing_intervals": series.missing_intervals,
        "fetched_at": series.fetched_at.isoformat(),
        "is_stale": series.is_stale,
        "source": "venue_completed_bars",
    }
    result["instrument"] = {
        "instrument_id": instrument.instrument_id,
        "exchange": instrument.exchange.value,
        "pair": instrument.symbol.replace("-", "/"),
        "base_asset": instrument.base_asset,
        "quote_asset": instrument.quote_asset,
        "price_precision": instrument.price_precision,
        "quantity_precision": instrument.quantity_precision,
        "read_only": True,
        "orders_supported": False,
    }
    return result
