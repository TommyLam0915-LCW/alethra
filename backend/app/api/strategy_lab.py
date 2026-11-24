"""Strategy Lab routes.

Read-only with respect to the platform's books: a run fetches public venue
candles, computes in memory and returns the result. It writes no portfolio row,
sends no order and touches no payment state. The only external calls are the
bounded Kraken OHLC reads, and they use the same shared upstream budget as the
other crypto routes.

The durable research routes additionally store the study, its exact completed
bars and every out-of-sample buy/sell so the owner can review, export and
re-check the simulation later. A stored study is evidence of what the engine
did, never a claim that the strategy is profitable.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Literal, Optional, Tuple
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.crypto_failures import crypto_failure
from app.lib.error_safety import safe_error_message
from app.auth.jwt import get_current_user
from app.auth.safety import rate_limit
from app.data.crypto_instruments import CRYPTO_INSTRUMENTS, get_crypto_instrument
from app.database import get_db
from app.models.market_data import MarketDataBar
from app.models.user import User
from app.providers.crypto_registry import get_crypto_provider
from app.providers.kraken_provider import KRAKEN_INTERVALS, KrakenError
from app.providers.provider_registry import get_data_provider
from app.services.market_data_storage import MarketDataStorage
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
from app.services.strategy_study_store import (
    get_study,
    list_studies,
    persist_study,
    run_detail,
    run_summary,
    trades_csv,
)
from app.services.arbitrage_engine import (
    ArbitrageConfig,
    run_triangular_arbitrage,
    result_to_dict,
)
from app.services.market_making_engine import (
    MarketMakingConfig,
    backtest_market_making,
    result_to_dict as mm_result_to_dict,
)
from app.services.pairs_trading_engine import (
    PairsConfig,
    run_pairs_backtest,
    result_to_dict as pairs_result_to_dict,
)
from app.services.seasonality_engine import (
    analyze_seasonality,
    result_to_dict as seasonality_result_to_dict,
)


router = APIRouter(prefix="/strategy-lab", tags=["strategy-lab"])

logger = logging.getLogger(__name__)

DEFAULT_INSTRUMENT_ID = "CRYPTO:KRAKEN:BTC-USD"
BAR_LIMIT = 720
# Daily bars give about two years of history; 4h about four months. Sub-hourly
# bars cannot cover a 40 bps round trip at their typical range, so the study
# refuses to pretend they are tradeable.
RESEARCH_INTERVALS = frozenset({1440, 240, 60, 15, 5})

# Which bars a study was priced off. A venue read costs external quota and
# only reaches the rolling window; a stored read costs nothing and reaches
# whatever the platform has already written down. The two must never be
# confused afterwards, so each series carries its own source label.
VENUE_SOURCE = "venue_completed_bars"
STORED_SOURCE = "stored_market_data_bars"
# Namespaced so a stored digest can never be mistaken for a venue series
# fingerprint, which is produced by a different recipe over different rows.
STORED_FINGERPRINT_NS = "alethra-stored-bars/v1"


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
    def _validate_selection_and_balances(self) -> "StrategyLabRunRequest":
        if self.starting_cash < self.notional_per_trade:
            raise ValueError("starting_cash must be greater than or equal to notional_per_trade")
        if not self.intervals:
            raise ValueError("at least one interval is required")
        if len(set(self.intervals)) != len(self.intervals):
            raise ValueError("intervals must not repeat")
        # Omitting the field means "the whole catalog"; an explicit empty list
        # means "none of them", and the selector's falsy branch used to rewrite
        # that into the largest run available. Refuse instead of widening.
        if self.strategy_ids is not None and not self.strategy_ids:
            raise ValueError(
                "strategy_ids must not be empty; omit the field to run the whole catalog"
            )
        return self


def _require_instrument(instrument_id: str):
    """Resolve crypto instrument."""
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


def _get_provider_for_instrument(instrument):
    """Get the appropriate data provider for an instrument's market."""
    if instrument.market.value == "CRYPTO":
        return get_crypto_provider()
    else:
        raise HTTPException(
            status_code=400,
            detail={
                "reason": "unsupported_market",
                "market": instrument.market.value,
            },
        )


def _instrument_payload(instrument) -> dict:
    return {
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


class StrategyResearchRequest(BaseModel):
    instrument_id: str = DEFAULT_INSTRUMENT_ID
    interval_minutes: int = 1440
    data_source: Literal["venue", "stored"] = Field(
        default="venue",
        description=(
            "\"venue\" reads Kraken's most recent completed candles and spends "
            "external quota. \"stored\" reads only bars already written to "
            "market_data_bars at this exact grain, spends no quota, and refuses "
            "with 409 instead of falling back to the network or the demo feed."
        ),
    )
    folds: int = Field(default=DEFAULT_FOLDS, ge=3, le=8)
    starting_cash: Decimal = Field(default=DEFAULT_STARTING_CASH, gt=0)
    notional_per_trade: Decimal = Field(default=DEFAULT_NOTIONAL_PER_TRADE, gt=0)
    commission_rate: Decimal = Field(default=DEFAULT_COMMISSION_RATE, ge=0, le=Decimal("0.05"))
    slippage_rate: Decimal = Field(default=DEFAULT_SLIPPAGE_RATE, ge=0, le=Decimal("0.05"))
    include_trades: bool = False
    target_bars: Optional[int] = Field(
        default=None,
        ge=120,
        le=10000,
        description=(
            "Number of historical bars to fetch. Kraken serves at most 720 of the "
            "most recent buckets per interval, so a value above 720 is refused "
            "rather than answered with a shorter series. Default: 720"
        ),
    )

    @model_validator(mode="after")
    def _validate_balances(self) -> "StrategyResearchRequest":
        if self.starting_cash < self.notional_per_trade:
            raise ValueError("starting_cash must be greater than or equal to notional_per_trade")
        if self.data_source == "stored" and self.target_bars is not None:
            # A stored read has no paging: it returns every row the platform
            # holds at this grain. Accepting target_bars here would answer a
            # request for 2,000 bars with 720 and never say so.
            raise ValueError(
                "target_bars is a venue pagination request and is not accepted "
                'with data_source="stored"'
            )
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

    result["instrument"] = _instrument_payload(instrument)
    return result


def _utc(value: Optional[datetime]) -> Optional[datetime]:
    """``market_data_bars`` keeps naive UTC; the study engine works in aware UTC."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _venue_research_bars(payload: StrategyResearchRequest, instrument) -> Tuple[List[LabBar], dict]:
    """Read the venue's most recent completed candles for one interval."""
    provider = _get_provider_for_instrument(instrument)
    try:
        if payload.target_bars and payload.target_bars > BAR_LIMIT:
            series = provider.get_candle_series_paginated(
                payload.instrument_id,
                interval_minutes=payload.interval_minutes,
                target_bars=payload.target_bars,
            )
        else:
            series = provider.get_candle_series(
                payload.instrument_id,
                interval_minutes=payload.interval_minutes,
                limit=BAR_LIMIT,
            )
    except KrakenError as exc:
        raise crypto_failure(exc)
    except Exception as exc:
        if type(exc).__name__ in ("TwelveDataError", "TwelveDataRateLimitError",
                                   "SyntheticStockError", "SyntheticForexError"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "reason": "provider_error",
                    "provider": provider.provider_id,
                    "message": safe_error_message(exc),
                },
            )
        raise

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
    data_nature = provider.get_data_quality(payload.instrument_id).get("data_nature", "unknown")
    provenance = {
        "provider_id": provider.provider_id,
        "data_nature": data_nature,
        "fingerprint": series.fingerprint,
        "integrity_ok": series.integrity_ok,
        "gap_count": len(series.gaps),
        "missing_intervals": series.missing_intervals,
        "fetched_at": series.fetched_at.isoformat(),
        "is_stale": series.is_stale,
        "staleness_assessed": True,
        "source": VENUE_SOURCE,
        "network_used": True,
    }
    return bars, provenance


def _stored_research_bars(
    db: Session, *, instrument_id: str, interval_minutes: int
) -> Tuple[List[LabBar], dict]:
    """Rebuild a study series from rows already written to ``market_data_bars``.

    Nothing here reaches the network, so a series the platform does not hold is
    a refusal rather than a fallback: quietly pricing the study off Kraken or
    off the synthetic demo feed would report a stored result that was never
    stored. Rows are read at exactly the requested grain, because a daily bar
    and a four-hour bar legitimately share one midnight and the timestamp alone
    cannot tell them apart.
    """
    rows = (
        db.query(MarketDataBar)
        .filter(
            MarketDataBar.instrument_id == instrument_id,
            MarketDataBar.interval_minutes == interval_minutes,
        )
        .order_by(MarketDataBar.bar_timestamp)
        .all()
    )
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason": "no_stored_bars",
                "instrument_id": instrument_id,
                "interval_minutes": interval_minutes,
                "network_used": False,
                "message": (
                    f"No row in market_data_bars carries {instrument_id} at grain "
                    f"{interval_minutes} minutes. Nothing was fetched from the venue "
                    'or the demo feed; re-run with data_source="venue" or store the '
                    "interval first."
                ),
            },
        )

    provider_ids = sorted({row.provider_id for row in rows})
    if len(provider_ids) > 1:
        # Two providers price one instrument differently. Averaging them or
        # picking the first would decide, silently, which market the study
        # describes — so the series is refused and the caller picks one.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason": "ambiguous_stored_providers",
                "instrument_id": instrument_id,
                "interval_minutes": interval_minutes,
                "provider_ids": provider_ids,
                "network_used": False,
                "message": (
                    f"{instrument_id} is stored by {len(provider_ids)} providers "
                    f"({', '.join(provider_ids)}) at grain {interval_minutes} minutes. "
                    "The study will not merge or choose between them."
                ),
            },
        )

    step = timedelta(minutes=interval_minutes)
    bars: List[LabBar] = []
    for row in rows:
        if row.volume is None and row.volume_decimal is None:
            # An unrecorded volume is not a zero volume: reporting 0 would put
            # a number in the series that no venue ever printed.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "reason": "stored_bar_incomplete",
                    "instrument_id": instrument_id,
                    "provider_id": provider_ids[0],
                    "bar_timestamp": _utc(row.bar_timestamp).isoformat(),
                    "network_used": False,
                    "message": (
                        "A stored bar has no volume in either volume or "
                        "volume_decimal. The study refuses to price it as zero."
                    ),
                },
            )
        bars.append(
            LabBar(
                open_time=_utc(row.bar_timestamp),
                interval_minutes=interval_minutes,
                open=Decimal(str(row.open)),
                high=Decimal(str(row.high)),
                low=Decimal(str(row.low)),
                close=Decimal(str(row.close)),
                volume=(
                    row.volume_decimal if row.volume is None else Decimal(str(row.volume))
                ),
            )
        )

    gap_count = 0
    missing_intervals = 0
    for previous, current in zip(bars, bars[1:]):
        delta = current.open_time - previous.open_time
        if delta != step:
            gap_count += 1
            missing_intervals += int(delta // step) - 1

    digest = hashlib.sha256()
    digest.update(
        f"{STORED_FINGERPRINT_NS}|{instrument_id}|{provider_ids[0]}|"
        f"{interval_minutes}m|".encode()
    )
    for bar in bars:
        digest.update(
            (
                f"{int(bar.open_time.timestamp())}:{bar.open}:{bar.high}:"
                f"{bar.low}:{bar.close}:{bar.volume};"
            ).encode()
        )

    nature_map = MarketDataStorage(db).get_nature_map([instrument_id])
    data_nature = MarketDataStorage.resolve_nature(
        nature_map, instrument_id, set(provider_ids)
    )
    provenance = {
        "provider_id": provider_ids[0],
        "data_nature": data_nature,
        "fingerprint": digest.hexdigest(),
        # A stored series is checked against its own grain, not the venue's
        # trading calendar: a gap is a step that is not one interval long.
        "integrity_ok": gap_count == 0,
        "gap_count": gap_count,
        "missing_intervals": missing_intervals,
        # The last write, not a venue clock — this series was never fetched now.
        "fetched_at": _utc(max(row.fetched_at for row in rows)).isoformat(),
        "is_stale": None,
        "staleness_assessed": False,
        "source": STORED_SOURCE,
        "network_used": False,
        "row_count": len(rows),
    }
    return bars, provenance


def _research_study(
    payload: StrategyResearchRequest, db: Session, *, include_trades: bool
) -> Tuple[dict, List[LabBar], dict]:
    """Read one interval from the requested source, study it, attach provenance.

    The same path serves the ad-hoc preview and the durable record so both
    describe the same engine over the same completed bars. The venue and the
    stored series differ only in where their bars came from; the engine, the
    minimum-size refusal and the dataset block are shared, so a stored study
    cannot quietly mean something else.
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

    if payload.data_source == "stored":
        bars, provenance = _stored_research_bars(
            db,
            instrument_id=payload.instrument_id,
            interval_minutes=payload.interval_minutes,
        )
    else:
        bars, provenance = _venue_research_bars(payload, instrument)

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
                "data_source": payload.data_source,
                "network_used": provenance["network_used"],
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
            include_trades=include_trades,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"reason": "insufficient_dataset", "message": str(exc)},
        )

    dataset = {
        "instrument_id": payload.instrument_id,
        "interval_minutes": payload.interval_minutes,
        "bars": len(bars),
        "first_open_time": bars[0].open_time.isoformat(),
        "last_open_time": bars[-1].open_time.isoformat(),
        # The pair is read as (this key, ``source`` below) = (asked for, got).
        # They coincide today because a missing stored series is a refusal, not
        # a fallback; recording both is what would make a future fallback
        # visible in the row instead of inferred from the engine.
        "data_source_requested": payload.data_source,
        **provenance,
    }
    result["dataset"] = dataset
    result["instrument"] = _instrument_payload(instrument)

    # MB-15: Set fingerprint in batch_metadata from dataset
    if "batch_metadata" in result:
        result["batch_metadata"]["fingerprint"] = provenance["fingerprint"]

    return result, bars, dataset


@router.post("/research")
def strategy_lab_research(
    payload: StrategyResearchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _rate_limit=Depends(rate_limit("strategy_research")),
):
    """Cost-aware multi-bar study with walk-forward out-of-sample selection.

    One read supplies one interval — the venue's rolling window, or the bars
    already in market_data_bars when ``data_source`` is ``"stored"`` — and the
    study then runs entirely in memory. Rule/exit selection for each fold reads
    only the folds before it, and the in-sample grid is returned only as a
    comparison, never as evidence.
    This preview writes nothing; use ``/research/runs`` to keep the record.
    """
    result, _bars, _dataset = _research_study(
        payload, db, include_trades=payload.include_trades
    )
    return result


@router.post(
    "/research/runs",
    status_code=status.HTTP_201_CREATED,
)
def strategy_research_run_create(
    payload: StrategyResearchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _rate_limit=Depends(rate_limit("strategy_research")),
):
    """Run the study and store it with every out-of-sample buy/sell.

    The stored bars are the exact completed bars the engine read, so the run
    can be re-checked after the venue's rolling window has moved past it.
    """
    result, bars, dataset = _research_study(payload, db, include_trades=True)
    try:
        run = persist_study(
            db,
            user_id=current_user.id,
            result=result,
            bars=bars,
            dataset=dataset,
        )
    except SQLAlchemyError:
        logger.exception("strategy study could not be stored")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "reason": "study_store_unavailable",
                "message": (
                    "The study ran but could not be stored, so no durable record "
                    "was created. Check that the database schema is current."
                ),
            },
        )
    return run_detail(run)


@router.get("/research/runs")
def strategy_research_run_list(
    instrument_id: Optional[str] = Query(default=None),
    interval_minutes: Optional[int] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The caller's stored studies, newest first."""
    runs = list_studies(
        db,
        user_id=current_user.id,
        limit=limit,
        instrument_id=instrument_id,
        interval_minutes=interval_minutes,
    )
    return {
        "lane": "strategy_research_simulated",
        "simulated": True,
        "orders_supported": False,
        "records": [run_summary(run) for run in runs],
    }


def _require_owned_study(db: Session, user: User, run_id: UUID):
    run = get_study(db, user_id=user.id, run_id=run_id)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail={"reason": "study_run_not_found", "run_id": str(run_id)},
        )
    return run


@router.get("/research/runs/{run_id}")
def strategy_research_run_detail(
    run_id: UUID,
    include_bars: bool = Query(default=False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """One stored study with its per-trade record.

    ``include_bars=true`` also returns the exact bars the engine read, which is
    what an external backtest needs to reproduce the run.
    """
    run = _require_owned_study(db, current_user, run_id)
    return run_detail(run, include_bars=include_bars)


@router.get("/research/runs/{run_id}/trades.csv")
def strategy_research_run_trades_csv(
    run_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Owner-authorized CSV export of the run's out-of-sample trades."""
    run = _require_owned_study(db, current_user, run_id)
    filename = f"strategy-study-{run.id}-trades.csv"
    return Response(
        content=trades_csv(run),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class ArbitrageBacktestRequest(BaseModel):
    """Request body for triangular arbitrage backtest."""
    interval_minutes: int = Field(default=60, ge=1, le=1440)
    limit: int = Field(default=720, ge=100, le=5000)
    min_spread_bps: Decimal = Field(default=Decimal("20"), ge=0, le=1000)
    target_atr_mult: Decimal = Field(default=Decimal("1.5"), gt=0, le=10)
    stop_atr_mult: Decimal = Field(default=Decimal("1.0"), gt=0, le=10)
    time_stop_bars: int = Field(default=8, ge=1, le=50)
    starting_cash: Decimal = Field(default=DEFAULT_STARTING_CASH, gt=0)
    notional_per_trade: Decimal = Field(default=DEFAULT_NOTIONAL_PER_TRADE, gt=0)
    commission_rate: Decimal = Field(default=DEFAULT_COMMISSION_RATE, ge=0, le=Decimal("0.05"))
    slippage_rate: Decimal = Field(default=DEFAULT_SLIPPAGE_RATE, ge=0, le=Decimal("0.05"))
    config_id: str = Field(default="balanced")


@router.post("/arbitrage/backtest")
def arbitrage_backtest(
    payload: ArbitrageBacktestRequest,
    current_user: User = Depends(get_current_user),
    _rate_limit=Depends(rate_limit("strategy_research")),
):
    """Triangular arbitrage backtest across BTC-USD, ETH-USD, ETH-BTC.

    Fetches real Kraken OHLCV for all three legs, aligns by timestamp,
    computes the implied cross rate (ETH-BTC × BTC-USD → implied ETH-USD),
    and simulates trades when the spread exceeds the configured threshold.

    This is a multi-instrument engine, separate from the single-instrument
    research lane. All data is live from Kraken; no synthetic fills.
    """
    config = ArbitrageConfig(
        min_spread_bps=payload.min_spread_bps,
        target_atr_mult=payload.target_atr_mult,
        stop_atr_mult=payload.stop_atr_mult,
        time_stop_bars=payload.time_stop_bars,
        starting_cash=payload.starting_cash,
        notional_per_trade=payload.notional_per_trade,
        commission_rate=payload.commission_rate,
        slippage_rate=payload.slippage_rate,
        config_id=payload.config_id,
    )

    try:
        result = run_triangular_arbitrage(
            interval_minutes=payload.interval_minutes,
            limit=payload.limit,
            config=config,
        )
    except KrakenError as exc:
        raise crypto_failure(exc)
    except Exception as exc:
        logger.exception("arbitrage backtest failed")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason": "arbitrage_error",
                "message": safe_error_message(exc),
            },
        )

    if result.bars == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason": "no_aligned_bars",
                "message": (
                    "The three legs (BTC-USD, ETH-USD, ETH-BTC) had no timestamps "
                    "in common after alignment. Check that ETH-BTC is available on Kraken."
                ),
            },
        )

    return result_to_dict(result)


class MarketMakingBacktestRequest(BaseModel):
    """Request body for market making backtest."""
    instrument_id: str = DEFAULT_INSTRUMENT_ID
    interval_minutes: int = Field(default=5, ge=1, le=1440)
    limit: int = Field(default=720, ge=100, le=720)
    spread_target_bps: Decimal = Field(default=Decimal("10"), ge=1, le=100)
    min_spread_bps: Decimal = Field(default=Decimal("2"), ge=0, le=50)
    max_spread_bps: Decimal = Field(default=Decimal("50"), ge=1, le=200)
    order_size_usd: Decimal = Field(default=Decimal("100"), gt=0, le=10000)
    max_inventory_usd: Decimal = Field(default=Decimal("1000"), gt=0, le=100000)
    inventory_skew_factor: Decimal = Field(default=Decimal("0.1"), ge=0, le=1)
    commission_rate: Decimal = Field(default=DEFAULT_COMMISSION_RATE, ge=0, le=Decimal("0.05"))
    slippage_rate: Decimal = Field(default=DEFAULT_SLIPPAGE_RATE, ge=0, le=Decimal("0.05"))
    config_id: str = Field(default="balanced")


@router.post("/market-making/backtest")
def market_making_backtest(
    payload: MarketMakingBacktestRequest,
    current_user: User = Depends(get_current_user),
    _rate_limit=Depends(rate_limit("strategy_research")),
):
    """Market making backtest using OHLCV-simulated order book.

    IMPORTANT: This is a simulation approximation, not a realistic backtest.
    Real market making requires L2/L3 order book data, queue position modeling,
    and adverse selection analysis. OHLCV bars cannot capture these dynamics.

    The engine estimates bid/ask spread from each bar's high-low range, adjusted
    by volume. Fills are detected when price touches our quotes. Inventory skew
    adjusts quotes to limit directional exposure.
    """
    instrument = _require_instrument(payload.instrument_id)

    if instrument.market.value != "CRYPTO":
        raise HTTPException(
            status_code=400,
            detail={
                "reason": "unsupported_market",
                "message": "Market making simulation is only supported for crypto instruments.",
                "market": instrument.market.value,
            },
        )

    config = MarketMakingConfig(
        spread_target_bps=payload.spread_target_bps,
        min_spread_bps=payload.min_spread_bps,
        max_spread_bps=payload.max_spread_bps,
        order_size_usd=payload.order_size_usd,
        max_inventory_usd=payload.max_inventory_usd,
        inventory_skew_factor=payload.inventory_skew_factor,
        commission_rate=payload.commission_rate,
        slippage_rate=payload.slippage_rate,
        config_id=payload.config_id,
    )

    try:
        result = backtest_market_making(
            instrument_id=payload.instrument_id,
            interval_minutes=payload.interval_minutes,
            limit=payload.limit,
            config=config,
        )
    except KrakenError as exc:
        raise crypto_failure(exc)
    except Exception as exc:
        logger.exception("market making backtest failed")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason": "market_making_error",
                "message": safe_error_message(exc),
            },
        )

    if result.bars == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason": "no_bars",
                "message": "No completed bars returned from the venue.",
            },
        )

    return mm_result_to_dict(result)


class PairsTradingBacktestRequest(BaseModel):
    """Request body for pairs trading backtest."""
    pair: str = Field(default="ETH-BTC")
    interval_minutes: int = Field(default=60, ge=1, le=1440)
    limit: int = Field(default=720, ge=100, le=5000)
    zscore_window: int = Field(default=20, ge=5, le=100)
    entry_threshold: Decimal = Field(default=Decimal("2.0"), ge=Decimal("0.5"), le=Decimal("5.0"))
    exit_threshold: Decimal = Field(default=Decimal("0.5"), ge=Decimal("0"), le=Decimal("2.0"))
    stop_threshold: Decimal = Field(default=Decimal("3.0"), ge=Decimal("1.0"), le=Decimal("10.0"))
    time_stop_bars: int = Field(default=15, ge=1, le=100)
    starting_cash: Decimal = Field(default=DEFAULT_STARTING_CASH, gt=0)
    notional_per_leg: Decimal = Field(default=Decimal("5000"), gt=0)
    commission_rate: Decimal = Field(default=DEFAULT_COMMISSION_RATE, ge=0, le=Decimal("0.05"))
    config_id: str = Field(default="balanced")


@router.post("/pairs-trading/backtest")
def pairs_trading_backtest(
    payload: PairsTradingBacktestRequest,
    current_user: User = Depends(get_current_user),
    _rate_limit=Depends(rate_limit("strategy_research")),
):
    """Pairs trading backtest using z-score mean reversion on asset ratios.

    Fetches real Kraken OHLCV for both legs, computes a rolling z-score of the
    price ratio, and simulates dollar-neutral trades with a triple barrier exit
    (z-score reversion, stop-loss, time-stop).

    Supports direct quotes (ETH-BTC) and synthetic ratios (ETH-SOL, BTC-SOL).
    """
    config = PairsConfig(
        zscore_window=payload.zscore_window,
        entry_threshold=payload.entry_threshold,
        exit_threshold=payload.exit_threshold,
        stop_threshold=payload.stop_threshold,
        time_stop_bars=payload.time_stop_bars,
        starting_cash=payload.starting_cash,
        notional_per_leg=payload.notional_per_leg,
        commission_rate=payload.commission_rate,
        config_id=payload.config_id,
    )

    try:
        result = run_pairs_backtest(
            pair=payload.pair,
            interval_minutes=payload.interval_minutes,
            limit=payload.limit,
            config=config,
        )
    except KrakenError as exc:
        raise crypto_failure(exc)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "reason": "invalid_pair",
                "message": str(exc),
            },
        )
    except Exception as exc:
        logger.exception("pairs trading backtest failed")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason": "pairs_trading_error",
                "message": safe_error_message(exc),
            },
        )

    if result.bars == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason": "no_aligned_bars",
                "message": (
                    f"The two legs of {payload.pair} had no timestamps in common "
                    "after alignment. Check that both instruments are available on Kraken."
                ),
            },
        )

    return pairs_result_to_dict(result)


class SeasonalityAnalyzeRequest(BaseModel):
    """Request body for intraday seasonality analysis."""
    instrument_id: str = DEFAULT_INSTRUMENT_ID
    interval_minutes: int = Field(default=60, ge=1, le=1440)
    limit: int = Field(default=720, ge=100, le=5000)


@router.post("/seasonality/analyze")
def seasonality_analyze(
    payload: SeasonalityAnalyzeRequest,
    current_user: User = Depends(get_current_user),
    _rate_limit=Depends(rate_limit("strategy_research")),
):
    """Intraday seasonality analysis — diagnostic, not a trading signal.

    Fetches live Kraken OHLCV, computes per-bar returns, and tests whether
    specific hours-of-day or days-of-week carry systematically non-zero mean
    returns.  All p-values carry Bonferroni and Benjamini-Hochberg FDR
    corrections for multiple testing.

    Stage 1 is purely exploratory.  A significant slot is evidence of a
    historical pattern, not a promise of future returns.
    """
    instrument = _require_instrument(payload.instrument_id)

    if instrument.market.value != "CRYPTO":
        raise HTTPException(
            status_code=400,
            detail={
                "reason": "unsupported_market",
                "message": "Seasonality analysis is only supported for crypto instruments.",
                "market": instrument.market.value,
            },
        )

    try:
        result = analyze_seasonality(
            instrument_id=payload.instrument_id,
            interval_minutes=payload.interval_minutes,
            limit=payload.limit,
        )
    except KrakenError as exc:
        raise crypto_failure(exc)
    except Exception as exc:
        logger.exception("seasonality analysis failed")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason": "seasonality_error",
                "message": safe_error_message(exc),
            },
        )

    return seasonality_result_to_dict(result)
