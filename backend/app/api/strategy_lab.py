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
    LabSettings,
    StrategyLabError,
    assemble_dataset,
    build_plan,
    run_lab,
    select_strategies,
)

router = APIRouter(prefix="/strategy-lab", tags=["strategy-lab"])

DEFAULT_INSTRUMENT_ID = "CRYPTO:KRAKEN:BTC-USD"
BAR_LIMIT = 720


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
